#!/usr/bin/env python3
"""EN main-menu hub labels — Title.arc pkg 5261 (Title_btn02_t01..t06).

These are NOT NCommonMSel plates. Submenus (Gallery/Options/…) use other pkgs;
the six hub rows + CESA are separate. RGBA4444 (fmt 8) same-size splice + exact zlib.
"""
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

from bclimutil import png_to_bclim_rgba4444_same_size  # noqa: E402
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

OUT = ROOT / "out" / "title_main_menu_en"
ASSET = ROOT / "assets" / "images" / "Title"
PKG = 5261
# Vanilla JP mapping (decoded from Title.arc):
#   t01 オプション / t02 ゲームスタート / t03 ギャラリー
#   t04 データ管理 / t05 コミュニケーション / t06 どこでもデート
LABELS: list[tuple[str, str]] = [
    ("timg/Title_btn02_t01.bclim", "Options"),
    ("timg/Title_btn02_t02.bclim", "Game Start"),
    ("timg/Title_btn02_t03.bclim", "Gallery"),
    ("timg/Title_btn02_t04.bclim", "Data Management"),
    ("timg/Title_btn02_t05.bclim", "Communication"),
    ("timg/Title_btn02_t06.bclim", "Anywhere Date"),
]


def render_label(text: str, w: int = 100, h: int = 20) -> Image.Image:
    """Black ink + alpha AA, matching existing Title EN masters."""
    for size in range(13, 8, -1):
        scale = 2
        big = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
        dr = ImageDraw.Draw(big)
        font = ImageFont.truetype(str(UI_FONT), size * scale)
        b = dr.textbbox((0, 0), text, font=font)
        tw, th = b[2] - b[0], b[3] - b[1]
        if tw > w * scale - 4:
            continue
        x = (w * scale - tw) // 2 - b[0]
        y = (h * scale - th) // 2 - b[1]
        dr.text((x, y), text, font=font, fill=(0, 0, 0, 255))
        return big.resize((w, h), Image.Resampling.BILINEAR)
    raise RuntimeError(f"cannot fit {text!r}")


def main() -> int:
    if not MOD_IMG.is_file():
        raise SystemExit(f"missing {MOD_IMG}")
    if not UI_FONT.is_file():
        raise SystemExit(f"missing UI font {UI_FONT}")

    bak = MOD_IMG.with_suffix(".bin.bak_pre_title_menu")
    if not bak.is_file():
        bak.write_bytes(MOD_IMG.read_bytes())
        print("created", bak, flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    ASSET.mkdir(parents=True, exist_ok=True)
    pkg_dir = OUT / "img_data"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "_fit"
    tmp.mkdir(parents=True, exist_ok=True)

    # Prefer vanilla Title ARC so PNG-pack leftovers don't blow the slot.
    src_img = VANILLA if VANILLA.is_file() else MOD_IMG
    print(f"Title ARC source: {src_img}", flush=True)
    raw = src_img.read_bytes()
    img = ImgBin(str(src_img))
    img.parse(False)
    res = img.entries[PKG]
    src_pkg = pkg_dir / f"{PKG:04d}"
    src_pkg.write_bytes(raw[res.fw.base_offset : res.fw.base_offset + res.fw.len()])

    pkg = Package(FileWindow(str(src_pkg)), 0)
    pkg.parse(False)
    arc = next(e for e in pkg.entries if isinstance(e, ARC))
    cmp_len = arc.fw.len()
    print(f"pkg {PKG} slot={cmp_len} arc={len(arc.parsed())}", flush=True)

    darc = DarcArchive(bytearray(_force_zero_gaps(arc.parsed())))
    for path, en in LABELS:
        entry = darc.find(path) or darc.find(Path(path).name)
        if entry is None:
            raise SystemExit(f"missing {path}")
        raw_b = darc.extract_file(entry)
        rgba = render_label(en)
        png = tmp / f"{Path(path).stem}.png"
        orig = tmp / f"{Path(path).stem}.bclim"
        rgba.save(png)
        rgba.save(ASSET / f"{Path(path).stem}.png")
        rgba.save(OUT / f"{Path(path).stem}_en.png")
        orig.write_bytes(raw_b)
        darc.replace_same_size(entry, png_to_bclim_rgba4444_same_size(png, orig))
        print(f"OK {Path(path).name} -> {en!r}", flush=True)

    cand = _force_zero_gaps(bytes(darc.data))
    z0 = len(zopfli_zlib.compress(cand))
    print(f"  zopfli={z0} slot={cmp_len}", flush=True)
    if z0 > cmp_len:
        raise SystemExit(f"Title ARC zopfli {z0} exceeds slot {cmp_len}")

    try:
        tuned, slot = compress_exact_zopfli(cand, cmp_len)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

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
        for dest in iter_deploy_targets(MOD_IMG):
            splice_packages_into_img(dest, pkg_dir, [PKG], dest)
    except PackError as exc:
        raise SystemExit(f"splice failed: {exc}") from exc

    print("deployed Title main-menu EN ->", MOD_IMG, flush=True)
    print("Rollback:", bak, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
