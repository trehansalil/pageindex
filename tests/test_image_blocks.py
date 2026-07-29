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
"""

import os
import tempfile
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
    _recover_picture_text,
    splice_figure_markers,
)
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

    def test_figure_without_chart_text(self):
        md = "[Figure: fig-0]\n\nSome prose after"
        _, blocks = route_and_extract_flat(md)
        image_blocks = [b for b in blocks if b.get("role") == "image"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["index"] == 0
        assert "ocr_text" not in image_blocks[0]

    def test_multiple_figures(self):
        md = "[Figure: fig-0]\n\n> [Chart text]: First\n\n[Figure: fig-1]\n\n> [Chart text]: Second"
        _, blocks = route_and_extract_flat(md)
        image_blocks = [b for b in blocks if b.get("role") == "image"]
        assert len(image_blocks) == 2
        assert image_blocks[0]["index"] == 0
        assert image_blocks[1]["index"] == 1


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

    def test_image_block_no_text_fields(self):
        data = {
            "blocks": [
                {"role": "image", "index": 0},
            ]
        }
        text = _flat_search_text(data)
        assert text.strip() == ""


class TestSaveFigure:
    """save_figure writes to the correct MinIO path with image/png content type."""

    def test_save_figure_puts_correct_object(self):
        with patch("pageindex_mcp.storage.get_minio") as mock_get:
            mc = MagicMock()
            mock_get.return_value = mc
            from pageindex_mcp.storage import save_figure

            key = save_figure("doc123", 0, b"\x89PNG fake")
            assert key == "figures/doc123/fig-0.png"
            mc.put_object.assert_called_once()
            call_args = mc.put_object.call_args
            assert call_args[0][1] == "figures/doc123/fig-0.png"
            assert call_args[1].get("content_type") or call_args[0][4] == "image/png"


class TestDeleteDocFigures:
    """delete_doc step 2c removes figures/<doc_id>/ objects."""

    @pytest.mark.asyncio
    async def test_figures_removed_in_cascade(self):
        fig_obj = MagicMock()
        fig_obj.object_name = "figures/abc123/fig-0.png"

        mock_mc = MagicMock()

        def _list_objects(_b, prefix="", **_kw):
            if prefix.startswith("figures/"):
                return [fig_obj]
            return []

        mock_mc.list_objects.side_effect = _list_objects
        mock_mc.remove_object.return_value = None
        mock_mc.stat_object.side_effect = Exception("not found")

        removed = []

        def _track_remove(bucket, name):
            removed.append(name)

        mock_mc.remove_object.side_effect = _track_remove

        with (
            patch("pageindex_mcp.storage.get_minio", return_value=mock_mc),
            patch("pageindex_mcp.cache.doc_cache_delete"),
            patch("pageindex_mcp.storage.hash_cache_delete"),
        ):
            from pageindex_mcp.storage import delete_doc

            await delete_doc("abc123")

        assert "figures/abc123/fig-0.png" in removed


class TestVlmDescribeGating:
    """VLM descriptions moved OUT of the converter (audit finding 8): the
    recovery step never calls the vision API; _add_vlm_descriptions is gated
    by zdr_egress_gate (HR3/ZDR)."""

    def test_recover_picture_results_never_calls_vlm(self, monkeypatch):
        """The converter-side recovery is OCR/crop only — no VLM, whatever the flag."""
        pr = PictureResult(ocr_text="chart text", png_bytes=b"fake", page=1, bbox={})
        monkeypatch.setattr(converters, "_OCR_ESCALATION", True)
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

    def test_hr3_pii_non_zdr_skips_vlm(self, monkeypatch):
        """PII corpus + non-ZDR endpoint blocks VLM descriptions (HR3): no image
        bytes egress, no description written."""
        monkeypatch.setattr(
            "pageindex_mcp.config.settings",
            SimpleNamespace(
                pii_corpus=True,
                openai_base_url="https://api.openai.com/v1",
                vlm_model="gpt-4.1",
            ),
        )
        pics = [PictureResult(ocr_text="chart", png_bytes=b"fake", page=1, bbox={})]
        with patch("litellm.completion") as comp:
            converters._add_vlm_descriptions(pics, "doc1")
        comp.assert_not_called()
        assert "description" not in pics[0]

    def test_zdr_endpoint_allows_vlm(self, monkeypatch):
        monkeypatch.setattr(
            "pageindex_mcp.config.settings",
            SimpleNamespace(
                pii_corpus=True,
                openai_base_url="https://myres.openai.azure.com/v1",
                vlm_model="gpt-4.1",
            ),
        )
        pics = [PictureResult(ocr_text="chart", png_bytes=b"fake", page=1, bbox={})]
        fake_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="A bar chart"))]
        )
        with patch("litellm.completion", return_value=fake_resp):
            converters._add_vlm_descriptions(pics, "doc1")
        assert pics[0]["description"] == "A bar chart"


class TestSpliceWithDescription:
    """splice_figure_markers emits the `| description` inline form the flat
    parser understands."""

    def test_description_spliced_inline(self):
        pics = [
            PictureResult(
                ocr_text="Revenue data here for 2024",
                png_bytes=b"fake",
                description="A bar chart of revenue",
            )
        ]
        out = splice_figure_markers("<!-- image -->", pics)
        assert "[Figure: fig-0 | A bar chart of revenue]" in out
        assert "> [Chart text]: Revenue data here for 2024" in out


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

    @pytest.mark.asyncio
    async def test_empty_results_noop(self):
        from pageindex_mcp.client import _enrich_image_blocks

        blocks = [{"role": "image", "index": 0}]
        await _enrich_image_blocks(blocks, [], "doc1")
        assert "figure_path" not in blocks[0]

    @pytest.mark.asyncio
    async def test_no_png_no_save(self):
        """Decorative results (finding 12) never hit MinIO."""
        from pageindex_mcp.client import _enrich_image_blocks

        blocks = [{"role": "image", "index": 0}]
        with patch("pageindex_mcp.client.save_figure") as sf:
            await _enrich_image_blocks(blocks, [{"ocr_text": "", "page": 1, "bbox": {}}], "doc1")
        sf.assert_not_called()
        assert "figure_path" not in blocks[0]


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

        region = self._make_region(0, 0, 560, 700)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            monkeypatch.setattr(converters, "shutil", types.ModuleType("shutil"))
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert len(result) == 0

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

    def test_page_coverage_threshold_configurable(self, monkeypatch):
        """PICTURE_PAGE_COVERAGE_THRESHOLD=0.9 → region at 80% is kept."""
        fake_fitz = _make_fake_fitz(600.0, 800.0)
        monkeypatch.setattr(converters, "_PICTURE_PAGE_COVERAGE_THRESHOLD", 0.9)

        region = self._make_region(0, 0, 560, 700)
        long_text = "Recovered text with enough characters to pass the decorative gate"

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda path, langs: long_text)
            monkeypatch.setattr(converters, "shutil", types.ModuleType("shutil"))
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert len(result) == 1

    def test_coverage_skip_tags_skipped_reason(self):
        """splice_figure_markers: skipped_reason='page_coverage' → marker stripped
        (mirrors the tag _recover_picture_results attaches for deliberate skips)."""
        md = "# Title\n\n<!-- image -->\n\nMore text"
        pics = [PictureResult(skipped_reason="page_coverage")]
        result = splice_figure_markers(md, pics)
        assert "<!-- image -->" not in result

    def test_splice_strips_skipped_marker(self):
        """splice_figure_markers: skipped_reason → marker stripped."""
        md = "Before\n<!-- image -->\nAfter"
        pics = [PictureResult(skipped_reason="page_coverage")]
        result = splice_figure_markers(md, pics)
        assert "<!-- image -->" not in result

    def test_splice_strips_decorative_marker(self):
        """splice_figure_markers: decorative=True → marker stripped."""
        md = "Before\n<!-- image -->\nAfter"
        pics = [PictureResult(decorative=True)]
        result = splice_figure_markers(md, pics)
        assert "<!-- image -->" not in result

    def test_splice_preserves_genuine_failure_marker(self):
        """splice_figure_markers: empty PictureResult (no reason) → marker preserved."""
        md = "Before\n<!-- image -->\nAfter"
        pics = [PictureResult()]
        result = splice_figure_markers(md, pics)
        assert "<!-- image -->" in result

    def test_splice_strip_env_disabled(self, monkeypatch):
        """STRIP_SKIPPED_IMAGE_MARKERS=false → even skipped markers are preserved."""
        monkeypatch.setenv("STRIP_SKIPPED_IMAGE_MARKERS", "false")
        md = "Before\n<!-- image -->\nAfter"
        pics = [PictureResult(skipped_reason="page_coverage")]
        result = splice_figure_markers(md, pics)
        assert "<!-- image -->" in result

    def test_splice_mixed_skipped_and_real(self):
        """Mix of skipped and real results: skipped stripped, real kept as [Figure]."""
        md = "A\n<!-- image -->\nB\n<!-- image -->\nC"
        pics = [
            PictureResult(skipped_reason="page_coverage"),
            PictureResult(ocr_text="Revenue chart", page=1, bbox={"l": 0, "t": 0, "r": 0, "b": 0}),
        ]
        result = splice_figure_markers(md, pics)
        assert "<!-- image -->" not in result
        assert "[Figure: fig-1]" in result
        assert "Revenue chart" in result


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

    @pytest.mark.asyncio
    async def test_standalone_image_marker_count_match(self, monkeypatch):
        """3 <!-- image --> markers -> exactly 3 PictureResults, same png_bytes (D0)."""
        source_bytes = b"\xff\xd8\xff\xe0FAKE_JPEG_3MARKERS"
        markdown = "# Title\n\n<!-- image -->\n\nMiddle\n\n<!-- image -->\n\nEnd\n\n<!-- image -->"
        captured_pics = await self._run_index_with_markdown(monkeypatch, markdown, source_bytes)

        assert len(captured_pics) == 3
        assert all(p["png_bytes"] == source_bytes for p in captured_pics)

    @pytest.mark.asyncio
    async def test_standalone_image_single_marker(self, monkeypatch):
        """1 <!-- image --> marker -> exactly 1 PictureResult (pre-D0 regression)."""
        source_bytes = b"\xff\xd8\xff\xe0FAKE_JPEG_1MARKER"
        markdown = "# Title\n\n<!-- image -->\n\nSome caption text"
        captured_pics = await self._run_index_with_markdown(monkeypatch, markdown, source_bytes)

        assert len(captured_pics) == 1
        assert captured_pics[0]["png_bytes"] == source_bytes

    @pytest.mark.asyncio
    async def test_multi_marker_raster_five_markers(self, monkeypatch):
        """5 <!-- image --> markers → exactly 5 PictureResults, all same png_bytes (D0 edge)."""
        source_bytes = b"\xff\xd8\xff\xe0FAKE_JPEG_5MARKERS"
        markdown = (
            "# Title\n\n<!-- image -->\n\nA\n\n<!-- image -->\n\nB\n\n"
            "<!-- image -->\n\nC\n\n<!-- image -->\n\nD\n\n<!-- image -->"
        )
        captured_pics = await self._run_index_with_markdown(monkeypatch, markdown, source_bytes)

        assert len(captured_pics) == 5
        assert all(p["png_bytes"] == source_bytes for p in captured_pics)
        assert all(p["page"] == 1 for p in captured_pics)
        assert all(p["bbox"] == {"l": 0, "t": 0, "r": 0, "b": 0} for p in captured_pics)


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
        """get_text(clip=rect) returns >20 chars -> region NOT in crops dict."""
        long_clip_text = "This is more than twenty characters of extracted text."
        fake_fitz = _make_fake_fitz_with_text(600.0, 800.0, long_clip_text)
        monkeypatch.setattr(converters, "_PICTURE_PAGE_COVERAGE_THRESHOLD", 0.6)

        region = self._make_region(0, 0, 100, 100)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            monkeypatch.setattr(converters, "shutil", types.ModuleType("shutil"))
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert 0 not in result
        assert len(result) == 0

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

    def test_text_layer_boundary_exactly_20_chars(self, monkeypatch):
        """Exactly 20 chars (== threshold) → does NOT skip, OCR proceeds."""
        clip_text = "A" * 20  # exactly at threshold
        fake_fitz = _make_fake_fitz_with_text(600.0, 800.0, clip_text)
        monkeypatch.setattr(converters, "_PICTURE_PAGE_COVERAGE_THRESHOLD", 0.6)

        region = self._make_region(0, 0, 100, 100)
        long_ocr = "Recovered OCR text that is long enough to pass the gate"

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda path, langs: long_ocr)
            monkeypatch.setattr(converters, "shutil", types.ModuleType("shutil"))
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert 0 in result  # OCR fires — boundary NOT exceeded

    def test_text_layer_boundary_21_chars_skips(self, monkeypatch):
        """21 chars (> threshold) → skip OCR."""
        clip_text = "A" * 21  # just above threshold
        fake_fitz = _make_fake_fitz_with_text(600.0, 800.0, clip_text)
        monkeypatch.setattr(converters, "_PICTURE_PAGE_COVERAGE_THRESHOLD", 0.6)

        region = self._make_region(0, 0, 100, 100)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            monkeypatch.setattr(converters, "shutil", types.ModuleType("shutil"))
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert len(result) == 0  # OCR skipped

    def test_text_layer_env_override(self, monkeypatch):
        """_PICTURE_OCR_MIN_CHARS env override: set to 50 → 30 chars should NOT skip."""
        clip_text = "A" * 30  # 30 chars, above default 20 but below custom 50
        fake_fitz = _make_fake_fitz_with_text(600.0, 800.0, clip_text)
        monkeypatch.setattr(converters, "_PICTURE_PAGE_COVERAGE_THRESHOLD", 0.6)
        monkeypatch.setattr(converters, "_PICTURE_OCR_MIN_CHARS", 50)

        region = self._make_region(0, 0, 100, 100)
        long_ocr = "Recovered OCR text that is long enough to pass the gate"

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda path, langs: long_ocr)
            monkeypatch.setattr(converters, "shutil", types.ModuleType("shutil"))
            result, _skip = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert 0 in result  # OCR fires because 30 < 50
        assert "png_bytes" in result[0]


# ---------------------------------------------------------------------------
# RFC-020 Task 4.1 / F4: independent PictureResult copies (standalone path)
# ---------------------------------------------------------------------------


class TestF4IndependentPictureResults:
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

    def test_multi_marker_all_independent(self):
        marker_count = 5
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

        pic_results[2]["bbox"] = {"l": 99}

        assert pic_results[2]["bbox"] == {"l": 99}
        for i in (0, 1, 3, 4):
            assert pic_results[i]["bbox"] == {"l": 0, "t": 0, "r": 0, "b": 0}

    def test_single_marker_parity(self):
        marker_count = 1
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

        assert len(pic_results) == 1
        assert pic_results[0]["ocr_text"] == ""
        assert pic_results[0]["page"] == 1
        assert pic_results[0]["bbox"] == {"l": 0, "t": 0, "r": 0, "b": 0}
        assert pic_results[0]["png_bytes"] == img_bytes

    def test_shared_reference_mutation_would_fail(self):
        """Demonstrates the OLD (buggy) pattern: [PictureResult(...)] * 3 shares
        the SAME dict object across all list slots, so a mutation to one entry
        leaks into every other entry."""
        img_bytes = b"fake-png"
        pic_results = [
            PictureResult(
                ocr_text="",
                page=1,
                bbox={"l": 0, "t": 0, "r": 0, "b": 0},
                png_bytes=img_bytes,
            )
        ] * 3

        pic_results[0].pop("png_bytes")

        assert "png_bytes" not in pic_results[0]
        assert "png_bytes" not in pic_results[1]
