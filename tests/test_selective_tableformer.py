"""Tests for selective TableFormer processing (RFC-050 D8).

Covers: table detection heuristic, PreClassification serialization,
per-chunk do_table_structure threading, and parameter forwarding through
the remote/service path.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ruled_pdf_page(h_lines: int, v_lines: int, *, rects: bool = False):
    """A REAL fitz page (saved + reopened) with *h_lines* horizontal and
    *v_lines* vertical rules, drawn as lines or as thin filled rects.

    RFC-052 R2 AC2: the old fakes handed ``_page_has_ruled_table`` Point/Rect
    objects, so the suite stayed green while every real ``get_cdrawings()``
    item -- a plain tuple -- raised AttributeError in production.
    """
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    for i in range(h_lines):
        y = 100 + i * 20
        if rects:
            page.draw_rect(fitz.Rect(50, y, 300, y + 1), fill=(0, 0, 0))
        else:
            page.draw_line((50, y), (300, y))
    for i in range(v_lines):
        x = 50 + i * 100
        if rects:
            page.draw_rect(fitz.Rect(x, 100, x + 1, 160), fill=(0, 0, 0))
        else:
            page.draw_line((x, 100), (x, 160))
    reopened = fitz.open("pdf", doc.tobytes())
    return reopened, reopened[0]


class TestPageHasRuledTable:
    @pytest.mark.parametrize(
        ("h_lines", "v_lines", "rects", "expected"),
        [(2, 2, False, False), (3, 3, False, True), (3, 3, True, True)],
        ids=["below-threshold", "line-items", "rect-items"],
    )
    def test_real_cdrawings_tuple_items(self, h_lines, v_lines, rects, expected):
        from pageindex_mcp.converters.preclassify import _page_has_ruled_table

        doc, page = _ruled_pdf_page(h_lines, v_lines, rects=rects)
        with doc:
            items = [it for d in page.get_cdrawings() for it in d["items"]]
            # The fixture must exercise the tuple form, or it proves nothing.
            assert items and all(isinstance(it[1], tuple) for it in items)
            assert _page_has_ruled_table(page) is expected

    def test_one_failing_page_is_marked_positive_alone(self, tmp_path, monkeypatch, caplog):
        """R2 AC2: a raise on one page marks only that page (safe default),
        logs WARNING, and detection still classifies every other page."""
        import logging

        fitz = pytest.importorskip("fitz")
        from pageindex_mcp.converters import preclassify

        cfg = MagicMock()
        cfg.allow_agpl_fallback = True
        monkeypatch.setattr("pageindex_mcp.config.pipeline_config", cfg)
        monkeypatch.setenv("TABLEFORMER_SKIP_ENABLED", "1")
        doc = fitz.open()
        for _ in range(7):
            doc.new_page().insert_text((72, 72), "Plain prose on a page without any table. " * 3)
        path = str(tmp_path / "seven.pdf")
        doc.save(path)
        doc.close()

        real = preclassify._page_has_ruled_table

        def flaky(page, **kw):
            if page.number == 3:
                raise AttributeError("'tuple' object has no attribute 'x'")
            return real(page, **kw)

        monkeypatch.setattr(preclassify, "_page_has_ruled_table", flaky)
        caplog.set_level(logging.WARNING, logger=preclassify.logger.name)
        pages, _method = preclassify.detect_pages_with_tables(path)
        assert pages == {2, 3, 4}  # page 3 plus its +/-1 padding, nothing else
        assert any("page 3" in r.getMessage() for r in caplog.records)


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

    def test_real_pdf_page_classes_and_padding(self, tmp_path, monkeypatch):
        """RFC-052 R2 AC3-5 on a REAL 12-page PDF: prose, a borderless column
        table (p2), a ruled table (p6), a raster image (p9) and a blank page
        (p11). Tables are padded +/-1; the blank page keeps TableFormer (text
        signals cannot see a table there). find_tables() confirmation narrows
        only the cheap positives."""
        fitz = pytest.importorskip("fitz")
        from pageindex_mcp.converters import preclassify

        cfg = MagicMock()
        cfg.allow_agpl_fallback = True
        monkeypatch.setattr("pageindex_mcp.config.pipeline_config", cfg)
        monkeypatch.setenv("TABLEFORMER_SKIP_ENABLED", "1")

        prose = "The insured person must report every claim within thirty days. " * 12
        doc = fitz.open()
        for i in range(12):
            page = doc.new_page()
            if i == 11:
                continue
            if i == 2:
                for r in range(5):
                    for c, x in enumerate((72, 230, 390)):
                        page.insert_text((x, 100 + r * 18), f"Region {r} value {c}")
                continue
            page.insert_textbox(fitz.Rect(72, 72, 540, 400), prose)
            if i == 6:
                for k in range(3):
                    page.draw_line((72, 450 + k * 20), (400, 450 + k * 20))
                    page.draw_line((72 + k * 150, 450), (72 + k * 150, 490))
            if i == 9:
                pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40))
                pix.clear_with(128)
                page.insert_image(fitz.Rect(72, 420, 272, 620), pixmap=pix)
        path = str(tmp_path / "twelve.pdf")
        doc.save(path)
        doc.close()

        classes, method = preclassify.detect_page_classes(path)
        assert [pc.flags for pc in classes] == [
            "T--", "T-t", "T-t", "T-t", "T--", "T-t",
            "T-t", "T-t", "T--", "Ti-", "T-t", "--t",
        ]  # fmt: skip
        assert method == "vector+column_alignment+no_text_layer"
        pages, method2 = preclassify.detect_pages_with_tables(path)
        assert pages == {1, 2, 3, 5, 6, 7, 10, 11}
        assert method2 == method

        confirmed, cmethod = preclassify.classify_pages(path, confirm_tables=True)
        assert [i for i, pc in enumerate(confirmed) if pc.has_tables] == [10, 11]
        assert cmethod == "no_text_layer+find_tables"


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
            "page_class_counts": None,
            "pageclass_ocr_pages": None,
        }

        def boom(_path):
            raise RuntimeError("tuple items")

        caplog.clear()
        monkeypatch.setattr(preclassify, "detect_page_classes", boom)
        assert preclassify._detect_page_classes_safe("/x.pdf") == (None, None)
        assert [r.levelno for r in caplog.records] == [logging.WARNING]
        monkeypatch.undo()

        # pdf_inspector missing: pdf_type stays unknown (WARNING), but page
        # classification no longer depends on it (RFC-052 R2 AC1). A blank
        # page has no text layer, so it keeps OCR and TableFormer.
        cfg = MagicMock()
        cfg.allow_agpl_fallback = True
        monkeypatch.setattr("pageindex_mcp.config.pipeline_config", cfg)
        doc = fitz.open()
        doc.new_page()
        path = str(tmp_path / "one.pdf")
        doc.save(path)
        doc.close()
        caplog.clear()
        monkeypatch.setattr(docling_conv, "_pdf_inspector_available", False)
        result = preclassify.preclassify_document(path, "one.pdf")
        assert result.pages_with_tables == {0}
        assert [pc.flags for pc in result.page_classes] == ["--t"]
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("pdf_inspector not installed" in m for m in warnings), warnings
        summaries = [r for r in caplog.records if r.getMessage().startswith("preclassify page")]
        assert summaries[0].levelno == logging.INFO
        assert summaries[0].attrs["pages_with_tables"] == "0"
        assert summaries[0].attrs["page_class_counts"] == {"--t": 1}
        assert summaries[0].attrs["pageclass_ocr_pages"] == "0"


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
        """Both optional fields default to None, so an older worker's body
        still validates; a missing or malformed page_classes means "no page
        classes" (every model on), never a 422 (RFC-052 R2 AC6/AC7)."""
        app = docling_service_app
        req = app.PdfConvertRequest(presigned_url="https://example.com/test.pdf")
        assert req.pages_with_tables is None
        assert req.page_classes is None
        assert app._request_page_classes(req) is None

        ok = app.PdfConvertRequest(
            presigned_url="https://example.com/test.pdf",
            page_classes=[[0, 1, "T--"], [2, 2, "T-t"]],
        )
        assert [pc.flags for pc in app._request_page_classes(ok)] == ["T--", "T--", "T-t"]

        bad = app.PdfConvertRequest(
            presigned_url="https://example.com/test.pdf", page_classes=[[5, 1, "T--"], 7]
        )
        assert app._request_page_classes(bad) is None


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
                # QA fix 2 (coldstart): _remote_pdf_to_markdown now clamps the
                # read timeout to the converter child's remaining deadline and
                # refuses to dial out at all below _MIN_USEFUL_CALL_S (60s) of
                # remaining time. No child deadline is set in this test (no
                # PAGEINDEX_CHILD_DEADLINE_EPOCH env var), so the configured
                # value IS the effective read timeout -- keep it realistic
                # (prod default) rather than a value the new floor would
                # itself reject as "not enough time for a useful call".
                docling_service_timeout_s=600,
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

        ranges = [[0, 1, "T--"], [2, 2, "T-t"]]
        md, _pics = asyncio.run(
            remote._remote_pdf_to_markdown(
                "test-key",
                pages_with_tables=[0, 2],
                page_classes=ranges,
            )
        )
        assert captured_payload["pages_with_tables"] == [0, 2]
        # RFC-052 R2 AC6: the run-length page classes travel verbatim.
        assert captured_payload["page_classes"] == ranges
        assert md == "# test"

        captured_payload.clear()
        asyncio.run(remote._remote_pdf_to_markdown("test-key"))
        assert captured_payload["page_classes"] is None


# ---------------------------------------------------------------------------
# Cascade enhancement tests (Task 10.4a)
# ---------------------------------------------------------------------------


def _grid_lines(xs, rows: int, *, jitter: float = 0.0, stagger: float = 0.0):
    """Text-line boxes starting at each x in *xs*, one per row; *stagger*
    shifts each column's rows down so no row is shared across columns."""
    return [
        (x + r * jitter, 100.0 + r * 18 + c * stagger, x + 80.0, 110.0 + r * 18 + c * stagger)
        for r in range(rows)
        for c, x in enumerate(xs)
    ]


class TestPageHasColumnAlignment:
    @pytest.mark.parametrize(
        ("lines", "expected"),
        [
            (_grid_lines((72, 230, 390), 5), True),
            # 1-px drift per row stays inside the 12-px x bins.
            (_grid_lines((96, 230, 384), 4, jitter=1.0), True),
            # Two aligned x-positions -- the old loose test fired on this,
            # which is indented prose, not a table (RFC-052 R2 AC3).
            (_grid_lines((72, 100), 8), False),
            # Three aligned columns whose lines never share a row.
            (_grid_lines((72, 230, 390), 5, stagger=7.0), False),
        ],
        ids=["3x5-table", "quantized-drift", "two-columns", "too-few-rows"],
    )
    def test_strict_alignment(self, lines, expected):
        from pageindex_mcp.converters.preclassify import _page_has_column_alignment

        assert _page_has_column_alignment(lines=lines) is expected


class TestAddNeighborPadding:
    def test_pads_both_sides(self):
        from pageindex_mcp.converters.preclassify import _add_neighbor_padding

        assert _add_neighbor_padding({2, 5}, page_count=10) == {1, 2, 3, 4, 5, 6}

    def test_clamps_to_bounds(self):
        from pageindex_mcp.converters.preclassify import _add_neighbor_padding

        assert _add_neighbor_padding({0}, page_count=3) == {0, 1}


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


class TestPageClassWireFormat:
    def test_run_length_roundtrip_needs_and_malformed_input(self, caplog):
        """RFC-052 R2 AC6: page classes cross the handshake JSON as inclusive
        run-length ranges; UD2 decides needs_ocr; anything that is not a
        gap-free cover of [0, N) is rejected rather than half-applied."""
        import json
        import logging

        from pageindex_mcp.converters.preclassify import (
            PageClass,
            PreClassification,
            page_classes_from_ranges,
            page_classes_to_ranges,
        )

        text_only = PageClass(True, False, False)
        table = PageClass(True, False, True)
        scanned = PageClass(False, True, False)
        classes = [text_only] * 3 + [table] * 2 + [scanned] + [text_only]
        ranges = [[0, 2, "T--"], [3, 4, "T-t"], [5, 5, "-i-"], [6, 6, "T--"]]
        assert page_classes_to_ranges(classes) == ranges

        # UD2: only a text-layer page with no images and no tables skips OCR.
        assert [pc.needs_ocr for pc in (text_only, table, scanned)] == [False, True, True]
        assert [pc.needs_tables for pc in (text_only, table, scanned)] == [False, True, False]

        wire = json.loads(json.dumps(PreClassification(page_classes=classes).to_dict()))
        assert wire["page_classes"] == ranges
        assert PreClassification.from_dict(wire).page_classes == classes
        assert PreClassification.from_dict({}).page_classes is None
        assert "page_classes" not in PreClassification().to_dict()

        for bad in (
            [[1, 2, "T--"]],  # does not start at page 0
            [[0, 1, "T--"], [3, 4, "T--"]],  # gap
            [[0, 1, "T--"], [1, 2, "T--"]],  # overlap
            [[0, 1, "Txx"]],  # unknown flag
            [[0, 1]],  # wrong arity
        ):
            with pytest.raises(ValueError):
                page_classes_from_ranges(bad)
        caplog.set_level(logging.WARNING)
        assert PreClassification.from_dict({"page_classes": [[1, 2, "T--"]]}).page_classes is None
        assert any("malformed page_classes" in r.getMessage() for r in caplog.records)
