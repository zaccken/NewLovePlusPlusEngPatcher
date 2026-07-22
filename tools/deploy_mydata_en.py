#!/usr/bin/env python3
"""EN My Data home: header (5575) + ToDo/Status buttons (5380).

Patches live LayeredFS packages so prior Mail/Myroom EN stays intact.
"""
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

OUT = ROOT / "out" / "mydata_en"
FONT = UI_FONT  # bundled OFL (assets/fonts/MPLUS1p-Regular.ttf)
INK = (70, 110, 160)

JOBS: list[tuple[int, list[tuple[str, str]], str]] = [
    # (pkg, labels, source)  source: "live" | "vanilla"
    (
        5575,
        [("timg/mydata_toptex_RGBA4.bclim", "My Data")],
        "live",
    ),
    (
        5380,
        [
            # Patch live so prior Myroom EN softkeys stay intact.
            ("timg/mcmn_tex_todo_RGBA4.bclim", "To-Do List"),
            ("timg/mcmn_tex_status_RGBA4.bclim", "Status"),
        ],
        "live",
    ),
]
def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size=size)


def render_label(
    w: int, h: int, text: str, *, hard: bool = False, max_size: int | None = None
) -> Image.Image:
    import numpy as np

    top = max_size if max_size is not None else min(16, h + 2)
    for size in range(top, 8, -1):
        scale = 2
        big = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
        dr = ImageDraw.Draw(big)
        f = font(size * scale)
        b = dr.textbbox((0, 0), text, font=f)
        tw, th = b[2] - b[0], b[3] - b[1]
        if tw > w * scale - 4:
            continue
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
    raise RuntimeError(f"cannot fit {text!r}")


def zero_interfile_gaps(data: bytes) -> bytes:
    """Clear inter-file padding so zlib SYNC_FLUSH can fit the slot."""
    t = bytearray(data)
    for sz, po in interfile_zero_gaps(data):
        t[po : po + sz] = b"\x00" * sz
    return bytes(t)


def patch_labels_into_darc(
    darc: DarcArchive,
    labels: list[tuple[str, str]],
    tmp: Path,
    *,
    hard: bool,
    max_size: int | None = None,
) -> None:
    for path, en in labels:
        entry = darc.find(path) or darc.find(Path(path).name)
        if entry is None:
            raise SystemExit(f"missing {path}")
        raw_b = darc.extract_file(entry)
        _pix, w, h, fmt, _ft = parse_bclim(raw_b)
        if fmt != 0xB:
            raise SystemExit(f"{path} fmt {fmt} not ETC1A4")
        rgba = render_label(w, h, en, hard=hard, max_size=max_size)
        png = tmp / "t.png"
        orig = tmp / "o.bclim"
        rgba.save(png)
        rgba.save(OUT / f"{Path(path).stem}_en.png")
        orig.write_bytes(raw_b)
        darc.replace_same_size(entry, png_to_bclim_etc1a4_same_size(png, orig))
        print(f"OK {path} -> {en!r} (hard={hard} max_size={max_size})", flush=True)


def fit_and_patch_arc(
    vanilla_arc: bytes,
    labels: list[tuple[str, str]],
    tmp: Path,
    cmp_len: int,
) -> bytes:
    """Prefer soft AA; fall back to hard + smaller glyphs if zopfli overshoots."""
    trials: list[tuple[bool, int | None]] = [
        (False, None),
        (True, None),
        (True, 13),
        (True, 12),
        (True, 11),
        (True, 10),
    ]
    best: tuple[int, bytes] | None = None
    for hard, max_size in trials:
        darc = DarcArchive(bytearray(vanilla_arc))
        patch_labels_into_darc(darc, labels, tmp, hard=hard, max_size=max_size)
        # Prior EN deploys leave urandom in gaps; zero them so zlib can pad.
        patched = zero_interfile_gaps(bytes(darc.data))
        z = len(zopfli_zlib.compress(patched))
        print(f"  trial hard={hard} max_size={max_size}: zopfli={z} slot={cmp_len}", flush=True)
        if best is None or z < best[0]:
            best = (z, patched)
        if z <= cmp_len:
            return patched
    assert best is not None
    if best[0] > cmp_len:
        raise SystemExit(f"no trial fits under slot {cmp_len} (best zopfli={best[0]})")
    return best[1]


def interfile_zero_gaps(data: bytes, min_len: int = 4) -> list[tuple[int, int]]:
    """All inter-file DARC padding (may re-salt prior urandom pads)."""
    darc = DarcArchive(data)
    spans = sorted((e.offset, e.offset + e.length) for e in darc.files)
    gaps: list[tuple[int, int]] = []
    for (_a0, a1), (b0, _b1) in zip(spans, spans[1:]):
        if b0 - a1 >= min_len:
            gaps.append((b0 - a1, a1))
    if spans and len(data) - spans[-1][1] >= min_len:
        gaps.append((len(data) - spans[-1][1], spans[-1][1]))
    gaps.sort(reverse=True)
    return gaps


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


def _try_empty_pad(data: bytes, body: bytes, hdr: bytes, exact_len: int) -> bytes | None:
    adler = struct.pack(">I", zlib.adler32(data) & 0xFFFFFFFF)
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


def compress_exact_with_gap_tune(data: bytes, exact_len: int) -> tuple[bytes, bytes]:
    """Find gap salt so SYNC_FLUSH zlib + empty blocks hits exact_len."""
    runs = interfile_zero_gaps(data)
    cap = sum(sz for sz, _ in runs)
    print(f"  gap capacity={cap}; empty-block tune…", flush=True)
    slot = compress_exact_empty_blocks(data, exact_len)
    if slot is not None:
        print("  hit pad_bytes=0", flush=True)
        return data, slot
    if not cap:
        raise SystemExit("no gap capacity and empty-block failed")

    hdrs = (b"\x78\x9c", b"\x78\xda", b"\x78\x5e", b"\x78\x01")
    for seed_i in range(6):
        pad_rng = os.urandom(cap)
        step = max(1, cap // 250)
        candidates = list(dict.fromkeys(list(range(0, cap + 1, step)) + list(range(0, min(cap, 64) + 1))))
        for n in candidates:
            cand = apply_gap_pad(data, n, pad_rng)
            for level in range(10):
                co = zlib.compressobj(level, wbits=-15)
                body = co.compress(cand) + co.flush(zlib.Z_SYNC_FLUSH)
                for hdr in hdrs:
                    hit = _try_empty_pad(cand, body, hdr, exact_len)
                    if hit is not None:
                        print(f"  hit seed={seed_i} pad_bytes={n} level={level}", flush=True)
                        return cand, hit
        print(f"  seed={seed_i} miss", flush=True)
    raise SystemExit("could not build exact zlib stream")


def compress_exact_zopfli(data: bytes, target: int) -> tuple[bytes, bytes]:
    """Exact-length zopfli via gap salt; retry seeds (no byte-fine loop)."""
    z0 = len(zopfli_zlib.compress(data))
    if z0 > target:
        raise SystemExit(f"zopfli {z0} exceeds slot {target}")
    if z0 == target:
        return data, zopfli_zlib.compress(data)

    runs = interfile_zero_gaps(data)
    cap = sum(sz for sz, _ in runs)
    if not cap:
        slot = compress_exact_empty_blocks(data, target)
        if slot is None:
            raise SystemExit("no gaps and empty-block failed")
        return data, slot

    print(f"  gap capacity={cap}; zopfli binary-search…", flush=True)
    for seed_i in range(12):
        rng = os.urandom(cap)

        def apply(n: int, pad: bytes = rng) -> bytes:
            return apply_gap_pad(data, n, pad)

        lo, hi = 0, cap
        best_under = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            cand = apply(mid)
            zl = len(zopfli_zlib.compress(cand))
            print(f"  seed={seed_i} pad={mid} zopfli={zl}", flush=True)
            if zl == target:
                print(f"  hit seed={seed_i} pad_bytes={mid}", flush=True)
                return cand, zopfli_zlib.compress(cand)
            if zl < target:
                best_under = mid
                lo = mid + 1
            else:
                hi = mid - 1

        for n in range(max(0, best_under - 6), min(cap, best_under + 40) + 1):
            cand = apply(n)
            z = zopfli_zlib.compress(cand)
            if len(z) == target:
                print(f"  hit seed={seed_i} pad_bytes={n}", flush=True)
                return cand, z
        print(f"  seed={seed_i} miss best_under={best_under}", flush=True)

    slot = compress_exact_empty_blocks(data, target)
    if slot is not None:
        return data, slot
    raise SystemExit("exact zopfli failed")


def patch_package(
    pkg_id: int, labels: list[tuple[str, str]], *, source: str
) -> None:
    src_img = MOD_IMG
    if source == "vanilla":
        if not VANILLA.is_file():
            raise SystemExit(f"missing vanilla {VANILLA}")
        src_img = VANILLA
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
    cmp_len = arc_elem.fw.len()
    print(
        f"pkg {pkg_id} source={source} ARC dec={len(arc_elem.parsed())} slot={cmp_len}",
        flush=True,
    )

    tmp = OUT / f"_fit_{pkg_id}"
    tmp.mkdir(parents=True, exist_ok=True)
    patched = fit_and_patch_arc(arc_elem.parsed(), labels, tmp, cmp_len)

    z0 = len(zopfli_zlib.compress(patched))
    print(f"  patched zopfli={z0} slot={cmp_len}", flush=True)
    if z0 > cmp_len:
        raise SystemExit(f"patched zopfli {z0} exceeds slot {cmp_len}")
    # Prefer empty-block when zlib SYNC_FLUSH fits (after zeroing gaps).
    slot = compress_exact_empty_blocks(patched, cmp_len)
    if slot is not None:
        print("  zlib empty-block", flush=True)
        tuned = patched
    elif z0 + 2000 >= cmp_len:
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
    print("  DMST OK", flush=True)

    for _dest in iter_deploy_targets(MOD_IMG):
        splice_packages_into_img(_dest, pkg_dir, [pkg_id], _dest)


def main() -> None:
    if not MOD_IMG.is_file():
        raise SystemExit(f"missing {MOD_IMG}")
    bak = MOD_IMG.with_suffix(".bin.bak_pre_mydata")
    if not bak.is_file():
        bak.write_bytes(MOD_IMG.read_bytes())
        print("created", bak, flush=True)
    OUT.mkdir(parents=True, exist_ok=True)

    for pkg_id, labels, source in JOBS:
        if pkg_id == 5575:
            print("skip 5575 (My Data header already EN on live)", flush=True)
            continue
        patch_package(pkg_id, labels, source=source)
    print("deployed My Data EN ->", MOD_IMG, flush=True)
    print("Rollback:", bak, flush=True)


if __name__ == "__main__":
    main()
