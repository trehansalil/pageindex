"""Zone-5: enrichment unification contract tests.

Locks the contract that standalone .jpg and PDF flat-success paths produce
identical block structures by both routing through _apply_picture_enrichment.

1. **Contract**: _apply_picture_enrichment preserves zero-block guard
   (raises LowQualityTreeError on non-empty markdown yielding empty blocks).
2. **Contract**: image_standalone detection when all blocks are role=image.
3. **Contract**: content_class override for bare image files (.jpg/.png).
4. **Wiring**: _apply_picture_enrichment is importable and called in client.py.
"""
from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, patch

import pytest

from pageindex_mcp.client import (
    _apply_picture_enrichment,
    apply_image_ext_content_class_override,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously for test convenience."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# 1. Zero-block guard
# ---------------------------------------------------------------------------


class TestZeroBlockGuard:
    """Non-empty markdown that yields empty blocks must raise LowQualityTreeError."""

    def test_zero_blocks_from_nonempty_md_raises(self):
        from pageindex_mcp.helpers import LowQualityTreeError

        # Mock route_and_extract_flat to return empty blocks
        with patch(
            "pageindex_mcp.client.route_and_extract_flat",
            return_value=("flat_prose", []),
        ), patch(
            "pageindex_mcp.client.splice_figure_markers",
            side_effect=lambda md, pics: md,
        ):
            with pytest.raises(LowQualityTreeError, match="flat_zero_block"):
                _run(_apply_picture_enrichment(
                    flat_md="Some non-empty markdown content here.",
                    pic_results=[],
                    ext=".pdf",
                    filename="test.pdf",
                    splice_markers=False,
                ))

    def test_empty_md_empty_blocks_no_raise(self):
        """Empty markdown with empty blocks is not an error (nothing to extract)."""
        with patch(
            "pageindex_mcp.client.route_and_extract_flat",
            return_value=("flat_prose", []),
        ):
            # Should not raise -- empty md + empty blocks is fine
            doc_id, cc, blocks, ratio = _run(_apply_picture_enrichment(
                flat_md="",
                pic_results=[],
                ext=".pdf",
                filename="test.pdf",
                splice_markers=False,
            ))
            assert blocks == []


# ---------------------------------------------------------------------------
# 2. image_standalone detection
# ---------------------------------------------------------------------------


class TestImageStandaloneDetection:
    """When all blocks are role=image and content_class is flat_prose/flat_mixed,
    content_class gets overridden to image_standalone."""

    def test_all_image_blocks_become_image_standalone(self):
        image_blocks = [
            {"role": "image", "ocr_text": "some text"},
            {"role": "image", "ocr_text": "more text"},
        ]
        with patch(
            "pageindex_mcp.client.route_and_extract_flat",
            return_value=("flat_prose", image_blocks),
        ), patch(
            "pageindex_mcp.client._enrich_image_blocks",
            new_callable=AsyncMock,
        ), patch(
            "pageindex_mcp.client.compute_image_enrichment_ratio",
            return_value=1.0,
        ), patch(
            "pageindex_mcp.client._IMAGE_STANDALONE_PIPELINE_ENABLED",
            True,
        ):
            doc_id, cc, blocks, ratio = _run(_apply_picture_enrichment(
                flat_md="<!-- image -->\n<!-- image -->",
                pic_results=[],
                ext=".pdf",
                filename="test.pdf",
                splice_markers=False,
            ))
            assert cc == "image_standalone"

    def test_mixed_blocks_stay_flat_prose(self):
        mixed_blocks = [
            {"role": "prose", "text": "Some real content"},
            {"role": "image", "ocr_text": "some text"},
        ]
        with patch(
            "pageindex_mcp.client.route_and_extract_flat",
            return_value=("flat_prose", mixed_blocks),
        ), patch(
            "pageindex_mcp.client._enrich_image_blocks",
            new_callable=AsyncMock,
        ), patch(
            "pageindex_mcp.client.compute_image_enrichment_ratio",
            return_value=0.5,
        ), patch(
            "pageindex_mcp.client._IMAGE_STANDALONE_PIPELINE_ENABLED",
            True,
        ):
            doc_id, cc, blocks, ratio = _run(_apply_picture_enrichment(
                flat_md="Hello\n<!-- image -->",
                pic_results=[],
                ext=".pdf",
                filename="test.pdf",
                splice_markers=False,
            ))
            assert cc == "flat_prose"


# ---------------------------------------------------------------------------
# 3. content_class override for bare image files
# ---------------------------------------------------------------------------


class TestContentClassOverride:
    """apply_image_ext_content_class_override forces image_standalone for
    bare .jpg/.png files."""

    @pytest.mark.parametrize("ext", [".jpg", ".jpeg", ".png", ".tiff", ".tif"])
    def test_image_ext_forces_image_standalone(self, ext):
        result = apply_image_ext_content_class_override(ext, "flat_prose")
        assert result == "image_standalone", (
            f"ext={ext} should force image_standalone, got {result}"
        )

    def test_pdf_ext_preserves_content_class(self):
        result = apply_image_ext_content_class_override(".pdf", "flat_prose")
        assert result == "flat_prose"

    def test_pdf_ext_preserves_flat_table(self):
        result = apply_image_ext_content_class_override(".pdf", "flat_table")
        assert result == "flat_table"


# ---------------------------------------------------------------------------
# 4. Wiring: _apply_picture_enrichment is used in client.py
# ---------------------------------------------------------------------------


class TestEnrichmentWiring:
    """_apply_picture_enrichment must be importable and called from client.py."""

    def test_importable(self):
        from pageindex_mcp.client import _apply_picture_enrichment

        assert callable(_apply_picture_enrichment)

    def test_called_in_client_source(self):
        """Verify the function is actually invoked (not just imported) in client.py."""
        from pageindex_mcp import client as cli_mod

        src = inspect.getsource(cli_mod)
        # Must be called (not just defined) -- look for call syntax outside the def
        call_sites = [
            i for i, line in enumerate(src.splitlines())
            if "_apply_picture_enrichment(" in line
            and not line.strip().startswith("def ")
            and not line.strip().startswith("async def ")
        ]
        assert len(call_sites) >= 1, (
            "_apply_picture_enrichment is defined but never called in client.py"
        )

    def test_apply_image_ext_content_class_override_importable(self):
        from pageindex_mcp.client import apply_image_ext_content_class_override

        assert callable(apply_image_ext_content_class_override)

    def test_splice_markers_kwarg_exists(self):
        """_apply_picture_enrichment must accept splice_markers kwarg
        to differentiate PDF vs standalone-image paths."""
        sig = inspect.signature(_apply_picture_enrichment)
        assert "splice_markers" in sig.parameters, (
            "_apply_picture_enrichment missing splice_markers parameter"
        )
