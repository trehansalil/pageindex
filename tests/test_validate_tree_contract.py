# tests/test_validate_tree_contract.py
"""Behavioral contract tests for the tree-quality gate (HR5).

Covers WORKER-01-C2: validate_tree() must reject low-quality trees before
persistence — failing on node_count<3, depth<2, or garbling (NUL / replacement
bytes), and passing a well-formed nested tree. The gate's verdict is what makes
the worker raise LowQualityTreeError instead of silently storing a bad tree.
"""

from pageindex_mcp.helpers import validate_tree


def _nested_ok_tree():
    """A valid tree: >=3 nodes, depth>=2, clean text."""
    return [
        {
            "title": "Root",
            "text": "clean root section text",
            "nodes": [
                {"title": "Child A", "text": "first child clause text"},
                {"title": "Child B", "text": "second child clause text"},
            ],
        }
    ]


def test_validate_tree_rejects_single_node():
    """WORKER-01-C2: a 1-node tree fails with reason node_count<3."""
    ok, reason = validate_tree([{"title": "Only", "text": "lonely node"}])
    assert ok is False
    assert reason == "node_count<3"


def test_validate_tree_rejects_flat_siblings_depth():
    """WORKER-01-C2: three flat siblings (no nesting) fail with reason depth<2."""
    flat = [
        {"title": "A", "text": "alpha"},
        {"title": "B", "text": "bravo"},
        {"title": "C", "text": "charlie"},
    ]
    ok, reason = validate_tree(flat)
    assert ok is False
    assert reason == "depth<2"


def test_validate_tree_rejects_garbling_nul_byte():
    """WORKER-01-C2: a node whose text contains a NUL ("\\x00") fails as garbling.

    This is the validated German-insurance failure mode (PyPDF2 byte garbling).
    """
    garbled = [
        {
            "title": "Root",
            "text": "ok",
            "nodes": [
                {"title": "Bad", "text": "corrupt\x00bytes here"},
                {"title": "Good", "text": "this one is fine"},
            ],
        }
    ]
    ok, reason = validate_tree(garbled)
    assert ok is False
    assert reason == "garbling"


def test_validate_tree_accepts_wellformed_nested_tree():
    """WORKER-01-C2: a nested tree of >=3 nodes with depth>=2 passes (True, "")."""
    ok, reason = validate_tree(_nested_ok_tree())
    assert ok is True
    assert reason == ""


# ---------------------------------------------------------------------------
# Zone-5 fix: table block content visibility in tree metrics
# ---------------------------------------------------------------------------

from pageindex_mcp.helpers.tree_validation import (
    TreeSignals,
    _flatten_tree_text,
    _node_char_count,
    _node_text_parts,
    _tree_max_leaf_ratio,
)


def _table_only_tree():
    """A tree where leaf nodes carry content only in table fields, no 'text'."""
    return [
        {
            "title": "Root",
            "text": "",
            "nodes": [
                {
                    "title": "Table Leaf A",
                    "text": "",
                    "headers": ["Col1", "Col2", "Col3"],
                    "rows": [
                        ["alpha", "bravo", "charlie"],
                        ["delta", "echo", "foxtrot"],
                    ],
                },
                {
                    "title": "Table Leaf B",
                    "text": "",
                    "row_records": [
                        {"key": "premium", "value": "1200"},
                        {"key": "deductible", "value": "500"},
                    ],
                },
            ],
        }
    ]


def _table_only_leaf():
    """A single leaf node with table content but no 'text' field."""
    return {
        "title": "",
        "text": "",
        "headers": ["Name", "Amount"],
        "rows": [["Alice", "100"], ["Bob", "200"]],
    }


class TestFlattenTreeTextTableBlocks:
    """Contract: _flatten_tree_text includes table block content."""

    def test_table_only_nodes_produce_nonzero_chars(self):
        """_flatten_tree_text must extract headers/rows/row_records content
        so char count is non-zero for table-only nodes."""
        flat = _flatten_tree_text(_table_only_tree())
        assert len(flat) > 0, "table-only tree produced zero-length flat_text"

    def test_headers_appear_in_flat_text(self):
        flat = _flatten_tree_text(_table_only_tree())
        assert "Col1" in flat
        assert "Col2" in flat

    def test_row_cells_appear_in_flat_text(self):
        flat = _flatten_tree_text(_table_only_tree())
        assert "alpha" in flat
        assert "foxtrot" in flat

    def test_row_records_dict_values_appear_in_flat_text(self):
        flat = _flatten_tree_text(_table_only_tree())
        assert "premium" in flat
        assert "1200" in flat

    def test_node_text_parts_extracts_all_table_fields(self):
        """_node_text_parts extracts headers, row cells, and row_records."""
        node = {
            "title": "T",
            "text": "body",
            "headers": ["H1"],
            "rows": [["R1C1"]],
            "row_records": [{"k": "v"}],
        }
        parts = _node_text_parts(node)
        assert "T" in parts
        assert "body" in parts
        assert "H1" in parts
        assert "R1C1" in parts
        assert "v" in parts


class TestTreeMaxLeafRatioTableContent:
    """Contract: _tree_max_leaf_ratio counts table content chars in leaf sizing."""

    def test_table_only_leaf_has_nonzero_char_count(self):
        """_node_char_count must be > 0 for a leaf with only table content."""
        count = _node_char_count(_table_only_leaf())
        assert count > 0, "table-only leaf reported 0 chars"

    def test_leaf_ratio_denominator_includes_table_chars(self):
        """_tree_max_leaf_ratio total must reflect table content."""
        tree = _table_only_tree()
        max_leaf, total, ratio = _tree_max_leaf_ratio(tree)
        assert total > 0, "total chars is 0 for table-only tree"
        assert max_leaf > 0, "max_leaf chars is 0 for table-only tree"
        assert 0.0 < ratio <= 1.0


class TestTreeSignalsFromTreeTableBlocks:
    """Contract: TreeSignals.from_tree produces non-zero flat_text for table-only trees."""

    def test_flat_text_nonzero_for_table_only_tree(self):
        sig = TreeSignals.from_tree(_table_only_tree())
        assert len(sig.flat_text) > 0, "TreeSignals.flat_text is empty for table-only tree"

    def test_primary_text_matches_flat_text(self):
        sig = TreeSignals.from_tree(_table_only_tree())
        assert sig.primary_text == sig.flat_text

    def test_node_count_correct_for_table_tree(self):
        sig = TreeSignals.from_tree(_table_only_tree())
        assert sig.node_count == 3  # root + 2 children
