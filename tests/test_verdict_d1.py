"""RFC-014 D1: unit tests for verdict-computation helpers in helpers.py.

Covers Property 1 (Verdict Determinism) and Property 2 (HR5 Independence)
from the RFC-014 design doc, plus direct sub-metric tests for
`ocr_noise_ratio` and `hash_pipe_ratio`.
"""

import inspect

import pytest

from pageindex_mcp.helpers import (
    _tree_max_leaf_ratio,
    classify_verdict,
    hash_pipe_ratio,
    ocr_noise_ratio,
    validate_tree,
)


# ── synthetic tree builder ───────────────────────────────────────────────────


def _make_tree(leaf_sizes: list[int], depth: int = 2) -> list:
    """Build a synthetic tree with given leaf char sizes at the specified depth.

    depth=1 -> all leaves are top-level nodes (flat).
    depth>=2 -> leaves are nested `depth` levels under synthetic parent nodes,
    one leaf per parent chain (keeps char totals exactly equal to leaf_sizes).
    """
    trees = []
    for i, size in enumerate(leaf_sizes):
        leaf = {"title": "", "text": "x" * size, "nodes": []}
        node = leaf
        # Wrap the leaf in (depth - 1) empty-text parent nodes so overall
        # nesting depth equals `depth`, without adding extra chars.
        for _ in range(depth - 1):
            node = {"title": "", "text": "", "nodes": [node]}
        trees.append(node)
    return trees


# ── Property 1: Verdict Determinism — _tree_max_leaf_ratio ─────────────────


@pytest.mark.parametrize(
    "leaf_sizes,expected_ratio",
    [
        # 5% concentration: 1 leaf of 50, 19 leaves of 50 each -> 50/1000 = 0.05
        ([50] + [50] * 19, 0.05),
        # 16% concentration: 1 leaf of 160, rest sums to 840 evenly
        ([160] + [10] * 84, 0.16),
        # 76% concentration: 1 giant leaf of 760, rest sums to 240
        ([760] + [10] * 24, 0.76),
    ],
)
def test_tree_max_leaf_ratio_concentration_levels(leaf_sizes, expected_ratio):
    tree = _make_tree(leaf_sizes, depth=2)
    max_leaf, total, ratio = _tree_max_leaf_ratio(tree)
    assert max_leaf == max(leaf_sizes)
    assert total == sum(leaf_sizes)
    assert ratio == pytest.approx(expected_ratio, abs=0.01)


def test_tree_max_leaf_ratio_empty_tree():
    assert _tree_max_leaf_ratio([]) == (0, 0, 0.0)


def test_tree_max_leaf_ratio_uses_title_and_text_chars():
    tree = [{"title": "abc", "text": "de", "nodes": []}]
    max_leaf, total, ratio = _tree_max_leaf_ratio(tree)
    assert max_leaf == 5
    assert total == 5
    assert ratio == 1.0


# ── sub-metric tests: ocr_noise_ratio ───────────────────────────────────────


def test_ocr_noise_ratio_empty_string():
    assert ocr_noise_ratio("") == 0.0


def test_ocr_noise_ratio_clean_text():
    assert ocr_noise_ratio("clean text") == 0.0


def test_ocr_noise_ratio_replacement_char():
    assert ocr_noise_ratio("ab�c") == pytest.approx(0.25)


# ── sub-metric tests: hash_pipe_ratio ───────────────────────────────────────


def test_hash_pipe_ratio_empty_string():
    assert hash_pipe_ratio("") == 0.0


def test_hash_pipe_ratio_no_matches():
    assert hash_pipe_ratio("abc") == 0.0


def test_hash_pipe_ratio_mixed():
    assert hash_pipe_ratio("a#b|c") == pytest.approx(0.4)


# ── Property 1: Verdict Determinism — classify_verdict category matrix ─────


def _balanced_pass_tree() -> list:
    """node_count>=3, depth>=2, max_leaf_ratio<0.15, clean text.

    10 equal-sized leaves -> max_leaf/total = 1/10 = 0.10 (<0.15). Titles are
    left empty so only `text` lengths determine char totals, keeping the
    ratio math exact.
    """
    return [
        {
            "title": "",
            "text": "",
            "nodes": [{"title": "", "text": "x" * 100, "nodes": []} for _ in range(10)],
        }
    ]


def test_classify_verdict_fail_on_garbling():
    tree = _balanced_pass_tree()
    verdict, reason = classify_verdict(tree, "default", "garbling")
    assert (verdict, reason) == ("FAIL", "garbling")


def test_classify_verdict_fail_on_max_leaf_ratio_over_threshold():
    # One huge leaf dominates: ratio > 0.75
    tree = _make_tree([760] + [10] * 24, depth=2)
    verdict, reason = classify_verdict(tree, "default", None)
    assert verdict == "FAIL"
    assert reason.startswith("max_leaf_ratio=")


def test_classify_verdict_pass_on_good_metrics():
    tree = _balanced_pass_tree()
    verdict, reason = classify_verdict(tree, "default", None)
    assert (verdict, reason) == ("PASS", "")


def _borderline_ratio_tree() -> list:
    """5 equal children under a Root -> max_leaf/total = 20/100 = 0.20,
    node_count=6 (>=3), depth=2 (>=2). Titles are left empty so only `text`
    lengths determine char totals, keeping the ratio math exact."""
    return [
        {
            "title": "",
            "text": "",
            "nodes": [{"title": "", "text": "x" * 20, "nodes": []} for _ in range(5)],
        }
    ]


def test_classify_verdict_marginal_on_borderline_ratio():
    # ratio = 0.20, node_count>=3 and depth>=2 so it's not FAIL-blocked,
    # and default category promotion requires ratio<0.15, so it stays MARGINAL.
    tree = _borderline_ratio_tree()
    verdict, reason = classify_verdict(tree, "default", None)
    assert verdict == "MARGINAL"
    assert reason == "leaf_concentration=0.20"


def test_classify_verdict_category_a_promoted():
    tree = _balanced_pass_tree()
    # Force MARGINAL base by pushing ratio just under promotion window but
    # tree already passes global PASS rule at ratio<0.15 -- to exercise the
    # cat_a path specifically we need base verdict MARGINAL i.e. NOT all of
    # node_count/depth/ratio/garbled satisfied simultaneously. Use depth=1
    # (flat) with clean-ish small ratio and ocr content class.
    tree = _make_tree([10] * 20, depth=1)  # flat -> depth=1, ratio=0.05
    verdict, reason = classify_verdict(tree, "ocr_rescued", None)
    assert (verdict, reason) == ("PASS", "cat_a_promoted")


def test_classify_verdict_category_b_promoted():
    # RFC-023 D4 added a MIN_FLAT_PROMOTION_CHARS=500 content-quality guard
    # to cat_b promotion, so leaf text is sized to clear it (10 chars/leaf
    # -> 200 total would fall short and land small_doc_promoted instead).
    tree = _make_tree([30] * 20, depth=1)  # flat -> depth=1, node_count>=3
    verdict, reason = classify_verdict(tree, "flat_prose", None)
    assert (verdict, reason) == ("PASS", "cat_b_promoted")


def test_classify_verdict_category_c_promoted():
    tree = _make_tree([10] * 20, depth=1)  # flat, clean text, low hash/pipe
    verdict, reason = classify_verdict(tree, "default", None)
    assert (verdict, reason) == ("PASS", "cat_c_promoted")


def test_classify_verdict_category_a_not_promoted_when_ratio_high():
    # ratio = 0.20 (borderline) with ocr_ content class -> promotion gate
    # requires ratio<0.15, so it stays MARGINAL.
    tree = _borderline_ratio_tree()
    verdict, reason = classify_verdict(tree, "ocr_rescued", None)
    assert verdict == "MARGINAL"
    assert reason == "leaf_concentration=0.20"


def test_classify_verdict_marginal_node_count_under_3():
    tree = [
        {"title": "A", "text": "x" * 50, "nodes": []},
        {"title": "B", "text": "x" * 50, "nodes": []},
    ]
    verdict, reason = classify_verdict(tree, "unrecognized_class", None)
    assert (verdict, reason) == ("MARGINAL", "node_count=2")


def test_classify_verdict_marginal_depth_under_2():
    # 5 top-level (flat) nodes -> node_count=5 (>=3), depth=1 (<2).
    # ratio is low (0.2 each) so it won't hit the >0.75 FAIL rule, but the
    # global PASS rule requires depth>=2, so falls through to MARGINAL.
    # Use an "unrecognized" content_class so no category promotion applies,
    # and make ratio high enough (>=0.15) that cat_c doesn't promote it.
    tree = _make_tree([30, 30, 30, 30, 200], depth=1)
    verdict, reason = classify_verdict(tree, "unrecognized_class", None)
    assert verdict == "MARGINAL"
    assert reason == "depth=1"


# ── Property 2: HR5 Independence ────────────────────────────────────────────


def test_classify_verdict_never_calls_validate_tree():
    """classify_verdict is a pure function: it receives validate_reason as an
    input parameter rather than calling validate_tree() itself."""
    source = inspect.getsource(classify_verdict)
    assert "validate_tree(" not in source


def test_validate_tree_source_unchanged_behavior():
    """Sanity-check that validate_tree's documented behavior (node_count<3,
    depth<2, garbling — in that priority order) still holds, i.e. D1 did not
    alter the HR5 gate itself."""
    # node_count < 3
    ok, reason = validate_tree([{"title": "A", "text": "x", "nodes": []}])
    assert (ok, reason) == (False, "node_count<3")

    # depth < 2 (3 flat nodes, no nesting)
    flat = [
        {"title": "A", "text": "x", "nodes": []},
        {"title": "B", "text": "x", "nodes": []},
        {"title": "C", "text": "x", "nodes": []},
    ]
    ok, reason = validate_tree(flat)
    assert (ok, reason) == (False, "depth<2")

    # garbling (null byte), with node_count>=3 and depth>=2 satisfied
    garbled = [
        {
            "title": "Root",
            "text": "",
            "nodes": [
                {"title": "A", "text": "\x00bad", "nodes": []},
                {"title": "B", "text": "ok", "nodes": []},
                {"title": "C", "text": "ok", "nodes": []},
            ],
        }
    ]
    ok, reason = validate_tree(garbled)
    assert (ok, reason) == (False, "garbling")

    # clean tree passes
    ok, reason = validate_tree(_balanced_pass_tree())
    assert (ok, reason) == (True, "")
