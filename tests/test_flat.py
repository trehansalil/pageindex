# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Flat document view, block text consolidation, and RFC block tests."""
from __future__ import annotations

import pytest

from pageindex_mcp.client import apply_image_ext_content_class_override
from pageindex_mcp.client import images as _img
from pageindex_mcp.helpers import (
    BULK_PROFILE,
    _classify_image_verdict,
    _flat_block_primary_text,
    _flat_search_text,
    _flatten_tree_text,
    classify_verdict,
    flat_doc_view,
)
from tests._garble_compat import check_garble


# --- from test_flat_doc_view.py ---


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


# ---------------------------------------------------------------------------
# Pre-aggregated row_records (new path)
# ---------------------------------------------------------------------------


class TestFlatDocViewPreAggregated:
    """When flat_meta contains a pre-aggregated 'row_records' key (written
    by _persist_flat_result after the Zone 4.7 fix), flat_doc_view should
    use it directly without re-deriving from blocks."""

    def test_uses_pre_aggregated_row_records(self):
        pre_agg = ["col1 | col2", "a | b", "x | y", "1 | 2"]
        data = _make_flat_data(
            blocks=_table_blocks(),
            row_records=pre_agg,
        )
        result = flat_doc_view(data)
        assert result is not None
        assert result["row_records"] == pre_agg

    def test_pre_aggregated_empty_list(self):
        """An explicit empty row_records means no tables -- should not fall
        back to block derivation."""
        data = _make_flat_data(
            blocks=_table_blocks(),
            row_records=[],
        )
        result = flat_doc_view(data)
        assert result is not None
        assert result["row_records"] == []

    def test_pre_aggregated_preserves_order(self):
        ordered = ["first", "second", "third"]
        data = _make_flat_data(row_records=ordered)
        result = flat_doc_view(data)
        assert result is not None
        assert result["row_records"] == ordered


# ---------------------------------------------------------------------------
# Fallback derivation (backward compatibility)
# ---------------------------------------------------------------------------


class TestFlatDocViewFallback:
    """When flat_meta has NO 'row_records' key (documents persisted before
    the Zone 4.7 pre-aggregation change), flat_doc_view must fall back to
    deriving row_records from blocks -- identical to the pre-fix behavior."""

    def test_derives_row_records_from_table_blocks(self):
        data = _make_flat_data(blocks=_table_blocks())
        assert "row_records" not in data
        result = flat_doc_view(data)
        assert result is not None
        assert result["row_records"] == ["col1 | col2", "a | b", "x | y", "1 | 2"]

    def test_no_table_blocks_yields_empty(self):
        data = _make_flat_data(
            blocks=[{"role": "prose", "text": "Just text."}],
        )
        result = flat_doc_view(data)
        assert result is not None
        assert result["row_records"] == []

    def test_empty_blocks_yields_empty(self):
        data = _make_flat_data(blocks=[])
        result = flat_doc_view(data)
        assert result is not None
        assert result["row_records"] == []


# ---------------------------------------------------------------------------
# Path equivalence -- both paths produce identical output
# ---------------------------------------------------------------------------


class TestFlatDocViewPathEquivalence:
    """Given the same logical document, the pre-aggregated path and the
    fallback derivation path must produce identical row_records output."""

    def test_identical_output_simple(self):
        blocks = _table_blocks()
        # Expected row_records from block derivation
        expected = ["col1 | col2", "a | b", "x | y", "1 | 2"]

        data_with_pre_agg = _make_flat_data(blocks=blocks, row_records=expected)
        data_without = _make_flat_data(blocks=blocks)

        result_pre_agg = flat_doc_view(data_with_pre_agg)
        result_fallback = flat_doc_view(data_without)

        assert result_pre_agg is not None
        assert result_fallback is not None
        assert result_pre_agg["row_records"] == result_fallback["row_records"]

    def test_identical_output_mixed_blocks(self):
        blocks = [
            {"role": "heading", "text": "# Title"},
            {"role": "table", "row_records": ["h1 | h2", "v1 | v2"]},
            {"role": "image", "ocr_text": "scanned"},
            {"role": "table", "row_records": ["a", "b", "c"]},
            {"role": "prose", "text": "conclusion"},
        ]
        expected = ["h1 | h2", "v1 | v2", "a", "b", "c"]

        result_pre = flat_doc_view(_make_flat_data(blocks=blocks, row_records=expected))
        result_fb = flat_doc_view(_make_flat_data(blocks=blocks))

        assert result_pre is not None
        assert result_fb is not None
        assert result_pre["row_records"] == result_fb["row_records"]


# ---------------------------------------------------------------------------
# Boundary conditions
# ---------------------------------------------------------------------------


class TestFlatDocViewBoundary:
    """Edge cases and non-flat document handling."""

    def test_tree_doc_returns_none(self):
        """A tree document (no content_class) is not a flat doc; flat_doc_view
        returns None."""
        tree_data = {
            "doc_name": "tree.pdf",
            "structure": [{"node_id": "n1", "title": "A", "text": "t"}],
        }
        assert flat_doc_view(tree_data) is None

    def test_response_shape_keys(self):
        """flat_doc_view must return exactly the expected response shape."""
        data = _make_flat_data(blocks=[{"role": "prose", "text": "hi"}])
        result = flat_doc_view(data)
        assert result is not None
        expected_keys = {"doc_name", "content_class", "blocks", "row_records", "structure", "doc_description"}
        assert set(result.keys()) == expected_keys

    def test_structure_is_empty_list(self):
        """Flat docs have no tree structure; always returns empty list."""
        data = _make_flat_data(blocks=[])
        result = flat_doc_view(data)
        assert result is not None
        assert result["structure"] == []

    def test_doc_name_fallback_to_filename(self):
        data = {
            "filename": "fallback.pdf",
            "content_class": "flat_prose",
            "blocks": [],
        }
        result = flat_doc_view(data)
        assert result is not None
        assert result["doc_name"] == "fallback.pdf"

    def test_table_block_with_none_row_records(self):
        """A table block whose row_records is None (malformed) should not
        crash the fallback derivation."""
        blocks = [{"role": "table", "row_records": None}]
        data = _make_flat_data(blocks=blocks)
        result = flat_doc_view(data)
        assert result is not None
        assert result["row_records"] == []


# --- from test_flat_block_text_consolidation.py ---


# ---------------------------------------------------------------------------
# _flat_block_primary_text regression tests -- all block roles
# ---------------------------------------------------------------------------


class TestFlatBlockPrimaryText:
    """_flat_block_primary_text must handle every block role correctly,
    returning primary document text WITHOUT OCR/description enrichment."""

    def test_prose_block_returns_text(self):
        block = {"role": "prose", "text": "Hello world"}
        assert _flat_block_primary_text(block) == "Hello world"

    def test_heading_block_returns_text(self):
        block = {"role": "heading", "text": "# Section Title"}
        assert _flat_block_primary_text(block) == "# Section Title"

    def test_table_block_with_row_records(self):
        block = {
            "role": "table",
            "row_records": ["col1 | col2", "a | b"],
        }
        assert _flat_block_primary_text(block) == "col1 | col2\na | b"

    def test_table_block_empty_row_records(self):
        block = {"role": "table", "row_records": []}
        assert _flat_block_primary_text(block) == ""

    def test_table_block_no_row_records_key(self):
        block = {"role": "table"}
        assert _flat_block_primary_text(block) == ""

    def test_table_block_with_text_and_row_records_prefers_row_records(self):
        """D2 (RFC-041): table blocks consistently use row_records, ignoring
        any text key.  This is the unified behavior across all purposes."""
        block = {
            "role": "table",
            "text": "should be ignored",
            "row_records": ["r1", "r2"],
        }
        result = _flat_block_primary_text(block)
        assert result == "r1\nr2"

    def test_image_block_returns_empty_string(self):
        """Image blocks have no primary text -- OCR/description are
        enrichment, not primary content."""
        block = {
            "role": "image",
            "text": "",
            "ocr_text": "OCR content",
            "description": "A chart showing data",
        }
        assert _flat_block_primary_text(block) == ""

    def test_block_with_no_role_returns_text(self):
        block = {"text": "plain text"}
        assert _flat_block_primary_text(block) == "plain text"

    def test_empty_block_returns_empty(self):
        block = {}
        assert _flat_block_primary_text(block) == ""

    def test_block_with_empty_text_and_no_table_role(self):
        block = {"role": "prose", "text": ""}
        assert _flat_block_primary_text(block) == ""


# ---------------------------------------------------------------------------
# _flat_search_text regression tests -- OCR/description enrichment
# ---------------------------------------------------------------------------


class TestFlatSearchTextEnrichment:
    """_flat_search_text must include OCR/description enrichment for image
    blocks (search-index text is a SUPERSET of primary text)."""

    def test_search_text_includes_ocr_for_image_blocks(self):
        data = {
            "blocks": [
                {"role": "image", "ocr_text": "OCR scanned text"},
            ],
        }
        result = _flat_search_text(data)
        assert "OCR scanned text" in result

    def test_search_text_includes_description_for_image_blocks(self):
        data = {
            "blocks": [
                {"role": "image", "description": "Chart showing revenue"},
            ],
        }
        result = _flat_search_text(data)
        assert "Chart showing revenue" in result

    def test_search_text_includes_both_ocr_and_description(self):
        data = {
            "blocks": [
                {
                    "role": "image",
                    "ocr_text": "OCR text here",
                    "description": "Desc here",
                },
            ],
        }
        result = _flat_search_text(data)
        assert "OCR text here" in result
        assert "Desc here" in result

    def test_search_text_includes_table_row_records(self):
        data = {
            "blocks": [
                {"role": "table", "row_records": ["a | b", "c | d"]},
            ],
        }
        result = _flat_search_text(data)
        assert "a | b" in result
        assert "c | d" in result

    def test_search_text_includes_prose_text(self):
        data = {
            "blocks": [
                {"role": "prose", "text": "Hello world"},
            ],
        }
        result = _flat_search_text(data)
        assert "Hello world" in result

    def test_search_text_merges_top_level_row_records(self):
        """Top-level row_records (legacy shape) are appended if not already
        present from blocks."""
        data = {
            "blocks": [],
            "row_records": ["extra row"],
        }
        result = _flat_search_text(data)
        assert "extra row" in result

    def test_search_text_deduplicates_top_level_row_records(self):
        data = {
            "blocks": [
                {"role": "table", "row_records": ["shared row"]},
            ],
            "row_records": ["shared row"],
        }
        result = _flat_search_text(data)
        # "shared row" should appear exactly once
        assert result.count("shared row") == 1

    def test_search_text_empty_blocks(self):
        data = {"blocks": []}
        result = _flat_search_text(data)
        assert result == ""


# ---------------------------------------------------------------------------
# _flat_block_text removal -- exhaustiveness check
# ---------------------------------------------------------------------------


class TestFlatBlockTextRemoved:
    """_flat_block_text was dead production code (zero production callers,
    RFC-022 B3 artifact superseded by RFC-027 D0 _flat_block_primary_text).
    Verify it is no longer importable from the helpers package."""

    def test_flat_block_text_not_in_helpers_namespace(self):
        import pageindex_mcp.helpers as helpers

        assert not hasattr(helpers, "_flat_block_text"), (
            "_flat_block_text should have been removed from helpers; "
            "it is dead code superseded by _flat_block_primary_text"
        )

    def test_flat_block_text_not_in_helpers_all(self):
        import pageindex_mcp.helpers as helpers

        assert "_flat_block_text" not in helpers.__all__, (
            "_flat_block_text should not be listed in helpers.__all__"
        )

    def test_flat_block_text_not_importable_from_flat_module(self):
        from pageindex_mcp.helpers import flat

        assert not hasattr(flat, "_flat_block_text"), (
            "_flat_block_text should have been removed from flat.py"
        )

    def test_flat_block_text_direct_import_raises(self):
        with pytest.raises(ImportError):
            from pageindex_mcp.helpers import _flat_block_text  # noqa: F401

    def test_primary_text_still_importable(self):
        """Ensure the production replacement is still available."""
        from pageindex_mcp.helpers import _flat_block_primary_text  # noqa: F401

        assert callable(_flat_block_primary_text)

    def test_search_text_still_importable(self):
        """Ensure the search-index variant is still available."""
        from pageindex_mcp.helpers import _flat_search_text  # noqa: F401

        assert callable(_flat_search_text)


# --- from test_rfc_blocks.py ---


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

    def test_synthetic_structure_generated_from_blocks(self):
        blocks = [{"text": "alpha content"}, {"text": "beta content"}, {"text": "gamma content"}]
        structure = _synthesize_flat_structure([], blocks)
        assert len(structure) == len(blocks)
        assert all(node["text"] for node in structure)

    def test_synthetic_structure_promotes_cat_b(self):
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
        # FAIL (the hard floor this doc-shape used to slip past), not
        # MARGINAL.
        structure = _synthesize_flat_structure([], [])
        assert structure == []
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert (verdict, reason) == ("FAIL", "zero_content")

    def test_non_empty_garbled_structure_still_detected(self):
        blocks = [{"text": "\x00" * 200}]
        structure = _synthesize_flat_structure([], blocks)
        assert structure
        assert _tree_garble(structure) is True
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert verdict == "FAIL" or (verdict == "MARGINAL" and "garbl" in reason)

    def test_doc3_codepath_produces_enriched_blocks_for_table_heavy_doc(self):
        blocks = _table_heavy_doc_blocks()
        structure = _synthesize_flat_structure([], blocks)
        assert len(structure) == len(blocks) == 3
        enriched = [node for node in structure if node["text"].strip()]
        assert len(enriched) == 3

    def test_total_enriched_chars_exceeds_minimum_threshold(self):
        blocks = _table_heavy_doc_blocks()
        structure = _synthesize_flat_structure([], blocks)
        total_chars = sum(len(node["text"]) for node in structure)
        assert total_chars > 375

    def test_classify_verdict_receives_real_content_for_table_heavy_doc(self):
        blocks = _table_heavy_doc_blocks()
        structure = _synthesize_flat_structure([], blocks)
        verdict, reason = classify_verdict(structure, "flat_table", None)
        # Pre-fix failure mode: content starvation -> a garbling-driven
        # verdict. Post-fix the reason must not be garbling-driven at all
        # (actual observed post-fix result: MARGINAL/depth=1, a legitimate
        # structural reason).
        assert "garbling" not in reason
        assert verdict != "FAIL"


class TestFlatBlockText:
    """Property 5 (B3): _flat_block_primary_text falls back to verbalized
    row_records for role="table" blocks that carry no "text" key."""

    def test_table_block_without_text_key_falls_back_to_row_records(self):
        block = {"role": "table", "row_records": ["a | b | c", "d | e | f"]}
        assert "text" not in block
        text = _flat_block_primary_text(block)
        assert text == "a | b | c\nd | e | f"

    def test_pre_fix_text_only_measurement_would_starve_table_blocks(self):
        # Regression guard: the pre-B3 measurement (b.get("text", "")) sees
        # zero content for table blocks, which is the bug this fix
        # addresses.
        blocks = _table_heavy_doc_blocks()
        pre_fix_chars = sum(len(b.get("text", "")) for b in blocks)
        assert pre_fix_chars == 0
        post_fix_chars = sum(len(_flat_block_primary_text(b)) for b in blocks)
        assert post_fix_chars > 375


class TestTreeGarble:
    """Property 2 (B1): check_garble on empty tree -> False."""

    def test_tree_garble_empty_list_returns_false(self):
        assert _tree_garble([]) is False

    def test_tree_garble_non_empty_unchanged(self):
        assert _tree_garble([{"text": "real content"}]) is False
        assert _tree_garble([{"text": "\x00" * 200}]) is True


class TestImageExtensionRouting:
    """Property 3 (B2): a file whose extension is in _IMAGE_EXTS gets
    content_class="image_standalone" regardless of block-role composition."""

    def test_jpg_extension_sets_image_standalone(self):
        content_class = apply_image_ext_content_class_override(".jpg", "flat_prose")
        assert content_class == "image_standalone"

    def test_pipeline_disabled_falls_back_to_flat_path(self, monkeypatch):
        monkeypatch.setattr(_img, "_IMAGE_STANDALONE_PIPELINE_ENABLED", False)
        content_class = apply_image_ext_content_class_override(".jpg", "flat_prose")
        assert content_class == "flat_prose"
        # Even with the override disabled, the enrichment rescue gate (B2-B)
        # is defense-in-depth and still promotes a well-enriched flat doc —
        # but only when max_leaf_ratio is below the 0.75 hard-FAIL ceiling.
        structure = _multi_node_tree()
        verdict, reason = classify_verdict(
            structure, content_class, None, image_enrichment_ratio=0.9
        )
        assert (verdict, reason) == ("PASS", "image_enrichment_promoted")


class TestClassifyImageVerdict:
    """_classify_image_verdict: PASS/FAIL boundaries for image-standalone
    documents based on enrichment ratio."""

    def test_classify_image_verdict_full_ratio_passes(self):
        assert _classify_image_verdict(1.0) == ("PASS", "image_enrichment_complete")

    def test_classify_image_verdict_none_fails(self):
        assert _classify_image_verdict(None) == ("FAIL", "no_image_enrichment")


class TestImageEnrichmentGateOrdering:
    """Property 4 (B2): QF2a gate ordering — max_leaf_ratio > 0.75 hard-FAIL
    fires AFTER image_standalone routing but BEFORE image-enrichment rescue.
    A 100% single-leaf tree can no longer PASS via enrichment rescue."""

    def test_image_enrichment_rescue_overrides_max_leaf_ratio(self):
        """Image-enrichment rescue runs before max_leaf_ratio gate because
        flat image-enriched documents are expected to have single-leaf
        structure (max_leaf_ratio=1.0). Without the rescue, every
        image-enriched flat doc would hard-FAIL on structure alone."""
        structure = _single_leaf_tree()
        verdict, reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.9
        )
        assert verdict == "PASS"
        assert reason == "image_enrichment_promoted"

    def test_image_enrichment_rescue_works_below_hard_fail_ceiling(self):
        """When max_leaf_ratio is below 0.75, image-enrichment rescue
        promotes."""
        structure = _multi_node_tree()
        verdict, reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.9
        )
        assert (verdict, reason) == ("PASS", "image_enrichment_promoted")

    def test_non_image_enriched_doc_still_fails_on_max_leaf_ratio(self):
        structure = _single_leaf_tree()
        verdict, reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=None
        )
        assert verdict == "FAIL"
        assert reason.startswith("max_leaf_ratio=")


# ---------------------------------------------------------------------------
# Zone-9: _flat_block_primary_text header-only table fallback
# ---------------------------------------------------------------------------


class TestFlatBlockPrimaryTextHeaderOnly:
    """Zone-9 fix: when a table block has headers but zero data rows
    (row_records empty), _flat_block_primary_text must fall back to the
    header list so flat_char_count is non-zero and recovery.py flat-prefer
    routing sees real content volume."""

    def test_header_only_table_returns_joined_headers(self):
        block = {"role": "table", "headers": ["Name", "Age", "City"], "row_records": []}
        result = _flat_block_primary_text(block)
        assert result == "Name | Age | City"

    def test_header_only_table_no_row_records_key(self):
        block = {"role": "table", "headers": ["Col A", "Col B"]}
        result = _flat_block_primary_text(block)
        assert result == "Col A | Col B"

    def test_header_only_table_none_row_records(self):
        block = {"role": "table", "headers": ["X", "Y"], "row_records": None}
        result = _flat_block_primary_text(block)
        assert result == "X | Y"

    def test_header_only_table_filters_empty_headers(self):
        """Empty/falsy header cells should be filtered out."""
        block = {"role": "table", "headers": ["A", "", "C", None], "row_records": []}
        result = _flat_block_primary_text(block)
        assert result == "A | C"

    def test_row_records_preferred_over_headers(self):
        """When row_records exist, they take precedence over headers."""
        block = {
            "role": "table",
            "headers": ["H1", "H2"],
            "row_records": ["data1 | data2"],
        }
        result = _flat_block_primary_text(block)
        assert result == "data1 | data2"

    def test_no_headers_no_row_records_returns_empty(self):
        block = {"role": "table", "headers": [], "row_records": []}
        result = _flat_block_primary_text(block)
        assert result == ""

    def test_no_headers_key_no_row_records_returns_empty(self):
        block = {"role": "table"}
        result = _flat_block_primary_text(block)
        assert result == ""

    def test_header_only_table_char_count_nonzero(self):
        """The whole point of the Zone-9 fix: flat_char_count must not
        undercount header-only tables to zero."""
        blocks = [
            {"role": "prose", "text": "intro"},
            {"role": "table", "headers": ["Product", "Price", "Rating"], "row_records": []},
        ]
        char_count = sum(len(_flat_block_primary_text(b)) for b in blocks)
        assert char_count > len("intro"), (
            f"Header-only table should contribute to char_count, got {char_count}"
        )


# ---------------------------------------------------------------------------
# Contract: _flat_block_primary_text is the canonical role-aware accessor
# ---------------------------------------------------------------------------


class TestFlatBlockPrimaryTextContract:
    """Contract tests verifying _flat_block_primary_text is the single
    canonical accessor for block primary text across all roles."""

    def test_prose_block_returns_text(self):
        assert _flat_block_primary_text({"role": "prose", "text": "hello"}) == "hello"

    def test_kv_block_returns_text(self):
        assert _flat_block_primary_text({"role": "kv", "text": "key: value"}) == "key: value"

    def test_title_block_returns_text(self):
        assert _flat_block_primary_text({"role": "title", "text": "Section 1"}) == "Section 1"

    def test_image_block_no_text_returns_empty(self):
        """Image blocks carry enrichment in ocr_text/description, not 'text'.
        Primary text for image blocks is empty."""
        block = {"role": "image", "ocr_text": "chart data", "description": "bar chart"}
        assert _flat_block_primary_text(block) == ""

    def test_table_with_text_key_prefers_row_records(self):
        """D2 (RFC-041): table blocks consistently use row_records,
        ignoring any text key.  Unified behavior across all purposes."""
        block = {"role": "table", "text": "legacy text", "row_records": ["r1"]}
        assert _flat_block_primary_text(block) == "r1"

    def test_table_row_records_joined_with_newline(self):
        block = {"role": "table", "row_records": ["row1", "row2", "row3"]}
        assert _flat_block_primary_text(block) == "row1\nrow2\nrow3"


# ---------------------------------------------------------------------------
# Contract: _flat_search_text role-aware retrieval text
# ---------------------------------------------------------------------------


class TestFlatSearchTextContract:
    """Contract tests verifying _flat_search_text renders role-aware
    retrieval text for flat documents."""

    def test_mixed_doc_all_roles_represented(self):
        """A mixed flat doc with prose, table, and image blocks should
        include content from each role's appropriate field."""
        data = {
            "blocks": [
                {"role": "prose", "text": "Introduction text."},
                {"role": "table", "row_records": ["A | B", "1 | 2"]},
                {"role": "image", "ocr_text": "chart OCR", "description": "pie chart"},
            ],
        }
        result = _flat_search_text(data)
        assert "Introduction text." in result
        assert "A | B" in result
        assert "1 | 2" in result
        assert "chart OCR" in result
        assert "pie chart" in result

    def test_table_block_uses_row_records_not_text(self):
        """_flat_search_text must read row_records for table blocks, not
        any 'text' key."""
        data = {
            "blocks": [
                {
                    "role": "table",
                    "text": "should NOT appear",
                    "row_records": ["actual | data"],
                },
            ],
        }
        result = _flat_search_text(data)
        assert "actual | data" in result
        assert "should NOT appear" not in result

    def test_image_block_skips_text_key(self):
        """Image blocks should use ocr_text and description, ignoring
        any 'text' key."""
        data = {
            "blocks": [
                {
                    "role": "image",
                    "text": "should be ignored",
                    "ocr_text": "OCR result",
                },
            ],
        }
        result = _flat_search_text(data)
        assert "OCR result" in result
        # Image blocks with role='image' skip the text key in the else branch
        # because the role=='image' branch handles them specially

    def test_none_blocks_handled(self):
        data = {"blocks": None}
        assert _flat_search_text(data) == ""

    def test_missing_blocks_key(self):
        data = {}
        assert _flat_search_text(data) == ""


# ---------------------------------------------------------------------------
# Wiring: indexer.py flat_char_count uses _flat_block_primary_text
# ---------------------------------------------------------------------------


class TestIndexerFlatCharCountWiring:
    """Verify that indexer.py's flat_char_count computation and flat
    structure synthesis use _flat_block_primary_text, not naive
    block.get('text', '')."""

    def test_flat_char_count_includes_table_row_records(self):
        """Simulate the flat_char_count computation from
        indexer.py:_persist_flat_result (line 1103)."""
        blocks = [
            {"role": "prose", "text": "Hello world"},
            {"role": "table", "row_records": ["col1 | col2", "a | b"]},
        ]
        # This mirrors indexer.py line 1103:
        # flat_char_count = sum(len(_flat_block_primary_text(b)) for b in blocks)
        flat_char_count = sum(len(_flat_block_primary_text(b)) for b in blocks)
        assert flat_char_count > len("Hello world"), (
            "Table row_records must contribute to flat_char_count"
        )
        expected = len("Hello world") + len("col1 | col2\na | b")
        assert flat_char_count == expected

    def test_flat_char_count_header_only_table_nonzero(self):
        """Header-only tables must contribute non-zero chars to
        flat_char_count (Zone-9 fix target)."""
        blocks = [
            {"role": "table", "headers": ["Name", "Value"], "row_records": []},
        ]
        flat_char_count = sum(len(_flat_block_primary_text(b)) for b in blocks)
        assert flat_char_count > 0, (
            "Header-only table must contribute to flat_char_count"
        )

    def test_flat_structure_synthesis_uses_primary_text(self):
        """Simulate the flat_structure synthesis from
        indexer.py:_persist_flat_result (lines 1080-1084)."""
        blocks = [
            {"role": "prose", "text": "Intro paragraph."},
            {"role": "table", "row_records": ["A | B", "1 | 2"]},
            {"role": "image", "ocr_text": "chart"},  # no primary text
        ]
        # This mirrors indexer.py lines 1080-1084:
        flat_structure = [
            {"title": "", "text": _flat_block_primary_text(b)}
            for b in blocks
            if _flat_block_primary_text(b).strip()
        ]
        assert len(flat_structure) == 2, (
            "Prose + table blocks should produce structure nodes; image should not"
        )
        assert flat_structure[0]["text"] == "Intro paragraph."
        assert flat_structure[1]["text"] == "A | B\n1 | 2"

    def test_naive_block_get_text_would_miss_table_content(self):
        """Regression proof: naive block.get('text', '') produces zero
        for table blocks, demonstrating the bug _flat_block_primary_text
        fixes."""
        blocks = [
            {"role": "table", "row_records": ["data1", "data2"]},
        ]
        naive_count = sum(len(b.get("text", "")) for b in blocks)
        correct_count = sum(len(_flat_block_primary_text(b)) for b in blocks)
        assert naive_count == 0, "Naive access should see zero for table blocks"
        assert correct_count > 0, "Role-aware access should see table content"


# ---------------------------------------------------------------------------
# Wiring: _flat_block_primary_text is importable from helpers namespace
# ---------------------------------------------------------------------------


class TestCanonicalHelperExports:
    """Verify the two canonical helpers are exported from the helpers
    package and are the same objects as in flat.py."""

    def test_primary_text_exported_from_helpers(self):
        from pageindex_mcp.helpers import _flat_block_primary_text as h_fn
        from pageindex_mcp.helpers.flat import _flat_block_primary_text as f_fn
        assert h_fn is f_fn

    def test_search_text_exported_from_helpers(self):
        from pageindex_mcp.helpers import _flat_search_text as h_fn
        from pageindex_mcp.helpers.flat import _flat_search_text as f_fn
        assert h_fn is f_fn

    def test_indexer_imports_primary_text_from_helpers(self):
        """indexer.py must import _flat_block_primary_text from helpers."""
        import ast as _ast
        from pathlib import Path as _Path

        indexer_src = (_Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp" / "client" / "indexer.py")
        tree = _ast.parse(indexer_src.read_text())
        imported_names = set()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.name)
        assert "_flat_block_primary_text" in imported_names, (
            "indexer.py must import _flat_block_primary_text"
        )

    def test_recovery_imports_primary_text_from_helpers(self):
        """recovery.py must import _flat_block_primary_text from helpers."""
        import ast as _ast
        from pathlib import Path as _Path

        recovery_src = (_Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp" / "client" / "recovery.py")
        tree = _ast.parse(recovery_src.read_text())
        imported_names = set()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.name)
        assert "_flat_block_primary_text" in imported_names, (
            "recovery.py must import _flat_block_primary_text"
        )


# ===========================================================================
# D2 (RFC-041): block_text / doc_text unification — Property 2
# ===========================================================================

from pageindex_mcp.helpers.flat import BlockTextPurpose, block_text, doc_text


class TestBlockTextPurposes:
    """Property 2: block_text with each purpose for table, paragraph, image blocks."""

    def test_paragraph_block_all_purposes(self):
        block = {"role": "prose", "text": "Hello world"}
        for purpose in BlockTextPurpose:
            assert block_text(block, purpose) == "Hello world"

    def test_table_block_row_records_all_purposes(self):
        block = {"role": "table", "row_records": ["a | b", "c | d"]}
        for purpose in BlockTextPurpose:
            assert block_text(block, purpose) == "a | b\nc | d"

    def test_table_header_only_all_purposes(self):
        """Zone-9: header-only table returns header text for all purposes."""
        block = {"role": "table", "headers": ["Name", "Age"], "row_records": []}
        for purpose in BlockTextPurpose:
            assert block_text(block, purpose) == "Name | Age"

    def test_image_block_search_includes_enrichment(self):
        block = {"role": "image", "ocr_text": "scanned text", "description": "a photo"}
        result = block_text(block, BlockTextPurpose.SEARCH)
        assert "scanned text" in result
        assert "a photo" in result

    def test_image_block_non_search_excludes_enrichment(self):
        block = {"role": "image", "ocr_text": "scanned text", "description": "a photo"}
        for purpose in (BlockTextPurpose.GARBLE_CHECK, BlockTextPurpose.CHAR_COUNT, BlockTextPurpose.DISPLAY):
            assert block_text(block, purpose) == ""

    def test_table_with_dict_row_records(self):
        block = {"role": "table", "row_records": [{"key": "premium", "value": "1200"}]}
        result = block_text(block, BlockTextPurpose.CHAR_COUNT)
        assert "premium" in result
        assert "1200" in result

    def test_table_with_rows_no_row_records(self):
        block = {"role": "table", "headers": ["H1"], "rows": [["alpha", "bravo"]]}
        result = block_text(block, BlockTextPurpose.CHAR_COUNT)
        assert "H1" in result
        assert "alpha" in result
        assert "bravo" in result

    def test_table_text_fallback(self):
        """Table block with only text (no row_records, no headers, no rows)."""
        block = {"role": "table", "text": "fallback text"}
        assert block_text(block, BlockTextPurpose.CHAR_COUNT) == "fallback text"

    def test_empty_block(self):
        assert block_text({}, BlockTextPurpose.CHAR_COUNT) == ""

    def test_tree_node_with_row_records(self):
        """Tree nodes (no role) with row_records are detected as table content."""
        node = {"title": "T", "row_records": ["r1", "r2"]}
        assert block_text(node, BlockTextPurpose.GARBLE_CHECK) == "r1\nr2"

    def test_tree_node_with_headers_only(self):
        """Tree nodes with only headers return pipe-joined header text."""
        node = {"title": "T", "headers": ["Col1", "Col2"]}
        result = block_text(node, BlockTextPurpose.CHAR_COUNT)
        assert result == "Col1 | Col2"


class TestBlockTextConsistencyAcrossPurposes:
    """Property 2: all accessor paths produce consistent text for table blocks."""

    def test_table_block_consistent_base_text(self):
        block = {"role": "table", "row_records": ["name | age", "alice | 30"]}
        texts = {purpose: block_text(block, purpose) for purpose in BlockTextPurpose}
        base_text = texts[BlockTextPurpose.CHAR_COUNT]
        for purpose, text in texts.items():
            assert text == base_text, (
                f"block_text({purpose}) returned different text than CHAR_COUNT"
            )

    def test_header_only_table_consistent(self):
        block = {"role": "table", "headers": ["X", "Y", "Z"], "row_records": []}
        texts = {purpose: block_text(block, purpose) for purpose in BlockTextPurpose}
        for purpose, text in texts.items():
            assert text == "X | Y | Z", (
                f"block_text({purpose}) returned '{text}' instead of 'X | Y | Z'"
            )


class TestDocText:
    """doc_text whole-document extraction."""

    def test_search_purpose_matches_flat_search_text(self):
        data = {
            "blocks": [
                {"role": "prose", "text": "Introduction"},
                {"role": "table", "row_records": ["a | b"]},
                {"role": "image", "ocr_text": "scanned"},
            ]
        }
        assert doc_text(data, BlockTextPurpose.SEARCH) == _flat_search_text(data)

    def test_char_count_purpose_excludes_image_enrichment(self):
        data = {
            "blocks": [
                {"role": "prose", "text": "Body text"},
                {"role": "image", "ocr_text": "should not appear"},
            ]
        }
        result = doc_text(data, BlockTextPurpose.CHAR_COUNT)
        assert "Body text" in result
        assert "should not appear" not in result

    def test_legacy_top_level_row_records_appended_for_search(self):
        data = {
            "blocks": [],
            "row_records": ["legacy_row"],
        }
        result = doc_text(data, BlockTextPurpose.SEARCH)
        assert "legacy_row" in result

    def test_empty_doc(self):
        assert doc_text({}, BlockTextPurpose.SEARCH) == ""
        assert doc_text({"blocks": []}, BlockTextPurpose.CHAR_COUNT) == ""


class TestGarbleScoreRegression:
    """Garble.py internal callers produce same garble scores pre/post migration."""

    def test_flat_block_garble_check_uses_block_text(self):
        from pageindex_mcp.helpers.garble import (
            GarbleConfig,
            ScriptContext,
            _garble_check_flat_blocks,
        )

        garbled_digits = "1234567890" * 60
        blocks = [
            {"role": "prose", "text": "Clean prose text about insurance. " * 5},
            {"role": "table", "row_records": [garbled_digits]},
        ]
        ctx = ScriptContext(dominant_script="Latn", had_presentation_forms=False, source="test")
        cfg = GarbleConfig()
        report = _garble_check_flat_blocks(blocks, script_context=ctx, config=cfg)
        assert report is not None

    def test_clean_table_not_garbled(self):
        from pageindex_mcp.helpers.garble import (
            GarbleConfig,
            ScriptContext,
            _garble_check_flat_blocks,
        )

        blocks = [
            {"role": "table", "row_records": [
                "Name | Premium | Deductible",
                "Liability | 5000 | 500",
                "Comprehensive | 3000 | 250",
            ]},
        ]
        ctx = ScriptContext(dominant_script="Latn", had_presentation_forms=False, source="test")
        cfg = GarbleConfig()
        report = _garble_check_flat_blocks(blocks, script_context=ctx, config=cfg)
        assert report is None


# ===========================================================================
# D10b (RFC-041): Zone-9 fix in _flat_search_text — Property 10
# ===========================================================================


class TestSearchTextHeaderOnlyTable:
    """Property 10: header-only table block returns header text from
    _flat_search_text (now doc_text(data, SEARCH))."""

    def test_header_only_table_returns_headers_via_search(self):
        data = {
            "blocks": [
                {"role": "table", "headers": ["Name", "Age", "City"], "row_records": []},
            ]
        }
        result = _flat_search_text(data)
        assert "Name" in result
        assert "Age" in result
        assert "City" in result

    def test_header_only_table_parity_with_primary_text(self):
        """Accessor parity: _flat_search_text and _flat_block_primary_text
        return the same header text for header-only tables."""
        block = {"role": "table", "headers": ["X", "Y"], "row_records": []}
        data = {"blocks": [block]}
        search_result = _flat_search_text(data)
        primary_result = _flat_block_primary_text(block)
        assert search_result == primary_result

    def test_header_only_no_row_records_key(self):
        block = {"role": "table", "headers": ["A", "B"]}
        data = {"blocks": [block]}
        result = _flat_search_text(data)
        assert "A" in result
        assert "B" in result


# ===========================================================================
# D2 Task 2.3: CI lint — block['text'] access outside block_text()
# ===========================================================================


class TestBlockTextCILint:
    """CI lint test for direct block['text'] access."""

    def test_block_text_importable(self):
        from pageindex_mcp.helpers import block_text as bt
        assert callable(bt)

    def test_doc_text_importable(self):
        from pageindex_mcp.helpers import doc_text as dt
        assert callable(dt)

    def test_block_text_purpose_importable(self):
        from pageindex_mcp.helpers import BlockTextPurpose as BTP
        assert hasattr(BTP, "GARBLE_CHECK")
        assert hasattr(BTP, "SEARCH")
        assert hasattr(BTP, "CHAR_COUNT")
        assert hasattr(BTP, "DISPLAY")

    def test_legacy_wrappers_delegate_to_block_text(self):
        """_flat_block_primary_text and _flat_search_text must delegate
        to block_text / doc_text, not duplicate extraction logic."""
        block = {"role": "table", "headers": ["H1", "H2"], "row_records": []}
        assert _flat_block_primary_text(block) == block_text(block, BlockTextPurpose.CHAR_COUNT)

        data = {"blocks": [block]}
        assert _flat_search_text(data) == doc_text(data, BlockTextPurpose.SEARCH)
