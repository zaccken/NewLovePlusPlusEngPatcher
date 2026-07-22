#!/usr/bin/env python3
"""Patch Options submenu plates/buttons still JP on live pkg 5245."""
from __future__ import annotations

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

from bclimutil import (  # noqa: E402
    canvas_for_pixel_bytes,
    d2xy,
    gcm,
    parse_bclim,
    png_to_bclim_a8_same_size,
)
from darcutil import DarcArchive  # noqa: E402
from img import ARC, FileWindow, Image as ImgBin, Package  # noqa: E402
from pack_images import splice_packages_into_img  # noqa: E402

from deploy_common import (  # noqa: E402
    UI_FONT,
    iter_deploy_targets,
    resolve_img_paths,
)

MOD_IMG, VANILLA = resolve_img_paths()

OUT = ROOT / "out" / "msel5245_plates"
FONT = UI_FONT  # bundled OFL (assets/fonts/MPLUS1p-Regular.ttf)
PKG = 5245

# Minimal set: page plates still JP on live; buttons already short-EN.
LABELS = [
    ("timg/Com_M_Sel_Plate_Text03_01_00.bclim", "Display Settings"),
    ("timg/Com_M_Sel_Plate_Text03_02_00.bclim", "Sound Settings"),
]


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size=size)


def glyph_h(a: np.ndarray) -> int:
    ys, _ = np.where(a > 20)
    return int(ys.max() - ys.min() + 1) if len(ys) else 0


def decode_a8(raw: bytes) -> tuple[np.ndarray, int, int]:
    pix, w, h, _fmt, _ = parse_bclim(raw)
    pot_w, pot_h = canvas_for_pixel_bytes(len(pix), w, h, 1)
    canvas = np.zeros((pot_h, pot_w), dtype=np.uint8)
    tiles_x = max(1, gcm(pot_w, 8) // 8)
    for i, a in enumerate(pix):
        mx, my = d2xy(i % 64)
        tile = i // 64
        x = mx + (tile % tiles_x) * 8
        y = my + (tile // tiles_x) * 8
        if x < pot_w and y < pot_h:
            canvas[y, x] = a
    return canvas, w, h


def render(w: int, h: int, text: str, target_h: int, *, hard: bool) -> np.ndarray:
    for size in range(target_h + 4, 7, -1):
        scale = 2
        big = Image.new("L", (w * scale, h * scale), 0)
        dr = ImageDraw.Draw(big)
        f = font(size * scale)
        b = dr.textbbox((0, 0), text, font=f)
        tw, th = b[2] - b[0], b[3] - b[1]
        if tw > w * scale - 4:
            continue
        x = (w * scale - tw) // 2
        y = (h * scale - th) // 2 - b[1]
        dr.text((x, y), text, font=f, fill=255)
        resample = Image.Resampling.NEAREST if hard else Image.Resampling.BILINEAR
        cand = np.array(big.resize((w, h), resample))
        if hard:
            cand = (cand >= 80).astype(np.uint8) * 255
        if glyph_h(cand) <= target_h + 2:
            return cand
    raise RuntimeError(text)


def make_bclim(raw: bytes, en: str, tmp: Path, *, hard: bool) -> bytes:
    canvas, w, h = decode_a8(raw)
    jp = canvas[:h, :w]
    ys, _ = np.where(jp > 40)
    th = int(ys.max() - ys.min() + 1) if len(ys) else h // 2
    en_a = render(w, h, en, th, hard=hard)
    out = np.maximum(np.zeros_like(jp), en_a)
    rgba = Image.merge(
        "RGBA",
        (Image.new("L", (w, h), 255),) * 3 + (Image.fromarray(out, "L"),),
    )
    png = tmp / "t.png"
    orig = tmp / "o.bclim"
    rgba.save(png)
    stem = en.replace(" ", "_")
    rgba.save(OUT / f"{stem}.png")
    orig.write_bytes(raw)
    return png_to_bclim_a8_same_size(png, orig)


def compress_exact_empty_blocks(data: bytes, exact_len: int) -> bytes | None:
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

    for level in range(10):
        co = zlib.compressobj(level, wbits=-15)
        hit = try_body(co.compress(data) + co.flush(zlib.Z_SYNC_FLUSH))
        if hit is not None:
            return hit
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


def main() -> None:
    if not MOD_IMG.is_file():
        raise SystemExit(f"missing {MOD_IMG}")
    bak = MOD_IMG.with_suffix(".bin.bak_pre_opt_plates")
    if not bak.is_file():
        bak.write_bytes(MOD_IMG.read_bytes())
        print("created", bak, flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    pkg_dir = OUT / "img_data"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "_fit"
    tmp.mkdir(parents=True, exist_ok=True)

    raw = MOD_IMG.read_bytes()
    img = ImgBin(str(MOD_IMG))
    img.parse(False)
    ent = img.entries[PKG]
    src = pkg_dir / f"{PKG:04d}"
    src.write_bytes(raw[ent.fw.base_offset : ent.fw.base_offset + ent.fw.len()])
    pkg = Package(FileWindow(str(src)), 0)
    pkg.parse(False)
    arc = next(e for e in pkg.entries if isinstance(e, ARC))
    cmp = arc.fw.len()
    print(f"pkg {PKG} slot={cmp}", flush=True)

    patched: bytes | None = None
    for hard in (False, True):
        darc = DarcArchive(bytearray(arc.parsed()))
        for path, en in LABELS:
            entry = darc.find(path)
            darc.replace_same_size(
                entry, make_bclim(darc.extract_file(entry), en, tmp, hard=hard)
            )
            print(f"OK {path} -> {en!r} hard={hard}", flush=True)
        cand = bytes(darc.data)
        z = len(zopfli_zlib.compress(cand))
        print(f"  zopfli={z}", flush=True)
        if z <= cmp:
            patched = cand
            break
        patched = cand
    assert patched is not None
    z0 = len(zopfli_zlib.compress(patched))
    if z0 > cmp:
        raise SystemExit(f"patched ARC zopfli {z0} exceeds slot {cmp}")

    slot = compress_exact_empty_blocks(patched, cmp)
    tuned = patched
    if slot is None:
        print("empty-block miss; tuning zopfli via transparent-pixel noise…", flush=True)
        import os

        t = bytearray(patched)
        # Nudge trailing zeros inside each patched BCLIM payload (alpha=0 already).
        rng = os.urandom(4096)
        ri = 0
        for path, _en in LABELS:
            darc = DarcArchive(bytes(t))
            entry = darc.find(path)
            # last 64 bytes of file payload before CLIM footer often pad
            start = entry.offset
            end = entry.offset + entry.length
            for i in range(end - 80, end - 40):
                if t[i] == 0:
                    t[i] = rng[ri % len(rng)] or 1
                    ri += 1
                    z = len(zopfli_zlib.compress(bytes(t)))
                    if z == cmp:
                        tuned = bytes(t)
                        slot = zopfli_zlib.compress(tuned)
                        print(f"  zopfli hit via noise @ {i:#x}", flush=True)
                        break
                    if z > cmp:
                        t[i] = 0
            if slot is not None:
                break
        if slot is None:
            slot = compress_exact_empty_blocks(bytes(t), cmp)
            if slot is not None:
                tuned = bytes(t)
                print("  empty-block hit after noise", flush=True)
    if slot is None:
        raise SystemExit("could not build exact zlib stream")
    do = zlib.decompressobj()
    got = do.decompress(slot)
    if got != tuned or do.unused_data or not do.eof:
        raise SystemExit("zlib verify failed")
    print(f"ARC exact {len(slot)}", flush=True)
    patched = tuned

    blob = bytearray(src.read_bytes())
    entry_off = Package.ENTRY_SIZE
    _typ, dec_len, _do, _fl, is_cmp, slot_len, cmp_off = Package.parse_entry(
        bytes(blob[entry_off : entry_off + Package.ENTRY_SIZE])
    )
    if not is_cmp or slot_len != cmp or len(patched) != dec_len:
        raise SystemExit("ARC entry mismatch")
    blob[cmp_off : cmp_off + cmp] = slot
    new_pkg = pkg_dir / f"new_{PKG:04d}"
    new_pkg.write_bytes(blob)

    pkg2 = Package(FileWindow(str(new_pkg)), 0)
    pkg2.parse(False)
    for a, b in zip(pkg.entries, pkg2.entries):
        if isinstance(a, ARC):
            if b.parsed() != patched:
                raise SystemExit("ARC mismatch")
        elif a.parsed() != b.parsed():
            raise SystemExit("DMST changed")
    print("DMST OK", flush=True)
    for _dest in iter_deploy_targets(MOD_IMG):
        splice_packages_into_img(_dest, pkg_dir, [PKG], _dest)
    print("deployed Options plates/buttons EN ->", MOD_IMG, flush=True)
    print("Rollback:", bak, flush=True)
    print(
        "Also refresh Azahar Custom Textures (load/textures) + full restart.",
        flush=True,
    )


if __name__ == "__main__":
    main()
