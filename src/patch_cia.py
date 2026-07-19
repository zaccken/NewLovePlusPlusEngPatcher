#!/usr/bin/env python3
"""One-click New Love Plus+ CIA English patcher.

Pipeline:
  encrypted CIA -> decrypt -> extract CXI/RomFS -> inject EN .dbin2
  -> rebuild RomFS/CXI/CIA (decrypted, CFW/emulator ready)

NLPPGit (https://github.com/Makein/NLPPGit) is translation assets only.
Packing tools come from LovePlusProject (nlpp-tools / NLPTextTool) plus
ctrtool / makerom / 3dstool / Batch CIA Decryptor.
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

DEFAULT_DBIN = ROOT.parent / "rebuild_dbin2"
DEFAULT_EXTRACTED = ROOT.parent / "New Love Plus Plus" / "extracted"
DEFAULT_MAIN_CXI = Path(r"C:\Users\Zepse\nlpp_work\main.cxi")
DEFAULT_ROMFS = Path(r"C:\Users\Zepse\nlpp_work\romfs")
DEFAULT_IMG_BIN = Path(r"C:\Users\Zepse\nlpp_work\romfs\img.bin")
DEFAULT_IMAGES = ROOT / "assets" / "images"
TITLE_ID = "00040000000F4E00"
PACKS = ("NLP_01", "NLP_02", "script")

# Expected SHA-1 of the retail/encrypted New Love Plus+ CIA dump this patcher targets.
EXPECTED_CIA_SHA1 = "a9fbd2e6d790b6cb6194f7820e1a71f597160f2b"


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


def is_encrypted_cia(cia: Path) -> bool:
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


def decrypt_cia(cia: Path, work: Path) -> Path:
    """Decrypt an encrypted CIA into work/, return path to *-decrypted.cia."""
    out_name = f"{cia.stem}-decrypted.cia"
    out_path = work / out_name
    if out_path.is_file():
        print(f"[decrypt] using existing {out_path.name}")
        return out_path

    print(f"[decrypt] decrypting {cia.name} ...")
    bin_dir = work / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("ctrtool.exe", "makerom.exe", "decrypt.exe", "seeddb.bin"):
        shutil.copy2(CIA_TOOLS / name, bin_dir / name)

    work_cia = work / cia.name
    if work_cia.resolve() != cia.resolve():
        shutil.copy2(cia, work_cia)

    # decrypt.exe writes tmp.*.ncch into bin\
    _run(["cmd", "/c", f'echo.| bin\\decrypt.exe "{work_cia.name}"'], cwd=work)

    ncchs = sorted(bin_dir.glob("tmp.*.ncch"))
    if not ncchs:
        # Some builds rename oddly; accept any *.ncch
        ncchs = sorted(p for p in bin_dir.glob("*.ncch") if p.stat().st_size > 0)
    if not ncchs:
        raise PatchError("decrypt.exe produced no NCCH partitions")

    # Normalize names like the Batch Decryptor
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


def write_layeredfs(out_dir: Path, dbin_root: Path, img_bin: Path | None = None) -> int:
    """Emit a Luma/Azahar LayeredFS drop (same format as Makein/NLPPATCH releases)."""
    title_romfs = out_dir / TITLE_ID / "romfs"
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
    if img_bin and img_bin.is_file():
        title_romfs.mkdir(parents=True, exist_ok=True)
        dest_img = title_romfs / "img.bin"
        print(f"[layeredfs] copying img.bin ({img_bin.stat().st_size:,} bytes) ...")
        shutil.copy2(img_bin, dest_img)
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
                "  - romfs/img.bin (when built with --with-images)",
                "",
                "This matches the install style used by Makein/NLPPGit releases",
                "and LovePlusProject/NLPPATCH — no CIA rebuild required.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"[layeredfs] wrote {count} scripts under {out_dir / TITLE_ID}")
    return count


def pack_ui_images(args: argparse.Namespace, work: Path) -> Path:
    """Pack assets/images PNGs into a new img.bin; return its path."""
    from pack_images import PackError, pack_images

    images = Path(args.images).resolve()
    src_img = Path(args.img_bin).resolve()
    out_img = Path(args.packed_img).resolve() if args.packed_img else (work / "new_img.bin")
    img_work = work / "img_work"

    if args.reuse_packed_img and out_img.is_file():
        print(f"[images] reusing packed img.bin: {out_img}")
        return out_img

    print("[images] packing UI PNGs into img.bin (may take several minutes) ...")
    try:
        pack_images(images, src_img, img_work, out_img, only_keys=None)
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


def verify_cia_sha1(cia: Path, expected: str = EXPECTED_CIA_SHA1) -> str:
    print(f"[hash] computing SHA-1 of {cia.name} ...")
    digest = sha1_file(cia)
    print(f"[hash] got:      {digest}")
    print(f"[hash] expected: {expected}")
    if digest.lower() != expected.lower():
        raise PatchError(
            "CIA SHA-1 mismatch - refusing to patch.\n"
            f"  file:     {cia}\n"
            f"  got:      {digest}\n"
            f"  expected: {expected}\n"
            "Use the matching encrypted New Love Plus+ dump "
            "(decrypted CIAs will not match this hash)."
        )
    print("[hash] OK")
    return digest


def cmd_patch(args: argparse.Namespace) -> int:
    _require_tools()

    cia_in = Path(args.cia).resolve()
    if not cia_in.is_file():
        raise PatchError(f"CIA not found: {cia_in}")

    dbin_root = Path(args.dbin).resolve()
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    out_cia = Path(args.out).resolve()
    out_cia.parent.mkdir(parents=True, exist_ok=True)

    print("=== NLPP English CIA Patcher ===")
    print(f"input:  {cia_in}")
    print(f"dbin:   {dbin_root}")
    print(f"work:   {work}")
    print(f"output: {out_cia}")
    print()

    # Verify dump identity before any decrypt / image / RomFS work.
    if args.skip_hash:
        print("[hash] skipped (--skip-hash)")
    else:
        verify_cia_sha1(cia_in, args.expect_sha1)
    print()

    # 1) Decrypt if needed
    sibling_dec = ROOT.parent / "New Love Plus Plus" / "NewLovePlusPlus-decrypted.cia"
    if args.assume_decrypted or ("decrypted" in cia_in.name.lower()):
        decrypted = cia_in
        print("[decrypt] skipped (flag / filename)")
    else:
        try:
            encrypted = is_encrypted_cia(cia_in)
        except PatchError as exc:
            print(f"[decrypt] warning: {exc}; attempting decrypt")
            encrypted = True
        if encrypted and sibling_dec.is_file() and not args.force_decrypt:
            decrypted = sibling_dec
            print(f"[decrypt] reusing sibling decrypted CIA: {sibling_dec}")
        elif encrypted:
            decrypted = decrypt_cia(cia_in, work / "decrypt")
        else:
            decrypted = cia_in
            print("[decrypt] CIA already decrypted")

    packed_img: Path | None = None
    if args.with_images:
        packed_img = pack_ui_images(args, work)

    if args.layeredfs_out or args.layeredfs_only:
        write_layeredfs(Path(args.layeredfs_out).resolve(), dbin_root, packed_img)

    if args.layeredfs_only:
        print()
        print("Done (LayeredFS only). No CIA rebuilt.")
        return 0

    title_ver = parse_title_version(decrypted)

    # 2) Contents / CXI
    if args.cxi and Path(args.cxi).is_file():
        cxi = Path(args.cxi).resolve()
        manual = Path(args.manual).resolve() if args.manual else None
        print(f"[extract] using provided CXI: {cxi}")
    elif DEFAULT_EXTRACTED.is_dir() and any(DEFAULT_EXTRACTED.glob("content.0000.*")):
        c0 = sorted(DEFAULT_EXTRACTED.glob("content.0000.*"))[0]
        c1s = sorted(DEFAULT_EXTRACTED.glob("content.0001.*"))
        cxi, manual = c0, (c1s[0] if c1s else None)
        print(f"[extract] using sibling extracted/: {cxi.name}")
    else:
        cxi, manual = extract_cia_contents(decrypted, work / "contents")

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

    # 4) Rebuild containers
    new_romfs = work / "romfs_patched.bin"
    rebuild_romfs(romfs_dir, new_romfs)

    patched_cxi = work / "patched.cxi"
    rebuild_cxi(parts, new_romfs, patched_cxi)

    rebuild_cia(patched_cxi, manual, out_cia, title_ver)

    print()
    print("=== Done ===")
    print(f"Patched CIA: {out_cia}")
    print(f"Size:        {out_cia.stat().st_size:,} bytes")
    if packed_img is not None:
        print(f"Packed UI:   {packed_img}")
    print()
    print("Notes:")
    print("  - Output is a decrypted CIA (works with FBI on CFW, Azahar, Citra).")
    print("  - Retail NCCH re-encryption is not done here; use Decrypt9WIP")
    print("    'CIA Encryptor (NCCH)' on a 3DS if you specifically need that.")
    if packed_img is None:
        print("  - UI images were not packed (pass --with-images).")
    else:
        print("  - UI images were packed from assets/images into romfs/img.bin.")
        print("    Some BCLIMs expand in size (png2bclim); that is expected.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Decrypt, English-patch, and rebuild a New Love Plus+ CIA.",
    )
    p.add_argument(
        "--cia",
        required=True,
        help="Path to input .cia (encrypted or decrypted)",
    )
    p.add_argument(
        "--out",
        default=str(ROOT / "out" / "NewLovePlusPlus-EN.cia"),
        help="Output patched CIA path",
    )
    p.add_argument(
        "--dbin",
        default=str(DEFAULT_DBIN),
        help="Folder with NLP_01/NLP_02/script *.dbin2 (default: ../rebuild_dbin2)",
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
        default=str(ROOT / "out" / "layeredfs"),
        help="Also write a Luma/Azahar LayeredFS overlay here",
    )
    p.add_argument(
        "--layeredfs-only",
        action="store_true",
        help="Only write LayeredFS overlay (skip CIA rebuild)",
    )
    p.add_argument(
        "--with-images",
        action="store_true",
        help="Pack assets/images PNGs into romfs/img.bin before CIA/LayeredFS build",
    )
    p.add_argument(
        "--images",
        default=str(DEFAULT_IMAGES),
        help="UI PNG root (default: assets/images)",
    )
    p.add_argument(
        "--img-bin",
        default=str(DEFAULT_IMG_BIN),
        help="Source romfs/img.bin to patch",
    )
    p.add_argument(
        "--packed-img",
        help="Output path for packed img.bin (default: <work>/new_img.bin)",
    )
    p.add_argument(
        "--reuse-packed-img",
        action="store_true",
        help="Reuse --packed-img if it already exists (skip re-packing)",
    )
    p.add_argument(
        "--expect-sha1",
        default=EXPECTED_CIA_SHA1,
        help=f"Required SHA-1 of the input CIA (default: {EXPECTED_CIA_SHA1})",
    )
    p.add_argument(
        "--skip-hash",
        action="store_true",
        help="Skip the input CIA SHA-1 check (not recommended)",
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
