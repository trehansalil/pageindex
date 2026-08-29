---
zone_name: Verdict Persistence Asymmetry
severity: medium
bug_count: 2
status: improved
audit_date: 2026-08-29
audit_run: POST-FIX-WAVE3-VERIFY
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE3-VERIFY.md
key_files:
  - src/pageindex_mcp/storage/verdict.py
  - src/pageindex_mcp/storage/documents.py
  - src/pageindex_mcp/worker/registry_mirror.py
  - src/pageindex_mcp/storage/queries.py
tags:
  - zone-spec
  - medium
  - persistence
  - consistency
scorecard_verdict: regressed
scorecard_date: 2026-08-29
scorecard_run: POST-FIX-WAVE3-VERIFY
---

## Mechanism

Verdict data is persisted through a two-tier cascade with an explicit process boundary:

1. **Child process:** `save_doc_meta` (MinIO sidecar, eventual consistency, no write-visibility barrier)
2. **Parent process:** `_upsert_registry_row` → `upsert_doc` (Postgres authoritative write, RFC-037 D5 max-priority-wins arbiter)

This creates **three writers with asymmetric consistency guarantees**:
- `save_doc`: MinIO with read-after-write barrier ✓
- `save_doc_meta`: MinIO without barrier (eventual consistency)
- `registry_mirror`: Best-effort sidecar backfill

### Four Sources of Bug

**1. Transient Disagreement Window**
- The sidecar can transiently disagree with Postgres
- Any code path that reads verdict from sidecar without accounting for this window risks staleness
- No transactional boundary between the two writes

**2. Fragile Two-Source Fallback**
- `read_registry_fields` (verdict.py:252-322) implements artifact body (legacy) → sidecar fallback (Zone-5+)
- Works correctly but is fragile
- Any new caller reading verdict directly from MinIO without going through this function sees stale/missing data

**3. Process Boundary Desynchronization**
- `save_doc_meta` can succeed (MinIO sidecar written)
- `_upsert_registry_row` can fail (Postgres not written)
- Stores permanently divergent until `reconcile_registry_drift` runs
- No automatic recovery

**4. Manual Erasure Manifest Drift**
- Erasure operations must manually enumerate every storage prefix via `_ERASURE_MANIFEST`
- When new ingestion routes add prefixes (e.g. preloaded/), the manifest goes out of sync
- Creates silent erasure coverage gaps (ISS-41)

### Legacy Fourth Writer

`write_verdict` (verdict.py:201-232):
- Deprecated (Zone-5)
- Zero live production callers per trace_path
- Only legacy scripts use it
- Could diverge if reactivated

## Code Evidence

### save_doc_meta (verdict.py:78-198)

**Consistency Stamp (line ~186):**
```python
consistency_model='eventual'
```

**Write-Visibility Barrier Removal (line ~192):**
"write-visibility barrier removed. Postgres is the sole verdict authority; the sidecar is archival-only."

**Contrast with save_doc (documents.py):**
`save_doc` retains the `_confirm_write_visible` barrier.

### _upsert_registry_row (registry_mirror.py:56-200)

Runs in **worker parent process** (not child):
1. Calls `upsert_doc` (Postgres CAS)
2. Then best-effort backfills sidecar via `save_doc_meta`
3. Sets `consistency_regime='postgres-authoritative'`

### write_verdict (verdict.py:201-232)

**Deprecation Notice:**
```python
"""Deprecated (Zone-5): thin wrapper that delegates to save_doc_meta...
Retained only for legacy callers (promotion_sweep.run_sweep, 
preprocess_client.recompute_verdicts)."""
```

Live callers: 0 (per trace_path)

### read_registry_fields (verdict.py:252-322)

**Two-Source Fallback Pattern:**
1. Artifact body (for legacy docs)
2. Sidecar fallback (for Zone-5+ docs)

**Zone-5 NOTE:**
"New artifacts lack verdict fields in body; reads fall back to sidecar."

Problem: Any code that skips `read_registry_fields` and reads MinIO directly will miss this logic.

## Evidence History

| Artifact | Finding |
|---|---|
| Chains 7, 8 | Theme recurrence: asymmetric consistency |
| save_doc_meta | Removed `_confirm_write_visible` barrier (line ~186 stamps 'eventual') |
| reconcile.py | Load-bearing ordering fix: drain verdict-retry queue BEFORE MinIO etag diff scan |
| New ingestion routes | preloaded/ prefix never added to erasure manifest (ISS-41) |
| Registry-delete | Historically fire-and-forget; logs success on silent failure (ISS-40) |
| write_verdict | Zero live production callers per trace_path |

## Process Boundary Risk

```
Child Process (converters_cli):
  save_doc_meta()  ← MinIO sidecar written
  ↓ (async / eventual consistency)
  
Parent Process (worker):
  _upsert_registry_row() ← Postgres authoritative
    upsert_doc()
    ↓
    save_doc_meta() backfill (best-effort)
```

**Gap:** If parent process upsert_doc fails, the sidecar write from child process is orphaned.

## Erasure Manifest Drift

`_ERASURE_MANIFEST` (manually maintained tuple of storage prefixes):

**Current Coverage:**
- uploads/
- processed/*.json
- processed/*.meta.json
- Redis cache
- Documented backups

**Coverage Gap (ISS-41):**
- preloaded/ prefix added for new ingestion route
- Not added to erasure manifest
- Silent erasure failure for documents in preloaded/

## Related Chains

- Chain 7: Initial persistence mismatch
- Chain 8: Reconciliation asymmetry
