#!/usr/bin/env python3
"""One-click New Love Plus+ English patcher (CIA or 3DS/CCI in → CIA out).

Pipeline:
  encrypted CIA or .3ds/.cci -> decrypt -> extract CXI/RomFS -> inject EN .dbin2
  -> English heroine-name patches (scripts / resident TRB / img.bin table)
  -> optional single-pane name code.bin patch (--patch-code)
  -> rebuild RomFS/CXI/CIA (decrypted, CFW/emulator ready)

NLPPGit (https://github.com/Makein/NLPPGit) is translation assets only.
img.bin helpers from kiwiz/nlpp-tools (https://github.com/kiwiz/nlpp-tools);
also NLPTextTool / LovePlusProject refs plus ctrtool / makerom / 3dstool /
Batch CIA Decryptor.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
TOOLS = ROOT / "tools"
CIA_TOOLS = TOOLS / "cia"
TOOL_3DS = CIA_TOOLS / "3dstool" / "3dstool.exe"
CTRTOOL = CIA_TOOLS / "ctrtool.exe"
MAKEROM = CIA_TOOLS / "makerom.exe"
DECRYPT = CIA_TOOLS / "decrypt.exe"
SEEDDB = CIA_TOOLS / "seeddb.bin"

DEFAULT_DBIN = ROOT / "rebuild_dbin2"
DEFAULT_EXTRACTED = ROOT.parent / "New Love Plus Plus" / "extracted"
DEFAULT_MAIN_CXI = Path(r"C:\Users\Zepse\nlpp_work\main.cxi")
DEFAULT_ROMFS = Path(r"C:\Users\Zepse\nlpp_work\romfs")
DEFAULT_IMG_BIN = DEFAULT_EXTRACTED / "romfs" / "img.bin"
DEFAULT_IMAGES = ROOT / "assets" / "images"
DEFAULT_PACKED_IMG = ROOT / "cache" / "new_img.bin"
DEFAULT_CODE_BIN = DEFAULT_EXTRACTED / "exefs" / "code.bin"
# Legacy fallback when sibling extracted/ is absent (dev machines).
_LEGACY_IMG_BIN = Path(r"C:\Users\Zepse\nlpp_work\romfs\img.bin")
TITLE_ID = "00040000000F4E00"
PACKS = ("NLP_01", "NLP_02", "script")

# Accepted SHA-1 digests for known New Love Plus+ dumps (CIA and/or .3ds/.cci).
# Typical decrypted CIAs will not match (by design) — the patcher decrypts after verify.
# Some decrypted full .3ds dumps are listed (e.g. d138…) so cartridge dumps can hash-check.
ALLOWED_CIA_SHA1 = frozenset(
    {
        "a9fbd2e6d790b6cb6194f7820e1a71f597160f2b",  # encrypted CIA (headmasta)
        "811d2f0f72c2a1437997256f30b18fbb2dea6cda",  # decrypted CIA
        "6af1751f8b4f9d074311f3a7cf2b5d3c5e807cc8",
        "d138d92fd9d522827cb9665bc2c954f1e8ba1f92",  # decrypted full .3ds
        "6428e72eefec31d19282d2c7f0cb5082723a3206",  # encrypted trim .3ds
    }
)
ALLOWED_DUMP_SHA1 = ALLOWED_CIA_SHA1
# Primary / historically documented dump (kept for CLI help / display).
EXPECTED_CIA_SHA1 = "a9fbd2e6d790b6cb6194f7820e1a71f597160f2b"

_CCI_EXTS = {".3ds", ".cci"}
_CIA_EXTS = {".cia"}
# Batch Decryptor Redux partition name → CCI slot index.
_CCI_NCCH_SLOTS = (
    ("Main", 0),
    ("Manual", 1),
    ("DownloadPlay", 2),
    ("Partition4", 3),
    ("Partition5", 4),
    ("Partition6", 5),
    ("N3DSUpdateData", 6),
    ("UpdateData", 7),
)


class PatchError(RuntimeError):
    pass


def _run(cmd: list[str | Path], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
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
    if proc.stderr.strip():
        print(proc.stderr.rstrip(), file=sys.stderr)
    if check and proc.returncode != 0:
        raise PatchError(f"command failed ({proc.returncode}): {printable}")
    return proc


def _require_tools() -> None:
    missing = [p for p in (TOOL_3DS, CTRTOOL, MAKEROM, DECRYPT, SEEDDB) if not p.is_file()]
    if missing:
        names = ", ".join(p.name for p in missing)
        raise PatchError(
            f"missing CIA tools: {names}\n"
            f"Run: python src/setup_tools.py\n"
            f"Expected under: {CIA_TOOLS}"
        )


def _ctrtool_info(path: Path) -> str:
    proc = _run([CTRTOOL, "--seeddb", SEEDDB, path], check=False)
    return (proc.stdout or "") + (proc.stderr or "")


def detect_rom_kind(path: Path) -> str:
    """Return 'cia' or 'cci' from extension / NCSD magic."""
    ext = path.suffix.lower()
    if ext in _CIA_EXTS:
        return "cia"
    if ext in _CCI_EXTS:
        return "cci"
    try:
        with path.open("rb") as fh:
            fh.seek(0x100)
            magic = fh.read(4)
        if magic == b"NCSD":
            return "cci"
    except OSError:
        pass
    raise PatchError(
        f"unsupported rom type: {path.name} "
        f"(expected .cia / .3ds / .cci)"
    )


def is_encrypted_cia(cia: Path) -> bool:
    """True if main NCCH still has a retail crypto key (CIA or CCI)."""
    info = _ctrtool_info(cia)
    # Decrypted NCCH shows "Crypto Key: None"; encrypted shows Secure/Fixed/...
    if re.search(r"Crypto Key\s+None", info):
        return False
    if re.search(r"Crypto Key\s+(Secure|Fixed|Key 0x)", info):
        return True
    # Fallback: Batch Decryptor style — treat unknown as encrypted
    if "NCCH" in info and "Crypto Key" in info:
        return "None" not in info.split("Crypto Key", 1)[-1][:40]
    raise PatchError(f"could not determine encryption state of {cia}")


is_encrypted_rom = is_encrypted_cia


def _prepare_decrypt_workdir(rom: Path, work: Path) -> tuple[Path, Path]:
    """Copy decryptor bins + rom into work/; return (work_rom, bin_dir)."""
    bin_dir = work / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("ctrtool.exe", "makerom.exe", "decrypt.exe", "seeddb.bin"):
        shutil.copy2(CIA_TOOLS / name, bin_dir / name)

    work_rom = work / rom.name
    if work_rom.resolve() != rom.resolve():
        shutil.copy2(rom, work_rom)
    return work_rom, bin_dir


def _collect_decrypt_ncchs(bin_dir: Path) -> list[Path]:
    """Find NCCH blobs written by decrypt.exe (tmp.* or named partitions)."""
    named = sorted(bin_dir.glob("tmp.*.ncch"))
    if named:
        return named
    named = sorted(p for p in bin_dir.glob("*.ncch") if p.stat().st_size > 0)
    return named


def _rename_decrypt_ncchs(bin_dir: Path) -> None:
    """Match Batch Decryptor Redux :subroutineRename (foo.Main.ncch → tmp.Main.ncch)."""
    for src in list(bin_dir.glob("*.ncch")):
        name = src.name
        if name.startswith("tmp."):
            continue
        # e.g. Game.Main.ncch / 00040000….Main.ncch → tmp.Main.ncch
        for label, _slot in _CCI_NCCH_SLOTS:
            if name.endswith(f".{label}.ncch") or name == f"{label}.ncch":
                dest = bin_dir / f"tmp.{label}.ncch"
                if dest.exists() and dest.resolve() != src.resolve():
                    dest.unlink()
                if src.resolve() != dest.resolve():
                    src.rename(dest)
                break


def decrypt_cia(cia: Path, work: Path) -> Path:
    """Decrypt an encrypted CIA into work/, return path to *-decrypted.cia."""
    out_name = f"{cia.stem}-decrypted.cia"
    out_path = work / out_name
    if out_path.is_file():
        print(f"[decrypt] using existing {out_path.name}")
        return out_path

    print(f"[decrypt] decrypting {cia.name} ...")
    work_cia, bin_dir = _prepare_decrypt_workdir(cia, work)

    # decrypt.exe writes tmp.*.ncch into bin\
    _run(["cmd", "/c", f'echo.| bin\\decrypt.exe "{work_cia.name}"'], cwd=work)
    _rename_decrypt_ncchs(bin_dir)

    ncchs = _collect_decrypt_ncchs(bin_dir)
    if not ncchs:
        raise PatchError("decrypt.exe produced no NCCH partitions")

    # Normalize names like the Batch Decryptor (numeric slots for CIA rebuild).
    normalized: list[Path] = []
    for i, src in enumerate(ncchs):
        dest = bin_dir / f"tmp.{i}.ncch"
        if src.resolve() != dest.resolve():
            if dest.exists():
                dest.unlink()
            src.rename(dest)
        normalized.append(dest)

    args: list[str | Path] = [
        bin_dir / "makerom.exe",
        "-f",
        "cia",
        "-ignoresign",
        "-target",
        "p",
        "-o",
        out_name,
    ]
    for i, ncch in enumerate(normalized):
        args.extend(["-i", f"{ncch}:{i}:{i}"])
    _run(args, cwd=work)

    if not out_path.is_file():
        raise PatchError("makerom failed to write decrypted CIA")
    print(f"[decrypt] wrote {out_path}")
    return out_path


def decrypt_3ds_to_cxi(cci: Path, work: Path) -> tuple[Path, Path | None]:
    """Decrypt an encrypted .3ds/.cci; return (main CXI, manual CFA|None).

    Uses Batch CIA 3DS Decryptor Redux's decrypt.exe partition naming
    (tmp.Main.ncch / tmp.Manual.ncch), then copies those blobs for the
    existing CXI patch path. Does not keep an encrypted cartridge image.
    """
    out_main = work / "main.cxi"
    out_manual = work / "manual.cfa"
    if out_main.is_file():
        print(f"[decrypt] using existing {out_main.name}")
        return out_main, (out_manual if out_manual.is_file() else None)

    print(f"[decrypt] decrypting {cci.name} (CCI/3DS) ...")
    work_cci, bin_dir = _prepare_decrypt_workdir(cci, work)
    _run(["cmd", "/c", f'echo.| bin\\decrypt.exe "{work_cci.name}"'], cwd=work)
    _rename_decrypt_ncchs(bin_dir)

    main = bin_dir / "tmp.Main.ncch"
    if not main.is_file():
        # Fallback: first NCCH blob
        ncchs = _collect_decrypt_ncchs(bin_dir)
        if not ncchs:
            raise PatchError(
                "decrypt.exe produced no NCCH partitions from .3ds/.cci "
                "(is the dump encrypted with seed crypto? seeddb.bin required)"
            )
        main = ncchs[0]
        print(f"[decrypt] warning: no tmp.Main.ncch; using {main.name}")

    shutil.copy2(main, out_main)
    manual_src = bin_dir / "tmp.Manual.ncch"
    manual: Path | None = None
    if manual_src.is_file():
        shutil.copy2(manual_src, out_manual)
        manual = out_manual

    print(f"[decrypt] wrote decrypted CXI ({out_main.stat().st_size:,} bytes)")
    return out_main, manual


def extract_cci_partitions(cci: Path, out_dir: Path) -> tuple[Path, Path | None]:
    """Extract partition0 (game CXI) and optional partition1 (manual) from CCI."""
    out_dir.mkdir(parents=True, exist_ok=True)
    main = out_dir / "partition0.cxi"
    manual = out_dir / "partition1.cfa"
    if main.is_file():
        print(f"[extract] reusing {main.name}")
        return main, (manual if manual.is_file() else None)

    print(f"[extract] extracting CCI partitions from {cci.name} ...")
    cmd: list[str | Path] = [
        TOOL_3DS,
        "-xvtf",
        "cci",
        cci,
        "--partition0",
        main,
    ]
    # Always request partition1; 3dstool skips missing slots quietly on some dumps.
    cmd.extend(["--partition1", manual])
    _run(cmd, check=False)
    if not main.is_file():
        raise PatchError(f"3dstool failed to extract partition0 from {cci}")
    return main, (manual if manual.is_file() and manual.stat().st_size > 0 else None)


def prepare_cxi_from_rom(
    rom: Path,
    work: Path,
    *,
    kind: str,
    assume_decrypted: bool = False,
    force_decrypt: bool = False,
) -> tuple[Path, Path | None, int | None]:
    """Decrypt/extract rom → (cxi, manual, title_version)."""
    title_ver = parse_title_version(rom)

    if kind == "cia":
        sibling_dec = (
            ROOT.parent / "New Love Plus Plus" / "NewLovePlusPlus-decrypted.cia"
        )
        if assume_decrypted or ("decrypted" in rom.name.lower()):
            decrypted = rom
            print("[decrypt] skipped (flag / filename)")
        else:
            try:
                encrypted = is_encrypted_rom(rom)
            except PatchError as exc:
                print(f"[decrypt] warning: {exc}; attempting decrypt")
                encrypted = True
            if encrypted and sibling_dec.is_file() and not force_decrypt:
                decrypted = sibling_dec
                print(f"[decrypt] reusing sibling decrypted CIA: {sibling_dec}")
            elif encrypted:
                decrypted = decrypt_cia(rom, work / "decrypt")
            else:
                decrypted = rom
                print("[decrypt] CIA already decrypted")
        title_ver = parse_title_version(decrypted) or title_ver
        cxi, manual = extract_cia_contents(decrypted, work / "contents")
        return cxi, manual, title_ver

    # CCI / .3ds
    if assume_decrypted or ("decrypted" in rom.name.lower()):
        print("[decrypt] skipped (flag / filename); extracting CCI partitions")
        cxi, manual = extract_cci_partitions(rom, work / "cci_parts")
        return cxi, manual, title_ver

    try:
        encrypted = is_encrypted_rom(rom)
    except PatchError as exc:
        print(f"[decrypt] warning: {exc}; attempting decrypt")
        encrypted = True

    if encrypted:
        cxi, manual = decrypt_3ds_to_cxi(rom, work / "decrypt_3ds")
    else:
        print("[decrypt] .3ds/.cci already decrypted; extracting partitions")
        cxi, manual = extract_cci_partitions(rom, work / "cci_parts")
    return cxi, manual, title_ver


def extract_cia_contents(cia: Path, out_dir: Path) -> tuple[Path, Path | None]:
    """Extract CIA content files; return (content0, content1|None)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(out_dir.glob("content.0000.*"))
    if existing:
        c0 = existing[0]
        c1s = sorted(out_dir.glob("content.0001.*"))
        print(f"[extract] reusing {c0.name}")
        return c0, (c1s[0] if c1s else None)

    print(f"[extract] extracting contents from {cia.name} ...")
    _run([CTRTOOL, "--seeddb", SEEDDB, f"--contents={out_dir / 'content'}", cia])
    c0s = sorted(out_dir.glob("content.0000.*"))
    if not c0s:
        raise PatchError("ctrtool --contents produced no content.0000.*")
    c1s = sorted(out_dir.glob("content.0001.*"))
    return c0s[0], (c1s[0] if c1s else None)


def split_cxi(cxi: Path, parts_dir: Path) -> dict[str, Path]:
    parts_dir.mkdir(parents=True, exist_ok=True)
    needed = {
        "header": parts_dir / "ncchheader.bin",
        "exh": parts_dir / "exheader.bin",
        "plain": parts_dir / "plain.bin",
        "logo": parts_dir / "logo.bin",
        "exefs": parts_dir / "exefs.bin",
        "romfs": parts_dir / "romfs.bin",
    }
    if all(p.is_file() for p in needed.values()):
        print("[cxi] reusing split NCCH parts")
        return needed

    print(f"[cxi] splitting {cxi.name} ...")
    _run(
        [
            TOOL_3DS,
            "-xvtf",
            "cxi",
            cxi,
            "--header",
            needed["header"],
            "--exh",
            needed["exh"],
            "--plain",
            needed["plain"],
            "--logo",
            needed["logo"],
            "--exefs",
            needed["exefs"],
            "--romfs",
            needed["romfs"],
        ]
    )
    return needed


def ensure_romfs_dir(romfs_bin: Path, romfs_dir: Path, reuse: Path | None) -> Path:
    if reuse and reuse.is_dir() and (reuse / "script" / "bin" / "script").is_dir():
        print(f"[romfs] using extracted tree: {reuse}")
        return reuse

    if romfs_dir.is_dir() and (romfs_dir / "script" / "bin" / "script").is_dir():
        print(f"[romfs] using existing work tree: {romfs_dir}")
        return romfs_dir

    print(f"[romfs] extracting {romfs_bin.name} (large, may take a few minutes) ...")
    romfs_dir.mkdir(parents=True, exist_ok=True)
    _run([TOOL_3DS, "-xvtf", "romfs", romfs_bin, "--romfs-dir", romfs_dir])
    return romfs_dir


def inject_dbin2(romfs_dir: Path, dbin_root: Path) -> int:
    total = 0
    for pack in PACKS:
        src_dir = dbin_root / pack
        if not src_dir.is_dir():
            raise PatchError(f"missing packed scripts: {src_dir}")
        dest_dir = romfs_dir / "script" / "bin" / pack
        if not dest_dir.is_dir():
            raise PatchError(f"RomFS missing script pack folder: {dest_dir}")
        files = sorted(src_dir.glob("*.dbin2"))
        if not files:
            raise PatchError(f"no .dbin2 files in {src_dir}")
        for src in files:
            shutil.copy2(src, dest_dir / src.name)
            total += 1
        print(f"[inject] {pack}: {len(files)} file(s)")
    return total


def rebuild_romfs(romfs_dir: Path, out_bin: Path) -> None:
    print("[romfs] rebuilding romfs.bin (large, may take several minutes) ...")
    if out_bin.exists():
        out_bin.unlink()
    _run([TOOL_3DS, "-cvtf", "romfs", out_bin, "--romfs-dir", romfs_dir])
    if not out_bin.is_file() or out_bin.stat().st_size < 1024:
        raise PatchError("romfs rebuild failed")


def rebuild_cxi(parts: dict[str, Path], new_romfs: Path, out_cxi: Path) -> None:
    print("[cxi] rebuilding patched CXI ...")
    if out_cxi.exists():
        out_cxi.unlink()
    cmd: list[str | Path] = [
        TOOL_3DS,
        "-cvtf",
        "cxi",
        out_cxi,
        "--header",
        parts["header"],
        "--exh",
        parts["exh"],
        "--exefs",
        parts["exefs"],
        "--romfs",
        new_romfs,
        "--not-encrypt",
    ]
    if parts["plain"].is_file() and parts["plain"].stat().st_size:
        cmd.extend(["--plain", parts["plain"]])
    if parts["logo"].is_file() and parts["logo"].stat().st_size:
        cmd.extend(["--logo", parts["logo"]])
    _run(cmd)
    if not out_cxi.is_file():
        raise PatchError("CXI rebuild failed")


def rebuild_cia(cxi: Path, manual: Path | None, out_cia: Path, title_ver: int | None) -> None:
    print(f"[cia] building {out_cia.name} ...")
    if out_cia.exists():
        out_cia.unlink()
    cmd: list[str | Path] = [
        MAKEROM,
        "-f",
        "cia",
        "-o",
        out_cia,
        "-ignoresign",
        "-target",
        "p",
        "-content",
        f"{cxi}:0:0",
    ]
    if manual and manual.is_file():
        cmd.extend(["-content", f"{manual}:1:1"])
    if title_ver is not None:
        cmd.extend(["-ver", str(title_ver)])
    _run(cmd)
    if not out_cia.is_file():
        raise PatchError("CIA rebuild failed")


def _resolve_resident_trb(romfs_hint: Path | None) -> Path | None:
    candidates = []
    if romfs_hint is not None:
        candidates.append(
            romfs_hint
            / "SystemData"
            / "TextResource"
            / "textresource_resident_jpn.trb"
        )
    candidates.append(
        DEFAULT_ROMFS
        / "SystemData"
        / "TextResource"
        / "textresource_resident_jpn.trb"
    )
    candidates.append(
        DEFAULT_EXTRACTED
        / "romfs"
        / "SystemData"
        / "TextResource"
        / "textresource_resident_jpn.trb"
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def apply_name_patches(
    romfs_dir: Path,
    *,
    skip: bool = False,
) -> None:
    """English heroine names in scripts / resident TRB / img.bin name table."""
    if skip:
        print("[names] skipped (--skip-name-patches)")
        return
    from patch_names import apply_romfs_name_patches

    apply_romfs_name_patches(romfs_dir)


def _unpack_exefs(exefs_bin: Path, exefs_dir: Path) -> tuple[Path, Path]:
    """Unpack ExeFS; return (code.bin path, exefs.header path)."""
    if exefs_dir.exists():
        shutil.rmtree(exefs_dir)
    exefs_dir.mkdir(parents=True)
    header = exefs_dir / "exefs.header"
    print("[code] unpacking ExeFS ...")
    _run(
        [
            TOOL_3DS,
            "-xvtf",
            "exefs",
            exefs_bin,
            "--exefs-dir",
            exefs_dir,
            "--header",
            header,
        ]
    )
    code = exefs_dir / "code.bin"
    if not code.is_file():
        alt = exefs_dir / ".code"
        if alt.is_file():
            code = alt
        else:
            raise PatchError(f"no code.bin in unpacked ExeFS: {exefs_dir}")
    if not header.is_file():
        raise PatchError(f"no ExeFS header written: {header}")
    return code, header


def _repack_exefs(exefs_dir: Path, header: Path, out: Path) -> Path:
    if out.exists():
        out.unlink()
    print("[code] repacking ExeFS ...")
    _run(
        [
            TOOL_3DS,
            "-cvtf",
            "exefs",
            out,
            "--exefs-dir",
            exefs_dir,
            "--header",
            header,
        ]
    )
    if not out.is_file():
        raise PatchError("ExeFS rebuild failed")
    return out


def _blz_uncompress(src: Path, dest: Path) -> Path:
    """BLZ-decompress ExeFS .code (stock ~4.4MB -> ~8.1MB)."""
    if dest.exists():
        dest.unlink()
    _run(
        [
            TOOL_3DS,
            "-uvf",
            src,
            "--compress-type",
            "blz",
            "--compress-out",
            dest,
        ]
    )
    if not dest.is_file():
        raise PatchError(f"BLZ uncompress failed: {src}")
    return dest


def _blz_compress(src: Path, dest: Path, *, exact_size: int | None = None) -> Path:
    """BLZ-compress decompressed code.bin back into ExeFS slot size."""
    if dest.exists():
        dest.unlink()
    _run(
        [
            TOOL_3DS,
            "-zvf",
            src,
            "--compress-type",
            "blz",
            "--compress-out",
            dest,
        ]
    )
    if not dest.is_file():
        raise PatchError(f"BLZ compress failed: {src}")
    if exact_size is not None and dest.stat().st_size != exact_size:
        raise PatchError(
            f"BLZ compress size {dest.stat().st_size} != ExeFS slot {exact_size}. "
            "code.bin changes must stay BLZ-compatible with the stock compressed size."
        )
    return dest


def patch_exefs_code(exefs_bin: Path, work: Path) -> Path:
    """Unpack ExeFS, BLZ-decompress, apply name patch, recompress, repack."""
    from patch_code import patch_code_bin

    exefs_dir = work / "exefs_patched"
    code_cmp, header = _unpack_exefs(exefs_bin, exefs_dir)
    slot = code_cmp.stat().st_size
    code_dec = work / "code_namepatch_dec.bin"
    _blz_uncompress(code_cmp, code_dec)
    try:
        patch_code_bin(code_dec, force=True)
    except ValueError as exc:
        raise PatchError(str(exc)) from exc
    _blz_compress(code_dec, code_cmp, exact_size=slot)
    return _repack_exefs(exefs_dir, header, work / "exefs_namepatch.bin")


def inject_exefs_code(exefs_bin: Path, work: Path, code_src: Path) -> Path:
    """Replace ExeFS .code with a prebuilt binary (decompressed or already BLZ).

    Decompressed LayeredFS/Azahar code.bin (~8.1MB) is BLZ-compressed. Small
    patches that fill .text zero-pad may grow the compressed payload slightly;
    3dstool ExeFS/CXI rebuild accepts the larger .code section.
    """
    code_src = code_src.resolve()
    if not code_src.is_file():
        raise PatchError(f"--inject-code not found: {code_src}")
    exefs_dir = work / "exefs_injected"
    code_cmp, header = _unpack_exefs(exefs_bin, exefs_dir)
    slot = code_cmp.stat().st_size
    src_size = code_src.stat().st_size

    if src_size == slot:
        code_cmp.write_bytes(code_src.read_bytes())
        print(f"[code] injected compressed {code_src} ({src_size:,} bytes)")
    else:
        # Azahar/LayeredFS code.bin is decompressed (~8.1MB).
        print(f"[code] BLZ-compressing {code_src.name} ({src_size:,}; stock slot {slot:,}) ...")
        tmp = work / "code_inject_cmp.bin"
        _blz_compress(code_src, tmp, exact_size=None)
        new_size = tmp.stat().st_size
        if new_size != slot:
            print(
                f"[code] BLZ size {new_size:,} (was {slot:,}; "
                f"delta {new_size - slot:+d}) — ExeFS will grow"
            )
        code_cmp.write_bytes(tmp.read_bytes())
        print(f"[code] injected decompressed->BLZ {code_src}")
    return _repack_exefs(exefs_dir, header, work / "exefs_injected.bin")


def apply_romfs_overlay(romfs_dir: Path, overlay: Path) -> int:
    """Copy files from overlay onto romfs_dir (files only; keeps relative paths)."""
    overlay = overlay.resolve()
    if not overlay.is_dir():
        raise PatchError(f"--romfs-overlay not a directory: {overlay}")
    count = 0
    for src in overlay.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(overlay)
        dest = romfs_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        count += 1
    print(f"[overlay] copied {count} files from {overlay}")
    return count


def write_layeredfs(
    out_dir: Path,
    dbin_root: Path,
    img_bin: Path | None = None,
    *,
    resident_src: Path | None = None,
    code_bin_src: Path | None = None,
    patch_code: bool = False,
    skip_name_patches: bool = False,
) -> int:
    """Emit a Luma/Azahar LayeredFS drop (same format as Makein/NLPPATCH releases)."""
    title_root = out_dir / TITLE_ID
    title_romfs = title_root / "romfs"
    title_dir = title_romfs / "script" / "bin"
    count = 0
    for pack in PACKS:
        src_dir = dbin_root / pack
        if not src_dir.is_dir():
            raise PatchError(f"missing packed scripts: {src_dir}")
        dest = title_dir / pack
        dest.mkdir(parents=True, exist_ok=True)
        files = sorted(src_dir.glob("*.dbin2"))
        for src in files:
            shutil.copy2(src, dest / src.name)
            count += 1
        print(f"[layeredfs] {pack}: {len(files)} file(s)")

    if resident_src and resident_src.is_file():
        dest_trb = (
            title_romfs
            / "SystemData"
            / "TextResource"
            / "textresource_resident_jpn.trb"
        )
        dest_trb.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resident_src, dest_trb)
        print(f"[layeredfs] resident TRB <- {resident_src}")

    if img_bin and img_bin.is_file():
        title_romfs.mkdir(parents=True, exist_ok=True)
        dest_img = title_romfs / "img.bin"
        print(f"[layeredfs] copying img.bin ({img_bin.stat().st_size:,} bytes) ...")
        shutil.copy2(img_bin, dest_img)

    if patch_code:
        src = code_bin_src if code_bin_src and code_bin_src.is_file() else DEFAULT_CODE_BIN
        if not src.is_file():
            raise PatchError(
                f"--patch-code needs a vanilla code.bin (not found: {src}). "
                "Pass --code-bin PATH."
            )
        from patch_code import write_patched_code_bin

        dest_code = title_root / "code.bin"
        write_patched_code_bin(src, dest_code, force=True)
        print(f"[layeredfs] code.bin (single-pane name draw)")

    # Patch names in the overlay tree (dbin tokens → plain Takane/Rinko/Nene, etc.)
    apply_name_patches(title_romfs, skip=skip_name_patches)

    readme = out_dir / "LAYEREDFS_README.txt"
    readme.write_text(
        "\n".join(
            [
                "New Love Plus+ English LayeredFS overlay",
                f"Title ID: {TITLE_ID}",
                "",
                "Luma (3DS): copy the title folder to SD:/luma/titles/",
                "  and enable 'Enable game patching' in Luma settings.",
                "",
                "Azahar / Citra: copy the title folder to:",
                "  %AppData%/Azahar/load/mods/",
                "  (or Citra's load/mods equivalent)",
                "",
                "Contains:",
                "  - romfs/script/bin/{NLP_01,NLP_02,script}/*.dbin2",
                "  - romfs/SystemData/.../textresource_resident_jpn.trb (heroine names)",
                "  - romfs/img.bin (when built with --with-images / --name-img)",
                "  - code.bin (when built with --patch-code; single-pane name UI)",
                "",
                "Heroine dialog tokens (▲高嶺＊＊▲ etc.) are rewritten to plain",
                "English names at build time — see src/patch_names.py.",
                "Optional ExeFS name-draw patch — see src/patch_code.py.",
                "",
                "This matches the install style used by Makein/NLPPGit releases",
                "and LovePlusProject/NLPPATCH — no CIA rebuild required.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"[layeredfs] wrote {count} scripts under {title_root}")
    return count


def _resolve_source_img_bin(args: argparse.Namespace) -> Path:
    """Vanilla img.bin used as the pack base (never overwrite the dump in-place)."""
    explicit = Path(args.img_bin).resolve() if args.img_bin else DEFAULT_IMG_BIN
    if explicit.is_file():
        return explicit
    if DEFAULT_IMG_BIN.is_file():
        return DEFAULT_IMG_BIN.resolve()
    if _LEGACY_IMG_BIN.is_file():
        return _LEGACY_IMG_BIN.resolve()
    raise PatchError(
        "No source img.bin for UI packing. Pass --img-bin path/to/romfs/img.bin "
        f"(tried {explicit})."
    )


def pack_ui_images(args: argparse.Namespace, work: Path) -> Path:
    """Pack assets/images PNGs into cache/new_img.bin (same-size BCLIM only)."""
    from pack_images import PackError, pack_images

    images = Path(args.images).resolve()
    out_img = (
        Path(args.packed_img).resolve()
        if args.packed_img
        else DEFAULT_PACKED_IMG
    )
    out_img.parent.mkdir(parents=True, exist_ok=True)
    img_work = work / "img_work"

    # Default: reuse cache when present. --repack-images forces a rebuild.
    reuse = (not args.repack_images) and (
        args.reuse_packed_img or out_img.is_file()
    )
    if reuse and out_img.is_file():
        print(f"[images] reusing packed img.bin: {out_img}")
        print("         (delete it or pass --repack-images to rebuild from assets/images)")
        return out_img

    src_img = _resolve_source_img_bin(args)
    workers = getattr(args, "image_workers", None)
    fine_tune = bool(getattr(args, "image_fine_tune", False))
    print(f"[images] packing UI PNGs from {images}")
    print(f"         base img.bin: {src_img}")
    print(f"         output:       {out_img}")
    print(
        "         (same-size BCLIM; exact-zlib"
        f"{' + fine-tune' if fine_tune else '; fine-tune off — use --image-fine-tune to enable'})"
    )
    try:
        pack_images(
            images,
            src_img,
            img_work,
            out_img,
            only_keys=None,
            workers=workers,
            fine_tune=fine_tune,
        )
    except PackError as exc:
        raise PatchError(f"image packing failed: {exc}") from exc
    return out_img


def parse_title_version(cia: Path) -> int | None:
    info = _ctrtool_info(cia)
    m = re.search(r"TitleVersion:\s*.*?\((\d+)\)", info)
    if m:
        return int(m.group(1))
    m = re.search(r"Version:\s*(\d+)", info)
    return int(m.group(1)) if m else None


def sha1_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_cia_sha1(
    cia: Path,
    expected: str | None = None,
    *,
    allowed: frozenset[str] | set[str] | None = None,
) -> str:
    """Verify CIA SHA-1 against one digest or the known-dump allowlist."""
    if expected:
        allowed_set = {expected.lower()}
    else:
        allowed_set = {h.lower() for h in (allowed or ALLOWED_CIA_SHA1)}

    print(f"[hash] computing SHA-1 of {cia.name} ...")
    digest = sha1_file(cia)
    print(f"[hash] got:      {digest}")
    if len(allowed_set) == 1:
        only = next(iter(allowed_set))
        print(f"[hash] expected: {only}")
    else:
        print(f"[hash] allowed:  {len(allowed_set)} known encrypted dumps")

    if digest.lower() not in allowed_set:
        listed = "\n".join(f"    {h}" for h in sorted(allowed_set))
        raise PatchError(
            "Dump SHA-1 mismatch - refusing to patch.\n"
            f"  file:     {cia}\n"
            f"  got:      {digest}\n"
            f"  allowed:\n{listed}\n"
            "Use a matching New Love Plus+ dump (.cia / .3ds / .cci). "
            "Many decrypted CIAs will not match these hashes."
        )
    print("[hash] OK")
    return digest


def cmd_patch(args: argparse.Namespace) -> int:
    _require_tools()

    rom_in = Path(args.cia).resolve()
    if not rom_in.is_file():
        raise PatchError(f"ROM not found: {rom_in}")
    kind = detect_rom_kind(rom_in)

    dbin_root = Path(args.dbin).resolve()
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    out_cia = Path(args.out).resolve()
    out_cia.parent.mkdir(parents=True, exist_ok=True)

    print("=== NLPP English Patcher (→ CIA) ===")
    print(f"input:  {rom_in} ({kind})")
    print(f"dbin:   {dbin_root}")
    print(f"work:   {work}")
    print(f"output: {out_cia}")
    print()

    # Verify dump identity before any decrypt / image / RomFS work.
    if args.skip_hash:
        print("[hash] skipped (--skip-hash)")
    else:
        verify_cia_sha1(rom_in, expected=args.expect_sha1)
    print()

    packed_img: Path | None = None
    if args.with_images and not args.no_images:
        packed_img = pack_ui_images(args, work)
    elif args.no_images:
        print("[images] skipped (--no-images)")

    romfs_hint = Path(args.romfs).resolve() if args.romfs else (
        DEFAULT_ROMFS if DEFAULT_ROMFS.is_dir() else None
    )
    resident_src = _resolve_resident_trb(romfs_hint)

    # Optional: ship img.bin in LayeredFS just to patch the name table (no UI pack).
    layered_img = packed_img
    if layered_img is None and args.name_img:
        img_src = Path(args.img_bin).resolve()
        if img_src.is_file():
            layered_img = img_src
        else:
            print(f"[names] --name-img requested but img.bin missing: {img_src}")

    code_bin_src = Path(args.code_bin).resolve() if args.code_bin else DEFAULT_CODE_BIN

    if args.layeredfs_only and not args.layeredfs_out:
        args.layeredfs_out = str(ROOT / "out" / "layeredfs")

    if args.layeredfs_out:
        write_layeredfs(
            Path(args.layeredfs_out).resolve(),
            dbin_root,
            layered_img,
            resident_src=resident_src,
            code_bin_src=code_bin_src,
            patch_code=args.patch_code,
            skip_name_patches=args.skip_name_patches,
        )

    if args.layeredfs_only:
        print()
        print("Done (LayeredFS only). No CIA rebuilt.")
        return 0

    # 1–2) Decrypt (CIA or encrypted .3ds) → game CXI (+ optional manual)
    title_ver: int | None = None
    if args.cxi and Path(args.cxi).is_file():
        cxi = Path(args.cxi).resolve()
        manual = Path(args.manual).resolve() if args.manual else None
        title_ver = parse_title_version(rom_in)
        print(f"[extract] using provided CXI: {cxi}")
    elif (
        kind == "cia"
        and DEFAULT_EXTRACTED.is_dir()
        and any(DEFAULT_EXTRACTED.glob("content.0000.*"))
    ):
        c0 = sorted(DEFAULT_EXTRACTED.glob("content.0000.*"))[0]
        c1s = sorted(DEFAULT_EXTRACTED.glob("content.0001.*"))
        cxi, manual = c0, (c1s[0] if c1s else None)
        title_ver = parse_title_version(rom_in)
        print(f"[extract] using sibling extracted/: {cxi.name}")
    else:
        cxi, manual, title_ver = prepare_cxi_from_rom(
            rom_in,
            work,
            kind=kind,
            assume_decrypted=args.assume_decrypted,
            force_decrypt=args.force_decrypt,
        )

    parts = split_cxi(cxi, work / "ncch_parts")

    # 3) RomFS tree + inject
    reuse = Path(args.romfs).resolve() if args.romfs else (
        DEFAULT_ROMFS if DEFAULT_ROMFS.is_dir() else None
    )
    # Prefer linking/copying an existing RomFS tree; fall back to extracting romfs.bin.
    if args.in_place_romfs and reuse:
        romfs_dir = reuse
        print(f"[romfs] in-place inject into {romfs_dir}")
    else:
        romfs_work = work / "romfs"
        if reuse and (reuse / "script" / "bin" / "script").is_dir():
            if args.link_romfs:
                if romfs_work.exists():
                    # Only remove empty junction/dir we created previously
                    try:
                        romfs_work.rmdir()
                    except OSError:
                        shutil.rmtree(romfs_work)
                print(f"[romfs] junction -> {reuse}")
                _run(["cmd", "/c", "mklink", "/J", str(romfs_work), str(reuse)])
                romfs_dir = romfs_work
            elif (romfs_work / "script" / "bin" / "script").is_dir():
                print(f"[romfs] reusing work tree: {romfs_work}")
                romfs_dir = romfs_work
            else:
                print(f"[romfs] copying base tree from {reuse} (slow once) ...")
                if romfs_work.exists():
                    shutil.rmtree(romfs_work)
                shutil.copytree(reuse, romfs_work)
                romfs_dir = romfs_work
        else:
            romfs_dir = ensure_romfs_dir(parts["romfs"], romfs_work, reuse=None)

    injected = inject_dbin2(romfs_dir, dbin_root)
    print(f"[inject] total .dbin2 files: {injected}")

    if packed_img is not None:
        dest_img = romfs_dir / "img.bin"
        print(f"[inject] img.bin -> {dest_img}")
        shutil.copy2(packed_img, dest_img)

    if args.romfs_overlay:
        apply_romfs_overlay(romfs_dir, Path(args.romfs_overlay))

    # Heroine names: plain English in dialog scripts + UI name tables.
    apply_name_patches(romfs_dir, skip=args.skip_name_patches)

    if args.inject_code and args.patch_code:
        raise PatchError("use either --inject-code or --patch-code, not both")
    if args.inject_code:
        parts = dict(parts)
        parts["exefs"] = inject_exefs_code(parts["exefs"], work, Path(args.inject_code))
    elif args.patch_code:
        parts = dict(parts)
        parts["exefs"] = patch_exefs_code(parts["exefs"], work)

    # 4) Rebuild containers
    new_romfs = work / "romfs_patched.bin"
    rebuild_romfs(romfs_dir, new_romfs)

    patched_cxi = work / "patched.cxi"
    rebuild_cxi(parts, new_romfs, patched_cxi)

    rebuild_cia(patched_cxi, manual, out_cia, title_ver)

    if not args.keep_work:
        cleanup_patch_artifacts(
            work,
            out_cia=out_cia,
            packed_img=packed_img,
        )

    print()
    print("=== Done ===")
    print(f"Patched CIA: {out_cia}")
    print(f"Size:        {out_cia.stat().st_size:,} bytes")
    if packed_img is not None and packed_img.is_file():
        print(f"Packed UI:   {packed_img}")
    if args.layeredfs_out:
        layered_path = Path(args.layeredfs_out).resolve()
        if layered_path.is_dir():
            print(f"LayeredFS:   {layered_path}")
    print()
    print("Notes:")
    print("  - Output is a decrypted CIA (works with FBI on CFW, Azahar, Citra).")
    print("  - Retail NCCH re-encryption is not done here; use Decrypt9WIP")
    print("    'CIA Encryptor (NCCH)' on a 3DS if you specifically need that.")
    if not args.keep_work:
        print("  - Scratch work dir was removed (pass --keep-work to retain).")
    if packed_img is None:
        print("  - UI images were not packed (pass --with-images).")
    else:
        print("  - UI images were packed from assets/images into romfs/img.bin.")
        print("    Some BCLIMs expand in size (png2bclim); that is expected.")
    return 0


def _is_reparse_dir(path: Path) -> bool:
    """True for symlinks / Windows directory junctions (Py3.10-safe)."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            return bool(is_junction())
        except OSError:
            return False
    if sys.platform == "win32" and path.is_dir():
        try:
            import ctypes

            GetFileAttributesW = ctypes.windll.kernel32.GetFileAttributesW
            GetFileAttributesW.argtypes = (ctypes.c_wchar_p,)
            GetFileAttributesW.restype = ctypes.c_uint32
            attrs = GetFileAttributesW(str(path))
            FILE_ATTRIBUTE_REPARSE_POINT = 0x400
            INVALID = 0xFFFFFFFF
            return attrs != INVALID and bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
        except Exception:
            return False
    return False


def cleanup_patch_artifacts(
    work: Path,
    *,
    out_cia: Path,
    packed_img: Path | None = None,
) -> None:
    """Remove patch scratch after a successful CIA write; keep the finished CIA."""
    work = work.resolve()
    out_cia = out_cia.resolve()
    keep: set[Path] = {out_cia}
    if packed_img is not None:
        try:
            keep.add(packed_img.resolve())
        except OSError:
            pass

    def _safe_rm_tree(path: Path) -> None:
        if not path.exists():
            return
        try:
            if path.resolve() == out_cia.parent.resolve() and path.name == "out":
                print(f"[cleanup] refusing to delete entire out/: {path}")
                return
        except OSError:
            pass
        # Drop junctions/symlinks first so we do not recurse into external RomFS trees.
        romfs = path / "romfs"
        if romfs.exists():
            try:
                if _is_reparse_dir(romfs):
                    # Junction/symlink: unlink/rmdir removes the link, not the target.
                    try:
                        romfs.unlink()
                    except OSError:
                        romfs.rmdir()
            except OSError:
                pass
        print(f"[cleanup] removing {path} ...")
        shutil.rmtree(path, ignore_errors=True)

    if not work.is_dir():
        return
    try:
        if out_cia == work or out_cia.parent == work:
            # CIA lives inside work — only purge sibling scratch, not the CIA.
            for child in list(work.iterdir()):
                if child.resolve() in keep:
                    continue
                if child.is_dir():
                    _safe_rm_tree(child)
                else:
                    try:
                        child.unlink()
                    except OSError as exc:
                        print(f"[cleanup] warning: {child.name}: {exc}")
        else:
            _safe_rm_tree(work)
    except OSError as exc:
        print(f"[cleanup] warning: work dir: {exc}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Decrypt/patch New Love Plus+ from .cia or .3ds/.cci and rebuild a "
            "decrypted English CIA."
        ),
    )
    p.add_argument(
        "--cia",
        required=True,
        help="Path to input .cia / .3ds / .cci (encrypted or decrypted)",
    )
    p.add_argument(
        "--out",
        default=str(ROOT / "out" / "NewLovePlusPlus-EN.cia"),
        help="Output patched CIA path",
    )
    p.add_argument(
        "--dbin",
        default=str(DEFAULT_DBIN),
        help="Folder with NLP_01/NLP_02/script *.dbin2 (default: rebuild_dbin2/)",
    )
    p.add_argument(
        "--work",
        default=str(ROOT / "out" / "cia_work"),
        help="Scratch directory",
    )
    p.add_argument("--cxi", help="Optional pre-extracted main content/CXI")
    p.add_argument("--manual", help="Optional content.0001 manual CFA")
    p.add_argument(
        "--romfs",
        help="Optional already-extracted RomFS directory to copy from",
    )
    p.add_argument(
        "--in-place-romfs",
        action="store_true",
        help="Inject into --romfs directly (mutates that tree)",
    )
    p.add_argument(
        "--link-romfs",
        action="store_true",
        help="Junction work/romfs to --romfs instead of copying (mutates linked tree)",
    )
    p.add_argument(
        "--assume-decrypted",
        action="store_true",
        help="Skip encryption detection / decrypt step",
    )
    p.add_argument(
        "--force-decrypt",
        action="store_true",
        help="Decrypt even if a sibling *-decrypted.cia already exists",
    )
    p.add_argument(
        "--layeredfs-out",
        default=None,
        help="Also write a Luma/Azahar LayeredFS overlay here (off by default)",
    )
    p.add_argument(
        "--layeredfs-only",
        action="store_true",
        help="Only write LayeredFS overlay (skip CIA rebuild)",
    )
    p.add_argument(
        "--keep-work",
        action="store_true",
        help="Keep scratch work dir after a successful CIA build "
        "(default: delete out/cia_work and leave the finished CIA)",
    )
    p.add_argument(
        "--with-images",
        action="store_true",
        default=True,
        help="Pack assets/images into cache/new_img.bin and inject (default: on)",
    )
    p.add_argument(
        "--no-images",
        action="store_true",
        help="Skip UI packing / img.bin inject (scripts-only CIA)",
    )
    p.add_argument(
        "--images",
        default=str(DEFAULT_IMAGES),
        help="UI PNG root (default: assets/images) — drop your translated PNGs here",
    )
    p.add_argument(
        "--img-bin",
        default=str(DEFAULT_IMG_BIN),
        help="Vanilla source romfs/img.bin to pack from (default: sibling extracted/)",
    )
    p.add_argument(
        "--packed-img",
        default=str(DEFAULT_PACKED_IMG),
        help="Output/cache path for packed img.bin (default: cache/new_img.bin)",
    )
    p.add_argument(
        "--reuse-packed-img",
        action="store_true",
        help="Reuse --packed-img if present (default behavior when the file exists)",
    )
    p.add_argument(
        "--repack-images",
        action="store_true",
        help="Force rebuild cache/new_img.bin from assets/images even if cache exists",
    )
    p.add_argument(
        "--image-workers",
        type=int,
        default=None,
        help="Parallel PNG→BCLIM workers for UI packing (default: CPU count, max 32)",
    )
    p.add_argument(
        "--image-fine-tune",
        action="store_true",
        help="Opt-in per-byte zopfli fine-tune during UI pack (very slow; off by default)",
    )
    p.add_argument(
        "--expect-sha1",
        default=None,
        help=(
            "Require this exact dump SHA-1. "
            "Default: accept any hash in ALLOWED_DUMP_SHA1 "
            f"(primary: {EXPECTED_CIA_SHA1})."
        ),
    )
    p.add_argument(
        "--skip-hash",
        action="store_true",
        help="Skip the input dump SHA-1 check (not recommended)",
    )
    p.add_argument(
        "--skip-name-patches",
        action="store_true",
        help="Skip English heroine-name patches (▲高嶺＊＊▲ → Takane, resident/img tables)",
    )
    p.add_argument(
        "--name-img",
        action="store_true",
        help="Include img.bin in LayeredFS and patch its name table (even without --with-images)",
    )
    p.add_argument(
        "--patch-code",
        action="store_true",
        help="Apply single-pane English name-draw patch to ExeFS code.bin (see src/patch_code.py)",
    )
    p.add_argument(
        "--inject-code",
        help="Replace ExeFS .code with this binary (decompressed ~8.1MB Azahar/LayeredFS "
        "code.bin is BLZ-compressed to the stock slot; already-compressed also OK)",
    )
    p.add_argument(
        "--romfs-overlay",
        help="Copy files from this directory onto RomFS after script/img inject "
        "(e.g. Azahar mod SystemData/TextResource)",
    )
    p.add_argument(
        "--code-bin",
        default=str(DEFAULT_CODE_BIN),
        help="Vanilla code.bin for LayeredFS --patch-code (default: sibling extracted/exefs/code.bin)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return cmd_patch(args)
    except PatchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
