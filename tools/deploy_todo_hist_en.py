#!/usr/bin/env python3
"""EN To-Do History: points label (Que_Txt01c) + History header wording."""
from __future__ import annotations

import os
import struct
import sys
import zlib
from pathlib import Path

import numpy as np
import zopfli.zlib as zopfli_zlib
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "nlpp-tools"))

from bclimutil import parse_bclim, png_to_bclim_rgba4444_same_size  # noqa: E402
from darcutil import DarcArchive  # noqa: E402
from img import ARC, FileWindow, Image as ImgBin, Package  # noqa: E402
from pack_images import splice_packages_into_img  # noqa: E402

from deploy_common import (  # noqa: E402
    UI_FONT,
    iter_deploy_targets,
    resolve_img_paths,
)

MOD_IMG, VANILLA = resolve_img_paths()

OUT = ROOT / "out" / "todo_hist_en"
FONT = UI_FONT  # bundled OFL (assets/fonts/MPLUS1p-Regular.ttf)
INK = (70, 110, 160)

QUEST_PKG = 5253
# Rebuild Quest from vanilla so zlib slot + gaps stay usable.
QUEST_LABELS = [
    ("timg/Que_Txt01b.bclim", "To-Do List"),
    ("timg/Que_Txt01d.bclim", "History"),
    ("timg/Que_Txt01c.bclim", "Earned ToDo Points:"),
]


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size=size)


def render_label(
    w: int,
    h: int,
    text: str,
    *,
    hard: bool = False,
    max_size: int | None = None,
    align: str = "center",
) -> Image.Image:
    top = max_size if max_size is not None else min(16, h + 2)
    for size in range(top, 7, -1):
        scale = 2
        big = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
        dr = ImageDraw.Draw(big)
        f = font(size * scale)
        b = dr.textbbox((0, 0), text, font=f)
        tw, th = b[2] - b[0], b[3] - b[1]
        pad = 2 if align == "left" else 4
        if tw > w * scale - pad:
            continue
        if align == "left":
            x = 2 * scale - b[0]
        else:
            x = (w * scale - tw) // 2 - b[0]
        y = (h * scale - th) // 2 - b[1]
        dr.text((x, y), text, font=f, fill=INK + (255,))
        resample = Image.Resampling.NEAREST if hard else Image.Resampling.BILINEAR
        im = big.resize((w, h), resample)
        if hard:
            arr = np.array(im)
            mask = arr[:, :, 3] >= 80
            arr[~mask] = 0
            arr[mask, 3] = 255
            im = Image.fromarray(arr, "RGBA")
        return im
    raise RuntimeError(f"cannot fit {text!r} in {w}x{h}")


def interfile_zero_gaps(data: bytes, min_len: int = 4) -> list[tuple[int, int]]:
    darc = DarcArchive(data)
    spans = sorted((e.offset, e.offset + e.length) for e in darc.files)
    gaps: list[tuple[int, int]] = []
    for (_a0, a1), (b0, _b1) in zip(spans, spans[1:]):
        if b0 > a1:
            gaps.append((a1, b0))
    if spans and spans[-1][1] < len(data):
        gaps.append((spans[-1][1], len(data)))
    out: list[tuple[int, int]] = []
    for g0, g1 in gaps:
        chunk = data[g0:g1]
        if len(chunk) >= min_len and chunk == b"\x00" * len(chunk):
            out.append((g1 - g0, g0))
    out.sort(reverse=True)
    return out


def apply_gap_pad(data: bytes, n_bytes: int, pad_rng: bytes) -> bytes:
    runs = interfile_zero_gaps(data)
    t = bytearray(data)
    left = n_bytes
    off = 0
    for sz, po in runs:
        take = min(left, sz)
        if take:
            t[po : po + take] = pad_rng[off : off + take]
        left -= take
        off += sz
        if left <= 0:
            break
    return bytes(t)


def compress_exact_empty_blocks(data: bytes, exact_len: int) -> bytes | None:
    """Build a zlib stream of exactly exact_len via sync-flush + empty stored blocks."""
    adler = struct.pack(">I", zlib.adler32(data) & 0xFFFFFFFF)
    strategies = (
        zlib.Z_DEFAULT_STRATEGY,
        zlib.Z_FILTERED,
        zlib.Z_HUFFMAN_ONLY,
        zlib.Z_RLE,
        zlib.Z_FIXED,
    )
    hdrs = (b"\x78\x9c", b"\x78\xda", b"\x78\x5e", b"\x78\x01")

    def try_body(body: bytes) -> bytes | None:
        for hdr in hdrs:
            remain = exact_len - len(hdr) - 4 - len(body)
            if remain < 5 or remain % 5 != 0:
                continue
            n_empty = remain // 5
            out = (
                hdr
                + body
                + b"\x00\x00\x00\xff\xff" * (n_empty - 1)
                + b"\x01\x00\x00\xff\xff"
                + adler
            )
            if len(out) != exact_len:
                continue
            d = zlib.decompressobj()
            try:
                got = d.decompress(out)
            except zlib.error:
                continue
            if got == data and not d.unused_data and d.eof:
                return out
        return None

    # Fast path: default memLevel / strategy.
    for level in range(10):
        co = zlib.compressobj(level, wbits=-15)
        hit = try_body(co.compress(data) + co.flush(zlib.Z_SYNC_FLUSH))
        if hit is not None:
            return hit
    # Slow path: vary memLevel/strategy for mod-5 alignment when zero-gaps are gone.
    for level in range(10):
        for mem in range(1, 10):
            for strat in strategies:
                try:
                    co = zlib.compressobj(level, zlib.DEFLATED, -15, mem, strat)
                    hit = try_body(co.compress(data) + co.flush(zlib.Z_SYNC_FLUSH))
                except zlib.error:
                    continue
                if hit is not None:
                    return hit
    return None


def compress_exact_with_gap_tune(data: bytes, exact_len: int) -> tuple[bytes, bytes]:
    print("  trying zlib memLevel/strategy empty-block pad…", flush=True)
    slot = compress_exact_empty_blocks(data, exact_len)
    if slot is not None:
        print("  hit empty-block (no gap pad)", flush=True)
        return data, slot
    runs = interfile_zero_gaps(data)
    cap = sum(sz for sz, _ in runs)
    pad_rng = os.urandom(cap) if cap else b""
    print(f"  gap capacity={cap}; tuning…", flush=True)
    step = max(1, cap // 400) if cap else 1
    for n in list(range(cap, -1, -step)) + list(range(cap, -1, -1)):
        cand = apply_gap_pad(data, n, pad_rng) if cap else data
        slot = compress_exact_empty_blocks(cand, exact_len)
        if slot is not None:
            print(f"  hit pad_bytes={n}", flush=True)
            return cand, slot
    raise SystemExit("could not build exact zlib stream")


def compress_exact_zopfli(data: bytes, target: int) -> tuple[bytes, bytes]:
    z0 = len(zopfli_zlib.compress(data))
    if z0 > target:
        return compress_exact_with_gap_tune(data, target)
    if z0 == target:
        return data, zopfli_zlib.compress(data)
    runs = interfile_zero_gaps(data, min_len=8)
    cap = sum(sz for sz, _ in runs)
    rng = os.urandom(cap) if cap else b""
    lo, hi = 0, cap
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = apply_gap_pad(data, mid, rng) if cap else data
        z = zopfli_zlib.compress(cand)
        if len(z) == target:
            return cand, z
        if len(z) < target:
            lo = mid + 1
        else:
            hi = mid - 1
    base_n = max(hi, 0)
    t = bytearray(apply_gap_pad(data, base_n, rng) if cap else data)
    runs2 = interfile_zero_gaps(bytes(t))
    for sz, po in runs2:
        for i in range(sz):
            if t[po + i] != 0:
                continue
            t[po + i] = rng[po % len(rng)] if rng else 1
            z = zopfli_zlib.compress(bytes(t))
            if len(z) == target:
                return bytes(t), z
            if len(z) > target:
                t[po + i] = 0
    return compress_exact_with_gap_tune(data, target)


def splice_pkg(pkg_id: int, src_img: Path, patched: bytes, cmp_len: int) -> None:
    raw = src_img.read_bytes()
    img = ImgBin(str(src_img))
    img.parse(False)
    res = img.entries[pkg_id]
    pkg_dir = OUT / "img_data"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    src_pkg = pkg_dir / f"{pkg_id:04d}"
    src_pkg.write_bytes(raw[res.fw.base_offset : res.fw.base_offset + res.fw.len()])

    pkg = Package(FileWindow(str(src_pkg)), 0)
    pkg.parse(False)
    arc_elem = next(e for e in pkg.entries if isinstance(e, ARC))
    if arc_elem.fw.len() != cmp_len:
        raise SystemExit("slot mismatch")

    z0 = len(zopfli_zlib.compress(patched))
    print(f"  patched zopfli={z0} slot={cmp_len}", flush=True)
    if z0 <= cmp_len and (cmp_len - z0) < 4000:
        tuned, slot = compress_exact_zopfli(patched, cmp_len)
    else:
        tuned, slot = compress_exact_with_gap_tune(patched, cmp_len)
    do = zlib.decompressobj()
    got = do.decompress(slot)
    if got != tuned or do.unused_data or not do.eof:
        raise SystemExit("zlib verify failed")
    print(f"  ARC exact {len(slot)}", flush=True)

    blob = bytearray(src_pkg.read_bytes())
    entry_off = Package.ENTRY_SIZE
    _typ, dec_len, _do, _fl, is_cmp, slot_len, cmp_off = Package.parse_entry(
        bytes(blob[entry_off : entry_off + Package.ENTRY_SIZE])
    )
    if not is_cmp or slot_len != cmp_len or len(tuned) != dec_len:
        raise SystemExit("ARC entry mismatch")
    blob[cmp_off : cmp_off + cmp_len] = slot
    new_pkg = pkg_dir / f"new_{pkg_id:04d}"
    new_pkg.write_bytes(blob)

    pkg2 = Package(FileWindow(str(new_pkg)), 0)
    pkg2.parse(False)
    for a, b in zip(pkg.entries, pkg2.entries):
        if isinstance(a, ARC):
            if b.parsed() != tuned:
                raise SystemExit("ARC mismatch")
        elif a.parsed() != b.parsed():
            raise SystemExit("DMST changed")
    print("  DMST OK", flush=True)
    for _dest in iter_deploy_targets(MOD_IMG):
        splice_packages_into_img(_dest, pkg_dir, [pkg_id], _dest)


def patch_quest_labels(arc_bytes: bytes, tmp: Path) -> bytes:
    # Prefer full points phrasing; fall back if width is tight (120px).
    points_candidates = [
        "Earned ToDo Points:",
        "Earned ToDo Pts:",
        "ToDo Points:",
    ]
    last_err: Exception | None = None
    for pts in points_candidates:
        labels = [
            ("timg/Que_Txt01b.bclim", "To-Do List"),
            ("timg/Que_Txt01d.bclim", "History"),
            ("timg/Que_Txt01c.bclim", pts),
        ]
        try:
            darc = DarcArchive(bytearray(arc_bytes))
            for path, en in labels:
                entry = darc.find(path)
                raw_b = darc.extract_file(entry)
                _pix, w, h, fmt, _ = parse_bclim(raw_b)
                if fmt != 8:
                    raise SystemExit(f"{path} fmt {fmt}")
                align = "left" if path.endswith("Que_Txt01c.bclim") else "center"
                rgba = render_label(w, h, en, hard=False, align=align)
                png = tmp / "t.png"
                orig = tmp / "o.bclim"
                rgba.save(png)
                rgba.save(OUT / f"{Path(path).stem}_en.png")
                orig.write_bytes(raw_b)
                darc.replace_same_size(entry, png_to_bclim_rgba4444_same_size(png, orig))
                print(f"OK {path} -> {en!r}", flush=True)
            return bytes(darc.data)
        except RuntimeError as e:
            last_err = e
            print(f"  skip points {pts!r}: {e}", flush=True)
            continue
    raise SystemExit(f"no points label fits: {last_err}")


def main() -> None:
    if not MOD_IMG.is_file():
        raise SystemExit(f"missing {MOD_IMG}")
    if not VANILLA.is_file():
        raise SystemExit(f"missing vanilla {VANILLA}")
    bak = MOD_IMG.with_suffix(".bin.bak_pre_todo_hist")
    if not bak.is_file():
        bak.write_bytes(MOD_IMG.read_bytes())
        print("created", bak, flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    pkg_dir = OUT / "img_data"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    # Quest chrome from vanilla (full gaps). Header History remains on 5575.
    raw = VANILLA.read_bytes()
    img = ImgBin(str(VANILLA))
    img.parse(False)
    res = img.entries[QUEST_PKG]
    src = pkg_dir / f"{QUEST_PKG:04d}"
    src.write_bytes(raw[res.fw.base_offset : res.fw.base_offset + res.fw.len()])
    pkg = Package(FileWindow(str(src)), 0)
    pkg.parse(False)
    arc = next(e for e in pkg.entries if isinstance(e, ARC))
    cmp = arc.fw.len()
    print(f"pkg {QUEST_PKG} Quest EN (vanilla base) slot={cmp}", flush=True)
    tmp = OUT / "_fit_quest"
    tmp.mkdir(parents=True, exist_ok=True)
    patched = patch_quest_labels(arc.parsed(), tmp)
    splice_pkg(QUEST_PKG, VANILLA, patched, cmp)

    print("deployed To-Do History points EN ->", MOD_IMG, flush=True)
    print("Rollback:", bak, flush=True)


if __name__ == "__main__":
    main()
