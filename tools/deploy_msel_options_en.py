#!/usr/bin/env python3
"""Options EN for NCommonMSel pkg 5245 — exact-length zopfli ARC splice.

Ghidra ``OptionMenu_BindBtnTextures`` @ 001eb3dc binds these BCLIM names.
Prior freezes were from trailing NUL after a short zlib stream (same class as
CESA). Fix: zopfli-compress the patched ARC, then grow uncompressed padding
with urandom until compressed length == original cmp_len exactly (unused_data=0).
"""
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

from bclimutil import (  # noqa: E402
    canvas_for_pixel_bytes,
    d2xy,
    gcm,
    parse_bclim,
    png_to_bclim_a8_same_size,
)
from darcutil import DarcArchive  # noqa: E402
from img import ARC, FileWindow, Image as ImgBin, Package  # noqa: E402
from pack_images import PackError, splice_packages_into_img  # noqa: E402

from deploy_common import (  # noqa: E402
    UI_FONT,
    iter_deploy_targets,
    resolve_img_paths,
)

MOD_IMG, VANILLA = resolve_img_paths()

IMG_DATA = ROOT / "out" / "options_tex_extract" / "img_data"
PKG = 5245
FONT = UI_FONT  # bundled OFL (assets/fonts/MPLUS1p-Regular.ttf)
# Ghidra OptionMenu_BindBtnTextures + BindPlateTextures (incl. Display/Sound pages).
LABELS: list[tuple[str, str]] = [
    ("Com_M_Sel_Plate_Text03_00_00.bclim", "Options"),
    ("Com_M_Sel_Plate_Text03_01_00.bclim", "Display Settings"),
    ("Com_M_Sel_Plate_Text03_02_00.bclim", "Sound Settings"),
    ("Com_M_Sel_Btn_Text03_01_00.bclim", "Display Settings"),
    ("Com_M_Sel_Btn_Text03_02_00.bclim", "Sound Settings"),
    ("Com_M_Sel_Btn_Text04_04_00.bclim", "Network"),
    ("Com_M_Sel_Btn_Text03_05_00.bclim", "Password"),
    ("Com_M_Sel_Plate_Text03_06_00.bclim", "3DS System Clock"),
    ("Com_M_Sel_Plate_Text03_06_01.bclim", "3DS System Clock"),
]


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size=size)


def glyph_h(a: np.ndarray) -> int:
    ys, _ = np.where(a > 20)
    return int(ys.max() - ys.min() + 1) if len(ys) else 0


def decode_a8(raw: bytes) -> tuple[np.ndarray, int, int]:
    pix, w, h, _fmt, _ft = parse_bclim(raw)
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


def render_en_alpha(
    w: int, h: int, text: str, target_h: int, *, hard: bool = False
) -> np.ndarray:
    for size in range(target_h + 4, 7, -1):
        scale = 2
        big = Image.new("L", (w * scale, h * scale), 0)
        dr = ImageDraw.Draw(big)
        f = font(size * scale)
        b = dr.textbbox((0, 0), text, font=f)
        tw, th = b[2] - b[0], b[3] - b[1]
        if tw > w * scale - 6:
            continue
        x = max(2, (w * scale - tw) // 2)
        y = (h * scale - th) // 2 - b[1]
        dr.text((x, y), text, font=f, fill=255)
        resample = Image.Resampling.NEAREST if hard else Image.Resampling.BILINEAR
        cand = np.array(big.resize((w, h), resample))
        if hard:
            cand = (cand >= 80).astype(np.uint8) * 255
        else:
            cand = np.clip(cand.astype(np.float32) * 0.95, 0, 255).astype(np.uint8)
        if glyph_h(cand) <= target_h + 2:
            return cand
    raise RuntimeError(f"cannot fit {text!r}")


def make_en_bclim(raw: bytes, en: str, tmp: Path, *, hard: bool = False) -> bytes:
    canvas, w, h = decode_a8(raw)
    jp = canvas[:h, :w]
    ys, _ = np.where(jp > 40)
    th = int(ys.max() - ys.min() + 1) if len(ys) else h // 2
    en_a = render_en_alpha(w, h, en, th, hard=hard)
    # Full clear — shorter EN must not leave JP glyph stain.
    out = np.zeros_like(jp)
    out = np.maximum(out, en_a)
    rgba = Image.merge(
        "RGBA",
        (Image.new("L", (w, h), 255),) * 3 + (Image.fromarray(out, "L"),),
    )
    png = tmp / "t.png"
    orig = tmp / "o.bclim"
    rgba.save(png)
    orig.write_bytes(raw)
    return png_to_bclim_a8_same_size(png, orig)


def patch_arc(vanilla_arc: bytes, tmp: Path, cmp_len: int) -> bytes:
    best: tuple[int, bytes] | None = None
    for hard in (False, True):
        darc = DarcArchive(bytearray(vanilla_arc))
        for base, en in LABELS:
            entry = darc.find(f"timg/{base}") or darc.find(base)
            if entry is None:
                raise SystemExit(f"missing {base}")
            darc.replace_same_size(
                entry, make_en_bclim(darc.extract_file(entry), en, tmp, hard=hard)
            )
            print(f"OK {base} -> {en!r} hard={hard}")
        patched = bytes(darc.data)
        z = len(zopfli_zlib.compress(patched))
        print(f"  trial hard={hard}: zopfli={z} slot={cmp_len}")
        if best is None or z < best[0]:
            best = (z, patched)
        if z <= cmp_len:
            return patched
    assert best is not None
    if best[0] > cmp_len:
        raise SystemExit(f"no trial fits slot {cmp_len} (best zopfli={best[0]})")
    return best[1]


def interfile_zero_gaps(data: bytes) -> list[tuple[int, int]]:
    """Zero padding between DARC files only (never inside BCLIM payloads)."""
    darc = DarcArchive(data)
    spans = sorted((e.offset, e.offset + e.length) for e in darc.files)
    gaps: list[tuple[int, int]] = []
    for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
        if b0 > a1:
            gaps.append((a1, b0))
    if spans and spans[-1][1] < len(data):
        gaps.append((spans[-1][1], len(data)))
    safe: list[tuple[int, int]] = []
    for g0, g1 in gaps:
        chunk = data[g0:g1]
        if len(chunk) >= 16 and chunk == b"\x00" * len(chunk):
            safe.append((g1 - g0, g0))
    safe.sort(reverse=True)
    return safe


def compress_exact_empty_blocks(data: bytes, exact_len: int) -> bytes | None:
    """Pad a short zlib stream to exact_len with empty stored blocks."""
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


def compress_exact_zopfli(data: bytes, target: int) -> tuple[bytes, bytes]:
    """Return (possibly padded uncompressed, zlib stream of len==target)."""
    base_z = len(zopfli_zlib.compress(data))
    if base_z > target:
        raise SystemExit(f"zopfli {base_z} already exceeds slot {target}")
    if base_z == target:
        slot = zopfli_zlib.compress(data)
        return data, slot

    # Prefer empty-block pad when under by a lot (gap pad can't always grow enough).
    if target - base_z >= 64:
        slot = compress_exact_empty_blocks(data, target)
        if slot is not None:
            print(f"  empty-block pad (zopfli was {base_z})", flush=True)
            return data, slot

    runs = interfile_zero_gaps(data)
    if not runs:
        slot = compress_exact_empty_blocks(data, target)
        if slot is None:
            raise SystemExit("no gaps and empty-block pad failed")
        return data, slot
    cap = sum(sz for sz, _ in runs)
    rng = os.urandom(cap)
    chunks: list[tuple[int, int, bytes]] = []
    off = 0
    for sz, po in runs:
        chunks.append((po, sz, rng[off : off + sz]))
        off += sz

    def apply_prefix(n_bytes: int) -> bytes:
        t = bytearray(data)
        left = n_bytes
        for po, sz, ch in chunks:
            take = min(left, sz)
            if take:
                t[po : po + take] = ch[:take]
            left -= take
            if left <= 0:
                break
        return bytes(t)

    lo, hi = 0, cap
    hit: bytes | None = None
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = apply_prefix(mid)
        cl = len(zopfli_zlib.compress(cand))
        print(f"  pad_bytes={mid} zopfli={cl}")
        if cl == target:
            hit = cand
            break
        if cl < target:
            lo = mid + 1
        else:
            hi = mid - 1

    if hit is None:
        t = bytearray(apply_prefix(max(hi, 0)))
        for po, sz, ch in chunks:
            for i in range(sz):
                if t[po + i] != 0:
                    continue
                t[po + i] = ch[i]
                cl = len(zopfli_zlib.compress(bytes(t)))
                if cl == target:
                    hit = bytes(t)
                    break
                if cl > target:
                    t[po + i] = 0
            if hit is not None:
                break
        if hit is None:
            slot = compress_exact_empty_blocks(data, target)
            if slot is not None:
                print("  fallback empty-block on unpadded ARC", flush=True)
                return data, slot
            raise SystemExit("could not hit exact zopfli length")

    slot = zopfli_zlib.compress(hit)
    do = zlib.decompressobj()
    got = do.decompress(slot)
    if got != hit or do.unused_data or not do.eof:
        raise SystemExit("exact stream verify failed")
    return hit, slot


def splice_arc(src_pkg: Path, patched_arc: bytes, dst_pkg: Path) -> None:
    blob = bytearray(src_pkg.read_bytes())
    entry_off = Package.ENTRY_SIZE
    _typ, dec_len, _do, _fl, is_cmp, cmp_len, cmp_off = Package.parse_entry(
        bytes(blob[entry_off : entry_off + Package.ENTRY_SIZE])
    )
    if not is_cmp:
        raise SystemExit("ARC not compressed")
    if len(patched_arc) != dec_len:
        raise SystemExit(f"ARC dec size {len(patched_arc)} != {dec_len}")

    tuned, slot = compress_exact_zopfli(patched_arc, cmp_len)
    print(f"ARC exact zopfli {len(slot)} unused_data=0")
    blob[cmp_off : cmp_off + cmp_len] = slot

    vanilla = src_pkg.read_bytes()
    if blob[:cmp_off] != vanilla[:cmp_off]:
        raise SystemExit("header region changed")
    if blob[cmp_off + cmp_len :] != vanilla[cmp_off + cmp_len :]:
        raise SystemExit("post-ARC region changed")
    if (
        blob[entry_off : entry_off + Package.ENTRY_SIZE]
        != vanilla[entry_off : entry_off + Package.ENTRY_SIZE]
    ):
        raise SystemExit("ARC entry header changed")

    dst_pkg.write_bytes(blob)
    pkg = Package(FileWindow(str(dst_pkg)), 0)
    pkg.parse(False)
    orig = Package(FileWindow(str(src_pkg)), 0)
    orig.parse(False)
    for a, b in zip(orig.entries, pkg.entries):
        da, db = a.parsed(), b.parsed()
        print(f"  verify {b.fn}: {len(db)}")
        if isinstance(a, ARC):
            if db != tuned:
                raise SystemExit("ARC content mismatch")
        elif da != db:
            raise SystemExit(f"DMST changed {a.fn}")
    print("DMST unchanged OK")


def main() -> None:
    bak = MOD_IMG.with_suffix(".bin.bak_pre_msel5245")
    if not bak.is_file():
        if not MOD_IMG.is_file():
            raise SystemExit(f"missing {MOD_IMG}")
        bak.write_bytes(MOD_IMG.read_bytes())
        print("created bak from current mod img")

    image = ImgBin(str(bak))
    image.parse(False)
    res = image.entries[PKG]
    if res is None:
        raise SystemExit(f"pkg {PKG} missing")
    src_pkg = IMG_DATA / f"{PKG:04d}"
    IMG_DATA.mkdir(parents=True, exist_ok=True)
    src_pkg.write_bytes(
        bak.read_bytes()[res.fw.base_offset : res.fw.base_offset + res.fw.len()]
    )
    print(f"vanilla package {PKG} ({src_pkg.stat().st_size} bytes)")

    tmp = ROOT / "out" / "msel5245_en" / "_fit"
    tmp.mkdir(parents=True, exist_ok=True)

    pkg = Package(FileWindow(str(src_pkg)), 0)
    pkg.parse(False)
    arc_elem = next(e for e in pkg.entries if isinstance(e, ARC))
    print(f"vanilla ARC {len(arc_elem.parsed())} cmp_slot={arc_elem.fw.len()}")

    patched_arc = patch_arc(arc_elem.parsed(), tmp, arc_elem.fw.len())
    new_pkg = IMG_DATA / f"new_{PKG:04d}"
    splice_arc(src_pkg, patched_arc, new_pkg)

    # Splice into live MOD — never use bak as img base (that copies bak over
    # the whole LayeredFS img and wipes later package EN patches).
    try:
        for _dest in iter_deploy_targets(MOD_IMG):
            splice_packages_into_img(_dest, IMG_DATA, [PKG], _dest)
    except PackError as exc:
        raise SystemExit(f"splice failed: {exc}") from exc

    print("deployed exact-zopfli Options EN ->", MOD_IMG)
    print("Fully quit Azahar and open Options.")
    print("Rollback: tools/restore_img_pre_msel5245.ps1")


if __name__ == "__main__":
    main()
