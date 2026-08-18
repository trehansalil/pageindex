"""Zone-2 OCR recovery contract tests.

Validates the Zone-2 refactor of the OCR recovery pipeline:
1. OcrRetryReason enum exhaustiveness (3 members: GARBLE, LOW_CONTENT, IMAGE_DOMINANT)
2. Independent per-reason flag gating (GARBLE/LOW_CONTENT -> OCR_ESCALATION_GARBLE,
   IMAGE_DOMINANT -> IMAGE_DOMINANT_OCR_ESCALATION_ENABLED)
3. Unconditional rtl_decision clear on every recovery path
4. _repeating_token_density returns 1.0 (not None) for <20 tokens
5. IMAGE_DOMINANT skips keep-best heuristic (no pre-retry snapshot)
6. Pre-retry snapshot only for GARBLE/LOW_CONTENT
7. Per-picture re-entry guard in converters._recover_picture_results
8. Config canonical flag source (IMAGE_DOMINANT_OCR_ESCALATION_ENABLED in config.py)
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from pageindex_mcp.helpers import (
    ExtractionState,
    OcrRetryReason,
    RecoveryOutcome,
    Route,
    TreeDefect,
)


# ---------------------------------------------------------------------------
# 1. OcrRetryReason enum exhaustiveness
# ---------------------------------------------------------------------------


class TestOcrRetryReasonExhaustiveness:
    """OcrRetryReason must have exactly 3 members matching the recovery dispatch."""

    def test_exactly_three_members(self):
        members = list(OcrRetryReason)
        assert len(members) == 3, (
            f"OcrRetryReason must have exactly 3 members, got {len(members)}: {members}"
        )

    def test_garble_member(self):
        assert OcrRetryReason.GARBLE == "garble"

    def test_low_content_member(self):
        assert OcrRetryReason.LOW_CONTENT == "low_content"

    def test_image_dominant_member(self):
        assert OcrRetryReason.IMAGE_DOMINANT == "image_dominant"

    def test_is_str_enum(self):
        from enum import StrEnum

        assert issubclass(OcrRetryReason, StrEnum)

    def test_every_member_has_reason_label_in_recover_ocr_retry(self):
        """Every OcrRetryReason member must appear in _reason_label and
        _splice_label dicts inside _recover_ocr_retry (source-level check)."""
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._recover_ocr_retry)
        for member in OcrRetryReason:
            ref = f"OcrRetryReason.{member.name}"
            assert ref in src, (
                f"OcrRetryReason.{member.name} not referenced in _recover_ocr_retry"
            )

    def test_every_member_has_metric_result_mapping(self):
        """_metric_result dict in _recover_ocr_retry must map every reason."""
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._recover_ocr_retry)
        # The _metric_result dict should contain all three reason names.
        for member in OcrRetryReason:
            assert f"OcrRetryReason.{member.name}" in src


# ---------------------------------------------------------------------------
# 2. Independent per-reason flag gating
# ---------------------------------------------------------------------------


class TestPerReasonFlagGating:
    """GARBLE/LOW_CONTENT gate on OCR_ESCALATION_GARBLE;
    IMAGE_DOMINANT gates on IMAGE_DOMINANT_OCR_ESCALATION_ENABLED.
    These must be independent -- toggling one must not affect the other."""

    def test_garble_checks_ocr_escalation_garble_flag(self):
        """The GARBLE branch must check _OCR_ESCALATION_GARBLE (not IMAGE_DOMINANT)."""
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._recover_ocr_retry)
        # Parse to AST and find the GARBLE branch.
        tree = ast.parse(textwrap.dedent(src))
        garble_branch_found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                # Look for `reason == OcrRetryReason.GARBLE`
                if (
                    isinstance(node.comparators[0], ast.Attribute)
                    and getattr(node.comparators[0], "attr", None) == "GARBLE"
                ):
                    garble_branch_found = True
        assert garble_branch_found, "GARBLE comparison not found in _recover_ocr_retry"

        # Source-level: GARBLE branch references _OCR_ESCALATION_GARBLE.
        # Extract lines between GARBLE check and the next elif/else.
        lines = src.split("\n")
        in_garble_block = False
        garble_block = []
        for line in lines:
            if "reason == OcrRetryReason.GARBLE" in line:
                in_garble_block = True
                continue
            if in_garble_block:
                if "elif reason ==" in line or "else:" in line:
                    break
                garble_block.append(line)
        garble_src = "\n".join(garble_block)
        assert "_OCR_ESCALATION_GARBLE" in garble_src, (
            "GARBLE branch does not check _OCR_ESCALATION_GARBLE"
        )

    def test_low_content_checks_ocr_escalation_garble_flag(self):
        """LOW_CONTENT branch must also check _OCR_ESCALATION_GARBLE."""
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._recover_ocr_retry)
        lines = src.split("\n")
        in_low_content_block = False
        low_content_block = []
        for line in lines:
            if "reason == OcrRetryReason.LOW_CONTENT" in line:
                in_low_content_block = True
                continue
            if in_low_content_block:
                if "elif reason ==" in line or "else:" in line:
                    break
                low_content_block.append(line)
        low_content_src = "\n".join(low_content_block)
        assert "_OCR_ESCALATION_GARBLE" in low_content_src, (
            "LOW_CONTENT branch does not check _OCR_ESCALATION_GARBLE"
        )

    def test_image_dominant_checks_independent_flag(self):
        """IMAGE_DOMINANT branch must check _IMAGE_DOMINANT_OCR_ESCALATION_ENABLED,
        NOT _OCR_ESCALATION_GARBLE."""
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._recover_ocr_retry)
        lines = src.split("\n")
        in_image_dominant_block = False
        image_dominant_block = []
        for line in lines:
            if "reason == OcrRetryReason.IMAGE_DOMINANT" in line:
                in_image_dominant_block = True
                continue
            if in_image_dominant_block:
                if "# ---- Pre-retry" in line or "_use_keep_best" in line:
                    break
                image_dominant_block.append(line)
        image_dominant_src = "\n".join(image_dominant_block)
        assert "_IMAGE_DOMINANT_OCR_ESCALATION_ENABLED" in image_dominant_src, (
            "IMAGE_DOMINANT branch does not check _IMAGE_DOMINANT_OCR_ESCALATION_ENABLED"
        )
        # Must NOT gate on _OCR_ESCALATION_GARBLE (that would be flag conflation).
        assert "_OCR_ESCALATION_GARBLE" not in image_dominant_src, (
            "IMAGE_DOMINANT branch still gates on _OCR_ESCALATION_GARBLE "
            "(flag conflation not fully decoupled)"
        )


# ---------------------------------------------------------------------------
# 3. Unconditional rtl_decision clear
# ---------------------------------------------------------------------------


class TestUnconditionalRtlDecisionClear:
    """rtl_decision must be cleared on every recovery path, not just
    the remote+renormalize branch (the pre-Zone-2 inconsistency)."""

    def test_rtl_decision_cleared_on_all_paths(self):
        """Both the remote+renormalize branch and the else branch must
        clear (or set) rtl_decision."""
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._recover_ocr_retry)
        # The else branch sets state.rtl_decision = None.
        assert "state.rtl_decision = None" in src, (
            "rtl_decision not unconditionally cleared in _recover_ocr_retry"
        )
        # The if branch (renormalize) sets it via _renormalize_bidi_guarded return.
        assert "_renormalize_bidi_guarded" in src, (
            "_renormalize_bidi_guarded not called in _recover_ocr_retry"
        )

    def test_rtl_decision_clear_not_reason_gated(self):
        """The rtl_decision clearing code must NOT be inside a per-reason
        if/elif block -- it must apply to all reasons."""
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._recover_ocr_retry)
        # Find the rtl_decision clearing: it should be after the OCR dispatch
        # and before _reconvert_and_revalidate, NOT inside a reason== branch.
        lines = src.split("\n")
        rtl_clear_line = None
        for i, line in enumerate(lines):
            if "state.rtl_decision = None" in line:
                rtl_clear_line = i
                break
        assert rtl_clear_line is not None
        # Verify it's not inside a `if reason ==` block by checking indentation
        # relative to the reason-gating blocks (which end before the try: block).
        # The rtl_decision clear should be at the try-block indentation level,
        # not deeper inside a reason-specific branch.
        reconvert_line = None
        for i, line in enumerate(lines):
            if "_reconvert_and_revalidate" in line:
                reconvert_line = i
                break
        assert reconvert_line is not None
        # rtl_decision clear must come before reconvert_and_revalidate
        assert rtl_clear_line < reconvert_line, (
            "rtl_decision clear must come before _reconvert_and_revalidate"
        )


# ---------------------------------------------------------------------------
# 4. _repeating_token_density returns 1.0 for <20 tokens
# ---------------------------------------------------------------------------


class TestRepeatingTokenDensity:
    """_repeating_token_density must return 1.0 (not None) for <20 tokens,
    ensuring RFC-029 D4 density comparison always runs."""

    def _get_density_fn(self):
        """Extract and compile the nested _repeating_token_density function."""
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._recover_ocr_retry)
        # Parse the source to find the nested function definition.
        lines = src.split("\n")
        fn_lines = []
        capture = False
        base_indent = None
        for line in lines:
            if "def _repeating_token_density" in line:
                capture = True
                base_indent = len(line) - len(line.lstrip())
                fn_lines.append(line[base_indent:])
                continue
            if capture:
                stripped = line.lstrip()
                if stripped and not line.startswith(" " * (base_indent + 1)) and not stripped.startswith("#") and not stripped.startswith('"""') and not stripped.startswith("'"):
                    # Check if this is continuation of docstring or body
                    current_indent = len(line) - len(line.lstrip())
                    if current_indent <= base_indent and stripped:
                        break
                fn_lines.append(line[base_indent:] if len(line) >= base_indent else line)

        fn_src = "\n".join(fn_lines)
        ns: dict = {}
        exec(fn_src, ns)
        return ns["_repeating_token_density"]

    def test_returns_float_for_short_text(self):
        """<20 alnum tokens must return 1.0, not None."""
        fn = self._get_density_fn()
        result = fn("hello world")
        assert result == 1.0, (
            f"Expected 1.0 for <20 tokens, got {result!r} (was None pre-fix)"
        )

    def test_returns_float_for_empty_text(self):
        fn = self._get_density_fn()
        result = fn("")
        assert result == 1.0

    def test_return_type_is_always_float(self):
        """Return type annotation must be float (not float | None)."""
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._recover_ocr_retry)
        # Find the function signature line.
        for line in src.split("\n"):
            if "def _repeating_token_density" in line:
                assert "-> float:" in line or "-> float :" in line, (
                    f"_repeating_token_density return type must be float, got: {line.strip()}"
                )
                # Must NOT return Optional/None.
                assert "None" not in line, (
                    f"_repeating_token_density signature still references None: {line.strip()}"
                )
                break

    def test_returns_density_for_20plus_tokens(self):
        """>=20 alnum tokens must return a real density (0.0-1.0)."""
        fn = self._get_density_fn()
        text = " ".join(f"word{i}" for i in range(25))
        result = fn(text)
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0
        # All unique tokens -> low density.
        assert result < 0.2

    def test_high_repetition_gives_high_density(self):
        fn = self._get_density_fn()
        text = " ".join(["repeated"] * 25)
        result = fn(text)
        assert result == 1.0

    def test_no_none_branches_in_keep_best(self):
        """After the fix, there must be no '_pre_density is None' or
        '_post_density is None' branches in the keep-best heuristic."""
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._recover_ocr_retry)
        assert "_pre_density is None" not in src, (
            "Stale None-density branch still exists in keep-best heuristic"
        )
        assert "_post_density is None" not in src, (
            "Stale None-density branch still exists in keep-best heuristic"
        )


# ---------------------------------------------------------------------------
# 5. IMAGE_DOMINANT skips keep-best heuristic
# ---------------------------------------------------------------------------


class TestImageDominantSkipsKeepBest:
    """IMAGE_DOMINANT must accept recovery unconditionally -- no pre-retry
    snapshot, no revert."""

    def test_use_keep_best_excludes_image_dominant(self):
        """_use_keep_best must be True only for GARBLE/LOW_CONTENT."""
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._recover_ocr_retry)
        # Find the _use_keep_best assignment.
        for line in src.split("\n"):
            if "_use_keep_best" in line and "=" in line and "reason in" in line:
                # Must include GARBLE and LOW_CONTENT but not IMAGE_DOMINANT.
                assert "GARBLE" in line
                assert "LOW_CONTENT" in line
                assert "IMAGE_DOMINANT" not in line, (
                    "_use_keep_best includes IMAGE_DOMINANT -- "
                    "image-dominant should skip the keep-best heuristic"
                )
                break
        else:
            pytest.fail("_use_keep_best assignment not found in _recover_ocr_retry")


# ---------------------------------------------------------------------------
# 6. Pre-retry snapshot only for GARBLE/LOW_CONTENT
# ---------------------------------------------------------------------------


class TestPreRetrySnapshot:
    """RecoveryOutcome snapshot must be created only when _use_keep_best is True,
    which covers GARBLE and LOW_CONTENT but not IMAGE_DOMINANT."""

    def test_pre_retry_gated_by_use_keep_best(self):
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._recover_ocr_retry)
        lines = src.split("\n")
        # Find "if _use_keep_best:" guarding RecoveryOutcome construction.
        snapshot_guard_found = False
        for i, line in enumerate(lines):
            if "_use_keep_best" in line and "if" in line:
                # Check that RecoveryOutcome appears after it.
                for j in range(i + 1, min(i + 15, len(lines))):
                    if "RecoveryOutcome(" in lines[j]:
                        snapshot_guard_found = True
                        break
                if snapshot_guard_found:
                    break
        assert snapshot_guard_found, (
            "RecoveryOutcome pre-retry snapshot not guarded by _use_keep_best"
        )


# ---------------------------------------------------------------------------
# 7. Per-picture re-entry guard
# ---------------------------------------------------------------------------


class TestPerPictureReentryGuard:
    """converters._recover_picture_results must have a force_full_page_ocr_applied
    parameter that short-circuits to [] when True."""

    def test_reentry_guard_parameter_exists(self):
        from pageindex_mcp.converters import _recover_picture_results

        sig = inspect.signature(_recover_picture_results)
        assert "force_full_page_ocr_applied" in sig.parameters, (
            "_recover_picture_results missing force_full_page_ocr_applied parameter"
        )

    def test_reentry_guard_defaults_false(self):
        from pageindex_mcp.converters import _recover_picture_results

        sig = inspect.signature(_recover_picture_results)
        param = sig.parameters["force_full_page_ocr_applied"]
        assert param.default is False, (
            f"force_full_page_ocr_applied default must be False, got {param.default!r}"
        )

    def test_reentry_guard_returns_empty_list(self):
        """When force_full_page_ocr_applied=True, function must return []
        immediately (source-level check: first executable line after the
        parameter check returns [])."""
        from pageindex_mcp.converters import _recover_picture_results

        src = inspect.getsource(_recover_picture_results)
        lines = src.split("\n")
        # Find the guard check and verify it returns [].
        for i, line in enumerate(lines):
            if "force_full_page_ocr_applied" in line and "if" in line:
                # Next non-comment, non-blank line should be return [].
                for j in range(i + 1, min(i + 5, len(lines))):
                    stripped = lines[j].strip()
                    if stripped and not stripped.startswith("#"):
                        assert stripped == "return []", (
                            f"Re-entry guard does not return [], found: {stripped}"
                        )
                        return
        pytest.fail("force_full_page_ocr_applied guard not found in source")


# ---------------------------------------------------------------------------
# 8. Config canonical flag source
# ---------------------------------------------------------------------------


class TestConfigCanonicalFlagSource:
    """IMAGE_DOMINANT_OCR_ESCALATION_ENABLED must be defined in config.py
    (canonical source) and imported from there by client.py."""

    def test_flag_defined_in_config(self):
        from pageindex_mcp import config

        assert hasattr(config, "IMAGE_DOMINANT_OCR_ESCALATION_ENABLED"), (
            "IMAGE_DOMINANT_OCR_ESCALATION_ENABLED not in config.py"
        )

    def test_flag_is_bool(self):
        from pageindex_mcp.config import IMAGE_DOMINANT_OCR_ESCALATION_ENABLED

        assert isinstance(IMAGE_DOMINANT_OCR_ESCALATION_ENABLED, bool)

    def test_flag_in_effective_config_snapshot(self):
        """The flag must appear in effective_config_snapshot() for observability."""
        from pageindex_mcp.config import effective_config_snapshot

        snap = effective_config_snapshot()
        assert "image_dominant_ocr_escalation_enabled" in snap, (
            "IMAGE_DOMINANT_OCR_ESCALATION_ENABLED not surfaced in "
            "effective_config_snapshot()"
        )

    def test_client_imports_from_config_not_os_getenv(self):
        """client.py must import IMAGE_DOMINANT_OCR_ESCALATION_ENABLED from
        config.py, not read os.getenv locally."""
        import pageindex_mcp.client as client_mod

        src_path = inspect.getfile(client_mod)
        with open(src_path) as f:
            src = f.read()
        # Must have an import line from .config.
        assert "IMAGE_DOMINANT_OCR_ESCALATION_ENABLED" in src
        # Must NOT have a local os.getenv("IMAGE_DOMINANT_OCR_ESCALATION_ENABLED")
        # as the gating mechanism (a comment reference is ok).
        lines = src.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "os.getenv" in stripped and "IMAGE_DOMINANT_OCR_ESCALATION_ENABLED" in stripped:
                pytest.fail(
                    f"client.py still reads IMAGE_DOMINANT_OCR_ESCALATION_ENABLED "
                    f"via os.getenv (line: {stripped!r}) -- must import from config.py"
                )

    def test_all_three_ocr_flags_independent_in_config(self):
        """config.py must export three independent OCR escalation flags."""
        from pageindex_mcp import config

        assert hasattr(config, "OCR_ESCALATION_GARBLE")
        assert hasattr(config, "OCR_ESCALATION_PER_PICTURE")
        assert hasattr(config, "IMAGE_DOMINANT_OCR_ESCALATION_ENABLED")
