#!/usr/bin/env python3
"""Fetch / wire local tools needed by patch_cia.py and the drop bat.

Auto-downloads open-source binaries where licensing is clear:
  - 3dstool (dnasdw/3dstool)
  - ctrtool / makerom (3DSGuy/Project_CTR)
  - seeddb.bin (ihaveamac/3DS-rom-tools)

decrypt.exe is vendored under tools/Batch-CIA-3DS-Decryptor-Redux/
(credit: davidmorom / xxmichibxx Batch CIA 3DS Decryptor Redux) and copied
into tools/cia/ for the patcher.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
CIA_TOOLS = ROOT / "tools" / "cia"
VENDORED_DECRYPT = ROOT / "tools" / "Batch-CIA-3DS-Decryptor-Redux" / "decrypt.exe"
SIBLING_DECRYPTOR = (
    ROOT.parent
    / "New Love Plus Plus"
    / "tools"
    / "Batch-CIA-3DS-Decryptor-Redux-1.0.6.2"
    / "bin"
)

THREEDS_URL = "https://github.com/dnasdw/3dstool/releases/download/v1.2.6/3dstool.zip"
CTRTOOL_URL = (
    "https://github.com/3DSGuy/Project_CTR/releases/download/"
    "ctrtool-v1.2.1/ctrtool-v1.2.1-win_x64.zip"
)
MAKEROM_URL = (
    "https://github.com/3DSGuy/Project_CTR/releases/download/"
    "makerom-v0.19.0/makerom-v0.19.0-win_x86_64.zip"
)
SEEDDB_URL = (
    "https://raw.githubusercontent.com/ihaveamac/3DS-rom-tools/"
    "master/seeddb/seeddb.bin"
)

EXTRACT_ROMFS_SRC = ROOT.parent / "New Love Plus Plus" / "tools" / "extract_romfs.py"


def download(url: str, dest: Path) -> None:
    print(f"downloading {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "NewLovePlusPlusEngPatcher/setup_tools"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out)


def _extract_exe_from_zip(zpath: Path, exe_name: str, dest: Path) -> None:
    with zipfile.ZipFile(zpath, "r") as zf:
        matches = [
            n
            for n in zf.namelist()
            if Path(n).name.lower() == exe_name.lower() and not n.endswith("/")
        ]
        if not matches:
            raise SystemExit(f"{exe_name} not found inside {zpath.name}")
        matches.sort(key=lambda n: n.count("/"))
        member = matches[0]
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, dest.open("wb") as out:
            shutil.copyfileobj(src, out)


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
        matches = list((CIA_TOOLS / "3dstool").rglob("3dstool.exe"))
        if not matches:
            raise SystemExit("3dstool.exe missing after extract")
        if matches[0].parent != CIA_TOOLS / "3dstool":
            for p in matches[0].parent.iterdir():
                shutil.move(str(p), CIA_TOOLS / "3dstool" / p.name)
    print(f"ok: {exe}")


def ensure_project_ctr_bin(name: str, url: str) -> None:
    dest = CIA_TOOLS / name
    if dest.is_file():
        print(f"ok: {dest}")
        return
    with tempfile.TemporaryDirectory(prefix="nlpp_ctr_") as tmp:
        zpath = Path(tmp) / f"{name}.zip"
        download(url, zpath)
        _extract_exe_from_zip(zpath, name, dest)
    print(f"ok: {dest} (downloaded)")


def ensure_seeddb() -> None:
    dest = CIA_TOOLS / "seeddb.bin"
    if dest.is_file():
        print(f"ok: {dest}")
        return
    download(SEEDDB_URL, dest)
    print(f"ok: {dest} (downloaded)")


def ensure_decrypt_exe() -> None:
    """Install vendored decrypt.exe into tools/cia/."""
    dest = CIA_TOOLS / "decrypt.exe"
    if dest.is_file():
        print(f"ok: {dest}")
        return

    candidates = [
        VENDORED_DECRYPT,
        SIBLING_DECRYPTOR / "decrypt.exe",
        ROOT.parent
        / "New Love Plus Plus"
        / "tools"
        / "Batch-CIA-3DS-Decryptor-Redux-1.0.6.2"
        / "decrypt.exe",
    ]
    for src in candidates:
        if src.is_file():
            CIA_TOOLS.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            print(f"copied: {dest} <- {src}")
            return

    raise SystemExit(
        "decrypt.exe missing. Expected vendored copy at:\n"
        f"  {VENDORED_DECRYPT}\n"
        "See tools/Batch-CIA-3DS-Decryptor-Redux/CREDITS.md"
    )


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


def ensure_python_deps() -> None:
    missing: list[str] = []
    for mod, pip_name in (
        ("PIL", "Pillow"),
        ("numpy", "numpy"),
        ("zopfli", "zopfli"),
    ):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pip_name)
    if missing:
        req = ROOT / "requirements.txt"
        print()
        print(f"[!] Missing Python packages: {', '.join(missing)}")
        print(f"    Run:  {sys.executable} -m pip install -r \"{req}\"")
        print()
        raise SystemExit("Python dependencies missing")


def main() -> int:
    print("Setting up CIA patch tools...")
    CIA_TOOLS.mkdir(parents=True, exist_ok=True)
    ensure_python_deps()
    ensure_3dstool()
    ensure_project_ctr_bin("ctrtool.exe", CTRTOOL_URL)
    ensure_project_ctr_bin("makerom.exe", MAKEROM_URL)
    ensure_seeddb()
    ensure_decrypt_exe()
    ensure_extract_romfs()
    print()
    print("nlpp-tools (vendored):", ROOT / "tools" / "nlpp-tools")
    print("UI font (OFL):", ROOT / "assets" / "fonts" / "MPLUS1p-Regular.ttf")
    print(
        "decrypt.exe credit: davidmorom / xxmichibxx "
        "(tools/Batch-CIA-3DS-Decryptor-Redux/CREDITS.md)"
    )
    print()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
