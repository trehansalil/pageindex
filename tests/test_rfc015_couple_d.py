"""RFC-015 Couple D — helpers.py sub-fixes D5a / D5b / D8 / D9.

All tests use synthetic / property-based inputs. The doc-specific validating
trees named in the RFC (6147c7d7, 8cfeca9a, 92eebefa, e544d939, ...) live only in
MinIO `processed/*.json` and are NOT in the repo, so per this repo's
no-fabrication rule they are exercised here via minimal synthetic reproductions of
the SHAPE each fix targets — never invented copies of the real stored artifacts.

HR5 posture: every assertion below confirms a change is additive/tightening —
D5a/D5b recover MORE real structure (never split without a genuine ordinal run),
D8 only ADDS garble FAILs (clean/legit text stays not-garbled), D9 only fills
column 0 (data columns untouched). None can flip a previously-rejected tree to a
silent PASS.
"""

from pageindex_mcp.helpers import (
    BULK_PROFILE,
    FLAT_MARKDOWN_PROFILE,
    _OVERSIZED_ORDINAL_RE,
    _flat_parse_table,
    _flatten_tree_text,
    _forward_fill_leading_column,
    _has_heading_markers,
    _has_sparse_mojibake,
    _ordinal_value,
    check_garble,
    split_oversized_leaf_nodes,
    validate_tree,
)

# ── D5b: `Schedule (N)` ordinal alternative ─────────────────────────────────


def test_d5b_schedule_matches_ordinal_regex():
    """`Schedule 3` / `Schedule (3)` are recognised as ordinal markers."""
    assert _OVERSIZED_ORDINAL_RE.search("Schedule 3") is not None
    assert _OVERSIZED_ORDINAL_RE.search("Schedule (3)") is not None


def test_d5b_schedule_ordinal_value():
    """The captured Schedule number feeds the strictly-increasing-run guard."""
    m = _OVERSIZED_ORDINAL_RE.search("Schedule (7)")
    assert m is not None
    assert _ordinal_value(m) == (7,)


def test_d5b_existing_alternatives_untouched():
    """D5b is additive — §/Article/Section/مادة all still match as before."""
    assert _OVERSIZED_ORDINAL_RE.search("§ 12") is not None
    assert _OVERSIZED_ORDINAL_RE.search("Article (9)") is not None
    assert _OVERSIZED_ORDINAL_RE.search("Section 4") is not None
    assert _OVERSIZED_ORDINAL_RE.search("المادة ٥") is not None


# ── D5a: size-gate decoupled from marker density ────────────────────────────


def test_d5a_has_heading_markers():
    """_has_heading_markers detects an ordinal run; plain prose has none."""
    assert _has_heading_markers("... Schedule 1 ... Schedule 2 ...") is True
    assert _has_heading_markers("just some ordinary paragraph text here") is False
    assert _has_heading_markers("") is False


def test_d5a_small_leaf_with_markers_is_split():
    """A leaf UNDER max_chars but carrying a real ordinal run is now split
    (6147c7d7's 19,959-char residual-leaf class). Pre-D5a this was skipped."""
    body = (
        "Schedule 1\n" + "a" * 400 + "\n"
        "Schedule 2\n" + "b" * 400 + "\n"
        "Schedule 3\n" + "c" * 400 + "\n"
    )
    tree = [{"node_id": "n1", "title": "root", "text": body, "nodes": []}]
    split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)
    assert len(tree[0]["nodes"]) == 3
    titles = [c["title"] for c in tree[0]["nodes"]]
    assert titles == ["Schedule 1", "Schedule 2", "Schedule 3"]


def test_d5a_small_leaf_without_markers_untouched():
    """A small marker-free leaf is NOT split — D5a only widens, never forces."""
    tree = [{"node_id": "n1", "title": "root", "text": "plain short body", "nodes": []}]
    split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)
    assert tree[0]["nodes"] == []
    assert tree[0]["text"] == "plain short body"


def test_d5a_cross_reference_only_leaf_not_split():
    """A small leaf with only non-monotonic cross-refs (no increasing run of
    min_segments) is left intact — the LIS guard still governs."""
    text = "see Article 9 above, and Article 2 earlier, per Article 5."
    tree = [{"node_id": "n1", "title": "root", "text": text, "nodes": []}]
    split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)
    assert tree[0]["nodes"] == []


# ── D8: sparse mixed-script mojibake detection ──────────────────────────────


def test_d8_glued_mojibake_flagged():
    """Latin fragments glued into Arabic (no spaces) → garbled."""
    moji = "كtابcجديدxمادةyنص عربي سليم شروط التأمين " * 5
    assert len(moji) > 100
    assert _has_sparse_mojibake(moji) is True


def test_d8_clean_arabic_not_flagged():
    """Clean multi-word Arabic prose must NOT be flagged (space is not 'glued').
    This is the calibration anchor that would break under a literal \\x20 class."""
    clean_ar = "هذا نص عربي سليم تماما عن شروط التأمين والتغطية القانونية اليوم " * 2
    assert len(clean_ar) > 100
    assert _has_sparse_mojibake(clean_ar) is False


def test_d8_transliterated_names_not_flagged():
    """Space-separated Latin names among Arabic (b1a72fb2 class) → not flagged."""
    translit = "المدير Ahmed Hassan وقع العقد مع Mohamed Ali في مدينة القاهرة اليوم " * 2
    assert _has_sparse_mojibake(translit) is False


def test_d8_short_text_not_flagged():
    """<100 chars is length-gated out regardless of content."""
    assert _has_sparse_mojibake("كtابcمادة") is False


def test_d8_wired_into_garble_gates_additively():
    """The mojibake signal reaches both tree-bulk and flat-markdown garble paths,
    while clean text stays not-garbled (existing bulk checks unweakened)."""
    moji = "كtابcجديدxمادةyنص عربي سليم شروط التأمين " * 5
    assert check_garble(_flatten_tree_text([{"node_id": "1", "title": "", "text": moji}]), expected_script=None, profile=BULK_PROFILE) is True
    assert check_garble(moji, expected_script=None, profile=FLAT_MARKDOWN_PROFILE) is True
    # regression guard: plain English still not garbled
    clean = "This is a perfectly normal paragraph about insurance terms."
    assert check_garble(_flatten_tree_text([{"node_id": "1", "title": "S", "text": clean}]), expected_script=None, profile=BULK_PROFILE) is False
    assert check_garble(clean, expected_script=None, profile=FLAT_MARKDOWN_PROFILE) is False


def test_d8_reordered_tree_still_fails_validate():
    """HR5: adding D8's OR must not let any previously-rejected tree pass. A
    mojibake tree is rejected by validate_tree via the garbling reason."""
    moji = "كtابcجديدxمادةyنص عربي سليم شروط التأمين بوليصة تغطية " * 5
    # depth>=2 and node_count>=3 so the ONLY remaining gate is garbling.
    tree = [
        {
            "node_id": "1",
            "title": "root",
            "text": "",
            "start_index": 1,
            "nodes": [
                {"node_id": "1a", "title": "a", "text": moji, "start_index": 2},
                {"node_id": "1b", "title": "b", "text": "more text", "start_index": 3},
            ],
        }
    ]
    ok, reason = validate_tree(tree)
    assert ok is False
    assert reason == "garbling"


# ── D9: table column-0 rowspan forward-fill ─────────────────────────────────


def test_d9_forward_fill_column_zero_only():
    """Empty column-0 cells inherit the last non-empty label; data columns keep
    their own empties (the anti-corruption invariant)."""
    rows = [
        ["Selbstbehalt", "Katze", "10%"],
        ["", "Hund", ""],
        ["", "Pferd", "20%"],
    ]
    _forward_fill_leading_column(rows)
    assert [r[0] for r in rows] == ["Selbstbehalt", "Selbstbehalt", "Selbstbehalt"]
    # data columns (index 1+) untouched — the empty in row 1 col 2 stays empty
    assert rows[1] == ["Selbstbehalt", "Hund", ""]


def test_d9_no_leading_value_leaves_empty():
    """A leading empty with no prior value stays empty (nothing to fill from)."""
    rows = [["", "x"], ["Label", "y"], ["", "z"]]
    _forward_fill_leading_column(rows)
    assert [r[0] for r in rows] == ["", "Label", "Label"]


def test_d9_wired_into_flat_parse_table():
    """_flat_parse_table forward-fills the merged label into both the structured
    rows and the verbalized row_records (e544d939 Katze/Selbstbehalt shape)."""
    lines = [
        "| Selbstbehalt | Tier | Satz |",
        "| --- | --- | --- |",
        "| Selbstbehalt | Katze | 10% |",
        "| | Hund | 15% |",
        "| | Pferd | 20% |",
    ]
    block, nxt = _flat_parse_table(lines, 0)
    assert nxt == 5
    data_rows = block["rows"][1:]
    assert [r[0] for r in data_rows] == ["Selbstbehalt", "Selbstbehalt", "Selbstbehalt"]
    # verbalized records carry the recovered label on every row
    assert all("Selbstbehalt: Selbstbehalt" in rec for rec in block["row_records"])
