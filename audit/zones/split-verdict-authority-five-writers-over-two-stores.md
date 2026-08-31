---
zone_name: Split verdict authority: five writers over two stores
severity: critical
bug_count: 5
status: regressed
audit_date: 2026-08-29
audit_run: POST-FIX-WAVE4
scorecard_verdict: regressed
scorecard_date: 2026-08-29
scorecard_run: POST-FIX-WAVE4
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE4.md
key_files:
  - src/pageindex_mcp/storage/verdict.py
  - src/pageindex_mcp/worker/registry_mirror.py
  - src/pageindex_mcp/registry/queries.py
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/registry_backfill/backfill.py
  - promotion_sweep.py
tags:
  - zone-spec
  - critical
  - verdict
  - multi-writer
  - cas-guard
  - consistency
---

## Mechanism

The authoritative verdict for a document is written by five independent code paths across two stores:

1. **MinIO sidecar** `processed/<id>.meta.json`
2. **Postgres** `doc_registry` row

Only one of them enforces the max-priority-wins CAS. The others are unconditional read-merge-writes.

### The Five Writers

**Writer 1 & 2: Child Process → Sidecar (Optimistic)**
- `_persist_tree_result` / `_persist_flat_result` call `save_doc_meta` from the isolated `converters_cli` child
- Child has no Postgres pool, so writes optimistically and unconditionally to sidecar
- No CAS guard

**Writer 3: Worker Parent → Postgres (Authoritative)**
- Runs `_upsert_registry_row` which performs real CAS (`_UPSERT_VERDICT_CAS` with VERDICT_PRIORITY guard)
- Best-effort backfills sidecar with winning Postgres values
- Second write to same key; authoritative and capable of silently reverting the first

**Writer 4 & 5: Background Jobs (No CAS)**
- `registry_backfill/reconcile.py:76-82`
- `registry_backfill/backfill.py:161, :323` (sidecar-only self-heal, no CAS)
- `promotion_sweep.py:124, :141` (save_doc_meta + upsert_doc)

### False Belief: save_doc_meta Arbitrates

The false belief documented at **registry_backfill/backfill.py:145**:
> "The CAS guard in save_doc_meta protects against clobbering a newer verdict"

Reality: `save_doc_meta` contains **no priority comparison at all** — it merges 'verdict' from meta over existing in a plain `_MERGE_FIELDS` loop.

**Verified by grep:** VERDICT_PRIORITY is absent from storage/verdict.py:78-190. Only docstring mention of "priority" is at line 98 describing Postgres.

### Dual Arbitration Semantics

Two different UPDATE bodies exist for Postgres:

- `_UPSERT_VERDICT_CAS` — Enforces priority guard (RFC-037)
- `_UPSERT_VERDICT_OVERRIDE` — Bypasses priority while keeping processed_at CAS

Selected at **registry/queries.py:155** based on call context.

Two arbitration semantics per table; inconsistent comparison operators (> vs >=) in prior refactors (RFC-037).

## Code Evidence

### save_doc_meta: No Priority Logic

**storage/verdict.py:78-190**

```python
def save_doc_meta(doc_id: str, meta: dict, *, ...) -> ...:
    """Consistency: eventual - no _confirm_write_visible call"""
    # Removed: Write-visibility barrier
    existing = read_doc_meta(doc_id)
    for field in _MERGE_FIELDS:
        if field in meta:
            existing[field] = meta[field]  # Unconditional merge
    return _write_doc_meta_atomic(doc_id, existing)
```

Grep confirms VERDICT_PRIORITY never appears in this file.

### Child Process Writes (Optimistic)

**client/indexer.py:1302 (tree), ~1141 (flat)**

```python
# Isolated converters_cli child with no Postgres pool
result = await _persist_tree_result(state, ...)  # Calls save_doc_meta
```

### Worker Backfill: Second Write, Authoritative

**worker/registry_mirror.py:150-175**

```python
# After _upsert_registry_row (CAS-guarded in Postgres)
_backfill_sidecar_from_winning_row(doc_id, winning_row)
# Second write to same MinIO key; overwrites child's optimistic write
```

### Dual CAS Guards with Inconsistent Operators

**registry/queries.py:86-127**

```python
_UPSERT_VERDICT_CAS = """
    UPDATE doc_registry
    SET verdict = %s, verdict_priority = %s
    WHERE id = %s AND (verdict_priority < %s OR ...)  -- Guards with >
"""

_UPSERT_VERDICT_OVERRIDE = """
    UPDATE doc_registry
    SET verdict = %s  -- NO priority guard
    WHERE id = %s AND processed_at > %s
"""
```

Selected at **queries.py:155**.

### Further Writers Without CAS

**registry_backfill/backfill.py:145**
> "The CAS guard in save_doc_meta..."

Contradicted by lines 161, :323 which call save_doc_meta with no CAS.

**promotion_sweep.py:124, :141** — Direct save_doc_meta calls; no CAS.

## Consequence

A re-ingestion producing a lower-priority verdict:
- ✓ Discarded by Postgres (CAS wins)
- ✗ May still land in sidecar (save_doc_meta has no guard)
- ✗ Corpus audits reading sidecars see different verdict than registry

## Impact

- Inconsistent document verdict between sidecar and registry
- No observable end-to-end verification without reasoning about which writer ran last
- Corpus audit conclusions depend on which store is queried
- RFC-037 D1/D5 (dual-arbitration guards) unresolved at POST-FIX-WAVE3

## Files to Review

| File | Role |
|------|------|
| storage/verdict.py | Optimistic writer; no CAS |
| worker/registry_mirror.py | Authoritative writer; backfill logic |
| registry/queries.py | Dual CAS bodies; inconsistent operators |
| client/indexer.py | Child process optimistic writes |
| registry_backfill/backfill.py | Background self-heal; wrong CAS assumption |
| promotion_sweep.py | Unguarded updates |
