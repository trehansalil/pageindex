"""Zone-2 OCR recovery contract tests.

Validates the Zone-2 OCR recovery pipeline after Zone-1 refactor:
1. Per-picture re-entry guard in converters._recover_picture_results
2. Config canonical flag source (IMAGE_DOMINANT_OCR_ESCALATION_ENABLED)
3. ExtractionState.full_page_already_applied flag regression
4. _execute_ocr_retry stamps full_page_already_applied on success path

NOTE: OcrRetryReason was deleted by Zone-1.  Tests now target the
GateSpec-driven recovery API (_execute_ocr_retry, _recover_garble_ocr,
_recover_low_content_ocr, _recover_image_dominant_ocr).
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from pageindex_mcp.helpers import (
    ExtractionState,
    RecoveryOutcome,
    Route,
    TreeDefect,
)


# ---------------------------------------------------------------------------
# 1. _execute_ocr_retry stamps full_page_already_applied (Zone-1 shared helper)
# ---------------------------------------------------------------------------


class TestExecuteOcrRetryStampsFlag:
    """state.full_page_already_applied = True must be set inside
    _execute_ocr_retry after OCR dispatch."""

    def test_stamp_present(self):
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._execute_ocr_retry)
        assert "state.full_page_already_applied = True" in src

    def test_stamp_not_in_except_block(self):
        """The stamp must NOT be in an except handler."""
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._execute_ocr_retry)
        tree = ast.parse(textwrap.dedent(src))
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    handler_src = ast.get_source_segment(textwrap.dedent(src), handler)
                    if handler_src and "state.full_page_already_applied = True" in handler_src:
                        pytest.fail(
                            "full_page_already_applied stamp in except block"
                        )

    def test_three_recovery_methods_use_shared_execute(self):
        """All three split recovery methods (_recover_garble_ocr,
        _recover_low_content_ocr, _recover_image_dominant_ocr) must call
        _execute_ocr_retry."""
        from pageindex_mcp.client import CustomPageIndexClient

        for method_name in (
            "_recover_garble_ocr",
            "_recover_low_content_ocr",
            "_recover_image_dominant_ocr",
        ):
            method = getattr(CustomPageIndexClient, method_name)
            src = inspect.getsource(method)
            assert "_execute_ocr_retry" in src, (
                f"{method_name} does not call _execute_ocr_retry"
            )


# ---------------------------------------------------------------------------
# 2. Unconditional rtl_decision clear (via _execute_ocr_retry)
# ---------------------------------------------------------------------------


class TestUnconditionalRtlDecisionClear:
    """rtl_decision must be cleared on every recovery path, not just
    the remote+renormalize branch."""

    def test_rtl_decision_cleared(self):
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._execute_ocr_retry)
        assert "state.rtl_decision = None" in src, (
            "rtl_decision not unconditionally cleared in _execute_ocr_retry"
        )

    def test_renormalize_called(self):
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._execute_ocr_retry)
        assert "_renormalize_bidi_guarded" in src, (
            "_renormalize_bidi_guarded not called in _execute_ocr_retry"
        )


# ---------------------------------------------------------------------------
# 3. Per-picture re-entry guard (REGRESSION from original Zone-2 tests)
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
        for i, line in enumerate(lines):
            if "force_full_page_ocr_applied" in line and "if" in line:
                for j in range(i + 1, min(i + 5, len(lines))):
                    stripped = lines[j].strip()
                    if stripped and not stripped.startswith("#"):
                        assert stripped == "return []", (
                            f"Re-entry guard does not return [], found: {stripped}"
                        )
                        return
        pytest.fail("force_full_page_ocr_applied guard not found in source")


# ---------------------------------------------------------------------------
# 4. ExtractionState.full_page_already_applied regression
# ---------------------------------------------------------------------------


class TestFullPageAlreadyAppliedRegression:
    """ExtractionState.full_page_already_applied must propagate correctly."""

    def test_field_exists_and_defaults_false(self):
        import dataclasses

        fields = {f.name: f for f in dataclasses.fields(ExtractionState)}
        assert "full_page_already_applied" in fields
        assert fields["full_page_already_applied"].default is False

    def test_flag_is_mutable(self):
        """ExtractionState is not frozen; the flag must be settable."""
        state = ExtractionState(
            result={},
            ok=True,
            reason="",
            gate_result=None,
            first_defect=TreeDefect.OK,
            route=Route.TREE,
            md_content=None,
            tmp_md_path=None,
            pic_results=[],
            used_converter=None,
            total_chars=0,
            extraction_stages_captured=[],
        )
        assert state.full_page_already_applied is False
        state.full_page_already_applied = True
        assert state.full_page_already_applied is True

    def test_full_page_applied_causes_none_mode(self):
        """When full_page_already_applied=True, decide_ocr_strategy returns
        NONE even with all triggers active."""
        from pageindex_mcp.picture_plane import OcrMode, decide_ocr_strategy

        result = decide_ocr_strategy(
            ocr_escalation_enabled=True,
            has_image_markers=True,
            force_full_page=True,
            garble_status=True,
            full_page_already_applied=True,
        )
        assert result.mode == OcrMode.NONE


# ---------------------------------------------------------------------------
# 5. Config canonical flag source (REGRESSION from original Zone-2 tests)
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
        assert "IMAGE_DOMINANT_OCR_ESCALATION_ENABLED" in src
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


# ---------------------------------------------------------------------------
# 6. Pre-retry snapshot via RecoveryOutcome
# ---------------------------------------------------------------------------


class TestPreRetrySnapshot:
    """RecoveryOutcome snapshot must be created only when use_keep_best=True
    (GARBLE/LOW_CONTENT), not for IMAGE_DOMINANT."""

    def test_pre_retry_gated_by_use_keep_best(self):
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._execute_ocr_retry)
        lines = src.split("\n")
        snapshot_guard_found = False
        for i, line in enumerate(lines):
            if "use_keep_best" in line and "if" in line:
                for j in range(i + 1, min(i + 15, len(lines))):
                    if "RecoveryOutcome(" in lines[j]:
                        snapshot_guard_found = True
                        break
                if snapshot_guard_found:
                    break
        assert snapshot_guard_found, (
            "RecoveryOutcome pre-retry snapshot not guarded by use_keep_best"
        )
