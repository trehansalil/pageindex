---
zone_name: Verdict Persistence Dual-Writer (MinIO Sidecar vs Postgres Registry)
severity: high
bug_count: 2
status: improved
audit_date: 2026-09-01
audit_run: POST-RFC041
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-01_POST-RFC041.md
key_files:
  - src/pageindex_mcp/worker/registry_mirror.py
  - src/pageindex_mcp/storage/verdict.py
  - src/pageindex_mcp/registry/queries.py
  - src/pageindex_mcp/registry_backfill/backfill.py
  - src/pageindex_mcp/registry_backfill/reconcile.py
tags:
  - zone-spec
  - high
  - verdict
  - dual-writer
  - cas
scorecard_verdict: regressed
scorecard_date: 2026-09-01
scorecard_run: POST-RFC041
validation_date: 2026-09-01
validation_notes: "MinIO CAS guard REMOVED entirely (not divergent). No MinIO-side guard during
  Postgres degradation. Worse than claimed. Five write paths partially
  confirmed: multiple call chains funnel through 2 functions."
---
## Mechanism

Verdict is persisted to two independent stores (MinIO sidecars and Postgres registry) with different CAS guard semantics. While `_upsert_registry_row` implements a three-tier degradation cascade, the fundamental dual-writer pattern persists: any code path that writes to the MinIO sidecar without going through the Postgres-authoritative path creates a divergence window.

1. **CAS guard divergence:** MinIO CAS guard uses strict `>` on timestamp while Postgres uses `>=`, so tie scenarios cause permanent divergence (Chain 24).

2. **Incomplete guard enforcement:** Five independent code paths write verdict across two stores but only Postgres enforces CAS priority via `_UPSERT_SQL`; MinIO sidecar has no priority comparison despite backfill.py:145 asserting it does (Chain 10).

3. **Degradation pattern:** When Postgres path degrades, `_upsert_registry_row` stamps `consistency_regime=sidecar-only` and queues Redis retry, but during the degraded window a lower-priority re-ingestion can land in MinIO unchecked. Any new write path to MinIO sidecars that bypasses `_upsert_registry_row` overlay re-opens divergence.

## History

- **Chain 10:** RFC-037 D1/D5 added dual guards but left `save_doc_meta` with no priority comparison despite backfill.py:145 asserting it does; five code paths write verdict with only one enforcing CAS.
- **Chain 24:** MinIO strict `>` and Postgres `>=` on timestamp create tie-scenario permanent divergence; `PASS_MAX_LEAF_RATIO` widened 3 times chasing oscillation from verdict ledger/hysteresis failure after corpus wipes.

## Code Evidence

1. **_upsert_registry_row** at registry_mirror.py:56-200 implements three-tier cascade: disabled/DSN-missing stamps `consistency_regime=sidecar-only`, pool-not-ready queues verdict retry, normal path CAS-upserts via `upsert_doc` then backfills sidecar with postgres-authoritative stamp.

2. **_UPSERT_SQL** at registry/queries.py:127 is the Postgres CAS guard.

3. **Removed MinIO guard:** test_verdict_cas_guard_not_importable at test_architecture_guards.py:415-419 confirms old MinIO `_verdict_cas_guard` removed.

## Key Files

| File | Role |
|------|------|
| registry_mirror.py:56-200 | _upsert_registry_row three-tier degradation cascade |
| verdict.py | Verdict data structure and storage logic |
| queries.py:127 | _UPSERT_SQL with Postgres CAS guard (>=) |
| backfill.py | Backfill logic with incomplete CAS comment |
| reconcile.py | Reconciliation between MinIO and Postgres |
| test_architecture_guards.py:415-419 | Verification that MinIO CAS guard removed |
