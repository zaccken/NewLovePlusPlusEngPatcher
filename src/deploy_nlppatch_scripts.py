#!/usr/bin/env python3
"""Deploy vendored NLPPATCH English .dbin2 scripts to Azahar LayeredFS.

Source of truth (offline copy):
  vendor/NLPPATCH/release/romfs/script/bin/script/*.dbin2

NLPPATCH only translated the ``script`` pack (not NLP_01 / NLP_02).
After copy, heroine-name tokens are cleaned via patch_names.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from patch_names import patch_dbin2_tree

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = (
    ROOT / "vendor" / "NLPPATCH" / "release" / "romfs" / "script" / "bin" / "script"
)
AZAHAR_SCRIPT = (
    Path.home()
    / "AppData"
    / "Roaming"
    / "Azahar"
    / "load"
    / "mods"
    / "00040000000F4E00"
    / "romfs"
    / "script"
    / "bin"
    / "script"
)


def deploy(src_dir: Path, dest_dir: Path) -> int:
    if not src_dir.is_dir():
        raise SystemExit(f"vendored NLPPATCH scripts missing: {src_dir}")
    files = sorted(src_dir.glob("*.dbin2"))
    if not files:
        raise SystemExit(f"no .dbin2 files in {src_dir}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src in files:
        shutil.copy2(src, dest_dir / src.name)
    return len(files)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--azahar", type=Path, default=AZAHAR_SCRIPT)
    ap.add_argument(
        "--skip-names",
        action="store_true",
        help="do not run heroine-name token cleanup after copy",
    )
    args = ap.parse_args(argv)

    n = deploy(args.src.resolve(), args.azahar.resolve())
    print(f"[deploy] {n} scripts -> {args.azahar}")

    if not args.skip_names:
        touched, repl = patch_dbin2_tree(args.azahar.resolve())
        print(f"[names] {touched} files, {repl} token replacements")

    print("[done] Fully quit Azahar and relaunch to load new scripts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
