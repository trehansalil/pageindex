# ALLOW-NEW-TEST-FILE: zone-4 measurement blind-spot contract tests
"""Zone 4 — Measurement & Audit Self-Reinforcing Blind Spot contract tests.

Tests the canonical measurement helpers that zone-4 remediation exported:
- _flat_block_primary_text: flat block text extraction (header-only table fix)
- _node_text_parts: tree node text extraction including table content
- _node_char_count: tree node character counting via _node_text_parts
"""

from pageindex_mcp.helpers import (
    _flat_block_primary_text,
    _flatten_tree_text,
    _node_char_count,
    _node_text_parts,
    TreeSignals,
)


class TestFlatBlockPrimaryText:
    """_flat_block_primary_text must return usable text for every block shape."""

    def test_text_block_returns_text(self):
        block = {"text": "hello world", "role": "paragraph"}
        assert _flat_block_primary_text(block) == "hello world"

    def test_table_with_row_records(self):
        block = {"role": "table", "row_records": ["row1", "row2"]}
        assert _flat_block_primary_text(block) == "row1\nrow2"

    def test_table_header_only_fallback(self):
        """Zone-4 fix: header-only tables must not return empty string."""
        block = {"role": "table", "headers": ["Col A", "Col B"], "row_records": []}
        result = _flat_block_primary_text(block)
        assert "Col A" in result
        assert "Col B" in result
        assert len(result) > 0

    def test_table_headers_and_rows_prefers_rows(self):
        block = {
            "role": "table",
            "headers": ["H1", "H2"],
            "row_records": ["data1", "data2"],
        }
        assert _flat_block_primary_text(block) == "data1\ndata2"

    def test_table_no_headers_no_rows(self):
        block = {"role": "table"}
        assert _flat_block_primary_text(block) == ""

    def test_table_none_row_records(self):
        block = {"role": "table", "row_records": None, "headers": ["X"]}
        result = _flat_block_primary_text(block)
        assert "X" in result

    def test_table_prefers_row_records_over_text(self):
        """D2 (RFC-041): table blocks consistently use row_records,
        ignoring any text key.  Unified behavior across all purposes."""
        block = {"text": "direct text", "role": "table", "row_records": ["r"]}
        assert _flat_block_primary_text(block) == "r"

    def test_empty_block(self):
        assert _flat_block_primary_text({}) == ""

    def test_headers_with_falsy_values_skipped(self):
        block = {"role": "table", "headers": ["A", None, "", "B"], "row_records": []}
        result = _flat_block_primary_text(block)
        assert result == "A | B"


class TestNodeTextParts:
    """_node_text_parts must extract all text-bearing content from tree nodes."""

    def test_title_and_text(self):
        node = {"title": "Title", "text": "Body"}
        parts = _node_text_parts(node)
        assert "Title" in parts
        assert "Body" in parts

    def test_table_headers(self):
        node = {"headers": ["H1", "H2"]}
        parts = _node_text_parts(node)
        assert "H1" in parts
        assert "H2" in parts

    def test_table_row_records_strings(self):
        node = {"row_records": ["rec1", "rec2"]}
        parts = _node_text_parts(node)
        assert "rec1" in parts
        assert "rec2" in parts

    def test_table_row_records_dicts(self):
        node = {"row_records": [{"col": "val"}]}
        parts = _node_text_parts(node)
        assert "val" in parts

    def test_table_rows_cells(self):
        node = {"rows": [["a", "b"], ["c", "d"]]}
        parts = _node_text_parts(node)
        assert set(parts) == {"a", "b", "c", "d"}

    def test_empty_node(self):
        assert _node_text_parts({}) == []

    def test_none_fields_safe(self):
        node = {"headers": None, "rows": None, "row_records": None}
        assert _node_text_parts(node) == []


class TestNodeCharCount:
    """_node_char_count must sum all text parts for correct sizing."""

    def test_counts_title_and_text(self):
        node = {"title": "AB", "text": "CDE"}
        assert _node_char_count(node) == 5

    def test_counts_table_content(self):
        node = {"headers": ["H1"], "row_records": ["data"]}
        assert _node_char_count(node) == 6  # "H1" + "data"

    def test_empty_node_zero(self):
        assert _node_char_count({}) == 0


class TestNodeTextPartsFlagCombinations:
    """D5 (RFC-047): _node_text_parts include_ocr_text / include_summary split."""

    NODE = {
        "title": "Title",
        "text": "Body",
        "ocr_text": "OCR content",
        "summary": "LLM summary",
    }

    def test_ocr_only(self):
        parts = _node_text_parts(self.NODE, include_ocr_text=True, include_summary=False)
        assert "OCR content" in parts
        assert "LLM summary" not in parts

    def test_summary_only(self):
        parts = _node_text_parts(self.NODE, include_ocr_text=False, include_summary=True)
        assert "LLM summary" in parts
        assert "OCR content" not in parts

    def test_both_true(self):
        parts = _node_text_parts(self.NODE, include_ocr_text=True, include_summary=True)
        assert "OCR content" in parts
        assert "LLM summary" in parts

    def test_both_false(self):
        parts = _node_text_parts(self.NODE, include_ocr_text=False, include_summary=False)
        assert "OCR content" not in parts
        assert "LLM summary" not in parts
        assert "Title" in parts
        assert "Body" in parts

    def test_dedup_ocr_text_matching_body(self):
        node = {"text": "same", "ocr_text": "same", "summary": "different"}
        parts = _node_text_parts(node, include_ocr_text=True, include_summary=True)
        assert parts.count("same") == 1
        assert "different" in parts

    def test_dedup_summary_matching_body(self):
        node = {"text": "same", "ocr_text": "different", "summary": "same"}
        parts = _node_text_parts(node, include_ocr_text=True, include_summary=True)
        assert parts.count("same") == 1
        assert "different" in parts


class TestFlattenTreeTextSplitFlags:
    """D5 (RFC-047): _flatten_tree_text passes split flags through."""

    TREE = [
        {
            "title": "Root",
            "text": "body",
            "ocr_text": "ocr here",
            "summary": "summary here",
            "nodes": [],
        }
    ]

    def test_ocr_only_passthrough(self):
        text = _flatten_tree_text(self.TREE, include_ocr_text=True, include_summary=False)
        assert "ocr here" in text
        assert "summary here" not in text

    def test_summary_only_passthrough(self):
        text = _flatten_tree_text(self.TREE, include_ocr_text=False, include_summary=True)
        assert "summary here" in text
        assert "ocr here" not in text

    def test_neither(self):
        text = _flatten_tree_text(self.TREE, include_ocr_text=False, include_summary=False)
        assert "ocr here" not in text
        assert "summary here" not in text
        assert "Root" in text


class TestFlatTextCorrectedExcludesSummary:
    """D5 (RFC-047): TreeSignals.flat_text_corrected includes OCR but not summary."""

    def test_corrected_excludes_summary(self):
        tree = [
            {
                "title": "Page 1",
                "text": "body text",
                "ocr_text": "ocr content",
                "summary": "llm summary text",
                "nodes": [],
            }
        ]
        signals = TreeSignals.from_tree(tree)
        assert "ocr content" in signals.flat_text_corrected
        assert "llm summary text" not in signals.flat_text_corrected
        assert "body text" in signals.flat_text_corrected
