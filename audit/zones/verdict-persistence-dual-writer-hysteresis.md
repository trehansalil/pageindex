---
zone_name: Verdict Persistence Dual-Writer & Hysteresis Fragility
severity: high
bug_count: 6
status: regressed
audit_date: 2026-09-02
audit_run: POST-RFC043
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-02_POST-RFC043.md
key_files:
  - src/pageindex_mcp/storage/verdict.py
  - src/pageindex_mcp/worker/registry_mirror.py
  - src/pageindex_mcp/registry/queries.py
  - src/pageindex_mcp/storage/documents.py
tags:
  - zone-spec
  - high
  - persistence
  - dual-writer
  - cas-gap
  - hysteresis
scorecard_verdict: regressed
scorecard_date: 2026-09-02
scorecard_run: POST-RFC043
---
## Mechanism

Three distinct verdict-persistence writers with different consistency models create structural fragility. Three causes:

1. **Asymmetric consistency design**:
   - `save_doc_meta` (storage/verdict.py:78-198): consistency_model='eventual', no _confirm_write_visible barrier, no CAS priority comparison
   - Postgres _UPSERT_SQL: VERDICT_PRIORITY-based CAS (overwrites only when priority >= existing)
   - MinIO sidecar backfill has no CAS guard
   - These stores can transiently disagree; convergence depends on best-effort backfill

2. **Silent failure in persistence layer**:
   - `_upsert_registry_row` (registry_mirror.py:136-315) returns False on every degraded path but never raises
   - Until RFC-042 D3, callers had no signal to distinguish success from failure
   - `reconcile._drain_verdict_retry_queue` unconditionally deleted retry keys regardless
   - Permanently lost verdicts on transient failures

3. **Hysteresis ledger destruction**:
   - Hysteresis (RFC-025 D0) reads prior verdict from processed/*.meta.json
   - Standard corpus reingestion wipes this store
   - Destroys the ledger that `find_prior_verdict` scans
   - Causes verdict flapping independent of content changes

## Code Evidence

```python
# save_doc_meta (storage/verdict.py:78-198)
# Eventual consistency with no barriers or CAS
def save_doc_meta(...):
    consistency_model='eventual'
    # No _confirm_write_visible call
    # Unconditional merge of verdict/verdict_reason/max_leaf_ratio

# _upsert_registry_row (registry_mirror.py:136-315)
# Silent failure swallows exceptions
def _upsert_registry_row(...) -> bool:
    try:
        # Postgres write
        return True
    except:
        REGISTRY_WRITE_FAILURES_TOTAL.inc()
        enqueue_verdict_retry(...)
        return False  # Never raises

# Postgres _UPSERT_SQL (registry/queries.py:24-38)
# Priority-based CAS
CASE
  WHEN VERDICT_PRIORITY[incoming] >= VERDICT_PRIORITY[existing]
    THEN (incoming verdict)
  ELSE (existing verdict)
END

# _confirm_write_visible (storage/minio_ops.py:37-58)
# Used by save_doc/save_flat_doc but explicitly NOT by save_doc_meta
```

## Key Files

| File | Role |
|---|---|
| src/pageindex_mcp/storage/verdict.py | Eventual-consistency sidecar (no CAS) |
| src/pageindex_mcp/worker/registry_mirror.py | Postgres writer with silent failures |
| src/pageindex_mcp/registry/queries.py | Authoritative store with CAS |
| src/pageindex_mcp/storage/documents.py | Direct persistence callers (10+) |

## Evidence Chain

- **Chain 11** (RFC-022→RFC-025): Write-visibility barrier over-provisioned then removed asymmetrically; sidecar has no CAS guard
- **Chain 12** (RFC-025 D0→RFC-026): Reingestion wipes processed/*.meta.json; GHV-TKV-Tarif flapped PASS→MARGINAL on identical tree
- **Chain 22** (RFC-042 D3): Discovered 10+ direct save_doc_meta bypass callers contradicting single-writer claim
- **Chain 26** (RFC-042 D3): Reconcile retry loop silently dropped verdicts; added bool return and retention logic
