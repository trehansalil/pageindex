"""Tests for RFC-027 Tasks 4.1-4.3 (D7): page-count guard + chunked-Docling
route for oversized PDFs, with a pymupdf text-layer-only fallback on chunk
timeout.

Validates Design Property 8 (large-document chunked-Docling guard):
- `MAX_DOCLING_PAGES` gates direct vs. chunked routing (config.py env-var
  override pattern, matching `MIN_IMAGE_PROMOTED_CHARS` / `MIN_FLAT_PROMOTION_CHARS`)
- chunk math is `ceil(page_count / max_pages)`, page-boundary splits only
- a chunk that still times out on Docling falls back to pymupdf
  `page.get_text()` (never `pymupdf4llm` -- CLAUDE.md Hard Rule 4) and the
  resulting flat-structure document is not silently promoted past MARGINAL
  (CLAUDE.md Hard Rule 5)
- `CHILD_TIMEOUT` scales as `base_timeout + (chunk_count * per_chunk_timeout)`

The PDF primitive layer is pymupdf (`fitz`), so the fakes below mimic the exact
`fitz` surface `converters.py` uses: `fitz.open(path)` / `fitz.open()`,
`Document.page_count`, iteration over pages, `Page.get_text()`,
`Document.insert_pdf(src, from_page=..., to_page=...)`, `Document.save(path)`,
`Document.close()`, and the `with fitz.open(...)` context-manager protocol.
"""

import importlib
import math

import fitz
import pytest

from pageindex_mcp import config, converters
from pageindex_mcp.helpers import classify_verdict


class _FakePage:
    def __init__(self, text: str):
        self._text = text

    def get_text(self, *_args, **_kwargs) -> str:
        return self._text


class _FakeDoc:
    """Stand-in for a read-mode ``fitz.Document``."""

    def __init__(self, page_count: int, text: str):
        self.page_count = page_count
        self._pages = [_FakePage(text) for _ in range(page_count)]
        self.closed = False

    def __len__(self) -> int:
        return self.page_count

    def __iter__(self):
        return iter(self._pages)

    def __getitem__(self, index: int) -> _FakePage:
        return self._pages[index]

    def load_page(self, index: int) -> _FakePage:
        return self._pages[index]

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        self.close()
        return False


class _FakeWriterDoc:
    """Stand-in for the empty ``fitz.open()`` document each chunk is built in."""

    def __init__(self, recorder: "_FakeFitz"):
        self._recorder = recorder
        self._page_count = 0
        self.closed = False

    def insert_pdf(self, src, from_page=None, to_page=None):
        self._recorder.inserts.append((from_page, to_page))
        self._recorder.insert_sources.append(src)
        # pymupdf's ``to_page`` is INCLUSIVE -- mirror that here so a chunk cut
        # from the half-open slice [start, end) materializes exactly
        # ``end - start`` pages. An off-by-one in the port shows up as a wrong
        # page count in the timeout-fallback text below.
        self._page_count = to_page - from_page + 1

    def save(self, path, *_args, **_kwargs):
        self._recorder.saves.append(path)
        self._recorder.chunk_page_counts[path] = self._page_count

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        self.close()
        return False


class _FakeFitz:
    """Records every ``fitz.open`` call ``converters.py`` makes.

    ``open(path)`` yields a read doc; ``open()`` (no args) yields the writer doc
    used for chunk assembly. A path previously written by ``save()`` re-opens
    with the page count that chunk actually received, so the timeout fallback
    reads back exactly the pages the split produced.
    """

    def __init__(self, page_count: int, text: str = "lorem ipsum"):
        self.source_page_count = page_count
        self.text = text
        self.opened_paths: list[str] = []
        self.inserts: list[tuple[int, int]] = []
        self.insert_sources: list[object] = []
        self.saves: list[str] = []
        self.chunk_page_counts: dict[str, int] = {}
        self.docs: list[_FakeDoc] = []

    def open(self, path=None, *_args, **_kwargs):
        if path is None:
            return _FakeWriterDoc(self)
        self.opened_paths.append(path)
        doc = _FakeDoc(self.chunk_page_counts.get(path, self.source_page_count), self.text)
        self.docs.append(doc)
        return doc


def _patch_fitz(monkeypatch, page_count: int, text: str = "lorem ipsum") -> _FakeFitz:
    """Patch ``fitz.open`` where ``converters.py`` looks it up: it does a
    function-local ``import fitz``, so the module attribute is the seam."""
    recorder = _FakeFitz(page_count, text)
    monkeypatch.setattr(fitz, "open", recorder.open)
    return recorder


class TestChunkedSplitMath:
    def test_292_page_pdf_splits_into_ceil_292_over_150_equals_2_chunks(self, monkeypatch):
        """world-stats-pocketbook-2023.pdf shape (292 pages): the chunked
        route must run exactly ceil(292/150)==2 independent Docling passes."""
        fake_fitz = _patch_fitz(monkeypatch, 292)
        calls = []

        # RFC-036 D0b: chunks now run in a killable spawn subprocess, so patch
        # the subprocess seam (a spawned child would re-import the real module
        # and never see a parent-side pdf_to_markdown_docling monkeypatch).
        def _fake_chunk_runner(
            path, *, force_full_page_ocr, ocr_lang_override, timeout_s
        ):
            calls.append(path)
            return "chunk-md", [], []

        monkeypatch.setattr(
            converters, "_run_docling_chunk_with_timeout", _fake_chunk_runner
        )

        assert math.ceil(292 / 150) == 2
        md, pics, _stages = converters._pdf_to_markdown_docling_chunked(
            "fake.pdf", page_count=292, max_pages=150
        )
        assert len(calls) == 2
        assert md == "chunk-md\n\nchunk-md"
        assert pics == []
        # pymupdf's `to_page` is INCLUSIVE: the half-open slices [0, 150) and
        # [150, 292) must be requested as (0, 149) and (150, 291). Pinning the
        # concrete arguments is what catches an off-by-one in the port.
        assert fake_fitz.inserts == [(0, 149), (150, 291)]
        assert [fake_fitz.chunk_page_counts[p] for p in fake_fitz.saves] == [150, 142]
        assert fake_fitz.saves == calls

    def test_max_docling_pages_env_override_changes_chunk_boundary(self, monkeypatch):
        """A custom `MAX_DOCLING_PAGES` threshold changes the chunk count for
        the same 292-page document."""
        fake_fitz = _patch_fitz(monkeypatch, 292)
        calls = []
        # RFC-036 D0b: patch the subprocess seam, not pdf_to_markdown_docling
        # (spawn children never see parent-side monkeypatches).
        monkeypatch.setattr(
            converters,
            "_run_docling_chunk_with_timeout",
            lambda path, *, force_full_page_ocr, ocr_lang_override, timeout_s: (
                calls.append(path),
                ("chunk-md", [], []),
            )[1],
        )

        assert math.ceil(292 / 100) == 3
        converters._pdf_to_markdown_docling_chunked("fake.pdf", page_count=292, max_pages=100)
        assert len(calls) == 3
        # Boundaries move with the threshold, still inclusive on `to_page`.
        assert fake_fitz.inserts == [(0, 99), (100, 199), (200, 291)]
        assert [fake_fitz.chunk_page_counts[p] for p in fake_fitz.saves] == [100, 100, 92]


class TestSubThresholdDirectPath:
    def test_pdf_under_max_docling_pages_uses_direct_path_unchanged(self, monkeypatch):
        """A page count below `MAX_DOCLING_PAGES` must never be routed through
        the chunked path -- the direct single-pass conversion runs as before."""
        _patch_fitz(monkeypatch, 50)

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
    def test_chunk_timeout_falls_back_to_pymupdf_text_extraction(self, monkeypatch):
        """A chunk that times out on the Docling pipeline falls back to
        pymupdf (`fitz`) text-layer extraction via `page.get_text()` (no
        pymupdf4llm, per Hard Rule 4) instead of losing the chunk entirely."""
        fake_fitz = _patch_fitz(monkeypatch, 2, text="FALLBACK_TEXT")
        monkeypatch.setattr(converters, "_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S", 0.05)

        def _slow_docling(path, force_full_page_ocr=False, ocr_lang_override=None):
            import time

            time.sleep(0.5)
            return "should-never-be-used", []

        monkeypatch.setattr(converters, "pdf_to_markdown_docling", _slow_docling)

        md, pics, _stages = converters._pdf_to_markdown_docling_chunked(
            "fake.pdf", page_count=2, max_pages=1
        )
        assert "FALLBACK_TEXT" in md
        assert "should-never-be-used" not in md
        assert pics == []
        # One page per chunk -- `to_page=end - 1` makes each single-page slice
        # (0, 0) and (1, 1), so each fallback re-read yields exactly one page.
        assert fake_fitz.inserts == [(0, 0), (1, 1)]
        assert [fake_fitz.chunk_page_counts[p] for p in fake_fitz.saves] == [1, 1]
        assert md == "FALLBACK_TEXT\n\nFALLBACK_TEXT"
        # The fallback re-opened each written chunk file (not the source PDF).
        assert fake_fitz.opened_paths == ["fake.pdf", *fake_fitz.saves]
        assert all(doc.closed for doc in fake_fitz.docs)


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
        _patch_fitz(monkeypatch, 20)
        captured = {}

        def _fake_chunked(pdf_path, page_count, max_pages, **_kwargs):
            captured["page_count"] = page_count
            captured["max_pages"] = max_pages
            return "chunked-md", []

        monkeypatch.setattr(converters, "_pdf_to_markdown_docling_chunked", _fake_chunked)

        converters.pdf_to_markdown_docling("fake.pdf")
        assert captured == {"page_count": 20, "max_pages": 10}
