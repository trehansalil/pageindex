"""Tests for RFC-027 Tasks 4.1-4.3 (D7): page-count guard + chunked-Docling
route for oversized PDFs, with a PyPDF2 text-layer-only fallback on chunk
timeout.

Validates Design Property 8 (large-document chunked-Docling guard):
- `MAX_DOCLING_PAGES` gates direct vs. chunked routing (config.py env-var
  override pattern, matching `MIN_IMAGE_PROMOTED_CHARS` / `MIN_FLAT_PROMOTION_CHARS`)
- chunk math is `ceil(page_count / max_pages)`, page-boundary splits only
- a chunk that still times out on Docling falls back to PyPDF2
  `page.extract_text()` (never `pymupdf4llm` -- CLAUDE.md Hard Rule 4) and the
  resulting flat-structure document is not silently promoted past MARGINAL
  (CLAUDE.md Hard Rule 5)
- `CHILD_TIMEOUT` scales as `base_timeout + (chunk_count * per_chunk_timeout)`
"""

import importlib
import math
from types import SimpleNamespace

import pytest

import PyPDF2
from pageindex_mcp import config, converters
from pageindex_mcp.helpers import classify_verdict


def _fake_reader_cls(n_pages: int, text: str = "lorem ipsum"):
    class _FakeReader:
        def __init__(self, _path):
            self.pages = [SimpleNamespace(extract_text=lambda: text) for _ in range(n_pages)]

    return _FakeReader


class _FakeWriter:
    def __init__(self):
        self.added = []

    def add_page(self, page):
        self.added.append(page)

    def write(self, fh):
        pass


class TestChunkedSplitMath:
    def test_292_page_pdf_splits_into_ceil_292_over_150_equals_2_chunks(self, monkeypatch):
        """world-stats-pocketbook-2023.pdf shape (292 pages): the chunked
        route must run exactly ceil(292/150)==2 independent Docling passes."""
        monkeypatch.setattr(PyPDF2, "PdfReader", _fake_reader_cls(292))
        monkeypatch.setattr(PyPDF2, "PdfWriter", _FakeWriter)
        calls = []

        def _fake_docling(path, force_full_page_ocr=False, ocr_lang_override=None):
            calls.append(path)
            return "chunk-md", []

        monkeypatch.setattr(converters, "pdf_to_markdown_docling", _fake_docling)

        assert math.ceil(292 / 150) == 2
        md, pics = converters._pdf_to_markdown_docling_chunked(
            "fake.pdf", page_count=292, max_pages=150
        )
        assert len(calls) == 2
        assert md == "chunk-md\n\nchunk-md"
        assert pics == []

    def test_max_docling_pages_env_override_changes_chunk_boundary(self, monkeypatch):
        """A custom `MAX_DOCLING_PAGES` threshold changes the chunk count for
        the same 292-page document."""
        monkeypatch.setattr(PyPDF2, "PdfReader", _fake_reader_cls(292))
        monkeypatch.setattr(PyPDF2, "PdfWriter", _FakeWriter)
        calls = []
        monkeypatch.setattr(
            converters,
            "pdf_to_markdown_docling",
            lambda path, force_full_page_ocr=False, ocr_lang_override=None: (
                calls.append(path),
                ("chunk-md", []),
            )[1],
        )

        assert math.ceil(292 / 100) == 3
        converters._pdf_to_markdown_docling_chunked("fake.pdf", page_count=292, max_pages=100)
        assert len(calls) == 3


class TestSubThresholdDirectPath:
    def test_pdf_under_max_docling_pages_uses_direct_path_unchanged(self, monkeypatch):
        """A page count below `MAX_DOCLING_PAGES` must never be routed through
        the chunked path -- the direct single-pass conversion runs as before."""
        monkeypatch.setattr(PyPDF2, "PdfReader", _fake_reader_cls(50))

        def _chunked_should_not_be_called(*_args, **_kwargs):
            raise AssertionError("chunked-Docling path must not run for a sub-threshold PDF")

        monkeypatch.setattr(
            converters, "_pdf_to_markdown_docling_chunked", _chunked_should_not_be_called
        )

        class _Sentinel(Exception):
            pass

        def _fake_converter_factory(**_kwargs):
            raise _Sentinel("direct path reached _docling_converter as expected")

        monkeypatch.setattr(converters, "_docling_converter", _fake_converter_factory)

        with pytest.raises(_Sentinel):
            converters.pdf_to_markdown_docling("fake.pdf")


class TestChunkTimeoutFallback:
    def test_chunk_timeout_falls_back_to_pypdf2_text_extraction(self, monkeypatch):
        """A chunk that times out on the Docling pipeline falls back to
        PyPDF2 text-layer extraction (no pymupdf4llm, per Hard Rule 4)
        instead of losing the chunk entirely."""
        monkeypatch.setattr(PyPDF2, "PdfReader", _fake_reader_cls(2, text="FALLBACK_TEXT"))
        monkeypatch.setattr(PyPDF2, "PdfWriter", _FakeWriter)
        monkeypatch.setattr(converters, "_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S", 0.05)

        def _slow_docling(path, force_full_page_ocr=False, ocr_lang_override=None):
            import time

            time.sleep(0.5)
            return "should-never-be-used", []

        monkeypatch.setattr(converters, "pdf_to_markdown_docling", _slow_docling)

        md, pics = converters._pdf_to_markdown_docling_chunked(
            "fake.pdf", page_count=2, max_pages=1
        )
        assert "FALLBACK_TEXT" in md
        assert "should-never-be-used" not in md
        assert pics == []


class TestTextOnlyFallbackVerdict:
    def test_flat_multi_page_fallback_structure_lands_at_margin(self):
        """A flat-structure document produced by the D7 text-only fallback
        (one node per page, no headings recovered) must not be silently
        promoted past MARGINAL -- per CLAUDE.md Hard Rule 5."""
        structure = [
            {"node_id": str(i), "title": "", "text": "x" * n, "nodes": []}
            for i, n in enumerate([300, 120, 110, 100, 90, 80, 70, 60])
        ]
        verdict, _reason = classify_verdict(structure, "flat_prose", None)
        assert verdict == "MARGINAL"


class TestDynamicTimeoutScaling:
    def test_two_chunk_timeout_exceeds_single_chunk_base_case(self):
        """`CHILD_TIMEOUT` for a chunked document scales with chunk_count,
        so a 2-chunk document must get a larger timeout budget than a
        1-chunk document."""
        one_chunk = converters.chunked_docling_timeout_s(1)
        two_chunk = converters.chunked_docling_timeout_s(2)
        assert two_chunk > one_chunk
        assert one_chunk == (
            converters._CHUNKED_DOCLING_BASE_TIMEOUT_S
            + converters._CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S
        )
        assert two_chunk == (
            converters._CHUNKED_DOCLING_BASE_TIMEOUT_S
            + 2 * converters._CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S
        )


class TestMaxDoclingPagesConfig:
    def test_env_override_changes_config_value(self, monkeypatch):
        """`MAX_DOCLING_PAGES` follows the existing
        `int(os.environ.get(...))` config pattern -- an env override changes
        the resolved threshold."""
        monkeypatch.setenv("MAX_DOCLING_PAGES", "10")
        try:
            importlib.reload(config)
            assert config.MAX_DOCLING_PAGES == 10
        finally:
            monkeypatch.delenv("MAX_DOCLING_PAGES", raising=False)
            importlib.reload(config)
        assert config.MAX_DOCLING_PAGES == 150

    def test_overridden_threshold_is_passed_to_chunked_route(self, monkeypatch):
        """The overridden threshold actually drives the direct-vs-chunked
        routing decision inside `pdf_to_markdown_docling`."""
        monkeypatch.setattr(config, "MAX_DOCLING_PAGES", 10)
        monkeypatch.setattr(PyPDF2, "PdfReader", _fake_reader_cls(20))
        captured = {}

        def _fake_chunked(pdf_path, page_count, max_pages, **_kwargs):
            captured["page_count"] = page_count
            captured["max_pages"] = max_pages
            return "chunked-md", []

        monkeypatch.setattr(converters, "_pdf_to_markdown_docling_chunked", _fake_chunked)

        converters.pdf_to_markdown_docling("fake.pdf")
        assert captured == {"page_count": 20, "max_pages": 10}
