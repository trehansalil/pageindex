"""RFC-022 block-type tests: verdict blind-spot / image-routing / OCR-splice
fixes (design-rfc022-run5-verdict-bugfixes.md, Properties 1-5).

Consolidated from test_rfc022_b1.py (flat-doc synthetic structure + tree
garble empty-guard), test_rfc022_b2.py (image extension routing + QF2a gate
ordering), and test_rfc022_b3.py (table-block OCR splice into synthetic
structure).

`_synthesize_flat_structure` below mirrors the inline synthesis in
client.py's `index()` (client.py:1102-1107) verbatim, using `_flat_block_primary_text`
(the current, post-B3 measurement) since that function already falls back to
the raw `"text"` key for plain prose blocks — it is a strict superset of the
pre-B3 `b.get("text", "")` measurement used only by the regression guard in
`TestFlatBlockText`.

The B2-A extension override landed in client.py as
`apply_image_ext_content_class_override` (RFC-033 D7); tests call it
directly rather than mirroring the conditional locally, since a locally
mirrored predicate is exactly what let the override's total absence from
client.py go undetected until Run-15.
"""

from pageindex_mcp.client import apply_image_ext_content_class_override
from pageindex_mcp.client import images as _img
from pageindex_mcp.helpers import (
    BULK_PROFILE,
    _classify_image_verdict,
    _flat_block_primary_text,
    _flatten_tree_text,
    classify_verdict,
)

from tests._garble_compat import check_garble


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
        assert reason in ("", "cat_b_promoted")

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
