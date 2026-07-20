#!/usr/bin/env python3
"""Patch the boot CESA anti-piracy texture in img.bin package 90.

In-place only: replaces the zlib-compressed TEX payload inside the original
PACK bytes and splices that package back into img.bin at the same offset.
Never rewrites the whole img.bin (Image.write relocates packages and can
black-screen boot). Never touches code.bin.
"""

from __future__ import annotations

import argparse
import shutil
import struct
import sys
import tempfile
import zlib
from pathlib import Path

from PIL import Image

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
NLPP_TOOLS = ROOT / "tools" / "nlpp-tools"
DEFAULT_ASSET = ROOT / "assets" / "images" / "cesa" / "CESA_240X400.png"
DEFAULT_IMG = Path(
    r"C:\Users\Zepse\Documents\New Love Plus Decompilation\New Love Plus Plus\extracted\romfs\img.bin"
)
CESA_PKG_INDEX = 90
CESA_TEX_NAME = "CESA_240X400.texi"
TEX_W, TEX_H = 256, 512
VISIBLE_W, VISIBLE_H = 240, 400
ENTRY_SIZE = 0x20
# Stock padding outside 240x400 is black, not white.
PAD_RGB = (0, 0, 0)


def _ensure_nlpp_path() -> None:
    tools = str(NLPP_TOOLS)
    if tools not in sys.path:
        sys.path.insert(0, tools)


def morton(n: int) -> tuple[int, int]:
    x = y = 0
    for b in range(8):
        x |= (n >> (2 * b) & 1) << b
        y |= (n >> (2 * b + 1) & 1) << b
    return x, y


def encode_cesa_tex(im: Image.Image, width: int = TEX_W, height: int = TEX_H) -> bytes:
    """RGB image → NLPP TEX bytes (BGR8 + 8x8 Morton). Padding is black like stock."""
    canvas = Image.new("RGB", (width, height), PAD_RGB)
    rgb = im.convert("RGB")
    if rgb.size != (VISIBLE_W, VISIBLE_H):
        rgb = rgb.resize((VISIBLE_W, VISIBLE_H), Image.Resampling.NEAREST)
    canvas.paste(rgb, (0, 0))
    px = canvas.load()
    out = bytearray(width * height * 3)
    i = 0
    tile = 8
    for ty in range(0, height, tile):
        for tx in range(0, width, tile):
            for n in range(tile * tile):
                x, y = morton(n)
                r, g, b = px[tx + x, ty + y]
                out[i] = b
                out[i + 1] = g
                out[i + 2] = r
                i += 3
    return bytes(out)


def decode_cesa_tex(
    raw: bytes,
    width: int = TEX_W,
    height: int = TEX_H,
    ow: int = VISIBLE_W,
    oh: int = VISIBLE_H,
) -> Image.Image:
    img = Image.new("RGB", (width, height))
    px = img.load()
    i = 0
    tile = 8
    for ty in range(0, height, tile):
        for tx in range(0, width, tile):
            for n in range(tile * tile):
                x, y = morton(n)
                o = i * 3
                px[tx + x, ty + y] = (raw[o + 2], raw[o + 1], raw[o])
                i += 1
    return img.crop((0, 0, ow, oh))


def _find_cesa_tex_entry(pkg_raw: bytes) -> tuple[int, int, int, int]:
    """Return (entry_table_off, cmp_off, cmp_len, dec_len) for CESA TEX."""
    _ensure_nlpp_path()
    from img import FileWindow, Package

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pkg") as tmp:
        tmp.write(pkg_raw)
        tmp_path = Path(tmp.name)
    try:
        pkg = Package(FileWindow(str(tmp_path)), 0)
        pkg.parse(False)
        for i, elem in enumerate(pkg.entries):
            if elem.fn == CESA_TEX_NAME and elem.typ == b"TEX ":
                entry_off = ENTRY_SIZE + i * ENTRY_SIZE
                _typ, dec_len, _dec_off, _flags, is_cmp, cmp_len, cmp_off = (
                    Package.parse_entry(pkg_raw[entry_off : entry_off + ENTRY_SIZE])
                )
                if not is_cmp:
                    raise RuntimeError("CESA TEX is not compressed")
                return entry_off, cmp_off, cmp_len, dec_len
    finally:
        tmp_path.unlink(missing_ok=True)
    raise RuntimeError(f"{CESA_TEX_NAME} TEX entry not found")


def read_cesa_tex_from_pkg(pkg_raw: bytes) -> bytes:
    """Decompress the stock CESA TEX payload from a PACK blob."""
    _entry_off, cmp_off, cmp_len, dec_len = _find_cesa_tex_entry(pkg_raw)
    tex = zlib.decompress(pkg_raw[cmp_off : cmp_off + cmp_len])
    if len(tex) != dec_len:
        raise RuntimeError(f"unexpected decompressed size {len(tex)} != {dec_len}")
    return tex


def compress_zlib_exact(data: bytes, exact_len: int) -> bytes:
    """Build a zlib stream of exactly ``exact_len`` bytes with no trailing unused input.

    Trailing NUL padding after a short zlib stream leaves ``unused_data``; the
    game's inflater appears to reject that (white CESA boot screen). Pad inside
    the deflate stream with empty stored blocks instead.
    """
    adler = struct.pack(">I", zlib.adler32(data) & 0xFFFFFFFF)
    for level in range(10):
        cand = zlib.compress(data, level)
        if len(cand) != exact_len:
            continue
        d = zlib.decompressobj()
        got = d.decompress(cand)
        if got == data and d.unused_data == b"" and d.eof:
            return cand

    for level in range(10):
        co = zlib.compressobj(level, wbits=-15)
        body = co.compress(data) + co.flush(zlib.Z_SYNC_FLUSH)
        budget = exact_len - 2 - 4
        remain = budget - len(body)
        if remain < 5 or remain % 5 != 0:
            continue
        n_empty = remain // 5
        extras = b"\x00\x00\x00\xff\xff" * (n_empty - 1)
        final = b"\x01\x00\x00\xff\xff"
        for hdr in (b"\x78\x9c", b"\x78\xda", b"\x78\x5e", b"\x78\x01"):
            out = hdr + body + extras + final + adler
            if len(out) != exact_len:
                continue
            try:
                d = zlib.decompressobj()
                got = d.decompress(out)
            except zlib.error:
                continue
            if got == data and d.unused_data == b"" and d.eof:
                return out

    raise RuntimeError(f"cannot build exact zlib stream of length {exact_len}")


def compress_tex_to_slot(tex: bytes, slot_len: int) -> bytes:
    """zlib-compress TEX into a fixed-size slot; keep cmp_len unchanged."""
    slot = compress_zlib_exact(tex, slot_len)
    d = zlib.decompressobj()
    check = d.decompress(slot)
    if check != tex or d.unused_data != b"":
        raise RuntimeError("round-trip decompress mismatch / leftover input")
    return slot


def patch_cesa_package_inplace_tex(pkg_raw: bytes, tex: bytes) -> bytes:
    """Replace CESA compressed TEX inside original PACK; keep size/layout/cmp_len."""
    _entry_off, cmp_off, old_cmp_len, dec_len = _find_cesa_tex_entry(pkg_raw)
    if len(tex) != dec_len:
        raise RuntimeError(f"TEX size {len(tex)} != dec_len {dec_len}")
    out = bytearray(pkg_raw)
    out[cmp_off : cmp_off + old_cmp_len] = compress_tex_to_slot(tex, old_cmp_len)
    return bytes(out)


def patch_cesa_package_inplace(pkg_raw: bytes, png: Path) -> bytes:
    """Replace CESA TEX from a 240x400 PNG."""
    return patch_cesa_package_inplace_tex(pkg_raw, encode_cesa_tex(Image.open(png)))


def patch_img_bin_tex(
    src_img: Path,
    tex: bytes,
    dst_img: Path,
    work: Path | None = None,
) -> Path:
    """Splice package 90 with a new decompressed TEX blob."""
    _ensure_nlpp_path()
    from img import Image as ImgBin

    src_img = Path(src_img).resolve()
    dst_img = Path(dst_img).resolve()
    if work is None:
        work = dst_img.parent / "cesa_img_work"
    work.mkdir(parents=True, exist_ok=True)

    im = ImgBin(str(src_img))
    im.parse(False)
    res = im.entries[CESA_PKG_INDEX]
    if res is None:
        raise RuntimeError(f"img.bin missing package {CESA_PKG_INDEX}")

    base = res.fw.base_offset
    pkg_len = res.fw.len()
    pkg_raw = res.fw.read()
    if len(pkg_raw) != pkg_len:
        raise RuntimeError("package read length mismatch")

    patched_pkg = patch_cesa_package_inplace_tex(pkg_raw, tex)
    if len(patched_pkg) != pkg_len:
        raise RuntimeError(
            f"in-place patch changed package size {pkg_len} -> {len(patched_pkg)}"
        )

    (work / f"{CESA_PKG_INDEX:04d}.bin").write_bytes(pkg_raw)
    (work / f"{CESA_PKG_INDEX:04d}_patched.bin").write_bytes(patched_pkg)

    if dst_img.resolve() != src_img.resolve():
        shutil.copy2(src_img, dst_img)
    data = bytearray(dst_img.read_bytes())
    data[base : base + pkg_len] = patched_pkg
    dst_img.write_bytes(data)

    im2 = ImgBin(str(dst_img))
    im2.parse(False)
    for i, (a, b) in enumerate(zip(im.entries, im2.entries)):
        if a is None and b is None:
            continue
        da = a.fw.read() if a else None
        db = b.fw.read() if b else None
        if i == CESA_PKG_INDEX:
            if da == db:
                raise RuntimeError("package 90 did not change")
            # Verify TEX round-trips to the intended bytes.
            got = read_cesa_tex_from_pkg(db)
            if got != tex:
                raise RuntimeError("patched package TEX mismatch after splice")
            continue
        if da != db:
            raise RuntimeError(f"unexpected diff at package {i}")

    return dst_img


def patch_img_bin(
    src_img: Path,
    png: Path,
    dst_img: Path,
    work: Path | None = None,
) -> Path:
    """Splice patched package 90 into a copy of img.bin (byte-identical elsewhere)."""
    return patch_img_bin_tex(src_img, encode_cesa_tex(Image.open(png)), dst_img, work)


def _load_pkg90(src_img: Path) -> bytes:
    _ensure_nlpp_path()
    from img import Image as ImgBin

    im = ImgBin(str(src_img))
    im.parse(False)
    res = im.entries[CESA_PKG_INDEX]
    if res is None:
        raise RuntimeError(f"img.bin missing package {CESA_PKG_INDEX}")
    return res.fw.read()


def main() -> int:
    ap = argparse.ArgumentParser(description="Patch CESA boot warning texture in img.bin")
    ap.add_argument("--img", type=Path, default=DEFAULT_IMG, help="Source img.bin")
    ap.add_argument("--png", type=Path, default=DEFAULT_ASSET, help="English 240x400 PNG")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output img.bin (default: out/cesa_patch/img.bin)",
    )
    ap.add_argument("--work", type=Path, default=None, help="Scratch directory")
    ap.add_argument(
        "--inplace",
        action="store_true",
        help="Patch --img in place (makes a .bak first)",
    )
    ap.add_argument(
        "--decode",
        action="store_true",
        help="Decode current CESA texture from --img to --png",
    )
    ap.add_argument(
        "--recompress-only",
        action="store_true",
        help="Test 1: recompress original TEX bytes with no pixel changes",
    )
    ap.add_argument(
        "--roundtrip-jp",
        action="store_true",
        help="Test 2: decode JP then re-encode (black padding) and splice",
    )
    ap.add_argument(
        "--deploy-azahar",
        action="store_true",
        help="Copy --out to Azahar LayeredFS romfs/img.bin",
    )
    args = ap.parse_args()

    if args.decode:
        pkg_raw = _load_pkg90(args.img)
        tex = read_cesa_tex_from_pkg(pkg_raw)
        out_png = args.png
        out_png.parent.mkdir(parents=True, exist_ok=True)
        decode_cesa_tex(tex).save(out_png)
        print(f"[cesa] decoded -> {out_png}")
        return 0

    if args.inplace:
        bak = args.img.with_suffix(args.img.suffix + ".bak")
        if not bak.is_file():
            shutil.copy2(args.img, bak)
            print(f"[cesa] backup -> {bak}")
        out = args.img
    else:
        out = args.out or (ROOT / "out" / "cesa_patch" / "img.bin")

    work = args.work or (Path(out).parent / "work")
    pkg_raw = _load_pkg90(args.img)
    stock_tex = read_cesa_tex_from_pkg(pkg_raw)

    if args.recompress_only:
        print(f"[cesa] Test1 recompress-only {args.img} -> {out}")
        patch_img_bin_tex(args.img, stock_tex, out, work=work)
        mode = "recompress-only"
    elif args.roundtrip_jp:
        print(f"[cesa] Test2 roundtrip-jp {args.img} -> {out}")
        jp = decode_cesa_tex(stock_tex)
        rt = encode_cesa_tex(jp)
        if rt != stock_tex:
            # Visible region must match; report padding-only diffs if any.
            vis_diff = sum(
                1
                for a, b in zip(
                    encode_cesa_tex(jp),  # already have rt
                    stock_tex,
                )
                if a != b
            )
            print(f"[cesa] warn: roundtrip byte diffs vs stock: {vis_diff}")
        patch_img_bin_tex(args.img, rt, out, work=work)
        # Save preview
        preview = Path(work) / "roundtrip_jp.png"
        preview.parent.mkdir(parents=True, exist_ok=True)
        jp.save(preview)
        mode = "roundtrip-jp"
    else:
        if not args.png.is_file():
            print(f"[-] missing PNG: {args.png}", file=sys.stderr)
            return 1
        print(f"[cesa] {args.img} + {args.png} -> {out}")
        patch_img_bin(args.img, args.png, out, work=work)
        mode = "png"

    print(f"[cesa] wrote {out} ({out.stat().st_size:,} bytes) [{mode}]")

    if args.deploy_azahar:
        az = Path(
            r"C:\Users\Zepse\AppData\Roaming\Azahar\load\mods\00040000000F4E00\romfs\img.bin"
        )
        az.parent.mkdir(parents=True, exist_ok=True)
        # Never deploy a code.bin overlay from this tool.
        shutil.copy2(out, az)
        print(f"[cesa] deployed -> {az}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
