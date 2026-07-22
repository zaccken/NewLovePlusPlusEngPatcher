#!/usr/bin/env python3
"""Gallery / Communication / Data Management MSel EN — exact-zopfli splice.

Packages (A8 Text BCLIMs, same pattern as Options pkg 5245):
  5244 Text02 — Gallery home
  5241 Text04 — Communication home
  5242 Text05 — Data Management home
"""
from __future__ import annotations

import os
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

OUT = ROOT / "out" / "msel_menus_en"
FONT = UI_FONT  # bundled OFL (assets/fonts/MPLUS1p-Regular.ttf)
# Prefer pre-Options bak for virgin ARC bytes; fall back to current mod.
VANILLA_CANDIDATES = [
    MOD_IMG.with_suffix(".bin.bak_pre_msel5245"),
    MOD_IMG.with_suffix(".bin.bak_pre_msel_menus"),
    MOD_IMG,
]
# Per-package: (basename, English). Home-screen chrome only (plates of the same
# labels skipped — EN AA glyphs compress worse than JP and blow the zlib slot).
PKG_LABELS: dict[int, list[tuple[str, str]]] = {
    5240: [  # Business Card submenu + related headers
        ("Com_M_Sel_Plate_Text04_02_00.bclim", "Business Card"),
        ("Com_M_Sel_Btn_Text04_02_01.bclim", "My Profile Card"),
        ("Com_M_Sel_Btn_Text04_02_02.bclim", "Friends' Cards"),
        ("Com_M_Sel_Btn_Text04_02_03.bclim", "Direct Card Exchange"),
        ("Com_M_Sel_Btn_Text04_02_13.bclim", "StreetPass Settings"),
        ("Com_M_Sel_Plate_Text04_02_01.bclim", "Select Save Data"),
        ("Com_M_Sel_Plate_Text04_02_09.bclim", "StreetPass Settings"),
        ("Com_M_Sel_Plate_Text04_02_10.bclim", "Friends' Cards"),
    ],
    5244: [  # Gallery
        ("Com_M_Sel_Plate_Text02_00_00.bclim", "Gallery"),
        ("Com_M_Sel_Btn_Text02_01_00.bclim", "Event Gallery"),
        ("Com_M_Sel_Btn_Text02_02_00.bclim", "Illustration Gallery"),
        ("Com_M_Sel_Btn_Text02_01_05.bclim", "Gallery Options"),
    ],
    5241: [  # Communication
        ("Com_M_Sel_Plate_Text04_00_00.bclim", "Communication"),
        ("Com_M_Sel_Btn_Text04_01_01.bclim", "Girlfriend Comm."),
        ("Com_M_Sel_Btn_Text04_02_00.bclim", "Business Card"),
        ("Com_M_Sel_Btn_Text04_03_00.bclim", "Wireless Battle"),
    ],
    5242: [  # Data Management
        ("Com_M_Sel_Plate_Text05_00_00.bclim", "Data Management"),
        ("Com_M_Sel_Btn_Text05_02_00.bclim", "Delete Save Data"),
        ("Com_M_Sel_Btn_Text05_01_01.bclim", "Export Save Data"),
    ],
}


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
    raise RuntimeError(f"cannot fit {text!r} into {w}x{h}")


def make_en_bclim(raw: bytes, en: str, tmp: Path, *, hard: bool, salt: float) -> bytes:
    canvas, w, h = decode_a8(raw)
    jp = canvas[:h, :w]
    ys, _ = np.where(jp > 40)
    th = int(ys.max() - ys.min() + 1) if len(ys) else h // 2
    en_a = render_en_alpha(w, h, en, th)
    if hard:
        en_a = np.where(en_a >= 96, 255, 0).astype(np.uint8)
    out = np.maximum(np.zeros_like(jp), en_a)
    if salt > 0:
        rng = np.random.default_rng(0x4D53454C)  # "MSEL"
        mask = (out == 0) & (rng.random(out.shape) < salt)
        out[mask] = 1
    rgba = Image.merge(
        "RGBA",
        (Image.new("L", (w, h), 255),) * 3 + (Image.fromarray(out, "L"),),
    )
    png = tmp / "t.png"
    orig = tmp / "o.bclim"
    rgba.save(png)
    orig.write_bytes(raw)
    return png_to_bclim_a8_same_size(png, orig)


def patch_arc(
    vanilla_arc: bytes,
    labels: list[tuple[str, str]],
    tmp: Path,
    *,
    hard: bool,
    salt: float,
) -> bytes:
    darc = DarcArchive(bytearray(vanilla_arc))
    for base, en in labels:
        entry = darc.find(f"timg/{base}") or darc.find(base)
        if entry is None:
            raise SystemExit(f"missing {base}")
        darc.replace_same_size(
            entry, make_en_bclim(darc.extract_file(entry), en, tmp, hard=hard, salt=salt)
        )
        print(f"  OK {base} -> {en!r} (hard={hard} salt={salt})")
    return bytes(darc.data)


def fit_arc_for_slot(
    vanilla_arc: bytes, labels: list[tuple[str, str]], tmp: Path, cmp_len: int
) -> bytes:
    """Pick glyph style so zopfli(patched) lands in (0, cmp_len] with gap pad room."""
    # Prefer soft AA (looks better). Fall back to hard edges if too fat; salt if too lean.
    trials: list[tuple[bool, float]] = [
        (False, 0.0),
        (True, 0.0),
        (True, 0.03),
        (True, 0.06),
        (True, 0.10),
        (False, 0.02),
    ]
    gap_cap_est = sum(sz for sz, _ in interfile_zero_gaps(vanilla_arc))
    best: tuple[int, bytes, bool, float] | None = None
    for hard, salt in trials:
        patched = patch_arc(vanilla_arc, labels, tmp, hard=hard, salt=salt)
        z = len(zopfli_zlib.compress(patched))
        gaps = sum(sz for sz, _ in interfile_zero_gaps(patched))
        print(f"  trial hard={hard} salt={salt}: zopfli={z} gaps={gaps} slot={cmp_len}")
        if z > cmp_len:
            continue
        # Need enough pad headroom to grow up to cmp_len (urandom in gaps).
        if z + gaps < cmp_len:
            # keep as fallback if nothing else works
            if best is None or z > best[0]:
                best = (z, patched, hard, salt)
            continue
        return patched
    if best is not None:
        print(f"  using closest lean trial hard={best[2]} salt={best[3]} z={best[0]}")
        return best[1]
    raise SystemExit(f"no trial fits under slot {cmp_len}")


def interfile_zero_gaps(data: bytes) -> list[tuple[int, int]]:
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


def compress_exact_zopfli(data: bytes, target: int) -> tuple[bytes, bytes]:
    base_z = len(zopfli_zlib.compress(data))
    if base_z > target:
        raise SystemExit(f"zopfli {base_z} already exceeds slot {target}")
    if base_z == target:
        slot = zopfli_zlib.compress(data)
        return data, slot

    runs = interfile_zero_gaps(data)
    if not runs:
        raise SystemExit("no inter-file zero gaps to pad")
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
        print(f"    pad_bytes={mid} zopfli={cl}")
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
    print(f"  ARC exact zopfli {len(slot)} unused_data=0")
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
        if isinstance(a, ARC):
            if db != tuned:
                raise SystemExit("ARC content mismatch")
        elif da != db:
            raise SystemExit(f"DMST changed {a.fn}")
    print("  DMST unchanged OK")


def pick_vanilla() -> Path:
    for p in VANILLA_CANDIDATES:
        if p.is_file():
            return p
    raise SystemExit("no img.bin source found")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--only",
        type=int,
        nargs="+",
        help="package ids to patch (default: all in PKG_LABELS)",
    )
    args = ap.parse_args()

    if not MOD_IMG.is_file():
        raise SystemExit(f"missing {MOD_IMG}")

    bak_menus = MOD_IMG.with_suffix(".bin.bak_pre_msel_menus")
    if not bak_menus.is_file():
        bak_menus.write_bytes(MOD_IMG.read_bytes())
        print("created", bak_menus, flush=True)

    vanilla_img = pick_vanilla()
    print("vanilla ARC source:", vanilla_img, flush=True)
    vraw = vanilla_img.read_bytes()
    vimg = ImgBin(str(vanilla_img))
    vimg.parse(False)

    pkg_dir = OUT / "img_data"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    pkgs = sorted(args.only) if args.only else sorted(PKG_LABELS)
    for pkg_id in pkgs:
        if pkg_id not in PKG_LABELS:
            raise SystemExit(f"pkg {pkg_id} not in PKG_LABELS")

    for pkg_id in pkgs:
        labels = PKG_LABELS[pkg_id]
        res = vimg.entries[pkg_id]
        if res is None:
            raise SystemExit(f"pkg {pkg_id} missing")
        src_pkg = pkg_dir / f"{pkg_id:04d}"
        src_pkg.write_bytes(
            vraw[res.fw.base_offset : res.fw.base_offset + res.fw.len()]
        )
        print(f"\n=== PKG {pkg_id} ({src_pkg.stat().st_size} bytes) ===", flush=True)

        tmp = OUT / f"_fit_{pkg_id}"
        tmp.mkdir(parents=True, exist_ok=True)

        pkg = Package(FileWindow(str(src_pkg)), 0)
        pkg.parse(False)
        arc_elem = next(e for e in pkg.entries if isinstance(e, ARC))
        print(
            f"  ARC {len(arc_elem.parsed())} cmp_slot={arc_elem.fw.len()}",
            flush=True,
        )

        patched_arc = fit_arc_for_slot(
            arc_elem.parsed(), labels, tmp, arc_elem.fw.len()
        )
        new_pkg = pkg_dir / f"new_{pkg_id:04d}"
        splice_arc(src_pkg, patched_arc, new_pkg)

    # Splice onto current LayeredFS (keeps Options 5245 etc.)
    try:
        for _dest in iter_deploy_targets(MOD_IMG):
            splice_packages_into_img(_dest, pkg_dir, pkgs, _dest)
    except PackError as exc:
        raise SystemExit(f"splice failed: {exc}") from exc

    print("\ndeployed MSel EN pkgs", pkgs, "->", MOD_IMG, flush=True)
    print("Fully quit Azahar and re-open the menu.", flush=True)
    print("Rollback: img.bin.bak_pre_msel_meishi or bak_pre_msel_menus", flush=True)


if __name__ == "__main__":
    main()
