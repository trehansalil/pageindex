<!-- Space: CITRA -->
<!-- Title: RFC-037: Verdict Cas Unification -->
<!-- Folder: RFCs -->

---
id: "RFC-037"
title: "Verdict CAS Unification and Ledger Compliance Closure"
type: rfc
status: draft
date: "2026-08-24"
plan-impact: "yes"
tags:
  - rfc
  - verdict
  - storage
  - compliance
  - registry
aliases:
  - "RFC-037"
  - "Verdict-CAS-Unification"
governs:
  - "[[design-rfc037-verdict-cas-unification]]"
  - "[[tasks-rfc037-verdict-cas-unification]]"
supersedes: []
---

## Context

The post-fix-11 architecture defect zones audit (2026-08-24) identified Zone 5 as the highest-severity remaining defect zone (high, 10 bugs). The verdict system has three interacting instability sources that have generated a chain of fix–regress cycles spanning [[RFC-021]] through [[RFC-036]]:

1. **Dual-CAS divergence.** Two independent compare-and-swap guards protect verdict writes — one on the MinIO sidecar (`_verdict_cas_guard` in `storage/verdict.py:91-118`, strict `>` on `verdict_computed_at`) and one in the Postgres upsert (`_UPSERT_SQL` in `registry/queries.py:19-84`, `>=` on the same timestamp). The guards never consult each other. A tie on the sidecar blocks the write while the same tie on Postgres allows it, leaving the two substrates holding different verdicts indefinitely.

2. **Verdict ledger HR2 erasure gap.** The `verdicts/{sha256}.json` file in MinIO, used by `apply_verdict_hysteresis` (`helpers/verdict.py:449-496`) to anchor verdicts across re-ingestion cycles, is excluded from the 7-step erasure cascade in `delete_doc` (`storage/documents.py:141-315`). This violates CLAUDE.md Hard Rule 2: "Right-to-erasure must cascade across every derived store."

3. **Threshold oscillation without stable anchoring.** `PASS_MAX_LEAF_RATIO` was widened three times (0.17 → 0.20 → 0.30) chasing oscillation on different documents each time, because the verdict ledger / hysteresis mechanism failed to provide stable anchoring after corpus wipes.

Compounding the problem, three concurrent registry writers funnel through `_UPSERT_SQL` — `_upsert_registry_row` (`worker/registry_mirror.py`), `reconcile_registry_drift()` (`registry_backfill/reconcile.py`), and `_drain_verdict_retry_queue` (`reconcile.py`) — so any fix to the SQL CAS must be inherited by all three paths. Additionally, two independently defined but identical verdict priority maps exist: `_LEDGER_VERDICT_PRIORITY` (`storage/verdict.py:469`) and `_LEDGER_PRIORITY` (`helpers/verdict.py:444`), both mapping `{"PASS": 3, "MARGINAL": 2, "FAIL": 1, "ERROR": 0}`.

Prior RFCs that touched this area: [[RFC-025]] (hysteresis via prior-verdict anchoring, defeated by corpus reingestion wiping the prior-verdict store), [[RFC-026]] (GHV-TKV-Tarif flapping on identical tree after wipe, verdict gate hardening), [[RFC-034]] (write-visibility barrier).

## Goals

- Eliminate dual-CAS divergence by designating Postgres as the single authoritative verdict arbiter and demoting the MinIO sidecar CAS to a passive archive.
- Close the HR2 compliance gap by including the `verdicts/{sha256}.json` prefix in the erasure cascade.
- Remove the redundant verdict ledger write path and the hysteresis mechanism, since the SQL max-priority-wins guard subsumes their function.
- Eliminate the duplicate `_LEDGER_VERDICT_PRIORITY` / `_LEDGER_PRIORITY` maps to prevent future silent divergence.
- Stabilize the `PASS_MAX_LEAF_RATIO` threshold by removing the oscillation pressure that came from lacking a stable anchoring mechanism.

## Non-Goals

- Changing the `PASS_MAX_LEAF_RATIO` value itself (currently 0.30). The goal is to make the system stable at whatever value it has, not to recalibrate the threshold.
- Redesigning the verdict scoring logic in `classify_verdict`. This RFC only changes where verdicts are stored and arbitrated, not how they are computed.
- Adding new verdict dimensions (depth-adequacy, content-regression detection). Those belong in future RFCs.
- Backfilling provenance metadata on legacy documents.

## Glossary

| Term | Definition |
|------|------------|
| CAS Guard | Compare-and-swap guard that prevents a stale verdict from overwriting a newer one, using timestamp comparison. |
| Verdict Ledger | The `verdicts/{sha256}.json` file in MinIO that records the highest-priority verdict ever computed for a given content hash. |
| Sidecar | The `.meta.json` file stored alongside the processed document JSON in MinIO (`processed/{doc_id}.meta.json`). |
| Hysteresis Anchoring | The mechanism in `apply_verdict_hysteresis` that overrides a newly computed verdict with a prior higher-priority verdict from the ledger. |
| Max-Priority-Wins | The policy that a verdict can only be upgraded (ERROR → FAIL → MARGINAL → PASS), never downgraded, across re-ingestion cycles. |
| Registry Writer | Any code path that calls `_UPSERT_SQL` to persist verdict and metadata to Postgres. Three exist: live dual-write, cron reconciler, and retry queue drain. |

## Requirements

### Requirement 1: Single Verdict Arbiter

**User Story:** As a pipeline operator, I want a single authoritative verdict decision point, so that MinIO and Postgres never hold different verdicts for the same document.

#### Acceptance Criteria

1. WHEN a verdict is computed and upserted, THE `_UPSERT_SQL` query SHALL apply max-priority-wins logic: if the existing row's verdict has higher priority than the incoming verdict (PASS > MARGINAL > FAIL > ERROR), the existing verdict SHALL be preserved.
2. WHEN the Postgres upsert `RETURNING` clause returns the winning verdict, THE `save_doc_meta` function SHALL backfill the MinIO sidecar with the Postgres-arbitrated verdict (not the locally-computed one).
3. WHEN the MinIO sidecar is written, THE `_verdict_cas_guard` function SHALL always return `False` (allow write), since the sidecar is no longer the arbiter.
4. WHILE three concurrent registry writers exist, ALL three SHALL inherit the max-priority-wins guard automatically through `_UPSERT_SQL` with no additional per-writer changes.

### Requirement 2: HR2 Erasure Cascade Compliance

**User Story:** As a compliance officer, I want document erasure to remove all verdict data, so that no trace of an erased document's quality assessment survives.

#### Acceptance Criteria

1. WHEN `delete_doc` executes, THE erasure cascade SHALL include a step that removes `verdicts/{sha256}.json` for the document's content hash.
2. IF the sha256 hash is not available from the processed document (already deleted), THEN `delete_doc` SHALL attempt to read the sha256 from the sidecar `.meta.json` before the sidecar is deleted, and log a warning if neither source is available.
3. AFTER the erasure cascade completes, THE `verdicts/` prefix SHALL contain no object keyed to the deleted document's sha256.

### Requirement 3: Priority Map Deduplication

**User Story:** As a maintainer, I want a single source of truth for verdict priority ordering, so that priority maps cannot silently diverge.

#### Acceptance Criteria

1. WHEN verdict priority ordering is needed, ALL call sites SHALL reference a single `VERDICT_PRIORITY` constant defined in `helpers/types.py`.
2. THE duplicate definitions `_LEDGER_VERDICT_PRIORITY` (`storage/verdict.py:469`) and `_LEDGER_PRIORITY` (`helpers/verdict.py:444`) SHALL be removed.
3. A CI linting check SHOULD flag any file that defines a local verdict priority mapping instead of importing the canonical constant.

### Requirement 4: Ledger and Hysteresis Removal

**User Story:** As a developer, I want the verdict ledger and hysteresis mechanism removed after the SQL max-priority-wins guard is validated, so that the codebase has one verdict stability mechanism instead of three competing ones.

#### Acceptance Criteria

1. AFTER the SQL max-priority-wins guard is validated by a full corpus scoring cycle with zero verdict downgrades, THE `persist_verdict_ledger`, `read_verdict_ledger`, and `apply_verdict_hysteresis` functions SHALL be deleted.
2. WHEN the ledger write path is removed, THE `save_doc_meta` function SHALL no longer call `persist_verdict_ledger`.
3. THE two call sites in `client/indexer.py` that invoke `apply_verdict_hysteresis` (flat path at line ~852-863 and tree path at line ~985-996) SHALL be removed.
4. WHEN the hysteresis mechanism is removed, THE `read_ledger_fn` parameter threading through `_persist_flat_result` and `_persist_tree_result` SHALL be cleaned up.

## Decision Summary

This RFC designates Postgres as the single verdict arbiter via a max-priority-wins SQL CAS guard, demotes the MinIO sidecar CAS to a passive archive backfilled from the Postgres `RETURNING` row, closes the HR2 erasure cascade gap for verdict ledger files, and then removes the now-redundant verdict ledger and hysteresis mechanism. The implementation is sequenced across three releases to maintain safety:

- **D1: SQL max-priority-wins guard.** Add priority comparison logic to `_UPSERT_SQL` verdict columns in `registry/queries.py`. When the existing row's verdict has higher priority, the `ON CONFLICT` clause preserves the existing verdict fields. The `RETURNING` clause already returns verdict columns; the caller uses the returned (arbitrated) verdict for downstream writes. (~8 lines changed.)

- **D2: HR2 verdict ledger erasure.** Add a `verdicts/{sha256}.json` removal step to the `delete_doc` cascade in `storage/documents.py`, positioned after the sidecar read (to extract sha256) and before the sidecar deletion. (~15 lines added.)

- **D3: Ledger function removal.** Remove `persist_verdict_ledger`, `read_verdict_ledger`, and `_LEDGER_VERDICT_PRIORITY` from `storage/verdict.py`. (~-90 lines.)

- **D4: Hysteresis removal.** Remove `apply_verdict_hysteresis` and `_LEDGER_PRIORITY` from `helpers/verdict.py`, and remove the two call sites in `client/indexer.py` that invoke it. (~-50 lines.)

- **D5: Sidecar CAS collapse.** Simplify `_verdict_cas_guard` in `storage/verdict.py` to unconditionally return `False`, then inline/remove the function. (~-15 lines.)

- **D6: Priority constant consolidation.** Define a single `VERDICT_PRIORITY: dict[str, int]` constant in `helpers/types.py`, imported by all consumers. (~5 lines added, replaces two duplicate definitions.)

### Implementation Sequencing

**Release A (immediate):** D1 (SQL max-priority-wins) + D2 (HR2 erasure fix) + D6 (constant consolidation). These are safe to deploy independently and close the compliance gap.

**Release B (validation):** Full corpus scoring cycle confirming zero verdict downgrades under the new SQL guard. No code changes; operational validation only.

**Release C (cleanup):** D3 (ledger removal) + D4 (hysteresis removal) + D5 (CAS collapse). Gated on successful Release B validation.

## Consequences

- The sidecar `.meta.json` files become a passive archive; any tooling that reads verdicts from MinIO sidecars directly will continue to work because the sidecar is backfilled from the Postgres `RETURNING` row.
- A document that erroneously reached PASS and needs re-evaluation will require a manual registry reset — a `--force-recompute` flag that sets `verdict_computed_at` to epoch+1 and verdict to an empty string, bypassing the max-priority-wins guard.
- The three concurrent registry writers (`_upsert_registry_row`, `reconcile_registry_drift`, `_drain_verdict_retry_queue`) automatically inherit the max-priority-wins guard with no per-writer code changes, since they all funnel through `_UPSERT_SQL`.
- Net code reduction: approximately -140 lines (removing ledger, hysteresis, sidecar CAS) after adding ~25 lines (SQL guard, HR2 fix, consolidated constant).
- The `PASS_MAX_LEAF_RATIO` threshold becomes stable because the max-priority-wins guard prevents verdict oscillation on re-ingestion, removing the pressure to widen the threshold.

### Hard Rule Constraints (CLAUDE.md — Binding)

- **HR2 (right-to-erasure):** D2 closes the gap. After this RFC, `delete_doc` purges `uploads/`, `processed/*.json`, `processed/*.meta.json`, `verdicts/{sha256}.json`, Redis cache, and registry row.
- **HR5 (never silently persist a low-quality tree):** Unaffected. `validate_tree()` continues to run before `save_doc`; the verdict system is downstream of the quality gate.

## Traceability

| Artifact | Reference |
|----------|-----------|
| Design   | [[design-rfc037-verdict-cas-unification]] |
| Tasks    | [[tasks-rfc037-verdict-cas-unification]] |
| Supersedes | Aspects of [[RFC-025]] D0, [[RFC-026]] D3 (hysteresis mechanism) |
| Audit    | [Zone 5 — Verdict Threshold Oscillation and Dual-CAS Divergence](../../audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-24_POST-FIX-11.md) |
