---
zone_name: Verdict Persistence Competing Writers
severity: high
bug_count: 7
status: audited
audit_date: 2026-08-26
audit_run: POST-FIX-13
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-13.md
key_files:
  - src/pageindex_mcp/storage/verdict.py
  - src/pageindex_mcp/worker/registry_mirror.py
  - src/pageindex_mcp/registry_backfill/reconcile.py
  - src/pageindex_mcp/registry/queries.py
tags:
  - zone-spec
  - high
  - verdict
  - storage
  - consistency
---
## Mechanism

The generative mechanism is **DUAL-STORE EVENTUAL CONSISTENCY WITH ASYMMETRIC CAS GUARDS**. `_upsert_registry_row` (registry_mirror.py:55-155) is the Postgres-authoritative path: it CAS-upserts to Postgres (with RETURNING), then best-effort backfills the MinIO sidecar via save_doc_meta. But `save_doc_meta` (storage/verdict.py:78-185) is a read-merge-write that has no CAS guard — if the Postgres CAS accepted a higher-priority verdict but the sidecar backfill fails (exception caught at registry_mirror.py:144-149), the sidecar retains a stale verdict until reconcile_registry_drift heals it.

When the Postgres pool is unavailable, `_upsert_registry_row` at line 99 queues a Redis retry via `_enqueue_verdict_retry`, but if `_enqueue_verdict_retry` itself throws, the failure is swallowed.

The reingestion pipeline wipes processed/*.meta.json, destroying the hysteresis ledger that find_prior_verdict scans, so verdicts computed with hysteresis context can flap to different values on reingestion.

The write-visibility barrier was removed from save_doc_meta (documented at storage/verdict.py line 176-179) but retained in save_doc/save_flat_doc — a deliberate asymmetry that a future refactor could easily miss.

## Code Evidence

- `_upsert_registry_row` (registry_mirror.py:55-155): Postgres pool check at line 96, Redis retry at line 99 ('await _enqueue_verdict_retry(doc_id, verdict_fields)'). CAS upsert at line 128 ('winning = await upsert_doc(fields, force_verdict_override=_force_override)'). Sidecar backfill at line 133-143 with exception swallowed at line 144-149.

- `save_doc_meta` (storage/verdict.py:78-185): read-merge-write pattern with _read_existing_sidecar at line 113, merge loop at lines 128-170, put_object at line 173. Write-visibility barrier removal documented at line 176-179: 'Zone-4 Phase 3: write-visibility barrier removed. Postgres is the sole verdict authority; the sidecar is archival-only'.

- `reconcile_registry_drift` (reconcile.py:109-228): drains Redis verdict retry queue at line 155, then does incremental O(delta) reconcile using etag-based change detection.

## Related RFCs

RFC-034→036: Write-visibility barrier over-provisioned at 4.4s caused PersistenceNotVisibleError. Zone improved but residual gap remains: MinIO sidecar still lacks CAS equivalent.

RFC-026 D3: Reingestion wipes processed/*.meta.json, breaking hysteresis lookup, causing GHV-TKV-Tarif verdict flap.

Registry upsert_doc unconditionally overwrites verdict with empty string when sidecar omits it (Chain 31).

Cabinet Decision 106/2022 stored verdict=PASS with empty reason despite 40% Latin-mojibake garbling (Chain 32).
