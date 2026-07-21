"""Tests for the structured image block pipeline (replacing <!-- image --> placeholders).

Covers:
- route_and_extract_flat parsing of [Figure: fig-N] markers
- _flat_search_text handling of image blocks
- save_figure MinIO persistence
- delete_doc figures cascade (step 2c)
- VLM description gating (vlm_describe_images + HR3/ZDR)
- Backward compatibility with old <!-- image --> prose blocks
- _enrich_image_blocks wiring
"""

import types
from io import BytesIO
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from pageindex_mcp.converters import (
    PictureResult,
    _splice_picture_text,
    get_last_picture_results,
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
        orig_remove = mock_mc.remove_object

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
    """VLM descriptions are gated by vlm_describe_images and HR3/ZDR."""

    def test_vlm_off_by_default_no_api_call(self):
        from pageindex_mcp import converters

        pr = {"ocr_text": "chart text", "png_bytes": b"fake", "page": 1, "bbox": {}}
        recovered = {0: pr}

        with (
            mock.patch.object(converters, "_OCR_ESCALATION", True),
            mock.patch.object(converters, "_collect_picture_regions", return_value=[
                {"page": 1, "bbox": types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None)}
            ]),
            mock.patch.object(converters, "detect_ocr_langs", return_value=["eng"]),
            mock.patch.object(converters, "ensure_tessdata", side_effect=lambda x: x),
            mock.patch.object(converters, "_recover_picture_text", return_value=recovered),
            mock.patch.object(converters, "_add_vlm_descriptions") as mock_vlm,
        ):
            md, pics = converters._maybe_splice_picture_ocr(
                "<!-- image -->", document=object(), pdf_path="dummy.pdf",
                vlm_describe=False,
            )

        mock_vlm.assert_not_called()

    def test_vlm_enabled_calls_descriptions(self):
        from pageindex_mcp import converters

        pr = {"ocr_text": "chart text", "png_bytes": b"fake", "page": 1, "bbox": {}}
        recovered = {0: pr}

        with (
            mock.patch.object(converters, "_OCR_ESCALATION", True),
            mock.patch.object(converters, "_collect_picture_regions", return_value=[
                {"page": 1, "bbox": types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None)}
            ]),
            mock.patch.object(converters, "detect_ocr_langs", return_value=["eng"]),
            mock.patch.object(converters, "ensure_tessdata", side_effect=lambda x: x),
            mock.patch.object(converters, "_recover_picture_text", return_value=recovered),
            mock.patch.object(converters, "_add_vlm_descriptions") as mock_vlm,
        ):
            md, pics = converters._maybe_splice_picture_ocr(
                "<!-- image -->", document=object(), pdf_path="dummy.pdf",
                vlm_describe=True,
            )

        mock_vlm.assert_called_once()

    def test_hr3_pii_non_zdr_skips_vlm(self):
        """PII corpus + non-ZDR endpoint should skip VLM descriptions (HR3)."""
        from pageindex_mcp import converters

        recovered = {0: {"ocr_text": "", "png_bytes": b"fake", "page": 1, "bbox": {}}}

        fake_settings = types.SimpleNamespace(
            pii_corpus=True,
            openai_base_url="https://api.openai.com/v1",
            vlm_model="gpt-4.1",
        )

        with (
            mock.patch("pageindex_mcp.config._is_zdr_allowlisted", return_value=False),
            mock.patch("pageindex_mcp.config.settings", fake_settings),
        ):
            converters._add_vlm_descriptions(recovered, "doc123")

        assert "description" not in recovered[0]


class TestBackwardCompatOldBlocks:
    """Old <!-- image --> prose blocks still parse correctly (no regression)."""

    def test_old_image_marker_as_prose(self):
        md = "# Title\n\n<!-- image -->\n\nSome text"
        _, blocks = route_and_extract_flat(md)
        prose_blocks = [b for b in blocks if b.get("role") == "prose"]
        assert any("<!-- image -->" in (b.get("text", "") or "") for b in prose_blocks)
        image_blocks = [b for b in blocks if b.get("role") == "image"]
        assert len(image_blocks) == 0


class TestEnrichImageBlocks:
    """_enrich_image_blocks wires PictureResults into flat blocks."""

    def test_enriches_matching_blocks(self):
        from pageindex_mcp.client import _enrich_image_blocks

        blocks = [
            {"role": "title", "text": "Title"},
            {"role": "image", "index": 0},
            {"role": "prose", "text": "After"},
        ]
        pic_results = [
            {"ocr_text": "Revenue data", "png_bytes": b"\x89PNG", "page": 2,
             "bbox": {"l": 0, "t": 0, "r": 100, "b": 100}, "description": "A chart"},
        ]

        with patch("pageindex_mcp.client.save_figure", return_value="figures/doc1/fig-0.png"):
            _enrich_image_blocks(blocks, pic_results, "doc1")

        img = blocks[1]
        assert img["figure_path"] == "figures/doc1/fig-0.png"
        assert img["page"] == 2
        assert img["bbox"] == {"l": 0, "t": 0, "r": 100, "b": 100}
        assert img["ocr_text"] == "Revenue data"
        assert img["description"] == "A chart"

    def test_skips_non_image_blocks(self):
        from pageindex_mcp.client import _enrich_image_blocks

        blocks = [{"role": "prose", "text": "Hello"}]
        _enrich_image_blocks(blocks, [{"png_bytes": b"x"}], "doc1")
        assert "figure_path" not in blocks[0]

    def test_empty_results_noop(self):
        from pageindex_mcp.client import _enrich_image_blocks

        blocks = [{"role": "image", "index": 0}]
        _enrich_image_blocks(blocks, [], "doc1")
        assert "figure_path" not in blocks[0]


class TestGetLastPictureResults:
    """Thread-local stash returns empty list by default."""

    def test_default_empty(self):
        import threading

        results = [None]

        def _check():
            results[0] = get_last_picture_results()

        t = threading.Thread(target=_check)
        t.start()
        t.join()
        assert results[0] == []
