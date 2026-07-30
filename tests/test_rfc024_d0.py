"""Tests for RFC-024 Task 2.1 (D0): widen ``PASS_MAX_LEAF_RATIO`` default from
0.20 to 0.30 in ``classify_verdict``'s main PASS gate.

Validates Design Property 1 (design-rfc024-run7-verdict-stability-and-recovery-gaps.md):
documents whose `max_leaf_ratio` sits between the old default (0.20) and the
widened default (0.30) are now correctly promoted to PASS, while documents
above the widened default still fall to MARGINAL.
"""

from pageindex_mcp.helpers import classify_verdict

_WORDS = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
    "mike november oscar papa quebec romeo sierra tango uniform victor whiskey "
    "xray yankee zulu apple banana cherry date fig grape"
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


def _tree_with_ratio(ratio: float, total_chars: int = 10000, n_other: int = 6) -> list:
    """Root node with one dominant leaf (`ratio` share of leaf chars) and
    `n_other` smaller leaves, so node_count and depth clear their gates
    (node_count=1+n_other+1 >= 3, depth=2) and only max_leaf_ratio varies."""
    max_leaf = round(ratio * total_chars)
    other_leaf = (total_chars - max_leaf) // n_other
    leaves = [{"title": "", "text": _text_of_length(max_leaf), "nodes": []}]
    leaves += [
        {"title": "", "text": _text_of_length(other_leaf), "nodes": []} for _ in range(n_other)
    ]
    return [{"title": "Root", "text": "", "nodes": leaves}]


class TestPassMaxLeafRatioWidenedDefault:
    def test_ratio_below_widened_default_passes(self, monkeypatch):
        """max_leaf_ratio=0.25 with default (unset) PASS_MAX_LEAF_RATIO=0.30 -> PASS.

        0.25 sits above the OLD default (0.20) but below the WIDENED default
        (0.30), so this is the exact regression case D0 fixes."""
        monkeypatch.delenv("PASS_MAX_LEAF_RATIO", raising=False)
        structure = _tree_with_ratio(0.25)
        assert classify_verdict(structure, "hierarchical", None) == ("PASS", "")

    def test_ratio_above_widened_default_stays_marginal(self, monkeypatch):
        """max_leaf_ratio=0.35 with default (unset) PASS_MAX_LEAF_RATIO=0.30 -> MARGINAL."""
        monkeypatch.delenv("PASS_MAX_LEAF_RATIO", raising=False)
        structure = _tree_with_ratio(0.35)
        verdict, reason = classify_verdict(structure, "hierarchical", None)
        assert verdict == "MARGINAL"
        assert reason == "leaf_concentration=0.35"

    def test_low_ratio_passes_regardless_of_threshold(self, monkeypatch):
        """max_leaf_ratio=0.19 -> PASS both at the default (unset) widened
        threshold and at the narrower pre-D0 threshold, since 0.19 clears either."""
        structure = _tree_with_ratio(0.19)
        monkeypatch.delenv("PASS_MAX_LEAF_RATIO", raising=False)
        assert classify_verdict(structure, "hierarchical", None) == ("PASS", "")
        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.20")
        assert classify_verdict(structure, "hierarchical", None) == ("PASS", "")
