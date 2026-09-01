---
zone_name: Content Measurement Blind Spot (Table Block Text Extraction)
severity: high
bug_count: 3
status: improved
audit_date: 2026-09-01
audit_run: POST-RFC041
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-01_POST-RFC041.md
key_files:
  - src/pageindex_mcp/helpers/flat.py
  - src/pageindex_mcp/helpers/verdict.py
  - src/pageindex_mcp/client/indexer.py
tags:
  - zone-spec
  - high
  - measurement
  - table-extraction
  - blind-spot
scorecard_verdict: regressed
scorecard_date: 2026-09-01
scorecard_run: POST-RFC041
---
## Mechanism

Table blocks intentionally omit the `text` key by design (FLAT-05-C1 decision), storing content in `row_records`/`headers`/`rows` keys instead. Any code path that reads `block.get(text)` directly sees zero characters for every table block, causing systematic under-measurement.

1. **Schema design blind spot:** Any measurement using `block.get(text)` returns 0 chars for table blocks, causing content-volume gates to misclassify documents as low-content.

2. **Self-reinforcing audit failure:** The audit harness historically used the same pattern, so both pipeline and audit reported false-low counts — self-reinforcing feedback loop where operators designed RFCs around non-existent content loss (Chain 26: GHV-TKV-Tarif 375 measured vs 13022 actual chars, 96.1% under-count).

3. **Fix-one-miss-the-other pattern:** Zone-9 fix applied to only `_flat_block_primary_text` but not `_flat_search_text` or third inline site (Chain 12). The `block_text` consolidation closes this structurally, but the design decision remains a trap.

## History

- **Chain 11:** RFC-022 B3 attributed GHV-TKV-Tarif 4267→375 char drop to picture-OCR regression; real cause was table blocks carrying content in headers/rows/row_records not text.
- **Chain 12:** Zone-9 header-only-table fix applied to `_flat_block_primary_text` only.
- **Chain 26:** FLAT-05-C1 design caused naive `block.get(text)` in audit harness; GHV-TKV-Tarif 96.1% under-count; Run 9 audit defaulted all 24 docs to ERROR, fabricated report influenced RFC-015 decisions.

## Code Evidence

1. **block_text** at flat.py:185-258 is canonical single-block text extractor (D2 RFC-041); table blocks extract from `row_records` first, fall back to `headers+rows`, use `block.get(text)` as last resort. Header-only tables return joined headers (Zone-9 fix).

2. **_flat_block_primary_text** at flat.py:285-296 now delegates to `block_text(block, BlockTextPurpose.CHAR_COUNT)`.

3. **_flat_search_text** at flat.py:299-305 now delegates to `doc_text(data, BlockTextPurpose.SEARCH)`. Both are RFC-041 D2 consolidations.

## Key Files

| File | Role |
|------|------|
| flat.py:185-258 | block_text canonical extractor with table-specific logic |
| flat.py:285-296 | _flat_block_primary_text delegating to block_text |
| flat.py:299-305 | _flat_search_text delegating to doc_text |
| verdict.py | Content-volume gates and measurement consumers |
| indexer.py | Integration of text extraction in indexing |
