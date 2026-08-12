"""RFC-015 D2 / D3A — verdict-engine correctness tests (HR5 tightening).

D2  (content-ordering check): _tree_is_reordered flags leaves whose source
    start_index (fallback line_num) regresses below the running max; a reordered
    tree must FAIL validate_tree and classify_verdict, never PASS.
D3A (leaf-only ratio denominator): _tree_max_leaf_ratio sums only LEAF chars, so
    over-nested wrapper titles can no longer deflate the ratio. Leaf-only is a
    strict tightening — the ratio can only rise vs the old all-node denominator.

NOTE: The two doc-specific regressions named in Task 1.5 — 54e92c0a
(Federal Decree-Law 47/2021) and a4c1b522 (Ministerial Resolution 279/2022) —
require the real stored trees from MinIO, which are NOT in the repo. Per this
repo's no-fabrication rule they are recorded as a [GAP] in
.agents/state/PENDING_DECISIONS.md rather than invented here. The tests below
cover the D2/D3A LOGIC with synthetic, clearly-labelled trees.
"""

from pageindex_mcp.helpers import (
    _tree_is_reordered,
    _tree_max_leaf_ratio,
    classify_verdict,
    validate_tree,
)


def _leaf(idx=None, title="", text="x", key="start_index"):
    node = {"title": title, "text": text}
    if idx is not None:
        node[key] = idx
    return node


# ── D2: _tree_is_reordered ──────────────────────────────────────────────────


def test_monotonic_start_index_not_reordered():
    """D2: strictly increasing start_index across leaves → not reordered."""
    tree = [_leaf(10), _leaf(20), _leaf(30)]
    assert _tree_is_reordered(tree) is False


def test_equal_start_index_not_reordered():
    """D2: non-decreasing (ties) is not a regression."""
    tree = [_leaf(10), _leaf(10), _leaf(20)]
    assert _tree_is_reordered(tree) is False


def test_regressing_start_index_is_reordered():
    """D2: [10, 20, 15] regresses below running max → reordered."""
    tree = [_leaf(10), _leaf(20), _leaf(15)]
    assert _tree_is_reordered(tree) is True


def test_line_num_fallback_path_regressing():
    """D2: with no start_index, a regressing line_num still trips the check."""
    tree = [_leaf(10, key="line_num"), _leaf(20, key="line_num"), _leaf(5, key="line_num")]
    assert _tree_is_reordered(tree) is True


def test_missing_indices_are_skipped():
    """D2: leaves with neither start_index nor line_num are ignored, not treated as 0."""
    tree = [_leaf(10), {"title": "", "text": "y"}, _leaf(20)]
    assert _tree_is_reordered(tree) is False


def test_reorder_detected_across_nesting():
    """D2: regression across leaves in different subtrees is still caught (doc order)."""
    tree = [
        {"title": "A", "text": "", "nodes": [_leaf(10), _leaf(40)]},
        {"title": "B", "text": "", "nodes": [_leaf(20)]},  # 20 < running max 40
    ]
    assert _tree_is_reordered(tree) is True


# ── D2: wiring into validate_tree + classify_verdict ────────────────────────


def _varied_text(seed):
    """A blob with many DISTINCT tokens so it never trips the garble gate
    (token-repetition / digit-ratio heuristics in _is_garbled_blob)."""
    return " ".join(f"word{seed}n{j}alpha" for j in range(60))


def _wellformed_tree(indices):
    """A tree with >=3 nodes and depth>=2 so only ordering decides the verdict."""
    return [
        {
            "title": "Chapter",
            "text": "",
            "nodes": [_leaf(i, text=_varied_text(i)) for i in indices],
        }
    ]


def test_validate_tree_rejects_reordered():
    """D2: validate_tree returns (False, 'reordered') for out-of-order content."""
    ok, reason = validate_tree(_wellformed_tree([10, 30, 20]))
    assert ok is False
    assert reason == "reordered"


def test_validate_tree_accepts_ordered():
    """D2: an otherwise-valid, in-order tree still passes validate_tree."""
    ok, reason = validate_tree(_wellformed_tree([10, 20, 30]))
    assert ok is True
    assert reason == ""


def test_classify_verdict_reordered_not_pass():
    """D2: a reordered tree with an otherwise-fine ratio yields FAIL, not PASS,
    with 'reordered' in the reason."""
    verdict, reason = classify_verdict(
        _wellformed_tree([10, 30, 20]), content_class="tree", validate_result=None
    )
    assert verdict != "PASS"
    assert verdict == "FAIL"
    assert "reordered" in reason


def test_classify_verdict_ordered_can_pass():
    """D2 control: same shape but in order is not forced to FAIL by the ordering gate."""
    verdict, _ = classify_verdict(
        _wellformed_tree([10, 20, 30]), content_class="tree", validate_result=None
    )
    assert verdict != "FAIL", "ordering gate must not force FAIL for in-order tree"
    # Specifically: the reordering gate does not fire.
    assert _tree_is_reordered(_wellformed_tree([10, 20, 30])) is False


# ── D3A: leaf-only denominator ──────────────────────────────────────────────


def _old_all_node_ratio(structure):
    """The PRE-D3A formula (all-node denominator), computed inline HERE ONLY for
    comparison. Deliberately NOT resurrected in helpers.py."""
    max_leaf = 0
    total = 0

    def _walk(nodes):
        nonlocal max_leaf, total
        for n in nodes:
            chars = len(n.get("title", "")) + len(n.get("text", ""))
            total += chars
            children = n.get("nodes") or []
            if not children:
                max_leaf = max(max_leaf, chars)
            else:
                _walk(children)

    _walk(structure)
    return max_leaf / total if total > 0 else 0.0


def test_leaf_only_denominator_strictly_greater_with_wrappers():
    """D3A: deep non-leaf wrappers with long titles + one dominant leaf → the new
    leaf-only ratio is strictly greater than the old all-node-denominator ratio."""
    dominant = _leaf(text="Z" * 1000)
    tree = [
        {
            "title": "WRAPPER TITLE " * 20,  # long non-leaf title inflates old denom
            "text": "",
            "nodes": [
                {
                    "title": "INNER WRAPPER TITLE " * 20,
                    "text": "",
                    "nodes": [dominant, _leaf(text="s" * 50)],
                }
            ],
        }
    ]
    _, _, new_ratio = _tree_max_leaf_ratio(tree)
    old_ratio = _old_all_node_ratio(tree)
    assert new_ratio > old_ratio


def test_leaf_only_denominator_equals_old_when_flat():
    """D3A: with no non-leaf wrappers, leaf-only and all-node denominators agree."""
    tree = [_leaf(text="a" * 100), _leaf(text="b" * 50), _leaf(text="c" * 25)]
    _, _, new_ratio = _tree_max_leaf_ratio(tree)
    assert abs(new_ratio - _old_all_node_ratio(tree)) < 1e-12


def test_leaf_ratio_never_decreases_vs_old():
    """D3A tightening invariant: leaf-only ratio >= old ratio for arbitrary trees,
    so no previously-failing (high-ratio) tree can newly PASS."""
    trees = [
        _wellformed_tree([1, 2, 3]),
        [{"title": "T " * 40, "text": "", "nodes": [_leaf(text="x" * 500)]}],
        [_leaf(text="only")],
    ]
    for tree in trees:
        _, _, new_ratio = _tree_max_leaf_ratio(tree)
        assert new_ratio >= _old_all_node_ratio(tree) - 1e-12


def test_empty_tree_ratio_zero():
    """D3A: no leaves → ratio 0.0, no ZeroDivision."""
    assert _tree_max_leaf_ratio([]) == (0, 0, 0.0)
