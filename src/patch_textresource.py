#!/usr/bin/env python3
"""Dump / translate / rebuild textresource_jpn.trb (STRI/STRB).

Vanilla strings use a custom NLP codebook (flag 0) or UTF-8 (flag 1).
Rebuilt translations are stored as UTF-8 (flag 1). CDEI/CDEB/CONF/INDX
chunks from the source TRB are preserved; only STRI+STRB are rewritten.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
LOOKUP_PATH = ROOT / "tools" / "Trb2xlsx" / "TrbExport" / "lookup.txt"
OUT_DIR = ROOT / "out" / "textresource"

DEFAULT_TRB = (
    ROOT.parent
    / "New Love Plus Plus"
    / "extracted"
    / "romfs"
    / "SystemData"
    / "TextResource"
    / "textresource_jpn.trb"
)

AZAHAR_DIR = (
    Path.home()
    / "AppData"
    / "Roaming"
    / "Azahar"
    / "load"
    / "mods"
    / "00040000000F4E00"
    / "romfs"
    / "SystemData"
    / "TextResource"
)

JP_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
# SpotPass one-liner kept for backwards-compatible --inplace
PATCHES: list[tuple[str, str]] = [
    (
        "いつの間に通信の◙受信データがありません。",
        "No SpotPass\ndata found.",
    ),
]


def load_lookup(path: Path) -> list[str]:
    if not path.is_file():
        raise SystemExit(f"missing lookup table: {path}")
    return path.read_text(encoding="utf-8").splitlines()


def nlp_get_string(data: bytes, lookup: list[str]) -> str:
    ret: list[str] = []
    hi = 0
    for character in data:
        if hi == 0 and character >= 0x80:
            hi = character
            continue
        if hi >= 0x80:
            charindex = ((hi - 0x80) << 8) + character
            hi = 0
        else:
            charindex = character
        if not (1 <= charindex <= len(lookup)):
            ret.append("?")
            continue
        ret.append(lookup[charindex - 1])
    return "".join(ret)


def parse_chunks(data: bytes) -> dict:
    if data[:4] != b"STRI":
        raise ValueError("not a STRI textresource")
    pos = 0
    chunks: dict[str, bytes] = {}
    order: list[str] = []
    while pos + 8 <= len(data):
        sig = data[pos : pos + 4]
        if not all(32 <= b < 127 for b in sig):
            chunks["_tail"] = data[pos:]
            break
        size = struct.unpack_from("<I", data, pos + 4)[0]
        if size > len(data) - pos - 8:
            chunks["_tail"] = data[pos:]
            break
        name = sig.decode("ascii")
        body = data[pos + 8 : pos + 8 + size]
        chunks[name] = body
        order.append(name)
        pos = pos + 8 + size
    else:
        if pos < len(data):
            chunks["_tail"] = data[pos:]
    chunks["_order"] = order  # type: ignore[assignment]
    return chunks


def iter_entries(stri: bytes):
    off = 0
    idx = 0
    while off + 8 <= len(stri):
        stringindex, bytelength, flag = struct.unpack_from("<IHH", stri, off)
        yield idx, off, stringindex, bytelength, flag
        off += 8
        idx += 1


def decode_entry(strb: bytes, stringindex: int, flag: int, lookup: list[str]) -> tuple[str, bytes]:
    if flag == 2:
        return "", b""
    end = strb.find(b"\x00", stringindex)
    if end < 0:
        end = len(strb)
    buf = strb[stringindex:end]
    if flag == 1:
        return buf.decode("utf-8", errors="replace"), buf
    return nlp_get_string(buf, lookup), buf


def dump_entries(data: bytes, lookup: list[str]) -> list[dict]:
    chunks = parse_chunks(data)
    stri = chunks["STRI"]
    strb = chunks["STRB"]
    entries: list[dict] = []
    for idx, _off, stringindex, bytelength, flag in iter_entries(stri):
        text, raw = decode_entry(strb, stringindex, flag, lookup)
        entries.append(
            {
                "idx": idx,
                "flag": flag,
                "bytelength": bytelength,
                "text": text,
                "raw_hex": raw.hex(),
            }
        )
    return entries


def normalize_newlines(text: str) -> str:
    return text.replace("◙", "\n").replace("\r\n", "\n").replace("\r", "\n")


def to_game_newlines(text: str) -> str:
    """UTF-8 storage uses LF; display tools often show ◙."""
    return normalize_newlines(text)


def _soft_wrap_words(words: list[str], width: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = w if not cur else f"{cur} {w}"
        if cur and len(trial) > width:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def fit_wrap(
    english: str,
    original: str,
    *,
    min_width: int = 18,
    max_width: int = 30,
    jp_scale: float = 2.0,
    force_width: int | None = None,
) -> str:
    """Best-guess wrap for dialog-sized EN using JP line width as a budget.

    - Multi-line JP (◙/\\n) → re-wrap EN to ~2× max JP line length when needed.
    - Already-broken EN with an over-long line → same.
    - Single-line JP/EN blobs (newsletters, etc.) are left alone.
    """
    en = normalize_newlines(english).strip("\n")
    # Collapse accidental "word \\n word" spacing from MT.
    en = re.sub(r"[ \t]*\n[ \t]*", "\n", en)
    orig = normalize_newlines(original)
    jp_lines = orig.split("\n") if orig else [""]
    jp_breaks = max(0, len(jp_lines) - 1)
    en_lines = [L.strip() for L in en.split("\n")] if en else [""]
    max_en = max((len(L) for L in en_lines), default=0)

    if force_width is not None:
        width = force_width
    else:
        max_jp = max((len(L) for L in jp_lines), default=0)
        width = int(round(max_jp * jp_scale)) if max_jp else 26
        width = max(min_width, min(max_width, width))

    # Single-line JP → leave EN alone (mail / tips / labels), unless EN
    # already has breaks and overflows the budget.
    if jp_breaks <= 0:
        if "\n" not in en or max_en <= width:
            return en if "\n" not in en else "\n".join(en_lines)
        # fall through to wrap overflowing broken EN

    # Already fits: keep cleaned breaks.
    if max_en <= width and "\n" in en:
        return "\n".join(en_lines)

    words = en.replace("\n", " ").split()
    if not words:
        return en
    return "\n".join(_soft_wrap_words(words, width))


def wrap_like_original(english: str, original: str, width: int = 22) -> str:
    """Back-compat: soft-wrap using a fixed width (legacy callers)."""
    return fit_wrap(english, original, force_width=width)


def rebuild_trb(
    data: bytes,
    translations: dict[str, str],
    lookup: list[str],
) -> tuple[bytes, dict]:
    """Rebuild STRI+STRB applying JP->EN map. Preserve other chunks."""
    chunks = parse_chunks(data)
    stri = chunks["STRI"]
    strb = chunks["STRB"]

    new_stri = bytearray()
    new_strb = bytearray()
    stats = {"total": 0, "translated": 0, "kept": 0, "empty": 0}

    for idx, _off, stringindex, bytelength, flag in iter_entries(stri):
        stats["total"] += 1
        if flag == 2:
            new_stri += struct.pack("<IHH", len(new_strb), 0, 2)
            stats["empty"] += 1
            continue

        text, raw = decode_entry(strb, stringindex, flag, lookup)
        key = text
        # Also allow matching with LF normalized to ◙ (vanilla NLP form).
        key_alt = text.replace("\n", "◙")

        if key in translations or key_alt in translations:
            english = translations.get(key) or translations[key_alt]
            english = fit_wrap(english, text)
            # Game font: prefer fullwidth question mark.
            english = english.replace("?", "？")
            payload = english.encode("utf-8")
            new_stri += struct.pack("<IHH", len(new_strb), len(payload), 1)
            new_strb += payload + b"\x00"
            stats["translated"] += 1
        else:
            # Keep original encoding/bytes.
            new_stri += struct.pack("<IHH", len(new_strb), bytelength, flag)
            if raw:
                new_strb += raw + b"\x00"
            else:
                new_strb += b"\x00"
            stats["kept"] += 1

    # Pad STRB to 4-byte alignment with 0xC9 (Trb2xlsx convention).
    pad = (4 - (len(new_strb) % 4)) % 4
    if pad:
        new_strb += b"\xc9" * pad

    out = bytearray()
    for name in chunks["_order"]:
        if name == "STRI":
            body = bytes(new_stri)
        elif name == "STRB":
            body = bytes(new_strb)
        else:
            body = chunks[name]
        out += name.encode("ascii")
        out += struct.pack("<I", len(body))
        out += body
    if "_tail" in chunks:
        out += chunks["_tail"]
    return bytes(out), stats


def slot_room(stri: bytes, strb_size: int, stringindex: int) -> int:
    idxs = sorted({si for _, _, si, _, _ in iter_entries(stri)})
    i = idxs.index(stringindex)
    nxt = idxs[i + 1] if i + 1 < len(idxs) else strb_size
    return nxt - stringindex


def patch_inplace(
    data: bytes,
    patches: list[tuple[str, str]],
    lookup: list[str],
) -> tuple[bytes, list[str]]:
    chunks = parse_chunks(data)
    stri = bytearray(chunks["STRI"])
    strb = bytearray(chunks["STRB"])
    pending = {src: dst for src, dst in patches}
    logs: list[str] = []

    for idx, entry_off, stringindex, bytelength, flag in iter_entries(stri):
        text, _raw = decode_entry(strb, stringindex, flag, lookup)
        if text not in pending:
            continue
        english = to_game_newlines(pending.pop(text)).replace("?", "？")
        payload = english.encode("utf-8")
        need = len(payload) + 1
        room = slot_room(stri, len(strb), stringindex)
        if need > room:
            raise ValueError(
                f"entry {idx}: English needs {need} bytes, slot only has {room}."
            )
        strb[stringindex : stringindex + need] = payload + b"\x00"
        if need < room:
            strb[stringindex + need : stringindex + room] = b"\x00" * (room - need)
        struct.pack_into("<IHH", stri, entry_off, stringindex, len(payload), 1)
        logs.append(f"entry {idx}: {text!r} -> {english!r}")

    if pending:
        raise ValueError(f"string(s) not found: {', '.join(map(repr, pending))}")

    # Reassemble with patched STRI/STRB.
    out = bytearray()
    for name in chunks["_order"]:
        body = bytes(stri) if name == "STRI" else bytes(strb) if name == "STRB" else chunks[name]
        out += name.encode("ascii")
        out += struct.pack("<I", len(body))
        out += body
    if "_tail" in chunks:
        out += chunks["_tail"]
    return bytes(out), logs


def update_config_size(config: bytes, trb_size: int) -> bytes:
    if config[:4] != b"SIZE":
        raise ValueError("textresource_config.trb missing SIZE magic")
    out = bytearray(config)
    struct.pack_into("<I", out, 8, trb_size)
    return bytes(out)


def load_translations(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        # Support {"translations": {...}} or flat map.
        if "translations" in data and isinstance(data["translations"], dict):
            return {str(k): str(v) for k, v in data["translations"].items()}
        return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}
    raise ValueError(f"unsupported translations format: {path}")


def save_translations(path: Path, mapping: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"translations": mapping}, ensure_ascii=False, indent=1)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def seed_from_nlppatch(
    vanilla: bytes,
    patched: bytes,
    lookup: list[str],
) -> dict[str, str]:
    """Align by entry index; take EN where NLPPATCH removed Japanese."""
    v = dump_entries(vanilla, lookup)
    p = dump_entries(patched, lookup)
    if len(v) != len(p):
        raise ValueError(f"entry count mismatch: vanilla {len(v)} vs patch {len(p)}")
    out: dict[str, str] = {}
    for a, b in zip(v, p):
        src, dst = a["text"], b["text"]
        if not src or src == dst:
            continue
        if JP_RE.search(src) and not JP_RE.search(dst):
            out[src] = to_game_newlines(dst)
    return out


def cmd_dump(args: argparse.Namespace) -> int:
    lookup = load_lookup(args.lookup.resolve())
    entries = dump_entries(args.trb.resolve().read_bytes(), lookup)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.suffix.lower() == ".jsonl":
        with args.out.open("w", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    else:
        slim = [{"idx": e["idx"], "flag": e["flag"], "text": e["text"]} for e in entries]
        args.out.write_text(json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8")
    jp = sum(1 for e in entries if JP_RE.search(e["text"]))
    print(f"[trb] dumped {len(entries)} entries ({jp} with JP) -> {args.out}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    lookup = load_lookup(args.lookup.resolve())
    vanilla = args.trb.resolve().read_bytes()
    patched = args.nlppatch.resolve().read_bytes()
    seeded = seed_from_nlppatch(vanilla, patched, lookup)
    existing = load_translations(args.translations)
    # Seed does not overwrite manual/better translations already present.
    merged = dict(seeded)
    merged.update(existing)
    save_translations(args.translations, merged)
    print(f"[trb] seeded {len(seeded)} from NLPPATCH; total map {len(merged)} -> {args.translations}")
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    lookup = load_lookup(args.lookup.resolve())
    src = args.trb.resolve()
    data = src.read_bytes()
    mapping = load_translations(args.translations.resolve())
    # Always include built-in SpotPass patch.
    for jp, en in PATCHES:
        mapping.setdefault(jp, en)

    rebuilt, stats = rebuild_trb(data, mapping, lookup)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(rebuilt)
    print(f"[trb] rebuild {stats} -> {args.out} ({len(rebuilt)} bytes)")

    config_src = args.config
    if config_src is None:
        guess = src.parent / "textresource_config.trb"
        if guess.is_file():
            config_src = guess
    if config_src is not None:
        # Match NLPPATCH: SIZE may be >= file size (allocator headroom).
        size_value = max(len(rebuilt), args.min_config_size)
        cfg = update_config_size(config_src.resolve().read_bytes(), size_value)
        args.config_out.parent.mkdir(parents=True, exist_ok=True)
        args.config_out.write_bytes(cfg)
        print(f"[trb] config SIZE={size_value} -> {args.config_out}")

    if args.deploy_azahar:
        dest = AZAHAR_DIR / "textresource_jpn.trb"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.out, dest)
        print(f"[trb] deployed {dest}")
        if config_src is not None and args.config_out.is_file():
            cfg_dest = AZAHAR_DIR / "textresource_config.trb"
            shutil.copy2(args.config_out, cfg_dest)
            print(f"[trb] deployed {cfg_dest}")
    return 0


def cmd_inplace(args: argparse.Namespace) -> int:
    lookup = load_lookup(args.lookup.resolve())
    data = args.trb.resolve().read_bytes()
    patched, logs = patch_inplace(data, PATCHES, lookup)
    for line in logs:
        print(f"[trb] {line}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(patched)
    print(f"[trb] wrote {args.out} ({len(patched)} bytes)")
    return 0


_FW = str.maketrans("０１２３４５６７８９：／．，－＋％～　", "0123456789:/.,-+%~ ")
_JP_UNITS = {
    "時": ":00",
    "分": " min",
    "秒": " sec",
    "日": " day(s)",
    "週": " week(s)",
    "月": " month(s)",
    "年": " year(s)",
    "回": " time(s)",
    "人": " people",
    "個": "",
    "円": " yen",
}


def local_translate(text: str) -> str | None:
    """Rule-based EN for tiny / numeric JP fragments Google often rejects."""
    raw = text.strip()
    if not raw:
        return ""
    # Pure fullwidth/ASCII digits and punctuation.
    asciiish = raw.translate(_FW)
    if re.fullmatch(r"[\d\s:/\.,\+\-%~]+", asciiish):
        return asciiish
    # Patterns like ５時 / １０時 / ３日
    m = re.fullmatch(r"([０-９0-9]+)([時分秒日週月年回人個円])", raw)
    if m:
        num = m.group(1).translate(_FW)
        unit = m.group(2)
        if unit == "時":
            return f"{int(num)}:00"
        return f"{num}{_JP_UNITS.get(unit, '')}".rstrip()
    # Single common words
    simple = {
        "時": "Time",
        "分": "Min",
        "秒": "Sec",
        "日": "Day",
        "月": "Month",
        "年": "Year",
        "週": "Week",
        "回": "Times",
        "人": "People",
        "円": "Yen",
        "無": "None",
        "有": "Yes",
        "可": "OK",
        "不可": "No",
        "男": "Male",
        "女": "Female",
        "左": "Left",
        "右": "Right",
        "上": "Up",
        "下": "Down",
        "中": "Mid",
        "大": "L",
        "小": "S",
        "新": "New",
        "旧": "Old",
        "他": "Other",
        "等": "Etc.",
        "頁": "Page",
        "名": "Name",
        "姓": "Surname",
    }
    if raw in simple:
        return simple[raw]
    return None


SYSTEM_PROMPT = """You translate Japanese Nintendo 3DS UI strings from NEW Love Plus+ into concise English.
Rules:
- Return ONLY a JSON array of objects: [{"i": <int>, "t": "<english>"}]
- Keep the same number of items and the same "i" indices as the input.
- Preserve line breaks: input may use ◙ or \\n — output English with \\n in the same places/count when possible.
- Preserve placeholders/tokens exactly (e.g. %d, %s, ▲...＊＊▲, {}, numbers).
- Game terms: いつの間に通信=SpotPass, すれちがい通信=StreetPass, とわの/トワノ=Towano, 高嶺=Takane, 凛子/リンコ=Rinko, ネネ=Nene.
- Use '?' as fullwidth '？' is fine either way; prefer ASCII letters.
- Keep UI short and natural. Do not add quotes around the whole string.
- Do not leave Japanese characters in the translation unless it is a proper noun with no English form.
"""


def load_openai_api_key(explicit: str | None = None) -> str:
    if explicit:
        return explicit.strip()
    env = os.environ.get("OPENAI_API_KEY", "").strip()
    if env:
        return env
    candidates = [
        ROOT / ".env",
        ROOT.parent / "NewLovePlusPlusLocalizationProject" / ".env",
        ROOT.parent / ".env",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "OPENAI_API_KEY":
                val = v.strip().strip('"').strip("'")
                if val:
                    return val
    raise SystemExit(
        "OPENAI_API_KEY not set. Export it or put it in "
        "NewLovePlusPlusLocalizationProject/.env"
    )


def openai_translate_batch(
    client,
    model: str,
    texts: list[str],
    start_index: int,
) -> list[str | None]:
    """Translate a batch via OpenAI; returns EN strings aligned to texts."""
    payload = [{"i": start_index + n, "s": s.replace("\n", "◙")} for n, s in enumerate(texts)]
    user = (
        "Translate each Japanese UI string to English.\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    resp = client.chat.completions.create(
        model=model,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
                + '\nWrap the array in an object: {"items":[...]}',
            },
            {"role": "user", "content": user},
        ],
    )
    content = resp.choices[0].message.content or "{}"
    data = json.loads(content)
    items = data.get("items", data if isinstance(data, list) else [])
    by_i = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        if "i" in item and "t" in item:
            by_i[int(item["i"])] = str(item["t"])
        elif "i" in item and "s" in item:
            # model echoed source key by mistake
            continue
    out: list[str | None] = []
    for n, src in enumerate(texts):
        en = by_i.get(start_index + n)
        if en is None:
            out.append(None)
            continue
        en = to_game_newlines(en).replace("?", "？")
        jp_chars = len(JP_RE.findall(en))
        if jp_chars > max(2, len(en) // 4):
            out.append(None)
            continue
        out.append(en)
    return out


def cmd_translate_remaining(args: argparse.Namespace) -> int:
    """Translate remaining JP strings with OpenAI."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit("openai package missing. Run: pip install openai") from exc

    api_key = load_openai_api_key(args.api_key)
    client = OpenAI(api_key=api_key)
    model = args.model

    lookup = load_lookup(args.lookup.resolve())
    entries = dump_entries(args.trb.resolve().read_bytes(), lookup)
    mapping = load_translations(args.translations.resolve())

    pending: list[str] = []
    seen: set[str] = set()
    for e in entries:
        text = e["text"]
        if not text or not JP_RE.search(text):
            continue
        if text in mapping or text.replace("\n", "◙") in mapping:
            continue
        if text in seen:
            continue
        seen.add(text)
        pending.append(text)

    print(f"[trb] remaining unique JP strings: {len(pending)}")
    if args.limit:
        pending = pending[: args.limit]
        print(f"[trb] translating first {len(pending)} (--limit)")

    local_done = 0
    still: list[str] = []
    for s in pending:
        loc = local_translate(s)
        if loc is not None:
            mapping[s] = to_game_newlines(loc).replace("?", "？")
            local_done += 1
        else:
            still.append(s)
    if local_done:
        save_translations(args.translations, mapping)
        print(f"[trb] local rules filled {local_done}; OpenAI remaining {len(still)}")
    pending = still

    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    batch_size = max(1, args.batch_size)
    workers = max(1, args.workers)
    done = local_done
    errors = 0
    lock = threading.Lock()

    batches = [
        (i, pending[i : i + batch_size]) for i in range(0, len(pending), batch_size)
    ]

    def run_batch(start: int, chunk: list[str]) -> tuple[int, list[str], list[str | None]]:
        try:
            results = openai_translate_batch(client, model, chunk, start)
        except Exception as exc:
            print(f"[trb] batch error at {start}: {exc!r}; retrying one-by-one")
            results = []
            for n, s in enumerate(chunk):
                try:
                    one = openai_translate_batch(client, model, [s], start + n)[0]
                except Exception as exc2:
                    print(f"[trb] item error: {exc2!r} for {s[:40]!r}")
                    one = None
                results.append(one)
                time.sleep(args.sleep)
        return start, chunk, results

    finished_items = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_batch, start, chunk) for start, chunk in batches]
        for fut in as_completed(futures):
            _start, chunk, results = fut.result()
            with lock:
                for src, dst in zip(chunk, results):
                    if not dst:
                        errors += 1
                        if args.keep_failed:
                            mapping[src] = src
                        continue
                    mapping[src] = dst
                    done += 1
                finished_items += len(chunk)
                save_translations(args.translations, mapping)
                print(
                    f"[trb] progress {finished_items}/{len(pending)} "
                    f"(map={len(mapping)}, errors={errors}, model={model}, workers={workers})"
                )

    print(f"[trb] translated {done} strings; map size {len(mapping)}; errors {errors}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    lookup = load_lookup(args.lookup.resolve())
    entries = dump_entries(args.trb.resolve().read_bytes(), lookup)
    mapping = load_translations(args.translations) if args.translations else {}
    jp = [e for e in entries if JP_RE.search(e["text"])]
    covered = sum(
        1
        for e in jp
        if e["text"] in mapping or e["text"].replace("\n", "◙") in mapping
    )
    print(f"entries={len(entries)} jp={len(jp)} mapped={covered} missing={len(jp) - covered}")
    return 0


def cmd_rewrap(args: argparse.Namespace) -> int:
    """Re-wrap dialog EN in an existing EN TRB using JP line-width budgets."""
    lookup = load_lookup(args.lookup.resolve())
    vanilla = dump_entries(args.trb.resolve().read_bytes(), lookup)
    en_path = args.en_trb.resolve()
    en_entries = dump_entries(en_path.read_bytes(), lookup)
    if len(vanilla) != len(en_entries):
        raise SystemExit(
            f"entry count mismatch vanilla={len(vanilla)} en={len(en_entries)}"
        )

    mapping: dict[str, str] = {}
    changed = 0
    preview_idx = {4657, 4623, 4624, 4659, 4661}
    for i, (jp_e, en_e) in enumerate(zip(vanilla, en_entries)):
        jp = jp_e["text"]
        en = en_e["text"]
        if not en or en == jp:
            continue
        # Skip untranslated leftovers.
        if JP_RE.search(en) and not re.search(r"[A-Za-z]", en):
            continue
        new = fit_wrap(en, jp)
        if new != normalize_newlines(en).strip("\n"):
            changed += 1
            if i in preview_idx or (args.verbose and changed <= 12):
                print(f"--- idx {i} ---", flush=True)
                print("OLD:", repr(en)[:240], flush=True)
                print("NEW:", repr(new)[:240], flush=True)
        mapping[jp] = new
        mapping[jp.replace("\n", "◙")] = new

    print(f"[rewrap] candidates={len(mapping)} changed={changed}", flush=True)
    rebuilt, stats = rebuild_trb(args.trb.resolve().read_bytes(), mapping, lookup)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(rebuilt)
    print(f"[rewrap] rebuild {stats} -> {args.out} ({len(rebuilt)} bytes)", flush=True)

    if args.update_translations:
        tpath = args.update_translations.resolve()
        existing = load_translations(tpath) if tpath.is_file() else {}
        existing.update(mapping)
        save_translations(tpath, existing)
        print(f"[rewrap] updated {tpath} (+{changed} wraps)", flush=True)

    config_src = args.config
    if config_src is None:
        guess = args.trb.resolve().parent / "textresource_config.trb"
        if guess.is_file():
            config_src = guess
    if config_src is not None:
        size_value = max(len(rebuilt), args.min_config_size)
        cfg = update_config_size(config_src.resolve().read_bytes(), size_value)
        args.config_out.parent.mkdir(parents=True, exist_ok=True)
        args.config_out.write_bytes(cfg)
        print(f"[rewrap] config SIZE={size_value} -> {args.config_out}", flush=True)

    if args.deploy_azahar:
        dest = AZAHAR_DIR / "textresource_jpn.trb"
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Backup once.
        bak = dest.with_suffix(".trb.bak_pre_rewrap")
        if dest.is_file() and not bak.is_file():
            shutil.copy2(dest, bak)
            print(f"[rewrap] backup {bak}", flush=True)
        shutil.copy2(args.out, dest)
        print(f"[rewrap] deployed {dest}", flush=True)
        if config_src is not None and args.config_out.is_file():
            cfg_dest = AZAHAR_DIR / "textresource_config.trb"
            shutil.copy2(args.config_out, cfg_dest)
            print(f"[rewrap] deployed {cfg_dest}", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lookup", type=Path, default=LOOKUP_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("dump", help="Dump all entries to JSON/JSONL")
    p.add_argument("--trb", type=Path, default=DEFAULT_TRB)
    p.add_argument("--out", type=Path, default=OUT_DIR / "dump.jsonl")
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser("seed", help="Seed translations from NLPPATCH TRB by index")
    p.add_argument("--trb", type=Path, default=DEFAULT_TRB)
    p.add_argument(
        "--nlppatch",
        type=Path,
        default=ROOT
        / "vendor"
        / "NLPPATCH"
        / "release"
        / "romfs"
        / "SystemData"
        / "TextResource"
        / "textresource_jpn.trb",
    )
    p.add_argument("--translations", type=Path, default=OUT_DIR / "translations.json")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("translate", help="OpenAI-translate remaining JP strings")
    p.add_argument("--trb", type=Path, default=DEFAULT_TRB)
    p.add_argument("--translations", type=Path, default=OUT_DIR / "translations.json")
    p.add_argument("--batch-size", type=int, default=40)
    p.add_argument("--sleep", type=float, default=0.05)
    p.add_argument("--workers", type=int, default=6, help="parallel OpenAI batch workers")
    p.add_argument("--limit", type=int, default=0, help="translate only N unique strings")
    p.add_argument("--model", default="gpt-4o-mini", help="OpenAI chat model")
    p.add_argument("--api-key", default=None, help="OpenAI API key (else env/.env)")
    p.add_argument(
        "--keep-failed",
        action="store_true",
        help="leave failed MT entries as Japanese so rebuild coverage is complete",
    )
    p.set_defaults(func=cmd_translate_remaining)

    p = sub.add_parser("rebuild", help="Apply translations.json and rebuild TRB")
    p.add_argument("--trb", type=Path, default=DEFAULT_TRB)
    p.add_argument("--translations", type=Path, default=OUT_DIR / "translations.json")
    p.add_argument("--out", type=Path, default=OUT_DIR / "textresource_jpn.trb")
    p.add_argument("--config", type=Path, default=None)
    p.add_argument("--config-out", type=Path, default=OUT_DIR / "textresource_config.trb")
    p.add_argument(
        "--min-config-size",
        type=int,
        default=0,
        help="minimum SIZE written to config (NLPPATCH used headroom)",
    )
    p.add_argument("--deploy-azahar", action="store_true")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("inplace", help="Legacy same-slot SpotPass patch")
    p.add_argument("--trb", type=Path, default=DEFAULT_TRB)
    p.add_argument("--out", type=Path, default=OUT_DIR / "textresource_jpn.trb")
    p.set_defaults(func=cmd_inplace)

    p = sub.add_parser("stats", help="Coverage stats for translations map")
    p.add_argument("--trb", type=Path, default=DEFAULT_TRB)
    p.add_argument("--translations", type=Path, default=OUT_DIR / "translations.json")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser(
        "rewrap",
        help="Re-wrap dialog EN using JP line-width budgets (best-guess fit)",
    )
    p.add_argument("--trb", type=Path, default=DEFAULT_TRB, help="vanilla JP TRB")
    p.add_argument(
        "--en-trb",
        type=Path,
        default=AZAHAR_DIR / "textresource_jpn.trb",
        help="current English TRB to re-wrap",
    )
    p.add_argument("--out", type=Path, default=OUT_DIR / "textresource_jpn.trb")
    p.add_argument("--config", type=Path, default=None)
    p.add_argument("--config-out", type=Path, default=OUT_DIR / "textresource_config.trb")
    p.add_argument("--min-config-size", type=int, default=0)
    p.add_argument(
        "--update-translations",
        type=Path,
        default=None,
        help="merge rewrapped strings into translations.json",
    )
    p.add_argument("--deploy-azahar", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_rewrap)

    return ap


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
