#!/usr/bin/env python3
"""EN Confirm softkey 決定 — ETC1A4 Com_btn_k01_b{,ON} @ NCommonIcon pkg 5238."""
from __future__ import annotations

import os
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

from bclimutil import parse_bclim, png_to_bclim_etc1a4_same_size  # noqa: E402
from darcutil import DarcArchive  # noqa: E402
from img import ARC, FileWindow, Image as ImgBin, Package  # noqa: E402
from pack_images import PackError, splice_packages_into_img  # noqa: E402

from deploy_common import (  # noqa: E402
    UI_FONT,
    iter_deploy_targets,
    resolve_img_paths,
)

MOD_IMG, VANILLA = resolve_img_paths()

OUT = ROOT / "out" / "confirm_btn_en"
ASSET = ROOT / "assets" / "images" / "NCommonIcon.check" / "timg"
FONT = UI_FONT  # bundled OFL (assets/fonts/MPLUS1p-Regular.ttf)
PKG = 5238
# Match Back / Next softkey wording style (short).
LABEL = "OK"
TARGETS = [
    "timg/Com_btn_k01_b.bclim",
    "timg/Com_btn_k01_bON.bclim",
]


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size=size)


def render_btn(w: int, h: int, text: str, *, on: bool) -> Image.Image:
    """White rounded softkey matching Com_btn_m/t EN masters."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    # inset so transparent corners match JP tile
    x0, y0, x1, y1 = 2, 2, w - 3, h - 3
    fill = (248, 248, 248, 255)
    border = (80, 200, 220, 255) if on else (170, 170, 170, 255)
    dr.rounded_rectangle((x0, y0, x1, y1), radius=10, fill=fill, outline=border, width=2)
    for size in range(18, 9, -1):
        f = font(size)
        b = dr.textbbox((0, 0), text, font=f)
        tw, th = b[2] - b[0], b[3] - b[1]
        if tw > w - 14 or th > h - 12:
            continue
        x = (w - tw) // 2 - b[0]
        y = (h - th) // 2 - b[1] - 1
        dr.text((x, y), text, font=f, fill=(40, 40, 40, 255))
        return im
    raise RuntimeError(f"cannot fit {text!r}")


def interfile_zero_gaps(data: bytes) -> list[tuple[int, int]]:
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
        if len(chunk) >= 4 and chunk == b"\x00" * len(chunk):
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


def pad_zopfli_with_empty_blocks(data: bytes, exact_len: int) -> bytes | None:
    """Pad short zopfli zlib to exact_len. Works when zopfli ends non-final… often fails.

    Prefer gap-align so remain % 5 == 0; many ARCs still reject trailing empty
    blocks after a BFINAL stream, so callers should also try exact zopfli hits.
    """
    z = zopfli_zlib.compress(data)
    if len(z) > exact_len or len(z) < 6:
        return None
    if len(z) == exact_len:
        return z
    hdr, body, adler = z[:2], z[2:-4], z[-4:]
    remain = exact_len - len(hdr) - 4 - len(body)
    if remain < 5 or remain % 5 != 0:
        return None
    n_empty = remain // 5
    out = (
        hdr
        + body
        + b"\x00\x00\x00\xff\xff" * (n_empty - 1)
        + b"\x01\x00\x00\xff\xff"
        + adler
    )
    if len(out) != exact_len:
        return None
    d = zlib.decompressobj()
    try:
        got = d.decompress(out)
    except zlib.error:
        return None
    if got == data and not d.unused_data and d.eof:
        return out
    return None


def compress_exact(data: bytes, exact_len: int) -> tuple[bytes, bytes]:
    """Exact-length zopfli via inter-file gap salt (retry seeds; no byte-fine loop)."""
    z0 = len(zopfli_zlib.compress(data))
    print(f"  zopfli={z0} slot={exact_len}", flush=True)
    if z0 > exact_len:
        raise SystemExit(f"zopfli {z0} exceeds slot")
    if z0 == exact_len:
        return data, zopfli_zlib.compress(data)

    runs = interfile_zero_gaps(data)
    cap = sum(sz for sz, _ in runs)
    if not cap:
        slot = compress_exact_empty_blocks(data, exact_len)
        if slot is not None:
            print("  zlib empty-block (no gaps)", flush=True)
            return data, slot
        raise SystemExit("no gap capacity and empty-block failed")

    print(f"  gap capacity={cap}; zopfli binary-search…", flush=True)
    for seed_i in range(12):
        pad_rng = os.urandom(cap)

        def apply(n: int, rng: bytes = pad_rng) -> bytes:
            return apply_gap_pad(data, n, rng)

        lo, hi = 0, cap
        best_under = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            cand = apply(mid)
            zl = len(zopfli_zlib.compress(cand))
            if zl == exact_len:
                print(f"  hit seed={seed_i} pad_bytes={mid}", flush=True)
                return cand, zopfli_zlib.compress(cand)
            if zl < exact_len:
                best_under = mid
                lo = mid + 1
            else:
                hi = mid - 1

        for n in range(max(0, best_under - 8), min(cap, best_under + 48) + 1):
            cand = apply(n)
            z = zopfli_zlib.compress(cand)
            if len(z) == exact_len:
                print(f"  hit seed={seed_i} pad_bytes={n}", flush=True)
                return cand, z
            slot = pad_zopfli_with_empty_blocks(cand, exact_len)
            if slot is not None:
                print(f"  hit seed={seed_i} pad_bytes={n} + empty", flush=True)
                return cand, slot
        print(f"  seed={seed_i} miss (best_under={best_under})", flush=True)

    slot = compress_exact_empty_blocks(data, exact_len)
    if slot is not None:
        print("  zlib empty-block fallback", flush=True)
        return data, slot
    raise SystemExit("exact zlib failed")


def main() -> None:
    vanilla = VANILLA if VANILLA.is_file() else MOD_IMG
    if not MOD_IMG.is_file():
        raise SystemExit(f"missing {MOD_IMG}")
    bak = MOD_IMG.with_suffix(".bin.bak_pre_confirm_btn")
    if not bak.is_file():
        bak.write_bytes(MOD_IMG.read_bytes())
        print("created", bak, flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    ASSET.mkdir(parents=True, exist_ok=True)
    pkg_dir = OUT / "img_data"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "_fit"
    tmp.mkdir(parents=True, exist_ok=True)

    # Prefer live package so existing Back/Next EN (if any) is preserved.
    src_img = MOD_IMG
    vraw = src_img.read_bytes()
    vimg = ImgBin(str(src_img))
    vimg.parse(False)
    res = vimg.entries[PKG]
    src_pkg = pkg_dir / f"{PKG:04d}"
    src_pkg.write_bytes(vraw[res.fw.base_offset : res.fw.base_offset + res.fw.len()])

    pkg = Package(FileWindow(str(src_pkg)), 0)
    pkg.parse(False)
    arc_elem = next(e for e in pkg.entries if isinstance(e, ARC))
    cmp_len = arc_elem.fw.len()
    print(f"pkg {PKG} slot={cmp_len}", flush=True)

    darc = DarcArchive(bytearray(arc_elem.parsed()))
    for path in TARGETS:
        entry = darc.find(path) or darc.find(Path(path).name)
        if entry is None:
            raise SystemExit(f"missing {path}")
        raw_b = darc.extract_file(entry)
        _pix, w, h, fmt, _ = parse_bclim(raw_b)
        if fmt != 0xB:
            raise SystemExit(f"{path} fmt {fmt}")
        on = "ON" in path
        rgba = render_btn(w, h, LABEL, on=on)
        png = tmp / "t.png"
        orig = tmp / "o.bclim"
        rgba.save(png)
        stem = Path(path).stem
        rgba.save(OUT / f"{stem}_en.png")
        rgba.save(ASSET / f"{stem}.png")
        orig.write_bytes(raw_b)
        darc.replace_same_size(entry, png_to_bclim_etc1a4_same_size(png, orig))
        print(f"OK {path} -> {LABEL!r}", flush=True)

    tuned, slot = compress_exact(bytes(darc.data), cmp_len)
    do = zlib.decompressobj()
    got = do.decompress(slot)
    if got != tuned or do.unused_data or not do.eof:
        raise SystemExit("zlib verify failed")
    print(f"ARC exact {len(slot)}", flush=True)

    blob = bytearray(src_pkg.read_bytes())
    entry_off = Package.ENTRY_SIZE
    _typ, dec_len, _do, _fl, is_cmp, slot_len, cmp_off = Package.parse_entry(
        bytes(blob[entry_off : entry_off + Package.ENTRY_SIZE])
    )
    if not is_cmp or slot_len != cmp_len or len(tuned) != dec_len:
        raise SystemExit("ARC entry mismatch")
    blob[cmp_off : cmp_off + cmp_len] = slot
    new_pkg = pkg_dir / f"new_{PKG:04d}"
    new_pkg.write_bytes(blob)

    pkg2 = Package(FileWindow(str(new_pkg)), 0)
    pkg2.parse(False)
    for a, b in zip(pkg.entries, pkg2.entries):
        if isinstance(a, ARC):
            if b.parsed() != tuned:
                raise SystemExit("ARC mismatch")
        elif a.parsed() != b.parsed():
            raise SystemExit("DMST changed")
    print("DMST OK", flush=True)

    try:
        for _dest in iter_deploy_targets(MOD_IMG):
            splice_packages_into_img(_dest, pkg_dir, [PKG], _dest)
    except PackError as exc:
        raise SystemExit(f"splice failed: {exc}") from exc
    print("deployed Confirm OK ->", MOD_IMG, flush=True)
    print("Rollback:", bak, flush=True)


if __name__ == "__main__":
    main()
