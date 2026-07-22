#!/usr/bin/env python3
"""EN boot CESA anti-piracy warning — img.bin package 90 (CESA_240X400.texi).

Opt-in during PNG pack (can soft-lock if zlib is wrong). This deploy uses the
same exact-slot compressor as patch_cesa.py against release/bake_img.bin.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from patch_cesa import patch_img_bin  # noqa: E402

from deploy_common import iter_deploy_targets, resolve_img_paths  # noqa: E402

MOD_IMG, _VANILLA = resolve_img_paths()
CESA_PNG = ROOT / "assets" / "images" / "cesa" / "CESA_240X400.png"
OUT = ROOT / "out" / "cesa_en"


def main() -> int:
    if not MOD_IMG.is_file():
        raise SystemExit(f"missing {MOD_IMG}")
    if not CESA_PNG.is_file():
        print(f"[warn] missing {CESA_PNG} — skipping CESA EN", flush=True)
        return 0

    bak = MOD_IMG.with_suffix(".bin.bak_pre_cesa")
    if not bak.is_file():
        bak.write_bytes(MOD_IMG.read_bytes())
        print("created", bak, flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    work = OUT / "work"
    work.mkdir(parents=True, exist_ok=True)

    # Patch each deploy target in place (bake first).
    for dest in iter_deploy_targets(MOD_IMG):
        print(f"[cesa] patching {dest}", flush=True)
        patch_img_bin(dest, CESA_PNG, dest, work=work / dest.stem)

    print("deployed CESA EN ->", MOD_IMG, flush=True)
    print("Rollback:", bak, flush=True)
    print("Fully quit Azahar after testing — wrong zlib used to white-boot.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
