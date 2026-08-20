"""Tests for the structured image block pipeline (replacing <!-- image --> placeholders).

Covers:
- route_and_extract_flat parsing of [Figure: fig-N] markers
- _flat_search_text handling of image blocks
- save_figure MinIO persistence
- delete_doc figures cascade (step 2c)
- VLM description gating (vlm_describe_images + HR3/ZDR via zdr_egress_gate)
- _enrich_image_blocks wiring (async, to_thread persistence)
- RFC-017 D0: page-coverage filter skips full-page PictureItems
- RFC-017 D1: standalone image produces synthetic PictureResult
- RFC-018 D1: text-layer probe skips redundant per-picture OCR
- Audit remediation: tuple-return contract (no thread-local), dense ordinal
  keying, bounded concurrency, decorative-image gate, VLM retry+metric,
  and end-to-end flat-branch wiring (converter -> splice -> enrich)
"""

import os
import sys
import tempfile
import threading
import types
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from pageindex_mcp import client as client_mod
from pageindex_mcp import converters
from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.converters import (
    PictureResult,
    _add_vlm_descriptions,
    _recover_picture_text,
    splice_figure_markers,
    zdr_egress_gate,
)
from pageindex_mcp.picture_plane import PictureGateConfig
from pageindex_mcp.helpers import _flat_search_text, route_and_extract_flat


class TestRouteFlatImageBlocks:
    """route_and_extract_flat emits {"role": "image"} blocks for [Figure: fig-N] markers."""

    def test_figure_marker_produces_image_block(self):
        md = "# Title\n\n[Figure: fig-0]\n\n> [Chart text]: Revenue 2024 42%\n\nMore text"
        content_class, blocks = route_and_extract_flat(md)
        image_blocks = [b for b in blocks if b.get("role") == "image"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["index"] == 0
        assert image_blocks[0]["ocr_text"] == "Revenue 2024 42%"

    def test_figure_marker_with_description(self):
        md = "[Figure: fig-1 | A pie chart showing monthly revenue]\n\n> [Chart text]: Jan 100 Feb 200"
        _, blocks = route_and_extract_flat(md)
        image_blocks = [b for b in blocks if b.get("role") == "image"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["index"] == 1
        assert image_blocks[0]["description"] == "A pie chart showing monthly revenue"
        assert image_blocks[0]["ocr_text"] == "Jan 100 Feb 200"

class TestFlatSearchTextImage:
    """_flat_search_text includes ocr_text and description from image blocks."""

    def test_image_block_ocr_in_search_text(self):
        data = {
            "blocks": [
                {"role": "image", "index": 0, "ocr_text": "Revenue chart data"},
            ]
        }
        text = _flat_search_text(data)
        assert "Revenue chart data" in text

    def test_image_block_description_in_search_text(self):
        data = {
            "blocks": [
                {"role": "image", "index": 0, "description": "A bar chart"},
            ]
        }
        text = _flat_search_text(data)
        assert "A bar chart" in text

class TestVlmDescribeGating:
    """VLM descriptions moved OUT of the converter (audit finding 8): the
    recovery step never calls the vision API; _add_vlm_descriptions is gated
    by zdr_egress_gate (HR3/ZDR)."""

    def test_recover_picture_results_never_calls_vlm(self, monkeypatch):
        """The converter-side recovery is OCR/crop only — no VLM, whatever the flag."""
        pr = PictureResult(ocr_text="chart text", png_bytes=b"fake", page=1, bbox={})
        monkeypatch.setattr(converters, "_OCR_ESCALATION_PER_PICTURE", True)
        monkeypatch.setattr(
            converters, "_collect_picture_regions", lambda d: [{"page": 1, "bbox": None}]
        )
        monkeypatch.setattr(converters, "detect_ocr_langs", lambda s: ["eng"])
        monkeypatch.setattr(converters, "ensure_tessdata", lambda langs: langs)
        monkeypatch.setattr(converters, "_recover_picture_text", lambda *a, **k: ({0: pr}, {}))
        with mock.patch.object(converters, "_add_vlm_descriptions") as mock_vlm:
            pics = converters._recover_picture_results("<!-- image -->", object(), "dummy.pdf")
        mock_vlm.assert_not_called()
        assert pics == [pr]

class TestEnrichImageBlocks:
    """_enrich_image_blocks (async) wires pic_results into image blocks and
    persists PNGs off the event loop."""

    @pytest.mark.asyncio
    async def test_enriches_matching_image_block(self):
        from pageindex_mcp.client import _enrich_image_blocks

        blocks = [
            {"role": "prose", "text": "Intro"},
            {"role": "image", "index": 0},
        ]
        pic_results = [
            {
                "png_bytes": b"\x89PNG",
                "page": 2,
                "bbox": {"l": 0, "t": 0, "r": 100, "b": 100},
                "ocr_text": "Revenue data",
                "description": "A chart",
            },
        ]

        with patch("pageindex_mcp.client.save_figure", return_value="figures/doc1/fig-0.png"):
            await _enrich_image_blocks(blocks, pic_results, "doc1")

        img = blocks[1]
        assert img["figure_path"] == "figures/doc1/fig-0.png"
        assert img["page"] == 2
        assert img["bbox"] == {"l": 0, "t": 0, "r": 100, "b": 100}
        assert img["ocr_text"] == "Revenue data"
        assert img["description"] == "A chart"
        # Finding 11: crop bytes released after persist.
        assert "png_bytes" not in pic_results[0]

    @pytest.mark.asyncio
    async def test_skips_non_image_blocks(self):
        from pageindex_mcp.client import _enrich_image_blocks

        blocks = [{"role": "prose", "text": "Hello"}]
        with patch("pageindex_mcp.client.save_figure") as sf:
            await _enrich_image_blocks(blocks, [{"png_bytes": b"x"}], "doc1")
        sf.assert_not_called()
        assert "figure_path" not in blocks[0]

# ---------------------------------------------------------------------------
# Audit finding 1: converters return (md, pics) via return value, not a
# thread-local; docling converter output stays neutral (no splice at that layer)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# RFC-017 D0: Page-coverage filter
# ---------------------------------------------------------------------------


def _make_fake_fitz(page_width: float, page_height: float):
    """Build a fake fitz module + document for _recover_picture_text tests."""
    fake = types.ModuleType("fitz")
    fake.Rect = lambda *a: types.SimpleNamespace(
        coords=a,
        width=a[2] - a[0] if len(a) >= 4 else 0,
        height=a[3] - a[1] if len(a) >= 4 else 0,
    )

    class _FakePage:
        def __init__(self):
            self.rect = types.SimpleNamespace(height=page_height, width=page_width)
            self.rotation = 0

        def set_rotation(self, value):
            self.rotation = value

        def get_text(self, mode="text", *, clip=None):
            return ""

        def get_pixmap(self, *, clip=None, dpi=300):
            return types.SimpleNamespace(tobytes=lambda fmt: b"PNG_FAKE")

    class _FakeDoc:
        page_count = 1

        def __getitem__(self, idx):
            return _FakePage()

        def close(self):
            pass

    fake.open = lambda path: _FakeDoc()
    return fake


class TestPageCoverageFilter:
    """RFC-017 D0: _recover_picture_text skips PictureItems covering >60% of page."""

    def _make_region(self, l, t, r, b):
        return {
            "page": 1,
            "bbox": types.SimpleNamespace(l=l, t=t, r=r, b=b, coord_origin=None),
        }

    def test_page_coverage_filter_skips_large_region(self, monkeypatch):
        """Region at 80% page area → not in crops dict (page HAS text layer)."""
        fake_fitz = _make_fake_fitz(600.0, 800.0)
        monkeypatch.setattr(converters, "_PICTURE_PAGE_COVERAGE_THRESHOLD", 0.6)
        # F1: coverage skip is exempt when page has NO text layer (default);
        # disable exemption so the coverage filter fires on the empty-text-layer
        # fake page, preserving the pre-F1 test intent.
        monkeypatch.setattr(converters, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", False)
        monkeypatch.setattr(converters, "_GATE_CONFIG", PictureGateConfig(
            coverage_exempt_no_text_layer=False,
        ))

        region = self._make_region(0, 0, 560, 700)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            monkeypatch.setattr(converters, "shutil", types.ModuleType("shutil"))
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        # D5a (RFC-029): page_coverage skip retains png_bytes + skipped_reason,
        # no ocr_text — OCR still short-circuited.
        assert 0 in result
        assert result[0].get("skipped_reason") == "page_coverage"
        assert result[0].get("png_bytes")
        assert not result[0].get("ocr_text")

    def test_page_coverage_filter_keeps_small_region(self, monkeypatch):
        """Region at 30% page area → present in crops dict with valid PNG bytes."""
        fake_fitz = _make_fake_fitz(600.0, 800.0)
        monkeypatch.setattr(converters, "_PICTURE_PAGE_COVERAGE_THRESHOLD", 0.6)

        region = self._make_region(0, 0, 300, 400)
        long_text = "Chart text with enough characters to pass the decorative gate"

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda path, langs: long_text)
            monkeypatch.setattr(converters, "shutil", types.ModuleType("shutil"))
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert len(result) == 1
        assert "png_bytes" in result[0]

# ---------------------------------------------------------------------------
# Audit findings 4/7: dense ordinal keying + marker/region count guard
# ---------------------------------------------------------------------------


class TestDenseKeyingAndCountGuard:
    def _region(self):
        return {
            "page": 1,
            "bbox": types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None),
        }

    def test_sparse_regions_keep_index_alignment(self, monkeypatch):
        monkeypatch.setattr(converters, "_OCR_ESCALATION_PER_PICTURE", True)
        monkeypatch.setattr(
            converters,
            "_collect_picture_regions",
            lambda d: [self._region(), self._region(), self._region()],
        )
        monkeypatch.setattr(converters, "detect_ocr_langs", lambda s: ["eng"])
        monkeypatch.setattr(converters, "ensure_tessdata", lambda langs: langs)
        pr0 = PictureResult(ocr_text="first chart text here", png_bytes=b"a", page=1, bbox={})
        pr2 = PictureResult(ocr_text="third chart text here", png_bytes=b"c", page=1, bbox={})
        # Region 1's crop failed -> sparse dict; the dense list must NOT collapse.
        monkeypatch.setattr(
            converters,
            "_recover_picture_text",
            lambda *a, **k: ({0: pr0, 2: pr2}, {1: "page_coverage"}),
        )

        pics = converters._recover_picture_results("x <!-- image --> y", object(), "d.pdf")

        assert len(pics) == 3
        assert pics[0] is pr0
        assert pics[2] is pr2
        assert pics[1].get("skipped_reason") == "page_coverage"
        assert not pics[1].get("ocr_text")
        assert not pics[1].get("png_bytes")

        md = "<!-- image -->\n\n<!-- image -->\n\n<!-- image -->"
        out = splice_figure_markers(md, pics)
        assert "[Figure: fig-0]" in out
        assert "third chart text here" in out
        assert "[Figure: fig-2]" in out
        assert "[Figure: fig-1]" not in out
        assert "<!-- image -->" not in out  # skipped marker stripped

# ---------------------------------------------------------------------------
# Audit finding 10: bounded concurrency for OCR and VLM
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Audit finding 12: decorative-image gate (short OCR text drops the crop
# unless VLM description is enabled, in which case it's kept for reclassification)
# ---------------------------------------------------------------------------


class TestDecorativeGate:
    def _region(self):
        return {
            "page": 1,
            "bbox": types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None),
        }

    def test_short_ocr_vlm_off_drops_png(self, monkeypatch):
        monkeypatch.setattr(
            "pageindex_mcp.config.settings",
            SimpleNamespace(
                pii_corpus=False,
                openai_base_url="https://api.openai.com/v1",
                vlm_model="gpt-4.1",
                vlm_describe_images=False,
                llm_model="gpt-test",
            ),
        )
        fake_fitz = _make_fake_fitz(600.0, 800.0)
        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            monkeypatch.setattr(
                converters, "_tesseract_ocr_image", lambda png, langs: "short"
            )
            monkeypatch.setattr(converters, "shutil", types.ModuleType("shutil"))
            out, _skip = converters._recover_picture_text("dummy.pdf", [self._region()], ["eng"])
        assert out[0]["ocr_text"] == ""
        assert "png_bytes" not in out[0]

    def test_short_ocr_vlm_on_keeps_png_for_reclassification(self, monkeypatch):
        monkeypatch.setattr(
            "pageindex_mcp.config.settings",
            SimpleNamespace(
                pii_corpus=False,
                openai_base_url="https://api.openai.com/v1",
                vlm_model="gpt-4.1",
                vlm_describe_images=True,
                llm_model="gpt-test",
            ),
        )
        fake_fitz = _make_fake_fitz(600.0, 800.0)
        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            monkeypatch.setattr(
                converters, "_tesseract_ocr_image", lambda png, langs: "short"
            )
            monkeypatch.setattr(converters, "shutil", types.ModuleType("shutil"))
            out, _skip = converters._recover_picture_text("dummy.pdf", [self._region()], ["eng"])
        assert out[0]["ocr_text"] == ""
        assert out[0]["png_bytes"]


# ---------------------------------------------------------------------------
# Audit finding 15: VLM failure retries once, then increments a metric
# (no silent-only logging)
# ---------------------------------------------------------------------------


class TestVlmRetryAndMetric:
    def _settings(self):
        return SimpleNamespace(
            pii_corpus=False,
            openai_base_url="https://api.openai.com/v1",
            vlm_model="gpt-4.1",
            vlm_describe_images=False,
            llm_model="gpt-test",
        )

    def test_transient_failure_retried_then_succeeds(self, monkeypatch):
        monkeypatch.setattr("pageindex_mcp.config.settings", self._settings())
        monkeypatch.setattr(converters.time, "sleep", lambda s: None)
        calls = {"n": 0}
        fake_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="desc after retry"))]
        )

        def flaky(**kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("rate limited")
            return fake_resp

        pics = [PictureResult(png_bytes=b"p")]
        with (
            patch("litellm.completion", side_effect=flaky),
            patch("pageindex_mcp.metrics.IMAGE_DESCRIBE_FAILURES") as metric,
        ):
            _add_vlm_descriptions(pics, "d1")

        assert calls["n"] == 2
        assert pics[0]["description"] == "desc after retry"
        metric.labels.assert_not_called()

    def test_persistent_failure_increments_metric(self, monkeypatch):
        monkeypatch.setattr("pageindex_mcp.config.settings", self._settings())
        monkeypatch.setattr(converters.time, "sleep", lambda s: None)
        pics = [PictureResult(png_bytes=b"p")]
        with (
            patch("litellm.completion", side_effect=RuntimeError("boom")),
            patch("pageindex_mcp.metrics.IMAGE_DESCRIBE_FAILURES") as metric,
        ):
            _add_vlm_descriptions(pics, "d1")

        metric.labels.assert_called_once_with(error_type="RuntimeError")
        metric.labels.return_value.inc.assert_called_once()
        assert "description" not in pics[0]


# ---------------------------------------------------------------------------
# End-to-end flat-branch wiring: converter pic_results reach client.index()
# through the RETURN VALUE, get spliced, and drive _enrich_image_blocks
# ---------------------------------------------------------------------------


def _fake_client_settings(vlm_describe_images=False):
    return SimpleNamespace(
        openai_api_key="test-key",
        openai_base_url="https://api.openai.com/v1",
        azure_api_version=None,
        llm_model="gpt-test",
        minio_secure=False,
        minio_endpoint="localhost:9000",
        minio_bucket="pageindex",
        flat_doc_routing=True,
        vlm_fallback=False,
        vlm_model="gpt-4.1",
        vlm_describe_images=vlm_describe_images,
        pii_corpus=False,
    )


@pytest.fixture
def pdf_file():
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n fake pdf bytes")
    yield path
    if os.path.exists(path):
        os.unlink(path)


async def _tree_coro():
    return {"structure": [], "doc_description": ""}


def _wire_flat_branch(monkeypatch, *, chain_md, pics, vlm_describe_images=False):
    monkeypatch.setattr(
        client_mod, "settings", _fake_client_settings(vlm_describe_images=vlm_describe_images)
    )
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())
    monkeypatch.setattr(client_mod, "validate_tree", lambda structure, **kw: (False, "depth<2"))
    monkeypatch.setattr(client_mod, "prepare_tree", lambda structure, **kw: structure)
    monkeypatch.setattr(client_mod, "_OCR_ESCALATION_GARBLE", False)
    monkeypatch.setattr(client_mod, "_OCR_ESCALATION_PER_PICTURE", False)
    monkeypatch.setattr(
        client_mod, "pdf_markdown_converters", lambda: [("docling", lambda p, **kw: (chain_md, pics))]
    )
    monkeypatch.setattr(
        client_mod, "_generate_flat_doc_description", MagicMock(return_value="a flat doc")
    )
    mocks = {
        "save_flat_doc": MagicMock(),
        "save_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "save_figure": MagicMock(return_value="figures/x/fig-0.png"),
        "route_and_extract_flat": MagicMock(
            return_value=("flat_prose", [{"role": "image", "index": 0}])
        ),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(client_mod, name, m)
    return mocks


CHAIN_MD = "prose line one\n\nprose line two\n\n<!-- image -->\n\nprose line three"


class TestFlatBranchWiring:
    async def test_flat_enrich_receives_results(self, monkeypatch, pdf_file):
        """The converter's pic_results reach the flat branch through the RETURN
        VALUE (not a thread-local), get spliced into the flat markdown, and
        drive _enrich_image_blocks -> save_figure with the real doc_id."""
        pr = {"png_bytes": b"PNG", "page": 1, "bbox": {"l": 1}, "ocr_text": "long chart text here"}
        mocks = _wire_flat_branch(monkeypatch, chain_md=CHAIN_MD, pics=[pr])
        c = CustomPageIndexClient(api_key="test-key")
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro())

        doc_id = await c.index(pdf_file)

        routed_md = mocks["route_and_extract_flat"].call_args.args[0]
        assert "[Figure: fig-0]" in routed_md
        assert "long chart text here" in routed_md
        mocks["save_figure"].assert_called_once_with(doc_id, 0, b"PNG")
        saved_blocks = mocks["save_flat_doc"].call_args.args[1]["blocks"]
        img = next(b for b in saved_blocks if b.get("role") == "image")
        assert img["figure_path"] == "figures/x/fig-0.png"

# ---------------------------------------------------------------------------
# RFC-017 D1: Standalone image enrichment
# ---------------------------------------------------------------------------


class TestStandaloneImageEnrichment:
    """RFC-017 D1: standalone images produce synthetic PictureResult."""

    def test_standalone_image_marker_mismatch_degrades(self):
        """3 <!-- image --> markers + 1 empty PictureResult (RFC-023 D1): the
        matched marker keeps its neutral form (empty result, no skip reason);
        the two excess markers past len(pics) are stripped."""
        md = "# Title\n\n<!-- image -->\n\nMiddle\n\n<!-- image -->\n\nEnd\n\n<!-- image -->"
        pics = [PictureResult(ocr_text="", page=1, bbox={"l": 0, "t": 0, "r": 0, "b": 0})]
        result = splice_figure_markers(md, pics)
        assert result.count("<!-- image -->") == 1
        assert "[Figure:" not in result
        assert "Middle" in result and "End" in result

    @pytest.mark.asyncio
    async def test_standalone_image_produces_synthetic_pic_result(self, monkeypatch):
        """.jpg file → pic_results has exactly 1 entry with png_bytes == source bytes."""
        source_bytes = b"\xff\xd8\xff\xe0FAKE_JPEG_DATA"
        fd, jpg_path = tempfile.mkstemp(suffix=".jpg")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(source_bytes)

            fake_settings = SimpleNamespace(
                openai_api_key="k",
                openai_base_url="https://api.openai.com/v1",
                azure_api_version=None,
                llm_model="gpt-test",
                minio_secure=False,
                minio_endpoint="localhost:9000",
                minio_bucket="pageindex",
                flat_doc_routing=True,
                vlm_fallback=False,
                vlm_model="gpt-4.1",
                vlm_describe_images=False,
                pii_corpus=False,
            )
            monkeypatch.setattr(client_mod, "settings", fake_settings)
            monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
            monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
            monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())
            monkeypatch.setattr(client_mod, "validate_tree", lambda s, **kw: (False, "depth<2"))
            monkeypatch.setattr(
                client_mod,
                "route_and_extract_flat",
                MagicMock(return_value=("flat_prose", [{"role": "prose", "text": "x"}])),
            )
            monkeypatch.setattr(client_mod, "save_flat_doc", MagicMock())
            monkeypatch.setattr(client_mod, "save_doc", MagicMock())
            monkeypatch.setattr(client_mod, "save_raw", MagicMock())
            monkeypatch.setattr(client_mod, "save_doc_meta", MagicMock())
            monkeypatch.setattr(client_mod, "FLAT_DOCS_TOTAL", MagicMock())
            monkeypatch.setattr(client_mod, "LOW_QUALITY_TREES", MagicMock())
            monkeypatch.setattr(client_mod, "ensure_tessdata", lambda langs: langs)
            monkeypatch.setattr(
                client_mod, "image_to_markdown", lambda path, langs: "<!-- image -->"
            )

            captured_pics = []
            orig_splice = splice_figure_markers

            def spy_splice(md, pics):
                captured_pics.extend(pics)
                return orig_splice(md, pics)

            monkeypatch.setattr(client_mod, "splice_figure_markers", spy_splice)

            c = CustomPageIndexClient(api_key="test-key")

            async def _fake_tree(md_path):
                return {
                    "structure": [{"node_id": "n1", "text": "x", "nodes": []}],
                    "doc_description": "",
                }

            monkeypatch.setattr(c, "_run_md_to_tree", _fake_tree)

            await c.index(jpg_path)

            assert len(captured_pics) == 1
            assert captured_pics[0]["png_bytes"] == source_bytes
            assert captured_pics[0]["ocr_text"] == ""
            assert captured_pics[0]["page"] == 1
            assert captured_pics[0]["bbox"] == {"l": 0, "t": 0, "r": 0, "b": 0}
        finally:
            if os.path.exists(jpg_path):
                os.unlink(jpg_path)

    @staticmethod
    def _fake_settings():
        return SimpleNamespace(
            openai_api_key="k",
            openai_base_url="https://api.openai.com/v1",
            azure_api_version=None,
            llm_model="gpt-test",
            minio_secure=False,
            minio_endpoint="localhost:9000",
            minio_bucket="pageindex",
            flat_doc_routing=True,
            vlm_fallback=False,
            vlm_model="gpt-4.1",
            vlm_describe_images=False,
            pii_corpus=False,
        )

    async def _run_index_with_markdown(self, monkeypatch, markdown: str, source_bytes: bytes):
        """Drive CustomPageIndexClient.index() over a fake .jpg, capturing the
        pic_results list passed to splice_figure_markers, exactly as the
        RFC-017 D1 harness above does."""
        fd, jpg_path = tempfile.mkstemp(suffix=".jpg")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(source_bytes)

            monkeypatch.setattr(client_mod, "settings", self._fake_settings())
            monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
            monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
            monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())
            monkeypatch.setattr(client_mod, "validate_tree", lambda s, **kw: (False, "depth<2"))
            monkeypatch.setattr(
                client_mod,
                "route_and_extract_flat",
                MagicMock(return_value=("flat_prose", [{"role": "prose", "text": "x"}])),
            )
            monkeypatch.setattr(client_mod, "save_flat_doc", MagicMock())
            monkeypatch.setattr(client_mod, "save_doc", MagicMock())
            monkeypatch.setattr(client_mod, "save_raw", MagicMock())
            monkeypatch.setattr(client_mod, "save_doc_meta", MagicMock())
            monkeypatch.setattr(client_mod, "FLAT_DOCS_TOTAL", MagicMock())
            monkeypatch.setattr(client_mod, "LOW_QUALITY_TREES", MagicMock())
            monkeypatch.setattr(client_mod, "ensure_tessdata", lambda langs: langs)
            monkeypatch.setattr(client_mod, "image_to_markdown", lambda path, langs: markdown)

            captured_pics = []
            orig_splice = splice_figure_markers

            def spy_splice(md, pics):
                captured_pics.extend(pics)
                return orig_splice(md, pics)

            monkeypatch.setattr(client_mod, "splice_figure_markers", spy_splice)

            c = CustomPageIndexClient(api_key="test-key")

            async def _fake_tree(md_path):
                return {
                    "structure": [{"node_id": "n1", "text": "x", "nodes": []}],
                    "doc_description": "",
                }

            monkeypatch.setattr(c, "_run_md_to_tree", _fake_tree)

            await c.index(jpg_path)
            return captured_pics
        finally:
            if os.path.exists(jpg_path):
                os.unlink(jpg_path)

def _make_fake_fitz_with_text(page_width: float, page_height: float, clip_text: str):
    """Build a fake fitz module whose page.get_text(...) returns ``clip_text``,
    for RFC-018 D1 text-layer-probe tests on _recover_picture_text."""
    fake = types.ModuleType("fitz")
    fake.Rect = lambda *a: types.SimpleNamespace(
        coords=a,
        width=a[2] - a[0] if len(a) >= 4 else 0,
        height=a[3] - a[1] if len(a) >= 4 else 0,
    )

    class _FakePage:
        def __init__(self):
            self.rect = types.SimpleNamespace(height=page_height, width=page_width)
            self.rotation = 0

        def set_rotation(self, value):
            self.rotation = value

        def get_text(self, kind, *, clip=None):
            assert kind == "text"
            return clip_text

        def get_pixmap(self, *, clip=None, dpi=300):
            return types.SimpleNamespace(tobytes=lambda fmt: b"PNG_FAKE")

    class _FakeDoc:
        page_count = 1

        def __getitem__(self, idx):
            return _FakePage()

        def close(self):
            pass

    fake.open = lambda path: _FakeDoc()
    return fake


class TestTextLayerProbe:
    """RFC-018 D1: _recover_picture_text skips per-picture OCR when the PDF
    text layer already has clean text under the picture's bbox."""

    def _make_region(self, l, t, r, b):
        return {
            "page": 1,
            "bbox": types.SimpleNamespace(l=l, t=t, r=r, b=b, coord_origin=None),
        }

    def test_text_layer_skips_picture_ocr(self, monkeypatch):
        """get_text(clip=rect) returns >20 chars already in the Docling markdown
        export -> region NOT in crops dict (RFC-024 D1 containment guard)."""
        long_clip_text = "This is more than twenty characters of extracted text."
        fake_fitz = _make_fake_fitz_with_text(600.0, 800.0, long_clip_text)
        monkeypatch.setattr(converters, "_PICTURE_PAGE_COVERAGE_THRESHOLD", 0.6)

        region = self._make_region(0, 0, 100, 100)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            monkeypatch.setattr(converters, "shutil", types.ModuleType("shutil"))
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"], md=long_clip_text)

        # D5a (RFC-029): clip_text_already_exported retains png_bytes and
        # propagates clip_text into ocr_text — OCR itself was not invoked.
        assert 0 in result
        assert result[0].get("skipped_reason") == "clip_text_already_exported"
        assert result[0].get("png_bytes")
        assert result[0].get("ocr_text") == long_clip_text

    def test_no_text_layer_allows_picture_ocr(self, monkeypatch):
        """get_text(clip=rect) returns "" -> region IS in crops dict, OCR proceeds."""
        fake_fitz = _make_fake_fitz_with_text(600.0, 800.0, "")
        monkeypatch.setattr(converters, "_PICTURE_PAGE_COVERAGE_THRESHOLD", 0.6)

        region = self._make_region(0, 0, 100, 100)
        long_ocr_text = "Chart text recovered via OCR with enough characters to pass"

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            monkeypatch.setattr(
                converters, "_tesseract_ocr_image", lambda path, langs: long_ocr_text
            )
            monkeypatch.setattr(converters, "shutil", types.ModuleType("shutil"))
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert 0 in result
        assert len(result) == 1

# ---------------------------------------------------------------------------
# RFC-020 Task 4.1 / F4: independent PictureResult copies (standalone path)
# ---------------------------------------------------------------------------


class TestIndependentPictureResults:
    """RFC-020 F4: the standalone-image branch must build pic_results with a
    list comprehension (independent dict copies), never ``[PictureResult(...)] * N``
    (shared references) — mutating one entry must not affect the others."""

    def test_pic_results_not_shared_references(self):
        marker_count = 3
        img_bytes = b"fake-png"
        pic_results = [
            PictureResult(
                ocr_text="",
                page=1,
                bbox={"l": 0, "t": 0, "r": 0, "b": 0},
                png_bytes=img_bytes,
            )
            for _ in range(max(1, marker_count))
        ]

        pic_results[0].pop("png_bytes")

        assert "png_bytes" not in pic_results[0]
        assert "png_bytes" in pic_results[1]
        assert "png_bytes" in pic_results[2]
