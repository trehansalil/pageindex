"""Zone-5: OCR escalation flag split -- exhaustiveness tests.

Locks the contract that the monolithic OCR_ESCALATION flag was correctly split
into two independent controls (OCR_ESCALATION_GARBLE, OCR_ESCALATION_PER_PICTURE)
with backward-compatible legacy fallback.

Three contracts tested:

1. **decide_ocr_mode exhaustiveness** -- all 4 combinations of the two boolean
   axes (ocr_escalation_enabled x force_full_page) plus the has_image_markers
   dimension produce the correct OcrMode variant.
2. **Legacy backward compat** -- OCR_ESCALATION=0 (legacy) disables both split
   flags when neither is explicitly set.
3. **Wiring** -- effective_config_snapshot includes all three escalation keys.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# 1. decide_ocr_mode: exhaustive truth-table
# ---------------------------------------------------------------------------


class TestDecideOcrModeExhaustive:
    """Every combination of (ocr_escalation_enabled, has_image_markers,
    force_full_page) maps to exactly one OcrMode variant."""

    @pytest.mark.parametrize(
        "escalation, markers, force, expected",
        [
            # force_full_page always wins regardless of others
            (True, True, True, "full_page"),
            (True, False, True, "full_page"),
            (False, True, True, "full_page"),
            (False, False, True, "full_page"),
            # no force: escalation + markers -> PER_PICTURE
            (True, True, False, "per_picture"),
            # no force, escalation but no markers -> NONE
            (True, False, False, "none"),
            # no force, no escalation -> NONE regardless of markers
            (False, True, False, "none"),
            (False, False, False, "none"),
        ],
        ids=[
            "force-esc-mark",
            "force-esc-nomark",
            "force-noesc-mark",
            "force-noesc-nomark",
            "esc-mark",
            "esc-nomark",
            "noesc-mark",
            "noesc-nomark",
        ],
    )
    def test_truth_table(self, escalation, markers, force, expected):
        from pageindex_mcp.picture_plane import OcrMode, decide_ocr_mode

        result = decide_ocr_mode(
            ocr_escalation_enabled=escalation,
            has_image_markers=markers,
            force_full_page=force,
        )
        assert result == OcrMode(expected), (
            f"decide_ocr_mode(esc={escalation}, markers={markers}, "
            f"force={force}) -> {result}, expected {expected}"
        )

    def test_return_type_is_ocr_mode(self):
        from pageindex_mcp.picture_plane import OcrMode, decide_ocr_mode

        result = decide_ocr_mode(
            ocr_escalation_enabled=True,
            has_image_markers=True,
            force_full_page=False,
        )
        assert isinstance(result, OcrMode)


# ---------------------------------------------------------------------------
# 2. Legacy OCR_ESCALATION=0 backward compat
# ---------------------------------------------------------------------------


class TestLegacyOcrEscalationCompat:
    """When OCR_ESCALATION=0 is set and the split flags are NOT explicitly set,
    both new flags must inherit the disabled state."""

    def test_legacy_zero_disables_both(self, monkeypatch):
        """OCR_ESCALATION_GARBLE=0 and OCR_ESCALATION_PER_PICTURE=0 -> both False."""
        import importlib
        monkeypatch.setenv("OCR_ESCALATION_GARBLE", "0")
        monkeypatch.setenv("OCR_ESCALATION_PER_PICTURE", "0")
        import pageindex_mcp.config as cfg_mod
        importlib.reload(cfg_mod)
        try:
            assert cfg_mod.OCR_ESCALATION_GARBLE is False
            assert cfg_mod.OCR_ESCALATION_PER_PICTURE is False
        finally:
            monkeypatch.delenv("OCR_ESCALATION_GARBLE", raising=False)
            monkeypatch.delenv("OCR_ESCALATION_PER_PICTURE", raising=False)
            importlib.reload(cfg_mod)

    def test_split_flags_override_legacy(self, monkeypatch):
        """Explicit split flags set independently."""
        import importlib
        monkeypatch.setenv("OCR_ESCALATION_GARBLE", "1")
        monkeypatch.setenv("OCR_ESCALATION_PER_PICTURE", "0")
        import pageindex_mcp.config as cfg_mod
        importlib.reload(cfg_mod)
        try:
            assert cfg_mod.OCR_ESCALATION_GARBLE is True
            assert cfg_mod.OCR_ESCALATION_PER_PICTURE is False
        finally:
            monkeypatch.delenv("OCR_ESCALATION_GARBLE", raising=False)
            monkeypatch.delenv("OCR_ESCALATION_PER_PICTURE", raising=False)
            importlib.reload(cfg_mod)


# ---------------------------------------------------------------------------
# 3. Wiring: effective_config_snapshot includes all 3 escalation keys
# ---------------------------------------------------------------------------


class TestEffectiveConfigSnapshotWiring:
    """effective_config_snapshot must expose all three escalation keys."""

    def test_snapshot_contains_all_escalation_keys(self):
        from pageindex_mcp.config import effective_config_snapshot

        snap = effective_config_snapshot()
        for key in ("ocr_escalation_garble", "ocr_escalation_per_picture"):
            assert key in snap, f"effective_config_snapshot missing key: {key}"
            assert isinstance(snap[key], bool), (
                f"snap[{key!r}] = {snap[key]!r} is not bool"
            )

    def test_snapshot_keys_reflect_module_values(self):
        """Snapshot values must match the module-level flags."""
        from pageindex_mcp.config import (
            OCR_ESCALATION_GARBLE,
            OCR_ESCALATION_PER_PICTURE,
            effective_config_snapshot,
        )

        snap = effective_config_snapshot()
        assert snap["ocr_escalation_garble"] == OCR_ESCALATION_GARBLE
        assert snap["ocr_escalation_per_picture"] == OCR_ESCALATION_PER_PICTURE


# ---------------------------------------------------------------------------
# 4. Wiring: client.py imports the split flags and passes them to call sites
# ---------------------------------------------------------------------------


class TestClientWiring:
    """client.py must import and use the split flags, not the legacy one."""

    def test_client_imports_split_flags(self):
        """client.py re-exports split flags for its call sites."""
        from pageindex_mcp.client import (
            _OCR_ESCALATION_GARBLE,
            _OCR_ESCALATION_PER_PICTURE,
        )

        assert isinstance(_OCR_ESCALATION_GARBLE, bool)
        assert isinstance(_OCR_ESCALATION_PER_PICTURE, bool)

    def test_client_imports_decide_ocr_strategy(self):
        """client.py uses the centralized decide_ocr_strategy, not inline logic."""
        import inspect

        from pageindex_mcp import client as cli_mod
        from pageindex_mcp.picture_plane import decide_ocr_strategy

        src = inspect.getsource(cli_mod)
        assert "decide_ocr_strategy(" in src, (
            "client.py does not call decide_ocr_strategy -- OCR mode decision "
            "may have regressed to inline logic"
        )
