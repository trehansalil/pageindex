"""Tests for RFC-023 Task 5.2 (D10): ``PASS_MAX_LEAF_RATIO`` env-var-tunable
threshold in ``classify_verdict``'s main PASS gate.

Validates Design Property 10 (design-rfc023-run6-content-recovery-and-verdict-hardening.md):
the leaf-concentration threshold for the main PASS gate reads from
``PASS_MAX_LEAF_RATIO`` (default 0.20) rather than a hardcoded value, so
borderline documents with `max_leaf_ratio` just above the old default but
below the widened one are correctly promoted to PASS, while documents above
the widened threshold still fall to MARGINAL.
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


class TestPassMaxLeafRatioEnvVar:
    def test_ratio_below_widened_threshold_passes(self, monkeypatch):
        """max_leaf_ratio=0.18 with PASS_MAX_LEAF_RATIO=0.20 -> PASS."""
        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.20")
        structure = _tree_with_ratio(0.18)
        assert classify_verdict(structure, "hierarchical", None) == ("PASS", "")

    def test_ratio_above_widened_threshold_stays_marginal(self, monkeypatch):
        """max_leaf_ratio=0.22 with PASS_MAX_LEAF_RATIO=0.20 -> MARGINAL."""
        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.20")
        from pageindex_mcp.config import reset_pipeline_config
        reset_pipeline_config()
        structure = _tree_with_ratio(0.22)
        verdict, reason = classify_verdict(structure, "hierarchical", None)
        assert verdict == "MARGINAL"
        assert reason == "leaf_concentration=0.22"

    def test_low_ratio_passes_regardless_of_threshold(self, monkeypatch):
        """max_leaf_ratio=0.16 -> PASS both at the default (unset) threshold
        and at a narrower explicit threshold, since 0.16 clears either."""
        structure = _tree_with_ratio(0.16)
        monkeypatch.delenv("PASS_MAX_LEAF_RATIO", raising=False)
        assert classify_verdict(structure, "hierarchical", None) == ("PASS", "")
        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.17")
        assert classify_verdict(structure, "hierarchical", None) == ("PASS", "")
