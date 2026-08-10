"""RFC-034 D19 -- unit tests: enrichment preservation.

Addresses R4 (image pie chart, MARGINAL -> FAIL): 489 chars of real OCR
digits replaced by 1,203 chars of placeholder text. `_enrich_image_blocks`
(client.py) now compares information density (`_ocr_information_density`)
before promoting enrichment `ocr_text` over existing OCR text, preferring
concatenation over outright replacement wherever both sides carry signal.

Task 13.8 (tasks-rfc034-run15-reconciliation-remediation.md #task-13-8).
Validates: RFC-034 D19 / Design D19.
"""
from unittest.mock import patch

import pytest


class TestEnrichmentPreservation:
    """_enrich_image_blocks preserves real OCR content over low-density
    enrichment output."""

    @pytest.mark.asyncio
    async def test_real_ocr_digits_survive_boilerplate_enrichment(self):
        """489 chars of real OCR digits vs 1,203 chars of boilerplate --
        the real OCR content must survive."""
        from pageindex_mcp.client import _enrich_image_blocks

        real_ocr = "12.4% 8.9% 34.1% 22.7% 15.3% 6.6% " * 14  # digit-dense
        real_ocr = real_ocr[:489]
        boilerplate = (
            "[ -- image content unavailable -- ] :: "
            "[ -- rendering placeholder -- ] :: "
        ) * 20
        boilerplate = boilerplate[:1203]

        blocks = [{"role": "image", "index": 0, "ocr_text": real_ocr}]
        pic_results = [{"ocr_text": boilerplate}]

        with patch("pageindex_mcp.client.save_figure"):
            await _enrich_image_blocks(blocks, pic_results, "doc1")

        assert blocks[0]["ocr_text"] == real_ocr

    @pytest.mark.asyncio
    async def test_empty_existing_ocr_takes_description_enrichment(self):
        """Empty existing OCR + real description -- the description is used
        (no regression to the enrichment feature)."""
        from pageindex_mcp.client import _enrich_image_blocks

        blocks = [{"role": "image", "index": 0, "ocr_text": ""}]
        pic_results = [{"ocr_text": "", "description": "A bar chart showing quarterly revenue"}]

        with patch("pageindex_mcp.client.save_figure"):
            await _enrich_image_blocks(blocks, pic_results, "doc1")

        assert blocks[0]["description"] == "A bar chart showing quarterly revenue"

    @pytest.mark.asyncio
    async def test_both_real_content_concatenates(self):
        """Both existing OCR and enrichment carry real signal -- guard only
        decides ordering, not content discard, so both survive concatenated."""
        from pageindex_mcp.client import _enrich_image_blocks

        existing_ocr = "42% Labor 31% Materials 27% Overhead"
        new_ocr = "Regional breakdown: North 55%, South 45%"

        blocks = [{"role": "image", "index": 0, "ocr_text": existing_ocr}]
        pic_results = [{"ocr_text": new_ocr}]

        with patch("pageindex_mcp.client.save_figure"):
            await _enrich_image_blocks(blocks, pic_results, "doc1")

        assert existing_ocr in blocks[0]["ocr_text"]
        assert new_ocr in blocks[0]["ocr_text"]
