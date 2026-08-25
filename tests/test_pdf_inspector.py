"""Tests for pdf-inspector integration: shadow-mode classification and the
RFC-032 D1 ``inspector_force_ocr`` decision matrix.

Shadow mode: pdf-inspector classifies PDFs in probe_conversion_route() and emits
classification data in the handshake, but classification NEVER influences routing
decisions in that path. validate_tree() remains the sole quality gate.

The decision-matrix tests below cover the separate ``client.py::index()`` path
(RFC-032 D1/D4) where the pdf-inspector classification *does* drive whether
full-page OCR is force-enabled, gated on ``PDF_INSPECTOR_PRECLASSIFY`` and a
confidence threshold — with validate_tree() and the Fix-3 OCR-escalation retry
still acting as unconditional safety nets underneath it.
"""

from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.client import images as _img
from pageindex_mcp.client import indexer as _idx
from pageindex_mcp.client import recovery as _rec

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


def _fake_settings(**overrides):
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


@pytest.fixture
def pdf_file(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4\n fake pdf bytes")
    return str(path)


def _wire_index(monkeypatch, *, preclassify, validate_tree=None):
    """Patch every collaborator client.index() touches on the PDF -> markdown
    route, and capture the args the (single) converter chain entry is called
    with — so we can inspect whether OCR was forced."""
    fake_settings = _fake_settings()
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


def _make_client():
    return CustomPageIndexClient(api_key="test-key")


async def _run_index(monkeypatch, pdf_file, *, preclassify, pdf_classification, validate_tree=None):
    mocks = _wire_index(monkeypatch, preclassify=preclassify, validate_tree=validate_tree)
    c = _make_client()
    monkeypatch.setattr(
        c, "_run_md_to_tree", AsyncMock(return_value={"structure": [], "doc_description": "ok"})
    )
    await c.index(pdf_file, pdf_classification=pdf_classification)
    return mocks


class TestInspectorForceOcrDecisionMatrix:
    async def test_forces_ocr_for_scanned_at_high_confidence(self, monkeypatch, pdf_file):
        mocks = await _run_index(
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
        mocks = await _run_index(
            monkeypatch,
            pdf_file,
            preclassify=True,
            pdf_classification={"pdf_type": "scanned", "confidence": 0.85},
        )

        # Zone-1: _script_from_filename now returns "Latn" for eng/deu filenames
        mocks["conv_fn"].assert_called_once_with(pdf_file, expected_script="Latn")
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_not_called()

    async def test_missing_confidence_key_does_not_force_ocr(self, monkeypatch, pdf_file):
        """A classification dict without ``confidence`` defaults to 0 → no force."""
        mocks = await _run_index(
            monkeypatch,
            pdf_file,
            preclassify=True,
            pdf_classification={"pdf_type": "scanned"},
        )

        # Zone-1: _script_from_filename now returns "Latn" for eng/deu filenames
        mocks["conv_fn"].assert_called_once_with(pdf_file, expected_script="Latn")
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_not_called()

    async def test_preclassify_flag_off_ignores_classification(self, monkeypatch, pdf_file):
        mocks = await _run_index(
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
        mocks = await _run_index(
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
        mocks = _wire_index(monkeypatch, preclassify=preclassify)
        remote_settings = _fake_settings(docling_service_url="http://docling.test")
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
