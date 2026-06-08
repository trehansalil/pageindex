# tests/test_outline_inference.py
"""Unit tests for the PDF-OUTLINE heading-depth recovery in
``pageindex_mcp.converters`` — the last-resort depth source for flat-prose German
insurance PDFs (IPID / FAQ T&Cs) whose headings carry NO numbering, so the
numbering-prefix chain (_relevel_by_containment / _relevel_by_numbering) leaves
them flat (max_heading_level == 1) and the HR5 depth>=2 gate falsely rejects them.

These are FAST pure-Python tests: no Docling, no PyMuPDF, no PDF, no LLM. They
drive ``_apply_outline_levels`` (the pure string transform) directly with a
synthetic {heading -> page} map + parsed TOC, pinning the behaviour each
adversarial review of the design demanded:

  _outline_norm          title -> lowercase alphanumerics (cross-source matching)
  _title_matches         exact / substantial-substring section-title match
  _apply_outline_levels  assign H-levels from outline page-spans + inject missing
                         section titles (BLOCKER-1 fix: anchor only on real title
                         match, never "first child"; BLOCKER-2 fix: nesting-aware
                         extents so co-page L1/L2 entries don't collapse)
"""

import re

from pageindex_mcp.converters import (
    _apply_outline_levels,
    _outline_norm,
    _title_matches,
)


_HEAD = re.compile(r"^(#{1,6})[ \t]+(.*\S)[ \t]*$", re.MULTILINE)


def _headings(md: str) -> list[tuple[int, str]]:
    """[(level, title), ...] for every markdown heading, in document order."""
    return [(len(m.group(1)), m.group(2)) for m in _HEAD.finditer(md)]


def _md(*titles: str) -> str:
    """Build a flat (all-H1) markdown body with a blank line + body after each
    heading — the shape ``_relevel_headings`` produces before outline recovery."""
    return "".join(f"# {t}\n\nbody of {t}\n\n" for t in titles)


# ── _outline_norm ─────────────────────────────────────────────────────────────
def test_outline_norm_strips_to_lowercase_alnum_and_unifies_dashes():
    """Whitespace, embedded newlines, dash variants and punctuation are all
    stripped so a PyMuPDF TOC title reconciles with a Docling-rendered heading."""
    assert _outline_norm("Besondere Bedingungen\nKatzen-Krankenversicherung") == (
        "besonderebedingungenkatzenkrankenversicherung"
    )
    # en-dash / non-breaking hyphen normalise the same as ASCII '-'
    assert _outline_norm("A–B") == _outline_norm("A-B") == "ab"
    assert _outline_norm("") == ""
    assert _outline_norm(None) == ""  # type: ignore[arg-type]


# ── _title_matches ────────────────────────────────────────────────────────────
def test_title_matches_exact_and_substantial_substring():
    """Exact normalised equality matches; so does a substring when the shorter
    string is substantial (>= 8 alnum chars) — tolerating Docling rendering a
    longer heading than the TOC title."""
    sec = _outline_norm("Besondere Bedingungen Katzen-Krankenversicherung")
    assert _title_matches(sec, sec) is True
    # rendered heading longer than the (shorter) TOC title -> substring match
    assert _title_matches(sec, _outline_norm("Besondere Bedingungen")) is True


def test_title_matches_rejects_short_or_empty():
    """A short (<8 alnum) coincidental overlap is NOT a match (guards generic
    corpora against short-title false positives); empty never matches."""
    assert _title_matches(_outline_norm("Beitrag"), _outline_norm("Beginn")) is False
    assert _title_matches("", "anything") is False
    assert _title_matches("anything", "") is False


# ── _apply_outline_levels — Cat B: 2x L1, missing IPID anchor injected ────────
def test_catb_injects_missing_ipid_anchor_and_subordinates_faq():
    """Katzen-Kranken shape: the IPID outline title is NOT rendered by Docling, so
    it is INJECTED as H1 and the FAQ headings on its page become H2 children; the
    Besondere-Bedingungen title IS rendered, so it stays H1 (no duplicate inject).
    This is the BLOCKER-1 fix: the anchor is the real title, never the first FAQ."""
    md = _md(
        "Katzen-Krankenversicherung",                       # cover, page 1
        "Um welche Art von Versicherung handelt es sich?",  # FAQ, page 3
        "Was ist versichert?",                              # FAQ, page 3
        "Was ist nicht versichert?",                        # FAQ, page 4
        "Besondere Bedingungen Katzen-Krankenversicherung",  # T&C anchor, page 5
        "Leistungen",                                       # T&C child, page 6
    )
    heading_pages = {
        _outline_norm("Katzen-Krankenversicherung"): [1],
        _outline_norm("Um welche Art von Versicherung handelt es sich?"): [3],
        _outline_norm("Was ist versichert?"): [3],
        _outline_norm("Was ist nicht versichert?"): [4],
        _outline_norm("Besondere Bedingungen Katzen-Krankenversicherung"): [5],
        _outline_norm("Leistungen"): [6],
    }
    toc = [
        (1, "Informationsblatt zu Versicherungsprodukten", 3),
        (1, "Besondere Bedingungen Katzen-Krankenversicherung", 5),
    ]
    out = _apply_outline_levels(md, heading_pages, toc, total_pages=8)
    assert _headings(out) == [
        (1, "Katzen-Krankenversicherung"),                       # cover: pre-outline, untouched
        (1, "Informationsblatt zu Versicherungsprodukten"),      # INJECTED (was not rendered)
        (2, "Um welche Art von Versicherung handelt es sich?"),
        (2, "Was ist versichert?"),
        (2, "Was ist nicht versichert?"),
        (1, "Besondere Bedingungen Katzen-Krankenversicherung"),  # rendered title -> stays H1
        (2, "Leistungen"),
    ]
    # body text is preserved verbatim
    assert "body of Was ist versichert?" in out


# ── _apply_outline_levels — repeated identical titles (deque disambiguation) ──
def test_repeated_identical_titles_are_kept_apart_by_page_deque():
    """Hundehalterhaftpflicht shape: the same 'Besondere Bedingungen ...' chapter
    title appears 3x at pages 5/13/21. The per-text page deque pops in document
    order so each rendered heading anchors its OWN page band (H1) with its content
    as H2 — no collision, no zero-width band."""
    md = _md(
        "Besondere Bedingungen Hundehalterhaftpflichtversicherung",  # page 5
        "Geltungsbereich",                                            # page 6
        "Besondere Bedingungen Hundehalterhaftpflichtversicherung",  # page 13
        "Beitrag",                                                    # page 14
        "Besondere Bedingungen Hundehalterhaftpflichtversicherung",  # page 21
        "Kuendigung",                                                 # page 22
    )
    bb = _outline_norm("Besondere Bedingungen Hundehalterhaftpflichtversicherung")
    heading_pages = {
        bb: [5, 13, 21],
        _outline_norm("Geltungsbereich"): [6],
        _outline_norm("Beitrag"): [14],
        _outline_norm("Kuendigung"): [22],
    }
    toc = [
        (1, "Besondere Bedingungen Hundehalterhaftpflichtversicherung", 5),
        (1, "Besondere Bedingungen Hundehalterhaftpflichtversicherung", 13),
        (1, "Besondere Bedingungen Hundehalterhaftpflichtversicherung", 21),
    ]
    out = _apply_outline_levels(md, heading_pages, toc, total_pages=28)
    assert _headings(out) == [
        (1, "Besondere Bedingungen Hundehalterhaftpflichtversicherung"),
        (2, "Geltungsbereich"),
        (1, "Besondere Bedingungen Hundehalterhaftpflichtversicherung"),
        (2, "Beitrag"),
        (1, "Besondere Bedingungen Hundehalterhaftpflichtversicherung"),
        (2, "Kuendigung"),
    ]


# ── _apply_outline_levels — BLOCKER-2: nested co-page L1/L2 must not collapse ──
def test_copage_nested_l1_l2_entries_do_not_collapse():
    """Tier-OP-Kranken shape: an L1 section and its first L2 child start on the
    SAME page (3). Nesting-aware extents (end = next entry whose level <= current)
    keep the L1 reachable, so the rendered L1 title stays H1 (NOT demoted to H2 by
    a zero-width band) and the L2 title becomes H2 with its content at H3."""
    md = _md(
        "Umfang des Versicherungsschutzes",  # L1 title, page 3
        "Begriffsbestimmungen",              # L2 title, page 3
        "Tierarztkosten",                    # content under L2, page 4
        "Beitrag und Beginn",                # next L1 title, page 6
        "Faelligkeit",                       # content under 2nd L1, page 7
    )
    heading_pages = {
        _outline_norm("Umfang des Versicherungsschutzes"): [3],
        _outline_norm("Begriffsbestimmungen"): [3],
        _outline_norm("Tierarztkosten"): [4],
        _outline_norm("Beitrag und Beginn"): [6],
        _outline_norm("Faelligkeit"): [7],
    }
    toc = [
        (1, "Umfang des Versicherungsschutzes", 3),
        (2, "Begriffsbestimmungen", 3),
        (1, "Beitrag und Beginn", 6),
    ]
    out = _apply_outline_levels(md, heading_pages, toc, total_pages=8)
    assert _headings(out) == [
        (1, "Umfang des Versicherungsschutzes"),  # L1 NOT collapsed to H2
        (2, "Begriffsbestimmungen"),
        (3, "Tierarztkosten"),
        (1, "Beitrag und Beginn"),
        (2, "Faelligkeit"),
    ]


def test_copage_nested_missing_titles_inject_parent_before_child():
    """When BOTH a co-page L1 and its L2 child titles are absent from the rendered
    set, both are injected before the first content heading, shallowest-first so
    the parent H1 precedes the child H2 (injection-ordering correctness)."""
    md = _md(
        "Allgemeines",     # content, page 3
        "Tierarztkosten",  # content, page 4
        "Faelligkeit",     # content under 2nd L1, page 7
    )
    heading_pages = {
        _outline_norm("Allgemeines"): [3],
        _outline_norm("Tierarztkosten"): [4],
        _outline_norm("Faelligkeit"): [7],
    }
    toc = [
        (1, "Umfang des Versicherungsschutzes", 3),
        (2, "Begriffsbestimmungen", 3),
        (1, "Beitrag und Beginn", 6),
    ]
    out = _apply_outline_levels(md, heading_pages, toc, total_pages=8)
    assert _headings(out) == [
        (1, "Umfang des Versicherungsschutzes"),  # injected parent first
        (2, "Begriffsbestimmungen"),              # injected child second
        (3, "Allgemeines"),
        (3, "Tierarztkosten"),
        (1, "Beitrag und Beginn"),                # injected (its own band)
        (2, "Faelligkeit"),
    ]


# ── _apply_outline_levels — Cat D / degenerate: leave md unchanged ────────────
def test_empty_toc_returns_md_unchanged():
    """No usable outline (Cat D leaflet) -> md returned verbatim so the gate
    rejects it legitimately (HR5: the depth<2 threshold is never weakened)."""
    md = _md("Leistungen", "Beitrag")
    assert _apply_outline_levels(md, {}, [], total_pages=0) == md


def test_no_recovered_depth_returns_original_md():
    """If every rendered heading is its own section anchor (all H1, no children and
    no injected titles), the rewrite stays flat -> original md is returned so the
    gate still rejects it rather than receiving an equally-flat tree."""
    md = _md("Alpha Section Title", "Beta Section Title")
    heading_pages = {
        _outline_norm("Alpha Section Title"): [2],
        _outline_norm("Beta Section Title"): [4],
    }
    toc = [(1, "Alpha Section Title", 2), (1, "Beta Section Title", 4)]
    assert _apply_outline_levels(md, heading_pages, toc, total_pages=6) == md


def test_no_headings_returns_md_unchanged():
    """Body-only markdown (no headings) is returned verbatim."""
    md = "just prose\n\nmore prose\n"
    toc = [(1, "Alpha", 1), (1, "Beta", 2)]
    assert _apply_outline_levels(md, {}, toc, total_pages=3) == md


def test_heading_without_page_provenance_is_left_unchanged():
    """A rendered heading absent from the page map (no provenance) keeps its
    current level instead of being mis-placed into a section band."""
    md = _md(
        "Besondere Bedingungen Katzen-Krankenversicherung",  # page 5, anchor
        "Leistungen",                                         # page 6, child
        "Orphan Heading",                                     # NOT in the page map
    )
    heading_pages = {
        _outline_norm("Besondere Bedingungen Katzen-Krankenversicherung"): [5],
        _outline_norm("Leistungen"): [6],
    }
    toc = [
        (1, "Informationsblatt zu Versicherungsprodukten", 3),
        (1, "Besondere Bedingungen Katzen-Krankenversicherung", 5),
    ]
    out = _apply_outline_levels(md, heading_pages, toc, total_pages=8)
    levels = {t: lv for lv, t in _headings(out)}
    assert levels["Orphan Heading"] == 1  # no provenance -> untouched
    assert levels["Besondere Bedingungen Katzen-Krankenversicherung"] == 1
    assert levels["Leistungen"] == 2
