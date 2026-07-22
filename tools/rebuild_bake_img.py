#!/usr/bin/env python3
"""Rebuild release/bake_img.bin from vanilla + PNG pack + TRB + deploy chrome + SMS.

Self-contained (no Azahar required):

  vanilla img.bin
    → pack_images (assets/images)           # long; also writes cache/new_img.bin
    → rebuild textresource_jpn.trb from assets/textresource/translations.json
    → ordered deploy_*_en.py chrome (+ day-counter resident TRB)
    → SMS maildic
    → sync TRBs into release/romfs_overlay
    → release/bake_img.bin

Usage:
  python tools/rebuild_bake_img.py
  python tools/rebuild_bake_img.py --rom game.cia|.3ds|.cci   # extract vanilla from ROM
  python tools/rebuild_bake_img.py --skip-pack          # keep bake; re-run TRB/deploys/SMS
  python tools/rebuild_bake_img.py --reseed-from-pack   # force bake <- cache/new_img.bin
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from extract_vanilla_from_rom import resolve_vanilla_img  # noqa: E402
from nlpp_paths import (  # noqa: E402
    ASSETS_TEXTRESOURCE,
    BAKE_IMG,
    CACHE,
    CACHE_NEW_IMG,
    OVERLAY_TRB_DIR,
    RELEASE,
    TEXTRESOURCE,
    TRANSLATIONS_JSON,
    find_vanilla_main_trb,
    find_vanilla_resident_trb,
    require_translations_json,
)
from patch_cia import PatchError  # noqa: E402

# Shared-ARC-safe order (canonical last-writers for 5245/5247/5380/5575/5253).
DEPLOY_SCRIPTS: list[str] = [
    "deploy_msel_options_en.py",
    "deploy_msel_opt_plates_en.py",
    "deploy_msel_menus_en.py",
    "deploy_confirm_btn_en.py",
    "deploy_display_settings_en.py",
    # sound_settings is a subset of display_settings — skip by default
    "deploy_profile_en.py",
    "deploy_card_flist_en.py",
    "deploy_status_stats_en.py",
    "deploy_myroom_main_en.py",
    "deploy_mail_home_en.py",
    "deploy_mydata_en.py",
    "deploy_todo_en.py",
    "deploy_todo_hist_en.py",
    "deploy_schedule_header_en.py",
    "deploy_day_counter_en.py",
    # Hub main-menu rows (Title.arc) + boot CESA — not covered by NCommonMSel deploys.
    "deploy_title_main_menu_en.py",
    "deploy_cesa_en.py",
]


def run(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    print("\n==>", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=str(ROOT), env=env)


def sync_trb_overlay() -> None:
    """Copy durable TRBs into the RomFS overlay used by patch_cia."""
    OVERLAY_TRB_DIR.mkdir(parents=True, exist_ok=True)
    TEXTRESOURCE.mkdir(parents=True, exist_ok=True)
    names = (
        "textresource_jpn.trb",
        "textresource_resident_jpn.trb",
        "textresource_config.trb",
        "translations.json",
    )
    for name in names:
        src = TEXTRESOURCE / name
        if not src.is_file():
            continue
        dest = OVERLAY_TRB_DIR / name
        shutil.copy2(src, dest)
        print(f"[trb] overlay <- {src.name}", flush=True)


def rebuild_main_trb() -> None:
    """Regenerate textresource_jpn.trb (+ config) from translations.json."""
    translations = require_translations_json()
    vanilla_trb = find_vanilla_main_trb()
    if vanilla_trb is None:
        raise SystemExit(
            "vanilla textresource_jpn.trb not found.\n"
            "Pass --rom path\\to\\game.cia|.3ds|.cci, or set NLPP_VANILLA_TRB."
        )

    TEXTRESOURCE.mkdir(parents=True, exist_ok=True)
    ASSETS_TEXTRESOURCE.mkdir(parents=True, exist_ok=True)

    # Keep commit-worthy source and release working copy in sync.
    if translations.resolve() != TRANSLATIONS_JSON.resolve():
        shutil.copy2(translations, TRANSLATIONS_JSON)
        translations = TRANSLATIONS_JSON
    shutil.copy2(translations, TEXTRESOURCE / "translations.json")

    out_trb = TEXTRESOURCE / "textresource_jpn.trb"
    out_cfg = TEXTRESOURCE / "textresource_config.trb"
    run(
        [
            sys.executable,
            str(ROOT / "src" / "patch_textresource.py"),
            "rebuild",
            "--trb",
            str(vanilla_trb),
            "--translations",
            str(translations),
            "--out",
            str(out_trb),
            "--config-out",
            str(out_cfg),
        ]
    )
    if not out_trb.is_file():
        raise SystemExit(f"TRB rebuild did not write {out_trb}")
    print(f"[trb] rebuilt main TRB -> {out_trb}", flush=True)

    # Resident TRB is not rebuilt from translations.json; seed virgin bytes for
    # deploy_day_counter_en.py (日目 → Day) when release/ lacks it.
    resident_out = TEXTRESOURCE / "textresource_resident_jpn.trb"
    if not resident_out.is_file():
        vanilla_resident = find_vanilla_resident_trb()
        if vanilla_resident is None:
            raise SystemExit(
                "vanilla textresource_resident_jpn.trb not found.\n"
                "Pass --rom path\\to\\game.cia|.3ds|.cci, or set NLPP_VANILLA_RESIDENT_TRB."
            )
        shutil.copy2(vanilla_resident, resident_out)
        print(f"[trb] seeded resident TRB -> {resident_out}", flush=True)


def pack_ui(vanilla: Path, *, workers: int | None, fine_tune: bool) -> Path:
    """PNG pack → cache/new_img.bin (optional intermediate), then copy to bake."""
    CACHE.mkdir(parents=True, exist_ok=True)
    RELEASE.mkdir(parents=True, exist_ok=True)
    work = ROOT / "out" / "rebuild_bake_img_work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(ROOT / "src" / "pack_images.py"),
        "--img-bin",
        str(vanilla),
        "--images",
        str(ROOT / "assets" / "images"),
        "--out",
        str(CACHE_NEW_IMG),
        "--work",
        str(work),
    ]
    if workers is not None:
        cmd.extend(["--workers", str(workers)])
    if fine_tune:
        cmd.append("--fine-tune")
    run(cmd)
    if not CACHE_NEW_IMG.is_file():
        raise SystemExit(f"pack_images did not write {CACHE_NEW_IMG}")
    shutil.copy2(CACHE_NEW_IMG, BAKE_IMG)
    print(f"[bake] seeded from PNG pack -> {BAKE_IMG}", flush=True)
    return BAKE_IMG


def seed_vanilla_bak(vanilla: Path, bake: Path) -> None:
    """Sidecar bak used by several deploys for virgin ARC bytes."""
    bak = bake.with_suffix(".bin.bak_pre_msel5245")
    shutil.copy2(vanilla, bak)
    print(f"[bake] vanilla bak -> {bak}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--vanilla",
        type=Path,
        default=None,
        help="vanilla romfs/img.bin (default: sibling extracted/ or NLPP_VANILLA_IMG)",
    )
    ap.add_argument(
        "--rom",
        type=Path,
        default=None,
        help="extract vanilla img.bin + TRBs from this .cia / .3ds / .cci when needed",
    )
    ap.add_argument(
        "--skip-pack",
        action="store_true",
        help="skip PNG pack; keep existing bake, or seed from cache/new_img.bin if bake missing",
    )
    ap.add_argument(
        "--reseed-from-pack",
        action="store_true",
        help="force-copy cache/new_img.bin over bake before deploys (destructive)",
    )
    ap.add_argument(
        "--skip-deploys",
        action="store_true",
        help="skip deploy_* chrome (still rebuilds main TRB unless --skip-trb)",
    )
    ap.add_argument(
        "--skip-sms",
        action="store_true",
        help="skip SMS maildic deploy",
    )
    ap.add_argument(
        "--skip-trb",
        action="store_true",
        help="skip regenerating textresource_jpn.trb from translations.json",
    )
    ap.add_argument(
        "--include-sound-settings",
        action="store_true",
        help="also run deploy_sound_settings_en.py (subset; usually redundant)",
    )
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument(
        "--fine-tune",
        action="store_true",
        help="opt-in pack_images fine-tune (very slow)",
    )
    ap.add_argument(
        "--also-azahar",
        action="store_true",
        help="mirror deploy splices into Azahar LayeredFS when present",
    )
    args = ap.parse_args(argv)

    RELEASE.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)

    if args.vanilla is not None:
        vanilla = args.vanilla.resolve()
    else:
        try:
            vanilla = resolve_vanilla_img(rom=args.rom.resolve() if args.rom else None)
        except (FileNotFoundError, PatchError, OSError) as exc:
            raise SystemExit(str(exc)) from exc
    if not vanilla.is_file():
        raise SystemExit(f"vanilla img.bin missing: {vanilla}")

    # If we extracted from --rom, also point TRB lookup at the same cache tree.
    env = os.environ.copy()
    env["NLPP_VANILLA_IMG"] = str(vanilla)
    env["NLPP_DEPLOY_IMG"] = str(BAKE_IMG)
    if find_vanilla_main_trb() is None and args.rom is not None:
        raise SystemExit(
            "vanilla textresource_jpn.trb missing after ROM extract. "
            "Re-run with --rom, or set NLPP_VANILLA_TRB."
        )
    trb = find_vanilla_main_trb()
    if trb is not None:
        env["NLPP_VANILLA_TRB"] = str(trb)
    if args.also_azahar:
        env["NLPP_ALSO_AZAHAR"] = "1"
    else:
        env.pop("NLPP_ALSO_AZAHAR", None)

    print(f"[rebuild] vanilla: {vanilla}", flush=True)
    print(f"[rebuild] bake:    {BAKE_IMG}", flush=True)

    if args.reseed_from_pack:
        if not CACHE_NEW_IMG.is_file():
            raise SystemExit(f"--reseed-from-pack needs {CACHE_NEW_IMG}")
        shutil.copy2(CACHE_NEW_IMG, BAKE_IMG)
        print(f"[bake] reseeded from PNG pack -> {BAKE_IMG}", flush=True)
    elif args.skip_pack:
        if BAKE_IMG.is_file():
            print(f"[bake] keeping existing gold bake: {BAKE_IMG}", flush=True)
        elif CACHE_NEW_IMG.is_file():
            shutil.copy2(CACHE_NEW_IMG, BAKE_IMG)
            print(f"[bake] seeded from PNG pack (bake was missing) -> {BAKE_IMG}", flush=True)
        else:
            raise SystemExit(
                "no bake and no cache/new_img.bin — run without --skip-pack "
                "or provide release/bake_img.bin"
            )
    else:
        print(
            "[rebuild] PNG pack starting (often ~16 hours). "
            "Progress lines mean it is still working.",
            flush=True,
        )
        pack_ui(vanilla, workers=args.workers, fine_tune=args.fine_tune)

    seed_vanilla_bak(vanilla, BAKE_IMG)

    if not args.skip_trb:
        rebuild_main_trb()
    sync_trb_overlay()

    if not args.skip_deploys:
        scripts = list(DEPLOY_SCRIPTS)
        if args.include_sound_settings:
            idx = scripts.index("deploy_display_settings_en.py") + 1
            scripts.insert(idx, "deploy_sound_settings_en.py")
        for name in scripts:
            script = ROOT / "tools" / name
            if not script.is_file():
                raise SystemExit(f"missing deploy script: {script}")
            run([sys.executable, str(script)], env=env)

    if not args.skip_sms:
        run(
            [
                sys.executable,
                str(ROOT / "tools" / "deploy_sms_maildic_en.py"),
                "--img",
                str(BAKE_IMG),
                "--no-backup",
            ],
            env=env,
        )

    sync_trb_overlay()
    if not BAKE_IMG.is_file():
        raise SystemExit(f"bake missing after rebuild: {BAKE_IMG}")
    main_trb = TEXTRESOURCE / "textresource_jpn.trb"
    if not args.skip_trb and not main_trb.is_file():
        raise SystemExit(f"main TRB missing after rebuild: {main_trb}")
    print("\n[rebuild] OK", flush=True)
    print(f"  gold bake:     {BAKE_IMG}", flush=True)
    print(f"  PNG optional:  {CACHE_NEW_IMG}", flush=True)
    print(f"  main TRB:      {main_trb}", flush=True)
    print(f"  TRB overlay:   {OVERLAY_TRB_DIR}", flush=True)
    print("Drop a CIA on the bat to build the EN CIA.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"error: step failed with exit {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode) from exc
    except KeyboardInterrupt:
        print("aborted", file=sys.stderr)
        raise SystemExit(130) from None
