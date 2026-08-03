"""Tests for RFC-027 Task 1.1 (D0): split `_flat_block_text` to exclude
enrichment metadata from verdict char counts.

Validates Design Property: `_flat_block_primary_text(block)` returns only
`block.get('text', '')` plus table `row_records` -- actual extracted document
content -- and NEVER image `ocr_text`/`description` enrichment metadata.
`_flat_block_text` is unchanged and still includes enrichment, since it feeds
`_flat_search_text` and retrieval, not verdict classification.
"""

from pageindex_mcp.helpers import _flat_block_primary_text, _flat_block_text


class TestFlatBlockPrimaryTextExcludesEnrichment:
    def test_image_block_with_only_enrichment_returns_empty(self):
        """An image block with no 'text' key but ocr_text/description must
        contribute 0 chars to `_flat_block_primary_text` -- enrichment
        metadata is not document content."""
        block = {"role": "image", "ocr_text": "chart says 42%", "description": "a bar chart"}
        assert _flat_block_primary_text(block) == ""

    def test_prose_block_returns_text(self):
        """A prose block's 'text' field is primary content and passes through."""
        block = {"role": "prose", "text": "actual document prose"}
        assert _flat_block_primary_text(block) == "actual document prose"

    def test_table_block_falls_back_to_row_records(self):
        """Table blocks carry no 'text' key by design; row_records are
        document content, not enrichment, so they ARE included."""
        block = {"role": "table", "row_records": ["row one", "row two"]}
        assert _flat_block_primary_text(block) == "row one\nrow two"

    def test_table_block_with_no_row_records_returns_empty(self):
        block = {"role": "table"}
        assert _flat_block_primary_text(block) == ""

    def test_image_block_with_text_present_prefers_text(self):
        """If an image block somehow carries a 'text' key, that takes
        priority over enrichment fields (unlikely in practice, but the
        function's text-first branch must still hold)."""
        block = {"role": "image", "text": "caption", "ocr_text": "ignored"}
        assert _flat_block_primary_text(block) == "caption"


class TestFlatBlockTextRegressionUnchanged:
    """`_flat_block_text` (B3/RFC-022) must keep including enrichment --
    the D0 split is additive, not a removal of enrichment content system-wide."""

    def test_image_block_still_includes_ocr_text_and_description(self):
        block = {"role": "image", "ocr_text": "chart says 42%", "description": "a bar chart"}
        assert _flat_block_text(block) == "chart says 42%\na bar chart"

    def test_prose_block_returns_text(self):
        block = {"role": "prose", "text": "actual document prose"}
        assert _flat_block_text(block) == "actual document prose"

    def test_table_block_falls_back_to_row_records(self):
        block = {"role": "table", "row_records": ["row one", "row two"]}
        assert _flat_block_text(block) == "row one\nrow two"


class TestPrimaryTextExclusionDivergesFromFullText:
    def test_image_block_diverges_between_the_two_functions(self):
        """The whole point of the D0 split: for an image block with
        enrichment, `_flat_block_primary_text` and `_flat_block_text` must
        disagree -- primary excludes it, full includes it."""
        block = {"role": "image", "ocr_text": "42%", "description": "chart"}
        assert _flat_block_primary_text(block) != _flat_block_text(block)
        assert _flat_block_primary_text(block) == ""
        assert _flat_block_text(block) == "42%\nchart"

    def test_sum_over_blocks_excludes_enrichment_char_count(self):
        """Mirrors the `flat_char_count` computation in client.py: summing
        `_flat_block_primary_text` over a mixed block list must not include
        image-enrichment chars, even when enrichment dwarfs real content."""
        blocks = [
            {"role": "prose", "text": "short prose"},
            {"role": "image", "ocr_text": "x" * 5000, "description": "y" * 2000},
        ]
        primary_char_count = sum(len(_flat_block_primary_text(b)) for b in blocks)
        full_char_count = sum(len(_flat_block_text(b)) for b in blocks)
        assert primary_char_count == len("short prose")
        assert full_char_count == len("short prose") + len("x" * 5000 + "\n" + "y" * 2000)
        assert primary_char_count < full_char_count
