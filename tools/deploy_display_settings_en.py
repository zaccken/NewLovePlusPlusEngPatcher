#!/usr/bin/env python3
"""EN Display + Sound Settings chrome in Option.arc pkg 5247.

Rebuilds from vanilla so HelpBtn is Every Time / Once (not Defaults).
Also keeps Sound panel labels (SE / Voice / Mic Sensitivity).

Floating 初期設定 (Defaults) is NOT in this package — still unmapped.
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

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

OUT = ROOT / "out" / "display_set_en"
FONT = UI_FONT  # bundled OFL (assets/fonts/MPLUS1p-Regular.ttf)
PKG = 5247
INK = (51, 51, 51)

# All RGBA4444 (CLIM fmt 8) in Option.arc.
LABELS: list[tuple[str, str, str]] = [
    # Display Settings
    ("timg/Opt_TxtItem_Help.bclim", "Help Display", "label"),
    ("timg/Opt_TxtItem_Message.bclim", "Message Speed", "label"),
    ("timg/Opt_HelpBtn_A_A.bclim", "Every Time", "button"),
    ("timg/Opt_HelpBtn_A_B.bclim", "Every Time", "button"),
    ("timg/Opt_HelpBtn_B_A.bclim", "Once", "button"),
    ("timg/Opt_HelpBtn_B_B.bclim", "Once", "button"),
    # Sound Settings (same package)
    ("timg/Opt_txtItem_SE.bclim", "SE", "label"),
    ("timg/Opt_txtItem_VOICE.bclim", "Voice", "label"),
    ("timg/Opt_txtItem_MIC.bclim", "Mic Sensitivity", "label"),
]


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size=size)


def render_label(w: int, h: int, text: str) -> Image.Image:
    for size in range(min(14, h + 2), 6, -1):
        scale = 2
        big = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
        dr = ImageDraw.Draw(big)
        f = font(size * scale)
        b = dr.textbbox((0, 0), text, font=f)
        tw, th = b[2] - b[0], b[3] - b[1]
        if tw > w * scale - 2:
            continue
        x = 1 * scale - b[0]
        y = (h * scale - th) // 2 - b[1]
        dr.text((x, y), text, font=f, fill=INK + (255,))
        return big.resize((w, h), Image.Resampling.BILINEAR)
    raise RuntimeError(f"cannot fit label {text!r} in {w}x{h}")


def render_button(w: int, h: int, text: str) -> Image.Image:
    """White pill with grey border + centered dark text."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    dr.rounded_rectangle(
        (0, 0, w - 1, h - 1), radius=h // 2 - 1, fill=(255, 255, 255, 255)
    )
    dr.rounded_rectangle(
        (0, 0, w - 1, h - 1),
        radius=h // 2 - 1,
        outline=(180, 180, 180, 255),
        width=1,
    )
    for size in range(min(12, h - 6), 6, -1):
        f = font(size)
        b = dr.textbbox((0, 0), text, font=f)
        tw, th = b[2] - b[0], b[3] - b[1]
        if tw > w - 10:
            continue
        x = (w - tw) // 2 - b[0]
        y = (h - th) // 2 - b[1]
        dr.text((x, y), text, font=f, fill=(40, 40, 40, 255))
        return im
    raise RuntimeError(f"cannot fit button {text!r} in {w}x{h}")


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
    if not VANILLA.is_file():
        raise SystemExit(f"missing {VANILLA}")
    bak = MOD_IMG.with_suffix(".bin.bak_pre_display_set")
    if not bak.is_file():
        bak.write_bytes(MOD_IMG.read_bytes())
        print("created", bak, flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    pkg_dir = OUT / "img_data"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "_fit"
    tmp.mkdir(parents=True, exist_ok=True)

    raw = VANILLA.read_bytes()
    img = ImgBin(str(VANILLA))
    img.parse(False)
    ent = img.entries[PKG]
    src = pkg_dir / f"{PKG:04d}"
    src.write_bytes(raw[ent.fw.base_offset : ent.fw.base_offset + ent.fw.len()])
    pkg = Package(FileWindow(str(src)), 0)
    pkg.parse(False)
    arc = next(e for e in pkg.entries if isinstance(e, ARC))
    cmp = arc.fw.len()
    print(f"pkg {PKG} slot={cmp}", flush=True)

    darc = DarcArchive(bytearray(arc.parsed()))
    for path, en, kind in LABELS:
        entry = darc.find(path)
        raw_b = darc.extract_file(entry)
        _pix, w, h, fmt, _ = parse_bclim(raw_b)
        if fmt != 8:
            raise SystemExit(f"{path} fmt {fmt} (expected RGBA4444=8)")
        rgba = render_button(w, h, en) if kind == "button" else render_label(w, h, en)
        png = tmp / "t.png"
        orig = tmp / "o.bclim"
        rgba.save(png)
        rgba.save(OUT / f"{Path(path).stem}_en.png")
        orig.write_bytes(raw_b)
        darc.replace_same_size(entry, png_to_bclim_rgba4444_same_size(png, orig))
        print(f"OK {path} -> {en!r}", flush=True)

    patched = bytes(darc.data)
    z0 = len(zopfli_zlib.compress(patched))
    print(f"patched zopfli={z0} slot={cmp}", flush=True)
    if z0 > cmp:
        raise SystemExit("exceeds slot")
    slot = compress_exact_empty_blocks(patched, cmp)
    if slot is None:
        raise SystemExit("exact zlib failed")
    do = zlib.decompressobj()
    got = do.decompress(slot)
    if got != patched or do.unused_data or not do.eof:
        raise SystemExit("zlib verify failed")
    print(f"ARC exact {len(slot)}", flush=True)

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
    print("deployed Display/Sound Settings EN ->", MOD_IMG, flush=True)
    print("Rollback:", bak, flush=True)


if __name__ == "__main__":
    main()
