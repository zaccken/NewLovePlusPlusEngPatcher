#!/usr/bin/env python3
"""
English heroine-name patches for New Love Plus+.

Three layers (all needed for a complete result):

1. **`.dbin2` scripts** — dialog embeds control tokens like ``▲高嶺＊＊▲``.
   The game only recognizes the Japanese token form; ASCII inside the markers
   prints literally (triangles/stars visible). We strip markers and write plain
   ``Takane`` / ``Rinko`` / ``Nene``, rebuilding SDL2 buffers (length may shrink).

2. **`textresource_resident_jpn.trb`** — menus / some UI name strings.

3. **`img.bin` name table** — duplicate name/nickname bank used by UI.

Fixed-width slots in (2)/(3): English must fit the original UTF-8 byte budget;
shorter strings are NUL-padded at the end of the span.
"""
from __future__ import annotations

import argparse
import re
import struct
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Dialog script tokens → plain English (no ▲ / ＊ wrappers)
# ---------------------------------------------------------------------------

DBIN_REPLACEMENTS: list[tuple[str, str]] = [
    ("▲高嶺＊＊▲", "Takane"),
    ("▲Takane＊＊▲", "Takane"),
    ("▲高嶺＊＊", "Takane"),  # rare truncated forms (missing closing ▲)
    ("▲Takane＊＊", "Takane"),
    ("▲小早川＊▲", "Rinko"),
    ("▲Rinko＊▲", "Rinko"),
    ("▲小早川＊", "Rinko"),
    ("▲Rinko＊", "Rinko"),
    ("▲姉ヶ崎＊▲", "Nene"),
    ("▲Nene＊▲", "Nene"),
    ("▲姉ヶ崎＊", "Nene"),
    ("▲Nene＊", "Nene"),
]

TRAILING_PAD_RE = re.compile(
    r"(Takane|Rinko|Nene) {1,8}(?=[！？!?.\)\]\，\。,]|$)"
)

# ---------------------------------------------------------------------------
# Resident / img.bin fixed-width name table
# ---------------------------------------------------------------------------

# Keep ▲…▲ forms here in sync with any leftover control-code lookups.
TOKEN_REPLACEMENTS: list[tuple[str, str]] = [
    ("▲高嶺＊＊▲", "▲Takane＊＊▲"),
    ("▲小早川＊▲", "▲Rinko＊▲"),
    ("▲姉ヶ崎＊▲", "▲Nene＊▲"),
]

NAME_REPLACEMENTS: list[tuple[str, str]] = [
    ("高嶺愛花", "ManakaTakane"),
    ("小早川凛子", "Rinko Kobayaka"),
    ("姉ヶ崎寧々", "Nene Anegasaki"),
    ("高嶺ちゃん", "Takane-chan"),
    ("高嶺さん", "Takane-san"),
    ("高嶺くん", "Takane-kun"),
    ("高嶺選手", "Takane"),
    ("高嶺様", "Takane"),
    ("小早川", "Kobayaka"),
    ("姉ヶ崎", "Anegasaki"),
    ("高嶺", "Takane"),
    ("愛花", "Manaka"),
    ("凛子", "Rinko"),
    ("寧々", "Nene"),
]

IMG_NAME_REGION = (0x298FF000, 0x29908000)

PACKS = ("NLP_01", "NLP_02", "script")


# ---------------------------------------------------------------------------
# XOR helpers (NLPTextTool EncryptedBinary)
# ---------------------------------------------------------------------------

def _decrypt_byte(pos: int, value: int, key: int) -> int:
    kb = [key & 0xFF, (key >> 8) & 0xFF, (key >> 16) & 0xFF]
    seed = (key >> 24) & 0xFF
    return (value ^ ((kb[pos % 3] + (seed * (pos // 3))) & 0xFF)) & 0xFF


def _xor_u8(buf: bytearray, pos: int, key: int, value: int) -> None:
    buf[pos] = _decrypt_byte(pos, value & 0xFF, key)


def _xor_u16be(buf: bytearray, pos: int, key: int, value: int) -> None:
    _xor_u8(buf, pos, key, (value >> 8) & 0xFF)
    _xor_u8(buf, pos + 1, key, value & 0xFF)


def _xor_u32be(buf: bytearray, pos: int, key: int, value: int) -> None:
    _xor_u8(buf, pos, key, (value >> 24) & 0xFF)
    _xor_u8(buf, pos + 1, key, (value >> 16) & 0xFF)
    _xor_u8(buf, pos + 2, key, (value >> 8) & 0xFF)
    _xor_u8(buf, pos + 3, key, value & 0xFF)


def _read_u32be_xor(data: bytes, off: int, key: int) -> int:
    return (
        (_decrypt_byte(off, data[off], key) << 24)
        | (_decrypt_byte(off + 1, data[off + 1], key) << 16)
        | (_decrypt_byte(off + 2, data[off + 2], key) << 8)
        | _decrypt_byte(off + 3, data[off + 3], key)
    )


def _read_u16be_xor(data: bytes, off: int, key: int) -> int:
    return (_decrypt_byte(off, data[off], key) << 8) | _decrypt_byte(
        off + 1, data[off + 1], key
    )


def _read_utf8_xor(data: bytes, off: int, key: int) -> tuple[str, int]:
    length = _read_u16be_xor(data, off, key)
    raw = bytes(
        _decrypt_byte(off + 2 + i, data[off + 2 + i], key) for i in range(length)
    )
    return raw.decode("utf-8"), 2 + length


@dataclass
class SectionAEntry:
    value0: int
    value1: int


@dataclass
class UnknownEntry:
    values: list[SectionAEntry]
    value0: int
    value1: int


@dataclass
class SDL2Data:
    key: int
    unknown: list[UnknownEntry]
    dialogs: list[str]


@dataclass
class DbinEntry:
    unknown0: int
    unknown1: int
    sdl2: SDL2Data


def parse_sdl2(buf: bytes) -> SDL2Data:
    if buf[:4] != b"SDL2":
        raise ValueError("SDL2 signature missing")
    key = struct.unpack_from("<I", buf, 4)[0]
    table0 = _read_u16be_xor(buf, 8, key)
    dialogs_count = _read_u16be_xor(buf, 10, key)
    unknown: list[UnknownEntry] = []
    pos = 12
    for _ in range(table0):
        section_b = _read_u32be_xor(buf, pos, key)
        section_a = _read_u32be_xor(buf, pos + 4, key)
        pos += 8
        entries_n = _read_u32be_xor(buf, section_a, key)
        values = []
        a_off = section_a + 4
        for _ in range(entries_n):
            values.append(
                SectionAEntry(
                    _read_u32be_xor(buf, a_off, key),
                    _read_u32be_xor(buf, a_off + 4, key),
                )
            )
            a_off += 8
        unknown.append(
            UnknownEntry(
                values,
                _read_u32be_xor(buf, section_b, key),
                _read_u32be_xor(buf, section_b + 4, key),
            )
        )
    dialogs: list[str] = []
    for _ in range(dialogs_count):
        dialog_off = _read_u32be_xor(buf, pos, key)
        pos += 4
        text, _ = _read_utf8_xor(buf, dialog_off, key)
        dialogs.append(text)
    return SDL2Data(key, unknown, dialogs)


def build_sdl2(data: SDL2Data) -> bytes:
    section_a_length = len(data.unknown) * 4
    for entry in data.unknown:
        section_a_length += len(entry.values) * 8
    section_a_offset = 0xC + len(data.unknown) * 8 + len(data.dialogs) * 4
    section_b_offset = section_a_offset + section_a_length
    dialogs_offset = section_b_offset + len(data.unknown) * 8

    buf = bytearray(
        dialogs_offset + sum(len(d.encode("utf-8")) + 6 for d in data.dialogs) + 64
    )
    buf[0:4] = b"SDL2"
    struct.pack_into("<I", buf, 4, data.key)
    key = data.key
    _xor_u16be(buf, 8, key, len(data.unknown))
    _xor_u16be(buf, 10, key, len(data.dialogs))

    pos = 12
    cur_a = section_a_offset
    cur_b = section_b_offset
    for entry in data.unknown:
        _xor_u32be(buf, pos, key, cur_b)
        _xor_u32be(buf, pos + 4, key, cur_a)
        pos += 8

        _xor_u32be(buf, cur_a, key, len(entry.values))
        cur_a += 4
        for v in entry.values:
            _xor_u32be(buf, cur_a, key, v.value0)
            _xor_u32be(buf, cur_a + 4, key, v.value1)
            cur_a += 8

        _xor_u32be(buf, cur_b, key, entry.value0)
        _xor_u32be(buf, cur_b + 4, key, entry.value1)
        cur_b += 8

    cur_d = dialogs_offset
    for text in data.dialogs:
        _xor_u32be(buf, pos, key, cur_d)
        pos += 4
        raw = text.encode("utf-8")
        if len(raw) > 0xFFFF:
            raise ValueError("dialog too long")
        _xor_u16be(buf, cur_d, key, len(raw))
        for i, b in enumerate(raw):
            _xor_u8(buf, cur_d + 2 + i, key, b)
        cur_d += 2 + len(raw)
        while cur_d & 3:
            _xor_u8(buf, cur_d, key, 0)
            cur_d += 1

    return bytes(buf[:cur_d])


def parse_dbin2(data: bytes) -> tuple[int, int, list[DbinEntry]]:
    if data[:4] != b"DBN2":
        raise ValueError("DBN2 signature missing")
    key = struct.unpack_from("<I", data, 4)[0]
    entries_n = _read_u32be_xor(data, 8, key)
    unknown = _read_u32be_xor(data, 12, key)
    entries: list[DbinEntry] = []
    for i in range(entries_n):
        base = 0x10 + i * 0x10
        unk0 = _read_u32be_xor(data, base, key)
        unk1 = _read_u32be_xor(data, base + 4, key)
        off = _read_u32be_xor(data, base + 8, key)
        length = _read_u32be_xor(data, base + 12, key)
        sdl2 = parse_sdl2(data[off : off + length])
        entries.append(DbinEntry(unk0, unk1, sdl2))
    return key, unknown, entries


def build_dbin2(key: int, unknown: int, entries: list[DbinEntry]) -> bytes:
    data_offset = 0x10 + len(entries) * 0x10
    sdl2_bufs = [build_sdl2(e.sdl2) for e in entries]
    total = data_offset + sum(len(b) + 7 for b in sdl2_bufs)
    out = bytearray(total)
    out[0:4] = b"DBN2"
    struct.pack_into("<I", out, 4, key)
    _xor_u32be(out, 8, key, len(entries))
    _xor_u32be(out, 12, key, unknown)

    cur = data_offset
    for i, (entry, buf) in enumerate(zip(entries, sdl2_bufs)):
        base = 0x10 + i * 0x10
        _xor_u32be(out, base, key, entry.unknown0)
        _xor_u32be(out, base + 4, key, entry.unknown1)
        _xor_u32be(out, base + 8, key, cur)
        _xor_u32be(out, base + 12, key, len(buf))
        out[cur : cur + len(buf)] = buf
        cur += len(buf)
        while cur & 7:
            _xor_u8(out, cur, key, 0)
            cur += 1
    return bytes(out[:cur])


def replace_dialog_tokens(text: str) -> tuple[str, int]:
    n = 0
    for old, new in DBIN_REPLACEMENTS:
        c = text.count(old)
        if c:
            text = text.replace(old, new)
            n += c
    text2, m = TRAILING_PAD_RE.subn(r"\1", text)
    return text2, n + m


def patch_dbin2_file(path: Path) -> int:
    key, unknown, entries = parse_dbin2(path.read_bytes())
    total = 0
    for entry in entries:
        for i, dialog in enumerate(entry.sdl2.dialogs):
            new, n = replace_dialog_tokens(dialog)
            if n:
                entry.sdl2.dialogs[i] = new
                total += n
    if total:
        path.write_bytes(build_dbin2(key, unknown, entries))
    return total


def patch_dbin2_tree(root: Path) -> tuple[int, int]:
    """Patch NLP_01/NLP_02/script under root (or a flat tree of *.dbin2)."""
    files: list[Path] = []
    for pack in PACKS:
        pack_dir = root / pack
        if pack_dir.is_dir():
            files.extend(sorted(pack_dir.glob("*.dbin2")))
    if not files:
        files = sorted(root.rglob("*.dbin2"))
    touched = 0
    replacements = 0
    for path in files:
        n = patch_dbin2_file(path)
        if n:
            touched += 1
            replacements += n
    return touched, replacements


def replace_all(data: bytearray, old: str, new: str) -> int:
    old_b = old.encode("utf-8")
    new_b = new.encode("utf-8")
    if len(new_b) > len(old_b):
        raise ValueError(
            f"{new!r} is {len(new_b)} bytes but slot for {old!r} is only {len(old_b)}"
        )
    padded = new_b + b"\x00" * (len(old_b) - len(new_b))
    count = 0
    start = 0
    while True:
        i = data.find(old_b, start)
        if i < 0:
            break
        data[i : i + len(old_b)] = padded
        count += 1
        start = i + len(old_b)
    return count


def patch_name_table_bytes(data: bytearray) -> int:
    total = 0
    for old, new in TOKEN_REPLACEMENTS + NAME_REPLACEMENTS:
        total += replace_all(data, old, new)
    return total


def patch_resident_file(path: Path) -> int:
    data = bytearray(path.read_bytes())
    total = patch_name_table_bytes(data)
    if total:
        path.write_bytes(data)
    return total


def patch_img_name_table(path: Path) -> int:
    start, end = IMG_NAME_REGION
    data = bytearray(path.read_bytes())
    if end > len(data):
        raise ValueError(f"{path}: file too small for name table region")
    window = bytearray(data[start:end])
    total = patch_name_table_bytes(window)
    if total:
        data[start:end] = window
        path.write_bytes(data)
    return total


def apply_romfs_name_patches(romfs_dir: Path) -> dict[str, int]:
    """Patch scripts + resident + img.bin inside an extracted RomFS tree."""
    stats = {"dbin_files": 0, "dbin_repl": 0, "resident": 0, "img": 0}
    script_root = romfs_dir / "script" / "bin"
    if script_root.is_dir():
        touched, repl = patch_dbin2_tree(script_root)
        stats["dbin_files"] = touched
        stats["dbin_repl"] = repl
        print(f"[names] dbin2: {touched} files, {repl} replacements")

    resident = (
        romfs_dir / "SystemData" / "TextResource" / "textresource_resident_jpn.trb"
    )
    if resident.is_file():
        stats["resident"] = patch_resident_file(resident)
        print(f"[names] resident TRB: {stats['resident']} replacements")
    else:
        print(f"[names] resident TRB not found under {romfs_dir}")

    img = romfs_dir / "img.bin"
    if img.is_file():
        stats["img"] = patch_img_name_table(img)
        print(f"[names] img.bin name table: {stats['img']} replacements")
    else:
        print(f"[names] img.bin not found under {romfs_dir}")
    return stats


def patch_xml_scripts(scripts_dir: Path) -> tuple[int, int]:
    """Replace heroine tokens in finished XML (source of truth for rebuilds)."""
    files = sorted(scripts_dir.glob("*.xml"))
    touched = 0
    replacements = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        new, n = replace_dialog_tokens(text)
        if n:
            path.write_text(new, encoding="utf-8", newline="\n")
            touched += 1
            replacements += n
    return touched, replacements


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--romfs",
        type=Path,
        help="Extracted RomFS root — patch script/bin + resident + img.bin",
    )
    ap.add_argument(
        "--dbin",
        type=Path,
        help="Folder with NLP_01/NLP_02/script *.dbin2 (or any tree of .dbin2)",
    )
    ap.add_argument("--resident", type=Path, help="textresource_resident_jpn.trb")
    ap.add_argument("--img", type=Path, help="img.bin")
    ap.add_argument(
        "--xml",
        type=Path,
        help="assets/scripts — rewrite ▲高嶺＊＊▲ tokens to plain English",
    )
    args = ap.parse_args(argv)

    did = False
    if args.romfs:
        apply_romfs_name_patches(args.romfs.resolve())
        did = True
    if args.dbin:
        touched, repl = patch_dbin2_tree(args.dbin.resolve())
        print(f"[names] dbin2: {touched} files, {repl} replacements")
        did = True
    if args.resident:
        n = patch_resident_file(args.resident.resolve())
        print(f"[names] resident: {n} replacements")
        did = True
    if args.img:
        n = patch_img_name_table(args.img.resolve())
        print(f"[names] img.bin: {n} replacements")
        did = True
    if args.xml:
        touched, repl = patch_xml_scripts(args.xml.resolve())
        print(f"[names] xml: {touched} files, {repl} replacements")
        did = True
    if not did:
        ap.error("pass at least one of --romfs / --dbin / --resident / --img / --xml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
