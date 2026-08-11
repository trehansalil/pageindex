"""RFC-036 D6: complexity-proportional depth-adequacy scoring in classify_verdict.

expected_min_depth = min(5, 2 + floor(log2(node_count / 50))). A tree that
clears the existing node_count/depth/max_leaf_ratio PASS gate but falls
short of expected_min_depth is capped at MARGINAL with reason
'depth_inadequate', carrying expected_min_depth/actual_depth in the reason.
"""

from pageindex_mcp.helpers import classify_verdict


_WORDS = (
    "the quick brown fox jumps over lazy dog while article clause section "
    "provides that obligation shall apply notwithstanding any other term"
).split()


def _leaf_text(i: int) -> str:
    return " ".join(_WORDS[j % len(_WORDS)] + str(i) for j in range(20))


def _make_tree(node_count: int, depth: int) -> list:
    """Build a chain of `depth` levels ending in enough leaves to total
    `node_count` nodes, all leaves equal-sized so max_leaf_ratio stays low."""
    leaves_needed = node_count - (depth - 1)
    current = [
        {"title": "", "text": _leaf_text(i), "nodes": []} for i in range(leaves_needed)
    ]
    for _ in range(depth - 1):
        current = [{"title": "", "text": _leaf_text(0), "nodes": current}]
    return current


def test_50_node_depth2_pass_baseline_unchanged():
    tree = _make_tree(50, 2)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "PASS"
    assert reason == ""


def test_200_node_depth2_margin_depth_inadequate():
    tree = _make_tree(200, 2)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "MARGINAL"
    assert reason.startswith("depth_inadequate")
    assert "expected_min_depth=4" in reason
    assert "actual_depth=2" in reason


def test_200_node_depth4_pass():
    tree = _make_tree(200, 4)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "PASS"


def test_600_node_depth2_margin():
    tree = _make_tree(600, 2)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "MARGINAL"
    assert reason.startswith("depth_inadequate")


def test_600_node_depth5_pass():
    tree = _make_tree(600, 5)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "PASS"
