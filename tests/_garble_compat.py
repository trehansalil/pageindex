"""Zone-4 test compat: thin wrapper so legacy check_garble test calls work."""

from __future__ import annotations

import os

from pageindex_mcp.helpers import garble as _garble_mod
from pageindex_mcp.helpers.garble import (
    BlobKind,
    GarbleConfig,
    GarbleProfile,
    ScriptContext,
    detect_garble,
)


def _rebuild_garble_config_compat() -> GarbleConfig:
    """Rebuild GarbleConfig from current env + module state for test backward compat."""
    return GarbleConfig(
        garble_latin_gibberish_enabled=(
            os.environ.get("GARBLE_LATIN_GIBBERISH_ENABLED", "true").lower()
            not in ("false", "0", "no")
        ),
        garble_latin_ratio=float(os.environ.get("GARBLE_LATIN_RATIO", "0.4")),
        garble_nonsense_ratio=float(os.environ.get("GARBLE_NONSENSE_RATIO", "0.7")),
        garble_short_text_default=_garble_mod._GARBLE_SHORT_TEXT_DEFAULT,
        garble_flat_markdown_normalize=_garble_mod._GARBLE_FLAT_MARKDOWN_NORMALIZE,
        garble_node_ratio_threshold=_garble_mod._GARBLE_NODE_RATIO_THRESHOLD,
        garble_digit_floor=500,
    )


def check_garble(
    text: str,
    *,
    expected_script: str | None = None,
    profile: GarbleProfile,
    had_presentation_forms: bool = False,
    original_defect=None,
) -> bool:
    """Backward-compat test helper — delegates to detect_garble."""
    _blob = BlobKind.RAW_MARKDOWN if (profile and profile.normalize_markdown) else BlobKind.TREE_TEXT
    _ctx = ScriptContext(
        dominant_script=expected_script,
        had_presentation_forms=had_presentation_forms,
        source="test_compat",
    )
    _cfg = _rebuild_garble_config_compat()
    return bool(
        detect_garble(
            text,
            script_context=_ctx,
            config=_cfg,
            blob_kind=_blob,
            original_defect=original_defect,
        )
    )
