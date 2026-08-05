"""RFC-029 Design Property 5 / RFC-030 D0: Fence/HR handling for
route_and_extract_flat.

RFC-030 D0 superseded the original RFC-029 D3 behavior: the old fence-parity
toggle silently swallowed ALL content between an opening and closing (or
unpaired/unclosed) triple-backtick fence, which caused real content loss in
production (Reitlehrer corpus doc). The fix (helpers.py:2711-2726) strips
only the fence-delimiter lines themselves (``` optionally with a language
tag); enclosed content now falls through to the normal prose/table parsers
and is preserved. See design-rfc030-run13-rfc029-regression-fixes.md
Property 1.

Horizontal-rule lines (---, ===, ***) are still stripped as before.
"""

import pytest

from pageindex_mcp.helpers import route_and_extract_flat


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _block_texts(blocks: list[dict]) -> list[str]:
    """Return the text values of all blocks that carry a 'text' key."""
    return [b["text"] for b in blocks if "text" in b]


def _all_roles(blocks: list[dict]) -> list[str]:
    return [b["role"] for b in blocks]


# ---------------------------------------------------------------------------
# Property 5 primary — fences and HRs are stripped; real content survives
# ---------------------------------------------------------------------------

class TestFenceAndHRStripping:
    def test_fence_delimiters_stripped_content_preserved(self):
        """Only the ``` delimiter lines are stripped; enclosed content survives (RFC-030 D0)."""
        # Arrange
        md = (
            "Introduction paragraph.\n"
            "\n"
            "```python\n"
            "x = 1 + 2\n"
            "print(x)\n"
            "```\n"
            "\n"
            "Conclusion paragraph.\n"
        )

        # Act
        content_class, blocks = route_and_extract_flat(md)
        texts = _block_texts(blocks)

        # Assert
        combined = " ".join(texts)
        assert "x = 1 + 2" in combined, "enclosed content must fall through, not be swallowed"
        assert "print(x)" in combined
        assert "```" not in combined, "fence delimiter lines themselves must be stripped"

    def test_real_content_survives_around_and_inside_fence(self):
        """Prose before, inside, and after a fenced block is all preserved (RFC-030 D0)."""
        # Arrange
        md = (
            "Before fence.\n"
            "\n"
            "```\n"
            "formerly ignored code\n"
            "```\n"
            "\n"
            "After fence.\n"
        )

        # Act
        _content_class, blocks = route_and_extract_flat(md)
        texts = _block_texts(blocks)
        combined = " ".join(texts)

        # Assert
        assert "Before fence." in combined
        assert "After fence." in combined
        assert "formerly ignored code" in combined
        assert "```" not in combined

    def test_dash_hr_stripped(self):
        """A --- horizontal rule must not appear in any block text."""
        # Arrange
        md = (
            "Section A.\n"
            "\n"
            "---\n"
            "\n"
            "Section B.\n"
        )

        # Act
        _content_class, blocks = route_and_extract_flat(md)
        texts = _block_texts(blocks)
        combined = " ".join(texts)

        # Assert
        assert "---" not in combined
        assert "Section A." in combined
        assert "Section B." in combined

    def test_equals_hr_stripped(self):
        """A === horizontal rule must not appear in any block text."""
        # Arrange
        md = (
            "First paragraph.\n"
            "\n"
            "===\n"
            "\n"
            "Second paragraph.\n"
        )

        # Act
        _content_class, blocks = route_and_extract_flat(md)
        texts = _block_texts(blocks)
        combined = " ".join(texts)

        # Assert
        assert "===" not in combined
        assert "First paragraph." in combined
        assert "Second paragraph." in combined

    def test_star_hr_stripped(self):
        """A *** horizontal rule must not appear in any block text."""
        # Arrange
        md = (
            "Alpha.\n"
            "\n"
            "***\n"
            "\n"
            "Beta.\n"
        )

        # Act
        _content_class, blocks = route_and_extract_flat(md)
        texts = _block_texts(blocks)
        combined = " ".join(texts)

        # Assert
        assert "***" not in combined
        assert "Alpha." in combined
        assert "Beta." in combined

    def test_mixed_fences_and_hrs_delimiters_stripped_content_survives(self):
        """Fence delimiters and HR lines are stripped; fenced content and prose all survive (RFC-030 D0)."""
        # Arrange
        md = (
            "Real content A.\n"
            "\n"
            "```sql\n"
            "SELECT * FROM table;\n"
            "```\n"
            "\n"
            "---\n"
            "\n"
            "Real content B.\n"
            "\n"
            "===\n"
            "\n"
            "Real content C.\n"
            "\n"
            "```\n"
            "another code block\n"
            "```\n"
            "\n"
            "***\n"
            "\n"
            "Real content D.\n"
        )

        # Act
        _content_class, blocks = route_and_extract_flat(md)
        texts = _block_texts(blocks)
        combined = " ".join(texts)

        # Assert — HR divider lines are stripped, fence delimiters are stripped
        for artifact in ("---", "===", "***", "```"):
            assert artifact not in combined, f"artifact '{artifact}' must be stripped"

        # Assert — fenced content and all real content survive
        for real in (
            "SELECT * FROM table;",
            "another code block",
            "Real content A.",
            "Real content B.",
            "Real content C.",
            "Real content D.",
        ):
            assert real in combined, f"content '{real}' must survive"

    def test_fence_with_language_tag_delimiter_stripped_content_survives(self):
        """Opening fence with a language tag (```python) has its delimiter stripped; content survives."""
        # Arrange
        md = (
            "Preamble.\n"
            "\n"
            "```python\n"
            "def foo(): pass\n"
            "```\n"
            "\n"
            "Postamble.\n"
        )

        # Act
        _content_class, blocks = route_and_extract_flat(md)
        texts = _block_texts(blocks)
        combined = " ".join(texts)

        # Assert
        assert "def foo(): pass" in combined
        assert "```" not in combined
        assert "Preamble." in combined
        assert "Postamble." in combined

    def test_long_hr_variants_stripped(self):
        """HRs of 4+ repeated characters are also stripped."""
        # Arrange
        md = (
            "Before.\n"
            "\n"
            "------\n"
            "\n"
            "Middle.\n"
            "\n"
            "======\n"
            "\n"
            "After.\n"
        )

        # Act
        _content_class, blocks = route_and_extract_flat(md)
        texts = _block_texts(blocks)
        combined = " ".join(texts)

        # Assert
        assert "------" not in combined
        assert "======" not in combined
        assert "Before." in combined
        assert "Middle." in combined
        assert "After." in combined


# ---------------------------------------------------------------------------
# Regression — plain markdown (no fences, no HRs) is unaffected
# ---------------------------------------------------------------------------

class TestRegressionPlainMarkdown:
    def test_plain_prose_block_count_unchanged(self):
        """Plain prose paragraphs are unaffected; block count matches baseline."""
        # Arrange — three prose paragraphs separated by blank lines
        md = (
            "First paragraph of plain text.\n"
            "\n"
            "Second paragraph of plain text.\n"
            "\n"
            "Third paragraph of plain text.\n"
        )

        # Act
        content_class, blocks = route_and_extract_flat(md)
        prose_blocks = [b for b in blocks if b.get("role") == "prose"]

        # Assert
        assert content_class == "flat_prose"
        # All three paragraphs must be represented (may be merged into fewer
        # prose blocks depending on flush logic, but text must all be present)
        combined = " ".join(b["text"] for b in prose_blocks)
        assert "First paragraph of plain text." in combined
        assert "Second paragraph of plain text." in combined
        assert "Third paragraph of plain text." in combined

    def test_heading_and_prose_roles_preserved(self):
        """Title and prose roles survive without fences/HRs present."""
        # Arrange
        md = (
            "# Section Title\n"
            "\n"
            "A paragraph of content.\n"
        )

        # Act
        _content_class, blocks = route_and_extract_flat(md)
        roles = _all_roles(blocks)

        # Assert
        assert "title" in roles
        assert "prose" in roles

    def test_no_spurious_blocks_added_to_plain_markdown(self):
        """Processing plain markdown must not add extra blocks."""
        # Arrange
        md = "A single sentence."

        # Act
        _content_class, blocks = route_and_extract_flat(md)

        # Assert — exactly one block produced for a single sentence
        assert len(blocks) == 1
        assert blocks[0]["role"] == "prose"
        assert "A single sentence." in blocks[0]["text"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_unclosed_fence_at_eof_does_not_raise(self):
        """An unclosed triple-backtick fence must not raise an exception."""
        # Arrange
        md = (
            "Before unclosed fence.\n"
            "\n"
            "```\n"
            "code line one\n"
            "code line two\n"
            # note: no closing ```
        )

        # Act — must not raise
        content_class, blocks = route_and_extract_flat(md)

        # Assert — the function returns a valid (content_class, blocks) tuple
        assert isinstance(content_class, str)
        assert isinstance(blocks, list)

    def test_unclosed_fence_content_is_preserved(self):
        """Content after an unclosed opening fence is preserved as prose, not
        silently dropped (RFC-030 D0 / task 3.4: renamed from
        test_unclosed_fence_content_is_skipped — the old fence-parity toggle
        used to swallow everything following a stray/unclosed fence marker,
        which caused real content loss in production)."""
        # Arrange
        md = (
            "Real prose before fence.\n"
            "\n"
            "```\n"
            "line inside unclosed fence\n"
        )

        # Act
        _content_class, blocks = route_and_extract_flat(md)
        texts = _block_texts(blocks)
        combined = " ".join(texts)

        # Assert — content after the stray fence marker now falls through and survives
        assert "line inside unclosed fence" in combined
        # Assert — content before the fence is present
        assert "Real prose before fence." in combined

    def test_empty_string_returns_valid_tuple(self):
        """Empty input must return ('flat_prose', []) without error."""
        # Arrange
        md = ""

        # Act
        content_class, blocks = route_and_extract_flat(md)

        # Assert
        assert content_class == "flat_prose"
        assert blocks == []

    def test_only_hrs_returns_empty_blocks(self):
        """A document consisting only of HR lines produces no content blocks."""
        # Arrange
        md = "---\n\n===\n\n***\n"

        # Act
        _content_class, blocks = route_and_extract_flat(md)

        # Assert — all lines stripped, nothing to emit
        content_blocks = [b for b in blocks if b.get("role") in {"prose", "kv", "table"}]
        assert content_blocks == []

    def test_fence_immediately_followed_by_content(self):
        """Content inside the fence and on the line right after a closing
        fence are both emitted normally (RFC-030 D0: only the delimiter
        lines are stripped)."""
        # Arrange
        md = (
            "```\n"
            "formerly hidden\n"
            "```\n"
            "Visible line.\n"
        )

        # Act
        _content_class, blocks = route_and_extract_flat(md)
        texts = _block_texts(blocks)
        combined = " ".join(texts)

        # Assert
        assert "formerly hidden" in combined
        assert "Visible line." in combined
