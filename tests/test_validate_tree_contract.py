# tests/test_validate_tree_contract.py
"""Behavioral contract tests for the tree-quality gate (HR5).

Covers WORKER-01-C2: validate_tree() must reject low-quality trees before
persistence — failing on node_count<3, depth<2, or garbling (NUL / replacement
bytes), and passing a well-formed nested tree. The gate's verdict is what makes
the worker raise LowQualityTreeError instead of silently storing a bad tree.
"""

import pytest

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
