#!/usr/bin/env python3
"""Fetch / wire local tools needed by patch_cia.py."""

from __future__ import annotations

import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
CIA_TOOLS = ROOT / "tools" / "cia"
SIBLING_DECRYPTOR = (
    ROOT.parent
    / "New Love Plus Plus"
    / "tools"
    / "Batch-CIA-3DS-Decryptor-Redux-1.0.6.2"
    / "bin"
)
THREEDS_URL = "https://github.com/dnasdw/3dstool/releases/download/v1.2.6/3dstool.zip"
EXTRACT_ROMFS_SRC = ROOT.parent / "New Love Plus Plus" / "tools" / "extract_romfs.py"


def download(url: str, dest: Path) -> None:
    print(f"downloading {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)


def ensure_3dstool() -> None:
    exe = CIA_TOOLS / "3dstool" / "3dstool.exe"
    if exe.is_file():
        print(f"ok: {exe}")
        return
    zpath = CIA_TOOLS / "3dstool.zip"
    download(THREEDS_URL, zpath)
    with zipfile.ZipFile(zpath, "r") as zf:
        zf.extractall(CIA_TOOLS / "3dstool")
    if not exe.is_file():
        # zip may nest one level
        matches = list((CIA_TOOLS / "3dstool").rglob("3dstool.exe"))
        if not matches:
            raise SystemExit("3dstool.exe missing after extract")
        # move up if nested
        if matches[0].parent != CIA_TOOLS / "3dstool":
            for p in matches[0].parent.iterdir():
                shutil.move(str(p), CIA_TOOLS / "3dstool" / p.name)
    print(f"ok: {exe}")


def ensure_decryptor_bins() -> None:
    CIA_TOOLS.mkdir(parents=True, exist_ok=True)
    names = ("ctrtool.exe", "makerom.exe", "decrypt.exe", "seeddb.bin")
    for name in names:
        dest = CIA_TOOLS / name
        if dest.is_file():
            print(f"ok: {dest}")
            continue
        src = SIBLING_DECRYPTOR / name
        if not src.is_file():
            raise SystemExit(
                f"missing {name}. Place Batch CIA 3DS Decryptor Redux bins in:\n"
                f"  {SIBLING_DECRYPTOR}\n"
                f"or copy {name} into {CIA_TOOLS}"
            )
        shutil.copy2(src, dest)
        print(f"copied: {dest}")


def ensure_extract_romfs() -> None:
    dest = CIA_TOOLS / "extract_romfs.py"
    if dest.is_file():
        print(f"ok: {dest}")
        return
    if EXTRACT_ROMFS_SRC.is_file():
        shutil.copy2(EXTRACT_ROMFS_SRC, dest)
        print(f"copied: {dest}")
    else:
        print("note: extract_romfs.py not found (optional; 3dstool is used instead)")


def main() -> int:
    print("Setting up CIA patch tools...")
    ensure_3dstool()
    ensure_decryptor_bins()
    ensure_extract_romfs()
    print()
    print("nlpp-tools (kiwiz — optional, for image packing):")
    print(f"  {ROOT / 'tools' / 'nlpp-tools'}")
    print("  https://github.com/kiwiz/nlpp-tools")
    print("  ie / pe / darctool / png2bclim / png2texi")
    print()
    print("Done. Example:")
    print('  python src/patch_cia.py --cia "..\\New Love Plus Plus\\YourGame.cia"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
