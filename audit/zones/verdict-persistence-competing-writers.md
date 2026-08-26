---
zone_name: Verdict Persistence Competing Writers
severity: high
bug_count: 5
status: improved
audit_date: 2026-08-26
audit_run: POST-FIX-12
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-12.md
key_files:
  - src/pageindex_mcp/storage/verdict.py
  - src/pageindex_mcp/worker/registry_mirror.py
  - src/pageindex_mcp/registry/queries.py
  - src/pageindex_mcp/client/indexer.py
tags:
  - zone-spec
  - high
  - hard-rule-2
scorecard_verdict: regressed
scorecard_date: 2026-08-26
scorecard_run: POST-FIX-12
---
## Mechanism

The verdict persistence layer has a competing-writer pattern where the same MinIO key (processed/{doc_id}.meta.json) is written by two different processes. A provisional write occurs from the isolated converters_cli child subprocess via save_doc_meta, then an authoritative backfill write from the worker parent via _upsert_registry_row after Postgres CAS arbitration.

Three stores (MinIO sidecar, Postgres registry, Redis cache) each hold verdict state, written by different processes at different times with different guarantees. Postgres is designated the 'true arbiter' via CAS priority, but the MinIO sidecar is written first by the child process (which has no Postgres pool) and then overwritten by the parent. If the parent's backfill write fails (best-effort, non-fatal per the try/except in _upsert_registry_row), the sidecar retains the child's provisional verdict which may disagree with Postgres.

The ordering is enforced only by async sequencing, not locking. The write-visibility barrier was removed for verdict-field merges in save_doc_meta but 'intentionally retained' for primary artifact writes — an inconsistent durability guarantee. The deprecated write_verdict wrapper still exists as an additional surface that could drift.

## Evidence History

| RFC/Issue | Finding |
|---|---|
| RFC-036 D1 | Shrank `_WRITE_BARRIER_DELAYS` from 4.4s/8.8s to 0.45s and added `_verdict_cas_guard`, but Python-side and SQL-side CAS logic remained asymmetric |
| Flat-doc path | Still triple-writes, bypassing consolidation |
| Converters_cli boundary | Identified as additional race surface not covered by CAS guard |
| RFC-027 task 4.2 | `chunked_docling_timeout_s` created but never wired to worker.py (marked complete in tasks file) |
| Hard Rule 2 | Registry upsert with empty verdict string can overwrite previously-FAIL-verdicted documents, reintroducing them to queryable results |

## Code Evidence

**save_doc_meta** (storage/verdict.py:78-185) — Dual-write surface
```python
# Invoked from both converters_cli child and worker parent
# Comment at ~line 180: 
# "Zone-4 Phase 3: write-visibility barrier removed. 
#  Postgres is the sole verdict authority; the sidecar is archival-only"
```

**_upsert_registry_row** (registry_mirror.py:55-155) — Best-effort backfill
```python
winning = await upsert_doc(fields, force_verdict_override=_force_override)
if winning:
    # Backfill is best-effort inside try/except
    await asyncio.to_thread(save_doc_meta, doc_id, winning)
```

**upsert_doc** (registry/queries.py:130-184) — Two SQL variants
```python
sql = _UPSERT_OVERRIDE_SQL if force_verdict_override else _UPSERT_SQL
# Two SQL variants: one with CAS guard, one without
```

**Registry upsert vulnerability**
```python
# meta.get('verdict', '') could overwrite a previously-FAIL-verdicted document
# Reintroducing it to queryable results (violates Hard Rule 2)
```

## Key Files

- src/pageindex_mcp/storage/verdict.py
- src/pageindex_mcp/worker/registry_mirror.py
- src/pageindex_mcp/registry/queries.py
- src/pageindex_mcp/client/indexer.py
