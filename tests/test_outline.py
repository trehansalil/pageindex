# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
from __future__ import annotations
"""Outline extraction, inference, and depth tests."""
import re

import pytest

from pageindex_mcp.converters import (
    _apply_outline_levels,
    _collapse_spaced,
    _containment_depths,
    _outline_norm,
    _read_pdf_outline,
    _relevel_by_containment,
    _segment_label,
    _split_alnum,
    _title_matches,
    numbering_depth,
)
from pageindex_mcp.helpers import split_oversized_leaf_nodes


# --- from test_read_pdf_outline.py ---

def _pdf_with_outline(tmp_path, entries, n_pages=6):
    """Write a PDF with ``n_pages`` blank pages and a nested outline.

    ``entries``: list of ``(title, page_0based, is_child)`` — an ``is_child`` entry
    nests under the most recent top-level item."""
    from PyPDF2 import PdfWriter

    w = PdfWriter()
    for _ in range(n_pages):
        w.add_blank_page(width=200, height=200)
    last_parent = None
    for title, page0, is_child in entries:
        if is_child and last_parent is not None:
            w.add_outline_item(title, page0, parent=last_parent)
        else:
            last_parent = w.add_outline_item(title, page0)
    path = tmp_path / "outlined.pdf"
    with open(path, "wb") as fh:
        w.write(fh)
    return str(path)


def test_read_pdf_outline_applies_one_based_offsets(tmp_path):
    """A 2-level outline round-trips to 1-based level + 1-based page tuples in
    document (outline) order — the offset the consumer depends on."""
    path = _pdf_with_outline(
        tmp_path,
        [
            ("Chapter A", 0, False),  # level 1, page 1
            ("Section A.1", 2, True),  # level 2, page 3
            ("Chapter B", 4, False),  # level 1, page 5
        ],
    )
    toc, total_pages = _read_pdf_outline(path)
    assert total_pages == 6
    assert toc == [
        (1, "Chapter A", 1),
        (2, "Section A.1", 3),
        (1, "Chapter B", 5),
    ]


def test_read_pdf_outline_preserves_outline_order_not_page_order(tmp_path):
    """Document/outline order is preserved verbatim — entries are NOT re-sorted by
    page, because section extents are computed by nesting (reading order)."""
    path = _pdf_with_outline(
        tmp_path,
        [
            ("First", 1, False),  # page 2
            ("Second", 0, False),  # page 1 (earlier page, later in outline)
            ("Third", 3, False),  # page 4
        ],
    )
    toc, _ = _read_pdf_outline(path)
    assert [t for _, t, _ in toc] == ["First", "Second", "Third"]
    assert [p for _, _, p in toc] == [2, 1, 4]


def test_read_pdf_outline_fewer_than_two_entries_returns_empty(tmp_path):
    """A single-bookmark outline yields no usable structural signal -> ([], 0), so
    the caller leaves the markdown flat and the gate rejects it legitimately (HR5)."""
    path = _pdf_with_outline(tmp_path, [("Solo", 0, False)])
    assert _read_pdf_outline(path) == ([], 0)


def test_read_pdf_outline_no_outline_returns_empty(tmp_path):
    """A PDF with pages but no bookmarks at all -> ([], 0)."""
    from PyPDF2 import PdfWriter

    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(width=200, height=200)
    path = tmp_path / "flat.pdf"
    with open(path, "wb") as fh:
        w.write(fh)
    assert _read_pdf_outline(str(path)) == ([], 0)


# --- from test_outline_inference.py ---

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
        "Katzen-Krankenversicherung",  # cover, page 1
        "Um welche Art von Versicherung handelt es sich?",  # FAQ, page 3
        "Was ist versichert?",  # FAQ, page 3
        "Was ist nicht versichert?",  # FAQ, page 4
        "Besondere Bedingungen Katzen-Krankenversicherung",  # T&C anchor, page 5
        "Leistungen",  # T&C child, page 6
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
        (1, "Katzen-Krankenversicherung"),  # cover: pre-outline, untouched
        (1, "Informationsblatt zu Versicherungsprodukten"),  # INJECTED (was not rendered)
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
        "Geltungsbereich",  # page 6
        "Besondere Bedingungen Hundehalterhaftpflichtversicherung",  # page 13
        "Beitrag",  # page 14
        "Besondere Bedingungen Hundehalterhaftpflichtversicherung",  # page 21
        "Kuendigung",  # page 22
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
        "Begriffsbestimmungen",  # L2 title, page 3
        "Tierarztkosten",  # content under L2, page 4
        "Beitrag und Beginn",  # next L1 title, page 6
        "Faelligkeit",  # content under 2nd L1, page 7
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
        "Allgemeines",  # content, page 3
        "Tierarztkosten",  # content, page 4
        "Faelligkeit",  # content under 2nd L1, page 7
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
        (2, "Begriffsbestimmungen"),  # injected child second
        (3, "Allgemeines"),
        (3, "Tierarztkosten"),
        (1, "Beitrag und Beginn"),  # injected (its own band)
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
        "Leistungen",  # page 6, child
        "Orphan Heading",  # NOT in the page map
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


# --- from test_depth_inference.py ---

@pytest.mark.parametrize(
    "title,expected",
    [
        ("A.1.1", ["A", "1", "1"]),
        ("Versicherte Personen", []),
    ],
)
def test_segment_label_components(title, expected):
    assert _segment_label(title) == expected


def test_segment_label_letter_spaced():
    assert _collapse_spaced("T e i l   A") == "Teil A"
    assert _segment_label("T e i l   A") == ["A"]


@pytest.mark.parametrize(
    "tok,expected",
    [
        ("A1", ["A", "1"]),
        ("A(GB)1", ["A", "GB", "1"]),
    ],
)
def test_split_alnum(tok, expected):
    assert _split_alnum(tok) == expected


def test_containment_depths():
    assert _containment_depths(["A", "A.1", "A.1.1", "Versicherte Personen"]) == [1, 2, 3, None]


def test_relevel_by_containment():
    md = "# A\n\nbody a\n\n# A.1\n\nbody a1\n\n# A.1.1\n\nbody a11\n\n# Versicherte Personen\n\nbody vp\n"
    out = _relevel_by_containment(md)
    heading_lines = [ln for ln in out.splitlines() if ln.startswith("#")]
    assert heading_lines == ["# A", "## A.1", "### A.1.1", "# Versicherte Personen"]


def test_numeric_extension():
    lab = tuple(_segment_label("A.1.1"))
    anchors = {("A",), ("A", "1")}
    assert any(
        lab[:k] in anchors and all(c.isdigit() for c in lab[k:]) for k in range(len(lab) - 1, 0, -1)
    )
    bad_lab = tuple(_segment_label("A.1.x"))
    assert not any(
        bad_lab[:k] in anchors and all(c.isdigit() for c in bad_lab[k:])
        for k in range(len(bad_lab) - 1, 0, -1)
    )


@pytest.mark.parametrize(
    "title,expected",
    [
        ("المادة (9)", 2),
        ("A.1 Geltungsbereich", 2),
    ],
)
def test_numbering_depth(title, expected):
    assert numbering_depth(title) == expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("المادة ٩", ["9"]),
        ("Abschnitt A1", ["A", "1"]),
    ],
)
def test_segment_label_arabic(title, expected):
    assert _segment_label(title) == expected


_SMALL_MAX = 50


def _make_leaf(node_id, text):
    return {"title": "Root", "text": text, "nodes": [], "node_id": node_id}


def test_split_oversized_arabic_markers():
    preamble = "مقدمة " * 10 + "\n"
    body = "المادة (1)\nنص المادة الأولى\nالمادة (2)\nنص المادة الثانية\nالمادة (3)\nنص المادة الثالثة\n"
    text = preamble + body
    assert len(text) > _SMALL_MAX
    node = _make_leaf("root-1", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)
    assert len(result[0]["nodes"]) == 3
    assert result[0]["nodes"][0]["node_id"] == "root-1-s0"


def test_split_oversized_english_paren_inline():
    preamble = "preamble. "
    body = (
        "Article (1) the first provision states things. "
        "Article (2) the second provision continues. "
        "Article (3) the third provision concludes here."
    )
    text = preamble + body
    assert len(text) > _SMALL_MAX
    node = _make_leaf("paren", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)
    assert len(result[0]["nodes"]) == 3
    assert result[0]["text"] + "".join(c["text"] for c in result[0]["nodes"]) == text


def test_frontmatter_toc_left_intact():
    entries = "\n".join(
        f"Chapter Title {i} for Dartmouth Publishing House Social Rights Review "
        + "." * 12
        + f" {i}"
        for i in range(40)
    )
    text = "حقـوق الإنسان\nDartmouth Publishing House, Social Rights Review 1996.\n" + entries
    assert len(text) > _SMALL_MAX
    node = _make_leaf("toc", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)
    assert result[0]["nodes"] == []
