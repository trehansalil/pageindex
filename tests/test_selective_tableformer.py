"""Tests for selective TableFormer processing (RFC-050 D8).

Covers: table detection heuristic, PreClassification serialization,
per-chunk do_table_structure threading, and parameter forwarding through
the remote/service path.
"""

from __future__ import annotations

import threading
import types
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fake_page(h_lines: int = 0, v_lines: int = 0):
    """Create a fake fitz page with controllable horizontal/vertical lines."""
    drawings = []
    for i in range(h_lines):
        p1 = types.SimpleNamespace(x=0.0, y=float(i * 10))
        p2 = types.SimpleNamespace(x=100.0, y=float(i * 10))
        drawings.append({"items": [("l", p1, p2)]})
    for i in range(v_lines):
        p1 = types.SimpleNamespace(x=float(i * 10), y=0.0)
        p2 = types.SimpleNamespace(x=float(i * 10), y=100.0)
        drawings.append({"items": [("l", p1, p2)]})

    page = MagicMock()
    page.get_cdrawings.return_value = drawings
    return page


def _fake_page_with_blocks(blocks: list[tuple[float, float, float, float]]):
    """Create a fake fitz page with controllable text blocks.

    Each block is (x0, y0, x1, y1) — the first four elements of a
    ``page.get_text("blocks")`` tuple.
    """
    page = MagicMock()
    page.get_cdrawings.return_value = []
    full_blocks = [(x0, y0, x1, y1, "text", 0, 0) for x0, y0, x1, y1 in blocks]
    page.get_text.return_value = full_blocks
    return page


# ---------------------------------------------------------------------------
# _page_has_ruled_table
# ---------------------------------------------------------------------------


class TestPageHasRuledTable:
    def test_returns_false_below_thresholds(self):
        from pageindex_mcp.converters.preclassify import _page_has_ruled_table

        page = _fake_page(h_lines=2, v_lines=2)
        assert _page_has_ruled_table(page) is False

    def test_returns_true_at_thresholds(self):
        from pageindex_mcp.converters.preclassify import _page_has_ruled_table

        page = _fake_page(h_lines=3, v_lines=3)
        assert _page_has_ruled_table(page) is True

    def test_rect_items_count_as_lines(self):
        from pageindex_mcp.converters.preclassify import _page_has_ruled_table

        page = MagicMock()
        items = []
        for _ in range(3):
            items.append(("re", types.SimpleNamespace(width=100.0, height=2.0)))
        for _ in range(3):
            items.append(("re", types.SimpleNamespace(width=2.0, height=100.0)))
        page.get_cdrawings.return_value = [{"items": items}]
        assert _page_has_ruled_table(page) is True


# ---------------------------------------------------------------------------
# detect_pages_with_tables
# ---------------------------------------------------------------------------


class TestDetectPagesWithTables:
    def test_returns_none_when_agpl_gate_off(self, monkeypatch):
        from pageindex_mcp.converters import preclassify

        cfg = MagicMock()
        cfg.allow_agpl_fallback = False
        monkeypatch.setattr("pageindex_mcp.config.pipeline_config", cfg)
        pages, method = preclassify.detect_pages_with_tables("/fake/path.pdf")
        assert pages is None
        assert method is None

    def test_returns_none_when_kill_switch_false(self, monkeypatch):
        from pageindex_mcp.converters import preclassify

        cfg = MagicMock()
        cfg.allow_agpl_fallback = True
        monkeypatch.setattr("pageindex_mcp.config.pipeline_config", cfg)
        monkeypatch.setenv("TABLEFORMER_SKIP_ENABLED", "false")
        pages, method = preclassify.detect_pages_with_tables("/fake/path.pdf")
        assert pages is None
        assert method is None

    def test_returns_page_indices_with_tables(self, monkeypatch):
        from pageindex_mcp.converters import preclassify

        cfg = MagicMock()
        cfg.allow_agpl_fallback = True
        monkeypatch.setattr("pageindex_mcp.config.pipeline_config", cfg)
        monkeypatch.setenv("TABLEFORMER_SKIP_ENABLED", "1")

        page_no_table = _fake_page(h_lines=0, v_lines=0)
        page_with_table = _fake_page(h_lines=5, v_lines=5)

        fake_doc = MagicMock()
        fake_doc.__len__ = MagicMock(return_value=3)
        fake_doc.__getitem__ = MagicMock(
            side_effect=lambda idx: page_with_table if idx == 1 else page_no_table
        )
        fake_doc.__enter__ = MagicMock(return_value=fake_doc)
        fake_doc.__exit__ = MagicMock(return_value=False)

        fake_fitz = MagicMock()
        fake_fitz.open.return_value = fake_doc

        import builtins

        original_import = builtins.__import__

        def _inject_fitz(name, *args, **kwargs):
            if name == "fitz":
                return fake_fitz
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _inject_fitz)
        pages, method = preclassify.detect_pages_with_tables("/fake/path.pdf")
        # page 1 has a table; neighbor padding adds pages 0 and 2
        assert pages == {0, 1, 2}
        assert method == "vector"


# ---------------------------------------------------------------------------
# PreClassification serialization round-trip
# ---------------------------------------------------------------------------


class TestPreclassifyPageSetLogging:
    def test_summary_is_info_with_compact_ranges_and_failures_warn(
        self, tmp_path, monkeypatch, caplog
    ):
        """RFC-052 R1 AC8 / R2 AC7: the page-set summary is one INFO line with
        the table pages as compact ranges; a detection failure and a missing
        pdf_inspector (both silently "TableFormer everywhere") log WARNING."""
        import logging

        fitz = pytest.importorskip("fitz")
        from pageindex_mcp.converters import docling_conv, preclassify

        assert preclassify._compact_ranges(set(range(12)) | set(range(274, 292))) == (
            "0-11,274-291"
        )
        assert preclassify._compact_ranges([7, 5, 5]) == "5,7"
        assert preclassify._compact_ranges(None) is None

        caplog.set_level(logging.DEBUG, logger=preclassify.logger.name)
        preclassify._log_page_set_summary(
            "/x.pdf",
            pdf_type="text_based",
            page_count=300,
            pages_with_tables=set(range(12)) | set(range(274, 292)),
            detection_method="vector",
            pages_needing_ocr=[3],
        )
        summary = caplog.records[-1]
        assert summary.levelno == logging.INFO
        assert summary.attrs == {
            "pdf_type": "text_based",
            "page_count": 300,
            "pages_with_tables": "0-11,274-291",
            "pages_with_tables_count": 30,
            "detection_method": "vector",
            "pages_needing_ocr": "3",
            "pages_needing_ocr_count": 1,
        }

        def boom(_path):
            raise RuntimeError("tuple items")

        caplog.clear()
        monkeypatch.setattr(preclassify, "detect_pages_with_tables", boom)
        assert preclassify._detect_tables_if_text_based("/x.pdf", "text_based") == (None, None)
        assert [r.levelno for r in caplog.records] == [logging.WARNING]

        # pdf_inspector missing: pdf_type stays unknown, detection never runs.
        doc = fitz.open()
        doc.new_page()
        path = str(tmp_path / "one.pdf")
        doc.save(path)
        doc.close()
        caplog.clear()
        monkeypatch.setattr(docling_conv, "_pdf_inspector_available", False)
        result = preclassify.preclassify_document(path, "one.pdf")
        assert result.pages_with_tables is None
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("pdf_inspector not installed" in m for m in warnings), warnings
        summaries = [r for r in caplog.records if r.getMessage().startswith("preclassify page")]
        assert summaries[0].levelno == logging.INFO
        assert summaries[0].attrs["pages_with_tables"] is None


class TestPreClassificationTablesSerialization:
    def test_pages_with_tables_roundtrip(self):
        from pageindex_mcp.converters.preclassify import PreClassification

        pc = PreClassification(pages_with_tables={2, 0, 5})
        d = pc.to_dict()
        assert d["pages_with_tables"] == [0, 2, 5]

        restored = PreClassification.from_dict(d)
        assert restored.pages_with_tables == {0, 2, 5}

    def test_pages_with_tables_none_absent_from_dict(self):
        from pageindex_mcp.converters.preclassify import PreClassification

        pc = PreClassification(pages_with_tables=None)
        d = pc.to_dict()
        assert "pages_with_tables" not in d

        restored = PreClassification.from_dict(d)
        assert restored.pages_with_tables is None


# ---------------------------------------------------------------------------
# _pdf_to_markdown_docling_chunked per-chunk do_table_structure logic
# ---------------------------------------------------------------------------


class TestChunkedDoclingTableStructure:
    """Verify per-chunk do_table_structure is computed from pages_with_tables."""

    def test_pages_with_tables_none_all_chunks_get_true(self, tmp_path, monkeypatch):
        fitz = pytest.importorskip("fitz")
        from pageindex_mcp.converters import docling_conv

        doc = fitz.open()
        for _ in range(30):
            doc.new_page()
        path = str(tmp_path / "test.pdf")
        doc.save(path)
        doc.close()

        seen: list[bool] = []
        lock = threading.Lock()

        def fake_chunk(chunk_path, *, do_table_structure=True, **_kw):
            with lock:
                seen.append(do_table_structure)
            return "chunk", [], {}

        monkeypatch.setattr(docling_conv, "_run_docling_chunk_with_timeout", fake_chunk)
        docling_conv._pdf_to_markdown_docling_chunked(
            path,
            page_count=30,
            max_pages=10,
            pages_with_tables=None,
        )
        assert all(seen), f"Expected all True, got {seen}"
        assert len(seen) == 3

    def test_pages_with_tables_targets_first_chunk_only(self, tmp_path, monkeypatch):
        fitz = pytest.importorskip("fitz")
        from pageindex_mcp.converters import docling_conv

        doc = fitz.open()
        for _ in range(30):
            doc.new_page()
        path = str(tmp_path / "test.pdf")
        doc.save(path)
        doc.close()

        seen: dict[int, bool] = {}
        lock = threading.Lock()
        call_count = {"n": 0}

        def fake_chunk(chunk_path, *, do_table_structure=True, **_kw):
            with lock:
                idx = call_count["n"]
                call_count["n"] += 1
                seen[idx] = do_table_structure
            return "chunk", [], {}

        monkeypatch.setattr(docling_conv, "_run_docling_chunk_with_timeout", fake_chunk)
        # After neighbor padding, {0, 5} becomes {0, 1, 4, 5, 6}
        docling_conv._pdf_to_markdown_docling_chunked(
            path,
            page_count=30,
            max_pages=10,
            pages_with_tables={0, 1, 4, 5, 6},
        )
        assert seen[0] is True  # pages 0-9, tables on pages 0,1,4,5,6
        assert seen[1] is False  # pages 10-19, no tables
        assert seen[2] is False  # pages 20-29, no tables


# ---------------------------------------------------------------------------
# PdfConvertRequest pages_with_tables field
# ---------------------------------------------------------------------------


class TestPdfConvertRequestField:
    def test_default_is_none(self, docling_service_app):
        req = docling_service_app.PdfConvertRequest(presigned_url="https://example.com/test.pdf")
        assert req.pages_with_tables is None


# ---------------------------------------------------------------------------
# _remote_pdf_to_markdown payload threading
# ---------------------------------------------------------------------------


class TestRemotePdfToMarkdownPayload:
    def test_pages_with_tables_in_payload(self, monkeypatch):
        """The pages_with_tables kwarg reaches the JSON payload."""
        import asyncio

        from pageindex_mcp.client import remote

        captured_payload = {}

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"markdown": "# test", "picture_results": []}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, *, json=None, headers=None):
                captured_payload.update(json)
                return FakeResponse()

        monkeypatch.setattr(
            remote,
            "settings",
            MagicMock(
                pii_corpus=False,
                docling_service_url="http://fake:8000",
                docling_service_bearer_token="",
                docling_service_timeout_s=30,
            ),
        )
        monkeypatch.setattr(
            "pageindex_mcp.storage.presigned_get_url", lambda k: f"http://minio/{k}"
        )

        async def fake_check(client):
            pass

        monkeypatch.setattr(remote, "_check_remote_docling_version", fake_check)

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: FakeClient())

        md, pics = asyncio.run(
            remote._remote_pdf_to_markdown(
                "test-key",
                pages_with_tables=[0, 2],
            )
        )
        assert captured_payload["pages_with_tables"] == [0, 2]
        assert md == "# test"


# ---------------------------------------------------------------------------
# Cascade enhancement tests (Task 10.4a)
# ---------------------------------------------------------------------------


class TestPageHasColumnAlignment:
    def test_returns_true_for_two_column_table(self):
        from pageindex_mcp.converters.preclassify import _page_has_column_alignment

        blocks = []
        for row in range(5):
            blocks.append((100.0, float(row * 20), 200.0, float(row * 20 + 15)))
            blocks.append((300.0, float(row * 20), 400.0, float(row * 20 + 15)))
        page = _fake_page_with_blocks(blocks)
        assert _page_has_column_alignment(page) is True

    def test_returns_false_for_single_column_prose(self):
        from pageindex_mcp.converters.preclassify import _page_has_column_alignment

        blocks = [(50.0, float(row * 20), 500.0, float(row * 20 + 15)) for row in range(10)]
        page = _fake_page_with_blocks(blocks)
        assert _page_has_column_alignment(page) is False

    def test_quantization_groups_nearby_x_coords(self):
        from pageindex_mcp.converters.preclassify import _page_has_column_alignment

        blocks = []
        for row in range(4):
            blocks.append((96.0 + row * 1, float(row * 20), 200.0, float(row * 20 + 15)))
            blocks.append((288.0 + row * 1, float(row * 20), 400.0, float(row * 20 + 15)))
        page = _fake_page_with_blocks(blocks)
        assert _page_has_column_alignment(page, min_blocks_per_col=3) is True


class TestAddNeighborPadding:
    def test_pads_both_sides(self):
        from pageindex_mcp.converters.preclassify import _add_neighbor_padding

        assert _add_neighbor_padding({2, 5}, page_count=10) == {1, 2, 3, 4, 5, 6}

    def test_clamps_to_bounds(self):
        from pageindex_mcp.converters.preclassify import _add_neighbor_padding

        assert _add_neighbor_padding({0}, page_count=3) == {0, 1}


class TestCascadeDetection:
    def test_column_alignment_detects_borderless_table(self, monkeypatch):
        from pageindex_mcp.converters import preclassify

        cfg = MagicMock()
        cfg.allow_agpl_fallback = True
        monkeypatch.setattr("pageindex_mcp.config.pipeline_config", cfg)
        monkeypatch.setenv("TABLEFORMER_SKIP_ENABLED", "1")

        col_blocks = []
        for row in range(5):
            col_blocks.append((100.0, float(row * 20), 200.0, float(row * 20 + 15)))
            col_blocks.append((300.0, float(row * 20), 400.0, float(row * 20 + 15)))

        page_with_cols = MagicMock()
        page_with_cols.get_cdrawings.return_value = []
        page_with_cols.get_text.return_value = [
            (x0, y0, x1, y1, "text", 0, 0) for x0, y0, x1, y1 in col_blocks
        ]

        page_empty = MagicMock()
        page_empty.get_cdrawings.return_value = []
        page_empty.get_text.return_value = []

        fake_doc = MagicMock()
        fake_doc.__len__ = MagicMock(return_value=3)
        fake_doc.__getitem__ = MagicMock(
            side_effect=lambda idx: page_with_cols if idx == 1 else page_empty
        )
        fake_doc.__enter__ = MagicMock(return_value=fake_doc)
        fake_doc.__exit__ = MagicMock(return_value=False)

        fake_fitz = MagicMock()
        fake_fitz.open.return_value = fake_doc

        import builtins

        original_import = builtins.__import__

        def _inject_fitz(name, *args, **kwargs):
            if name == "fitz":
                return fake_fitz
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _inject_fitz)
        pages, method = preclassify.detect_pages_with_tables("/fake/path.pdf")
        # page 1 detected via column alignment; padding adds 0 and 2
        assert pages == {0, 1, 2}
        assert method == "column_alignment"

    def test_both_methods_fire(self, monkeypatch):
        from pageindex_mcp.converters import preclassify

        cfg = MagicMock()
        cfg.allow_agpl_fallback = True
        monkeypatch.setattr("pageindex_mcp.config.pipeline_config", cfg)
        monkeypatch.setenv("TABLEFORMER_SKIP_ENABLED", "1")

        page_ruled = _fake_page(h_lines=5, v_lines=5)
        page_ruled.get_text.return_value = []

        col_blocks = [(100.0, float(r * 20), 200.0, float(r * 20 + 15)) for r in range(5)]
        col_blocks += [(300.0, float(r * 20), 400.0, float(r * 20 + 15)) for r in range(5)]
        page_col = MagicMock()
        page_col.get_cdrawings.return_value = []
        page_col.get_text.return_value = [
            (x0, y0, x1, y1, "text", 0, 0) for x0, y0, x1, y1 in col_blocks
        ]

        fake_doc = MagicMock()
        fake_doc.__len__ = MagicMock(return_value=4)
        fake_doc.__getitem__ = MagicMock(
            side_effect=lambda idx: (
                page_ruled
                if idx == 0
                else page_col
                if idx == 2
                else MagicMock(
                    get_cdrawings=MagicMock(return_value=[]),
                    get_text=MagicMock(return_value=[]),
                )
            )
        )
        fake_doc.__enter__ = MagicMock(return_value=fake_doc)
        fake_doc.__exit__ = MagicMock(return_value=False)

        fake_fitz = MagicMock()
        fake_fitz.open.return_value = fake_doc

        import builtins

        original_import = builtins.__import__

        def _inject_fitz(name, *args, **kwargs):
            if name == "fitz":
                return fake_fitz
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _inject_fitz)
        pages, method = preclassify.detect_pages_with_tables("/fake/path.pdf")
        assert 0 in pages
        assert 2 in pages
        assert method == "vector+column_alignment"


class TestDetectionMethodSerialization:
    def test_detection_method_roundtrip(self):
        from pageindex_mcp.converters.preclassify import PreClassification

        pc = PreClassification(
            pages_with_tables={1, 2},
            detection_method="vector+column_alignment",
        )
        d = pc.to_dict()
        assert d["detection_method"] == "vector+column_alignment"

        restored = PreClassification.from_dict(d)
        assert restored.detection_method == "vector+column_alignment"
