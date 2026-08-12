"""Zone fix tests: Flag/threshold sprawl — gate-disabling, self-referential,
snapshot gaps.

Covers four fixes:
1. helpers._blank_line_fallback_enabled decoupled from PASS_MAX_LEAF_RATIO
2. config.effective_config_snapshot removed PASS_MAX_LEAF_RATIO fallback for
   leaf_split_ratio, added 4 new snapshot keys
3. converters._recover_picture_text supplementary garble check
4. client inspector_class gated behind PDF_INSPECTOR_PRECLASSIFY
"""

from __future__ import annotations

import inspect
import os
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# 1. _blank_line_fallback_enabled: LEAF_SPLIT_RATIO independent of
#    PASS_MAX_LEAF_RATIO (self-referential feedback-loop fix)
# ---------------------------------------------------------------------------


class TestLeafSplitRatioDecoupled:
    """LEAF_SPLIT_RATIO must NOT fall back to PASS_MAX_LEAF_RATIO."""

    def test_source_does_not_read_pass_max_leaf_ratio(self):
        """The function must not reference PASS_MAX_LEAF_RATIO at all
        (except possibly in a docstring comment)."""
        from pageindex_mcp.helpers import _blank_line_fallback_enabled

        source = inspect.getsource(_blank_line_fallback_enabled)
        executable_lines = [
            line
            for line in source.splitlines()
            if line.strip()
            and not line.lstrip().startswith("#")
            and not line.lstrip().startswith('"""')
            and not line.lstrip().startswith("'''")
        ]
        for line in executable_lines:
            if "PASS_MAX_LEAF_RATIO" in line:
                # Allow it in string literals that are part of docstrings
                # but NOT in os.environ.get calls
                if "os.environ" in line or "getenv" in line:
                    pytest.fail(
                        f"_blank_line_fallback_enabled still reads "
                        f"PASS_MAX_LEAF_RATIO: {line.strip()}"
                    )

    def test_different_pass_max_does_not_affect_leaf_split(self):
        """Changing PASS_MAX_LEAF_RATIO must not change the threshold."""
        from pageindex_mcp.helpers import _blank_line_fallback_enabled

        env_clean = {
            k: v
            for k, v in os.environ.items()
            if k not in ("PASS_MAX_LEAF_RATIO", "LEAF_SPLIT_RATIO",
                         "LEAF_CONCENTRATION_PARAGRAPH_SPLIT_ENABLED")
        }
        # With PASS_MAX_LEAF_RATIO=0.90 (very high), default leaf_split
        # should still be 0.30
        with patch.dict(os.environ, {**env_clean, "PASS_MAX_LEAF_RATIO": "0.90"}, clear=True):
            # tree_ratio=0.35 > default 0.30 => enabled
            assert _blank_line_fallback_enabled(0.35) is True
            # tree_ratio=0.25 < default 0.30 => disabled
            assert _blank_line_fallback_enabled(0.25) is False

    def test_leaf_split_ratio_env_overrides_default(self):
        """LEAF_SPLIT_RATIO env var is the sole override knob."""
        from pageindex_mcp.helpers import _blank_line_fallback_enabled

        env_clean = {
            k: v
            for k, v in os.environ.items()
            if k not in ("PASS_MAX_LEAF_RATIO", "LEAF_SPLIT_RATIO",
                         "LEAF_CONCENTRATION_PARAGRAPH_SPLIT_ENABLED")
        }
        with patch.dict(os.environ, {**env_clean, "LEAF_SPLIT_RATIO": "0.50"}, clear=True):
            assert _blank_line_fallback_enabled(0.40) is False
            assert _blank_line_fallback_enabled(0.55) is True

    def test_default_threshold_is_030(self):
        """Without any env vars, the threshold defaults to 0.30."""
        from pageindex_mcp.helpers import _blank_line_fallback_enabled

        env_clean = {
            k: v
            for k, v in os.environ.items()
            if k not in ("PASS_MAX_LEAF_RATIO", "LEAF_SPLIT_RATIO",
                         "LEAF_CONCENTRATION_PARAGRAPH_SPLIT_ENABLED")
        }
        with patch.dict(os.environ, env_clean, clear=True):
            # At exactly 0.30 => not enabled (> not >=)
            assert _blank_line_fallback_enabled(0.30) is False
            # Just above 0.30 => enabled
            assert _blank_line_fallback_enabled(0.31) is True


# ---------------------------------------------------------------------------
# 2. effective_config_snapshot: leaf_split_ratio decoupled + new keys
# ---------------------------------------------------------------------------


class TestConfigSnapshotDecoupled:
    """effective_config_snapshot must report leaf_split_ratio independently
    and include all new config keys."""

    def test_leaf_split_ratio_ignores_pass_max_leaf_ratio(self):
        """leaf_split_ratio in snapshot must NOT fall through to
        PASS_MAX_LEAF_RATIO."""
        from pageindex_mcp.config import effective_config_snapshot

        env_patch = {"PASS_MAX_LEAF_RATIO": "0.99"}
        with patch.dict(os.environ, env_patch):
            if "LEAF_SPLIT_RATIO" in os.environ:
                del os.environ["LEAF_SPLIT_RATIO"]
            snap = effective_config_snapshot()
        assert snap["leaf_split_ratio"] == pytest.approx(0.30), (
            "leaf_split_ratio should default to 0.30 regardless of "
            f"PASS_MAX_LEAF_RATIO, got {snap['leaf_split_ratio']}"
        )

    def test_snapshot_source_no_pass_max_leaf_ratio_for_leaf_split(self):
        """The source of effective_config_snapshot must not use
        PASS_MAX_LEAF_RATIO as a fallback for leaf_split_ratio."""
        from pageindex_mcp.config import effective_config_snapshot

        source = inspect.getsource(effective_config_snapshot)
        # Find the leaf_split_ratio block
        in_leaf_split = False
        for line in source.splitlines():
            if "leaf_split_ratio" in line and ":" in line:
                in_leaf_split = True
            elif in_leaf_split:
                if "PASS_MAX_LEAF_RATIO" in line:
                    pytest.fail(
                        "effective_config_snapshot still uses "
                        "PASS_MAX_LEAF_RATIO fallback for leaf_split_ratio"
                    )
                # Next dict key starts
                if '"' in line and ":" in line and "leaf_split" not in line:
                    break

    def test_new_snapshot_keys_present(self):
        """Four new config keys must be present in snapshot."""
        from pageindex_mcp.config import effective_config_snapshot

        snap = effective_config_snapshot()
        expected_keys = [
            "tree_path_picture_splice_enabled",
            "low_content_ocr_char_floor",
            "rfc029_flat_prefer_multiplier",
            "rfc029_min_chars_per_node",
        ]
        for key in expected_keys:
            assert key in snap, f"Missing snapshot key: {key}"

    def test_new_keys_default_values(self):
        """New keys should have correct defaults when env vars are unset."""
        from pageindex_mcp.config import effective_config_snapshot

        env_vars_to_clear = [
            "TREE_PATH_PICTURE_SPLICE_ENABLED",
            "LOW_CONTENT_OCR_CHAR_FLOOR",
            "RFC029_FLAT_PREFER_MULTIPLIER",
            "RFC029_MIN_CHARS_PER_NODE",
        ]
        clean_env = {
            k: v for k, v in os.environ.items() if k not in env_vars_to_clear
        }
        with patch.dict(os.environ, clean_env, clear=True):
            snap = effective_config_snapshot()
        assert snap["tree_path_picture_splice_enabled"] is True
        assert snap["low_content_ocr_char_floor"] == 300
        assert snap["rfc029_flat_prefer_multiplier"] == pytest.approx(3.0)
        assert snap["rfc029_min_chars_per_node"] == pytest.approx(150.0)

    def test_new_keys_env_override(self):
        """New keys must respond to env var overrides."""
        from pageindex_mcp.config import effective_config_snapshot

        overrides = {
            "TREE_PATH_PICTURE_SPLICE_ENABLED": "false",
            "LOW_CONTENT_OCR_CHAR_FLOOR": "500",
            "RFC029_FLAT_PREFER_MULTIPLIER": "5.0",
            "RFC029_MIN_CHARS_PER_NODE": "200",
        }
        with patch.dict(os.environ, overrides):
            snap = effective_config_snapshot()
        assert snap["tree_path_picture_splice_enabled"] is False
        assert snap["low_content_ocr_char_floor"] == 500
        assert snap["rfc029_flat_prefer_multiplier"] == pytest.approx(5.0)
        assert snap["rfc029_min_chars_per_node"] == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# 3. _recover_picture_text: supplementary garble check when
#    _REGION_AWARE_TEXT_CHECK_ENABLED is True
# ---------------------------------------------------------------------------


class TestSupplementaryGarbleCheck:
    """When region-aware text check is enabled and the region has chars,
    the page-level garble check must run as a second gate."""

    def test_source_has_supplementary_garble_gate(self):
        """_recover_picture_text must call _text_layer_has_content as a
        second gate when _region_has_own_text_layer returns True."""
        from pageindex_mcp.converters import _recover_picture_text

        source = inspect.getsource(_recover_picture_text)
        # The fix adds: if has_own_text and not _text_layer_has_content(page):
        assert "_text_layer_has_content" in source, (
            "_recover_picture_text must reference _text_layer_has_content "
            "for the supplementary garble check"
        )
        # Verify the pattern: has_own_text set False when garble detected
        assert "has_own_text = False" in source, (
            "_recover_picture_text must set has_own_text = False when the "
            "page-level garble check fails"
        )

    def test_region_aware_path_calls_both_checks(self):
        """Under _REGION_AWARE_TEXT_CHECK_ENABLED, both
        _region_has_own_text_layer and _text_layer_has_content must
        appear in the code path."""
        from pageindex_mcp.converters import _recover_picture_text

        source = inspect.getsource(_recover_picture_text)
        # Find the region-aware block
        lines = source.splitlines()
        in_region_block = False
        found_region_check = False
        found_garble_gate = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "_REGION_AWARE_TEXT_CHECK_ENABLED" in stripped:
                in_region_block = True
            if in_region_block:
                if "_region_has_own_text_layer" in stripped:
                    found_region_check = True
                if "_is_garbled_blob" in stripped:
                    found_garble_gate = True
                # Break out at else clause (end of if-block)
                if stripped.startswith("else:"):
                    break
        assert found_region_check, (
            "_region_has_own_text_layer not found in region-aware block"
        )
        assert found_garble_gate, (
            "_is_garbled_blob not found as supplementary garble gate "
            "in region-aware block"
        )


# ---------------------------------------------------------------------------
# 4. client.py: inspector_class gated behind PDF_INSPECTOR_PRECLASSIFY
# ---------------------------------------------------------------------------


class TestInspectorClassGated:
    """inspector_class must only be written to meta when
    PDF_INSPECTOR_PRECLASSIFY is True."""

    def test_source_gates_inspector_class_on_flag(self):
        """The line writing inspector_class must check
        PDF_INSPECTOR_PRECLASSIFY."""
        from pageindex_mcp.client import CustomPageIndexClient

        source = inspect.getsource(CustomPageIndexClient)
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if "inspector_class" in line and "meta[" in line:
                # Look at the surrounding if-condition (previous lines)
                context = "\n".join(lines[max(0, i - 3): i + 1])
                assert "PDF_INSPECTOR_PRECLASSIFY" in context, (
                    "inspector_class assignment must be gated behind "
                    "PDF_INSPECTOR_PRECLASSIFY flag"
                )
                break
        else:
            # If not found inline, check the if-condition line
            found = False
            for line in lines:
                if "PDF_INSPECTOR_PRECLASSIFY" in line and "inspector_class" in line:
                    found = True
                    break
                if "PDF_INSPECTOR_PRECLASSIFY" in line:
                    found = True
                    break
            assert found, (
                "PDF_INSPECTOR_PRECLASSIFY not found gating inspector_class"
            )

    def test_pdf_inspector_preclassify_imported_in_client(self):
        """PDF_INSPECTOR_PRECLASSIFY must be imported into client.py."""
        import pageindex_mcp.client as client_mod

        assert hasattr(client_mod, "PDF_INSPECTOR_PRECLASSIFY"), (
            "PDF_INSPECTOR_PRECLASSIFY not imported in client module"
        )


# ---------------------------------------------------------------------------
# 5. Snapshot completeness: every behavior flag in the snapshot
# ---------------------------------------------------------------------------


class TestSnapshotCompleteness:
    """effective_config_snapshot must cover all declared behavior flags."""

    def test_snapshot_has_at_least_24_keys(self):
        """The snapshot grew from ~20 to ~24 keys with the new additions."""
        from pageindex_mcp.config import effective_config_snapshot

        snap = effective_config_snapshot()
        assert len(snap) >= 24, (
            f"Snapshot has only {len(snap)} keys, expected at least 24"
        )

    def test_snapshot_values_are_serializable(self):
        """All snapshot values must be JSON-serializable primitives."""
        import json

        from pageindex_mcp.config import effective_config_snapshot

        snap = effective_config_snapshot()
        try:
            json.dumps(snap)
        except (TypeError, ValueError) as exc:
            pytest.fail(f"Snapshot not JSON-serializable: {exc}")

    def test_leaf_split_and_pass_max_are_independent_keys(self):
        """Both leaf_split_ratio and pass_max_leaf_ratio must be
        separate keys in the snapshot (not aliased)."""
        from pageindex_mcp.config import effective_config_snapshot

        with patch.dict(os.environ, {
            "PASS_MAX_LEAF_RATIO": "0.50",
            "LEAF_SPLIT_RATIO": "0.20",
        }):
            snap = effective_config_snapshot()
        assert snap["pass_max_leaf_ratio"] == pytest.approx(0.50)
        assert snap["leaf_split_ratio"] == pytest.approx(0.20)
        assert snap["pass_max_leaf_ratio"] != snap["leaf_split_ratio"]
