---
zone_name: Dual-Writer Verdict Persistence and Consistency Model Split
severity: high
wave: 3
priority: 5
status: triaged
audit_date: 2026-08-28
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md
tags:
  - zone-spec
  - high
  - wave-3
---
## Mechanism to Eliminate

Three independent verdict writers (_upsert_registry_row, _drain_verdict_retry_queue, save_doc_meta from child) share no unified consistency contract:

1. **Silent verdict loss**: Catch-all exception handler in _upsert_registry_row (line 156) silently drops verdict_fields on Postgres upsert failure. Retry only enqueued on pool-not-ready (line 105), not on transient query/network errors. Verdict data permanently lost.

2. **Orphan server queries**: Registry DELETE SQL (queries.py:244) lacks server-side statement_timeout. asyncpg client-side timeout kills coroutine but leaves Postgres query running.

3. **Silent consistency degradation**: When registry_enabled=false at runtime, consistency model silently degrades from Postgres-authoritative to sidecar-only with eventual consistency. No metric, no sidecar stamp, no alert surface for operators.

## Strategy

Consolidate:
1. Enqueue verdict retry on ALL Postgres failures in _upsert_registry_row, not just pool-not-ready (closes silent verdict loss gap)
2. Add SQL-level statement_timeout to DELETE via transaction block (Postgres kills query server-side)
3. Add REGISTRY_CONSISTENCY_DEGRADED Prometheus gauge (bridged via Redis) incremented when sidecar-only fallback fires (alert surface for operators)
4. Stamp consistency_regime in sidecar during _upsert_registry_row backfill (runtime regime forensically visible)

## Code Targets

| File | What | How | Constraint |
|---|---|---|---|
| `src/pageindex_mcp/worker/registry_mirror.py` lines 156–164 | Catch-all exception handler silently drops verdict_fields | After existing REGISTRY_WRITE_FAILURES_TOTAL.inc() + logger.error, add: if verdict_fields: await _enqueue_verdict_retry(doc_id, verdict_fields). Mirrors pool-not-ready path at line 105. | Must not re-raise; _enqueue_verdict_retry best-effort. Must run AFTER failure metric. |
| `src/pageindex_mcp/registry/queries.py` lines 244–257 | DELETE SQL lacks server-side statement_timeout | Replace _DELETE_SQL constant with _DELETE_SQL_TEMPLATE including SET LOCAL statement_timeout. Delete deletes doc inside async with pool.acquire() / async with conn.transaction() block. Execute SET LOCAL before DELETE. | SET LOCAL requires transaction block. Preserve asyncpg timeout= as client-side backstop. |
| `src/pageindex_mcp/worker/registry_mirror.py` lines 87–107 | No observable metric when registry disabled or pool unavailable (silent consistency degradation) | Add REGISTRY_CONSISTENCY_DEGRADED.inc() in registry_enabled=false early return (line 91) AND pool-not-ready early return (line 100). Call await _mirror_bridged_incr('registry_consistency_degraded'). | Use Redis-bridging pattern like existing registry metrics. _mirror_bridged_incr helper exists at line 182. |
| `src/pageindex_mcp/metrics/definitions.py` lines 136–137 | Missing REGISTRY_CONSISTENCY_DEGRADED gauge | After REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP (line 136), add Gauge: REGISTRY_CONSISTENCY_DEGRADED = Gauge('pageindex_registry_consistency_degraded_total', 'Times registry write path bypassed (registry_enabled=false or pool not ready), leaving MinIO sidecar sole source of truth. Mirrored from Redis on scrape.') | Gauge not Counter (bridged via Redis SET/INCRBY). Re-export from metrics/__init__.py. |
| `src/pageindex_mcp/metrics/__init__.py` lines 44–47 | Re-export REGISTRY_CONSISTENCY_DEGRADED | Add to import block from .definitions (after REGISTRY_FALLBACK_TOTAL) and to __all__ list | Must match name exactly. |
| `src/pageindex_mcp/worker/registry_mirror.py` lines 136–149 | Sidecar backfill does not stamp consistency_regime (indistinguishable from degraded-only) | Before save_doc_meta call (line 141), add winning['consistency_regime'] = 'postgres-authoritative'. In pool-not-ready and registry-disabled early returns (lines 91-107), best-effort save_doc_meta call: await asyncio.to_thread(save_doc_meta, doc_id, {'consistency_regime': 'sidecar-only'}). | Do not add new MinIO write on happy path. Sidecar-only stamp in degraded path is separate best-effort. save_doc_meta preserves existing fields. |
| `src/pageindex_mcp/metrics/sync.py` end of _BRIDGED_METRICS | REGISTRY_CONSISTENCY_DEGRADED must be registered for Redis sync | Add 'registry_consistency_degraded' to _BRIDGED_METRICS dict mapping to REGISTRY_CONSISTENCY_DEGRADED gauge object | Key must match _mirror_bridged_incr name in registry_mirror.py. |

## Wiring Checks

| Symbol | Must Be Imported By | Check Type |
|---|---|---|
| REGISTRY_CONSISTENCY_DEGRADED | `src/pageindex_mcp/metrics/__init__.py`, `src/pageindex_mcp/worker/registry_mirror.py` | import |
| REGISTRY_CONSISTENCY_DEGRADED | `src/pageindex_mcp/metrics/sync.py` | dispatch |
| _enqueue_verdict_retry | `src/pageindex_mcp/worker/registry_mirror.py` | call |
| save_doc_meta | `src/pageindex_mcp/worker/registry_mirror.py`, `src/pageindex_mcp/registry_backfill/reconcile.py` | call |

## Test Requirements

| Test File | What to Test | Assertion Type |
|---|---|---|
| `tests/test_registry.py` | When upsert_doc raises exception, verdict_fields enqueued via _enqueue_verdict_retry (not silently dropped). Mock upsert_doc to raise; assert _enqueue_verdict_retry called with correct doc_id and verdict_fields. | regression |
| `tests/test_registry.py` | When registry_enabled=false or pool=None, REGISTRY_CONSISTENCY_DEGRADED.inc() fires and _mirror_bridged_incr('registry_consistency_degraded') called. Mock metric and bridged-incr; assert both fire. | wiring |
| `tests/test_registry.py` | After successful Postgres upsert, winning dict passed to save_doc_meta contains consistency_regime='postgres-authoritative'. Mock upsert_doc to return winning dict; capture save_doc_meta call; assert key present. | contract |
| `tests/test_registry.py` | When pool not ready, best-effort save_doc_meta with consistency_regime='sidecar-only' attempted. Mock save_doc_meta; verify called with correct regime stamp. | contract |
| `tests/test_registry.py` | delete_doc executes SET LOCAL statement_timeout inside transaction block before DELETE. Mock pool.acquire/conn.transaction/conn.execute; assert SET LOCAL precedes DELETE with correct timeout derived from settings. | contract |
| `tests/test_storage.py` | save_doc_meta preserves existing consistency_regime during read-merge-write when only verdict fields supplied (no consistency_regime). Write sidecar with regime='postgres-authoritative'; call save_doc_meta with verdict-only meta; assert regime preserved. | regression |
| `tests/test_registry.py` | REGISTRY_CONSISTENCY_DEGRADED defined in metrics.definitions, re-exported from metrics/__init__.py, registered in _BRIDGED_METRICS. Import checks across all three modules. | wiring |

## Corpus Validation

- **Affected documents**: All corpus documents re-ingested after fix. consistency_regime field appears in .meta.json sidecars. Documents whose prior ingestion hit transient Postgres failure gain verdict retry and eventual convergence.
- **Expected direction**: stable
- **Spot check count**: 5

## Dependencies

- Verdict-Gate Threshold / Promotion / Override Cascade (Wave 1)
- Garble Detection Cross-Cutting Kernel (Wave 2)

## Complexity

Medium
