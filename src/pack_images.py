#!/usr/bin/env python3
"""Pack EngPatcher UI PNGs into romfs/img.bin via nlpp-tools + same-size DARC inject.

Uses tools from https://github.com/kiwiz/nlpp-tools (ie, pe, png2bclim).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from darcutil import DarcArchive
from image_map import normalize_folder_key, resolve_folder

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
ASSETS_IMAGES = ROOT / "assets" / "images"
NLPP_TOOLS = ROOT / "tools" / "nlpp-tools"
PNG2BCLIM = NLPP_TOOLS / "opt" / "bin" / "png2bclim.exe"
IE = NLPP_TOOLS / "bin" / "ie"
PE = NLPP_TOOLS / "bin" / "pe"

DEFAULT_IMG_BIN = Path(r"C:\Users\Zepse\nlpp_work\romfs\img.bin")
SKIP_DIR_RE = re.compile(r"(timg\s*-\s*copy|__pycache__|\.git)", re.I)
SKIP_PNG_RE = re.compile(r"(\(2\)|_jpn|_bak|copy)", re.I)


class PackError(RuntimeError):
    pass


def _run(cmd: list[str | Path], cwd: Path | None = None) -> None:
    printable = " ".join(str(c) for c in cmd)
    print(f"  > {printable}")
    proc = subprocess.run(
        [str(c) for c in cmd],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.stdout.strip():
        print(proc.stdout.rstrip())
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise PackError(f"command failed ({proc.returncode}): {printable}\n{err}")


def _require_tools() -> None:
    missing = [p for p in (IE, PE, PNG2BCLIM) if not p.is_file()]
    if missing:
        raise PackError(f"missing tools: {', '.join(p.name for p in missing)}")


def prefer_asset_folders(images_root: Path) -> dict[str, Path]:
    """Pick best source folder per logical archive key.

    Prefer plain working folders (e.g. Title/) and .check dumps over raw
    Title.arc trees when both exist — those usually hold the English masters.
    """
    ranked: dict[str, list[tuple[int, Path]]] = defaultdict(list)
    for path in sorted(images_root.iterdir()):
        if not path.is_dir():
            continue
        key = normalize_folder_key(path.name)
        if resolve_folder(path.name) is None:
            continue
        name = path.name.lower()
        if name.endswith(".check"):
            rank = 0
        elif name.endswith(".arc"):
            rank = 2
        else:
            rank = 1
        ranked[key].append((rank, path))

    chosen: dict[str, Path] = {}
    for key, items in ranked.items():
        items.sort(key=lambda t: (t[0], t[1].name))
        chosen[key] = items[0][1]
    return chosen


def iter_asset_pngs(folder: Path) -> list[Path]:
    by_stem: dict[str, tuple[int, Path]] = {}
    for png in folder.rglob("*.png"):
        rel_parts = png.relative_to(folder).parts
        if any(SKIP_DIR_RE.search(p) for p in rel_parts):
            continue
        if SKIP_PNG_RE.search(png.stem):
            continue
        stem = png.stem
        rank = 2
        for suffix in ("_eng", "_en", "_ENG"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                rank = 0  # English master wins
                break
        if "timg" in {p.lower() for p in rel_parts}:
            rank = min(rank, 1)
        prev = by_stem.get(stem)
        if prev is None or rank < prev[0]:
            by_stem[stem] = (rank, png)
    return sorted((p for _, p in by_stem.values()), key=lambda p: p.as_posix().lower())


def png_to_bclim_candidates(png: Path) -> list[str]:
    stem = png.stem
    # Strip common EN suffixes
    for suffix in ("_eng", "_en", "_ENG"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return [f"timg/{stem}.bclim", f"{stem}.bclim"]


def _bclim_looks_valid(path: Path, orig_size: int) -> tuple[bool, str]:
    """Accept only exact-size BCLIMs.

    png2bclim often re-encodes to a different format/size (e.g. 4KB -> 32KB).
    Those break alpha/UI panes in-game (solid grey panels). Same-size only.
    """
    data = path.read_bytes()
    size = len(data)
    if size < 128:
        return False, f"too small ({size} bytes)"
    if data[:4] == b"CLIM" and size < 256:
        return False, "header-only CLIM stub"
    if size != orig_size:
        return False, f"size/format changed ({orig_size} -> {size}); keeping original"
    return True, ""


def convert_png_to_bclim(png: Path, orig_bclim: Path, work: Path) -> Path:
    """Convert PNG to BCLIM; keep same file size for DARC inject."""
    from bclimutil import parse_bclim, png_to_bclim_same_size

    work.mkdir(parents=True, exist_ok=True)
    for old in work.glob("*"):
        if old.is_file():
            old.unlink()
    orig_size = orig_bclim.stat().st_size
    produced = work / f"{orig_bclim.stem}X.bclim"

    try:
        _pix, _w, _h, fmt, _footer = parse_bclim(orig_bclim.read_bytes())
    except Exception:
        fmt = -1

    # fmt 8 = RGBA4444; fmt 0xB = ETC1A4; fmt 1 = A8; fmt 3 = RGB565 (Release buttons).
    if fmt in (1, 3, 8, 0xB):
        try:
            produced.write_bytes(png_to_bclim_same_size(png, orig_bclim))
            ok, reason = _bclim_looks_valid(produced, orig_size)
            if ok:
                return produced
            raise PackError(reason)
        except Exception as exc:
            raise PackError(f"same-size BCLIM encode failed for {png.name}: {exc}") from exc

    staged_png = work / f"{orig_bclim.stem}.png"
    staged_bclim = work / f"{orig_bclim.stem}.bclim"
    shutil.copy2(png, staged_png)
    shutil.copy2(orig_bclim, staged_bclim)
    _run([PNG2BCLIM, staged_png], cwd=work)
    if not produced.is_file():
        hits = sorted(work.glob(f"{orig_bclim.stem}*X.bclim"))
        if not hits:
            raise PackError(f"png2bclim produced no BCLIM for {png.name}")
        produced = hits[0]
    ok, reason = _bclim_looks_valid(produced, orig_size)
    if not ok:
        raise PackError(f"bad BCLIM for {png.name}: {reason}")
    return produced


def unpack_packages(img_bin: Path, img_data: Path, indices: set[int]) -> None:
    img_bin = img_bin.resolve()
    img_data = img_data.resolve()
    img_data.mkdir(parents=True, exist_ok=True)
    needed = sorted(i for i in indices if not (img_data / f"{i:04d}").is_file())
    if not needed:
        print(f"[ie] packages already unpacked ({len(indices)})")
        return
    print(f"[ie] unpacking {len(needed)} package(s) from img.bin ...")
    _run(
        [
            sys.executable,
            IE,
            "--src_img",
            img_bin,
            "--img_dir",
            img_data,
            "unpack",
            "--idx",
            *[str(i) for i in needed],
        ],
        cwd=NLPP_TOOLS,
    )


def ensure_package_data(img_data: Path, index: int) -> Path:
    img_data = img_data.resolve()
    pkg = img_data / f"{index:04d}"
    pkg_dir = img_data / f"{index:04d}_data"
    if not pkg.is_file():
        raise PackError(f"missing unpacked package {pkg}")
    if not pkg_dir.is_dir() or not any(pkg_dir.glob("*.arc")):
        print(f"[pe] unpack {index:04d}")
        _run([sys.executable, PE, str(pkg), "unpack"], cwd=NLPP_TOOLS)
    return pkg_dir


def patch_arc_with_pngs(
    arc_path: Path,
    pngs: list[Path],
    work_dir: Path,
) -> tuple[int, int, list[str]]:
    """Convert PNGs and same-size-inject into the original .arc (no DARC rebuild)."""
    darc = DarcArchive.load(arc_path)
    extract_dir = work_dir / "extract"
    conv_dir = work_dir / "convert"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    # Extract only files we may convert (need original BCLIM beside PNG for png2bclim)
    darc.extract_all(extract_dir)

    ok = skipped = 0
    warnings: list[str] = []
    dirty = False

    for png in pngs:
        entry = None
        for cand in png_to_bclim_candidates(png):
            entry = darc.find(cand)
            if entry:
                break
        if entry is None:
            entry = darc.find(png.stem + ".bclim")
        if entry is None:
            skipped += 1
            warnings.append(f"no BCLIM match for {png.name}")
            continue

        dest_bclim = extract_dir / entry.name
        try:
            new_bclim = convert_png_to_bclim(png, dest_bclim, conv_dir / png.stem)
            darc.replace_same_size(entry, new_bclim.read_bytes())
            dirty = True
            ok += 1
        except PackError as exc:
            skipped += 1
            warnings.append(str(exc))
        except ValueError as exc:
            skipped += 1
            warnings.append(str(exc))

    if dirty:
        darc.save(arc_path)
    return ok, skipped, warnings


def repack_package(img_data: Path, index: int) -> Path:
    img_data = img_data.resolve()
    pkg = img_data / f"{index:04d}"
    print(f"[pe] repack {index:04d}")
    _run([sys.executable, PE, str(pkg), "repack"], cwd=NLPP_TOOLS)
    new_pkg = img_data / f"new_{index:04d}"
    if not new_pkg.is_file():
        raise PackError(f"pe repack did not create {new_pkg}")
    return new_pkg


def splice_packages_into_img(
    img_bin: Path,
    img_data: Path,
    patched: list[int],
    dst: Path,
) -> None:
    """Byte-splice same-size new_XXXX packages into img.bin (safe for LayeredFS).

    Avoids full Image.write() rebuilds that previously black-screened boots.
    """
    if str(NLPP_TOOLS) not in sys.path:
        sys.path.insert(0, str(NLPP_TOOLS))
    from img import Image  # type: ignore

    img_bin = img_bin.resolve()
    img_data = img_data.resolve()
    dst = dst.resolve()
    image = Image(str(img_bin))
    image.parse(False)

    if dst.resolve() != img_bin.resolve():
        shutil.copy2(img_bin, dst)
    data = bytearray(dst.read_bytes())

    for index in patched:
        new_pkg = img_data / f"new_{index:04d}"
        old_pkg = img_data / f"{index:04d}"
        if not new_pkg.is_file():
            raise PackError(f"missing {new_pkg}")
        res = image.entries[index]
        if res is None:
            raise PackError(f"package index {index} empty in img.bin")
        base = res.fw.base_offset
        pkg_len = res.fw.len()
        blob = new_pkg.read_bytes()
        if len(blob) > pkg_len:
            raise PackError(
                f"package {index:04d} grew ({pkg_len} -> {len(blob)}); "
                "refusing full img.bin rewrite"
            )
        if len(blob) < pkg_len:
            # pe often recompresses slightly smaller; pad the img.bin slot.
            print(
                f"[splice] package {index:04d} padded {len(blob)} -> {pkg_len} "
                f"(+{pkg_len - len(blob)} zeros)"
            )
            blob = blob + b"\x00" * (pkg_len - len(blob))
        data[base : base + pkg_len] = blob
        print(f"[splice] package {index:04d} @ {base:#x} ({pkg_len} bytes)")

    dst.write_bytes(data)


def selective_ie_repack(img_bin: Path, img_data: Path, dst: Path, patched: list[int]) -> None:
    """Rebuild img.bin, swapping only new_XXXX packages (avoids full unpack)."""
    import sys as _sys

    img_bin = img_bin.resolve()
    img_data = img_data.resolve()
    dst = dst.resolve()
    print(f"[ie] selective repack ({len(patched)} patched) -> {dst.name}")

    # nlpp-tools img package expects cwd/import path
    if str(NLPP_TOOLS) not in _sys.path:
        _sys.path.insert(0, str(NLPP_TOOLS))
    from img import FileWindow, Image, Package  # type: ignore

    image = Image(str(img_bin))
    image.parse(False)
    patched_set = set(patched)
    for index in patched:
        new_pkg = img_data / f"new_{index:04d}"
        if not new_pkg.is_file():
            raise PackError(f"missing {new_pkg}")
        old = image.entries[index]
        if old is None:
            raise PackError(f"package index {index} empty in img.bin")
        unk = getattr(old, "unk", 0)
        pkg = Package(FileWindow(str(new_pkg)), unk)
        pkg.parse(False)
        image.entries[index] = pkg

    # Unpatched packages still point into the original img.bin — parse headers now.
    for index, res in enumerate(image.entries):
        if res is None or index in patched_set:
            continue
        res.parse(False)

    if dst.exists():
        dst.unlink()
    with open(dst, "wb") as fh:
        image.write(fh)
    if not dst.is_file() or dst.stat().st_size < 1024:
        raise PackError("selective ie repack failed")


def pack_images(
    images_root: Path,
    img_bin: Path,
    work: Path,
    out_img: Path,
    only_keys: set[str] | None = None,
    *,
    splice: bool = True,
) -> dict[str, int]:
    _require_tools()
    if not img_bin.is_file():
        raise PackError(f"img.bin not found: {img_bin}")
    if not images_root.is_dir():
        raise PackError(f"images root missing: {images_root}")

    folders = prefer_asset_folders(images_root)
    if only_keys:
        folders = {k: v for k, v in folders.items() if k in only_keys}

    work = work.resolve()
    out_img = out_img.resolve()
    img_bin = img_bin.resolve()
    images_root = images_root.resolve()
    work.mkdir(parents=True, exist_ok=True)
    img_data = work / "img_data"
    conv = work / "bclim_tmp"
    report_lines: list[str] = []
    # CESA TEXI inject is opt-in only (--only cesa). Auto-packing it previously
    # produced a white boot soft-lock; prefer code.bin --skip-cesa-logo instead.
    cesa_png = images_root / "cesa" / "CESA_240X400.png"
    patch_cesa = cesa_png.is_file() and only_keys is not None and "cesa" in only_keys

    # Group by package index (shared packages get multiple arcs)
    by_pkg: dict[int, list[tuple[str, Path, str]]] = defaultdict(list)
    for key, folder in sorted(folders.items()):
        mapping = resolve_folder(folder.name)
        assert mapping is not None
        index, arc_name = mapping
        by_pkg[index].append((key, folder, arc_name))

    if not by_pkg and not patch_cesa:
        raise PackError("no mapped image folders found (and no assets/images/cesa PNG)")

    totals = {"packages": 0, "png_ok": 0, "png_skip": 0, "arcs": 0, "cesa": 0}
    patched_indices: list[int] = []

    if by_pkg:
        unpack_packages(img_bin, img_data, set(by_pkg))

        for index, items in sorted(by_pkg.items()):
            pkg_dir = ensure_package_data(img_data, index)
            pkg_ok = pkg_skip = 0
            touched = False
            for key, folder, arc_name in items:
                arc_path = pkg_dir / arc_name
                if not arc_path.is_file():
                    # case-insensitive search
                    hits = [p for p in pkg_dir.glob("*.arc") if p.name.lower() == arc_name.lower()]
                    if not hits:
                        report_lines.append(f"[miss-arc] {key}: {arc_name} not in package {index:04d}")
                        continue
                    arc_path = hits[0]

                pngs = iter_asset_pngs(folder)
                if not pngs:
                    report_lines.append(f"[empty] {folder.name}: no PNGs")
                    continue

                print(f"[arc] {arc_path.name} <- {folder.name} ({len(pngs)} png)")
                ok, skipped, warnings = patch_arc_with_pngs(arc_path, pngs, conv / f"{index:04d}_{key}")
                pkg_ok += ok
                pkg_skip += skipped
                touched = touched or ok > 0
                totals["arcs"] += 1
                for w in warnings[:12]:
                    report_lines.append(f"[warn] {key}: {w}")
                if len(warnings) > 12:
                    report_lines.append(f"[warn] {key}: ... +{len(warnings) - 12} more")

            totals["png_ok"] += pkg_ok
            totals["png_skip"] += pkg_skip
            if touched:
                repack_package(img_data, index)
                patched_indices.append(index)
                totals["packages"] += 1
                report_lines.append(f"[ok] package {index:04d}: {pkg_ok} replaced, {pkg_skip} skipped")
            else:
                report_lines.append(f"[skip] package {index:04d}: nothing replaced")

        if patched_indices:
            if splice:
                splice_packages_into_img(img_bin, img_data, patched_indices, out_img)
            else:
                selective_ie_repack(img_bin, img_data, out_img, patched_indices)
        elif not patch_cesa:
            raise PackError("no textures were injected; aborting img.bin rebuild")
        else:
            shutil.copy2(img_bin, out_img)
    else:
        shutil.copy2(img_bin, out_img)

    if patch_cesa:
        from patch_cesa import patch_img_bin

        print(f"[cesa] patching boot warning from {cesa_png}")
        patched = work / "img_cesa.bin"
        patch_img_bin(out_img, cesa_png, patched, work=work / "cesa_work")
        shutil.move(str(patched), str(out_img))
        totals["cesa"] = 1
        report_lines.append("[ok] package 0090: CESA_240X400.texi (boot warning)")

    if not patched_indices and not patch_cesa:
        raise PackError("no textures were injected; aborting img.bin rebuild")

    report_path = work / "image_pack_report.txt"
    report_path.write_text(
        "\n".join(
            [
                "NLPP image pack report",
                f"packages patched: {totals['packages']}",
                f"arcs touched:     {totals['arcs']}",
                f"png replaced:     {totals['png_ok']}",
                f"png skipped:      {totals['png_skip']}",
                f"cesa patched:     {totals['cesa']}",
                f"output:           {out_img}",
                "",
                *report_lines,
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"[report] {report_path}")
    print(
        f"[done] packages={totals['packages']} replaced={totals['png_ok']} "
        f"skipped={totals['png_skip']} cesa={totals['cesa']}"
    )
    return totals


AZAHAR_IMG = (
    Path.home()
    / "AppData"
    / "Roaming"
    / "Azahar"
    / "load"
    / "mods"
    / "00040000000F4E00"
    / "romfs"
    / "img.bin"
)

VANILLA_IMG = (
    ROOT.parent
    / "New Love Plus Plus"
    / "extracted"
    / "romfs"
    / "img.bin"
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Pack EngPatcher PNGs into img.bin")
    p.add_argument("--images", default=str(ASSETS_IMAGES), help="assets/images root")
    default_img = VANILLA_IMG if VANILLA_IMG.is_file() else DEFAULT_IMG_BIN
    p.add_argument("--img-bin", default=str(default_img), help="source romfs/img.bin")
    p.add_argument(
        "--work",
        default=str(ROOT / "out" / "img_work"),
        help="scratch directory",
    )
    p.add_argument(
        "--out",
        default=str(ROOT / "out" / "new_img.bin"),
        help="output img.bin path",
    )
    p.add_argument(
        "--only",
        nargs="*",
        help="optional logical keys to pack (e.g. title mail ncommonmsel(4))",
    )
    p.add_argument(
        "--full-repack",
        action="store_true",
        help="use legacy full Image.write rebuild instead of same-size package splice",
    )
    p.add_argument(
        "--deploy-azahar",
        action="store_true",
        help="copy output img.bin into Azahar LayeredFS mods",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    only = {normalize_folder_key(k) for k in args.only} if args.only else None
    img_bin = Path(args.img_bin)
    # Prefer current Azahar overlay as source when deploying (keeps CESA splice).
    if args.deploy_azahar and AZAHAR_IMG.is_file():
        img_bin = AZAHAR_IMG
        print(f"[img] using Azahar overlay as source: {img_bin}")
    try:
        pack_images(
            Path(args.images),
            img_bin,
            Path(args.work),
            Path(args.out),
            only_keys=only,
            splice=not args.full_repack,
        )
        if args.deploy_azahar:
            AZAHAR_IMG.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(args.out), AZAHAR_IMG)
            print(f"[deploy] {AZAHAR_IMG}")
        return 0
    except PackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
