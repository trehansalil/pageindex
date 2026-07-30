"""Tests for RFC-023 Task 2.1 (D4): content-quality guard on the
``cat_b_promoted`` gate in ``classify_verdict``.

Validates Design Property 5: for any flat-routed document evaluated by
``cat_b_promoted``, promotion to PASS SHALL be blocked if
``len(flat_text.strip()) < MIN_FLAT_PROMOTION_CHARS`` (default 500) OR if the
ratio of image-placeholder blocks to total blocks exceeds 0.5, regardless of
``node_count``, ``max_leaf_ratio``, or garble status.

Note: `_flatten_tree_text` concatenates node text with no separator, so
per-block text carries a trailing "\\n" here (as real extracted markdown
blocks do) to make each block land on its own line for the
placeholder-ratio line-scan in `classify_verdict`.
"""

from pageindex_mcp.helpers import classify_verdict

_IMAGE_MARKER = "<!-- image -->"


class TestCatBPromotedContentQualityGuard:
    def test_placeholder_blocks_below_char_threshold_blocked(self):
        """Doc 21 regression case: 15 <!-- image --> blocks, ~210 total
        chars. Passes node_count/leaf-ratio/garble gates pre-D4 but must
        no longer be promoted."""
        structure = [{"title": "", "text": _IMAGE_MARKER + "\n"} for _ in range(15)]
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert reason != "cat_b_promoted"
        assert verdict != "PASS"

    def test_real_text_blocks_above_threshold_promoted(self):
        structure = [
            {
                "title": "",
                "text": (
                    f"block number {i} has real prose content describing the "
                    "document in detail with enough words to be meaningful. " * 3 + "\n"
                ),
            }
            for i in range(15)
        ]
        flat_text = "".join(b["text"] for b in structure)
        assert len(flat_text.strip()) >= 500
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert (verdict, reason) == ("PASS", "cat_b_promoted")

    def test_placeholder_ratio_over_half_blocked_even_with_enough_chars(self):
        """Enough total chars to clear MIN_FLAT_PROMOTION_CHARS, but more
        than half the blocks are bare image placeholders (ratio 0.55)."""
        real_block = "abcde fghij klmno pqrst uvwxy zabcd hijkl mnopq\n"  # 49 chars
        structure = [{"title": "", "text": _IMAGE_MARKER + "\n"} for _ in range(11)]
        structure += [{"title": "", "text": real_block} for _ in range(9)]
        flat_text = "".join(b["text"] for b in structure)
        assert len(flat_text.strip()) >= 500
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert reason != "cat_b_promoted"

    def test_placeholder_ratio_exactly_half_not_blocked_by_ratio_gate(self):
        """Ratio == 0.5 is the boundary (gate rejects only when > 0.5)."""
        real_block = "abcde fghij klmno pqrst uvwxy zabcd hijkl mnopq\n"  # 49 chars
        structure = [{"title": "", "text": _IMAGE_MARKER + "\n"} for _ in range(10)]
        structure += [{"title": "", "text": real_block} for _ in range(10)]
        flat_text = "".join(b["text"] for b in structure)
        assert len(flat_text.strip()) >= 500
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert (verdict, reason) == ("PASS", "cat_b_promoted")

    def test_short_real_text_below_min_chars_blocked(self):
        structure = [{"title": "", "text": "short\n"} for _ in range(15)]
        flat_text = "".join(b["text"] for b in structure)
        assert len(flat_text.strip()) < 500
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert reason != "cat_b_promoted"
