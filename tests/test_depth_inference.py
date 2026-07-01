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
    _collapse_spaced,
    _containment_depths,
    _relevel_by_containment,
    _segment_label,
    _split_alnum,
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


# ── Arabic numbering_depth (Fix 1) ───────────────────────────────────────────
from pageindex_mcp.converters import numbering_depth  # noqa: E402


@pytest.mark.parametrize(
    "title,expected",
    [
        # Arabic tier — new in Fix 1
        ("الباب الأول", 1),       # chapter word -> depth 1
        ("الفصل الثاني", 1),      # section word -> depth 1
        ("المادة (9)", 2),         # article -> depth 2
        # German/English UNCHANGED — regression guard
        ("§3.1.2 Deckung", 3),    # §-paragraph sub-sub-section
        ("A.1 Geltungsbereich", 2),  # dot notation two levels
        ("Teil A Allgemeines", 1),   # part word -> depth 1
    ],
)
def test_numbering_depth_arabic_and_german_regression(title, expected):
    """Fix 1: Arabic chapter/article depth tiers; German/English results unchanged."""
    assert numbering_depth(title) == expected


def test_numbering_depth_plain_prose_returns_none():
    """Plain prose headings carry no recognised numbering prefix -> None."""
    assert numbering_depth("Versicherte Personen") is None
    assert numbering_depth("Geltungsbereich") is None


# ── Arabic _segment_label (Fix 1) ────────────────────────────────────────────
@pytest.mark.parametrize(
    "title,expected",
    [
        # Arabic structural word consumed; Arabic-Indic digit normalised
        ("المادة ٩", ["9"]),
        ("المادة (10)", ["10"]),
        # German UNCHANGED — regression guard
        ("Abschnitt A1", ["A", "1"]),
        ("A1-6.1 Versicherte Sachen", ["A", "1", "6", "1"]),
    ],
)
def test_segment_label_arabic_and_german_regression(title, expected):
    """Fix 1: Arabic article label extraction; German cases byte-for-byte unchanged."""
    assert _segment_label(title) == expected


# ── split_oversized_leaf_nodes (Fix 1, helpers.py) ───────────────────────────
from pageindex_mcp.helpers import split_oversized_leaf_nodes  # noqa: E402

_SMALL_MAX = 50  # tiny threshold so we don't need 50 k-char strings


def _make_leaf(node_id: str, text: str) -> dict:
    return {"title": "Root", "text": text, "nodes": [], "node_id": node_id}


def test_split_oversized_arabic_markers():
    """SPLIT-01-C1. A >max_chars leaf with ≥3 مادة markers is split into per-article children;
    parent retains preamble; node_ids are derived from parent."""
    preamble = "مقدمة " * 10 + "\n"
    body = (
        "المادة (1)\nنص المادة الأولى\n"
        "المادة (2)\nنص المادة الثانية\n"
        "المادة (3)\nنص المادة الثالثة\n"
    )
    text = preamble + body
    # ensure it triggers the size gate
    assert len(text) > _SMALL_MAX

    node = _make_leaf("root-1", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)

    assert result is not None
    assert len(result) == 1
    parent = result[0]
    # preamble stays in the parent
    assert parent["text"] == text[: text.index("المادة (1)")]
    children = parent["nodes"]
    assert len(children) == 3
    assert children[0]["node_id"] == "root-1-s0"
    assert children[2]["node_id"] == "root-1-s2"
    assert "المادة (1)" in children[0]["text"]
    assert "المادة (3)" in children[2]["text"]


def test_split_oversized_english_article_markers():
    """SPLIT-01-C1. A >max_chars leaf with Article 1/2/3 markers is split into 3 children."""
    preamble = "preamble text here.\n"
    body = (
        "Article 1\nFirst article content.\n"
        "Article 2\nSecond article content.\n"
        "Article 3\nThird article content.\n"
    )
    text = preamble + body
    assert len(text) > _SMALL_MAX

    node = _make_leaf("en-root", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)

    parent = result[0]
    assert len(parent["nodes"]) == 3
    assert "Article 1" in parent["nodes"][0]["text"]
    assert "Article 3" in parent["nodes"][2]["text"]
    assert parent["nodes"][0]["node_id"] == "en-root-s0"


def test_split_oversized_english_paren_form_inline():
    """REDESIGN: the real Penal-Code shape — paren 'Article (N)' markers appearing
    INLINE (not at line start) — is split. The old line-anchored + no-paren regex
    missed this entirely (the 236k-char tail-blob)."""
    preamble = "preamble. "
    # Markers are mid-line (inline prose), parenthesised, like Docling's demoted output.
    body = (
        "Article (1) the first provision states things. "
        "Article (2) the second provision continues. "
        "Article (3) the third provision concludes here."
    )
    text = preamble + body
    assert len(text) > _SMALL_MAX

    node = _make_leaf("paren", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)
    children = result[0]["nodes"]
    assert len(children) == 3
    assert children[0]["text"].startswith("Article (1)")
    assert children[1]["text"].startswith("Article (2)")
    # byte-exact reconstruction (inline split preserves the original)
    assert result[0]["text"] + "".join(c["text"] for c in children) == text


def test_split_oversized_cross_reference_not_split():
    """REDESIGN: inline matching must NOT be fooled by backward cross-references.
    The longest strictly-increasing ordinal run is kept, so a 'see Article 1' ref
    inside a later article does not create a spurious split point."""
    preamble = "preamble. "
    body = (
        "Article (1) opening. "
        "Article (2) body which mentions Article (1) again as a cross-reference. "
        "Article (3) closing."
    )
    text = preamble + body
    assert len(text) > _SMALL_MAX

    node = _make_leaf("xref", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)
    children = result[0]["nodes"]
    # 4 raw matches (1,2,1,3) -> increasing run 1,2,3 -> exactly 3 children,
    # and the cross-ref to Article (1) stays INSIDE article 2's text.
    assert len(children) == 3
    assert "cross-reference" in children[1]["text"]


def test_split_oversized_presentation_form_arabic():
    """REDESIGN: presentation-form Arabic (U+FExx ligatures + tatweel) must match
    via NFKC folding, while the STORED slice stays byte-identical to the original.
    This is the حقوق الإنسان (320k) class the old standard-letter regex could not
    touch."""
    # Build markers from Arabic Presentation Forms-B isolated glyphs for م ا د ة
    # plus a tatweel, so the raw bytes are NOT standard 'مادة'.
    pf = "ﻣﺍﺩﺓ"  # ﻣ ا ﺩ ة (presentation forms)
    tatweel = "ـ"
    marker = pf + tatweel  # e.g. ﻣﺍﺩﺓـ — folds to standard letters for matching
    body = (
        f"{marker} (1) اول. "
        f"{marker} (2) ثان. "
        f"{marker} (3) ثالث."
    )
    text = "تمهيد. " + body
    assert len(text) > _SMALL_MAX
    # sanity: raw text contains NO standard 'مادة'
    assert "مادة" not in text

    node = _make_leaf("pf", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)
    children = result[0]["nodes"]
    assert len(children) == 3
    # stored slices are byte-exact (still presentation-form, unmodified)
    assert result[0]["text"] + "".join(c["text"] for c in children) == text
    assert pf in children[0]["text"]


def test_split_oversized_idempotent():  # SPLIT-01-C3
    """Calling split_oversized_leaf_nodes twice gives the same result as once."""
    preamble = "intro " * 5 + "\n"
    body = "Article 1\ntext one.\nArticle 2\ntext two.\nArticle 3\ntext three.\n"
    text = preamble + body
    assert len(text) > _SMALL_MAX

    import copy
    node1 = _make_leaf("idem", text)

    first = split_oversized_leaf_nodes([node1], max_chars=_SMALL_MAX)
    second = split_oversized_leaf_nodes(copy.deepcopy(first), max_chars=_SMALL_MAX)

    # Number of children must be the same after second pass
    assert len(first[0]["nodes"]) == len(second[0]["nodes"])
    for c1, c2 in zip(first[0]["nodes"], second[0]["nodes"], strict=True):
        assert c1["text"] == c2["text"]


def test_split_oversized_small_leaf_untouched():  # SPLIT-01-C3
    """A leaf whose text is <= max_chars is returned unchanged."""
    text = "Article 1\nshort.\nArticle 2\nalso short.\n"
    assert len(text) <= _SMALL_MAX * 4  # definitely under large threshold
    node = _make_leaf("tiny", text)
    result = split_oversized_leaf_nodes([node], max_chars=10_000)
    assert result[0]["nodes"] == []
    assert result[0]["text"] == text


def test_split_oversized_no_markers_untouched():
    """A large leaf with no ordinal markers is NOT split (fewer than min_segments)."""
    text = "x" * (_SMALL_MAX + 10)
    node = _make_leaf("blob", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)
    assert result[0]["nodes"] == []
    assert result[0]["text"] == text


def test_split_oversized_below_min_segments_untouched():
    """REDESIGN: a large blob with only 2 increasing markers is NOT split at the
    default min_segments=3 (guards against inline false positives)."""
    text = "preamble. Article (1) one provision. Article (2) two provisions only."
    assert len(text) > _SMALL_MAX
    node = _make_leaf("two", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)
    assert result[0]["nodes"] == []
    # but with an explicit min_segments=2 it does split
    node2 = _make_leaf("two2", text)
    result2 = split_oversized_leaf_nodes([node2], max_chars=_SMALL_MAX, min_segments=2)
    assert len(result2[0]["nodes"]) == 2


def test_split_oversized_rtl_byte_identity():
    """Split segments are byte-identical slices of the original text (RTL-safe)."""
    preamble = "بسم الله " * 3 + "\n"
    seg1 = "المادة (1)\nالنص الأول بالعربية وبدون تعديل.\n"
    seg2 = "المادة (2)\nالنص الثاني بالعربية أيضاً.\n"
    seg3 = "المادة (3)\nالنص الثالث بالعربية كذلك.\n"
    text = preamble + seg1 + seg2 + seg3
    assert len(text) > _SMALL_MAX

    node = _make_leaf("rtl", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)
    children = result[0]["nodes"]
    assert len(children) == 3

    # Reconstruct: preamble + child texts must equal the original
    reconstructed = result[0]["text"] + "".join(c["text"] for c in children)
    assert reconstructed == text

    # Each child's text is an exact byte slice (no reordering)
    assert children[0]["text"].encode() == seg1.encode()
    assert children[1]["text"].encode() == seg2.encode()
    assert children[2]["text"].encode() == seg3.encode()


# ── Option 1: فقرة (paragraph) fallback ──────────────────────────────────────


def test_split_paragraph_fallback_scrambled_ordinals():
    """REDESIGN Option 1: when مادة markers exist but don't form a long enough
    increasing run (e.g. an RTL-scrambled extraction), fall back to splitting on
    the un-numbered فقرة marker instead of abandoning the leaf."""
    preamble = "مقدمة. "
    # Scrambled مادة ordinals (8, 2) -> LIS keeps only 1 marker, below min_segments.
    body = (
        "المادة (8) نص طويل. " + "س" * 40 + "\n"
        "فقرة اولى من نص طويل جدا. " + "ح" * 5200 + "\n"
        "فقرة ثانية من نص طويل جدا ايضا. " + "ط" * 5200 + "\n"
        "المادة (2) نص اخر. " + "ص" * 40 + "\n"
        "فقرة ثالثة. " + "ك" * 5200 + "\n"
    )
    text = preamble + body
    _max = 15_000  # >> the 5000-char فقرة dedup floor, << total text length
    assert len(text) > _max

    node = _make_leaf("scrambled", text)
    result = split_oversized_leaf_nodes([node], max_chars=_max)
    children = result[0]["nodes"]

    assert len(children) == 3
    # Byte-exact reconstruction (RTL-safe, no reordering).
    assert result[0]["text"] + "".join(c["text"] for c in children) == text


def test_split_paragraph_fallback_dense_refs_collapsed():
    """Dense inline فقرة references (below the min-segment-chars floor) are
    collapsed into the previous segment rather than creating tiny fragments."""
    body = (
        "فقرة اولى. " + "أ" * 6000 + "\n"
        "راجع فقرة سابقة هنا. "  # inline ref, close to the previous marker
        "فقرة ثانية. " + "ب" * 6000 + "\n"
        "فقرة ثالثة. " + "ج" * 6000 + "\n"
    )
    _max = 15_000
    assert len(body) > _max
    node = _make_leaf("dense", body)
    result = split_oversized_leaf_nodes([node], max_chars=_max)
    children = result[0]["nodes"]

    # The inline "فقرة سابقة" reference is NOT promoted to its own tiny child.
    assert len(children) == 3
    for child in children:
        assert len(child["text"]) >= 5000 or child is children[-1]


def test_split_paragraph_fallback_declines_if_still_oversized():
    """If فقرة splitting still leaves a segment >= max_chars, the leaf is left
    untouched rather than half-split."""
    text = (
        "فقرة اولى. " + "أ" * 60_000 + "\n"  # segment itself exceeds max_chars
        "فقرة ثانية. " + "ب" * 40 + "\n"
        "فقرة ثالثة. " + "ج" * 40 + "\n"
    )
    node = _make_leaf("too-big", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)
    assert result[0]["nodes"] == []
    assert result[0]["text"] == text


def test_split_paragraph_fallback_not_reached_when_ordinal_path_succeeds():
    """The فقرة fallback must never run when the ordinal path already produced a
    valid split — it is reached only via the two abandon branches."""
    text = (
        "preamble. "
        "Article (1) has فقرة mentioned inline but ordinal split wins. " + "x" * 40 + "\n"
        "Article (2) body. " + "y" * 40 + "\n"
        "Article (3) body. " + "z" * 40 + "\n"
    )
    assert len(text) > _SMALL_MAX
    node = _make_leaf("ordinal-wins", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)
    children = result[0]["nodes"]
    assert len(children) == 3
    assert children[0]["node_id"] == "ordinal-wins-s0"


# ── Option 2: front-matter / ToC guard ───────────────────────────────────────


def test_frontmatter_toc_left_intact():  # SPLIT-01-C2
    """REDESIGN Option 2: a dotted-leader ToC/bibliography block with ~no ordinal
    markers is accepted as-is instead of being force-split (there is nothing
    meaningful to split it on, and فقρة would shred the bibliography)."""
    entries = "\n".join(
        f"Chapter Title {i} for Dartmouth Publishing House Social Rights Review " + "." * 12 + f" {i}"
        for i in range(40)
    )
    text = "حقـوق الإنسان\nDartmouth Publishing House, Social Rights Review 1996.\n" + entries
    assert len(text) > _SMALL_MAX

    node = _make_leaf("toc", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)
    assert result[0]["nodes"] == []
    assert result[0]["text"] == text


def test_frontmatter_toc_does_not_suppress_real_article_toc():  # SPLIT-01-C2
    """A genuine article-dense Arabic ToC (high ordinal density) is NOT flagged as
    front matter and still splits normally."""
    preamble = "فهرس. " + "." * 10 + "\n"
    body = (
        "المادة (1)\nنص المادة الأولى.\n" + "." * 6 + "\n"
        "المادة (2)\nنص المادة الثانية.\n" + "." * 6 + "\n"
        "المادة (3)\nنص المادة الثالثة.\n" + "." * 6 + "\n"
    )
    text = preamble + body
    assert len(text) > _SMALL_MAX
    node = _make_leaf("real-toc", text)
    result = split_oversized_leaf_nodes([node], max_chars=_SMALL_MAX)
    assert len(result[0]["nodes"]) == 3
