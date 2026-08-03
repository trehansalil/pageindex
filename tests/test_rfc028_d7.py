"""Tests for RFC-028 Task 2.3 / 3.3 (D7): standalone Roman-numeral sub-clause
ordinal markers ("I. ", "II. ", "III. " ...) in `_OVERSIZED_ORDINAL_RE`, the
minimum-2-matches guard against incidental prose ("I. went to the store"),
and a deep fixture reproducing Haftpflicht-Besondere-Bedingungen's 27
Roman-numeral sub-clauses (depth 2 -> 3+).

Validates Design Property 7 (design-rfc028-run11-arabic-recovery-and-timeout-wiring.md).
"""

from pageindex_mcp.helpers import (
    _OVERSIZED_ORDINAL_RE,
    _ordinal_value,
    split_oversized_leaf_nodes,
)

_WORDS = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
    "mike november oscar papa quebec romeo sierra tango uniform victor whiskey "
).split()


def _text_of_length(n: int) -> str:
    if n <= 0:
        return ""
    words = []
    total = 0
    i = 0
    while total < n:
        w = _WORDS[i % len(_WORDS)]
        words.append(w)
        total += len(w) + 1
        i += 1
    return (" ".join(words) + " ")[:n]


def _roman(n: int) -> str:
    vals = [
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ]
    out = []
    for v, sym in vals:
        while n >= v:
            out.append(sym)
            n -= v
    return "".join(out)


class TestRomanNumeralMatching:
    def test_i_ii_iii_all_match(self):
        text = "I. went there.\nII. did that.\nIII. said so.\n"
        matches = list(_OVERSIZED_ORDINAL_RE.finditer(text))
        romans = [m.group("roman") for m in matches if m.group("roman") is not None]
        assert romans == ["I", "II", "III"]

    def test_roman_marker_ordinal_value(self):
        m1 = _OVERSIZED_ORDINAL_RE.search("I. ")
        m2 = _OVERSIZED_ORDINAL_RE.search("II. ")
        m3 = _OVERSIZED_ORDINAL_RE.search("III. ")
        assert _ordinal_value(m1) == (1,)
        assert _ordinal_value(m2) == (2,)
        assert _ordinal_value(m3) == (3,)

    def test_roman_up_to_xxvii(self):
        """Haftpflicht-Besondere-Bedingungen uses Roman numerals I through
        XXVII as its 27-clause structure."""
        m = _OVERSIZED_ORDINAL_RE.search("XXVII. ")
        assert m is not None
        assert m.group("roman") == "XXVII"
        assert _ordinal_value(m) == (27,)

    def test_lowercase_roman_matches_via_ignorecase_flag(self):
        # regex is IGNORECASE, so lowercase "i. " also matches the [IVX]+ class.
        m = _OVERSIZED_ORDINAL_RE.search("i. ")
        assert m is not None
        assert m.group("roman").upper() == "I"

    def test_bare_roman_without_trailing_space_does_not_match(self):
        # marker requires "\.\s" after the numeral -- no split on "I.next".
        assert _OVERSIZED_ORDINAL_RE.search("I.next") is None


class TestMinimumTwoMatchesGuard:
    def test_single_incidental_roman_marker_is_dropped_no_split(self):
        """A single 'I. went to the store' occurrence is prose, not a
        heading sequence -- must not trigger a split."""
        text = (
            f"I. went to the store and {_text_of_length(3000)}\n\n"
            f"{_text_of_length(3000)}\n\n"
            f"{_text_of_length(3000)}"
        )
        tree = [{"node_id": "n1", "title": "root", "text": text, "nodes": []}]
        split_oversized_leaf_nodes(
            tree, max_chars=50000, min_segments=3, _tree_ratio=0.1, _tree_total=len(text) * 10
        )
        assert tree[0]["nodes"] == []

    def test_two_roman_markers_are_sufficient_to_split(self):
        """>=2 Roman-numeral matches in the same leaf clear the guard and
        feed the split decision."""
        text = f"I. {_text_of_length(3000)}\nII. {_text_of_length(3000)}"
        tree = [{"node_id": "n1", "title": "root", "text": text, "nodes": []}]
        split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=2)
        assert len(tree[0]["nodes"]) == 2
        assert tree[0]["nodes"][0]["text"].startswith("I.")
        assert tree[0]["nodes"][1]["text"].startswith("II.")

    def test_three_roman_markers_split_succeeds(self):
        text = (
            f"I. {_text_of_length(3000)}\nII. {_text_of_length(3000)}\nIII. {_text_of_length(3000)}"
        )
        tree = [{"node_id": "n1", "title": "root", "text": text, "nodes": []}]
        split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)
        assert len(tree[0]["nodes"]) == 3
        assert tree[0]["nodes"][0]["text"].startswith("I.")
        assert tree[0]["nodes"][1]["text"].startswith("II.")
        assert tree[0]["nodes"][2]["text"].startswith("III.")


class TestHaftpflichtDeepFixture:
    """Reproduces Haftpflicht-Besondere-Bedingungen's structure: a depth-2
    Article node whose oversized leaf text is subdivided into 27
    Roman-numeral sub-clauses (I through XXVII), each itself long enough to
    need no further splitting. Asserts the tree gains a third level (depth
    2 -> 3+) via the recursive `split_oversized_leaf_nodes` call."""

    def test_27_roman_subclauses_split_into_third_level(self):
        clause_text = _text_of_length(2000)
        body = "\n".join(f"{_roman(i)}. {clause_text}" for i in range(1, 28))
        article_node = {
            "node_id": "article-9",
            "title": "Article 9",
            "text": body,
            "nodes": [],
        }
        tree = [
            {
                "node_id": "root",
                "title": "Haftpflicht-Besondere-Bedingungen",
                "text": "",
                "nodes": [article_node],
            }
        ]
        split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)

        # depth 1 (root) -> depth 2 (article_node, unchanged position) ->
        # depth 3 (27 Roman sub-clause children).
        assert tree[0]["nodes"][0] is article_node
        assert len(article_node["nodes"]) == 27
        assert article_node["nodes"][0]["text"].startswith("I.")
        assert article_node["nodes"][26]["text"].startswith("XXVII.")
        for idx, child in enumerate(article_node["nodes"], start=1):
            assert child["text"].startswith(f"{_roman(idx)}.")

    def test_root_still_at_depth_one_no_regression(self):
        """Non-regression: the root node itself (with no oversized text)
        is left untouched -- only the oversized leaf gains children."""
        clause_text = _text_of_length(2000)
        body = "\n".join(f"{_roman(i)}. {clause_text}" for i in range(1, 28))
        article_node = {
            "node_id": "article-9",
            "title": "Article 9",
            "text": body,
            "nodes": [],
        }
        tree = [
            {
                "node_id": "root",
                "title": "Haftpflicht-Besondere-Bedingungen",
                "text": "",
                "nodes": [article_node],
            }
        ]
        split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)
        assert tree[0]["node_id"] == "root"
        assert tree[0]["text"] == ""
