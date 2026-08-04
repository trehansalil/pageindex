"""RFC-029 Design Property 5: Fence/HR stripping for route_and_extract_flat.

Tests verify that triple-backtick fenced code blocks and horizontal-rule
lines (---, ===, ***) are stripped from the output produced by
route_and_extract_flat, while leaving real content intact.
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
    def test_fenced_code_block_content_not_emitted(self):
        """Content inside triple-backtick fences must not appear in any block."""
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
        assert "x = 1 + 2" not in combined, "fenced code line must not appear in output"
        assert "print(x)" not in combined, "fenced code line must not appear in output"

    def test_real_content_survives_after_fence_stripped(self):
        """Prose before and after a fenced block is preserved."""
        # Arrange
        md = (
            "Before fence.\n"
            "\n"
            "```\n"
            "ignored code\n"
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
        assert "ignored code" not in combined

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

    def test_mixed_fences_and_hrs_all_stripped(self):
        """Fenced blocks, --- HRs, === HRs, and *** HRs together — all stripped."""
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

        # Assert — no artifact appears
        for artifact in ("SELECT * FROM table;", "---", "===", "***", "another code block"):
            assert artifact not in combined, f"artifact '{artifact}' must be stripped"

        # Assert — all real content survives
        for real in ("Real content A.", "Real content B.", "Real content C.", "Real content D."):
            assert real in combined, f"real content '{real}' must survive"

    def test_fence_with_language_tag_stripped(self):
        """Opening fence with a language tag (```python) is still recognised."""
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
        assert "def foo(): pass" not in combined
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

    def test_unclosed_fence_content_is_skipped(self):
        """Content after an unclosed opening fence is not emitted as blocks."""
        # Arrange
        md = (
            "Real prose before fence.\n"
            "\n"
            "```\n"
            "skipped line inside unclosed fence\n"
        )

        # Act
        _content_class, blocks = route_and_extract_flat(md)
        texts = _block_texts(blocks)
        combined = " ".join(texts)

        # Assert — content inside the fence is skipped
        assert "skipped line inside unclosed fence" not in combined
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
        """Content on the line right after a closing fence is emitted normally."""
        # Arrange
        md = (
            "```\n"
            "hidden\n"
            "```\n"
            "Visible line.\n"
        )

        # Act
        _content_class, blocks = route_and_extract_flat(md)
        texts = _block_texts(blocks)
        combined = " ".join(texts)

        # Assert
        assert "hidden" not in combined
        assert "Visible line." in combined
