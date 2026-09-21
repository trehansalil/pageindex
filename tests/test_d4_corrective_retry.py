"""D4 (RFC-046): tests for image recovery eligibility and corrective retry."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp.client.images import _IMAGE_EXTS
from pageindex_mcp.helpers import (
    Candidate,
    ExtractionState,
    TreeDefect,
    arbitrate,
)
from pageindex_mcp.helpers.gates import _all_defects

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp"


# ---------------------------------------------------------------------------
# 6.1: Image recovery eligibility — ext guards widened
# ---------------------------------------------------------------------------


class TestImageRecoveryEligibility:
    """Task 6.1: recovery methods must accept _IMAGE_EXTS, not only .pdf."""

    _RECOVERY_FILE = SRC_ROOT / "client" / "recovery.py"

    def _method_source(self, method_name: str) -> str:
        """Return the source of a single method from recovery.py."""
        tree = ast.parse(self._RECOVERY_FILE.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == method_name:
                    return ast.get_source_segment(
                        self._RECOVERY_FILE.read_text(), node
                    )
        raise LookupError(f"{method_name} not found in recovery.py")

    @pytest.mark.parametrize(
        "method",
        [
            "_recover_garble_ocr",
            "_recover_low_content_ocr",
            "_recover_image_dominant_ocr",
        ],
    )
    def test_ocr_recovery_accepts_image_exts(self, method: str):
        """Each OCR escalation method must reference _IMAGE_EXTS in its guard."""
        src = self._method_source(method)
        assert "_IMAGE_EXTS" in src, (
            f"{method} does not reference _IMAGE_EXTS — "
            "image inputs will be silently skipped"
        )

    def test_rtl_repair_accepts_image_exts(self):
        src = self._method_source("_recover_rtl_repair")
        assert "_IMAGE_EXTS" in src

    def test_vlm_fallback_accepts_image_exts(self):
        src = self._method_source("_recover_vlm_fallback")
        assert "_IMAGE_EXTS" in src

    def test_execute_ocr_retry_has_image_dispatch(self):
        """_execute_ocr_retry must have an image-specific OCR dispatch path."""
        src = self._method_source("_execute_ocr_retry")
        assert "image_to_markdown" in src, (
            "_execute_ocr_retry lacks image_to_markdown dispatch — "
            "image inputs will hit the PDF-only converter path"
        )
        assert "image_tesseract" in src, (
            "_execute_ocr_retry lacks image_tesseract decision choice"
        )

    def test_image_exts_imported_in_recovery(self):
        """recovery.py must import _IMAGE_EXTS."""
        src = self._RECOVERY_FILE.read_text()
        assert "from .images import _IMAGE_EXTS" in src


# ---------------------------------------------------------------------------
# 6.2: Corrective retry — content-derived lang detection
# ---------------------------------------------------------------------------


class TestCorrectiveRetry:
    """Task 6.2: bounded detect-correct-retry in image path."""

    _INDEXER_FILE = SRC_ROOT / "client" / "indexer.py"

    def test_corrective_retry_event_in_indexer(self):
        """The d4_corrective_retry decision event must appear in indexer.py."""
        src = self._INDEXER_FILE.read_text()
        assert "d4_corrective_retry" in src

    def test_corrective_retry_uses_arbitrate(self):
        """The corrective path must use arbitrate() not ad-hoc comparison."""
        src = self._INDEXER_FILE.read_text()
        assert "arbitrate(" in src

    def test_arbitrate_corrective_prefers_clean(self):
        """When filename-derived OCR is garbled but corrective is clean,
        arbitrate picks the corrective."""
        garbled = Candidate(
            label="filename_derived",
            text="junk " * 100,
            char_count=500,
            garbled=True,
            engine="tesseract",
        )
        clean = Candidate(
            label="corrective_retry",
            text="مرحبا " * 100,
            char_count=600,
            garbled=False,
            engine="tesseract",
        )
        winner = arbitrate([garbled, clean])
        assert winner == 1, "corrective should win when it is not garbled"

    def test_arbitrate_keeps_original_when_both_clean(self):
        """When both are clean, the original (index 0) wins on tie-break."""
        original = Candidate(
            label="filename_derived",
            text="hello " * 100,
            char_count=600,
            garbled=False,
            engine="tesseract",
        )
        corrective = Candidate(
            label="corrective_retry",
            text="world " * 100,
            char_count=600,
            garbled=False,
            engine="tesseract",
        )
        winner = arbitrate([original, corrective])
        assert winner == 0, "original should win when both are clean"


# ---------------------------------------------------------------------------
# 6.3: Decision point registration
# ---------------------------------------------------------------------------


class TestD4DecisionPoints:
    """Task 6.3: decision points are registered."""

    def test_d4_corrective_retry_registered(self):
        from pageindex_mcp.obs.decision_points import point_for

        pt = point_for("d4_corrective_retry")
        assert pt is not None
        assert "corrective_retry" in pt.choices
        assert "skip_langs_match" in pt.choices

    def test_ocr_retry_dispatch_includes_image(self):
        from pageindex_mcp.obs.decision_points import point_for

        pt = point_for("ocr_retry_dispatch_route")
        assert "image_tesseract" in pt.choices


# ---------------------------------------------------------------------------
# Architecture guard conformance
# ---------------------------------------------------------------------------


class TestD4ArchitectureGuards:
    """Verify that widened methods still satisfy architecture invariants."""

    _RECOVERY_FILE = SRC_ROOT / "client" / "recovery.py"

    def _ast_tree(self) -> ast.Module:
        return ast.parse(self._RECOVERY_FILE.read_text())

    def _methods_calling_execute_ocr_retry(self) -> set[str]:
        """Discover all RecoveryMixin methods that call _execute_ocr_retry."""
        tree = self._ast_tree()
        callers = set()
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef) or cls.name != "RecoveryMixin":
                continue
            for method in cls.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(method):
                    if (
                        isinstance(node, ast.Attribute)
                        and node.attr == "_execute_ocr_retry"
                    ):
                        callers.add(method.name)
        return callers

    def test_all_ocr_retry_callers_have_full_page_guard(self):
        """Every method calling _execute_ocr_retry must check
        state.full_page_already_applied before the call."""
        tree = self._ast_tree()
        src = self._RECOVERY_FILE.read_text()
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef) or cls.name != "RecoveryMixin":
                continue
            for method in cls.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                has_retry = False
                retry_line = None
                for node in ast.walk(method):
                    if (
                        isinstance(node, ast.Attribute)
                        and node.attr == "_execute_ocr_retry"
                    ):
                        has_retry = True
                        retry_line = node.lineno
                if not has_retry:
                    continue
                # Check full_page_already_applied guard appears before retry call
                guard_line = None
                for node in ast.walk(method):
                    if isinstance(node, ast.If):
                        if_src = ast.get_source_segment(src, node)
                        if if_src and "full_page_already_applied" in if_src:
                            guard_line = node.lineno
                            break
                assert guard_line is not None and guard_line < retry_line, (
                    f"{method.name}: full_page_already_applied guard "
                    f"missing or after _execute_ocr_retry call"
                )

    def test_known_ocr_retry_callers_subset(self):
        """Sanity: known callers are a subset of discovered callers."""
        callers = self._methods_calling_execute_ocr_retry()
        expected = {
            "_recover_garble_ocr",
            "_recover_low_content_ocr",
            "_recover_image_dominant_ocr",
        }
        assert expected <= callers, f"Missing expected callers: {expected - callers}"
