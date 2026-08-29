---
zone_name: Measurement/Audit Tooling Shared Blind Spots
severity: medium
bug_count: 4
status: regressed
audit_date: 2026-08-28
audit_run: POST-FIX-WAVE3
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md
key_files:
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/storage/verdict.py
  - src/pageindex_mcp/helpers/verdict.py
tags:
  - zone-spec
  - medium
  - audit
  - measurement
  - blind-spot
scorecard_verdict: regressed
scorecard_date: 2026-08-12
scorecard_run: POST
---
## Mechanism

Corpus audit/scoring tooling **inherits the same structural blind spots** as the pipeline it measures. Char-count scoring via `block.get('text','')` is 0 for role='table' blocks in BOTH verdict-promotion code and corpus audit's diagnostic. Scoring harness had process-integrity bug where score-stage never invoked path to consume persisted MinIO metas, defaulting all 24 documents to ERROR with null node_count/chars:

- Audit tooling and pipeline share same content-measurement primitives (block.get('text',''), flat_char_count, node_count)
- No independent ground-truth oracle
- Table blocks store content in rows/cells, not in 'text' field
- Audit harness process bug: defaulted entire corpus run to ERROR
- RFC-025 D4 mandatory pre-publish MinIO re-verification is **process workaround, not root-cause fix**
- Self-reinforcing cycle: bug in pipeline → low measurement → audit confirms low number → operator trusts audit

## Code Evidence

**indexer.py** — Synthetic-structure content builder:
- Measures content for verdict promotion using block.get('text','')
- Same blind spot as audit diagnostic

**verdict.py + helpers/verdict.py**:
- block.get('text','') pattern yields 0 for role='table' blocks
- Affects both synthetic-structure builder and corpus audit harness
- Scoring harness process bug (chain 18): score-stage never invoked read_registry_fields to consume persisted MinIO metas after ingestion

**Memory note: fabricated-corpus-report-2026-07-17.md**:
- Confirms this pattern of audit-tooling coupling to pipeline blind spots

## Key Files

| File | Role |
|------|------|
| src/pageindex_mcp/client/indexer.py | Synthetic-structure char-count measurement |
| src/pageindex_mcp/storage/verdict.py | Audit/score harness verdict fields |
| src/pageindex_mcp/helpers/verdict.py | Verdict promotion measurement |

## Related Issues

- Chain 17: Synthetic-structure builder inherits table block blind spot
- Chain 18: Scoring harness defaulted entire corpus to ERROR without detection until reconciliation
- Chain 19: Pre-publish verification is process workaround not root-cause fix
- Chain 28: Run 9–15 claims of improvement were refuted by later audits

## Self-Reinforcing Bug Cycle

```
Pipeline blind spot (table.get('text','')=0)
        ↓
Audit harness measures same (=0)
        ↓
Reports low char-count as 'verified'
        ↓
Operator trusts audit result
        ↓
Defect never investigated
        ↓
Process workaround (pre-publish re-verification) substitutes for root fix
```

## Audit Evidence Trust Issue

When the audit tool inherits the same blind spot as the thing it measures, both the measurement and the 'verification' become suspect. RFC-025 D4's mandatory pre-publish MinIO re-verification prevents publishing wrong numbers, but does not fix the underlying measurement function.

