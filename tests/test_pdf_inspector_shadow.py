"""Tests for pdf-inspector shadow-mode integration.

Shadow mode: pdf-inspector classifies PDFs in probe_conversion_route() and emits
classification data in the handshake, but classification NEVER influences routing
decisions. validate_tree() remains the sole quality gate.
"""

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Fixtures: mock pdf-inspector result objects
# ---------------------------------------------------------------------------


@dataclass
class FakePdfResult:
    pdf_type: str = "text_based"
    confidence: float = 0.98
    page_count: int = 5
    pages_needing_ocr: list = field(default_factory=list)
    has_encoding_issues: bool = False


@dataclass
class FakeScannedResult:
    pdf_type: str = "scanned"
    confidence: float = 0.91
    page_count: int = 10
    pages_needing_ocr: list = field(default_factory=lambda: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    has_encoding_issues: bool = False


@dataclass
class FakeMixedResult:
    pdf_type: str = "mixed"
    confidence: float = 0.85
    page_count: int = 20
    pages_needing_ocr: list = field(default_factory=lambda: [5, 12, 18])
    has_encoding_issues: bool = True


def _make_fitz_mock(page_count: int):
    """Build a mock that works with ``fitz.open(path) as doc``."""
    mock_doc = MagicMock()
    mock_doc.page_count = page_count
    mock_doc.__enter__ = MagicMock(return_value=mock_doc)
    mock_doc.__exit__ = MagicMock(return_value=False)
    mock_fitz = MagicMock()
    mock_fitz.open.return_value = mock_doc
    return mock_fitz


# ---------------------------------------------------------------------------
# 1. probe_conversion_route: classification present when pdf-inspector available
# ---------------------------------------------------------------------------


class TestProbeWithPdfInspector:

    def test_text_based_classification_returned(self, tmp_path):
        pdf_path = str(tmp_path / "test.pdf")
        (tmp_path / "test.pdf").write_bytes(b"%PDF-1.4 fake")

        with (
            patch("pageindex_mcp.converters._pdf_inspector_available", True),
            patch("pageindex_mcp.converters._detect_pdf", return_value=FakePdfResult()),
            patch.dict("sys.modules", {"fitz": _make_fitz_mock(5)}),
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

    def test_scanned_classification_returned(self, tmp_path):
        pdf_path = str(tmp_path / "scan.pdf")
        (tmp_path / "scan.pdf").write_bytes(b"%PDF-1.4 fake")

        with (
            patch("pageindex_mcp.converters._pdf_inspector_available", True),
            patch("pageindex_mcp.converters._detect_pdf", return_value=FakeScannedResult()),
            patch.dict("sys.modules", {"fitz": _make_fitz_mock(10)}),
            patch("pageindex_mcp.config.MAX_DOCLING_PAGES", 150),
        ):
            from pageindex_mcp.converters import probe_conversion_route

            _, _, classification = probe_conversion_route(pdf_path)

        assert classification["pdf_type"] == "scanned"
        assert classification["pages_needing_ocr"] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

    def test_mixed_classification_with_encoding_issues(self, tmp_path):
        pdf_path = str(tmp_path / "mixed.pdf")
        (tmp_path / "mixed.pdf").write_bytes(b"%PDF-1.4 fake")

        with (
            patch("pageindex_mcp.converters._pdf_inspector_available", True),
            patch("pageindex_mcp.converters._detect_pdf", return_value=FakeMixedResult()),
            patch.dict("sys.modules", {"fitz": _make_fitz_mock(20)}),
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

    def test_returns_none_classification_when_unavailable(self, tmp_path):
        pdf_path = str(tmp_path / "test.pdf")
        (tmp_path / "test.pdf").write_bytes(b"%PDF-1.4 fake")

        with (
            patch("pageindex_mcp.converters._pdf_inspector_available", False),
            patch.dict("sys.modules", {"fitz": _make_fitz_mock(5)}),
            patch("pageindex_mcp.config.MAX_DOCLING_PAGES", 150),
        ):
            from pageindex_mcp.converters import probe_conversion_route

            chunk_count, is_docling, classification = probe_conversion_route(pdf_path)

        assert chunk_count == 1
        assert is_docling is True
        assert classification is None

    def test_non_pdf_returns_none_classification(self):
        from pageindex_mcp.converters import probe_conversion_route

        chunk_count, is_docling, classification = probe_conversion_route("readme.md")
        assert chunk_count == 1
        assert is_docling is False
        assert classification is None

    def test_detect_exception_returns_none_classification(self, tmp_path):
        pdf_path = str(tmp_path / "bad.pdf")
        (tmp_path / "bad.pdf").write_bytes(b"%PDF-1.4 fake")

        with (
            patch("pageindex_mcp.converters._pdf_inspector_available", True),
            patch(
                "pageindex_mcp.converters._detect_pdf",
                side_effect=RuntimeError("Rust panic"),
            ),
            patch.dict("sys.modules", {"fitz": _make_fitz_mock(5)}),
            patch("pageindex_mcp.config.MAX_DOCLING_PAGES", 150),
        ):
            from pageindex_mcp.converters import probe_conversion_route

            chunk_count, is_docling, classification = probe_conversion_route(pdf_path)

        assert chunk_count == 1
        assert is_docling is True
        assert classification is None


# ---------------------------------------------------------------------------
# 3. Shadow mode: routing unchanged regardless of classification
# ---------------------------------------------------------------------------


class TestShadowModeRouting:

    def test_scanned_pdf_still_routes_to_docling(self, tmp_path):
        pdf_path = str(tmp_path / "scan.pdf")
        (tmp_path / "scan.pdf").write_bytes(b"%PDF-1.4 fake")

        with (
            patch("pageindex_mcp.converters._pdf_inspector_available", True),
            patch("pageindex_mcp.converters._detect_pdf", return_value=FakeScannedResult()),
            patch.dict("sys.modules", {"fitz": _make_fitz_mock(10)}),
            patch("pageindex_mcp.config.MAX_DOCLING_PAGES", 150),
        ):
            from pageindex_mcp.converters import probe_conversion_route

            chunk_count, is_docling, _ = probe_conversion_route(pdf_path)

        assert chunk_count == 1
        assert is_docling is True

    def test_large_text_pdf_still_chunks(self, tmp_path):
        pdf_path = str(tmp_path / "large.pdf")
        (tmp_path / "large.pdf").write_bytes(b"%PDF-1.4 fake")

        fake_result = FakePdfResult(page_count=300)
        with (
            patch("pageindex_mcp.converters._pdf_inspector_available", True),
            patch("pageindex_mcp.converters._detect_pdf", return_value=fake_result),
            patch.dict("sys.modules", {"fitz": _make_fitz_mock(300)}),
            patch("pageindex_mcp.config.MAX_DOCLING_PAGES", 150),
        ):
            from pageindex_mcp.converters import probe_conversion_route

            chunk_count, is_docling, _ = probe_conversion_route(pdf_path)

        assert chunk_count == 2
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

    def test_handshake_omits_classification_when_none(self):
        classification = None
        handshake = {
            "handshake": True,
            "chunk_count": 1,
            "is_docling_route": True,
        }
        if classification is not None:
            handshake["pdf_classification"] = classification

        assert "pdf_classification" not in handshake


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

    def test_handles_missing_classification_gracefully(self):
        handshake = {
            "handshake": True,
            "chunk_count": 1,
            "is_docling_route": True,
        }
        pdf_class = handshake.get("pdf_classification")
        assert pdf_class is None


# ---------------------------------------------------------------------------
# 6. Prometheus metrics
# ---------------------------------------------------------------------------


class TestPdfInspectorMetrics:

    def test_classification_counter_exists(self):
        from pageindex_mcp.metrics import PDF_INSPECTOR_CLASSIFICATIONS

        assert PDF_INSPECTOR_CLASSIFICATIONS is not None

    def test_classification_latency_histogram_exists(self):
        from pageindex_mcp.metrics import PDF_INSPECTOR_LATENCY

        assert PDF_INSPECTOR_LATENCY is not None

    def test_counter_labels_include_pdf_type(self):
        from pageindex_mcp.metrics import PDF_INSPECTOR_CLASSIFICATIONS

        PDF_INSPECTOR_CLASSIFICATIONS.labels(pdf_type="text_based").inc()
        assert (
            PDF_INSPECTOR_CLASSIFICATIONS.labels(pdf_type="text_based")._value.get()
            >= 1
        )


# ---------------------------------------------------------------------------
# 7. _run_pdf_inspector unit tests
# ---------------------------------------------------------------------------


class TestRunPdfInspector:

    def test_returns_none_when_unavailable(self):
        with patch("pageindex_mcp.converters._pdf_inspector_available", False):
            from pageindex_mcp.converters import _run_pdf_inspector

            assert _run_pdf_inspector("/some/path.pdf") is None

    def test_returns_dict_on_success(self, tmp_path):
        pdf_path = str(tmp_path / "test.pdf")
        (tmp_path / "test.pdf").write_bytes(b"%PDF-1.4 fake")

        with (
            patch("pageindex_mcp.converters._pdf_inspector_available", True),
            patch(
                "pageindex_mcp.converters._detect_pdf",
                return_value=FakePdfResult(),
            ),
        ):
            from pageindex_mcp.converters import _run_pdf_inspector

            result = _run_pdf_inspector(pdf_path)

        assert result is not None
        assert result["pdf_type"] == "text_based"
        assert result["confidence"] == 0.98
        assert isinstance(result["pages_needing_ocr"], list)

    def test_returns_none_on_exception(self, tmp_path):
        pdf_path = str(tmp_path / "test.pdf")
        (tmp_path / "test.pdf").write_bytes(b"%PDF-1.4 fake")

        with (
            patch("pageindex_mcp.converters._pdf_inspector_available", True),
            patch(
                "pageindex_mcp.converters._detect_pdf",
                side_effect=Exception("boom"),
            ),
        ):
            from pageindex_mcp.converters import _run_pdf_inspector

            assert _run_pdf_inspector(pdf_path) is None
