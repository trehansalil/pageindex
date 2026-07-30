"""Tests for RFC-024 Tasks 2.2/2.3 (D3): extend the oversized-leaf ordinal
splitter with Clause/Part/Annex/بند/باب MOU/decree markers, and add a
leaf_concentration-aware paragraph-boundary (blank-line) splitting fallback
gated on the SAME ``PASS_MAX_LEAF_RATIO`` env var as D0.

Validates Design Property 4 (design-rfc024-run7-verdict-stability-and-recovery-gaps.md).
"""

from pageindex_mcp.helpers import (
    _OVERSIZED_ORDINAL_RE,
    _has_heading_markers,
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


def test_clause_markers_detected_by_has_heading_markers():
    """(a) 'Clause 1 ... Clause 2 ... Clause 3' -> _has_heading_markers True,
    so the leaf is split-eligible even when under max_chars."""
    text = "Clause 1 says X. Clause 2 says Y. Clause 3 says Z."
    assert _has_heading_markers(text) is True


def test_clause_markers_split_fires():
    """(a, continued) a Clause-marked leaf under max_chars actually splits --
    'splitting fires' per the RFC D3 test-strategy row, not just detection."""
    text = (
        f"Clause 1 {_text_of_length(3000)}\n"
        f"Clause 2 {_text_of_length(3000)}\n"
        f"Clause 3 {_text_of_length(3000)}"
    )
    tree = [{"node_id": "n1", "title": "root", "text": text, "nodes": []}]
    split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)
    assert len(tree[0]["nodes"]) == 3
    assert tree[0]["nodes"][0]["text"].startswith("Clause 1")


def test_band_arabic_markers_split_succeeds():
    """(b) 'بند ١ ... بند ٢ ... بند ٣' (>=3 markers, strictly increasing) ->
    the ordinal splitter forms an increasing run and splits successfully."""
    text = (
        f"بند ١ {_text_of_length(3000)}\n"
        f"بند ٢ {_text_of_length(3000)}\n"
        f"بند ٣ {_text_of_length(3000)}"
    )
    tree = [{"node_id": "n1", "title": "root", "text": text, "nodes": []}]
    split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)
    assert len(tree[0]["nodes"]) == 3
    assert tree[0]["nodes"][0]["text"].startswith("بند ١")
    assert tree[0]["nodes"][1]["text"].startswith("بند ٢")
    assert tree[0]["nodes"][2]["text"].startswith("بند ٣")


class TestExistingPatternsUnchanged:
    """(c) Article/Section/مادة patterns still match and produce no regression."""

    def test_article_still_matches(self):
        assert _OVERSIZED_ORDINAL_RE.search("Article 9") is not None

    def test_section_still_matches(self):
        assert _OVERSIZED_ORDINAL_RE.search("Section 4") is not None

    def test_mada_still_matches(self):
        assert _OVERSIZED_ORDINAL_RE.search("المادة ٥") is not None

    def test_article_split_still_succeeds(self):
        text = (
            f"Article 1 {_text_of_length(3000)}\n"
            f"Article 2 {_text_of_length(3000)}\n"
            f"Article 3 {_text_of_length(3000)}"
        )
        tree = [{"node_id": "n1", "title": "root", "text": text, "nodes": []}]
        split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)
        assert len(tree[0]["nodes"]) == 3


def test_blank_line_paragraph_fallback_fires_above_pass_max_leaf_ratio(monkeypatch):
    """(d) A marker-less leaf whose blank-line-separated paragraphs carry no
    ordinal/فقرة sequence still splits on blank-line boundaries when the
    tree's max_leaf_ratio exceeds PASS_MAX_LEAF_RATIO (the D0 threshold,
    reused unmodified per the RFC's D0-vs-D3 consistency requirement)."""
    monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.30")
    para = _text_of_length(2500)
    text = f"{para}\n\n{para}\n\n{para}"
    # Single leaf holds 100% of the tree's leaf chars -> max_leaf_ratio=1.0,
    # well above the 0.30 threshold.
    tree = [{"node_id": "n1", "title": "root", "text": text, "nodes": []}]
    split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)
    assert len(tree[0]["nodes"]) == 3


def test_blank_line_fallback_does_not_fire_below_pass_max_leaf_ratio(monkeypatch):
    """Sibling of (d): the same marker-less blank-line text is left UNSPLIT
    when the tree-level max_leaf_ratio is below PASS_MAX_LEAF_RATIO."""
    monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.30")
    para = _text_of_length(2500)
    text = f"{para}\n\n{para}\n\n{para}"
    tree = [{"node_id": "n1", "title": "root", "text": text, "nodes": []}]
    split_oversized_leaf_nodes(
        tree, max_chars=50000, min_segments=3, _tree_ratio=0.1, _tree_total=len(text) * 10
    )
    assert tree[0]["nodes"] == []


def test_part_roman_numerals_ordinal_value():
    """(e) 'Part IV ... Part V ... Part VI' -> _ordinal_value returns correct
    int tuples via _roman_to_int."""
    m4 = _OVERSIZED_ORDINAL_RE.search("Part IV")
    m5 = _OVERSIZED_ORDINAL_RE.search("Part V")
    m6 = _OVERSIZED_ORDINAL_RE.search("Part VI")
    assert _ordinal_value(m4) == (4,)
    assert _ordinal_value(m5) == (5,)
    assert _ordinal_value(m6) == (6,)


def test_annex_letters_ordinal_value():
    """(f) 'Annex A ... Annex B ... Annex C' -> _ordinal_value returns correct
    int tuples via ord() conversion."""
    ma = _OVERSIZED_ORDINAL_RE.search("Annex A")
    mb = _OVERSIZED_ORDINAL_RE.search("Annex B")
    mc = _OVERSIZED_ORDINAL_RE.search("Annex C")
    assert _ordinal_value(ma) == (1,)
    assert _ordinal_value(mb) == (2,)
    assert _ordinal_value(mc) == (3,)


def test_part_prose_false_positive_regression_guard():
    """(g) 'Part 2 of the agreement' repeated (non-sequential, same ordinal
    each time) is English prose making a cross-reference, not a heading
    sequence -> must NOT produce a spurious split."""
    text = (
        f"As mentioned in Part 2 of the agreement, {_text_of_length(2500)}\n\n"
        f"Part 2 of the agreement also states {_text_of_length(2500)}\n\n"
        f"Referring again to Part 2 of the agreement, {_text_of_length(2500)}"
    )
    tree = [{"node_id": "n1", "title": "root", "text": text, "nodes": []}]
    split_oversized_leaf_nodes(
        tree, max_chars=50000, min_segments=3, _tree_ratio=0.1, _tree_total=len(text) * 10
    )
    assert tree[0]["nodes"] == []
