"""RFC-035 D2 tests — Batch 3 landscape orientation detection and
rasterize-rotate-reextract fallback.

Covers:
  Task 5.5. ``_probe_landscape_pages`` (Phase 1) correctly tags landscape
     pages (rotated or wide-aspect) and leaves portrait pages untagged.
  Task 5.6. ``_landscape_pages_below_threshold`` (Phase 2 trigger) only
     flags pages that are BOTH landscape-tagged AND below
     ``LANDSCAPE_CHAR_THRESHOLD`` — a landscape page with plenty of text, or
     a sparse portrait page, must not be flagged.
  Task 5.7. ``_landscape_rasterize_rotate_reextract`` (Phase 2) falls
     through (logs a warning, returns no result for that page) rather than
     raising when rasterization fails.
  Task 5.8. Routing re-evaluation: a Phase 2 re-extraction that produces
     ``PictureResults`` (tagged ``skipped_reason="landscape_fallback_picture"``
     by the landscape rasterize-rotate-reextract fallback in converters.py)
     must re-route the document to the flat-mixed path instead of letting
     it stay on the tree path (Design Key Design Principle 8 / Launch
     Constraint 5); a re-extraction with NO ``PictureResults`` must leave
     the document on its original (tree) routing path — the reroute guard
     must not fire on char-count/validate_tree success alone.

Mirrors the ``client.py::index()`` wiring pattern used by
test_pdf_inspector_tier1.py and test_rfc030_d0_d1.py.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("fitz")
import fitz

import pageindex_mcp.client as client_mod
from pageindex_mcp import converters
from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.converters import (
    _landscape_pages_below_threshold,
    _landscape_rasterize_rotate_reextract,
    _probe_landscape_pages,
)


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
    """Task 5.5: Property 3 (partial) — landscape pages are correctly tagged."""

    def test_rotated_wide_page_is_tagged_landscape(self, tmp_path):
        path = _make_pdf(tmp_path, "rot90.pdf", width=600, height=800, rotate=90)
        pages = _probe_landscape_pages(path)
        assert len(pages) == 1
        assert pages[0]["is_landscape"] is True
        assert pages[0]["rotate"] == 90

    def test_portrait_page_is_not_tagged_landscape(self, tmp_path):
        path = _make_pdf(tmp_path, "portrait.pdf", width=600, height=800, rotate=0)
        pages = _probe_landscape_pages(path)
        assert len(pages) == 1
        assert pages[0]["is_landscape"] is False
        assert pages[0]["rotate"] == 0


class TestFallbackTriggerSkip:
    """Task 5.6: Property 3 (partial) — fallback triggers only when
    landscape AND below-threshold."""

    @staticmethod
    def _mock_document(char_count):
        item = SimpleNamespace(text="x" * char_count)
        doc = MagicMock()
        doc.iterate_items.return_value = [(item, 0)]
        return doc

    def test_landscape_page_below_threshold_is_flagged(self, monkeypatch):
        # RFC-036 D0c: a below-threshold landscape page is only flagged when
        # it also carries a detectable picture/graphic region (page 1,
        # 1-indexed) — otherwise dense numeric-table pages false-positive.
        monkeypatch.setattr(
            converters, "_collect_picture_regions", lambda doc: [{"page": 1, "bbox": {}}]
        )
        landscape_pages = [{"page_no": 0, "rotate": 0, "is_landscape": True}]
        document = self._mock_document(200)
        below = _landscape_pages_below_threshold(document, landscape_pages)
        assert len(below) == 1
        assert below[0]["page_no"] == 0
        assert below[0]["char_count"] == 200

    def test_landscape_page_below_threshold_without_picture_is_not_flagged(
        self, monkeypatch
    ):
        # RFC-036 D0c: dense numeric-table pages fall below the char
        # threshold but carry no picture region, so they no longer
        # false-positive trigger the rasterize-rotate-reextract fallback.
        monkeypatch.setattr(converters, "_collect_picture_regions", lambda doc: [])
        landscape_pages = [{"page_no": 0, "rotate": 0, "is_landscape": True}]
        document = self._mock_document(200)
        below = _landscape_pages_below_threshold(document, landscape_pages)
        assert below == []

    def test_landscape_page_above_threshold_is_not_flagged(self, monkeypatch):
        monkeypatch.setattr(
            converters, "_collect_picture_regions", lambda doc: [{"page": 1, "bbox": {}}]
        )
        landscape_pages = [{"page_no": 0, "rotate": 0, "is_landscape": True}]
        document = self._mock_document(2000)
        below = _landscape_pages_below_threshold(document, landscape_pages)
        assert below == []

    def test_portrait_page_below_threshold_is_not_flagged(self, monkeypatch):
        # Guards against false-positive rescue of legitimately sparse
        # portrait pages (e.g. cover/divider pages).
        monkeypatch.setattr(
            converters, "_collect_picture_regions", lambda doc: [{"page": 1, "bbox": {}}]
        )
        landscape_pages = [{"page_no": 0, "rotate": 0, "is_landscape": False}]
        document = self._mock_document(200)
        below = _landscape_pages_below_threshold(document, landscape_pages)
        assert below == []


class TestRasterizationFailureFallthrough:
    """Task 5.7: rasterization failure logs a warning and falls through
    rather than raising (Design Error Handling item 6)."""

    def test_rasterize_failure_falls_through_without_raising(
        self, tmp_path, monkeypatch, caplog
    ):
        path = _make_pdf(tmp_path, "any.pdf", width=800, height=600, rotate=0)
        monkeypatch.setattr(
            converters,
            "_rasterize_rotate_page",
            MagicMock(side_effect=RuntimeError("render failed")),
        )
        pages = [{"page_no": 0, "rotate": 0, "is_landscape": True, "char_count": 100}]

        with caplog.at_level("WARNING"):
            result = _landscape_rasterize_rotate_reextract(path, pages)

        assert result == []
        assert any("landscape rasterize/rotate failed" in r.message for r in caplog.records)


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
        "vlm_describe_images": False,
        # HR3: pii_corpus=True + non-ZDR endpoint closes zdr_egress_gate so
        # _generate_flat_doc_description never attempts a real litellm call.
        "pii_corpus": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def pdf_file(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4\n fake pdf bytes")
    return str(path)


def _wire_index(monkeypatch, *, pic_results, flat_return):
    fake_settings = _fake_settings()
    monkeypatch.setattr(client_mod, "settings", fake_settings)
    # zdr_egress_gate re-imports settings from .config fresh on every call.
    monkeypatch.setattr("pageindex_mcp.config.settings", fake_settings)
    monkeypatch.setattr(client_mod, "PDF_INSPECTOR_PRECLASSIFY", False)
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())
    monkeypatch.setattr(client_mod, "validate_tree", lambda structure, **kw: (True, None))
    monkeypatch.setattr(client_mod, "split_oversized_leaf_nodes", lambda structure: structure)

    # Large body so the RFC-029 D1 flat-prefer check (which also compares
    # flat vs. tree char counts) does not itself trigger and confound the
    # D2 reroute-on-PictureResults assertion below.
    md_text = "# Heading\n\n" + ("Body paragraph text. " * 300)
    # D3B's flat-path garble gate is orthogonal to the D2 routing decision
    # under test here — stub it out so repeated filler text in md_text
    # doesn't spuriously trip it.
    monkeypatch.setattr(client_mod, "_flat_text_is_garbled", lambda *a, **kw: False)
    conv_fn = MagicMock(return_value=(md_text, pic_results))
    monkeypatch.setattr(client_mod, "pdf_markdown_converters", lambda: [("docling", conv_fn)])
    monkeypatch.setattr(
        client_mod, "pdf_to_markdown_docling", MagicMock(return_value=(md_text, []))
    )
    monkeypatch.setattr(client_mod, "ensure_tessdata", lambda langs: langs)

    mocks = {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "route_and_extract_flat": MagicMock(return_value=flat_return),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "OCR_ESCALATION_TOTAL": MagicMock(),
        "VLM_FALLBACK_TOTAL": MagicMock(),
        "RAW_UPLOAD_FAILURES": MagicMock(),
        "PDF_PRIMARY_CONVERTER_FAILURES": MagicMock(),
        "PDF_EXTRACT_FALLBACKS": MagicMock(),
        "PDF_INSPECTOR_FORCED_OCR": MagicMock(),
        "find_prior_verdict": MagicMock(return_value=None),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(client_mod, name, m)
    mocks["conv_fn"] = conv_fn
    return mocks


def _make_client():
    return CustomPageIndexClient(api_key="test-key")


async def _run_index(monkeypatch, pdf_file, *, pic_results, flat_return):
    mocks = _wire_index(monkeypatch, pic_results=pic_results, flat_return=flat_return)
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
    async def test_picture_results_reroutes_to_flat_mixed(self, monkeypatch, pdf_file):
        """A Phase 2 re-extraction that produced PictureResults (tagged
        skipped_reason='landscape_fallback_picture') must re-route to the
        flat-mixed path, not stay on the tree path."""
        pic_results = [{"page": 1, "skipped_reason": "landscape_fallback_picture"}]
        c, doc_id, mocks = await _run_index(
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

    async def test_no_picture_results_stays_on_original_routing_path(
        self, monkeypatch, pdf_file
    ):
        """A Phase 2 re-extraction with no PictureResults must NOT trigger the
        reroute guard — the document stays on its original (tree) path."""
        c, doc_id, mocks = await _run_index(
            monkeypatch,
            pdf_file,
            pic_results=[],
            flat_return=("flat_prose", [{"role": "prose", "text": "x"}]),
        )

        assert isinstance(doc_id, str)
        mocks["save_doc"].assert_called_once()
        mocks["save_flat_doc"].assert_not_called()
        assert c.last_content_class is None
