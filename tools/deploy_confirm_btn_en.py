#!/usr/bin/env python3
"""EN Confirm softkey 決定 — ETC1A4 Com_btn_k01_b{,ON} @ NCommonIcon pkg 5238."""
from __future__ import annotations

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
from exact_zlib import compress_exact_zopfli, _force_zero_gaps  # noqa: E402
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
LABEL = "OK"
TARGETS = [
    "timg/Com_btn_k01_b.bclim",
    "timg/Com_btn_k01_bON.bclim",
]


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size=size)


def render_btn(
    w: int,
    h: int,
    text: str,
    *,
    on: bool,
    radius: int = 10,
    border_w: int = 2,
    max_size: int = 18,
    hard_text: bool = False,
) -> Image.Image:
    """White rounded softkey matching Com_btn_m/t EN masters."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    x0, y0, x1, y1 = 2, 2, w - 3, h - 3
    fill = (248, 248, 248, 255)
    border = (80, 200, 220, 255) if on else (170, 170, 170, 255)
    dr.rounded_rectangle(
        (x0, y0, x1, y1),
        radius=radius,
        fill=fill,
        outline=border,
        width=border_w,
    )
    for size in range(max_size, 8, -1):
        f = font(size)
        b = dr.textbbox((0, 0), text, font=f)
        tw, th = b[2] - b[0], b[3] - b[1]
        if tw > w - 14 or th > h - 12:
            continue
        x = (w - tw) // 2 - b[0]
        y = (h - th) // 2 - b[1] - 1
        if hard_text:
            # 1-bit ink → fewer ETC1 edge colors → usually smaller zlib.
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).text((x, y), text, font=f, fill=255)
            mask = mask.point(lambda p: 255 if p >= 128 else 0)
            ink = Image.new("RGBA", (w, h), (40, 40, 40, 255))
            im.paste(ink, (0, 0), mask)
        else:
            dr.text((x, y), text, font=f, fill=(40, 40, 40, 255))
        return im
    raise RuntimeError(f"cannot fit {text!r}")


def _extract_pkg(img_path: Path, pkg_dir: Path) -> tuple[Path, Package, ARC]:
    raw = img_path.read_bytes()
    img = ImgBin(str(img_path))
    img.parse(False)
    res = img.entries[PKG]
    src_pkg = pkg_dir / f"{PKG:04d}"
    src_pkg.write_bytes(raw[res.fw.base_offset : res.fw.base_offset + res.fw.len()])
    pkg = Package(FileWindow(str(src_pkg)), 0)
    pkg.parse(False)
    arc = next(e for e in pkg.entries if isinstance(e, ARC))
    return src_pkg, pkg, arc


def _patch_confirm(
    arc_bytes: bytes,
    tmp: Path,
    *,
    style: dict,
) -> bytes:
    darc = DarcArchive(bytearray(arc_bytes))
    for path in TARGETS:
        entry = darc.find(path) or darc.find(Path(path).name)
        if entry is None:
            raise SystemExit(f"missing {path}")
        raw_b = darc.extract_file(entry)
        _pix, w, h, fmt, _ = parse_bclim(raw_b)
        if fmt != 0xB:
            raise SystemExit(f"{path} fmt {fmt}")
        on = "ON" in path
        rgba = render_btn(w, h, LABEL, on=on, **style)
        png = tmp / "t.png"
        orig = tmp / "o.bclim"
        rgba.save(png)
        stem = Path(path).stem
        rgba.save(OUT / f"{stem}_en.png")
        ASSET.mkdir(parents=True, exist_ok=True)
        rgba.save(ASSET / f"{stem}.png")
        orig.write_bytes(raw_b)
        darc.replace_same_size(entry, png_to_bclim_etc1a4_same_size(png, orig))
        print(f"OK {path} -> {LABEL!r} style={style}", flush=True)
    return bytes(darc.data)


# Lean → fancy. Prefer styles that keep zopfli under the live slot.
STYLES: list[dict] = [
    {"radius": 6, "border_w": 1, "max_size": 14, "hard_text": True},
    {"radius": 8, "border_w": 1, "max_size": 16, "hard_text": True},
    {"radius": 8, "border_w": 2, "max_size": 16, "hard_text": True},
    {"radius": 10, "border_w": 2, "max_size": 18, "hard_text": True},
    {"radius": 10, "border_w": 2, "max_size": 18, "hard_text": False},
]


def main() -> int:
    if not MOD_IMG.is_file():
        raise SystemExit(f"missing {MOD_IMG}")
    bak = MOD_IMG.with_suffix(".bin.bak_pre_confirm_btn")
    if not bak.is_file():
        bak.write_bytes(MOD_IMG.read_bytes())
        print("created", bak, flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    pkg_dir = OUT / "img_data"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "_fit"
    tmp.mkdir(parents=True, exist_ok=True)

    # Live first (keeps Back/Next EN from PNG pack); vanilla if live is too fat.
    bases: list[tuple[str, Path]] = [("live", MOD_IMG)]
    if VANILLA.is_file() and VANILLA.resolve() != MOD_IMG.resolve():
        bases.append(("vanilla", VANILLA))

    best_over: tuple[int, bytes, int] | None = None  # (zopfli, arc, slot)
    tuned: bytes | None = None
    slot: bytes | None = None
    src_pkg: Path | None = None
    pkg: Package | None = None
    cmp_len = 0

    for base_name, base_path in bases:
        src_pkg, pkg, arc = _extract_pkg(base_path, pkg_dir)
        cmp_len = arc.fw.len()
        print(f"pkg {PKG} base={base_name} slot={cmp_len}", flush=True)
        virgin = _force_zero_gaps(arc.parsed())
        z_base = len(zopfli_zlib.compress(virgin))
        print(f"  base zopfli (zero gaps)={z_base}", flush=True)

        for style in STYLES:
            cand = _force_zero_gaps(_patch_confirm(virgin, tmp, style=style))
            z = len(zopfli_zlib.compress(cand))
            print(f"  trial {base_name} {style}: zopfli={z} slot={cmp_len}", flush=True)
            if z > cmp_len:
                if best_over is None or z < best_over[0]:
                    best_over = (z, cand, cmp_len)
                continue
            try:
                tuned, slot = compress_exact_zopfli(cand, cmp_len)
                print(f"  exact-zlib hit ({base_name}, {style})", flush=True)
                break
            except RuntimeError as exc:
                print(f"  exact-zlib miss: {exc}", flush=True)
                continue
        if tuned is not None:
            break

    if tuned is None or slot is None or src_pkg is None or pkg is None:
        over = f" best_over={best_over[0]}" if best_over else ""
        print(
            f"[warn] Confirm OK could not fit slot{over} — leaving pkg {PKG} unchanged "
            "(Back/Next EN from PNG pack still apply; Confirm may stay JP).",
            flush=True,
        )
        return 0

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
