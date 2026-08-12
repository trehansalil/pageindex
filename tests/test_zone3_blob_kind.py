"""Zone-3 blob-kind tests: normalize_for_garble with BlobKind.RAW_MARKDOWN
strips markdown scaffolding correctly, TREE_TEXT returns identity,
GARBLE_DIGIT_FLOOR is 500."""

from __future__ import annotations

import pytest

from pageindex_mcp.script import BlobKind, GARBLE_DIGIT_FLOOR, normalize_for_garble


# ---------------------------------------------------------------------------
# GARBLE_DIGIT_FLOOR constant
# ---------------------------------------------------------------------------

class TestGarbleDigitFloor:
    def test_value_is_500(self):
        assert GARBLE_DIGIT_FLOOR == 500

    def test_type_is_int(self):
        assert isinstance(GARBLE_DIGIT_FLOOR, int)


# ---------------------------------------------------------------------------
# BlobKind enum
# ---------------------------------------------------------------------------

class TestBlobKindEnum:
    def test_has_raw_markdown(self):
        assert BlobKind.RAW_MARKDOWN == "RAW_MARKDOWN"

    def test_has_tree_text(self):
        assert BlobKind.TREE_TEXT == "TREE_TEXT"

    def test_exactly_two_members(self):
        assert len(BlobKind) == 2


# ---------------------------------------------------------------------------
# normalize_for_garble: TREE_TEXT returns identity
# ---------------------------------------------------------------------------

class TestNormalizeTreeText:
    def test_identity_plain(self):
        blob = "Some plain text with no markdown"
        assert normalize_for_garble(blob, BlobKind.TREE_TEXT) == blob

    def test_identity_with_hashes(self):
        """TREE_TEXT should NOT strip heading markers."""
        blob = "## Heading with hashes"
        assert normalize_for_garble(blob, BlobKind.TREE_TEXT) == blob

    def test_identity_with_pipes(self):
        blob = "| col1 | col2 |"
        assert normalize_for_garble(blob, BlobKind.TREE_TEXT) == blob

    def test_identity_with_html_comments(self):
        blob = "text <!-- comment --> more"
        assert normalize_for_garble(blob, BlobKind.TREE_TEXT) == blob

    def test_identity_empty(self):
        assert normalize_for_garble("", BlobKind.TREE_TEXT) == ""

    def test_identity_whitespace(self):
        blob = "  spaced  out  text  "
        assert normalize_for_garble(blob, BlobKind.TREE_TEXT) == blob


# ---------------------------------------------------------------------------
# normalize_for_garble: RAW_MARKDOWN strips scaffolding
# ---------------------------------------------------------------------------

class TestNormalizeRawMarkdown:
    def test_strips_heading_markers(self):
        blob = "# Heading"
        result = normalize_for_garble(blob, BlobKind.RAW_MARKDOWN)
        assert "#" not in result
        assert "Heading" in result

    def test_strips_multiple_heading_levels(self):
        blob = "## Level 2\n### Level 3\n###### Level 6"
        result = normalize_for_garble(blob, BlobKind.RAW_MARKDOWN)
        assert "#" not in result
        assert "Level 2" in result
        assert "Level 3" in result

    def test_strips_pipes(self):
        blob = "| col1 | col2 | col3 |"
        result = normalize_for_garble(blob, BlobKind.RAW_MARKDOWN)
        assert "|" not in result
        assert "col1" in result
        assert "col2" in result

    def test_strips_html_comments(self):
        blob = "before <!-- image --> after"
        result = normalize_for_garble(blob, BlobKind.RAW_MARKDOWN)
        assert "<!--" not in result
        assert "-->" not in result
        assert "before" in result
        assert "after" in result

    def test_collapses_whitespace(self):
        blob = "word1   word2\t\tword3\n\nword4"
        result = normalize_for_garble(blob, BlobKind.RAW_MARKDOWN)
        # Whitespace should be collapsed to single spaces
        assert "  " not in result
        assert "\t" not in result
        assert "\n" not in result

    def test_combined_stripping(self):
        blob = "## Title\n| a | b |\n<!-- comment -->\nBody text"
        result = normalize_for_garble(blob, BlobKind.RAW_MARKDOWN)
        assert "#" not in result
        assert "|" not in result
        assert "<!--" not in result
        assert "Title" in result
        assert "Body text" in result
