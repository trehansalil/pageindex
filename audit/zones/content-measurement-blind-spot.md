---
zone_name: Content Measurement Blind Spot
severity: high
bug_count: 3
status: regressed
audit_date: 2026-08-29
audit_run: POST-FIX-WAVE3-VERIFY
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE3-VERIFY.md
key_files:
  - src/pageindex_mcp/helpers/flat.py
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/storage/verdict.py
tags:
  - zone-spec
  - high
  - measurement
  - blind-spot
  - tables
scorecard_verdict: regressed
scorecard_date: 2026-08-29
scorecard_run: POST-FIX-WAVE3-VERIFY
---

## Mechanism

Multiple code paths across both the ingestion pipeline and the corpus-audit scoring harness use `block.get('text', '')` to measure content volume, which returns **0 chars** for `role='table'` blocks **by design** — table cell content lives in `headers`/`rows`/`row_records` instead.

### The Self-Reinforcing False Measurement Cycle

1. **Pipeline under-measures content** → `flat_char_count` shows low number
2. **Audit tooling confirms the low number** (shares same blind spot) → operator trusts the audit
3. **Operator designs RFCs to fix problems partly created by the measurement bug itself**
4. **Fixes don't work because the root cause was never fixed** → cycle repeats

The correct helpers exist but are not used uniformly across all measurement sites.

### Schema Root Cause

**By-Design Decision (FLAT-05-C1):** Flat-doc blocks with `role='table'` carry their content in:
- `row_records` (list of pipe-delimited row strings)
- `headers` (list of column names)
- **NO 'text' key**

This is intentional for structured data fidelity, but **every measurement site must use a role-aware helper** to extract text from the correct key per role.

### Measurement Impact

When a new measurement site uses naive `block.get('text','')`, it silently under-counts by the **entire table content** — which can be the majority of a document's chars.

**Example:** GHV-TKV-Tarif
- 13,022 raw chars
- 375 measured chars (from 3 tables with no text key)
- **96.1% under-count**

## Code Evidence

### Correct Helper: _flat_block_primary_text (flat.py:174-196)

```python
def _flat_block_primary_text(block):
  if block.get('role') == 'table':
    # Fall back to row_records for table blocks
    # Then to headers for header-only tables (Zone-9 fix)
    return join_row_records(block)
  elif block.get('role') == 'image':
    return block.get('ocr_text', '') + ' ' + block.get('description', '')
  else:
    # Default: prose block text key
    return block.get('text', '')
```

### Correct Helper: _flat_search_text (flat.py:199-221)

```python
def _flat_search_text(block):
  # Handles:
  role = block.get('role')
  if role == 'table':
    return row_records  # Correct for tables
  elif role == 'image':
    return ocr_text + ' ' + description
  else:
    return text key
```

### Naive Pattern (Wrong)

Appears in audit tooling and historically in `content_signals` computation:
```python
block.get('text', '')  # Returns 0 for tables!
```

### Correct Usage (RFC-022 B3 Fix)

client.py lines ~1158-1176:
```python
# Already uses _flat_block_primary_text for verdict computation
```

But audit harness was **not updated to match**.

## Evidence History

| Artifact | Finding |
|---|---|
| Chains 6, 14, 22 | Theme recurrence: measurement blindness |
| GHV-TKV-Tarif | 13,022 raw chars → 375 measured chars (96.1% under-count) |
| Unfallversicherung | Meta counter showing 7,471-7,408 char variance; benefit-comparison table 75% empty cells in persisted form |
| Run 9 corpus scoring | Score-stage never reads MinIO results; all 24 docs default to verdict=ERROR |
| Run 16 reconciliation | Stored PASS verdicts persist uncorrected on docs judged FAIL in fresh reingestion |
| Fabricated Run 9 report | Measurement bug + scoring-harness bug → fabricated corpus report drove RFC-015 |

### Compounding Process-Integrity Bug

The corpus audit harness had a separate but critical bug:
- Score-stage entry point never reads MinIO results
- Arq job handler hands off incorrectly
- All documents defaulted to `verdict=ERROR` despite real PASS/MARGINAL data in MinIO meta

This produced the **fabricated Run 9 corpus report**, which partly drove RFC-015 design decisions based on false data.

## Schema Design vs. Measurement

| Concern | Design Choice | Consequence |
|---|---|---|
| Structured data fidelity | Tables stored as row_records + headers (no text key) | Correct for data preservation |
| Metric consistency | Every measurement site must use role-aware helper | Audit harness missed this requirement |
| Backward compatibility | Some sites still use naive block.get('text','') | Measurement divergence |

## Impact on Verdict Promotion

The content-volume floor (`th.min_marginal_chars`) is evaluated against under-counted metrics:

- Document measures 375 chars (actual: 13,022)
- Promotion gate checks: "375 < min_marginal_chars?"
- Gate fires incorrectly based on false measurement
- Document mis-classifies as low-content → wrong verdict

## Related Chains

- Chain 6: Initial measurement mismatch
- Chain 14: Audit harness discovery
- Chain 22: Storage/metric bifurcation
