#!/usr/bin/env python3
"""EN Profile UI: A8 header (pkg 5246) + sharp RGB565 field atlas (pkg 5252)."""
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
    png_to_bclim_rgb565_same_size,
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

OUT = ROOT / "out" / "profile_en"
PREV = ROOT / "out" / "profile_previews"
FONT = UI_FONT  # bundled OFL (assets/fonts/MPLUS1p-Regular.ttf)
BG = (255, 220, 0)
# Match JP atlas chroma ink (light cyan) — yellow is keyed out on grey bars.
INK = (160, 210, 230)

PKG_HEADER = 5246
PKG_ATLAS = 5252

HEADER_LABELS = [
    ("timg/Com_M_Sel_Plate_Text01_00_00.bclim", "Profile"),
]

# (y0, y1, x0, x1, text) — wide centered boxes so EN stays one size.
# JP order was 姓/名; EN uses First Name then Last Name.
ATLAS_LABELS: list[tuple[int, int, int, int, str]] = [
    (10, 24, 48, 192, "First Name"),
    (49, 63, 48, 192, "Last Name"),
    (90, 103, 48, 192, "Birthday"),
    (129, 143, 48, 192, "Blood"),
    (169, 183, 48, 192, "Hometown"),
]
# Drop separate "Type" under Blood (JP 血液型 split); Blood alone covers it.
ATLAS_MD: tuple[int, int, int, int, int, int] = (110, 122, 106, 118, 144, 156)
# Shared glyph height for main field labels (M/D stay slightly smaller).
ATLAS_LABEL_SIZE = 12
ATLAS_MD_SIZE = 11


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size=size)


# ---- A8 header (white plate) -------------------------------------------------


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


def render_en_alpha(w: int, h: int, text: str, target_h: int) -> np.ndarray:
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
        cand = np.array(big.resize((w, h), Image.Resampling.BILINEAR))
        if glyph_h(cand) <= target_h + 2:
            return np.clip(cand.astype(np.float32) * 0.95, 0, 255).astype(np.uint8)
    raise RuntimeError(f"cannot fit {text!r}")


def make_a8_en(
    raw: bytes, en: str, tmp: Path, *, hard: bool = False, salt: float = 0.0
) -> bytes:
    canvas, w, h = decode_a8(raw)
    jp = canvas[:h, :w]
    ys, _ = np.where(jp > 40)
    th = int(ys.max() - ys.min() + 1) if len(ys) else h // 2
    en_a = render_en_alpha(w, h, en, th)
    if hard:
        en_a = np.where(en_a >= 96, 255, 0).astype(np.uint8)
    out = np.maximum(np.zeros_like(jp), en_a)
    if salt > 0:
        rng = np.random.default_rng(0x50524F46)  # "PROF"
        mask = (out == 0) & (rng.random(out.shape) < salt)
        out[mask] = 1
    rgba = Image.merge(
        "RGBA",
        (Image.new("L", (w, h), 255),) * 3 + (Image.fromarray(out, "L"),),
    )
    png = tmp / "a8.png"
    orig = tmp / "a8_o.bclim"
    rgba.save(png)
    rgba.save(OUT / f"{en.replace(' ', '_')}_a8.png")
    orig.write_bytes(raw)
    return png_to_bclim_a8_same_size(png, orig)


def patch_header_arc(vanilla_arc: bytes, tmp: Path) -> bytes:
    """Try soft then hard A8 so zopfli fits the tiny 5246 slot."""
    trials: list[tuple[bool, float]] = [
        (False, 0.0),
        (True, 0.0),
        (True, 0.02),
        (True, 0.05),
    ]
    best: tuple[int, bytes, bool, float] | None = None
    for hard, salt in trials:
        darc = DarcArchive(bytearray(vanilla_arc))
        for path, en in HEADER_LABELS:
            entry = darc.find(path) or darc.find(Path(path).name)
            if entry is None:
                raise SystemExit(f"missing {path}")
            darc.replace_same_size(
                entry,
                make_a8_en(
                    darc.extract_file(entry), en, tmp, hard=hard, salt=salt
                ),
            )
        patched = bytes(darc.data)
        z = len(zopfli_zlib.compress(patched))
        print(f"  trial hard={hard} salt={salt}: zopfli={z}", flush=True)
        if best is None or z < best[0]:
            best = (z, patched, hard, salt)
        if z <= 1651:
            print(f"OK header Profile (hard={hard} salt={salt})", flush=True)
            return patched
    assert best is not None
    if best[0] > 1651:
        raise SystemExit(
            f"header zopfli {best[0]} still exceeds slot 1651 (hard={best[2]})"
        )
    print(
        f"OK header Profile best hard={best[2]} salt={best[3]} z={best[0]}",
        flush=True,
    )
    return best[1]


# ---- RGB565 atlas (hard ink, no soft outline) --------------------------------


def render_hard_label(
    w: int, h: int, text: str, *, prefer_size: int | None = None
) -> Image.Image:
    """1× hard glyphs on yellow — avoids muddy soft-AA on grey bars."""
    sizes: list[int]
    if prefer_size is not None:
        # Prefer fixed size; only shrink if the string truly won't fit.
        sizes = list(range(prefer_size, 7, -1))
    else:
        sizes = list(range(min(13, h), 7, -1))
    for size in sizes:
        img = Image.new("RGB", (w, h), BG)
        dr = ImageDraw.Draw(img)
        f = font(size)
        b = dr.textbbox((0, 0), text, font=f)
        tw, th = b[2] - b[0], b[3] - b[1]
        if tw > w - 1 or th > h:
            continue
        x = (w - tw) // 2 - b[0]
        y = (h - th) // 2 - b[1]
        dr.text((x, y), text, font=f, fill=INK)
        # Crush partial AA fringe into solid ink / pure yellow.
        arr = np.array(img)
        dist = np.abs(arr.astype(np.int16) - np.array(BG, dtype=np.int16)).sum(
            axis=2
        )
        mask = dist > 40
        out = np.zeros_like(arr)
        out[:] = BG
        out[mask] = INK
        return Image.fromarray(out, "RGB")
    raise RuntimeError(f"cannot fit {text!r} in {w}x{h}")


def paste_label(
    img: Image.Image,
    y0: int,
    y1: int,
    x0: int,
    x1: int,
    text: str,
    *,
    prefer_size: int | None = None,
) -> None:
    box_w = x1 - x0 + 1
    box_h = y1 - y0 + 1
    ImageDraw.Draw(img).rectangle((x0, y0, x1, y1), fill=BG)
    img.paste(
        render_hard_label(box_w, box_h, text, prefer_size=prefer_size), (x0, y0)
    )


def make_atlas_en(raw: bytes, tmp: Path) -> bytes:
    base = PREV / "Profile_Info_Profile_t.png"
    _pix, w, h, fmt, _ft = parse_bclim(raw)
    if fmt != 3:
        raise SystemExit(f"atlas fmt {fmt} not RGB565")
    if base.is_file():
        img = Image.open(base).convert("RGB").resize((w, h), Image.Resampling.NEAREST)
    else:
        img = Image.new("RGB", (w, h), BG)
    # Full clear yellow first so no JP stain remains.
    ImageDraw.Draw(img).rectangle((0, 0, w - 1, h - 1), fill=BG)
    for y0, y1, x0, x1, text in ATLAS_LABELS:
        paste_label(img, y0, y1, x0, x1, text, prefer_size=ATLAS_LABEL_SIZE)
    y0, y1, mx0, mx1, dx0, dx1 = ATLAS_MD
    paste_label(img, y0, y1, mx0, mx1, "M", prefer_size=ATLAS_MD_SIZE)
    paste_label(img, y0, y1, dx0, dx1, "D", prefer_size=ATLAS_MD_SIZE)
    png = tmp / "atlas.png"
    orig = tmp / "atlas_o.bclim"
    img.save(png)
    img.save(OUT / "Profile_Info_Profile_t_en.png")
    orig.write_bytes(raw)
    return png_to_bclim_rgb565_same_size(png, orig)


# ---- exact zlib / zopfli helpers ---------------------------------------------


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
    adler = struct.pack(">I", zlib.adler32(data) & 0xFFFFFFFF)
    bodies: list[bytes] = []
    for level in range(10):
        co = zlib.compressobj(level, wbits=-15)
        bodies.append(co.compress(data) + co.flush(zlib.Z_SYNC_FLUSH))
    hdrs = (b"\x78\x9c", b"\x78\xda", b"\x78\x5e", b"\x78\x01")
    for body in bodies:
        for hdr in hdrs:
            remain = exact_len - len(hdr) - 4 - len(body)
            if remain < 5 or remain % 5 != 0:
                continue
            n_empty = remain // 5
            extras = b"\x00\x00\x00\xff\xff" * (n_empty - 1)
            final = b"\x01\x00\x00\xff\xff"
            out = hdr + body + extras + final + adler
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


def compress_exact_with_gap_tune(data: bytes, exact_len: int) -> tuple[bytes, bytes]:
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
    base_z = len(zopfli_zlib.compress(data))
    if base_z > target:
        raise SystemExit(f"zopfli {base_z} exceeds slot {target}")
    if base_z == target:
        return data, zopfli_zlib.compress(data)
    runs = interfile_zero_gaps(data, min_len=8)
    if not runs:
        # tiny ARC: try empty-block pad instead
        return compress_exact_with_gap_tune(data, target)
    cap = sum(sz for sz, _ in runs)
    rng = os.urandom(cap)

    def apply(n: int) -> bytes:
        return apply_gap_pad(data, n, rng)

    lo, hi = 0, cap
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = apply(mid)
        z = zopfli_zlib.compress(cand)
        if len(z) == target:
            return cand, z
        if len(z) < target:
            best = (mid, cand, z)
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        raise SystemExit("zopfli cannot reach slot")
    # fine scan near best
    mid0, _, _ = best
    for n in range(max(0, mid0 - 64), min(cap, mid0 + 64) + 1):
        cand = apply(n)
        z = zopfli_zlib.compress(cand)
        if len(z) == target:
            do = zlib.decompressobj()
            got = do.decompress(z)
            if got == cand and not do.unused_data:
                return cand, z
    # fall back to empty-block exact
    return compress_exact_with_gap_tune(data, target)


def patch_package(
    pkg_id: int,
    patch_fn,
    *,
    use_zopfli: bool,
) -> None:
    vanilla = VANILLA if VANILLA.is_file() else MOD_IMG
    vraw = vanilla.read_bytes()
    vimg = ImgBin(str(vanilla))
    vimg.parse(False)
    res = vimg.entries[pkg_id]
    pkg_dir = OUT / "img_data"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    src_pkg = pkg_dir / f"{pkg_id:04d}"
    src_pkg.write_bytes(vraw[res.fw.base_offset : res.fw.base_offset + res.fw.len()])

    pkg = Package(FileWindow(str(src_pkg)), 0)
    pkg.parse(False)
    arc_elem = next(e for e in pkg.entries if isinstance(e, ARC))
    cmp_len = arc_elem.fw.len()
    print(f"pkg {pkg_id} ARC dec={len(arc_elem.parsed())} slot={cmp_len}", flush=True)

    tmp = OUT / f"_fit_{pkg_id}"
    tmp.mkdir(parents=True, exist_ok=True)
    patched = patch_fn(arc_elem.parsed(), tmp)

    if use_zopfli:
        tuned, slot = compress_exact_zopfli(patched, cmp_len)
    else:
        tuned, slot = compress_exact_with_gap_tune(patched, cmp_len)
    do = zlib.decompressobj()
    got = do.decompress(slot)
    if got != tuned or do.unused_data or not do.eof:
        raise SystemExit(f"pkg {pkg_id} zlib verify failed")
    print(f"  ARC exact {len(slot)}", flush=True)

    blob = bytearray(src_pkg.read_bytes())
    entry_off = Package.ENTRY_SIZE
    _typ, dec_len, _do, _fl, is_cmp, slot_len, cmp_off = Package.parse_entry(
        bytes(blob[entry_off : entry_off + Package.ENTRY_SIZE])
    )
    if not is_cmp or slot_len != cmp_len or len(tuned) != dec_len:
        raise SystemExit(f"pkg {pkg_id} ARC entry mismatch")
    blob[cmp_off : cmp_off + cmp_len] = slot
    new_pkg = pkg_dir / f"new_{pkg_id:04d}"
    new_pkg.write_bytes(blob)

    pkg2 = Package(FileWindow(str(new_pkg)), 0)
    pkg2.parse(False)
    for a, b in zip(pkg.entries, pkg2.entries):
        if isinstance(a, ARC):
            if b.parsed() != tuned:
                raise SystemExit(f"pkg {pkg_id} ARC mismatch")
        elif a.parsed() != b.parsed():
            raise SystemExit(f"pkg {pkg_id} DMST changed")
    print(f"  DMST OK", flush=True)

    try:
        for _dest in iter_deploy_targets(MOD_IMG):
            splice_packages_into_img(_dest, pkg_dir, [pkg_id], _dest)
    except PackError as exc:
        raise SystemExit(f"splice {pkg_id} failed: {exc}") from exc


def patch_atlas_arc(vanilla_arc: bytes, tmp: Path) -> bytes:
    darc = DarcArchive(bytearray(vanilla_arc))
    atlas = darc.find("timg/Profile_Info_Profile_t.bclim")
    if atlas is None:
        raise SystemExit("missing atlas")
    darc.replace_same_size(atlas, make_atlas_en(darc.extract_file(atlas), tmp))
    print("OK Profile_Info_Profile_t (hard ink)", flush=True)
    return bytes(darc.data)


def main() -> None:
    if not MOD_IMG.is_file():
        raise SystemExit(f"missing {MOD_IMG}")
    bak = MOD_IMG.with_suffix(".bin.bak_pre_profile_v3")
    if not bak.is_file():
        bak.write_bytes(MOD_IMG.read_bytes())
        print("created", bak, flush=True)
    OUT.mkdir(parents=True, exist_ok=True)

    patch_package(PKG_HEADER, patch_header_arc, use_zopfli=True)
    patch_package(PKG_ATLAS, patch_atlas_arc, use_zopfli=False)
    print("deployed Profile header+atlas ->", MOD_IMG, flush=True)
    print("Rollback:", bak, flush=True)


if __name__ == "__main__":
    main()
