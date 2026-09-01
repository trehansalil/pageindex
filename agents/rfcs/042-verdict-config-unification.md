---
id: "RFC-042"
title: "Verdict & Config Unification"
type: rfc
status: draft
date: "2026-09-01"
plan-impact: "yes"
tags:
  - rfc
  - verdict
  - config
  - architecture
aliases:
  - "RFC-042"
  - "Verdict Config Unification"
governs: []
supersedes: []
---

## Context

[[041-recurring-defect-consolidation|RFC-041]] (Recurring Defect Consolidation) structurally closed zones 1/6/7 from the WAVE4 audit (garble funnel, recovery dedup, multi-writer). A POST-RFC041 re-audit on 2026-09-01 identified 7 architecture defect zones with 26 total bugs. Code validation on 2026-09-01 confirmed 4 zones require remediation through this RFC, while Zone 4 (Content Measurement Blind Spot) was verified as resolved by RFC-041 D2.

This RFC addresses:
- **Zone 2:** Verdict Computation & Promotion Cascade (CRITICAL, 6 bugs) — six promotion paths with implicit source-order priority, threshold coupling across config access paths
- **Zone 5:** Verdict Persistence Dual-Writer (HIGH, 2 bugs) — MinIO CAS guard removed entirely (worse than the originally claimed `>` vs `>=` divergence), no guard during Postgres degradation
- **Zone 6:** Config Snapshot vs Live-Read Divergence (MEDIUM-HIGH, 2 bugs) — 121 `os.environ` reads across 17 files bypass the frozen PipelineConfig snapshot
- **Zone 4:** Content Measurement Blind Spot (RESOLVED) — RFC-041 D2 closed the trap; regression guard only needed

A companion [[RFC-043]] will address OCR Recovery + Garble + Erasure zones (Zones 1, 3, 7).

### Relationship to Prior RFCs

- [[041-recurring-defect-consolidation|RFC-041]]: Structurally closed zones 1/6/7; D11 began verdict authority consolidation; D2 closed the content measurement trap
- [[040-verdict-garble-critical-zone-remediation|RFC-040]]: Reordered promotion guards (D2), partially fixed NFKC ordering (D6)
- [[RFC-037]]: Added dual CAS guards (D1/D5) but left MinIO guard incomplete (later removed)

## Goals

1. Eliminate verdict persistence dual-writer by making MinIO a write-through cache of the Postgres-authoritative store
2. Make promotion evaluation order explicit and contractual — changes require an RFC amendment
3. Isolate promotion thresholds so they cannot drift between config access paths during a single document's evaluation
4. Consolidate hot-path config access through the frozen PipelineConfig snapshot
5. Guard the RFC-041 D2 content measurement fix against regression

## Non-Goals

- Changing promotion evaluation outcomes for existing documents (this RFC stabilizes, not changes, behavior)
- Eliminating `os.environ` reads in startup-only code paths (tracing, subprocess management)
- Modifying gate evaluation order in `validate_tree` (separate concern, handled by [[RFC-043]])
- Full config unification across all 17 files (startup-only reads are intentionally left as `os.environ`)
- Addressing OCR recovery, garble detection, or erasure cascade zones (deferred to [[RFC-043]])

## Glossary

| Term | Definition |
|------|------------|
| Write-Through Cache | Pattern where MinIO sidecar is written only as a downstream effect of a Postgres-authoritative write via `_upsert_registry_row` |
| CAS Guard | Compare-And-Swap guard that rejects verdict writes with lower priority than existing data, using `>=` semantics |
| Promotion Path | One of six `_try_*` functions in verdict.py (canonical names `_try_cat_a/b/c`; alias names `_try_ocr_promotion` etc. at verdict.py:399-402) that can promote a document from MARGINAL/FAIL to PASS |
| PROMOTION_ORDER | Module-level constant tuple listing the six promotion paths in explicit priority order |
| VerdictThresholds | Existing frozen dataclass (types.py:483-531) initialized from PipelineConfig via `from_config()`, passed to each `_try_*` as parameters. D2 extends it to absorb `CATEGORY_BC_PROMOTION_THRESHOLD`. |
| Hot-Path | Code executed per-document during indexing (gates.py, pictures.py, indexer.py, tree_split.py, garble.py, verdict.py) |
| Startup-Only | Code run once at import/init time before PipelineConfig freezes (tracing.py, subprocess_mgr.py, minio_client.py, etc.) |
| Frozen Snapshot | PipelineConfig dataclass built at import time from `os.environ`, immutable for the process lifetime |

## Requirements

### Requirement 1: Promotion Evaluation Ordering Contract

**User Story:** As a pipeline operator, I want promotion evaluation order to be explicit and documented, so that refactoring or reordering doesn't silently flip document verdicts.

#### Acceptance Criteria

1. WHEN `apply_promotions` evaluates promotion paths, THE Verdict Module SHALL iterate a module-level `PROMOTION_ORDER` constant, not rely on source-code ordering.
2. IF `PROMOTION_ORDER` is changed, THEN THE change SHALL require an RFC amendment reference in the commit message.
3. WHEN the same `DocumentSignature` is evaluated twice with the same `PipelineConfig`, THE Verdict Module SHALL return identical promotion results (determinism property).

### Requirement 2: Promotion Threshold Isolation

**User Story:** As a pipeline operator, I want promotion thresholds frozen for one document's evaluation, so that mid-evaluation config changes or dual-source reads cannot cause non-deterministic verdicts.

#### Acceptance Criteria

1. WHEN `apply_promotions` begins evaluating a document, THE Verdict Module SHALL use the existing `VerdictThresholds` frozen dataclass (types.py:483-531), initialized via `from_config(PipelineConfig)`. **(Amendment 2026-09-01: VerdictThresholds already exists; D2 extends it, not creates a new class.)**
2. WHILE a document is being evaluated, THE `_try_*` functions SHALL receive thresholds as parameters (already the case — all read from `th: VerdictThresholds`), not read globals or PipelineConfig directly.
3. THE module-level constant `CATEGORY_BC_PROMOTION_THRESHOLD` (config.py:17) SHALL be absorbed into PipelineConfig and read via `VerdictThresholds.from_config()` — this is the sole remaining threshold leak. **(Amendment 2026-09-01: replaces prior AC3 about mid-evaluation reset; threshold isolation is already enforced by VerdictThresholds.)**

### Requirement 3: MinIO Verdict Write-Through

**User Story:** As a pipeline operator, I want verdict persisted to exactly one authoritative store with MinIO as a cache, so that CAS divergence cannot cause permanent verdict conflicts.

#### Acceptance Criteria

1. WHEN a verdict is persisted, THE Registry Mirror SHALL write to Postgres first, THEN write-through to MinIO sidecar using the Postgres-authoritative data.
2. IF Postgres is unavailable, THEN THE Registry Mirror SHALL stamp `consistency_regime=sidecar-only` and queue a Redis retry, AND the MinIO write SHALL still enforce CAS priority ordering from the last known Postgres state.
3. WHEN a lower-priority verdict arrives during Postgres degradation, THE MinIO CAS guard SHALL reject the write.
4. WHEN `reconcile_registry_drift` runs, it currently reads MinIO sidecars and upserts to Postgres. THE Reconciler SHALL continue this direction (MinIO→Postgres via `_upsert_all`) but the `_drain_verdict_retry_queue` sub-path (Postgres→MinIO backfill) SHALL use the write-through path. **(Amendment 2026-09-01: corrected reconcile direction — actual flow is MinIO→Postgres, not the reverse.)**
5. THE `save_doc_meta` function SHALL be callable only from the `_upsert_registry_row` write-through path; architecture guard tests SHALL enforce this. All 10+ existing callers (see D3 decision) SHALL be migrated or eliminated. **(Amendment 2026-09-01: caller count corrected from 4 to 10+.)**

### Requirement 4: Config Access Consolidation

**User Story:** As a developer, I want hot-path code to read config from one source (PipelineConfig), so that boolean parsing divergence and threshold drift cannot cause misdiagnosed verdict shifts.

#### Acceptance Criteria

1. WHEN hot-path code (gates.py, pictures.py, indexer.py, tree_split.py, garble.py, verdict.py) reads a configuration value, IT SHALL use PipelineConfig fields, not `os.environ` directly.
2. IF a new `os.environ` read is added to a hot-path file, THEN THE architecture guard test SHALL fail CI.
3. WHILE startup-only files (tracing.py, subprocess_mgr.py, minio_client.py, constants.py, definitions.py) MAY continue reading `os.environ` directly, THEY SHALL be listed in an explicit allowlist.

### Requirement 5: Content Measurement Regression Guard

**User Story:** As a pipeline operator, I want assurance that table blocks contribute to character counts, so that the RFC-041 D2 fix cannot silently regress.

#### Acceptance Criteria

1. WHEN `block_text` is called on a table block (role="table" or has row_records/headers/rows), IT SHALL return non-zero character count from the structured data, not from `block.get("text")`.
2. IF any code path in src/ accesses `block.get("text")` directly for measurement purposes, THEN THE grep-based guard test SHALL fail CI.

### Requirement 6: Verdict Subsystem Integration Tests

**User Story:** As a developer, I want end-to-end tests covering the full verdict pipeline, so that changes to any component surface as test failures before reaching production.

#### Acceptance Criteria

1. WHEN the verdict pipeline runs end-to-end (evaluate_gates → apply_promotions → finalize_gate_and_route → _upsert_registry_row), THE integration test SHALL verify Postgres and MinIO hold identical verdict data.
2. WHEN Postgres is unavailable mid-processing, THE degradation-mode test SHALL verify sidecar-only mode with CAS guard enforcement and lower-priority write rejection.

## Decision Summary

### D1: Promotion Evaluation Ordering Contract (Requirement 1)

Replace implicit source-order iteration in `apply_promotions` with an explicit `PROMOTION_ORDER` module-level constant listing the six `_try_*` functions in priority order:

1. `_try_image_enrichment` (verdict.py:227)
2. `_try_structural_pass` (verdict.py:272)
3. `_try_cat_a` / alias `_try_ocr_promotion` (verdict.py:290)
4. `_try_cat_b` / alias `_try_flat_promotion` (verdict.py:316)
5. `_try_cat_c` / alias `_try_content_class_promotion` (verdict.py:342)
6. `_try_small_doc` / alias `_try_small_doc_promotion` (verdict.py:363)

**(Amendment 2026-09-01:** Functions 3-6 have canonical names `_try_cat_a/b/c` and `_try_small_doc`; alias names defined at verdict.py:399-402. PROMOTION_ORDER should use the alias names for readability since `apply_promotions` already uses them.**)**

All six still evaluate unconditionally (VG-6 telemetry via `promotion_paths_matched` preserved), but winner selection uses the constant's order, not source-code position. The current source-order dependency is fragile — any refactor that moves a function definition changes promotion priority without warning. The constant makes the contract explicit and testable.

### D2: Promotion Threshold Isolation (Requirement 2)

**(Amendment 2026-09-01:** `VerdictThresholds` (types.py:483-531) already exists as a frozen dataclass with `from_config(PipelineConfig)`. All six `_try_*` functions already receive `th: VerdictThresholds` as a parameter — threshold isolation is largely complete.**)**

The real D2 gap is `CATEGORY_BC_PROMOTION_THRESHOLD` — a bare module-level float constant at config.py:17, imported inside `VerdictThresholds.from_config()` (line 519). This value bypasses PipelineConfig. D2 absorbs it into PipelineConfig so the frozen snapshot covers all thresholds.

Key thresholds already isolated via `VerdictThresholds`: `min_marginal_chars` (content-volume floor), `pass_max_leaf_ratio`, `cat_a_max_leaf_ratio`, `cat_a_max_ocr_noise`, `cat_bc_promotion_threshold`, `small_doc_*` fields. No hysteresis anchoring logic was found in current promotion paths — the 0.30→0.40 range from Zone 2 Chain 19 may have been removed or relocated.

### D3: MinIO Verdict Write-Through Cache (Requirement 3)

**(Amendment 2026-09-01:** `_upsert_registry_row` already calls `save_doc_meta` in 3 places (degradation stamp ×2, backfill ×1) and already stamps `consistency_regime` (`sidecar-only` / `postgres-authoritative`). The write-through pattern and degradation stamping exist. D3's real scope is closing the bypass paths — 10+ call sites across 7 files that write to MinIO without going through `_upsert_registry_row`.**)**

Close all `save_doc_meta` bypass callers:
- `_persist_flat_result` (indexer.py:1166), `_persist_tree_result` (indexer.py:1334) — redirect through `_upsert_registry_row`
- `save_flat_doc` (documents.py:173) — remove direct verdict write; registry flow handles persistence
- `_drain_verdict_retry_queue` (reconcile.py:82) — already in registry flow, keep
- `write_verdict` (verdict.py:232) — deprecated wrapper, remove
- `recompute_verdicts` (preprocess_client.py:369) — redirect through registry flow
- `run_sweep` (promotion_sweep.py:113,124) — redirect through registry flow
- `_enrich_one` (backfill.py:161), `_heal_orphans` (backfill.py:323) — redirect through registry flow

Add CAS guard to the MinIO write path within `_upsert_registry_row` using `>=` semantics matching Postgres `_UPSERT_SQL` (queries.py:127). Make `save_doc_meta` a private function (`_save_doc_meta`) importable only from `registry_mirror.py`, enforced by architecture guard test (consistent with existing `test_architecture_guards.py` patterns).

Alternative considered: removing MinIO verdict storage — rejected because sidecar-only degradation mode is load-bearing for availability during Postgres outages.

### D4: Config Access Consolidation (Requirement 4)

Route all hot-path `os.environ` reads through `PipelineConfig`. Hot-path files: gates.py, pictures.py, indexer.py, tree_split.py, garble.py, verdict.py. Startup-only files remain as live reads — they execute before PipelineConfig freezes. Architecture guard test prevents regression.

The 121 `os.environ` references span 17 files (validated count, worse than the originally claimed 9). Consolidation prioritizes the 6 hot-path files where dual-source reads cause verdict-affecting divergence. Alternative considered: runtime interception of `os.environ` — rejected as too complex for the scope.

### D5: Content Measurement Regression Guard (Requirement 5)

Test-only — no production code changes. Two guards:
1. Functional test: table blocks with `row_records` contribute non-zero chars via `block_text`
2. Grep guard: no external code reads `block.get("text")` directly for measurement

This guards the RFC-041 D2 fix (verified complete: both `_flat_block_primary_text` and `_flat_search_text` delegate to canonical `block_text`, zero external callers of `block.get("text")` remain).

### D6: Verdict Subsystem Integration Tests (Requirement 6)

Three integration test scenarios covering the full verdict write path:
1. **Normal:** doc → evaluate_gates → apply_promotions → finalize_gate_and_route → _upsert_registry_row → verify Postgres+MinIO identical
2. **Degradation:** Postgres unavailable → sidecar-only with CAS guard → lower-priority write rejected
3. **Recovery:** Postgres returns → reconcile_registry_drift reads Postgres → write-through to MinIO

## Implementation Plan

### Sequencing

Foundation first (D4+D5) → Persistence (D3) → Computation (D1+D2) → Integration (D6).

Config consolidation and guards ship before verdict changes so that verdict debugging has a stable config layer. Persistence must stabilize before computation changes, to avoid diagnosing persistence bugs as computation regressions.

### Effort Estimate

| Phase | Deliverables | Effort | Amendment |
|-------|-------------|--------|-----------|
| 1. Foundation | D4 (Config), D5 (Guard) | ~4 hours | unchanged |
| 2. Persistence | D3 (Write-Through) | ~6→8 hours | 10+ callers not 4; expanded scope |
| 3. Computation | D1 (Ordering), D2 (Thresholds) | ~8→5 hours | D2 narrowed to one constant; VerdictThresholds exists |
| 4. Integration | D6 (Tests) | ~4 hours | unchanged |
| **Total** | | **~21 hours** | **(Amendment 2026-09-01)** |

See [[tasks-rfc042-verdict-config-unification]] for the full task breakdown with dependency graph.

## Test Strategy

| Category | Tests | Deliverable |
|----------|-------|-------------|
| Architecture guards | Config access (no os.environ in hot-path), save_doc_meta isolation, block.get("text") grep | D4, D3, D5 |
| Property tests | Promotion determinism, config consistency | D1+D2, D4 |
| Golden-file tests | 5 known-sensitive document promotion outcomes | D1 |
| Integration tests | End-to-end pipeline, degradation mode, reconciliation | D6 |

## Risks

1. **Config consolidation surfaces threshold drift** (Chain 7 pattern): Routing `os.environ` reads through PipelineConfig may reveal that some code paths were using different parsed values. Golden-file tests catch this early. Mitigation: compare before/after values for all consolidated variables before merging.
2. **save_doc_meta refactor touches 4+ callers**: Incremental per-caller migration with per-step verification at Checkpoint 2.
3. **Promotion threshold isolation changes behavior**: If thresholds were inadvertently read from divergent sources, isolating them may change outcomes. Determinism property test validates. Mitigation: corpus spot-check on known-sensitive documents.
4. **Verdict flips on borderline documents**: These are intentional corrections (eliminating config-path divergence), not regressions, but require careful review during corpus spot-checks.

## Consequences

- **Zone 2** (Verdict Computation) structurally closed: explicit ordering + threshold isolation prevent the Chain 6/19/20 recurrence pattern where promotion reordering and threshold coupling flipped ~40 document verdicts.
- **Zone 4** (Content Measurement) guarded: regression test prevents the `block.get("text")` trap from reopening.
- **Zone 5** (Verdict Persistence) structurally closed: single-writer with CAS eliminates the dual-writer divergence window that caused permanent verdict conflicts (Chain 10/24).
- **Zone 6** (Config Divergence) partially closed: hot-path consolidation covers the verdict-critical paths; startup-only reads remain (acceptable risk, explicitly allowlisted).
- [[RFC-043]] (OCR+Garble+Erasure) can proceed independently — no dependency on RFC-042 deliverables.

## Traceability

| Artifact | Reference |
|----------|-----------|
| Design   | [[design-rfc042-verdict-config-unification]] |
| Tasks    | [[tasks-rfc042-verdict-config-unification]] |
| Prior RFC | [[041-recurring-defect-consolidation\|RFC-041]], [[040-verdict-garble-critical-zone-remediation\|RFC-040]], [[RFC-037]] |
| Audit Zones | Zones 2, 4, 5, 6 (see `audit/zones/_index.md`) |
| Supersedes | N/A |