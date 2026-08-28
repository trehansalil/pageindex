---
zone_name: Dual-Writer Verdict Persistence and Consistency Model Split
severity: high
bug_count: 4
status: new
audit_date: 2026-08-28
audit_run: POST-FIX-WAVE3
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md
key_files:
  - src/pageindex_mcp/storage/verdict.py
  - src/pageindex_mcp/storage/documents.py
  - src/pageindex_mcp/worker/registry_mirror.py
  - src/pageindex_mcp/registry_backfill/reconcile.py
  - src/pageindex_mcp/registry/queries.py
tags:
  - zone-spec
  - high
  - storage
  - consistency
  - dual-writer
scorecard_verdict: regressed
scorecard_date: 2026-08-28
scorecard_run: POST-FIX-WAVE3
---
## Mechanism

Two independent writers (save_doc_meta from isolated converters_cli child subprocess, _upsert_registry_row from long-lived worker parent) target overlapping verdict fields for same doc_id across different process boundaries. **Consistency model is split:**

- Postgres documented authoritative (CAS + RETURNING, max-priority-wins per RFC-037 D5)
- MinIO sidecar is passive archive
- save_doc/save_flat_doc retain `_confirm_write_visible` barrier
- save_doc_meta **deliberately omits** barrier (eventual consistency)
- When registry_enabled=false or pool unavailable, sidecar silently becomes sole source of truth with degraded consistency
- reconcile_registry_drift cron has **load-bearing ordering**: drain Redis verdict retry queue BEFORE MinIO etag diff, or freshly-recovered verdicts get overwritten by stale MinIO reads

## Code Evidence

**verdict.py:78–197** — `save_doc_meta`:
- Lines 84–89: documents 'eventual' consistency with **no** `_confirm_write_visible` barrier
- Contrast: documents.py save_doc line 106 retains barrier
- Line 186: stamps consistency_model='eventual'

**registry_mirror.py:55–164** — `_upsert_registry_row`:
- Lines 87–93: pool-not-ready fallback to sidecar-only with `_enqueue_verdict_retry`
- Lines 136–145: best-effort sidecar backfill from Postgres winning row

**reconcile.py:109–228**:
- Line 156: `_drain_verdict_retry_queue` runs BEFORE MinIO etag diff (lines 163–170)
- **Load-bearing ordering** to prevent overwriting recovered verdicts with stale reads

## Key Files

| File | Role |
|------|------|
| src/pageindex_mcp/storage/verdict.py | save_doc_meta (eventual consistency) |
| src/pageindex_mcp/storage/documents.py | save_doc/save_flat_doc (strong consistency) |
| src/pageindex_mcp/worker/registry_mirror.py | Dual-writer orchestration, fallback logic |
| src/pageindex_mcp/registry_backfill/reconcile.py | Reconciliation with load-bearing ordering |
| src/pageindex_mcp/registry/queries.py | Registry delete (fire-and-forget → timeout) |

## Related Issues

- Chain 20: Erasure manifest missing preloaded/ prefix (ISS-41)
- Chain 21: Registry-delete fire-and-forget (ISS-40)
- Chain 22: save_doc_meta barrier removal created asymmetric consistency
- Chain 23: registry_enabled=false silently changes consistency model

## Critical Dependencies

1. reconcile.py:156 **MUST** run _drain_verdict_retry_queue before MinIO etag diff
2. registry_mirror.py fallback logic only works when at least one store is available

