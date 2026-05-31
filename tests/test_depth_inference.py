# tests/test_depth_inference.py
"""Unit tests for the ported numbering-prefix DEPTH-INFERENCE helpers in
``pageindex_mcp.converters`` (the docling heading-depth recovery, no hardcoded
per-scheme regex table).

These are FAST pure-Python tests: no Docling, no PDF, no LLM. They pin the
behaviour validated offline in ``bench_layer2.py`` / ``bench_repromote.py``:

  _segment_label        leading numbering label -> atomic components (or [])
  _split_alnum          split an alnum token at letter<->digit and (..) groups
  _collapse_spaced      glue Docling's letter-spaced 'T e i l   A' -> 'Teil A'
  _containment_depths   depth = 1 + len(longest present proper-prefix label)
  _relevel_by_containment   re-level '#' headings from containment depth
  numeric-extension gate    the re-promotion anchor rule (a numeric extension of
                            a kept label promotes; a non-numeric tail / a bare
                            list marker does not)
"""

import pytest

from pageindex_mcp.converters import (
    _segment_label,
    _split_alnum,
    _collapse_spaced,
    _containment_depths,
    _relevel_by_containment,
)


# ── _segment_label — leading numbering label -> atomic components ─────────────
@pytest.mark.parametrize(
    "title,expected",
    [
        ("A.1.1", ["A", "1", "1"]),          # dot notation (AKB)
        ("A1-6.1", ["A", "1", "6", "1"]),    # hyphen clause (PHV), with sub-dot
        ("A(GB)-1", ["A", "GB", "1"]),       # parenthesised group component
        ("Abschnitt A1", ["A", "1"]),        # structural word consumed, label nests
        ("Teil A", ["A"]),                   # structural word -> bare section label
        ("Versicherte Personen", []),        # bare prose title -> no label
    ],
)
def test_segment_label_components(title, expected):
    """Each German-clause numbering style segments into the validated component
    list; a bare prose title yields the empty label []."""
    assert _segment_label(title) == expected


def test_segment_label_letter_spaced_collapses_to_section_label():
    """A Docling letter-spaced 'T e i l   A' heading (after _collapse_spaced glues
    the words) segments to the same ['A'] as the un-spaced 'Teil A'. The segmenter
    runs _collapse_spaced internally, so passing the spaced form directly also
    yields ['A']."""
    assert _collapse_spaced("T e i l   A") == "Teil A"
    assert _segment_label(_collapse_spaced("T e i l   A")) == ["A"]
    assert _segment_label("T e i l   A") == ["A"]


def test_segment_label_overlong_prose_word_is_not_a_label():
    """An over-long single prose word (no separators, >14 chars) is rejected as a
    label so a sentence fragment never becomes a clause code."""
    assert _segment_label("Donaudampfschifffahrtsgesellschaft") == []


def test_segment_label_non_numeric_tail_keeps_letter_component():
    """A trailing letter component (e.g. 'A.1.x') is preserved as a component, NOT
    silently dropped — this is what lets the numeric-extension gate later reject it
    (its tail 'x' is non-numeric). Mis-segmented prose 'Fuehren' (no separator,
    first component multi-letter) yields no label."""
    assert _segment_label("A.1.x") == ["A", "1", "x"]
    assert _segment_label("Fuehren") == []


# ── _split_alnum — split a token at letter<->digit and (..) boundaries ────────
@pytest.mark.parametrize(
    "tok,expected",
    [
        ("A1", ["A", "1"]),
        ("A", ["A"]),
        ("B4", ["B", "4"]),
        ("A(GB)", ["A", "GB"]),
        ("A(GB)1", ["A", "GB", "1"]),
    ],
)
def test_split_alnum(tok, expected):
    """_split_alnum splits at every letter<->digit boundary and pulls a (..) group
    out as its own component."""
    assert _split_alnum(tok) == expected


# ── _collapse_spaced — letter-spaced heading recovery ────────────────────────
def test_collapse_spaced_leaves_ordinary_headings_untouched():
    """An ordinary heading (multi-letter tokens, normal single-space gaps) is NOT a
    letter-spaced rendering and is returned verbatim."""
    assert _collapse_spaced("Versicherte Personen") == "Versicherte Personen"
    assert _collapse_spaced("A.1.1 Geltungsbereich") == "A.1.1 Geltungsbereich"


def test_collapse_spaced_recovers_word_boundaries_on_wide_gaps():
    """Single spaces glue letters within a word; a run of 2+ spaces marks the word
    boundary, so 'T e i l   A' -> 'Teil A' (two words, not 'TeilA')."""
    assert _collapse_spaced("T e i l   A") == "Teil A"


# ── _containment_depths — depth by longest present proper-prefix label ────────
def test_containment_depths_nests_dot_chain_and_none_for_bare_title():
    """depth(i) = 1 + length of the longest OTHER present label that is a proper
    prefix of label(i). 'A'->1, 'A.1'->2, 'A.1.1'->3; a bare-title heading has no
    label and returns None (so the caller leaves its level untouched). This is the
    production None-for-bare contract — bench's standalone helper used 1 instead."""
    titles = ["A", "A.1", "A.1.1", "Versicherte Personen"]
    assert _containment_depths(titles) == [1, 2, 3, None]


def test_containment_depths_missing_intermediate_label_does_not_inflate_depth():
    """Depth counts only the longest PRESENT proper-prefix label. With 'A' present
    but 'A.1' absent, 'A.1.1' nests on 'A' (prefix length 1) -> depth 2, not 3."""
    titles = ["A", "A.1.1"]
    assert _containment_depths(titles) == [1, 2]


# ── _relevel_by_containment — rewrite '#'-levels from containment depth ───────
def test_relevel_by_containment_sets_levels_and_leaves_labelless_unchanged():
    """All-'#' headings A / A.1 / A.1.1 are re-levelled to '# A', '## A.1',
    '### A.1.1' by their containment depth, while a label-less heading keeps its
    original level. Body text and spacing are preserved verbatim."""
    md = (
        "# A\n\nbody a\n\n"
        "# A.1\n\nbody a1\n\n"
        "# A.1.1\n\nbody a11\n\n"
        "# Versicherte Personen\n\nbody vp\n"
    )
    out = _relevel_by_containment(md)
    heading_lines = [ln for ln in out.splitlines() if ln.startswith("#")]
    assert heading_lines == [
        "# A",
        "## A.1",
        "### A.1.1",
        "# Versicherte Personen",  # no label -> level unchanged
    ]
    # Non-heading content is preserved verbatim.
    assert "body a11" in out
    assert "body vp" in out


def test_relevel_by_containment_noop_when_no_headings():
    """With no markdown headings present, the text is returned unchanged."""
    md = "just some prose\n\nmore prose\n"
    assert _relevel_by_containment(md) == md


# ── numeric-extension gate — the re-promotion anchor rule ─────────────────────
def _is_numeric_extension(label: list[str], anchors: set[tuple]) -> bool:
    """Replica of the gate inside ``_repromote_numbered_headings`` (converters.py
    lines ~323-326): a demoted item's label is promotable IFF some non-empty
    kept-anchor label P is a PROPER prefix of it and every component beyond P is a
    pure digit run. Kept here as a small, importable mirror so the gate's decision
    can be unit-tested without booting Docling."""
    lab = tuple(label)
    return any(
        lab[:k] in anchors and all(c.isdigit() for c in lab[k:])
        for k in range(len(lab) - 1, 0, -1)
    )


def test_numeric_extension_promotes_pure_digit_extension_of_kept_anchor():
    """'A.1.1' = kept anchor 'A.1' + ['1'] (a pure digit run) -> promotable. The
    one-step extension 'A.1' of anchor 'A' is likewise promotable."""
    anchors = {("A",), ("A", "1")}
    assert _is_numeric_extension(_segment_label("A.1.1"), anchors) is True
    assert _is_numeric_extension(_segment_label("A.1"), anchors) is True


def test_numeric_extension_rejects_non_numeric_tail():
    """'A.1.x' = anchor 'A.1' + ['x'] but the tail is a LETTER, not a digit run, so
    it is NOT a numeric extension and must not be promoted."""
    anchors = {("A",), ("A", "1")}
    seg = _segment_label("A.1.x")
    assert seg == ["A", "1", "x"]            # shape: a non-numeric tail component
    assert _is_numeric_extension(seg, anchors) is False


def test_numeric_extension_rejects_bare_list_marker():
    """A bare list marker 'a' segments to ['a'] but has NO kept-anchor proper
    prefix, so it is not a numeric extension of any kept section and stays body
    text (no spurious promotion)."""
    anchors = {("A",), ("A", "1")}
    seg = _segment_label("a")
    assert seg == ["a"]                      # a single lowercase list marker
    assert _is_numeric_extension(seg, anchors) is False
