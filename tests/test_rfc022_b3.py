"""RFC-022 B3: GHV-TKV OCR splice regression (table blocks starve synthetic
structure of content).

Validates Design Property 5 (design-rfc022-run5-verdict-bugfixes.md):
  Property 5 - OCR splice completeness: the B1 synthetic-structure fallback
  measures a flat block's scoreable text via `_flat_block_text`, which falls
  back to verbalized `row_records` for `role="table"` blocks (and
  `ocr_text`/`description` for `role="image"` blocks) instead of the raw
  `"text"` key those blocks never carry. This prevents table-heavy docs
  (Doc 3, GHV-TKV-Tarif.pdf: 13,022 raw chars -> 375 measured chars under
  the pre-fix `b.get("text", "")` logic, all from 3 tables with no "text"
  key) from starving `classify_verdict` of content it should see.

`_synthesize_flat_structure` mirrors the landed inline synthesis in
client.py's `index()` (client.py:1102-1107) verbatim.
"""

from pageindex_mcp.helpers import _flat_block_text, classify_verdict


def _synthesize_flat_structure(flat_structure: list, blocks: list) -> list:
    # B1+B3 (RFC-022): mirrors client.py:1102-1107.
    if not flat_structure and blocks:
        flat_structure = [
            {"title": "", "text": _flat_block_text(b)}
            for b in blocks
            if _flat_block_text(b).strip()
        ]
    return flat_structure


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


def test_doc3_codepath_produces_enriched_blocks():
    blocks = _table_heavy_doc_blocks()
    structure = _synthesize_flat_structure([], blocks)
    assert len(structure) == len(blocks)
    assert all(node["text"].strip() for node in structure)


def test_enriched_blocks_count_positive_after_splice():
    blocks = _table_heavy_doc_blocks()
    structure = _synthesize_flat_structure([], blocks)
    enriched = [node for node in structure if node["text"].strip()]
    assert len(enriched) > 0
    assert len(enriched) == 3


def test_total_enriched_chars_exceeds_minimum_threshold():
    blocks = _table_heavy_doc_blocks()
    structure = _synthesize_flat_structure([], blocks)
    total_chars = sum(len(node["text"]) for node in structure)
    assert total_chars > 375


def test_table_block_without_text_key_falls_back_to_row_records():
    block = {"role": "table", "row_records": ["a | b | c", "d | e | f"]}
    assert "text" not in block
    text = _flat_block_text(block)
    assert text == "a | b | c\nd | e | f"


def test_pre_fix_text_only_measurement_would_starve_table_blocks():
    # Regression guard: the pre-B3 measurement (b.get("text", "")) sees zero
    # content for table blocks, which is the bug this fix addresses.
    blocks = _table_heavy_doc_blocks()
    pre_fix_chars = sum(len(b.get("text", "")) for b in blocks)
    assert pre_fix_chars == 0
    post_fix_chars = sum(len(_flat_block_text(b)) for b in blocks)
    assert post_fix_chars > 375


def test_classify_verdict_receives_real_content_for_table_heavy_doc():
    blocks = _table_heavy_doc_blocks()
    structure = _synthesize_flat_structure([], blocks)
    verdict, reason = classify_verdict(structure, "flat_table", None)
    # Pre-fix failure mode: content starvation -> a garbling-driven verdict.
    # Post-fix the reason must not be garbling-driven at all (actual observed
    # post-fix result: MARGINAL/depth=1, a legitimate structural reason).
    assert "garbling" not in reason
    assert verdict != "FAIL"
