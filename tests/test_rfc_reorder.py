# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""RFC-015 reorder-detection consolidated tests.

Merges test_rfc015_couple_d.py (D5a/D5b/D8/D9 — helpers.py oversized-leaf
splitting, garble detection, table forward-fill) and test_rfc015_d10.py
(preamble node synthesis) into a single suite, grouped by the production
function each class exercises.

All tests use synthetic / property-based inputs. The doc-specific validating
trees named in the RFC (6147c7d7, 8cfeca9a, 92eebefa, e544d939, 722eb392, ...)
live only in MinIO `processed/*.json` and are NOT in the repo, so per this
repo's no-fabrication rule they are exercised here via minimal synthetic
reproductions of the SHAPE each fix targets — never invented copies of the
real stored artifacts.

HR5 posture: every assertion below confirms a change is additive/tightening —
D5a/D5b recover MORE real structure (never split without a genuine ordinal
run), D8 only ADDS garble FAILs (clean/legit text stays not-garbled), D9 only
fills column 0 (data columns untouched), and D10's preamble synthesis is
purely additive (no preamble / no heading => unchanged tree). None can flip a
previously-rejected tree to a silent PASS.
"""

from pageindex_mcp.helpers import (
    _OVERSIZED_ORDINAL_RE,
    BULK_PROFILE,
    FLAT_MARKDOWN_PROFILE,
    _flat_parse_table,
    _flatten_tree_text,
    _forward_fill_leading_column,
    _has_heading_markers,
    _ordinal_value,
    _synthesize_preamble_node,
    garble_prongs,
    split_oversized_leaf_nodes,
    validate_tree,
)

from tests._garble_compat import check_garble

_LONG_PREAMBLE = (
    "This policy covers the named rider while mounted on any horse owned, "
    "hired, or borrowed, including liability arising from third-party injury "
    "or property damage during riding lessons, competitions, or hacking."
)
assert len(_LONG_PREAMBLE.strip()) > 50


def _tree(structure):
    return {"structure": structure}


# ── D5b: `Schedule (N)` ordinal alternative ─────────────────────────────────


class TestOrdinalMarkerRecognition:
    """_OVERSIZED_ORDINAL_RE / _ordinal_value — the strictly-increasing-run guard."""

    def test_schedule_ordinal_recognized_and_valued(self):
        """`Schedule 3` / `Schedule (3)` are recognised as ordinal markers, and
        the captured number feeds the strictly-increasing-run guard."""
        assert _OVERSIZED_ORDINAL_RE.search("Schedule 3") is not None
        assert _OVERSIZED_ORDINAL_RE.search("Schedule (3)") is not None
        m = _OVERSIZED_ORDINAL_RE.search("Schedule (7)")
        assert m is not None
        assert _ordinal_value(m) == (7,)

    def test_existing_ordinal_alternatives_untouched(self):
        """D5b is additive — §/Article/Section/مادة all still match as before."""
        assert _OVERSIZED_ORDINAL_RE.search("§ 12") is not None
        assert _OVERSIZED_ORDINAL_RE.search("Article (9)") is not None
        assert _OVERSIZED_ORDINAL_RE.search("Section 4") is not None
        assert _OVERSIZED_ORDINAL_RE.search("المادة ٥") is not None


# ── D5a: size-gate decoupled from marker density ────────────────────────────


class TestHeadingMarkersAndOversizedSplit:
    """_has_heading_markers / split_oversized_leaf_nodes."""

    def test_has_heading_markers(self):
        """_has_heading_markers detects an ordinal run; plain prose has none."""
        assert _has_heading_markers("... Schedule 1 ... Schedule 2 ...") is True
        assert _has_heading_markers("just some ordinary paragraph text here") is False
        assert _has_heading_markers("") is False

    def test_small_leaf_with_markers_is_split(self):
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

    def test_small_leaf_without_ordinal_run_untouched(self):
        """D5a only widens, never forces — a small marker-free leaf, and a small
        leaf with only non-monotonic cross-refs (no increasing run of
        min_segments), are both left intact by the LIS guard."""
        plain_tree = [{"node_id": "n1", "title": "root", "text": "plain short body", "nodes": []}]
        split_oversized_leaf_nodes(plain_tree, max_chars=50000, min_segments=3)
        assert plain_tree[0]["nodes"] == []
        assert plain_tree[0]["text"] == "plain short body"

        cross_ref_text = "see Article 9 above, and Article 2 earlier, per Article 5."
        cross_ref_tree = [{"node_id": "n1", "title": "root", "text": cross_ref_text, "nodes": []}]
        split_oversized_leaf_nodes(cross_ref_tree, max_chars=50000, min_segments=3)
        assert cross_ref_tree[0]["nodes"] == []


# ── D8: sparse mixed-script mojibake detection ──────────────────────────────


class TestSparseMojibakeDetection:
    """garble_prongs / check_garble / validate_tree."""

    def test_glued_mojibake_flagged(self):
        """Latin fragments glued into Arabic (no spaces) → garbled."""
        moji = "كtابcجديدxمادةyنص عربي سليم شروط التأمين " * 5
        assert len(moji) > 100
        assert "sparse_mojibake" in garble_prongs(moji, original_text=moji)

    def test_clean_and_short_text_not_flagged(self):
        """Clean multi-word Arabic prose must NOT be flagged (space is not
        'glued' — the calibration anchor that would break under a literal \\x20
        class), and text under the 100-char length gate is excluded regardless
        of content."""
        clean_ar = "هذا نص عربي سليم تماما عن شروط التأمين والتغطية القانونية اليوم " * 2
        assert len(clean_ar) > 100
        assert "sparse_mojibake" not in garble_prongs(clean_ar, original_text=clean_ar)
        assert "sparse_mojibake" not in garble_prongs("كtابcمادة", original_text="كtابcمادة")

    def test_transliterated_names_not_flagged(self):
        """Space-separated Latin names among Arabic (b1a72fb2 class) → not flagged."""
        translit = "المدير Ahmed Hassan وقع العقد مع Mohamed Ali في مدينة القاهرة اليوم " * 2
        assert "sparse_mojibake" not in garble_prongs(translit, original_text=translit)

    def test_wired_into_garble_gates_additively(self):
        """The mojibake signal reaches both tree-bulk and flat-markdown garble
        paths, while clean text stays not-garbled (existing bulk checks
        unweakened)."""
        moji = "كtابcجديدxمادةyنص عربي سليم شروط التأمين " * 5
        assert (
            check_garble(
                _flatten_tree_text([{"node_id": "1", "title": "", "text": moji}]),
                expected_script=None,
                profile=BULK_PROFILE,
            )
            is True
        )
        assert check_garble(moji, expected_script=None, profile=FLAT_MARKDOWN_PROFILE) is True
        clean = "This is a perfectly normal paragraph about insurance terms."
        assert (
            check_garble(
                _flatten_tree_text([{"node_id": "1", "title": "S", "text": clean}]),
                expected_script=None,
                profile=BULK_PROFILE,
            )
            is False
        )
        assert check_garble(clean, expected_script=None, profile=FLAT_MARKDOWN_PROFILE) is False

    def test_reordered_tree_still_fails_validate(self):
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


class TestForwardFillColumnZero:
    """_forward_fill_leading_column / _flat_parse_table."""

    def test_forward_fill_column_zero_only(self):
        """Empty column-0 cells inherit the last non-empty label; data columns
        keep their own empties (the anti-corruption invariant)."""
        rows = [
            ["Selbstbehalt", "Katze", "10%"],
            ["", "Hund", ""],
            ["", "Pferd", "20%"],
        ]
        _forward_fill_leading_column(rows)
        assert [r[0] for r in rows] == ["Selbstbehalt", "Selbstbehalt", "Selbstbehalt"]
        # data columns (index 1+) untouched — the empty in row 1 col 2 stays empty
        assert rows[1] == ["Selbstbehalt", "Hund", ""]

    def test_no_leading_value_leaves_empty(self):
        """A leading empty with no prior value stays empty (nothing to fill from)."""
        rows = [["", "x"], ["Label", "y"], ["", "z"]]
        _forward_fill_leading_column(rows)
        assert [r[0] for r in rows] == ["", "Label", "Label"]

    def test_wired_into_flat_parse_table(self):
        """_flat_parse_table forward-fills the merged label into both the
        structured rows and the verbalized row_records (e544d939
        Katze/Selbstbehalt shape)."""
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


# ── D10: preamble node synthesis ────────────────────────────────────────────


class TestPreambleNodeSynthesis:
    """_synthesize_preamble_node — the vendored fork's tree-builder silently
    drops body text preceding the first heading (722eb392 GHV Reitlehrer
    Haftpflicht "who is covered" clause)."""

    def test_preamble_over_threshold_synthesizes_node_at_index_0(self):
        md_text = f"{_LONG_PREAMBLE}\n\n## Section 1 - Scope of Cover\n\nBody text here.\n"
        original_node = {
            "title": "Section 1 - Scope of Cover",
            "text": "Body text here.",
            "nodes": [],
        }
        tree = _tree([original_node])

        result = _synthesize_preamble_node(md_text, tree)

        assert len(result["structure"]) == 2
        preamble_node = result["structure"][0]
        assert preamble_node["title"] == "[Preamble]"
        assert preamble_node["text"] == f"{_LONG_PREAMBLE}\n"
        assert preamble_node["text"].strip() == _LONG_PREAMBLE
        assert preamble_node["nodes"] == []
        assert result["structure"][1] is original_node

    def test_trivial_preamble_at_or_under_threshold_is_not_synthesized(self):
        # Whitespace-only preamble (strips to 0 chars, under 50-char threshold) — preamble is not synthesized.
        md_text = "   \n\n## Section 1\n\nBody text.\n"
        original_node = {"title": "Section 1", "text": "Body text.", "nodes": []}
        tree = _tree([original_node])

        result = _synthesize_preamble_node(md_text, tree)

        assert len(result["structure"]) == 1
        assert result["structure"][0] is original_node

    def test_no_synthesis_when_no_preamble_or_no_heading(self):
        """Purely additive: a document whose first heading is already at line 1
        (no preamble), or that has no heading at all, gets no new node and an
        unchanged tree."""
        md_text_no_preamble = "## Section 1 - Scope of Cover\n\nBody text here.\n"
        original_node = {
            "title": "Section 1 - Scope of Cover",
            "text": "Body text here.",
            "nodes": [],
        }
        tree = _tree([original_node])
        result = _synthesize_preamble_node(md_text_no_preamble, tree)
        assert result["structure"] == [original_node]
        assert len(result["structure"]) == 1

        md_text_no_heading = (
            f"{_LONG_PREAMBLE}\n\nMore plain prose with no markdown heading at all.\n"
        )
        flat_node = {"title": "flat", "text": md_text_no_heading, "nodes": []}
        flat_tree = _tree([flat_node])
        result2 = _synthesize_preamble_node(md_text_no_heading, flat_tree)
        assert result2["structure"] == [flat_node]
        assert len(result2["structure"]) == 1

    def test_empty_or_missing_structure_handled_gracefully(self):
        assert _synthesize_preamble_node("", {"structure": []}) == {"structure": []}
        assert _synthesize_preamble_node(f"{_LONG_PREAMBLE}\n\n## H\n", {}) == {}
        assert _synthesize_preamble_node(f"{_LONG_PREAMBLE}\n\n## H\n", {"structure": None}) == {
            "structure": None
        }

    def test_synthesized_node_has_expected_bounds(self):
        md_text = f"{_LONG_PREAMBLE}\n\n## Section 1\n\nBody.\n"
        tree = _tree([{"title": "Section 1", "text": "Body.", "nodes": []}])

        result = _synthesize_preamble_node(md_text, tree)
        preamble_node = result["structure"][0]

        assert preamble_node["start_index"] == 0
        # First heading line is at index 2 (0-indexed: preamble line, blank line, heading).
        assert preamble_node["end_index"] == 1
        assert preamble_node["node_id"] == "preamble"
