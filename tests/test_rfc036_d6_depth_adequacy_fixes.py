"""RFC-036 D6: property validation for the depth-adequacy formula in
classify_verdict.

expected_min_depth = min(5, 2 + floor(log2(node_count / 50))). A tree that
clears the existing node_count/depth/max_leaf_ratio PASS gate but falls
short of expected_min_depth is capped at MARGINAL with reason
'depth_inadequate', carrying expected_min_depth/actual_depth. This file
covers the required test matrix plus the 100/200/400 node boundary
thresholds where expected_min_depth steps from 2->3, 3->4, 4->5.
"""

from pageindex_mcp.helpers import classify_verdict


_WORDS = (
    "the quick brown fox jumps over lazy dog while article clause section "
    "provides that obligation shall apply notwithstanding any other term"
).split()


def _leaf_text(i: int) -> str:
    return " ".join(_WORDS[j % len(_WORDS)] + str(i) for j in range(20))


def _make_tree(node_count: int, depth: int) -> list:
    """Build a chain of `depth` levels ending in enough equal-sized leaves
    to total `node_count` nodes, so max_leaf_ratio stays low and only the
    depth-adequacy gate is under test."""
    leaves_needed = node_count - (depth - 1)
    current = [
        {"title": "", "text": _leaf_text(i), "nodes": []} for i in range(leaves_needed)
    ]
    for _ in range(depth - 1):
        current = [{"title": "", "text": _leaf_text(0), "nodes": current}]
    return current


def test_50_node_depth2_pass_baseline():
    tree = _make_tree(50, 2)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "PASS"
    assert reason == ""


def test_200_node_depth2_marginal_depth_inadequate():
    tree = _make_tree(200, 2)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "MARGINAL"
    assert reason == "depth_inadequate:expected_min_depth=4,actual_depth=2"


def test_200_node_depth4_pass():
    tree = _make_tree(200, 4)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "PASS"


def test_600_node_depth2_marginal():
    tree = _make_tree(600, 2)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "MARGINAL"
    assert reason == "depth_inadequate:expected_min_depth=5,actual_depth=2"


def test_600_node_depth5_pass():
    tree = _make_tree(600, 5)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "PASS"


def test_boundary_99_nodes_expected_depth_2():
    # Just below the 100-node threshold: expected_min_depth stays at 2.
    tree = _make_tree(99, 2)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "PASS"
    assert reason == ""


def test_boundary_100_nodes_expected_depth_3():
    # At the 100-node threshold: expected_min_depth steps up to 3.
    tree = _make_tree(100, 2)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "MARGINAL"
    assert reason == "depth_inadequate:expected_min_depth=3,actual_depth=2"

    tree = _make_tree(100, 3)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "PASS"


def test_boundary_199_nodes_expected_depth_3():
    # Just below the 200-node threshold: expected_min_depth still 3.
    tree = _make_tree(199, 3)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "PASS"


def test_boundary_200_nodes_expected_depth_4():
    # At the 200-node threshold: expected_min_depth steps up to 4.
    tree = _make_tree(200, 3)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "MARGINAL"
    assert reason == "depth_inadequate:expected_min_depth=4,actual_depth=3"

    tree = _make_tree(200, 4)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "PASS"


def test_boundary_399_nodes_expected_depth_4():
    # Just below the 400-node threshold: expected_min_depth still 4.
    tree = _make_tree(399, 4)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "PASS"


def test_boundary_400_nodes_expected_depth_5():
    # At the 400-node threshold: expected_min_depth steps up to 5 (the cap).
    tree = _make_tree(400, 4)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "MARGINAL"
    assert reason == "depth_inadequate:expected_min_depth=5,actual_depth=4"

    tree = _make_tree(400, 5)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "PASS"


def test_depth_cap_never_exceeds_5():
    # Even a very large tree caps expected_min_depth at 5, not higher.
    tree = _make_tree(5000, 5)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "PASS"
