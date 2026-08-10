"""Tests RFC-035 D1 (Task 3.2-3.4): thread `inspector_class` through the
`classify_verdict` cat_c branch.

Design Property 2: for tree-path docs (`content_class` empty/falsy),
`inspector_class == "text_based"` widens the cat_c promotion threshold to
`CATEGORY_BC_PROMOTION_THRESHOLD * 1.2`; `content_class` remains the sole
cat_a/cat_b/cat_c branch selector (precedence unchanged), and omitting
`inspector_class` preserves the pre-D1 default cat_c threshold (backward
compat).

See `pageindex_mcp.helpers.classify_verdict`.
"""

import random

from pageindex_mcp.helpers import classify_verdict


def _flat_leaf_tree(chars_per_leaf: list[int]) -> list[dict]:
    """A flat (depth == 1) sibling tree with one leaf per entry in
    ``chars_per_leaf``, each leaf's text a repeated single-token char run (so
    it never trips the token-repetition garble check)."""
    return [
        {"node_id": str(i), "title": "", "text": "x" * n, "nodes": []}
        for i, n in enumerate(chars_per_leaf)
    ]


class TestInspectorClassThreading:
    def test_empty_content_class_text_based_inspector_promotes_cat_c(self):
        """content_class='', inspector_class='text_based': leaf_concentration
        0.20 exceeds the default cat_c threshold (0.17) but clears the
        widened 0.204 (0.17 * 1.2) threshold -- promote cat_c_promoted."""
        structure = _flat_leaf_tree([20, 20, 20, 20, 20])
        verdict, reason = classify_verdict(
            structure, "", None, inspector_class="text_based"
        )
        assert (verdict, reason) == ("PASS", "cat_c_promoted")

    def test_flat_mixed_content_class_takes_precedence_over_inspector_class(self):
        """content_class='flat_mixed' with inspector_class='text_based':
        content_class remains the sole branch selector, so this takes the
        flat_/cat_b branch (not cat_c) regardless of inspector_class."""
        structure = _flat_leaf_tree([60] * 10)
        verdict, reason = classify_verdict(
            structure, "flat_mixed", None, inspector_class="text_based"
        )
        assert (verdict, reason) == ("PASS", "cat_b_promoted")

    def test_empty_content_class_no_inspector_class_default_cat_c_behavior(self):
        """content_class='', inspector_class=None (omitted default):
        leaf_concentration ~0.143 clears the unwidened default 0.17 cat_c
        threshold -- pre-D1 behavior is unaffected (backward compat)."""
        structure = _flat_leaf_tree([20] * 7)
        verdict, reason = classify_verdict(structure, "", None)
        assert (verdict, reason) == ("PASS", "cat_c_promoted")

    def test_empty_content_class_no_inspector_class_boundary_not_promoted(self):
        """Negative boundary guard: content_class='', inspector_class=None at
        leaf_concentration 0.20 (above the default 0.17 threshold, below the
        widened 0.204) must NOT promote -- proves D1 *widens* the threshold
        conditionally rather than raising it unconditionally."""
        structure = _flat_leaf_tree([20] * 5)
        verdict, reason = classify_verdict(structure, "", None)
        assert (verdict, reason) == ("MARGINAL", "depth=1")


class TestInspectorClassPrecedenceProperty:
    """Design Testing Strategy, Property-Based Tests (D1): random
    (content_class, inspector_class) pairs -- content_class always takes
    routing precedence; inspector_class only influences the cat_c branch.
    """

    _CONTENT_CLASSES = ["ocr_scanned", "ocr_image", "flat_prose", "flat_mixed"]
    _INSPECTOR_CLASSES = [None, "", "text_based", "scanned", "image_based", "zzz_bogus"]

    def test_random_pairs_content_class_precedence(self):
        """For 200 random pairs, on the cat_c-boundary tree (leaf_concentration
        0.20, between 0.17 and 0.204):

        - non-empty content_class: verdict is invariant to inspector_class
          (identical to the inspector_class=None result -- precedence holds);
        - empty content_class: cat_c promotion fires iff
          inspector_class == 'text_based'.
        """
        rng = random.Random(0xD1)
        structure = _flat_leaf_tree([20] * 5)
        for _ in range(200):
            content_class = rng.choice(["", *self._CONTENT_CLASSES])
            inspector_class = rng.choice(self._INSPECTOR_CLASSES)
            result = classify_verdict(
                structure, content_class, None, inspector_class=inspector_class
            )
            if content_class:
                baseline = classify_verdict(structure, content_class, None)
                assert result == baseline, (
                    f"inspector_class={inspector_class!r} changed the verdict "
                    f"for content_class={content_class!r}: {result} != {baseline}"
                )
                assert result[1] != "cat_c_promoted"
            elif inspector_class == "text_based":
                assert result == ("PASS", "cat_c_promoted"), (content_class, inspector_class, result)
            else:
                assert result == ("MARGINAL", "depth=1"), (content_class, inspector_class, result)
