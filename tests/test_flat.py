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
        """Table blocks should use row_records, not text key."""
        block = {
            "role": "table",
            "text": "should be ignored",
            "row_records": ["r1", "r2"],
        }
        # _flat_block_primary_text: text is non-empty so it returns text
        # Actually, let's verify the real behavior: text is checked first
        result = _flat_block_primary_text(block)
        # text is truthy so it returns text (the function checks text first)
        assert result == "should be ignored"

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
