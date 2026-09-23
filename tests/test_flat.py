# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Flat document view, block/node text measurement, flat verdicts, and outline tests.

Consolidated (ICR-97 test-budget reduction): absorbs test_zone4_measurement.py,
test_d6_flat_verdicts.py and test_outline.py.  Per-row parametrize tables are
collapsed into table-driven tests that assert the whole table and name every
offending row.
"""

from __future__ import annotations

import os
import re
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pageindex_mcp.client import CustomPageIndexClient, apply_image_ext_content_class_override
from pageindex_mcp.client import images as _img
from pageindex_mcp.client import indexer as _idx
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
from pageindex_mcp.helpers import (
    BULK_PROFILE,
    TreeSignals,
    _classify_image_verdict,
    _flat_block_primary_text,
    _flat_search_text,
    _flatten_tree_text,
    _node_char_count,
    _node_text_parts,
    classify_verdict,
    flat_doc_view,
    split_oversized_leaf_nodes,
)
from pageindex_mcp.helpers.flat import BlockTextPurpose, block_text, doc_text
from pageindex_mcp.helpers.types import (
    LowQualityTreeError,
    TreeDefect,
    TreeGateResult,
    VerdictThresholds,
)
from pageindex_mcp.helpers.verdict import compute_verdict, evaluate_gates
from tests._garble_compat import check_garble


def _report(failures: list[str], what: str) -> None:
    """Assert once, naming every offending row."""
    assert not failures, f"{what}: {len(failures)} row(s) failed:\n  " + "\n  ".join(failures)


@pytest.fixture()
def th():
    return VerdictThresholds.from_env()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_flat_data(
    *,
    blocks: list[dict] | None = None,
    row_records: list[str] | None = None,
    content_class: str = "flat_prose",
) -> dict:
    """Build a minimal flat_meta dict as _persist_flat_result would."""
    data: dict = {
        "doc_name": "test.pdf",
        "content_class": content_class,
        "blocks": blocks or [],
        "doc_description": "test doc",
    }
    if row_records is not None:
        data["row_records"] = row_records
    return data


def _table_blocks() -> list[dict]:
    return [
        {"role": "prose", "text": "Introduction paragraph."},
        {"role": "table", "row_records": ["col1 | col2", "a | b"]},
        {"role": "prose", "text": "Middle paragraph."},
        {"role": "table", "row_records": ["x | y", "1 | 2"]},
    ]


# ===========================================================================
# flat_doc_view
# ===========================================================================


class TestFlatDocViewRowRecords:
    """A pre-aggregated 'row_records' key (written by _persist_flat_result
    after the Zone 4.7 fix) is used verbatim; without it, flat_doc_view falls
    back to deriving row_records from blocks (pre-fix behavior).  Both paths
    must agree."""

    def test_pre_aggregated_used_verbatim(self):
        cases = [
            ("populated", ["col1 | col2", "a | b", "x | y", "1 | 2"]),
            ("explicit_empty", []),  # no tables -- must NOT fall back to blocks
            ("order_preserved", ["first", "second", "third"]),
        ]
        failures = []
        for label, pre_agg in cases:
            result = flat_doc_view(_make_flat_data(blocks=_table_blocks(), row_records=pre_agg))
            if result is None:
                failures.append(f"{label}: flat_doc_view returned None")
            elif result["row_records"] != pre_agg:
                failures.append(f"{label}: row_records={result['row_records']!r}, expected {pre_agg!r}")
        _report(failures, "pre-aggregated row_records")

    def test_fallback_derives_from_table_blocks(self):
        cases = [
            ("table_blocks", _table_blocks(), ["col1 | col2", "a | b", "x | y", "1 | 2"]),
            ("no_table_blocks", [{"role": "prose", "text": "Just text."}], []),
            ("empty_blocks", [], []),
            # malformed row_records=None must not crash the derivation
            ("none_row_records", [{"role": "table", "row_records": None}], []),
        ]
        failures = []
        for label, blocks, expected in cases:
            data = _make_flat_data(blocks=blocks)
            assert "row_records" not in data
            result = flat_doc_view(data)
            if result is None:
                failures.append(f"{label}: flat_doc_view returned None")
            elif result["row_records"] != expected:
                failures.append(f"{label}: row_records={result['row_records']!r}, expected {expected!r}")
        _report(failures, "row_records fallback derivation")

    def test_both_paths_produce_identical_output(self):
        cases = [
            ("simple", _table_blocks(), ["col1 | col2", "a | b", "x | y", "1 | 2"]),
            (
                "mixed_blocks",
                [
                    {"role": "heading", "text": "# Title"},
                    {"role": "table", "row_records": ["h1 | h2", "v1 | v2"]},
                    {"role": "image", "ocr_text": "scanned"},
                    {"role": "table", "row_records": ["a", "b", "c"]},
                    {"role": "prose", "text": "conclusion"},
                ],
                ["h1 | h2", "v1 | v2", "a", "b", "c"],
            ),
        ]
        failures = []
        for label, blocks, expected in cases:
            pre = flat_doc_view(_make_flat_data(blocks=blocks, row_records=expected))
            fb = flat_doc_view(_make_flat_data(blocks=blocks))
            if pre is None or fb is None:
                failures.append(f"{label}: flat_doc_view returned None")
            elif pre["row_records"] != fb["row_records"]:
                failures.append(
                    f"{label}: pre-aggregated {pre['row_records']!r} != fallback {fb['row_records']!r}"
                )
        _report(failures, "flat_doc_view path equivalence")


class TestFlatDocViewBoundary:
    """Edge cases and non-flat document handling."""

    def test_response_shape_and_non_flat_docs(self):
        # A tree document (no content_class) is not a flat doc.
        assert (
            flat_doc_view(
                {"doc_name": "tree.pdf", "structure": [{"node_id": "n1", "title": "A", "text": "t"}]}
            )
            is None
        )

        result = flat_doc_view(_make_flat_data(blocks=[{"role": "prose", "text": "hi"}]))
        assert result is not None
        assert set(result.keys()) == {
            "doc_name",
            "content_class",
            "blocks",
            "row_records",
            "structure",
            "doc_description",
        }
        # Flat docs have no tree structure; always an empty list.
        assert flat_doc_view(_make_flat_data(blocks=[]))["structure"] == []

        # doc_name falls back to the filename key.
        fallback = flat_doc_view(
            {"filename": "fallback.pdf", "content_class": "flat_prose", "blocks": []}
        )
        assert fallback is not None and fallback["doc_name"] == "fallback.pdf"


# ===========================================================================
# _flat_block_primary_text -- every block shape
# ===========================================================================


class TestFlatBlockPrimaryText:
    """_flat_block_primary_text must handle every block role, returning
    primary document text.  Table blocks use row_records (D2/RFC-041),
    falling back to pipe-joined headers when there are no data rows
    (Zone-9), and image blocks contribute ocr_text (D6/RFC-046) so the flat
    garble gate and flat_char_count see image content."""

    # (label, block, expected)
    _CASES = [
        ("prose", {"role": "prose", "text": "Hello world"}, "Hello world"),
        ("paragraph_role", {"role": "paragraph", "text": "hello world"}, "hello world"),
        ("heading", {"role": "heading", "text": "# Section Title"}, "# Section Title"),
        ("no_role", {"text": "plain text"}, "plain text"),
        ("prose_empty_text", {"role": "prose", "text": ""}, ""),
        ("empty_block", {}, ""),
        (
            "table_row_records",
            {"role": "table", "row_records": ["col1 | col2", "a | b"]},
            "col1 | col2\na | b",
        ),
        ("table_empty_row_records", {"role": "table", "row_records": []}, ""),
        ("table_no_keys", {"role": "table"}, ""),
        (
            "table_text_ignored_for_row_records",
            {"role": "table", "text": "should be ignored", "row_records": ["r1", "r2"]},
            "r1\nr2",
        ),
        (
            "table_headers_only",
            {"role": "table", "headers": ["Name", "Age", "City"], "row_records": []},
            "Name | Age | City",
        ),
        (
            "table_headers_no_row_records_key",
            {"role": "table", "headers": ["Col A", "Col B"]},
            "Col A | Col B",
        ),
        (
            "table_headers_none_row_records",
            {"role": "table", "headers": ["X", "Y"], "row_records": None},
            "X | Y",
        ),
        (
            "table_headers_falsy_filtered",
            {"role": "table", "headers": ["A", "", "C", None], "row_records": []},
            "A | C",
        ),
        (
            "table_headers_falsy_filtered_2",
            {"role": "table", "headers": ["A", None, "", "B"], "row_records": []},
            "A | B",
        ),
        (
            "table_row_records_beat_headers",
            {"role": "table", "headers": ["H1", "H2"], "row_records": ["data1 | data2"]},
            "data1 | data2",
        ),
        ("table_no_headers_no_rows", {"role": "table", "headers": [], "row_records": []}, ""),
        (
            "image_with_ocr",
            {
                "role": "image",
                "text": "",
                "ocr_text": "OCR content",
                "description": "A chart showing data",
            },
            "OCR content",
        ),
        ("image_without_ocr", {"role": "image", "text": ""}, ""),
    ]

    def test_primary_text_table(self):
        failures = []
        for label, block, expected in self._CASES:
            got = _flat_block_primary_text(dict(block))
            if got != expected:
                failures.append(f"{label}: got {got!r}, expected {expected!r}")
        _report(failures, "_flat_block_primary_text")


# ===========================================================================
# _flat_search_text -- role-aware retrieval text
# ===========================================================================


class TestFlatSearchText:
    """_flat_search_text renders role-aware retrieval text: a SUPERSET of
    primary text that adds OCR/description enrichment for image blocks and
    reads row_records (never 'text') for table blocks."""

    def test_every_role_contributes_its_own_field(self):
        cases = [
            ("image_ocr", [{"role": "image", "ocr_text": "OCR scanned text"}], ["OCR scanned text"]),
            (
                "image_description",
                [{"role": "image", "description": "Chart showing revenue"}],
                ["Chart showing revenue"],
            ),
            (
                "image_both",
                [{"role": "image", "ocr_text": "OCR text here", "description": "Desc here"}],
                ["OCR text here", "Desc here"],
            ),
            (
                "table_row_records",
                [{"role": "table", "row_records": ["a | b", "c | d"]}],
                ["a | b", "c | d"],
            ),
            ("prose", [{"role": "prose", "text": "Hello world"}], ["Hello world"]),
            (
                "mixed_doc_all_roles",
                [
                    {"role": "prose", "text": "Introduction text."},
                    {"role": "table", "row_records": ["A | B", "1 | 2"]},
                    {"role": "image", "ocr_text": "chart OCR", "description": "pie chart"},
                ],
                ["Introduction text.", "A | B", "1 | 2", "chart OCR", "pie chart"],
            ),
        ]
        failures = []
        for label, blocks, expected_substrings in cases:
            result = _flat_search_text({"blocks": blocks})
            for needle in expected_substrings:
                if needle not in result:
                    failures.append(f"{label}: missing {needle!r} in search text")
        _report(failures, "_flat_search_text role coverage")

    def test_text_key_ignored_for_table_and_image_plus_edge_cases(self):
        table = _flat_search_text(
            {"blocks": [{"role": "table", "text": "should NOT appear", "row_records": ["actual | data"]}]}
        )
        assert "actual | data" in table
        assert "should NOT appear" not in table

        image = _flat_search_text(
            {"blocks": [{"role": "image", "text": "should be ignored", "ocr_text": "OCR result"}]}
        )
        assert "OCR result" in image

        # Legacy top-level row_records are appended, but deduplicated against
        # anything already contributed by a block.
        assert "extra row" in _flat_search_text({"blocks": [], "row_records": ["extra row"]})
        deduped = _flat_search_text(
            {
                "blocks": [{"role": "table", "row_records": ["shared row"]}],
                "row_records": ["shared row"],
            }
        )
        assert deduped.count("shared row") == 1

        assert _flat_search_text({"blocks": []}) == ""
        assert _flat_search_text({"blocks": None}) == ""
        assert _flat_search_text({}) == ""


# ===========================================================================
# Flat structure synthesis + verdicts
# ===========================================================================


def _tree_garble(nodes, expected_script=None):
    """Test helper: replaces deleted _tree_is_garbled wrapper."""
    if not nodes:
        return False
    return check_garble(
        _flatten_tree_text(nodes),
        expected_script=expected_script,
        profile=BULK_PROFILE,
    )


def _synthesize_flat_structure(flat_structure: list, blocks: list) -> list:
    # B1+B3 (RFC-022): mirrors client.py:1102-1107.
    if not flat_structure and blocks:
        flat_structure = [
            {"title": "", "text": _flat_block_primary_text(b)}
            for b in blocks
            if _flat_block_primary_text(b).strip()
        ]
    return flat_structure


def _single_leaf_tree(size: int = 1000) -> list:
    """Three nodes, one dominant leaf -> max_leaf_ratio > 0.75 (hard-FAIL threshold).
    D1 requires node_count >= 3 for image-enrichment exception."""
    return [
        {"title": "", "text": "x" * size, "nodes": []},
        {"title": "", "text": "y" * 10, "nodes": []},
        {"title": "", "text": "z" * 10, "nodes": []},
    ]


def _multi_node_tree() -> list:
    """Three children -> max_leaf_ratio ~0.60 (below 0.75 ceiling, above 0.30 pass).
    D1 requires node_count >= 3 for image-enrichment exception."""
    return [
        {"node_id": "1", "title": "A", "text": "x" * 600, "nodes": []},
        {"node_id": "2", "title": "B", "text": "y" * 400, "nodes": []},
        {"node_id": "3", "title": "C", "text": "z" * 20, "nodes": []},
    ]


def _table_heavy_doc_blocks() -> list:
    """Approximates Doc 3 (GHV-TKV-Tarif.pdf): 3 table blocks with no "text"
    key, content living entirely in row_records."""
    return [
        {
            "role": "table",
            "row_records": [f"Tarif row {i}: Leistung {i} EUR {i * 10}" for i in range(20)],
        },
        {
            "role": "table",
            "row_records": [f"Beitrag row {i}: Stufe {i} Praemie {i * 5}" for i in range(20)],
        },
        {
            "role": "table",
            "row_records": [f"Selbstbeteiligung row {i}: Wert {i}" for i in range(20)],
        },
    ]


class TestSynthesizeFlatStructure:
    """Property 1 (B1) + Property 5 (B3): synthetic structure for flat docs
    with structure=[] and non-empty blocks, including table-aware content
    measurement so table blocks aren't starved of their row_records text."""

    def test_synthetic_structure_generated_and_promotes_cat_b(self):
        blocks = [{"text": "alpha content"}, {"text": "beta content"}, {"text": "gamma content"}]
        structure = _synthesize_flat_structure([], blocks)
        assert len(structure) == len(blocks)
        assert all(node["text"] for node in structure)

        # RFC-023 D4 added a MIN_FLAT_PROMOTION_CHARS=500 content-quality
        # guard to the cat_b promotion path (below 500 chars,
        # small_doc_promoted fires instead), and cat_b also requires
        # max_leaf_ratio < CATEGORY_BC_PROMOTION_THRESHOLD (0.17), which with
        # equal-sized blocks needs at least 6 of them (1/6 < 0.17).
        blocks = [
            {
                "text": f"block number {i} has some additional prose content padding "
                "here to exceed the minimum threshold with extra padding words appended"
            }
            for i in range(6)
        ]
        structure = _synthesize_flat_structure([], blocks)
        assert len(structure) == 6
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert verdict == "PASS"
        assert reason in ("structural_pass", "cat_b_promoted")

    def test_empty_structure_and_empty_blocks_yields_zero_content_fail(self):
        # RFC-026 D0: an empty structure is now an unconditional zero_content
        # FAIL (the hard floor this doc-shape used to slip past), not MARGINAL.
        structure = _synthesize_flat_structure([], [])
        assert structure == []
        assert classify_verdict(structure, "flat_prose", None) == ("FAIL", "zero_content")

    def test_non_empty_garbled_structure_still_detected(self):
        structure = _synthesize_flat_structure([], [{"text": "\x00" * 200}])
        assert structure
        assert _tree_garble(structure) is True
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert verdict == "FAIL" or (verdict == "MARGINAL" and "garbl" in reason)

    def test_table_heavy_doc_is_not_content_starved(self):
        """Doc-3 shape: all content lives in row_records.  Every block must
        produce an enriched node, the total must clear the content floor, and
        the resulting verdict must not be garbling-driven (the pre-fix failure
        mode was content starvation reading as garbling)."""
        blocks = _table_heavy_doc_blocks()
        structure = _synthesize_flat_structure([], blocks)
        assert len(structure) == len(blocks) == 3
        assert len([node for node in structure if node["text"].strip()]) == 3
        assert sum(len(node["text"]) for node in structure) > 375

        verdict, reason = classify_verdict(structure, "flat_table", None)
        assert "garbling" not in reason
        assert verdict != "FAIL"


class TestImageStandaloneRoutingAndVerdict:
    """Property 3 (B2): a file whose extension is in _IMAGE_EXTS gets
    content_class="image_standalone" regardless of block-role composition,
    and _classify_image_verdict decides PASS/FAIL from the enrichment ratio."""

    def test_extension_override_and_image_verdict_boundaries(self, monkeypatch):
        assert apply_image_ext_content_class_override(".jpg", "flat_prose") == "image_standalone"
        assert _classify_image_verdict(1.0) == ("PASS", "image_enrichment_complete")
        assert _classify_image_verdict(None) == ("FAIL", "no_image_enrichment")

        # With the pipeline disabled the override is a no-op, but the
        # enrichment rescue gate (B2-B) is defense-in-depth and still promotes
        # a well-enriched flat doc whose max_leaf_ratio is under the 0.75
        # hard-FAIL ceiling.
        monkeypatch.setattr(_img, "_IMAGE_STANDALONE_PIPELINE_ENABLED", False)
        content_class = apply_image_ext_content_class_override(".jpg", "flat_prose")
        assert content_class == "flat_prose"
        verdict, reason = classify_verdict(
            _multi_node_tree(), content_class, None, image_enrichment_ratio=0.9
        )
        assert (verdict, reason) == ("PASS", "image_enrichment_promoted")


class TestImageEnrichmentGateOrdering:
    """Property 4 (B2): the image-enrichment rescue runs BEFORE the
    max_leaf_ratio gate, because flat image-enriched documents are expected
    to have single-leaf structure -- without it every image-enriched flat
    doc would hard-FAIL on structure alone.  Without an enrichment ratio the
    same tree still hard-FAILs."""

    def test_rescue_precedes_max_leaf_ratio_only_when_enriched(self):
        failures = []
        cases = [
            ("single_leaf_enriched", _single_leaf_tree(), 0.9, "PASS", "image_enrichment_promoted"),
            ("multi_node_enriched", _multi_node_tree(), 0.9, "PASS", "image_enrichment_promoted"),
            ("single_leaf_unenriched", _single_leaf_tree(), None, "FAIL", None),
        ]
        for label, structure, ratio, exp_verdict, exp_reason in cases:
            verdict, reason = classify_verdict(
                structure, "flat_prose", None, image_enrichment_ratio=ratio
            )
            if verdict != exp_verdict:
                failures.append(f"{label}: verdict={verdict}, expected {exp_verdict}")
            if exp_reason is None:
                if not reason.startswith("max_leaf_ratio="):
                    failures.append(f"{label}: reason={reason!r}, expected max_leaf_ratio=...")
            elif reason != exp_reason:
                failures.append(f"{label}: reason={reason!r}, expected {exp_reason!r}")
        _report(failures, "image-enrichment gate ordering")


class TestIndexerFlatCharCountWiring:
    """indexer.py's flat_char_count computation and flat structure synthesis
    use _flat_block_primary_text, not a naive block.get('text', '')."""

    def test_flat_char_count_counts_table_and_header_only_content(self):
        blocks = [
            {"role": "prose", "text": "Hello world"},
            {"role": "table", "row_records": ["col1 | col2", "a | b"]},
        ]
        # Mirrors indexer.py:_persist_flat_result (line 1103).
        flat_char_count = sum(len(_flat_block_primary_text(b)) for b in blocks)
        assert flat_char_count == len("Hello world") + len("col1 | col2\na | b")

        header_only = [{"role": "table", "headers": ["Name", "Value"], "row_records": []}]
        assert sum(len(_flat_block_primary_text(b)) for b in header_only) > 0, (
            "Header-only table must contribute to flat_char_count (Zone-9 fix)"
        )

        # Regression proof: naive access sees zero for table blocks.
        naive_blocks = [{"role": "table", "row_records": ["data1", "data2"]}]
        assert sum(len(b.get("text", "")) for b in naive_blocks) == 0
        assert sum(len(_flat_block_primary_text(b)) for b in naive_blocks) > 0

    def test_flat_structure_synthesis_uses_primary_text(self):
        """D6 (RFC-046): image blocks with ocr_text contribute nodes; image
        blocks without it are still excluded."""
        blocks = [
            {"role": "prose", "text": "Intro paragraph."},
            {"role": "table", "row_records": ["A | B", "1 | 2"]},
            {"role": "image", "ocr_text": "chart"},
            {"role": "image"},
        ]
        flat_structure = [
            {"title": "", "text": _flat_block_primary_text(b)}
            for b in blocks
            if _flat_block_primary_text(b).strip()
        ]
        assert [n["text"] for n in flat_structure] == [
            "Intro paragraph.",
            "A | B\n1 | 2",
            "chart",
        ]


# ===========================================================================
# D2 (RFC-041): block_text / doc_text unification -- Property 2
# ===========================================================================


class TestBlockTextPurposes:
    """Property 2: block_text with each purpose for table, paragraph and
    image blocks (and bare tree nodes, which carry no 'role')."""

    # (label, block, expected text for EVERY purpose)
    _PURPOSE_INVARIANT_CASES = [
        ("paragraph", {"role": "prose", "text": "Hello world"}, "Hello world"),
        ("table_row_records", {"role": "table", "row_records": ["a | b", "c | d"]}, "a | b\nc | d"),
        (
            "table_header_only",
            {"role": "table", "headers": ["Name", "Age"], "row_records": []},
            "Name | Age",
        ),
        (
            "table_consistent_base_text",
            {"role": "table", "row_records": ["name | age", "alice | 30"]},
            "name | age\nalice | 30",
        ),
        (
            "table_header_only_consistent",
            {"role": "table", "headers": ["X", "Y", "Z"], "row_records": []},
            "X | Y | Z",
        ),
        ("image_without_ocr", {"role": "image"}, ""),
    ]

    def test_text_is_purpose_invariant_for_non_image_content(self):
        """Table/paragraph text is identical across every accessor path; an
        image with no enrichment is empty for every purpose."""
        failures = []
        for label, block, expected in self._PURPOSE_INVARIANT_CASES:
            for purpose in BlockTextPurpose:
                got = block_text(dict(block), purpose)
                if got != expected:
                    failures.append(f"{label}/{purpose.name}: got {got!r}, expected {expected!r}")
        _report(failures, "block_text purpose invariance")

    def test_image_enrichment_visibility_per_purpose(self):
        """D6 (RFC-046): SEARCH sees ocr_text AND description; GARBLE_CHECK and
        CHAR_COUNT see ocr_text only (so the flat garble gate and
        flat_char_count see image content); DISPLAY sees neither."""
        block = {"role": "image", "ocr_text": "scanned text", "description": "a photo"}
        expectations = [
            (BlockTextPurpose.SEARCH, ["scanned text", "a photo"], []),
            (BlockTextPurpose.GARBLE_CHECK, ["scanned text"], ["a photo"]),
            (BlockTextPurpose.CHAR_COUNT, ["scanned text"], ["a photo"]),
            (BlockTextPurpose.DISPLAY, [], ["scanned text", "a photo"]),
        ]
        failures = []
        for purpose, present, absent in expectations:
            result = block_text(dict(block), purpose)
            for needle in present:
                if needle not in result:
                    failures.append(f"{purpose.name}: missing {needle!r}")
            for needle in absent:
                if needle in result:
                    failures.append(f"{purpose.name}: unexpectedly contains {needle!r}")
            if purpose is BlockTextPurpose.DISPLAY and result != "":
                failures.append(f"DISPLAY: expected '', got {result!r}")
        _report(failures, "image-block enrichment visibility")

    def test_table_shape_fallbacks_and_bare_tree_nodes(self):
        cases = [
            (
                "dict_row_records",
                {"role": "table", "row_records": [{"key": "premium", "value": "1200"}]},
                ["premium", "1200"],
            ),
            (
                "rows_without_row_records",
                {"role": "table", "headers": ["H1"], "rows": [["alpha", "bravo"]]},
                ["H1", "alpha", "bravo"],
            ),
            ("text_fallback", {"role": "table", "text": "fallback text"}, ["fallback text"]),
            ("tree_node_row_records", {"title": "T", "row_records": ["r1", "r2"]}, ["r1", "r2"]),
            ("tree_node_headers_only", {"title": "T", "headers": ["Col1", "Col2"]}, ["Col1 | Col2"]),
        ]
        failures = []
        for label, block, expected_substrings in cases:
            result = block_text(dict(block), BlockTextPurpose.CHAR_COUNT)
            for needle in expected_substrings:
                if needle not in result:
                    failures.append(f"{label}: missing {needle!r} in {result!r}")
        _report(failures, "block_text table/tree-node shapes")
        assert block_text({}, BlockTextPurpose.CHAR_COUNT) == ""


class TestDocText:
    """doc_text whole-document extraction."""

    def test_search_matches_flat_search_text_and_appends_legacy_rows(self):
        data = {
            "blocks": [
                {"role": "prose", "text": "Introduction"},
                {"role": "table", "row_records": ["a | b"]},
                {"role": "image", "ocr_text": "scanned"},
            ]
        }
        assert doc_text(data, BlockTextPurpose.SEARCH) == _flat_search_text(data)
        assert "legacy_row" in doc_text(
            {"blocks": [], "row_records": ["legacy_row"]}, BlockTextPurpose.SEARCH
        )
        assert doc_text({}, BlockTextPurpose.SEARCH) == ""
        assert doc_text({"blocks": []}, BlockTextPurpose.CHAR_COUNT) == ""

    def test_char_count_includes_image_ocr_display_does_not(self):
        data = {
            "blocks": [
                {"role": "prose", "text": "Body text"},
                {"role": "image", "ocr_text": "scanned chart"},
            ]
        }
        char_count = doc_text(data, BlockTextPurpose.CHAR_COUNT)
        assert "Body text" in char_count and "scanned chart" in char_count

        display = doc_text(data, BlockTextPurpose.DISPLAY)
        assert "Body text" in display and "scanned chart" not in display


class TestGarbleScoreRegression:
    """garble.py's internal flat-block caller produces the same scores after
    the block_text migration: a digit-noise table is reported, a clean one
    is not."""

    def test_flat_block_garble_check_uses_block_text(self):
        from pageindex_mcp.helpers.garble import (
            GarbleConfig,
            ScriptContext,
            _garble_check_flat_blocks,
        )

        ctx = ScriptContext(dominant_script="Latn", had_presentation_forms=False, source="test")
        cfg = GarbleConfig()

        garbled_blocks = [
            {"role": "prose", "text": "Clean prose text about insurance. " * 5},
            {"role": "table", "row_records": ["1234567890" * 60]},
        ]
        assert _garble_check_flat_blocks(garbled_blocks, script_context=ctx, config=cfg) is not None

        clean_blocks = [
            {
                "role": "table",
                "row_records": [
                    "Name | Premium | Deductible",
                    "Liability | 5000 | 500",
                    "Comprehensive | 3000 | 250",
                ],
            },
        ]
        assert not _garble_check_flat_blocks(clean_blocks, script_context=ctx, config=cfg)


# ===========================================================================
# Zone 4 -- tree-node measurement helpers (merged from test_zone4_measurement.py)
# ===========================================================================


class TestNodeTextParts:
    """_node_text_parts must extract all text-bearing content from tree nodes."""

    _CASES = [
        ("title_and_text", {"title": "Title", "text": "Body"}, ["Title", "Body"]),
        ("table_headers", {"headers": ["H1", "H2"]}, ["H1", "H2"]),
        ("row_records_strings", {"row_records": ["rec1", "rec2"]}, ["rec1", "rec2"]),
        ("row_records_dicts", {"row_records": [{"col": "val"}]}, ["val"]),
    ]

    def test_extracts_every_text_bearing_field(self):
        failures = []
        for label, node, expected in self._CASES:
            parts = _node_text_parts(dict(node))
            for needle in expected:
                if needle not in parts:
                    failures.append(f"{label}: {needle!r} missing from {parts!r}")
        _report(failures, "_node_text_parts")

        assert set(_node_text_parts({"rows": [["a", "b"], ["c", "d"]]})) == {"a", "b", "c", "d"}
        assert _node_text_parts({}) == []
        assert _node_text_parts({"headers": None, "rows": None, "row_records": None}) == []

    def test_node_char_count_sums_every_part(self):
        failures = []
        for label, node, expected in (
            ("title_and_text", {"title": "AB", "text": "CDE"}, 5),
            ("table_content", {"headers": ["H1"], "row_records": ["data"]}, 6),
            ("empty", {}, 0),
        ):
            got = _node_char_count(dict(node))
            if got != expected:
                failures.append(f"{label}: _node_char_count={got}, expected {expected}")
        _report(failures, "_node_char_count")


class TestNodeTextPartsSplitFlags:
    """D5 (RFC-047): the include_ocr_text / include_summary split, both on
    _node_text_parts and threaded through _flatten_tree_text."""

    NODE = {
        "title": "Title",
        "text": "Body",
        "ocr_text": "OCR content",
        "summary": "LLM summary",
    }

    def test_flag_combinations(self):
        cases = [
            ("ocr_only", True, False, ["OCR content"], ["LLM summary"]),
            ("summary_only", False, True, ["LLM summary"], ["OCR content"]),
            ("both_true", True, True, ["OCR content", "LLM summary"], []),
            ("both_false", False, False, ["Title", "Body"], ["OCR content", "LLM summary"]),
        ]
        failures = []
        for label, ocr, summary, present, absent in cases:
            parts = _node_text_parts(dict(self.NODE), include_ocr_text=ocr, include_summary=summary)
            for needle in present:
                if needle not in parts:
                    failures.append(f"{label}: missing {needle!r}")
            for needle in absent:
                if needle in parts:
                    failures.append(f"{label}: unexpectedly contains {needle!r}")
        _report(failures, "_node_text_parts split flags")

    def test_dedup_against_body_text(self):
        """A ocr_text/summary identical to the body must not be counted twice."""
        parts = _node_text_parts(
            {"text": "same", "ocr_text": "same", "summary": "different"},
            include_ocr_text=True,
            include_summary=True,
        )
        assert parts.count("same") == 1 and "different" in parts

        parts = _node_text_parts(
            {"text": "same", "ocr_text": "different", "summary": "same"},
            include_ocr_text=True,
            include_summary=True,
        )
        assert parts.count("same") == 1 and "different" in parts

    def test_flatten_tree_text_passes_flags_through(self):
        tree = [
            {
                "title": "Root",
                "text": "body",
                "ocr_text": "ocr here",
                "summary": "summary here",
                "nodes": [],
            }
        ]
        cases = [
            ("ocr_only", True, False, ["ocr here"], ["summary here"]),
            ("summary_only", False, True, ["summary here"], ["ocr here"]),
            ("neither", False, False, ["Root"], ["ocr here", "summary here"]),
        ]
        failures = []
        for label, ocr, summary, present, absent in cases:
            text = _flatten_tree_text(tree, include_ocr_text=ocr, include_summary=summary)
            for needle in present:
                if needle not in text:
                    failures.append(f"{label}: missing {needle!r}")
            for needle in absent:
                if needle in text:
                    failures.append(f"{label}: unexpectedly contains {needle!r}")
        _report(failures, "_flatten_tree_text split flags")

    def test_flat_text_corrected_includes_ocr_but_not_summary(self):
        """D5 (RFC-047): TreeSignals.flat_text_corrected includes OCR but not
        the LLM summary."""
        signals = TreeSignals.from_tree(
            [
                {
                    "title": "Page 1",
                    "text": "body text",
                    "ocr_text": "ocr content",
                    "summary": "llm summary text",
                    "nodes": [],
                }
            ]
        )
        assert "ocr content" in signals.flat_text_corrected
        assert "body text" in signals.flat_text_corrected
        assert "llm summary text" not in signals.flat_text_corrected


# ===========================================================================
# D6 (RFC-046): flat verdicts come from FLAT signals
# ===========================================================================


class TestFlatSignalsOverrideTreeSignals:
    """4.1: the flat_signals parameter overrides tree-derived signals on both
    evaluate_gates and compute_verdict; without it the tree gate's own
    signals are used."""

    def test_flat_signals_win_when_provided(self, th):
        flat_structure = [{"title": "", "text": "flat content " * 100}]
        flat_sig = TreeSignals.from_tree(
            flat_structure, expected_script=None, garble_threshold=th.garble_threshold
        )
        tree_structure = [
            {"title": "a", "text": "x" * 500},
            {"title": "b", "text": "y" * 500},
            {"title": "c", "text": "z" * 500},
        ]
        tree_gate = TreeGateResult(
            ok=True,
            defect=TreeDefect.OK,
            signals=TreeSignals.from_tree(
                tree_structure, expected_script=None, garble_threshold=th.garble_threshold
            ),
        )

        outcome = evaluate_gates(flat_structure, tree_gate, None, th, flat_signals=flat_sig)
        assert outcome.signals is flat_sig
        assert outcome.signals.node_count == 1

        result = compute_verdict(
            [
                {"title": "", "text": "content " * 200},
                {"title": "", "text": "more content " * 200},
                {"title": "", "text": "even more " * 200},
            ],
            "flat_prose",
            TreeGateResult(ok=True, defect=TreeDefect.OK),
            flat_signals=flat_sig,
        )
        assert result.signals is flat_sig

    def test_without_flat_signals_uses_validate_result(self, th):
        structure = [
            {"title": "a", "text": "x" * 500},
            {"title": "b", "text": "y" * 500},
        ]
        tree_sig = TreeSignals.from_tree(
            structure, expected_script=None, garble_threshold=th.garble_threshold
        )
        tree_gate = TreeGateResult(ok=True, defect=TreeDefect.OK, signals=tree_sig)

        outcome = evaluate_gates(structure, tree_gate, None, th)
        assert outcome.signals is tree_sig


class TestFlatVerdictWithHighTreeRatio:
    """4.2: flat signals carry the FLAT max_leaf_ratio, not the tree's."""

    def test_flat_signals_carry_flat_max_leaf_ratio(self, th):
        flat_structure = [{"title": "", "text": ch * 1000} for ch in "ABCD"]
        flat_sig = TreeSignals.from_tree(
            flat_structure, expected_script=None, garble_threshold=th.garble_threshold
        )
        assert flat_sig.max_leaf_ratio == pytest.approx(0.25, abs=0.01)

        tree_sig = TreeSignals.from_tree(
            [{"title": "", "text": "X" * 8600}, {"title": "", "text": "Y" * 1400}],
            expected_script=None,
            garble_threshold=th.garble_threshold,
        )
        assert tree_sig.max_leaf_ratio == pytest.approx(0.86, abs=0.01)

        result = compute_verdict(
            flat_structure,
            "flat_prose",
            TreeGateResult(ok=True, defect=TreeDefect.OK, signals=tree_sig),
            flat_signals=flat_sig,
        )
        assert result.signals.max_leaf_ratio == pytest.approx(0.25, abs=0.01)


class TestNoDeadVerdictArgument:
    """Known trap (keep this test): once flat_signals is supplied, the
    `structure` argument is effectively DEAD -- the resolved signal source
    wins and the emitted signals/reasons describe it, not the structure that
    was passed in.  This pins the behaviour so no call site can quietly rely
    on `structure` being read on the flat route."""

    def test_flat_signals_are_used_not_structure(self, th):
        wrong_structure = [{"title": "", "text": "wrong"}]
        right_structure = [
            {"title": "", "text": "right " * 200},
            {"title": "", "text": "content " * 200},
            {"title": "", "text": "here " * 200},
        ]
        flat_sig = TreeSignals.from_tree(
            right_structure, expected_script=None, garble_threshold=th.garble_threshold
        )

        outcome = evaluate_gates(wrong_structure, None, None, th, flat_signals=flat_sig)
        assert outcome.signals is flat_sig
        assert outcome.signals.node_count == 3


# ===========================================================================
# PDF outline extraction + inference (merged from test_outline.py)
# ===========================================================================


def _pdf_with_outline(tmp_path, entries, n_pages=6):
    """Write a PDF with ``n_pages`` blank pages and a nested outline.

    ``entries``: list of ``(title, page_0based, is_child)`` -- an ``is_child``
    entry nests under the most recent top-level item."""
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


def test_read_pdf_outline_applies_one_based_offsets_in_outline_order(tmp_path):
    """A 2-level outline round-trips to 1-based level + 1-based page tuples in
    document (outline) order -- the offset the consumer depends on.  Order is
    preserved verbatim, NOT re-sorted by page, because section extents are
    computed by nesting (reading order)."""
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


def test_read_pdf_outline_without_usable_outline_returns_empty(tmp_path):
    """A single-bookmark outline yields no usable structural signal, and a PDF
    with no bookmarks at all yields none either -> ([], 0) in both cases, so
    the caller leaves the markdown flat and the gate rejects it legitimately
    (HR5)."""
    from PyPDF2 import PdfWriter

    solo = _pdf_with_outline(tmp_path, [("Solo", 0, False)])
    assert _read_pdf_outline(solo) == ([], 0)

    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(width=200, height=200)
    flat = tmp_path / "flat.pdf"
    with open(flat, "wb") as fh:
        w.write(fh)
    assert _read_pdf_outline(str(flat)) == ([], 0)


_HEAD = re.compile(r"^(#{1,6})[ \t]+(.*\S)[ \t]*$", re.MULTILINE)


def _headings(md: str) -> list[tuple[int, str]]:
    """[(level, title), ...] for every markdown heading, in document order."""
    return [(len(m.group(1)), m.group(2)) for m in _HEAD.finditer(md)]


def _md(*titles: str) -> str:
    """Build a flat (all-H1) markdown body with a blank line + body after each
    heading -- the shape ``_relevel_headings`` produces before outline recovery."""
    return "".join(f"# {t}\n\nbody of {t}\n\n" for t in titles)


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


def test_title_matches_requires_exact_or_substantial_substring():
    """Exact normalised equality matches; so does a substring when the shorter
    string is substantial (>= 8 alnum chars) -- tolerating Docling rendering a
    longer heading than the TOC title.  A short (<8 alnum) coincidental overlap
    is NOT a match, and empty never matches."""
    sec = _outline_norm("Besondere Bedingungen Katzen-Krankenversicherung")
    assert _title_matches(sec, sec) is True
    assert _title_matches(sec, _outline_norm("Besondere Bedingungen")) is True
    assert _title_matches(_outline_norm("Beitrag"), _outline_norm("Beginn")) is False
    assert _title_matches("", "anything") is False
    assert _title_matches("anything", "") is False


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


def test_repeated_identical_titles_are_kept_apart_by_page_deque():
    """Hundehalterhaftpflicht shape: the same 'Besondere Bedingungen ...' chapter
    title appears 3x at pages 5/13/21. The per-text page deque pops in document
    order so each rendered heading anchors its OWN page band (H1) with its content
    as H2 -- no collision, no zero-width band."""
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


def test_copage_nested_l1_l2_entries_do_not_collapse():
    """Tier-OP-Kranken shape (BLOCKER-2): an L1 section and its first L2 child
    start on the SAME page (3). Nesting-aware extents (end = next entry whose
    level <= current) keep the L1 reachable, so the rendered L1 title stays H1
    (NOT demoted to H2 by a zero-width band) and the L2 title becomes H2 with
    its content at H3."""
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


def test_degenerate_outlines_return_md_unchanged():
    """Cat D / degenerate inputs leave the markdown verbatim so the gate rejects
    them legitimately (HR5: the depth<2 threshold is never weakened):
    no usable outline; every rendered heading its own section anchor (no
    recovered depth); and body-only markdown with no headings at all."""
    failures = []

    md = _md("Leistungen", "Beitrag")
    if _apply_outline_levels(md, {}, [], total_pages=0) != md:
        failures.append("empty_toc: markdown was rewritten")

    md = _md("Alpha Section Title", "Beta Section Title")
    heading_pages = {
        _outline_norm("Alpha Section Title"): [2],
        _outline_norm("Beta Section Title"): [4],
    }
    toc = [(1, "Alpha Section Title", 2), (1, "Beta Section Title", 4)]
    if _apply_outline_levels(md, heading_pages, toc, total_pages=6) != md:
        failures.append("no_recovered_depth: markdown was rewritten")

    md = "just prose\n\nmore prose\n"
    if _apply_outline_levels(md, {}, [(1, "Alpha", 1), (1, "Beta", 2)], total_pages=3) != md:
        failures.append("no_headings: markdown was rewritten")

    _report(failures, "_apply_outline_levels degenerate inputs")


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


# --- depth inference ---------------------------------------------------------


def test_segment_label_components():
    """_segment_label tokenises numbering (Latin, Arabic-Indic and
    letter-spaced) and returns [] for a plain prose title."""
    cases = [
        ("latin_dotted", "A.1.1", ["A", "1", "1"]),
        ("prose_title", "Versicherte Personen", []),
        ("letter_spaced", "T e i l   A", ["A"]),
        ("arabic_indic_digit", "المادة ٩", ["9"]),
        ("alnum_run", "Abschnitt A1", ["A", "1"]),
    ]
    failures = []
    for label, title, expected in cases:
        got = _segment_label(title)
        if got != expected:
            failures.append(f"{label}: _segment_label({title!r})={got!r}, expected {expected!r}")
    _report(failures, "_segment_label")
    assert _collapse_spaced("T e i l   A") == "Teil A"


def test_split_alnum():
    failures = []
    for tok, expected in (("A1", ["A", "1"]), ("A(GB)1", ["A", "GB", "1"])):
        got = _split_alnum(tok)
        if got != expected:
            failures.append(f"_split_alnum({tok!r})={got!r}, expected {expected!r}")
    _report(failures, "_split_alnum")


def test_numbering_depth():
    failures = []
    for title, expected in (("المادة (9)", 2), ("A.1 Geltungsbereich", 2)):
        got = numbering_depth(title)
        if got != expected:
            failures.append(f"numbering_depth({title!r})={got}, expected {expected}")
    _report(failures, "numbering_depth")


def test_containment_depths_and_relevel():
    assert _containment_depths(["A", "A.1", "A.1.1", "Versicherte Personen"]) == [1, 2, 3, None]

    md = (
        "# A\n\nbody a\n\n# A.1\n\nbody a1\n\n"
        "# A.1.1\n\nbody a11\n\n# Versicherte Personen\n\nbody vp\n"
    )
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


# --- split_oversized_leaf_nodes ---------------------------------------------

_SMALL_MAX = 50


def _make_leaf(node_id, text):
    return {"title": "Root", "text": text, "nodes": [], "node_id": node_id}


def test_split_oversized_on_article_markers():
    """Arabic المادة (N) markers and English 'Article (N)' inline markers both
    split an oversized leaf into one child per article, preserving the full
    original text across parent + children."""
    preamble = "مقدمة " * 10 + "\n"
    body = (
        "المادة (1)\nنص المادة الأولى\n"
        "المادة (2)\nنص المادة الثانية\n"
        "المادة (3)\nنص المادة الثالثة\n"
    )
    text = preamble + body
    assert len(text) > _SMALL_MAX
    result = split_oversized_leaf_nodes([_make_leaf("root-1", text)], max_chars=_SMALL_MAX)
    assert len(result[0]["nodes"]) == 3
    assert result[0]["nodes"][0]["node_id"] == "root-1-s0"

    text = (
        "preamble. "
        "Article (1) the first provision states things. "
        "Article (2) the second provision continues. "
        "Article (3) the third provision concludes here."
    )
    assert len(text) > _SMALL_MAX
    result = split_oversized_leaf_nodes([_make_leaf("paren", text)], max_chars=_SMALL_MAX)
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
    result = split_oversized_leaf_nodes([_make_leaf("toc", text)], max_chars=_SMALL_MAX)
    assert result[0]["nodes"] == []


# ===========================================================================
# FLAT-03: post-validate flat routing inside CustomPageIndexClient.index()
# ===========================================================================
# RFC-004 Amendment 1 (D4'): validate_tree() itself is unchanged (HR5); the
# branch is on the *reason* it returns.  node_count<3 / depth<2 route to the
# flat success path when flat_doc_routing is on; garbling stays terminal.


def _flat_route_settings(flat_doc_routing: bool):
    return SimpleNamespace(
        openai_api_key="test-key",
        openai_base_url="https://api.openai.com/v1",
        azure_api_version=None,
        llm_model="gpt-test",
        minio_secure=False,
        minio_endpoint="localhost:9000",
        minio_bucket="pageindex",
        flat_doc_routing=flat_doc_routing,
        vlm_fallback=False,
        vlm_model="gpt-4.1",
        vlm_describe_images=False,
        pii_corpus=False,
    )


@pytest.fixture()
def flat_md_file():
    """A real on-disk markdown file so index() runs to the validate_tree branch."""
    fd, path = tempfile.mkstemp(suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("Just some flat prose with no headings whatsoever, clearly readable.\n")
    yield path
    if os.path.exists(path):
        os.unlink(path)


def _wire_flat_route(monkeypatch, *, gate_result, flat_doc_routing=True):
    """Patch every collaborator index() touches around the post-validate
    routing branch, so the only variables are the gate result and the
    flat_doc_routing kill-switch.  Persistence is mocked, which is what lets
    the tests assert what was NOT written."""
    fake_settings = _flat_route_settings(flat_doc_routing)
    monkeypatch.setattr(_idx, "settings", fake_settings)
    monkeypatch.setattr(_img, "settings", fake_settings)
    monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
    monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
    monkeypatch.setattr(_idx, "validate_tree", lambda structure, **kw: gate_result)
    monkeypatch.setattr(_idx, "prepare_tree", lambda structure, **kw: structure)
    monkeypatch.setattr(_idx, "_generate_flat_doc_description", lambda text, **kw: "")

    mocks = {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "route_and_extract_flat": MagicMock(
            return_value=("flat_prose", [{"role": "prose", "text": "flat prose body"}])
        ),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(_idx, name, m)
    monkeypatch.setattr(_img, "route_and_extract_flat", mocks["route_and_extract_flat"])
    monkeypatch.setattr(_img, "LOW_QUALITY_TREES", mocks["LOW_QUALITY_TREES"])
    return mocks


def _flat_route_client(monkeypatch):
    c = CustomPageIndexClient(api_key="test-key")

    async def _tree(*a, **k):
        return {
            "structure": [{"title": "Root", "text": "body text", "nodes": []}],
            "doc_description": "",
        }

    monkeypatch.setattr(c, "_run_md_to_tree", _tree)
    return c


_FLAT_ROUTED_GATES = (
    TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW, detail="node_count=2"),
    TreeGateResult(ok=False, defect=TreeDefect.DEPTH_LOW, detail="depth=1"),
)


async def test_flat_03_c1_non_garbling_rejection_routes_to_flat_success(monkeypatch, flat_md_file):
    """FLAT-03-C1: with flat_doc_routing on, a node_count<3 / depth<2 gate
    failure routes to the flat SUCCESS path — route_and_extract_flat runs on
    the converter markdown, save_flat_doc persists, index() returns the doc_id,
    FLAT_DOCS_TOTAL{content_class} is incremented, no LowQualityTreeError is
    raised, and the tree artifact processed/<doc_id>.json is NOT written."""
    failures: list[str] = []
    for gate in _FLAT_ROUTED_GATES:
        mocks = _wire_flat_route(monkeypatch, gate_result=gate, flat_doc_routing=True)
        c = _flat_route_client(monkeypatch)
        try:
            doc_id = await c.index(flat_md_file)
        except LowQualityTreeError as exc:
            failures.append(f"{gate.defect.name}: raised LowQualityTreeError({exc.reason!r})")
            continue
        if not (isinstance(doc_id, str) and len(doc_id) == 36):
            failures.append(f"{gate.defect.name}: index() returned {doc_id!r}, not a doc_id")
        if not mocks["route_and_extract_flat"].called:
            failures.append(f"{gate.defect.name}: route_and_extract_flat not called")
        if mocks["save_flat_doc"].call_count != 1:
            failures.append(
                f"{gate.defect.name}: save_flat_doc calls={mocks['save_flat_doc'].call_count}"
            )
        elif mocks["save_flat_doc"].call_args.args[0] != doc_id:
            failures.append(f"{gate.defect.name}: save_flat_doc persisted a different doc_id")
        # HR5-adjacent negative: the flat route must NOT also write the tree.
        if mocks["save_doc"].called:
            failures.append(f"{gate.defect.name}: save_doc wrote processed/<doc_id>.json")
        mocks["FLAT_DOCS_TOTAL"].labels.assert_called_once_with(content_class="flat_prose")
        if c.last_content_class != "flat_prose":
            failures.append(f"{gate.defect.name}: last_content_class={c.last_content_class!r}")
    _report(failures, "FLAT-03-C1 flat success routing")


async def test_flat_03_c2_garbling_stays_terminal_with_flat_routing_on(monkeypatch, flat_md_file):
    """FLAT-03-C2 (HR5): a document whose reason resolves to 'garbling' raises
    LowQualityTreeError('garbling') even with flat_doc_routing TRUE — nothing
    is persisted (no .json, no .flat.json) and LOW_QUALITY_TREES{reason=
    garbling} is incremented.

    NOTE on the trigger: the contract names validate_tree returning
    (False, 'garbling') as the trigger, but REASON_POLICY now gives GARBLING
    the RETRY_OCR policy, so that reason alone routes to recovery and (if
    unrecovered on the tree route) persists with a FAIL verdict.  The
    surviving terminal 'garbling' reason is the per-block flat garble gate
    inside _persist_flat_result, which is what this test drives: a flat-routed
    document (node_count<3, flat routing ON) whose blocks are garbled must
    still raise rather than persist a flat artifact."""
    from pageindex_mcp.helpers import GarbleReport

    mocks = _wire_flat_route(
        monkeypatch,
        gate_result=TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW, detail="n=2"),
        flat_doc_routing=True,
    )
    _garbled = GarbleReport(is_garbled=True, fired_prongs=frozenset({"test"}))
    monkeypatch.setattr(_idx, "_garble_check_flat_blocks", lambda blocks, **kw: _garbled)
    c = _flat_route_client(monkeypatch)
    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(flat_md_file)

    assert exc.value.reason == "garbling"
    mocks["save_doc"].assert_not_called()
    mocks["save_flat_doc"].assert_not_called()
    mocks["LOW_QUALITY_TREES"].labels.assert_called_with(reason="garbling")
    assert c.last_content_class is None


async def test_flat_03_c3_kill_switch_rejects_every_reason(monkeypatch, flat_md_file):
    """FLAT-03-C3: with flat_doc_routing FALSE the legacy behaviour returns —
    node_count<3 and depth<2 raise LowQualityTreeError(reason) like every other
    failure, and no flat doc is persisted."""
    failures: list[str] = []
    for gate in _FLAT_ROUTED_GATES:
        mocks = _wire_flat_route(monkeypatch, gate_result=gate, flat_doc_routing=False)
        c = _flat_route_client(monkeypatch)
        try:
            doc_id = await c.index(flat_md_file)
        except LowQualityTreeError as exc:
            if exc.reason != gate.defect.value:
                failures.append(f"{gate.defect.name}: raised reason={exc.reason!r}")
        else:
            failures.append(f"{gate.defect.name}: did not raise, returned {doc_id!r}")
        if mocks["save_flat_doc"].called:
            failures.append(f"{gate.defect.name}: save_flat_doc persisted with the switch off")
    _report(failures, "FLAT-03-C3 kill-switch")
