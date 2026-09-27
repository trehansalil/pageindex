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


# ---------------------------------------------------------------------------
# RFC-052 P2 (R3, R4): page-class chunking, OCR policy, TableFormer mode
# ---------------------------------------------------------------------------

_TEXT = ("T--", False, False)  # text layer only: needs nothing (UD2)
_TABLE = ("T-t", True, True)  # D5: a table page keeps OCR until R4 says otherwise
_SCAN = ("---", False, True)
_IMAGE = ("Ti-", False, True)


def _classes(*runs):
    from pageindex_mcp.converters.preclassify import PageClass

    return [PageClass.from_flags(flags) for flags, n in runs for _ in range(n)]


def _fake_docling_modules(monkeypatch):
    """Stand-ins for the function-local Docling imports -- never load the real
    (heavy) package in a unit test."""
    import sys
    import types

    ns = types.SimpleNamespace

    class _Opts:
        def __init__(self):
            self.table_structure_options = ns(mode=None)
            self.ocr_options = None

    mods = {
        "docling": {},
        "docling.datamodel": {},
        "docling.datamodel.pipeline_options": {
            "PdfPipelineOptions": _Opts,
            "TableFormerMode": ns(ACCURATE="ACCURATE", FAST="FAST"),
            "TesseractCliOcrOptions": lambda **kw: ns(**kw),
        },
        "docling.datamodel.accelerator_options": {
            "AcceleratorDevice": ns(CPU="cpu"),
            "AcceleratorOptions": lambda **kw: ns(**kw),
        },
        "docling.datamodel.base_models": {"InputFormat": ns(PDF="pdf", IMAGE="image")},
        "docling.document_converter": {
            "DocumentConverter": lambda format_options: ns(format_options=format_options),
            "PdfFormatOption": lambda pipeline_options: ns(pipeline_options=pipeline_options),
        },
    }
    for name, attrs in mods.items():
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)


class TestPageClassChunking:
    def test_chunker_properties_p1_p2_max_and_order(self):
        """RFC-052 R3 AC1-2, D6, design P1/P2: the chunks partition [0, N) in
        page order, never drop a model a page needs (union on absorption),
        never exceed max_pages, and stay within ceil(N/min) + |runs|."""
        import itertools
        import math
        import random

        from pageindex_mcp.converters.page_class_chunker import PageChunk, page_class_chunks

        rng = random.Random(52)
        flag_pool = [_TEXT[0], _TABLE[0], _SCAN[0], _IMAGE[0]]
        cases = [
            _classes((_TEXT[0], 10), (_TABLE[0], 5)),
            _classes((_TEXT[0], 1)),
            _classes(*[(f, 1) for f in flag_pool * 15]),  # alternating single pages
        ]
        for _ in range(150):
            runs = [(rng.choice(flag_pool), rng.randint(1, 25)) for _ in range(rng.randint(1, 8))]
            cases.append(_classes(*runs))
        for classes, (max_pages, min_pages) in itertools.product(
            cases, [(11, 10), (60, 10), (5, 1), (3, 7), (1, 1)]
        ):
            n = len(classes)
            chunks = page_class_chunks(classes, max_pages=max_pages, min_pages=min_pages)
            pages = [p for c in chunks for p in range(c.start, c.end + 1)]
            assert pages == list(range(n)), (classes, chunks)  # P1: exactly once, in order
            for c in chunks:
                assert 1 <= c.page_count <= max_pages
                for p in range(c.start, c.end + 1):  # P2: needs only ever turn ON
                    assert c.needs_tables >= classes[p].needs_tables
                    assert c.needs_ocr >= classes[p].needs_ocr
            runs = len(list(itertools.groupby(classes, lambda pc: (pc.needs_tables, pc.needs_ocr))))
            assert len(chunks) <= math.ceil(n / min(min_pages, max_pages)) + runs

        # The REAL classifier run layout on doc_store/world-stats-pocketbook-
        # 2023.pdf (QA, run-6): 0-2 (T,O) 3 * 3-4 (noT,O) 2 * 5-7 (none) 3 *
        # 8-274 (T,O) 267 * 275-287 (none) 13 * 288-290 (T,O) 3 * 291 (none) 1.
        # noT,O = no text layer, no image, no table (_SCAN's flags "---"):
        # needs_tables False, needs_ocr True. The 275-287 run is >= min_pages
        # on its own and has no covering neighbour (both its neighbours need
        # models it doesn't), so it survives as its own model-free chunk
        # instead of being dragged into an all-on run -- the HIGH finding
        # this fixture replaces (today's algorithm produced ONE all-on run:
        # 0 pages skip anything).
        pocket = _classes(
            (_TABLE[0], 3), (_SCAN[0], 2), (_TEXT[0], 3), (_TABLE[0], 267),
            (_TEXT[0], 13), (_TABLE[0], 3), (_TEXT[0], 1),
        )  # fmt: skip
        chunks = page_class_chunks(pocket, max_pages=60, min_pages=10)
        pages = [p for c in chunks for p in range(c.start, c.end + 1)]
        assert pages == list(range(len(pocket)))  # P1: coverage, order

        free_ranges = [(c.start, c.end) for c in chunks if not (c.needs_tables or c.needs_ocr)]
        assert (275, 287) in free_ranges, chunks

        # P2 plus the amendment: a page never gains a model unless the chunk
        # it landed in already needed it OR the page was absorbed into a
        # neighbour whose needs covered it (checked directly against the
        # known short runs: pages 3-4, 5-7 and 291).
        for c in chunks:
            for p in range(c.start, c.end + 1):
                assert c.needs_tables >= pocket[p].needs_tables
                assert c.needs_ocr >= pocket[p].needs_ocr
        # The two absorbed short prefixes/suffix pick up TableFormer+OCR only
        # because their covering neighbour (a table run) already needed both.
        absorbing_chunk = next(c for c in chunks if c.start <= 3 <= c.end)
        assert absorbing_chunk.needs_tables and absorbing_chunk.needs_ocr
        tail_chunk = next(c for c in chunks if c.start <= 291 <= c.end)
        assert tail_chunk.needs_tables and tail_chunk.needs_ocr
        assert len(chunks) <= 8  # was 1 before the fix (one all-on run)

        # A short run joins a neighbour ONLY when that neighbour already
        # covers its needs; here neither "none" neighbour covers the table
        # run's needs, so it stays its own (short) chunk instead of turning
        # TableFormer+OCR on for one of the 20-page text runs either side.
        iso = _classes((_TEXT[0], 20), (_TABLE[0], 2), (_TEXT[0], 20))
        assert page_class_chunks(iso, max_pages=60, min_pages=5) == [
            PageChunk(0, 19, False, False),
            PageChunk(20, 21, True, True),
            PageChunk(22, 41, False, False),
        ]
        sup = _classes((_TABLE[0], 3), (_SCAN[0], 2), (_TEXT[0], 30))
        assert page_class_chunks(sup, max_pages=60, min_pages=5) == [
            PageChunk(0, 4, True, True),
            PageChunk(5, 34, False, False),
        ]
        assert page_class_chunks([], max_pages=10, min_pages=5) == []
        with pytest.raises(ValueError):
            page_class_chunks(iso, max_pages=0, min_pages=1)

        # RFC-052 R3 AC2 amendment (2026-09-27): a table page every 11th page
        # among otherwise-uniform text used to isolate each lone table page
        # as its own 1-page chunk -- 54 chunks over 297 pages (27 ten-page
        # text runs + 27 one-page table runs), each 1-page chunk a child that
        # reloads the Docling models from scratch. The bounded-upgrade rule
        # merges each lone table page into its (exactly min_pages-sized, tied
        # left) text neighbour, and those newly-T,O 11-page runs then coalesce
        # with each other (every run now shares the same needs), leaving one
        # T,O run for the whole document -- split_to_max then cuts that into
        # ceil(297 / 60) = 5 near-equal chunks instead of 54 lone ones.
        eleventh = _classes(*([(_TEXT[0], 10), (_TABLE[0], 1)] * 27))
        eleventh_chunks = page_class_chunks(eleventh, max_pages=60, min_pages=10)
        assert len(eleventh_chunks) == 5  # was 54 before the amendment
        assert sum(c.page_count for c in eleventh_chunks) == len(eleventh) == 297
        assert all(c.needs_tables and c.needs_ocr for c in eleventh_chunks)

    def test_ocr_policy_tableformer_mode_and_converter_cache_key(self, monkeypatch, caplog):
        """RFC-052 R3 AC3 + R4 AC1 (tasks 5.2, 5.4): DOCLING_DO_OCR is a
        three-way global override, a per-request policy beats it, the
        TableFormer mode is config, and both options key the converter cache
        (a shared key would silently serve one chunk's pipeline to another)."""
        import logging

        from pageindex_mcp.converters import docling_conv as dc

        _fake_docling_modules(monkeypatch)
        monkeypatch.delenv("DOCLING_FORCE_FULL_PAGE_OCR", raising=False)
        monkeypatch.delenv("DOCLING_TABLEFORMER_MODE", raising=False)
        # env, the chunk's needs_ocr -> do_ocr. 0/unset = page-class driven
        # (and with no page classes that is today's "no OCR").
        for env, needs, expected in [
            (None, False, False), (None, True, True), ("0", False, False), ("0", True, True),
            ("1", False, True), ("true", False, True), ("off", True, False),
        ]:  # fmt: skip
            if env is None:
                monkeypatch.delenv("DOCLING_DO_OCR", raising=False)
            else:
                monkeypatch.setenv("DOCLING_DO_OCR", env)
            assert dc._resolve_do_ocr(False, needs_ocr=needs) is expected, (env, needs)
        monkeypatch.setenv("DOCLING_DO_OCR", "1")
        assert dc._resolve_do_ocr(False, needs_ocr=True, policy="force_off") is False
        monkeypatch.setenv("DOCLING_DO_OCR", "off")
        assert dc._resolve_do_ocr(False, needs_ocr=False, policy="force_on") is True
        assert dc._resolve_do_ocr(False, needs_ocr=True, policy="page_class") is True
        with pytest.raises(ValueError):
            dc._ocr_policy("sometimes")
        # An explicit per-chunk do_ocr is the parent's resolved value: env loses.
        assert dc._build_pdf_pipeline_options(do_ocr=True).do_ocr is True
        monkeypatch.setenv("DOCLING_DO_OCR", "1")
        assert dc._build_pdf_pipeline_options(do_ocr=False).do_ocr is False

        caplog.set_level(logging.WARNING)
        modes = []
        for env, override in [(None, None), ("fast", None), ("FAST ", None), ("bogus", None),
                              ("fast", "accurate")]:  # fmt: skip
            if env is None:
                monkeypatch.delenv("DOCLING_TABLEFORMER_MODE", raising=False)
            else:
                monkeypatch.setenv("DOCLING_TABLEFORMER_MODE", env)
            opts = dc._build_pdf_pipeline_options(tableformer_mode=override)
            modes.append(opts.table_structure_options.mode)
        assert modes == ["ACCURATE", "FAST", "FAST", "ACCURATE", "ACCURATE"]
        assert any("bogus" in r.getMessage() for r in caplog.records)

        monkeypatch.delenv("DOCLING_TABLEFORMER_MODE", raising=False)
        monkeypatch.setattr(dc, "_DOCLING_CONVERTER_CACHE", {})
        a = dc._docling_converter(do_ocr=False)
        b = dc._docling_converter(do_ocr=True)
        c = dc._docling_converter(do_ocr=True, tableformer_mode="fast")
        assert len({id(a), id(b), id(c)}) == 3
        assert dc._docling_converter(do_ocr=True) is b
        opts = [conv.format_options["pdf"].pipeline_options for conv in (a, b, c)]
        assert [o.do_ocr for o in opts] == [False, True, True]
        assert [o.table_structure_options.mode for o in opts] == ["ACCURATE", "ACCURATE", "FAST"]

    def test_force_full_page_ocr_beats_page_class_and_global_off(self, tmp_path, monkeypatch):
        """RFC-052 R3 AC4, HR5 (task 5.3): the recovery escalation still OCRs
        text-layer pages -- whose class needs no OCR -- even with the global
        override off and a request policy of force_off."""
        fitz = pytest.importorskip("fitz")
        from pageindex_mcp.converters import docling_conv as dc

        _fake_docling_modules(monkeypatch)
        monkeypatch.setenv("DOCLING_DO_OCR", "off")
        monkeypatch.delenv("DOCLING_FORCE_FULL_PAGE_OCR", raising=False)
        opts = dc._build_pdf_pipeline_options(force_full_page_ocr=True, do_ocr=False)
        assert opts.do_ocr is True and opts.ocr_options.force_full_page_ocr is True

        doc = fitz.open()
        for _ in range(30):
            doc.new_page()
        path = str(tmp_path / "text.pdf")
        doc.save(path)
        doc.close()
        seen = []

        def fake_chunk(chunk_path, *, force_full_page_ocr, do_ocr, **_kw):
            seen.append((force_full_page_ocr, do_ocr))
            return "md", [], {}

        monkeypatch.setattr(dc, "_run_docling_chunk_with_timeout", fake_chunk)
        dc._pdf_to_markdown_docling_chunked(
            path,
            page_count=30,
            max_pages=10,
            force_full_page_ocr=True,
            page_classes=_classes((_TEXT[0], 30)),
            do_ocr_policy="force_off",
        )
        assert seen == [(True, True)] * 3

    def test_chunked_route_page_class_chunks_records_and_kill_switch(
        self, tmp_path, monkeypatch, caplog
    ):
        """RFC-052 R3 AC1/3/5/6, R1 AC7 (tasks 5.2, 5.4): page-class chunks set
        each chunk child's tables/OCR/mode, every page is converted exactly
        once in order, picture pages rebase on the chunk start, the per-chunk
        docling_chunk record carries the real flags, and PAGECLASS_CHUNKING=0
        (or the request override) restores today's uniform chunks."""
        import inspect
        import logging
        import queue

        fitz = pytest.importorskip("fitz")
        from pageindex_mcp.converters import docling_conv as dc
        from pageindex_mcp.converters import pipeline

        doc = fitz.open()
        for i in range(30):
            doc.new_page().insert_text((72, 72), f"p{i}")
        path = str(tmp_path / "mixed.pdf")
        doc.save(path)
        doc.close()
        # 12 text, 13 table, 5 scanned: the 5-page scan run (< MIN_CHUNK_PAGES)
        # joins the table run, which already runs OCR.
        classes = _classes((_TEXT[0], 12), (_TABLE[0], 13), (_SCAN[0], 5))
        lock = threading.Lock()
        calls: list[tuple] = []

        def fake_chunk(chunk_path, *, do_table_structure, do_ocr, tableformer_mode, **_kw):
            with fitz.open(chunk_path) as chunk:
                texts = [p.get_text().strip() for p in chunk]
            with lock:
                calls.append((texts[0], len(texts), do_table_structure, do_ocr, tableformer_mode))
            return " ".join(texts), [{"page": 1}], {}

        monkeypatch.setattr(dc, "_run_docling_chunk_with_timeout", fake_chunk)
        monkeypatch.delenv("DOCLING_DO_OCR", raising=False)
        monkeypatch.delenv("DOCLING_FORCE_FULL_PAGE_OCR", raising=False)
        monkeypatch.delenv("PAGECLASS_CHUNKING", raising=False)
        monkeypatch.setenv("DOCLING_TABLEFORMER_MODE", "fast")
        caplog.set_level(logging.INFO, logger=dc.logger.name)

        def run(**kw):
            calls.clear()
            caplog.clear()
            md, pics, _ = dc._pdf_to_markdown_docling_chunked(
                path, page_count=30, max_pages=10, workers=2, pages_with_tables={3}, **kw
            )
            recs = sorted(
                (r for r in caplog.records if getattr(r, "event", None) == "docling_chunk"),
                key=lambda r: getattr(r, dc.FLAT_FIELDS_ATTR)["page_start"],
            )
            fields = [getattr(r, dc.FLAT_FIELDS_ATTR) for r in recs]
            return md, [p["page"] for p in pics], sorted(calls, key=lambda c: int(c[0][1:])), [
                (f["page_start"], f["page_end"], f["do_table_structure"], f["do_ocr"],
                 f["tableformer_mode"])
                for f in fields
            ]  # fmt: skip

        md, pic_pages, got, recs = run(page_classes=classes)
        assert md == " ".join(f"p{i}" for i in range(30)).replace("p5 p6", "p5\n\np6").replace(
            "p11 p12", "p11\n\np12"
        ).replace("p20 p21", "p20\n\np21")
        assert pic_pages == [1, 7, 13, 22]
        # pages_with_tables={3} ORs TableFormer into the first text chunk only.
        assert got == [
            ("p0", 6, True, False, "fast"),
            ("p6", 6, False, False, "fast"),
            ("p12", 9, True, True, "fast"),
            ("p21", 9, True, True, "fast"),
        ]
        assert recs == [
            (0, 5, True, False, "fast"),
            (6, 11, False, False, "fast"),
            (12, 20, True, True, "fast"),
            (21, 29, True, True, "fast"),
        ]

        # RFC-052 P2 finding 1 (R2 AC7): with page classes inactive -- kill
        # switch or a length mismatch -- do_ocr must degrade to "on" (today's
        # pre-R3 behaviour), the same way do_table_structure already degrades
        # to "on" wherever pages_with_tables does not force it off. A scanned
        # page must never silently lose OCR just because its page-class list
        # was malformed, mismatched, or chunking was switched off.
        uniform = [
            ("p0", 10, True, True, "fast"),
            ("p10", 10, False, True, "fast"),
            ("p20", 10, False, True, "fast"),
        ]
        assert run(page_classes=classes, pageclass_chunking=False)[2] == uniform
        assert run(page_classes=classes[:-1])[2] == uniform  # length mismatch: degrade, OCR on
        monkeypatch.setenv("PAGECLASS_CHUNKING", "0")
        md, pic_pages, got, recs = run(page_classes=classes)
        assert (got, pic_pages) == (uniform, [1, 11, 21])
        assert [r[:2] for r in recs] == [(0, 9), (10, 19), (20, 29)]
        monkeypatch.delenv("PAGECLASS_CHUNKING")

        # The child hands do_ocr/tableformer_mode to the real pipeline signature.
        real = inspect.signature(pipeline.pdf_to_markdown_docling)
        child_kw = []

        def fake_pipeline(*a, **kw):
            real.bind(*a, **kw)
            child_kw.append((kw["pages_with_tables"], kw["do_ocr"], kw["tableformer_mode"]))
            return "md", [], {}

        monkeypatch.setattr(pipeline, "pdf_to_markdown_docling", fake_pipeline)
        q = queue.Queue()
        dc._docling_chunk_worker(
            q, path, False, None, do_table_structure=False, do_ocr=True, tableformer_mode="fast"
        )
        assert q.get_nowait()[0] == "ok"
        assert child_kw == [(set(), True, "fast")]

    def test_direct_path_and_service_thread_page_classes(
        self, tmp_path, monkeypatch, docling_service_app
    ):
        """RFC-052 R3 AC3 on the single-pass route (the document is one chunk:
        the union of its pages' needs), and the service wiring: /convert/pdf
        hands the parsed page classes and the bench's per-request overrides to
        the pipeline (task 5.2)."""
        import asyncio

        import pydantic

        fitz = pytest.importorskip("fitz")
        from pageindex_mcp.converters import pipeline

        doc = fitz.open()
        for _ in range(4):
            doc.new_page()
        path = str(tmp_path / "small.pdf")
        doc.save(path)
        doc.close()
        built = []

        class _Stop(Exception):
            pass

        def fake_converter(**kw):
            built.append((kw["do_table_structure"], kw["do_ocr"], kw["tableformer_mode"]))
            raise _Stop

        monkeypatch.setattr(pipeline, "_docling_converter", fake_converter)
        monkeypatch.delenv("DOCLING_DO_OCR", raising=False)
        monkeypatch.delenv("PAGECLASS_CHUNKING", raising=False)
        monkeypatch.delenv("DOCLING_TABLEFORMER_MODE", raising=False)
        for kw in (
            {"page_classes": _classes((_TEXT[0], 4))},
            {"page_classes": _classes((_TEXT[0], 3), (_IMAGE[0], 1))},
            {"page_classes": _classes((_TEXT[0], 3), (_TABLE[0], 1)), "tableformer_mode": "fast"},
            {"page_classes": _classes((_TEXT[0], 4)), "pageclass_chunking": False},
            {},
        ):
            with pytest.raises(_Stop):
                pipeline.pdf_to_markdown_docling(path, max_pages=100, **kw)
        # tableformer_mode None: the converter resolves DOCLING_TABLEFORMER_MODE.
        # RFC-052 P2 finding 1 (R2 AC7): with page classes inactive (kill
        # switch, or none supplied at all) do_ocr must degrade to "on" --
        # today's pre-R3 options, every model on -- not silently to "off".
        assert built == [
            (False, False, None),
            (False, True, None),
            (True, True, "fast"),
            (True, True, None),  # kill switch: today's options (every model on)
            (True, True, None),  # no page classes: today's options (every model on)
        ]

        app = docling_service_app
        captured = []

        async def fake_download(url, suffix=".pdf"):
            tmp = tmp_path / f"dl{len(captured)}.pdf"
            tmp.write_bytes(b"%PDF")
            return str(tmp)

        def fake_pdf(pdf_path, **kw):
            captured.append(kw)
            return "# md", [], {}

        monkeypatch.setattr(app, "_download_to_temp", fake_download)
        monkeypatch.setattr(app, "_pdf_page_count", lambda _p: 3)
        monkeypatch.setattr("pageindex_mcp.converters.pdf_to_markdown_docling", fake_pdf)

        class _ConnectedClient:  # convert_pdf watches its client (cancel)
            async def is_disconnected(self):
                return False

        def _convert(req):
            return app.convert_pdf(req, _ConnectedClient())

        url = "https://example.com/x.pdf"
        resp0 = asyncio.run(
            _convert(
                app.PdfConvertRequest(
                    presigned_url=url,
                    page_classes=[[0, 1, "T--"], [2, 2, "T-t"]],
                    tableformer_mode="fast",
                    pageclass_chunking=True,
                    do_ocr_policy="page_class",
                )
            )
        )
        # RFC-052 P2 finding 3: the response echoes what the converter
        # actually used -- page classes active (after validation, not just
        # the request echo), the resolved OCR policy, route and chunk count
        # -- so a bench arm can verify from the response itself that its
        # overrides really applied rather than silently falling back.
        assert resp0.applied == {
            "tableformer_mode": "fast",
            "pageclass_chunking": True,
            "do_ocr_policy": "page_class",
            "page_classes_active": True,
            "route": "direct",
            "chunk_count": 1,
        }
        monkeypatch.delenv("DOCLING_DO_OCR", raising=False)  # 0: page-class driven
        asyncio.run(_convert(app.PdfConvertRequest(presigned_url=url)))
        asyncio.run(
            _convert(
                app.PdfConvertRequest(
                    presigned_url=url, page_classes=[[0, 2, "T--"]], pageclass_chunking=False
                )
            )
        )
        # RFC-052 P2 finding 2: the shipped Dockerfile/docker-compose set
        # DOCLING_DO_OCR=0 (page-class driven) explicitly, not merely leave
        # it unset -- with no page classes on the request that must still
        # force OCR on, same as the absent-env case above.
        monkeypatch.setenv("DOCLING_DO_OCR", "0")
        asyncio.run(_convert(app.PdfConvertRequest(presigned_url=url)))
        monkeypatch.setenv("DOCLING_DO_OCR", "off")
        asyncio.run(_convert(app.PdfConvertRequest(presigned_url=url)))
        first, older_worker, switched_off, env_zero, env_off = captured
        assert [pc.flags for pc in first["page_classes"]] == ["T--", "T--", "T-t"]
        assert (first["tableformer_mode"], first["pageclass_chunking"]) == ("fast", True)
        assert first["do_ocr_policy"] == "page_class"
        assert [older_worker[k] for k in ("page_classes", "tableformer_mode",
                "pageclass_chunking")] == [None] * 3  # fmt: skip
        # R2 AC7: with no page classes in effect (absent, or the kill switch)
        # every model stays on -- as the service ran before R3 -- rather than
        # page-class-driven OCR silently becoming "no OCR". An explicit global
        # off is still honoured. DOCLING_DO_OCR=0 (the shipped env) behaves
        # exactly like the absent-env case, never "no OCR" with no classes.
        assert (
            older_worker["do_ocr_policy"]
            == switched_off["do_ocr_policy"]
            == env_zero["do_ocr_policy"]
            == "force_on"
        )
        assert env_off["do_ocr_policy"] is None
        with pytest.raises(pydantic.ValidationError):
            app.PdfConvertRequest(presigned_url=url, tableformer_mode="turbo")
