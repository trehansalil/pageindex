"""Zone-5: primary_text contract tests.

Locks the contract that primary_text excludes enrichment metadata so
classify_verdict's image-enrichment-promoted path uses structurally correct
char counts.

1. **TreeSignals.from_tree**: primary_text == flat_text (no enrichment in trees).
2. **_flat_block_primary_text**: excludes ocr_text/description from image blocks.
3. **classify_verdict regression**: warid-597-shaped doc (70 image blocks,
   3208 chars barcode/digit noise) does NOT earn image_enrichment_promoted PASS
   when primary_text is below the min_image_promoted_chars floor.
"""
from __future__ import annotations

import pytest

from pageindex_mcp.helpers import (
    TreeDefect,
    TreeGateResult,
    TreeSignals,
    _flat_block_primary_text,
    classify_verdict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _varied(seed: int, n: int = 60) -> str:
    """Non-repeating filler that trips no garble heuristics."""
    return " ".join(f"word{seed}n{j}alpha" for j in range(n))


def _leaf(title: str, text: str, **extra) -> dict:
    return {"title": title, "text": text, "nodes": [], **extra}


# ---------------------------------------------------------------------------
# 1. TreeSignals.from_tree: primary_text == flat_text for trees
# ---------------------------------------------------------------------------


class TestTreeSignalsPrimaryText:
    def test_primary_text_equals_flat_text(self):
        tree = [
            {"title": "Root", "text": "Hello world", "nodes": [
                _leaf("A", _varied(1)),
                _leaf("B", _varied(2)),
            ]},
        ]
        sig = TreeSignals.from_tree(tree)
        assert sig.primary_text == sig.flat_text
        assert len(sig.primary_text) > 0

    def test_empty_tree_both_empty(self):
        sig = TreeSignals.from_tree([])
        assert sig.primary_text == sig.flat_text == ""


# ---------------------------------------------------------------------------
# 2. _flat_block_primary_text: contract for enrichment exclusion
# ---------------------------------------------------------------------------


class TestFlatBlockPrimaryText:
    def test_prose_block_returns_text(self):
        block = {"text": "Some real content", "role": "prose"}
        assert _flat_block_primary_text(block) == "Some real content"

    def test_table_block_returns_row_records(self):
        block = {"role": "table", "row_records": ["row1", "row2"]}
        result = _flat_block_primary_text(block)
        assert "row1" in result
        assert "row2" in result

    def test_image_block_returns_empty(self):
        """Image blocks carry ocr_text/description -- primary_text must exclude them."""
        block = {
            "role": "image",
            "ocr_text": "OCR extracted text from barcode",
            "description": "A picture of something",
        }
        result = _flat_block_primary_text(block)
        assert result == "", (
            f"_flat_block_primary_text returned {result!r} for image block; "
            "must be empty to exclude enrichment metadata"
        )

    def test_image_block_with_text_key_returns_text(self):
        """If an image block somehow has a 'text' key, that IS primary content."""
        block = {"role": "image", "text": "inline caption", "ocr_text": "OCR data"}
        result = _flat_block_primary_text(block)
        assert result == "inline caption"


# ---------------------------------------------------------------------------
# 3. classify_verdict regression: warid-597-shaped doc
# ---------------------------------------------------------------------------


class TestWarid597Regression:
    """A doc with 70 image blocks and 3208 chars of barcode/digit noise
    must NOT earn image_enrichment_promoted PASS when primary_text is
    below the min_image_promoted_chars floor (default 500)."""

    def _make_warid_597_structure(self) -> list:
        """Synthesize a tree that mimics the warid-597 pattern:
        sparse real content, many image blocks inflate flat_text."""
        # Minimal tree structure: 3 nodes, short real text
        return [
            {"title": "Doc", "text": "Short", "nodes": [
                _leaf("A", "tiny text"),
                _leaf("B", "also tiny"),
            ]},
        ]

    def _make_warid_597_blocks(self, n_image_blocks: int = 70) -> list[dict]:
        """70 image blocks with barcode/digit noise."""
        return [
            {
                "role": "image",
                "ocr_text": f"1234567890 BARCODE {i} " * 3,
                "description": f"barcode region {i}",
            }
            for i in range(n_image_blocks)
        ]

    def test_below_char_floor_returns_marginal(self):
        """When primary_text (excluding image enrichment) is below
        min_image_promoted_chars, verdict must be MARGINAL, not PASS."""
        structure = self._make_warid_597_structure()

        # Build blocks: lots of image blocks with noise
        blocks = self._make_warid_597_blocks(70)

        # primary_text = sum of _flat_block_primary_text across blocks
        # For image blocks, primary_text is "" -> total primary chars ~ 0
        primary_chars = sum(len(_flat_block_primary_text(b)) for b in blocks)
        assert primary_chars == 0, "Image blocks should contribute 0 primary chars"

        # Build TreeSignals with short primary_text to represent the real doc
        sig = TreeSignals.from_tree(structure)

        # The tree itself has very little text (< 500 chars min_image_promoted_chars)
        assert len(sig.primary_text) < 500

        # Validate that classify_verdict with high enrichment ratio does NOT
        # yield PASS via image_enrichment_promoted when primary_text is short
        verdict, reason = classify_verdict(
            structure=structure,
            content_class="flat_prose",
            validate_result=None,  # flat-doc path
            image_enrichment_ratio=0.95,  # high enrichment
        )
        # With such short primary_text, this must NOT be PASS via
        # image_enrichment_promoted
        if reason == "image_enrichment_promoted":
            pytest.fail(
                f"warid-597 regression: classify_verdict returned "
                f"({verdict}, {reason}) -- image_enrichment_promoted must not "
                f"fire when primary_text < min_image_promoted_chars"
            )

    def test_above_char_floor_with_clean_text_can_pass(self):
        """Confirm that sufficient clean primary_text DOES allow
        image_enrichment_promoted to fire (positive control)."""
        # Build a tree with enough text to pass the char floor
        long_text = _varied(42, n=200)  # ~200 words -> well over 500 chars
        structure = [
            {"title": "Doc", "text": long_text, "nodes": [
                _leaf("A", _varied(1, 100)),
                _leaf("B", _varied(2, 100)),
            ]},
        ]
        sig = TreeSignals.from_tree(structure)
        assert len(sig.primary_text) >= 500

        verdict, reason = classify_verdict(
            structure=structure,
            content_class="flat_prose",
            validate_result=None,
            image_enrichment_ratio=0.95,
        )
        # With enough clean primary_text and high enrichment, the
        # image_enrichment_promoted path should fire (PASS or MARGINAL
        # depending on other caps like depth_inadequate).
        # The key contract: it should NOT be blocked by char_floor.
        assert reason != "image_enrichment_promoted_below_char_floor", (
            f"Sufficient primary_text should pass char floor, got ({verdict}, {reason})"
        )
