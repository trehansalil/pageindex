# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""PDF inspector, RFC inspector, and VLM fallback tests."""
from __future__ import annotations

import logging
import os
import random
import string
import tempfile
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import filler_text

pytest.importorskip("fitz")
import fitz

from pageindex_mcp import converters
from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.client import images as _img
from pageindex_mcp.client import indexer as _idx
from pageindex_mcp.client import recovery as _rec
from pageindex_mcp.converters import (
    _RFC029_TABLE_MIN_COLLAPSE_COLS,
    _landscape_pages_below_threshold,
    _landscape_rasterize_rotate_reextract,
    _repair_docling_tables,
    _tag_landscape_pages_for_fallback,
)
from pageindex_mcp.helpers import GarbleReport, LowQualityTreeError, classify_verdict
from pageindex_mcp.helpers.gates import TreeDefect, TreeSignals
from pageindex_mcp.helpers.types import TreeGateResult


def _make_client():
    return CustomPageIndexClient(api_key="test-key")


# --- from test_pdf_inspector.py ---

# ---------------------------------------------------------------------------
# Fixtures: mock pdf-inspector result objects
# ---------------------------------------------------------------------------


@dataclass
class FakePdfResult:
    pdf_type: str = "text_based"
    confidence: float = 0.98
    pages_needing_ocr: list = field(default_factory=list)
    has_encoding_issues: bool = False


@dataclass
class FakeScannedResult:
    pdf_type: str = "scanned"
    confidence: float = 0.91
    pages_needing_ocr: list = field(default_factory=lambda: [1, 2, 3])
    has_encoding_issues: bool = False


@dataclass
class FakeMixedResult:
    pdf_type: str = "mixed"
    confidence: float = 0.85
    pages_needing_ocr: list = field(default_factory=lambda: [5, 12, 18])
    has_encoding_issues: bool = True


def _make_pdfium_mock(page_count: int):
    """Build a mock that works with ``pypdfium2.PdfDocument(path)``.

    RFC-034 D4: ``probe_conversion_route`` reads page count via pypdfium2
    (BSD-3/Apache-2) rather than fitz (AGPL-3.0), unconditionally.
    """
    mock_doc = MagicMock()
    mock_doc.__len__ = MagicMock(return_value=page_count)
    mock_pdfium = MagicMock()
    mock_pdfium.PdfDocument.return_value = mock_doc
    return mock_pdfium


# ---------------------------------------------------------------------------
# 1. probe_conversion_route: classification present when pdf-inspector available
# ---------------------------------------------------------------------------


class TestProbeWithPdfInspector:
    def test_text_based_classification_returned(self, tmp_path):
        pdf_path = str(tmp_path / "test.pdf")
        (tmp_path / "test.pdf").write_bytes(b"%PDF-1.4 fake")

        with (
            patch("pageindex_mcp.converters.docling_conv._pdf_inspector_available", True),
            patch(
                "pageindex_mcp.converters.docling_conv._detect_pdf", return_value=FakePdfResult()
            ),
            patch.dict("sys.modules", {"pypdfium2": _make_pdfium_mock(5)}),
            patch("pageindex_mcp.config.MAX_DOCLING_PAGES", 150),
        ):
            from pageindex_mcp.converters import probe_conversion_route

            chunk_count, is_docling, classification = probe_conversion_route(pdf_path)

        assert chunk_count == 1
        assert is_docling is True
        assert classification is not None
        assert classification["pdf_type"] == "text_based"
        assert classification["confidence"] == 0.98
        assert classification["pages_needing_ocr"] == []
        assert classification["has_encoding_issues"] is False

    def test_mixed_classification_with_encoding_issues(self, tmp_path):
        pdf_path = str(tmp_path / "mixed.pdf")
        (tmp_path / "mixed.pdf").write_bytes(b"%PDF-1.4 fake")

        with (
            patch("pageindex_mcp.converters.docling_conv._pdf_inspector_available", True),
            patch(
                "pageindex_mcp.converters.docling_conv._detect_pdf", return_value=FakeMixedResult()
            ),
            patch.dict("sys.modules", {"pypdfium2": _make_pdfium_mock(20)}),
            patch("pageindex_mcp.config.MAX_DOCLING_PAGES", 150),
        ):
            from pageindex_mcp.converters import probe_conversion_route

            _, _, classification = probe_conversion_route(pdf_path)

        assert classification["pdf_type"] == "mixed"
        assert classification["has_encoding_issues"] is True
        assert classification["pages_needing_ocr"] == [5, 12, 18]


# ---------------------------------------------------------------------------
# 2. probe_conversion_route: graceful degradation without pdf-inspector
# ---------------------------------------------------------------------------


class TestProbeWithoutPdfInspector:
    def test_non_pdf_returns_none_classification(self):
        from pageindex_mcp.converters import probe_conversion_route

        chunk_count, is_docling, classification = probe_conversion_route("readme.md")
        assert chunk_count == 1
        assert is_docling is False
        assert classification is None


# ---------------------------------------------------------------------------
# 3. Shadow mode: routing unchanged regardless of classification
# ---------------------------------------------------------------------------


class TestShadowModeRouting:
    def test_scanned_pdf_still_routes_to_docling(self, tmp_path):
        pdf_path = str(tmp_path / "scan.pdf")
        (tmp_path / "scan.pdf").write_bytes(b"%PDF-1.4 fake")

        with (
            patch("pageindex_mcp.converters.docling_conv._pdf_inspector_available", True),
            patch(
                "pageindex_mcp.converters.docling_conv._detect_pdf",
                return_value=FakeScannedResult(),
            ),
            patch.dict("sys.modules", {"pypdfium2": _make_pdfium_mock(10)}),
            patch("pageindex_mcp.config.MAX_DOCLING_PAGES", 150),
        ):
            from pageindex_mcp.converters import probe_conversion_route

            chunk_count, is_docling, _ = probe_conversion_route(pdf_path)

        assert chunk_count == 1
        assert is_docling is True


# ---------------------------------------------------------------------------
# 4. Handshake emission includes classification
# ---------------------------------------------------------------------------


class TestHandshakeEmission:
    def test_handshake_includes_classification_fields(self):
        classification = {
            "pdf_type": "text_based",
            "confidence": 0.98,
            "pages_needing_ocr": [],
            "has_encoding_issues": False,
        }
        handshake = {
            "handshake": True,
            "chunk_count": 1,
            "is_docling_route": True,
        }
        if classification is not None:
            handshake["pdf_classification"] = classification

        assert handshake["pdf_classification"]["pdf_type"] == "text_based"
        assert handshake["pdf_classification"]["confidence"] == 0.98


# ---------------------------------------------------------------------------
# 5. Worker parses classification from handshake
# ---------------------------------------------------------------------------


class TestWorkerHandshakeParsing:
    def test_parses_classification_from_handshake(self):
        handshake = {
            "handshake": True,
            "chunk_count": 1,
            "is_docling_route": True,
            "pdf_classification": {
                "pdf_type": "scanned",
                "confidence": 0.91,
                "pages_needing_ocr": [1, 2, 3],
                "has_encoding_issues": False,
            },
        }
        pdf_class = handshake.get("pdf_classification")
        assert pdf_class is not None
        assert pdf_class["pdf_type"] == "scanned"
        assert pdf_class["confidence"] == 0.91


# ---------------------------------------------------------------------------
# 6. Prometheus metrics
# ---------------------------------------------------------------------------


class TestPdfInspectorMetrics:
    def test_classification_counter_exists(self):
        from pageindex_mcp.metrics import PDF_INSPECTOR_CLASSIFICATIONS

        assert PDF_INSPECTOR_CLASSIFICATIONS is not None

    def test_counter_labels_include_pdf_type(self):
        from pageindex_mcp.metrics import PDF_INSPECTOR_CLASSIFICATIONS

        PDF_INSPECTOR_CLASSIFICATIONS.labels(pdf_type="text_based").inc()
        assert PDF_INSPECTOR_CLASSIFICATIONS.labels(pdf_type="text_based")._value.get() >= 1


# ---------------------------------------------------------------------------
# 7. _run_pdf_inspector unit tests
# ---------------------------------------------------------------------------


class TestRunPdfInspector:
    def test_returns_dict_on_success(self, tmp_path):
        pdf_path = str(tmp_path / "test.pdf")
        (tmp_path / "test.pdf").write_bytes(b"%PDF-1.4 fake")

        with (
            patch("pageindex_mcp.converters.docling_conv._pdf_inspector_available", True),
            patch(
                "pageindex_mcp.converters.docling_conv._detect_pdf",
                return_value=FakePdfResult(),
            ),
        ):
            from pageindex_mcp.converters import _run_pdf_inspector

            result = _run_pdf_inspector(pdf_path)

        assert result is not None
        assert result["pdf_type"] == "text_based"
        assert result["confidence"] == 0.98
        assert isinstance(result["pages_needing_ocr"], list)


# ---------------------------------------------------------------------------
# 8. RFC-032 D1 — inspector_force_ocr decision matrix in client.py::index()
# ---------------------------------------------------------------------------
#
# Covers Task 5.1 (RFC-032 D4): flag on/off, each pdf_type, confidence
# above/below the 0.90 threshold, and classification absent (None), plus
# Task 5.3: the same decision reaching the remote Docling route.


def _pdi_fake_settings(**overrides):
    base = {
        "openai_api_key": "test-key",
        "openai_base_url": "https://api.openai.com/v1",
        "azure_api_version": None,
        "llm_model": "gpt-test",
        "minio_secure": False,
        "minio_endpoint": "localhost:9000",
        "minio_bucket": "pageindex",
        "flat_doc_routing": True,
        "vlm_fallback": False,
        "vlm_model": "gpt-4.1",
    }
    base.update(overrides)
    return SimpleNamespace(**base)



def _pdi_wire_index(monkeypatch, *, preclassify, validate_tree=None):
    """Patch every collaborator client.index() touches on the PDF -> markdown
    route, and capture the args the (single) converter chain entry is called
    with — so we can inspect whether OCR was forced."""
    fake_settings = _pdi_fake_settings()
    monkeypatch.setattr(_idx, "settings", fake_settings)
    monkeypatch.setattr(_rec, "settings", fake_settings)
    # pipeline_config is now the canonical source (indexer.py reads
    # pipeline_config.pdf_inspector_preclassify live rather than importing a
    # frozen module-level constant), so patch the config object itself.
    monkeypatch.setattr(_idx, "pipeline_config", replace(_idx.pipeline_config, pdf_inspector_preclassify=preclassify))
    monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
    monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
    if validate_tree is None:
        validate_tree = lambda structure, **kw: (True, None)  # noqa: E731
    monkeypatch.setattr(_idx, "validate_tree", validate_tree)
    monkeypatch.setattr(_rec, "validate_tree", validate_tree)
    monkeypatch.setattr(_idx, "prepare_tree", lambda structure, **kw: structure)

    conv_fn = MagicMock(return_value="# Heading\n\nBody text\n")
    monkeypatch.setattr(_idx, "pdf_markdown_converters", lambda: [("docling", conv_fn, True)])
    # Fix-3's OCR-escalation retry calls pdf_to_markdown_docling directly
    # (not the chain entry above) — stub it so a garbling verdict can drive
    # the retry path without touching a real Docling conversion.
    monkeypatch.setattr(
        _rec,
        "pdf_to_markdown_docling",
        MagicMock(return_value="# Heading\n\nRecovered text\n"),
    )
    # The retry path also calls ensure_tessdata() before OCR — stub it so tests
    # never probe the real tessdata dir (or download traineddata when
    # TESSDATA_ALLOW_DOWNLOAD=1 is set in the environment).
    monkeypatch.setattr(_idx, "ensure_tessdata", lambda langs: langs)
    monkeypatch.setattr(_rec, "ensure_tessdata", lambda langs: langs)

    mocks = {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(_idx, name, m)

    route_and_extract_flat = MagicMock(
        return_value=("flat_prose", [{"role": "prose", "text": "x"}])
    )
    monkeypatch.setattr(_img, "route_and_extract_flat", route_and_extract_flat)
    mocks["route_and_extract_flat"] = route_and_extract_flat

    idx_metrics = {
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "VLM_FALLBACK_TOTAL": MagicMock(),
        "RAW_UPLOAD_FAILURES": MagicMock(),
        "PDF_PRIMARY_CONVERTER_FAILURES": MagicMock(),
        "PDF_EXTRACT_FALLBACKS": MagicMock(),
        "PDF_INSPECTOR_FORCED_OCR": MagicMock(),
    }
    for name, m in idx_metrics.items():
        monkeypatch.setattr(_idx, name, m)
    mocks.update(idx_metrics)

    ocr_escalation_total = MagicMock()
    monkeypatch.setattr(_rec, "OCR_ESCALATION_TOTAL", ocr_escalation_total)
    monkeypatch.setattr(_rec, "VLM_FALLBACK_TOTAL", mocks["VLM_FALLBACK_TOTAL"])
    monkeypatch.setattr(_img, "LOW_QUALITY_TREES", mocks["LOW_QUALITY_TREES"])
    mocks["OCR_ESCALATION_TOTAL"] = ocr_escalation_total

    mocks["conv_fn"] = conv_fn
    return mocks


async def _pdi_run_index(monkeypatch, pdf_file, *, preclassify, pdf_classification, validate_tree=None):
    mocks = _pdi_wire_index(monkeypatch, preclassify=preclassify, validate_tree=validate_tree)
    c = _make_client()
    monkeypatch.setattr(
        c, "_run_md_to_tree", AsyncMock(return_value={"structure": [], "doc_description": "ok"})
    )
    await c.index(pdf_file, pdf_classification=pdf_classification)
    return mocks


class TestInspectorForceOcrDecisionMatrix:
    async def test_forces_ocr_for_scanned_at_high_confidence(self, monkeypatch, pdf_file):
        mocks = await _pdi_run_index(
            monkeypatch,
            pdf_file,
            preclassify=True,
            pdf_classification={"pdf_type": "scanned", "confidence": 0.95},
        )

        mocks["conv_fn"].assert_called_once()
        assert mocks["conv_fn"].call_args.args == (pdf_file, True)
        assert "ocr_lang_override" in mocks["conv_fn"].call_args.kwargs
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_called_once()

    async def test_confidence_below_threshold_falls_through_to_normal_path(
        self, monkeypatch, pdf_file
    ):
        mocks = await _pdi_run_index(
            monkeypatch,
            pdf_file,
            preclassify=True,
            pdf_classification={"pdf_type": "scanned", "confidence": 0.85},
        )

        # Zone-1: _script_from_filename now returns "Latn" for eng/deu filenames
        mocks["conv_fn"].assert_called_once_with(pdf_file, expected_script="Latn")
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_not_called()

    async def test_missing_confidence_key_does_not_force_ocr(self, monkeypatch, pdf_file):
        """A classification dict without ``confidence`` defaults to 0 -> no force."""
        mocks = await _pdi_run_index(
            monkeypatch,
            pdf_file,
            preclassify=True,
            pdf_classification={"pdf_type": "scanned"},
        )

        # Zone-1: _script_from_filename now returns "Latn" for eng/deu filenames
        mocks["conv_fn"].assert_called_once_with(pdf_file, expected_script="Latn")
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_not_called()

    async def test_preclassify_flag_off_ignores_classification(self, monkeypatch, pdf_file):
        mocks = await _pdi_run_index(
            monkeypatch,
            pdf_file,
            preclassify=False,
            pdf_classification={"pdf_type": "scanned", "confidence": 1.0},
        )

        # Zone-1: _script_from_filename now returns "Latn" for eng/deu filenames
        mocks["conv_fn"].assert_called_once_with(pdf_file, expected_script="Latn")
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_not_called()


class TestSafetyNetsIntactAfterInspectorForcedOcr:
    """Task 5.2 (RFC-032 D4): validate_tree() and the Fix-3 OCR-retry escalation
    are unconditional safety nets — they must still run/fire exactly as before
    even when pdf-inspector already forced full-page OCR on the first pass."""

    async def test_fix3_retry_fires_after_forced_ocr_when_validate_tree_flags_garbling(
        self, monkeypatch, pdf_file
    ):
        validate = MagicMock(side_effect=[(False, "garbling"), (True, None)])
        mocks = await _pdi_run_index(
            monkeypatch,
            pdf_file,
            preclassify=True,
            pdf_classification={"pdf_type": "scanned", "confidence": 0.95},
            validate_tree=validate,
        )

        assert validate.call_count == 2
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_called_once()
        mocks["OCR_ESCALATION_TOTAL"].labels.assert_called_once_with(result="recovered")
        mocks["save_doc"].assert_called_once()
        mocks["save_flat_doc"].assert_not_called()


class TestInspectorForcedOcrOnRemoteDoclingRoute:
    """Task 5.3 (RFC-032 D4 / Design AD3) — remote-path counterpart of the
    decision matrix above.

    Task 3.1 wired ``inspector_force_ocr`` into BOTH converter-loop branches,
    but the tests above only exercise the local ``conv_fn`` branch
    (``_use_remote`` is False because ``_fake_settings()`` has no
    ``docling_service_url``). These cover the remote
    ``_remote_pdf_to_markdown()`` branch, proving the D2 wiring is symmetric."""

    @staticmethod
    def _wire_remote(monkeypatch, *, preclassify=True):
        mocks = _pdi_wire_index(monkeypatch, preclassify=preclassify)
        remote_settings = _pdi_fake_settings(docling_service_url="http://docling.test")
        monkeypatch.setattr(_idx, "settings", remote_settings)
        monkeypatch.setattr(_rec, "settings", remote_settings)
        remote = AsyncMock(return_value=("# Heading\n\nBody text\n", []))
        monkeypatch.setattr(_idx, "_remote_pdf_to_markdown", remote)
        mocks["remote"] = remote
        return mocks

    async def _run(self, monkeypatch, pdf_file, *, pdf_classification, preclassify=True):
        mocks = self._wire_remote(monkeypatch, preclassify=preclassify)
        c = _make_client()
        c._staging_key = "uploads/doc.pdf"
        monkeypatch.setattr(
            c, "_run_md_to_tree", AsyncMock(return_value={"structure": [], "doc_description": "ok"})
        )
        await c.index(pdf_file, pdf_classification=pdf_classification)
        return mocks

    async def test_remote_route_forces_full_page_ocr_for_scanned(self, monkeypatch, pdf_file):
        mocks = await self._run(
            monkeypatch, pdf_file, pdf_classification={"pdf_type": "scanned", "confidence": 0.95}
        )

        mocks["remote"].assert_awaited_once()
        assert mocks["remote"].await_args.kwargs["force_full_page_ocr"] is True
        assert mocks["remote"].await_args.kwargs["ocr_lang_override"]
        mocks["conv_fn"].assert_not_called()
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_called_once()


# --- from test_rfc_inspector.py ---

_THRESHOLD = _RFC029_TABLE_MIN_COLLAPSE_COLS
_TRIALS = 60
_SEED = 20260810


# ---------------------------------------------------------------------------
# D0: _repair_docling_tables separator guard
# ---------------------------------------------------------------------------


def _collapsed_rows_logged(caplog) -> int:
    for record in caplog.records:
        message = record.getMessage()
        if "table_repair" in message and "collapsed_rows=" in message:
            marker = "collapsed_rows="
            start = message.index(marker) + len(marker)
            end = message.index(",", start)
            return int(message[start:end])
    raise AssertionError("no table_repair log record found")


class TestTableRepairSeparatorGuard:
    def test_first_post_separator_degenerate_row_is_preserved(self, caplog):
        """Row immediately after separator with all-identical cells (count >
        threshold) is a Docling repeated-label first body row, not a merge
        artefact -- must be preserved in normalized minimal-padding form and
        collapsed_rows must be 0."""
        md = (
            "| A | B | C | D |\n"
            "| --- | --- | --- | --- |\n"
            "| Fee     | Fee     | Fee     | Fee     |\n"
        )
        with caplog.at_level(logging.INFO):
            out = _repair_docling_tables(md, "cabinet_resolution_no_21.pdf")
        lines = out.strip().split("\n")
        assert lines[-1] == "| Fee | Fee | Fee | Fee |"
        assert "| Fee |" not in lines
        assert _collapsed_rows_logged(caplog) == 0

    def test_guard_scope_is_limited_to_a_single_first_row(self):
        """Scope-limitation verification, combining two related scenarios:

        (a) when the first AND second post-separator rows are both
            degenerate, only the first is guarded -- the second is
            collapsed (the guard shields a single row only);
        (b) the prev_was_separator flag resets to False after the first
            post-separator row is processed -- a degenerate row at
            position 3+ (after a normal row) must still be collapsed.
        """
        md_two_consecutive = (
            "| A | B | C | D |\n"
            "| --- | --- | --- | --- |\n"
            "| Fee | Fee | Fee | Fee |\n"
            "| dup | dup | dup | dup |\n"
        )
        out = _repair_docling_tables(md_two_consecutive, "cabinet_resolution_no_21.pdf")
        lines = out.strip().split("\n")
        assert "| Fee | Fee | Fee | Fee |" in lines
        assert lines[-1] == "| dup |"
        assert "| dup | dup | dup | dup |" not in out

        md_flag_reset = (
            "| A | B | C | D |\n"
            "| --- | --- | --- | --- |\n"
            "| Fee | Fee | Fee | Fee |\n"
            "| w | x | y | z |\n"
            "| dup | dup | dup | dup |\n"
        )
        out = _repair_docling_tables(md_flag_reset, "cabinet_resolution_no_21.pdf")
        lines = out.strip().split("\n")
        assert "| Fee | Fee | Fee | Fee |" in lines
        assert "| w | x | y | z |" in lines
        assert lines[-1] == "| dup |"
        assert "| dup | dup | dup | dup |" not in out

    def test_collapse_requires_all_three_conditions_simultaneously(self):
        """Generalized property test: collapse fires on a row iff (a) every
        cell is byte-identical, (b) cell count exceeds the collapse
        threshold, AND (c) the row does not immediately follow a separator
        row."""

        def _random_word(rng: random.Random) -> str:
            return "".join(rng.choices(string.ascii_lowercase, k=rng.randint(1, 6)))

        def _random_row(rng: random.Random, num_cols: int, identical: bool) -> list[str]:
            if identical:
                word = _random_word(rng)
                return [word] * num_cols
            cells = [_random_word(rng) for _ in range(num_cols)]
            if len(set(cells)) == 1:
                cells[0] = cells[0] + "x"
            return cells

        def _build_table(target_row: list[str], first_post_separator: bool) -> str:
            header = ["h" + str(i) for i in range(len(target_row))]
            lines = ["| " + " | ".join(header) + " |"]
            lines.append("| " + " | ".join("---" for _ in header) + " |")
            if not first_post_separator:
                filler = ["f" + str(i) for i in range(len(target_row))]
                lines.append("| " + " | ".join(filler) + " |")
            lines.append("| " + " | ".join(target_row) + " |")
            return "\n".join(lines) + "\n"

        rng = random.Random(_SEED)

        for _ in range(_TRIALS):
            num_cols = rng.randint(2, 8)
            identical = rng.choice([True, False])
            first_post_separator = rng.choice([True, False])

            target_row = _random_row(rng, num_cols, identical)
            md = _build_table(target_row, first_post_separator)
            out = _repair_docling_tables(md, "prop.pdf")

            all_identical = len(set(target_row)) == 1
            over_threshold = num_cols > _THRESHOLD
            should_collapse = all_identical and over_threshold and not first_post_separator

            collapsed_line = "| " + target_row[0] + " |"
            full_line = "| " + " | ".join(target_row) + " |"

            if should_collapse:
                assert collapsed_line in out.split("\n"), (
                    f"expected collapse: cols={num_cols} identical={identical} "
                    f"first_post_sep={first_post_separator}\n{md}\n---\n{out}"
                )
                assert full_line not in out.split("\n")
            else:
                assert full_line in out.split("\n"), (
                    f"expected preservation: cols={num_cols} identical={identical} "
                    f"first_post_sep={first_post_separator}\n{md}\n---\n{out}"
                )


# ---------------------------------------------------------------------------
# D1: inspector_class threading through classify_verdict
# ---------------------------------------------------------------------------


def _flat_leaf_tree(chars_per_leaf: list[int]) -> list[dict]:
    """A flat (depth == 1) sibling tree with one leaf per entry in
    ``chars_per_leaf``, using prose-shaped filler so improved garble
    detection does not flag test fixtures."""
    return [
        {"node_id": str(i), "title": "", "text": filler_text(n, i), "nodes": []}
        for i, n in enumerate(chars_per_leaf)
    ]


class TestInspectorClassThreading:
    def test_empty_content_class_text_based_inspector_promotes_cat_c(self):
        """content_class='', inspector_class='text_based': leaf_concentration
        0.20 exceeds the default cat_c threshold (0.17) but clears the
        widened 0.204 (0.17 * 1.2) threshold -- promote cat_c_promoted."""
        structure = _flat_leaf_tree([20, 20, 20, 20, 20])
        verdict, reason = classify_verdict(structure, "", None, inspector_class="text_based")
        assert verdict == "PASS"
        assert reason in ("structural_pass", "cat_c_promoted")

    def test_flat_mixed_content_class_takes_precedence_over_inspector_class(self):
        """content_class='flat_mixed' with inspector_class='text_based':
        content_class remains the sole branch selector, so this takes the
        flat_/cat_b branch (not cat_c) regardless of inspector_class."""
        structure = _flat_leaf_tree([60] * 10)
        verdict, reason = classify_verdict(
            structure, "flat_mixed", None, inspector_class="text_based"
        )
        assert verdict == "PASS"
        assert reason in ("structural_pass", "cat_b_promoted")

    def test_empty_content_class_cat_c_threshold_boundary(self):
        """Positive and negative boundary checks combined, both with
        inspector_class=None (omitted default) -- pre-D1 behavior:

        - leaf_concentration ~0.143 clears the unwidened default 0.17
          cat_c threshold (backward compat, promoted);
        - leaf_concentration 0.20 (above 0.17, below the widened 0.204)
          must NOT promote -- proves D1 *widens* the threshold
          conditionally rather than raising it unconditionally.
        """
        structure_below = _flat_leaf_tree([20] * 7)
        verdict, reason = classify_verdict(structure_below, "", None)
        assert verdict == "PASS"
        assert reason in ("structural_pass", "cat_c_promoted")

        structure_boundary = _flat_leaf_tree([20] * 5)
        sig = TreeSignals.from_tree(structure_boundary, garble_threshold=0.15)
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.DEPTH_LOW,
            detail="depth=1",
            signals=sig,
            all_defects=frozenset({TreeDefect.DEPTH_LOW}),
        )
        verdict, reason = classify_verdict(structure_boundary, "", gate)
        assert (verdict, reason) == ("MARGINAL", "depth=1")


class TestInspectorClassPrecedenceProperty:
    """Property-based test (D1): random (content_class, inspector_class)
    pairs -- content_class always takes routing precedence; inspector_class
    only influences the cat_c branch."""

    _CONTENT_CLASSES = ["ocr_scanned", "ocr_image", "flat_prose", "flat_mixed"]
    _INSPECTOR_CLASSES = [None, "", "text_based", "scanned", "image_based", "zzz_bogus"]

    def test_random_pairs_content_class_precedence(self):
        """For 60 random pairs, on the cat_c-boundary tree (leaf_concentration
        0.20, between 0.17 and 0.204):

        - non-empty content_class: verdict is invariant to inspector_class
          (identical to the inspector_class=None result -- precedence holds);
        - empty content_class: cat_c promotion fires iff
          inspector_class == 'text_based'.
        """
        rng = random.Random(0xD1)
        structure = _flat_leaf_tree([20] * 5)
        sig = TreeSignals.from_tree(structure, garble_threshold=0.15)
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.DEPTH_LOW,
            detail="depth=1",
            signals=sig,
            all_defects=frozenset({TreeDefect.DEPTH_LOW}),
        )
        for _ in range(_TRIALS):
            content_class = rng.choice(["", *self._CONTENT_CLASSES])
            inspector_class = rng.choice(self._INSPECTOR_CLASSES)
            result = classify_verdict(
                structure, content_class, gate, inspector_class=inspector_class
            )
            if content_class:
                baseline = classify_verdict(structure, content_class, gate)
                assert result == baseline, (
                    f"inspector_class={inspector_class!r} changed the verdict "
                    f"for content_class={content_class!r}: {result} != {baseline}"
                )
                assert result[1] != "cat_c_promoted"
            elif inspector_class == "text_based":
                assert result[0] == "PASS", (content_class, inspector_class, result)
                assert result[1] in ("", "cat_c_promoted"), (
                    content_class,
                    inspector_class,
                    result,
                )
            else:
                assert result == ("MARGINAL", "depth=1"), (content_class, inspector_class, result)


# ---------------------------------------------------------------------------
# D2: landscape orientation detection + rasterize-rotate-reextract fallback
# ---------------------------------------------------------------------------


def _make_pdf(tmp_path, name, width, height, rotate=0):
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    if rotate:
        page.set_rotation(rotate)
    path = str(tmp_path / name)
    doc.save(path)
    doc.close()
    return path


class TestOrientationProbe:
    """_tag_landscape_pages_for_fallback correctly tags landscape pages
    (rotated or wide-aspect) and leaves portrait pages untagged."""

    def test_rotated_and_portrait_pages_are_tagged_correctly(self, tmp_path):
        rotated_path = _make_pdf(tmp_path, "rot90.pdf", width=600, height=800, rotate=90)
        rotated_pages = _tag_landscape_pages_for_fallback(rotated_path)
        assert len(rotated_pages) == 1
        assert rotated_pages[0]["is_landscape"] is True
        assert rotated_pages[0]["rotate"] == 90

        portrait_path = _make_pdf(tmp_path, "portrait.pdf", width=600, height=800, rotate=0)
        portrait_pages = _tag_landscape_pages_for_fallback(portrait_path)
        assert len(portrait_pages) == 1
        assert portrait_pages[0]["is_landscape"] is False
        assert portrait_pages[0]["rotate"] == 0


class TestFallbackTriggerSkip:
    """_landscape_pages_below_threshold only flags pages that are BOTH
    landscape-tagged AND below LANDSCAPE_CHAR_THRESHOLD, AND (RFC-036 D0c)
    carry a detectable picture/graphic region."""

    @staticmethod
    def _mock_document(char_count):
        item = SimpleNamespace(text="x" * char_count)
        doc = MagicMock()
        doc.iterate_items.return_value = [(item, 0)]
        return doc

    def test_landscape_page_below_threshold_is_flagged(self, monkeypatch):
        # RFC-036 D0c: a below-threshold landscape page is only flagged when
        # it also carries a detectable picture/graphic region (page 1,
        # 1-indexed) -- otherwise dense numeric-table pages false-positive.
        monkeypatch.setattr(
            converters.pictures, "_collect_picture_regions", lambda doc: [{"page": 1, "bbox": {}}]
        )
        landscape_pages = [{"page_no": 0, "rotate": 0, "is_landscape": True}]
        document = self._mock_document(200)
        below = _landscape_pages_below_threshold(document, landscape_pages)
        assert len(below) == 1
        assert below[0]["page_no"] == 0
        assert below[0]["char_count"] == 200

    def test_landscape_page_below_threshold_without_picture_is_not_flagged(self, monkeypatch):
        # RFC-036 D0c: dense numeric-table pages fall below the char
        # threshold but carry no picture region, so they no longer
        # false-positive trigger the rasterize-rotate-reextract fallback.
        monkeypatch.setattr(converters.pictures, "_collect_picture_regions", lambda doc: [])
        landscape_pages = [{"page_no": 0, "rotate": 0, "is_landscape": True}]
        document = self._mock_document(200)
        below = _landscape_pages_below_threshold(document, landscape_pages)
        assert below == []

    def test_above_threshold_and_portrait_pages_are_not_flagged(self, monkeypatch):
        """Combines two negative-outcome scenarios that share the same
        picture-region mock: a landscape page above the char threshold, and
        a portrait page below it (e.g. a legitimately sparse cover/divider
        page) -- neither must be flagged."""
        monkeypatch.setattr(
            converters.pictures, "_collect_picture_regions", lambda doc: [{"page": 1, "bbox": {}}]
        )

        landscape_above = [{"page_no": 0, "rotate": 0, "is_landscape": True}]
        document_above = self._mock_document(2000)
        assert _landscape_pages_below_threshold(document_above, landscape_above) == []

        portrait_below = [{"page_no": 0, "rotate": 0, "is_landscape": False}]
        document_below = self._mock_document(200)
        assert _landscape_pages_below_threshold(document_below, portrait_below) == []


class TestRasterizationFailureFallthrough:
    """Rasterization failure logs a warning and falls through rather than
    raising."""

    def test_rasterize_failure_falls_through_without_raising(self, tmp_path, monkeypatch, caplog):
        path = _make_pdf(tmp_path, "any.pdf", width=800, height=600, rotate=0)
        monkeypatch.setattr(
            converters.pictures,
            "_rasterize_rotate_page",
            MagicMock(side_effect=RuntimeError("render failed")),
        )
        pages = [{"page_no": 0, "rotate": 0, "is_landscape": True, "char_count": 100}]

        with caplog.at_level("WARNING"):
            result = _landscape_rasterize_rotate_reextract(path, pages)

        assert result == []
        assert any("landscape rasterize/rotate failed" in r.message for r in caplog.records)


def _rfi_fake_settings(**overrides):
    base = {
        "openai_api_key": "test-key",
        "openai_base_url": "https://api.openai.com/v1",
        "azure_api_version": None,
        "llm_model": "gpt-test",
        "minio_secure": False,
        "minio_endpoint": "localhost:9000",
        "minio_bucket": "pageindex",
        "flat_doc_routing": True,
        "vlm_fallback": False,
        "vlm_model": "gpt-4.1",
        "vlm_describe_images": False,
        # HR3: pii_corpus=True + non-ZDR endpoint closes zdr_egress_gate so
        # _generate_flat_doc_description never attempts a real litellm call.
        "pii_corpus": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)



def _rfi_wire_index(monkeypatch, *, pic_results, flat_return):
    fake_settings = _rfi_fake_settings()
    monkeypatch.setattr(_idx, "settings", fake_settings)
    monkeypatch.setattr(_rec, "settings", fake_settings)
    # zdr_egress_gate re-imports settings from .config fresh on every call.
    monkeypatch.setattr("pageindex_mcp.config.settings", fake_settings)
    # pipeline_config is now the canonical source (indexer.py reads
    # pipeline_config.pdf_inspector_preclassify live), so patch the config
    # object rather than a frozen module-level constant.
    monkeypatch.setattr(_idx, "pipeline_config", replace(_idx.pipeline_config, pdf_inspector_preclassify=False))
    monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
    monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
    monkeypatch.setattr(_idx, "validate_tree", lambda structure, **kw: (True, None))
    monkeypatch.setattr(_rec, "validate_tree", lambda structure, **kw: (True, None))
    monkeypatch.setattr(_idx, "prepare_tree", lambda structure, **kw: structure)

    # Large body so the RFC-029 D1 flat-prefer check (which also compares
    # flat vs. tree char counts) does not itself trigger and confound the
    # D2 reroute-on-PictureResults assertion below.
    md_text = "# Heading\n\n" + ("Body paragraph text. " * 300)
    # D3B's flat-path garble gate is orthogonal to the D2 routing decision
    # under test here -- stub it out so repeated filler text in md_text
    # doesn't spuriously trip it.
    _not_garbled = GarbleReport(is_garbled=False, fired_prongs=frozenset())
    monkeypatch.setattr(_idx, "detect_garble", lambda *a, **kw: _not_garbled)
    monkeypatch.setattr(_rec, "detect_garble", lambda *a, **kw: _not_garbled)
    conv_fn = MagicMock(return_value=(md_text, pic_results))
    monkeypatch.setattr(_idx, "pdf_markdown_converters", lambda: [("docling", conv_fn, True)])
    monkeypatch.setattr(_rec, "pdf_to_markdown_docling", MagicMock(return_value=(md_text, [])))
    monkeypatch.setattr(_idx, "ensure_tessdata", lambda langs: langs)
    monkeypatch.setattr(_rec, "ensure_tessdata", lambda langs: langs)

    mocks = {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(_idx, name, m)

    route_and_extract_flat = MagicMock(return_value=flat_return)
    monkeypatch.setattr(_img, "route_and_extract_flat", route_and_extract_flat)
    monkeypatch.setattr(_idx, "route_and_extract_flat", route_and_extract_flat)
    monkeypatch.setattr(_idx, "_garble_check_flat_blocks", lambda *a, **kw: None)
    mocks["route_and_extract_flat"] = route_and_extract_flat

    idx_metrics = {
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "VLM_FALLBACK_TOTAL": MagicMock(),
        "RAW_UPLOAD_FAILURES": MagicMock(),
        "PDF_PRIMARY_CONVERTER_FAILURES": MagicMock(),
        "PDF_EXTRACT_FALLBACKS": MagicMock(),
        "PDF_INSPECTOR_FORCED_OCR": MagicMock(),
    }
    for name, m in idx_metrics.items():
        monkeypatch.setattr(_idx, name, m)
    mocks.update(idx_metrics)

    ocr_escalation_total = MagicMock()
    monkeypatch.setattr(_rec, "OCR_ESCALATION_TOTAL", ocr_escalation_total)
    monkeypatch.setattr(_rec, "VLM_FALLBACK_TOTAL", mocks["VLM_FALLBACK_TOTAL"])
    monkeypatch.setattr(_img, "LOW_QUALITY_TREES", mocks["LOW_QUALITY_TREES"])
    mocks["OCR_ESCALATION_TOTAL"] = ocr_escalation_total

    mocks["conv_fn"] = conv_fn
    return mocks


async def _rfi_run_index(monkeypatch, pdf_file, *, pic_results, flat_return):
    mocks = _rfi_wire_index(monkeypatch, pic_results=pic_results, flat_return=flat_return)
    c = _make_client()
    tree_structure = [
        {
            "title": "Section",
            "text": "x",
            "nodes": [{"title": "Leaf", "text": "y " * 4000}],
        }
    ]
    monkeypatch.setattr(
        c,
        "_run_md_to_tree",
        AsyncMock(return_value={"structure": tree_structure, "doc_description": "ok"}),
    )
    doc_id = await c.index(pdf_file)
    return c, doc_id, mocks


class TestRoutingReevaluationAfterFallbackReextraction:
    """A Phase 2 re-extraction that produces PictureResults (tagged
    skipped_reason='landscape_fallback_picture' by the landscape
    rasterize-rotate-reextract fallback) must re-route the document to the
    flat-mixed path instead of letting it stay on the tree path; a
    re-extraction with NO PictureResults must leave the document on its
    original (tree) routing path."""

    async def test_picture_results_reroutes_to_flat_mixed(self, monkeypatch, pdf_file):
        pic_results = [{"page": 1, "skipped_reason": "landscape_fallback_picture"}]
        c, doc_id, mocks = await _rfi_run_index(
            monkeypatch,
            pdf_file,
            pic_results=pic_results,
            flat_return=("flat_mixed", [{"role": "prose", "text": "chart caption"}]),
        )

        assert isinstance(doc_id, str)
        mocks["save_flat_doc"].assert_called_once()
        mocks["save_doc"].assert_not_called()
        assert c.last_content_class == "flat_mixed"
        mocks["FLAT_DOCS_TOTAL"].labels.assert_called_once_with(content_class="flat_mixed")

    async def test_no_picture_results_stays_on_original_routing_path(self, monkeypatch, pdf_file):
        c, doc_id, mocks = await _rfi_run_index(
            monkeypatch,
            pdf_file,
            pic_results=[],
            flat_return=("flat_prose", [{"role": "prose", "text": "x"}]),
        )

        assert isinstance(doc_id, str)
        mocks["save_doc"].assert_called_once()
        mocks["save_flat_doc"].assert_not_called()
        assert c.last_content_class is None


# --- from test_vlm_fallback.py ---


def _vlm_fake_settings(*, vlm_fallback: bool = True, vlm_model: str = "gpt-4.1-test"):
    return SimpleNamespace(
        openai_api_key="test-key",
        openai_base_url="https://api.openai.com/v1",
        azure_api_version=None,
        llm_model="gpt-test",
        minio_secure=False,
        minio_endpoint="localhost:9000",
        minio_bucket="pageindex",
        flat_doc_routing=True,
        vlm_fallback=vlm_fallback,
        vlm_model=vlm_model,
        vlm_describe_images=False,
        pii_corpus=False,
    )


def _tree_result():
    return {
        "structure": [
            {
                "title": "Root",
                "text": "root text",
                "children": [
                    {"title": "Child", "text": "child text", "children": []},
                ],
            },
        ],
        "doc_description": "test doc",
    }



def _wire_vlm(monkeypatch, *, validate_side_effect, vlm_raises=False, vlm_fallback=True):
    """Wire index() so the garble retry always fails and the VLM path fires."""
    fake_settings = _vlm_fake_settings(vlm_fallback=vlm_fallback)
    monkeypatch.setattr(_idx, "settings", fake_settings)
    monkeypatch.setattr(_rec, "settings", fake_settings)
    monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
    monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
    vt_mock = MagicMock(side_effect=validate_side_effect)
    monkeypatch.setattr(_idx, "validate_tree", vt_mock)
    monkeypatch.setattr(_rec, "validate_tree", vt_mock)
    monkeypatch.setattr(
        _idx,
        "pdf_markdown_converters",
        lambda: [("docling", lambda p, **kw: "# garbled md", True)],
    )
    monkeypatch.setattr(_idx, "prepare_tree", lambda structure, **kw: structure)
    ocr_langs = lambda s: ["eng"]
    tessdata = lambda langs: langs
    monkeypatch.setattr(_idx, "detect_ocr_langs", ocr_langs)
    monkeypatch.setattr(_rec, "detect_ocr_langs", ocr_langs)
    monkeypatch.setattr(_idx, "ensure_tessdata", tessdata)
    monkeypatch.setattr(_rec, "ensure_tessdata", tessdata)
    monkeypatch.setattr(
        _rec, "pdf_to_markdown_docling", lambda path, force, langs, **kw: "# still garbled"
    )

    vlm_mock = AsyncMock()
    if vlm_raises:
        vlm_mock.side_effect = RuntimeError("VLM boom")
    else:
        vlm_mock.return_value = "# VLM recovered heading\n\nSome real content here."

    route_flat = MagicMock(return_value=("flat_prose", [{"role": "prose", "text": "x"}]))
    low_q = MagicMock()
    vlm_total = MagicMock()
    idx_mocks = {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": low_q,
        "VLM_FALLBACK_TOTAL": vlm_total,
        "detect_garble": MagicMock(
            return_value=GarbleReport(is_garbled=False, fired_prongs=frozenset())
        ),
        "_garble_check_flat_blocks": MagicMock(return_value=None),
    }
    for name, m in idx_mocks.items():
        monkeypatch.setattr(_idx, name, m)
    rec_mocks = {
        "OCR_ESCALATION_TOTAL": MagicMock(),
        "VLM_FALLBACK_TOTAL": vlm_total,
        "route_and_extract_flat": route_flat,
        "detect_garble": MagicMock(return_value=GarbleReport(is_garbled=False, fired_prongs=frozenset())),
    }
    for name, m in rec_mocks.items():
        monkeypatch.setattr(_rec, name, m)
    idx_mocks["route_and_extract_flat"] = route_flat

    mocks = {**idx_mocks, **rec_mocks}
    return mocks, vlm_mock


# ---------------------------------------------------------------------------
# VLM-C1: VLM recovers a valid tree from a garble-rejected PDF
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_VLM_C1_recovered(monkeypatch, pdf_file):
    """When VLM markdown passes validate_tree, the doc is persisted as a tree
    and VLM_FALLBACK_TOTAL{result=recovered} is incremented."""
    # validate_tree: 1st call garbled (initial), 2nd garbled (OCR retry),
    # 3rd ok (VLM output)
    mocks, vlm_mock = _wire_vlm(
        monkeypatch,
        validate_side_effect=[
            TreeGateResult(ok=False, defect=TreeDefect.GARBLING),  # initial
            TreeGateResult(ok=False, defect=TreeDefect.GARBLING),  # OCR retry
            TreeGateResult(ok=True, defect=TreeDefect.OK),  # VLM output
        ],
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    with patch("pageindex_mcp.converters.vlm_extract_markdown", vlm_mock):
        doc_id = await c.index(pdf_file)

    assert isinstance(doc_id, str) and len(doc_id) == 36
    mocks["save_doc"].assert_called_once()
    vlm_mock.assert_awaited_once()
    mocks["VLM_FALLBACK_TOTAL"].labels.assert_called_with(result="recovered")
    mocks["VLM_FALLBACK_TOTAL"].labels.return_value.inc.assert_called()


# ---------------------------------------------------------------------------
# VLM-C2: VLM output is also garbled — terminal rejection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_VLM_C2_still_garbled(monkeypatch, pdf_file):
    """Zone-5 update: VLM output still garbled persists with FAIL verdict;
    VLM_FALLBACK_TOTAL{result=still_garbled} is incremented."""
    mocks, vlm_mock = _wire_vlm(
        monkeypatch,
        validate_side_effect=[
            TreeGateResult(ok=False, defect=TreeDefect.GARBLING),  # initial
            TreeGateResult(ok=False, defect=TreeDefect.GARBLING),  # OCR retry
            TreeGateResult(ok=False, defect=TreeDefect.GARBLING),  # VLM output
        ],
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    with patch("pageindex_mcp.converters.vlm_extract_markdown", vlm_mock):
        doc_id = await c.index(pdf_file)

    assert isinstance(doc_id, str) and len(doc_id) == 36
    mocks["VLM_FALLBACK_TOTAL"].labels.assert_called_with(result="still_garbled")


# ---------------------------------------------------------------------------
# VLM-C3: VLM call raises — falls through to terminal rejection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_VLM_C3_error_falls_through(monkeypatch, pdf_file):
    """Zone-5 update: VLM error persists with FAIL verdict;
    VLM_FALLBACK_TOTAL{result=error} is incremented."""
    mocks, vlm_mock = _wire_vlm(
        monkeypatch,
        validate_side_effect=[
            TreeGateResult(ok=False, defect=TreeDefect.GARBLING),  # initial
            TreeGateResult(ok=False, defect=TreeDefect.GARBLING),  # OCR retry
        ],
        vlm_raises=True,
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    with patch("pageindex_mcp.converters.vlm_extract_markdown", vlm_mock):
        doc_id = await c.index(pdf_file)

    assert isinstance(doc_id, str) and len(doc_id) == 36
    mocks["VLM_FALLBACK_TOTAL"].labels.assert_called_with(result="error")


# ---------------------------------------------------------------------------
# VLM-C4: VLM disabled by default — never called
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_VLM_C4_disabled_by_default(monkeypatch, pdf_file):
    """Zone-5 update: VLM disabled, garbling persists with FAIL verdict;
    VLM path is skipped entirely."""
    mocks, vlm_mock = _wire_vlm(
        monkeypatch,
        validate_side_effect=[
            TreeGateResult(ok=False, defect=TreeDefect.GARBLING),  # initial
            TreeGateResult(ok=False, defect=TreeDefect.GARBLING),  # OCR retry
        ],
        vlm_fallback=False,
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    with patch("pageindex_mcp.converters.vlm_extract_markdown", vlm_mock):
        doc_id = await c.index(pdf_file)

    assert isinstance(doc_id, str) and len(doc_id) == 36
    vlm_mock.assert_not_awaited()
    mocks["VLM_FALLBACK_TOTAL"].labels.assert_not_called()


# ---------------------------------------------------------------------------
# VLM-C5: VLM only fires on garbling, not node_count<3 or depth<2
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_VLM_C5_only_fires_on_garbling(monkeypatch, pdf_file):
    """When reason is 'node_count<3' (not garbling), the VLM path is skipped
    even if vlm_fallback=True — it routes to the flat path instead."""
    mocks, vlm_mock = _wire_vlm(
        monkeypatch,
        validate_side_effect=[
            TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW),  # initial — not garbling
        ],
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    with patch("pageindex_mcp.converters.vlm_extract_markdown", vlm_mock):
        doc_id = await c.index(pdf_file)

    assert isinstance(doc_id, str)
    vlm_mock.assert_not_awaited()
    mocks["save_flat_doc"].assert_called_once()


# ---------------------------------------------------------------------------
# VLM-C6: VLM recovers via the flat-path garble gate
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_VLM_C6_flat_path_garble_recovered(monkeypatch, pdf_file):
    """When validate_tree returns node_count<3 and flat text is garbled, VLM
    fires via the flat-path garble gate and recovers a clean flat doc."""
    mocks, vlm_mock = _wire_vlm(
        monkeypatch,
        validate_side_effect=[
            TreeGateResult(
                ok=False, defect=TreeDefect.NODE_COUNT_LOW
            ),  # initial — routes to flat path
        ],
    )
    # detect_garble returns garbled for the original markdown, not-garbled for VLM output.
    # check_garble (VLM recovery path) follows the same pattern.
    from pageindex_mcp.helpers import GarbleReport

    garble_calls = []

    def _fake_detect_garble(text, **kw):
        garble_calls.append(text)
        is_garbled = "VLM recovered" not in text
        return GarbleReport(
            is_garbled=is_garbled,
            fired_prongs=frozenset({"test"}) if is_garbled else frozenset(),
        )

    def _fake_flat_garble(text, **kw):
        is_garbled = "VLM recovered" not in text
        return GarbleReport(is_garbled=is_garbled, fired_prongs=frozenset({"test"}) if is_garbled else frozenset())

    monkeypatch.setattr(_idx, "detect_garble", _fake_detect_garble)
    monkeypatch.setattr(_rec, "detect_garble", _fake_flat_garble)

    flat_garble_calls = []
    def _fake_flat_block_garble(blocks, **kw):
        flat_garble_calls.append(len(blocks))
        is_first = len(flat_garble_calls) == 1
        if is_first:
            return GarbleReport(is_garbled=True, fired_prongs=frozenset({"test"}))
        return None

    monkeypatch.setattr(_idx, "_garble_check_flat_blocks", _fake_flat_block_garble)

    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    with patch("pageindex_mcp.converters.vlm_extract_markdown", vlm_mock):
        doc_id = await c.index(pdf_file)

    assert isinstance(doc_id, str) and len(doc_id) == 36
    vlm_mock.assert_awaited_once()
    mocks["save_flat_doc"].assert_called_once()
    mocks["VLM_FALLBACK_TOTAL"].labels.assert_called_with(result="recovered")


# ---------------------------------------------------------------------------
# VLM-C7: Flat-path garble + VLM still garbled — terminal rejection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_VLM_C7_flat_path_garble_still_garbled(monkeypatch, pdf_file):
    """When VLM output also fails check_garble(FLAT_MARKDOWN), terminal rejection."""
    mocks, vlm_mock = _wire_vlm(
        monkeypatch,
        validate_side_effect=[
            TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW),
        ],
    )
    from pageindex_mcp.helpers import GarbleReport

    _garbled = GarbleReport(is_garbled=True, fired_prongs=frozenset({"test"}))
    monkeypatch.setattr(_idx, "detect_garble", lambda text, **kw: _garbled)
    monkeypatch.setattr(_rec, "detect_garble", lambda text, **kw: _garbled)
    monkeypatch.setattr(_idx, "_garble_check_flat_blocks", lambda blocks, **kw: _garbled)

    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_tree_result()))

    with patch("pageindex_mcp.converters.vlm_extract_markdown", vlm_mock):
        with pytest.raises(LowQualityTreeError) as exc:
            await c.index(pdf_file)

    assert exc.value.reason == "garbling"
    mocks["save_doc"].assert_not_called()
    mocks["save_flat_doc"].assert_not_called()
    mocks["VLM_FALLBACK_TOTAL"].labels.assert_called_with(result="still_garbled")
