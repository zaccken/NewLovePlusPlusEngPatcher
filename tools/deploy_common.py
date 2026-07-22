"""Shared deploy targets — primary img is bake/env; Azahar is optional mirror."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nlpp_paths import (  # noqa: E402
    AZAHAR_MOD_IMG,
    AZAHAR_MOD_TRB_DIR,
    BAKE_IMG,
    OVERLAY_TRB_DIR,
    RELEASE,
    ROMFS_OVERLAY,
    TEXTRESOURCE,
    find_vanilla_img,
    require_vanilla_img,
)

# Bundled SIL OFL font (see assets/fonts/README.md). Replaces YuGothR.ttc.
UI_FONT = ROOT / "assets" / "fonts" / "MPLUS1p-Regular.ttf"

__all__ = [
    "AZAHAR_MOD_IMG",
    "AZAHAR_MOD_TRB_DIR",
    "BAKE_IMG",
    "OVERLAY_TRB_DIR",
    "RELEASE",
    "ROMFS_OVERLAY",
    "ROOT",
    "TEXTRESOURCE",
    "UI_FONT",
    "find_vanilla_img",
    "iter_deploy_targets",
    "require_vanilla_img",
    "resolve_img_paths",
    "resolve_resident_trb",
    "ui_font",
]


def ui_font(size: int):
    """Load the bundled EngPatcher UI font at ``size`` pt."""
    from PIL import ImageFont

    if not UI_FONT.is_file():
        raise SystemExit(
            f"missing UI font: {UI_FONT}\n"
            "Expected assets/fonts/MPLUS1p-Regular.ttf (SIL OFL)."
        )
    return ImageFont.truetype(str(UI_FONT), size=size)


def resolve_img_paths() -> tuple[Path, Path]:
    """Return (primary_img, vanilla_img) for deploy_* scripts.

    Primary resolution (self-sustaining first):
      1. NLPP_DEPLOY_IMG
      2. release/bake_img.bin if present
      3. Azahar LayeredFS img.bin (optional test convenience)

    Vanilla resolution:
      1. NLPP_VANILLA_IMG / sibling extracted
      2. primary.bak_pre_msel5245 (legacy sidecar)
      3. primary itself
    """
    env = os.environ.get("NLPP_DEPLOY_IMG")
    if env:
        primary = Path(env).resolve()
        if not primary.is_file():
            raise SystemExit(f"NLPP_DEPLOY_IMG not found: {primary}")
    elif BAKE_IMG.is_file():
        primary = BAKE_IMG.resolve()
    elif AZAHAR_MOD_IMG.is_file():
        primary = AZAHAR_MOD_IMG.resolve()
    else:
        raise SystemExit(
            "No deploy img.bin target. Run tools/rebuild_bake_img.py first, or set "
            "NLPP_DEPLOY_IMG, or place gold at:\n"
            f"  {BAKE_IMG}"
        )

    vanilla = find_vanilla_img()
    if vanilla is None:
        bak = primary.with_suffix(".bin.bak_pre_msel5245")
        if bak.is_file():
            vanilla = bak.resolve()
        else:
            vanilla = primary
    return primary, vanilla


def iter_deploy_targets(primary: Path) -> list[Path]:
    """Imgs to splice into: primary, then bake/Azahar mirrors when distinct."""
    targets: list[Path] = [primary.resolve()]
    seen = {targets[0]}

    def _add(p: Path) -> None:
        rp = p.resolve()
        if rp.is_file() and rp not in seen:
            targets.append(rp)
            seen.add(rp)

    _add(BAKE_IMG)
    if os.environ.get("NLPP_ALSO_AZAHAR", "").strip() in ("1", "true", "yes"):
        _add(AZAHAR_MOD_IMG)
    return targets


def resolve_resident_trb() -> Path:
    """Best available resident TRB for day-counter / sync (no Azahar required)."""
    env = os.environ.get("NLPP_RESIDENT_TRB")
    if env:
        p = Path(env)
        if p.is_file():
            return p.resolve()
        raise SystemExit(f"NLPP_RESIDENT_TRB not found: {p}")

    for c in (
        OVERLAY_TRB_DIR / "textresource_resident_jpn.trb",
        TEXTRESOURCE / "textresource_resident_jpn.trb",
        AZAHAR_MOD_TRB_DIR / "textresource_resident_jpn.trb",
        ROOT.parent
        / "New Love Plus Plus"
        / "extracted"
        / "romfs"
        / "SystemData"
        / "TextResource"
        / "textresource_resident_jpn.trb",
    ):
        if c.is_file():
            return c.resolve()
    raise SystemExit(
        "resident TRB not found under release/ or extracted/. "
        "Set NLPP_RESIDENT_TRB or run rebuild with textresource present."
    )
