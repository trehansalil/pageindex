"""RFC-036 D0 tests — landscape reextract runaway: page cap, thread/process
kill, splice fix, fragmentation guard.

Covers Task 1.8 (Design Properties 1-4), unit/PBT layer only (no live
Docling/MinIO dependency, per Design Property-Based Testing Configuration).

  Property 1 (Landscape page cap bounds reextraction): synthetic 20-page
    document with 15 landscape-tagged pages -- ``MAX_LANDSCAPE_PAGES`` fires
    and only the top-N pages are reextracted.
  Property 2 (Thread pool cleanup on timeout): a chunk conversion sized to
    exceed the per-chunk timeout degrades gracefully within budget and
    leaves no background process alive.
  Property 3 (Landscape content spliced at page position): fallback
    markdown lands at the correct page index, not appended at document
    end; no ordering change for documents that never trigger the landscape
    path.
  Property 4 (Singleton ratio guard prevents fragmentation): a table block
    with >60% single-value rows is left as a single TABLE node instead of
    being shattered into singleton kv children.

Regression (synthetic proxies for the named corpus fixtures -- true
re-ingestion against live Docling/MinIO is the Design's separate
Integration Tests layer):
  uae_numbers_english_page_16_17_landscape (FAIL->MARGINAL): the two
    flagged landscape pages both land inside MAX_LANDSCAPE_PAGES and
    splice back at their original page positions instead of the document
    end.
  world-stats-pocketbook-2023 (ERROR->clean-timeout-with-status): dense
    numeric-table pages carry no picture region, so D0c's trigger
    condition never flags them -- the runaway reextraction loop this
    fixture used to hit simply never starts.
"""

from __future__ import annotations

import multiprocessing
import time
from unittest.mock import MagicMock

import pytest

from pageindex_mcp import converters
from pageindex_mcp.converters import (
    FuturesTimeoutError,
    _landscape_rasterize_rotate_reextract,
    _outline_norm,
    _run_docling_chunk_with_timeout,
    _splice_landscape_fallback,
)
from pageindex_mcp.helpers import _segment_table_nodes


def _wire_fake_docling(monkeypatch, tmp_path, markdown="# Chart\n\nrecovered content"):
    # _landscape_rasterize_rotate_reextract bails to [] when the AGPL
    # fallback is disabled; pin it on so the cap/deadline assertions are
    # hermetic regardless of the host's ALLOW_AGPL_FALLBACK env setting.
    monkeypatch.setattr("pageindex_mcp.config.ALLOW_AGPL_FALLBACK", True)
    monkeypatch.setattr(
        converters,
        "_rasterize_rotate_page",
        lambda pdf_path, page_no, dpi=300: str(tmp_path / f"page{page_no}.png"),
    )
    monkeypatch.setattr(converters, "_repair_docling_tables", lambda md, doc_name=None: md)
    fake_result = MagicMock()
    fake_result.document.export_to_markdown.return_value = markdown
    fake_result.document.pictures = []
    fake_converter = MagicMock()
    fake_converter.convert.return_value = fake_result
    monkeypatch.setattr(converters, "_docling_converter", lambda **kw: fake_converter)


class TestMaxLandscapePagesCap:
    """Property 1: MAX_LANDSCAPE_PAGES bounds the per-page reextraction loop."""

    def test_cap_bounds_reextraction_to_top_n_pages(self, tmp_path, monkeypatch):
        _wire_fake_docling(monkeypatch, tmp_path)
        pages = [
            {"page_no": i, "rotate": 90, "is_landscape": True, "char_count": 10}
            for i in range(15)
        ]

        results = _landscape_rasterize_rotate_reextract("fake.pdf", pages)

        assert len(results) == converters.MAX_LANDSCAPE_PAGES
        assert [r["page_no"] for r in results] == list(range(converters.MAX_LANDSCAPE_PAGES))

    def test_below_cap_document_reextracts_all_flagged_pages(self, tmp_path, monkeypatch):
        _wire_fake_docling(monkeypatch, tmp_path)
        pages = [
            {"page_no": i, "rotate": 90, "is_landscape": True, "char_count": 10}
            for i in range(3)
        ]

        results = _landscape_rasterize_rotate_reextract("fake.pdf", pages)

        assert len(results) == 3
        assert [r["page_no"] for r in results] == [0, 1, 2]

    def test_deadline_also_bounds_the_loop(self, tmp_path, monkeypatch):
        _wire_fake_docling(monkeypatch, tmp_path)
        monkeypatch.setattr(converters, "LANDSCAPE_REEXTRACT_DEADLINE_SECONDS", 0.0)
        pages = [
            {"page_no": i, "rotate": 90, "is_landscape": True, "char_count": 10}
            for i in range(5)
        ]

        results = _landscape_rasterize_rotate_reextract("fake.pdf", pages)

        assert results == []


# Module-level (picklable-by-reference) worker stand-ins for the
# multiprocessing 'spawn' context Property 2 exercises.
def _slow_chunk_worker(result_queue, pdf_path, force_full_page_ocr, ocr_lang_override):
    time.sleep(5)
    result_queue.put(("ok", ("late content", [])))


def _fast_chunk_worker(result_queue, pdf_path, force_full_page_ocr, ocr_lang_override):
    result_queue.put(("ok", ("chunk markdown", [])))


class TestThreadPoolCleanupOnTimeout:
    """Property 2: a chunk exceeding its timeout degrades gracefully within
    budget and leaves no background process alive."""

    def test_timeout_raises_within_budget_and_leaves_no_surviving_process(
        self, monkeypatch
    ):
        monkeypatch.setattr(converters, "_docling_chunk_worker", _slow_chunk_worker)
        start = time.monotonic()

        with pytest.raises(FuturesTimeoutError):
            _run_docling_chunk_with_timeout(
                "fake.pdf",
                force_full_page_ocr=False,
                ocr_lang_override=None,
                timeout_s=0.5,
            )

        elapsed = time.monotonic() - start
        # Graceful degradation within budget: the call returns near the
        # requested timeout, not after the worker's full 5s sleep.
        assert elapsed < 4.0
        # No background thread/process outlives the parent's exit.
        assert multiprocessing.active_children() == []

    def test_fast_chunk_completes_normally(self, monkeypatch):
        monkeypatch.setattr(converters, "_docling_chunk_worker", _fast_chunk_worker)

        md, pics = _run_docling_chunk_with_timeout(
            "fake.pdf",
            force_full_page_ocr=False,
            ocr_lang_override=None,
            timeout_s=5.0,
        )

        assert md == "chunk markdown"
        assert pics == []
        assert multiprocessing.active_children() == []


class TestSpliceLandscapeFallback:
    """Property 3: fallback markdown lands at its original page position,
    not appended at document end; no-op for non-landscape documents."""

    def test_splice_inserts_before_next_heading_not_at_document_end(self):
        md = (
            "# Intro\n\nIntro text.\n\n"
            "# Chapter Two\n\nChapter two text.\n\n"
            "# Chapter Three\n\nChapter three text.\n"
        )
        heading_pages = {
            _outline_norm("Intro"): [1],
            _outline_norm("Chapter Two"): [3],
            _outline_norm("Chapter Three"): [5],
        }
        # page_no is 0-indexed (PyMuPDF); page 2 (1-indexed) falls between
        # the "Intro" (page 1) and "Chapter Two" (page 3) headings.
        landscape_fallback_pages = [{"page_no": 1, "markdown": "LANDSCAPE CHART CONTENT"}]

        result = _splice_landscape_fallback(md, landscape_fallback_pages, heading_pages)

        intro_idx = result.index("# Intro")
        landscape_idx = result.index("LANDSCAPE CHART CONTENT")
        chapter_two_idx = result.index("# Chapter Two")
        assert intro_idx < landscape_idx < chapter_two_idx
        assert not result.rstrip().endswith("LANDSCAPE CHART CONTENT")

    def test_splice_falls_back_to_document_end_when_no_later_heading(self):
        md = "# Only Heading\n\nText.\n"
        heading_pages = {_outline_norm("Only Heading"): [1]}
        landscape_fallback_pages = [{"page_no": 5, "markdown": "LATE PAGE CONTENT"}]

        result = _splice_landscape_fallback(md, landscape_fallback_pages, heading_pages)

        assert result.rstrip().endswith("LATE PAGE CONTENT")

    def test_no_ordering_change_for_documents_without_landscape_fallback(self):
        md = "# A\n\ntext a\n\n# B\n\ntext b\n"

        result = _splice_landscape_fallback(md, [], {})

        assert result == md


class TestSingletonRatioGuard:
    """Property 4: >60% single-value rows skip segmentation and keep a
    single TABLE node."""

    @staticmethod
    def _table_text(n_singleton: int, n_pair: int) -> str:
        rows = [f"| {i} |" for i in range(n_singleton)]
        rows += [f"| key{i} | val{i} |" for i in range(n_pair)]
        table = "| Value |\n|---|\n" + "\n".join(rows)
        prose_unit = "Chart axis labels described in the following table. "
        prose = (prose_unit * ((2500 // len(prose_unit)) + 1))[:2500]
        return prose + "\n" + table

    def test_80_percent_singleton_rows_skips_segmentation(self):
        text = self._table_text(n_singleton=20, n_pair=5)  # 20/25 = 80%
        structure = [{"node_id": "n1", "title": "Chart", "text": text, "nodes": []}]

        result = _segment_table_nodes(structure)

        assert result[0]["nodes"] == []
        assert result[0]["text"] == text

    def test_exactly_60_percent_singleton_rows_still_segments(self):
        """Design Key Test Scenario: boundary condition — Property 4 says a
        block AT the 60% singleton ratio SHALL be segmented per existing
        behavior (the guard fires only strictly above the threshold)."""
        text = self._table_text(n_singleton=6, n_pair=4)  # 6/10 = 60% exactly
        structure = [{"node_id": "n1", "title": "Data Table", "text": text, "nodes": []}]

        result = _segment_table_nodes(structure)

        assert len(result[0]["nodes"]) > 1
        assert result[0]["text"] == ""

    def test_below_60_percent_singleton_rows_still_segments(self):
        text = self._table_text(n_singleton=2, n_pair=8)  # 2/10 = 20%
        structure = [{"node_id": "n1", "title": "Data Table", "text": text, "nodes": []}]

        result = _segment_table_nodes(structure)

        assert len(result[0]["nodes"]) > 1
        assert result[0]["text"] == ""


class TestRegressionFixtures:
    """Synthetic regression proxies for the two Run-19 audit fixtures named
    in RFC-036 D0's test strategy. True corpus re-ingestion against live
    Docling/MinIO is the Design's separate Integration Tests layer; these
    reproduce each fixture's defining shape against the fixed code paths."""

    def test_uae_numbers_landscape_pages_land_within_cap_and_splice_in_order(
        self, tmp_path, monkeypatch
    ):
        """uae_numbers_english_page_16_17_landscape (FAIL->MARGINAL): both
        flagged pages (16, 17) fall well within MAX_LANDSCAPE_PAGES and
        splice back at their original positions instead of being appended
        at document end -- the ordering defect that caused the FAIL."""
        _wire_fake_docling(monkeypatch, tmp_path, markdown="Recovered chart text")
        pages = [
            {"page_no": 15, "rotate": 90, "is_landscape": True, "char_count": 50},
            {"page_no": 16, "rotate": 90, "is_landscape": True, "char_count": 50},
        ]

        results = _landscape_rasterize_rotate_reextract("fake.pdf", pages)
        assert len(results) == 2

        md = (
            "# Page 15 Section\n\ntext\n\n"
            "# Page 18 Section\n\nmore text\n"
        )
        heading_pages = {
            _outline_norm("Page 15 Section"): [15],
            _outline_norm("Page 18 Section"): [18],
        }
        spliced = _splice_landscape_fallback(md, results, heading_pages)

        assert spliced.count("Recovered chart text") == 2
        first_heading_idx = spliced.index("# Page 15 Section")
        last_heading_idx = spliced.index("# Page 18 Section")
        for idx in (
            i for i in _all_indices(spliced, "Recovered chart text")
        ):
            assert first_heading_idx < idx < last_heading_idx

    def test_world_stats_pocketbook_dense_tables_never_trigger_landscape_path(self):
        """world-stats-pocketbook-2023 (ERROR->clean-timeout-with-status):
        292 dense numeric-table pages fall below LANDSCAPE_CHAR_THRESHOLD
        but carry no picture region, so D0c's trigger condition never
        flags them -- the runaway reextraction loop this fixture used to
        hit simply never starts."""
        from unittest.mock import MagicMock as _MM
        from unittest.mock import patch as _patch

        landscape_pages = [
            {"page_no": i, "rotate": 90, "is_landscape": True} for i in range(292)
        ]
        document = _MM()
        document.iterate_items.return_value = [
            (type("Item", (), {"text": "1234.56 " * 10})(), 0)
        ]

        with _patch.object(converters, "_collect_picture_regions", return_value=[]):
            below = converters._landscape_pages_below_threshold(document, landscape_pages)

        assert below == []


def _all_indices(haystack: str, needle: str) -> list[int]:
    indices = []
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            break
        indices.append(idx)
        start = idx + 1
    return indices
