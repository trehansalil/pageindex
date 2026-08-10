"""Unit tests RFC-034 D16: over-strip guard for `_strip_toc_heading_nodes`.

Design Property (D16): `_strip_toc_heading_nodes_guarded` applies D11's ToC
strip all-or-nothing per document. If the strip would reduce `max_depth` by
more than 1, or remove more than 20% of nodes, the guard discards the
stripped candidate, keeps the original tree, logs a `toc_strip_skipped`
warning, and increments the `TOC_STRIP_SKIPPED` counter. Otherwise the
stripped tree is returned, matching D11's original (unguarded) behavior.
"""

from pageindex_mcp.helpers import (
    _strip_toc_heading_nodes_guarded,
    _tree_depth,
    _tree_node_count,
)
from pageindex_mcp.metrics import TOC_STRIP_SKIPPED


def _toc_node(title):
    return {"title": f"{title} ......... 12", "text": "", "nodes": []}


def _real_node(title, text, nodes=None):
    return {"title": title, "text": text, "nodes": nodes or []}


def _skipped_count():
    return TOC_STRIP_SKIPPED._value.get()


def test_600_node_tree_skips_strip_when_over_20_percent_removed(caplog):
    """Test 1: synthetic 600-node tree, depth 3, 490 of 600 nodes are pure
    ToC dot-leader nodes (81.7% removal) -- stripping is skipped and the
    original tree is returned unchanged, with a warning logged."""
    nested_chain = _real_node(
        "Chapter 1",
        "Body text of chapter 1.",
        nodes=[
            _real_node(
                "Article 1",
                "Body text of article 1.",
                nodes=[_real_node("Clause 1.1", "Body text of clause 1.1.")],
            )
        ],
    )
    flat_real_nodes = [
        _real_node(f"Article {i}", f"Body text of article {i}.") for i in range(2, 109)
    ]
    toc_nodes = [_toc_node(f"Schedule {i}") for i in range(1, 491)]
    tree = [nested_chain] + flat_real_nodes + toc_nodes
    assert _tree_node_count(tree) == 600
    assert _tree_depth(tree) == 3

    before = _skipped_count()
    with caplog.at_level("WARNING"):
        result = _strip_toc_heading_nodes_guarded(tree, doc_name="synthetic-600.pdf")

    assert result == tree
    assert _tree_node_count(result) == 600
    assert "toc_strip_skipped" in caplog.text
    assert _skipped_count() == before + 1


def test_50_node_tree_still_strips_below_threshold():
    """Test 2: synthetic 50-node tree with 5 ToC nodes (10% removal, depth
    preserved) -- stripping still applies, matching D11's intended behavior."""
    real_nodes = [
        _real_node(f"Article {i}", f"Body text of article {i}.") for i in range(1, 46)
    ]
    toc_nodes = [_toc_node(f"Schedule {i}") for i in range(1, 6)]
    tree = real_nodes + toc_nodes
    assert _tree_node_count(tree) == 50

    before = _skipped_count()
    result = _strip_toc_heading_nodes_guarded(tree, doc_name="synthetic-50.pdf")

    assert _tree_node_count(result) == 45
    assert [n["title"] for n in result] == [f"Article {i}" for i in range(1, 46)]
    assert _skipped_count() == before


def test_node_removal_exactly_at_20_percent_threshold_still_strips():
    """Test 3a: exactly 20% node removal (20 of 100) does NOT trigger the
    guard -- the threshold is a strict ">20%", not ">=20%"."""
    real_nodes = [
        _real_node(f"Article {i}", f"Body text of article {i}.") for i in range(1, 81)
    ]
    toc_nodes = [_toc_node(f"Schedule {i}") for i in range(1, 21)]
    tree = real_nodes + toc_nodes
    assert _tree_node_count(tree) == 100

    before = _skipped_count()
    result = _strip_toc_heading_nodes_guarded(tree, doc_name="synthetic-100-at-20.pdf")

    assert _tree_node_count(result) == 80
    assert _skipped_count() == before


def test_node_removal_just_over_20_percent_threshold_skips_strip():
    """Test 3b: 21 of 100 nodes removed (21%) crosses the strict >20%
    threshold -- the guard fires and the original tree is kept."""
    real_nodes = [
        _real_node(f"Article {i}", f"Body text of article {i}.") for i in range(1, 80)
    ]
    toc_nodes = [_toc_node(f"Schedule {i}") for i in range(1, 22)]
    tree = real_nodes + toc_nodes
    assert _tree_node_count(tree) == 100

    before = _skipped_count()
    result = _strip_toc_heading_nodes_guarded(tree, doc_name="synthetic-100-over-20.pdf")

    assert result == tree
    assert _tree_node_count(result) == 100
    assert _skipped_count() == before + 1


def test_depth_reduction_of_exactly_1_still_strips():
    """Test 3c: a strip that reduces max_depth by exactly 1 (depth 3 -> 2,
    with only 1 of 10 total nodes removed) is within bounds -- only a
    reduction of MORE than 1 fires the guard."""
    deep_chain = _real_node(
        "Chapter 2",
        "Body text of chapter 2.",
        nodes=[
            _real_node(
                "Article 2",
                "Body text of article 2.",
                nodes=[_toc_node("Clause 2.1")],
            )
        ],
    )
    flat_real_nodes = [
        _real_node(f"Article {i}", f"Body text of article {i}.") for i in range(3, 9)
    ]
    tree = [deep_chain] + flat_real_nodes
    assert _tree_node_count(tree) == 9
    assert _tree_depth(tree) == 3

    before = _skipped_count()
    result = _strip_toc_heading_nodes_guarded(tree, doc_name="synthetic-depth-1-drop.pdf")

    assert _tree_depth(result) == 2
    assert result[0]["nodes"][0]["nodes"] == []
    assert _skipped_count() == before


def test_fdl33_regression_stripping_still_applies():
    """Test 4: regression fixture modeled on the FDL-33 corpus document
    (~502 nodes, a legitimate ToC fraction under the 20% guard threshold,
    depth preserved) -- D16's guard must not regress D11's fix for FDL-33,
    the case D11 was originally built to resolve."""
    real_nodes = [
        _real_node(f"Article {i}", f"Body text of article {i}.") for i in range(1, 413)
    ]
    toc_nodes = [_toc_node(f"Article {i}") for i in range(1, 91)]
    tree = real_nodes + toc_nodes
    total_before = _tree_node_count(tree)
    assert total_before == 502
    depth_before = _tree_depth(tree)

    before = _skipped_count()
    result = _strip_toc_heading_nodes_guarded(tree, doc_name="fdl-33.pdf")

    assert _tree_node_count(result) == 412
    assert (total_before - _tree_node_count(result)) / total_before < 0.20
    assert _tree_depth(result) == depth_before
    assert _skipped_count() == before
