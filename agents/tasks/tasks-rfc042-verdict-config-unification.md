---
id: "tasks-rfc042-verdict-config-unification"
title: "Tasks: Verdict & Config Unification"
type: tasks
status: draft
date: "2026-09-01"
tags:
  - tasks
  - verdict
  - config
aliases:
  - "tasks-rfc042-verdict-config-unification"
governs:
  - "[[RFC-042]]"
---

# Implementation Plan: Verdict & Config Unification

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [[042-verdict-config-unification\|RFC-042]] |
| Design Document | [[design-rfc042-verdict-config-unification]] |
| Audit Zones | Zone 2 (Verdict Computation), Zone 4 (Content Measurement), Zone 5 (Verdict Persistence), Zone 6 (Config Divergence) |

## Overview

Unifies the verdict subsystem and configuration layer to close four POST-RFC041 architecture defect zones. Proceeds foundation-first: consolidate config access, add regression guards, then refactor verdict persistence into a single-writer pattern, and finally stabilize verdict computation ordering and threshold isolation. Total estimated effort: ~22 hours across 4 phases.

## Tasks

- [ ] 1. Foundation — Config Access Consolidation & Measurement Guard (D4, D5)

  - [ ] 1.1 Audit `os.environ` reads across src/

    - Grep all 121 `os.environ` references across the 17 source files
    - Classify each reference as **hot-path** (called per-document during indexing) vs **startup-only** (read once at import/init time)
    - Produce a classification table: file, line, variable name, category, PipelineConfig equivalent (if exists)
    - Identify variables read live that already exist in PipelineConfig (dual-sourced)
    - _Requirements: [R4.1](042-verdict-config-unification#requirement-4-config-access-consolidation), [DP-4.1](design-rfc042-verdict-config-unification#d4-config-access-consolidation)_

  - [ ] 1.2 Route hot-path `os.environ` reads through PipelineConfig

    - Replace live `os.environ` reads in hot-path files with PipelineConfig field access, starting with: gates.py, pictures.py, indexer.py, tree_split.py
    - For variables not yet in PipelineConfig, add new fields to the frozen dataclass at config.py:366-578
    - Ensure boolean parsing is consistent: use PipelineConfig's `_bool()` parser for all boolean flags
    - Update `reset_pipeline_config` at config.py:626-669 to include any newly added fields
    - NOTE: Startup-only reads (tracing.py, subprocess_mgr.py, minio_client.py) are left as `os.environ` — they run once before PipelineConfig is frozen
    - _Requirements: [R4.2](042-verdict-config-unification#requirement-4-config-access-consolidation), [DP-4.2](design-rfc042-verdict-config-unification#d4-config-access-consolidation)_

  - [ ] 1.3 Add architecture guard test for config access

    - Write a grep-based test that asserts hot-path source files (gates.py, pictures.py, indexer.py, tree_split.py, garble.py, verdict.py) do not contain direct `os.environ` references
    - Maintain an allowlist for startup-only files where live reads are intentional
    - Test should fail if a new `os.environ` read is added to a hot-path file
    - _Requirements: [R4.3](042-verdict-config-unification#requirement-4-config-access-consolidation), [DP-4.3](design-rfc042-verdict-config-unification#d4-config-access-consolidation)_

  - [ ] 1.4 Add content measurement regression guard (D5)

    - Write a test that constructs a document with table blocks containing `row_records`/`headers`/`rows` (no `text` key)
    - Assert `block_text(block, BlockTextPurpose.CHAR_COUNT)` returns non-zero character count from table content
    - Assert `_flat_block_primary_text` delegates to `block_text` and returns identical count
    - Assert `_flat_search_text` via `doc_text` includes table block content
    - Verify no code path in src/ accesses `block.get("text")` directly for measurement (grep guard)
    - _Requirements: [R5.1](042-verdict-config-unification#requirement-5-content-measurement-regression-guard), [DP-5.1](design-rfc042-verdict-config-unification#d5-content-measurement-regression-guard)_

  - [ ] 1.C Checkpoint — Foundation

    - Run `uv run pytest tests/ -k "config or measurement"` and verify all pass
    - Run `uv run pytest tests/ -k "architecture_guard"` and verify config + measurement guards pass
    - Verify no regressions in existing test suite: `uv run pytest tests/`
    - Ask the user if questions arise before proceeding.

- [ ] 2. Verdict Persistence — Single-Writer Enforcement (D3)

  - [ ] 2.1 Rename `save_doc_meta` → `_save_doc_meta` and enforce single-caller **(Amendment 2026-09-01)**

    - Rename `save_doc_meta` to `_save_doc_meta` in verdict.py — private to `registry_mirror.py`
    - NOTE: `_upsert_registry_row` already calls `save_doc_meta` in 3 places (degradation stamp ×2, backfill ×1) and already stamps `consistency_regime`. The write-through pattern exists; this task closes bypass paths.
    - Add architecture guard test (consistent with existing `test_architecture_guards.py` patterns) asserting `_save_doc_meta` is only imported/called from `registry_mirror.py`
    - _Requirements: [R3.1](042-verdict-config-unification#requirement-3-minio-verdict-write-through), [DP-3.1](design-rfc042-verdict-config-unification#d3-minio-verdict-write-through-cache)_

  - [ ] 2.2 Migrate all `save_doc_meta` bypass callers **(Amendment 2026-09-01: expanded from 4 to 10+ callers)**

    - Migrate all callers outside `_upsert_registry_row` through the registry write-through path:
      - `_persist_flat_result` (indexer.py:1166) — redirect through `_upsert_registry_row`
      - `_persist_tree_result` (indexer.py:1334) — redirect through `_upsert_registry_row`
      - `save_flat_doc` (documents.py:173) — remove direct verdict write; registry flow handles persistence
      - `_drain_verdict_retry_queue` (reconcile.py:82) — already in registry flow, keep as-is
      - `write_verdict` (verdict.py:232) — deprecated wrapper, remove entirely
      - `recompute_verdicts` (preprocess_client.py:369) — redirect through registry flow
      - `run_sweep` (promotion_sweep.py:113,124) — redirect through registry flow
      - `_enrich_one` (backfill.py:161) — redirect through registry flow
      - `_heal_orphans` (backfill.py:323) — redirect through registry flow
    - NOTE: Incremental per-caller migration with per-step test runs recommended
    - _Requirements: [R3.2](042-verdict-config-unification#requirement-3-minio-verdict-write-through), [DP-3.2](design-rfc042-verdict-config-unification#d3-minio-verdict-write-through-cache)_

  - [ ] 2.3 Add CAS guard to MinIO write path

    - In the write-through path inside `_upsert_registry_row`, add a timestamp + priority CAS comparison before writing to MinIO
    - Use the same `>=` semantics as Postgres `_UPSERT_SQL` (queries.py:127) — eliminate the historical `>` vs `>=` divergence
    - During Postgres degradation (`consistency_regime=sidecar-only`), the MinIO CAS guard must still enforce priority ordering from the last known Postgres state
    - _Requirements: [R3.3](042-verdict-config-unification#requirement-3-minio-verdict-write-through), [DP-3.3](design-rfc042-verdict-config-unification#d3-minio-verdict-write-through-cache)_

  - [ ] 2.4 Verify `reconcile_registry_drift` uses write-through path **(Amendment 2026-09-01: direction corrected)**

    - `reconcile_registry_drift` (reconcile.py:113-232) reads MinIO sidecars → upserts to Postgres via `_upsert_all`. This direction is correct and unchanged.
    - `_drain_verdict_retry_queue` (reconcile.py:82) replays queued verdicts into Postgres, then backfills MinIO. This sub-path must use `_save_doc_meta` via `_upsert_registry_row`, not call it directly.
    - Verify `_drain_verdict_retry_queue` is the only reconcile sub-path that writes to MinIO
    - _Requirements: [R3.4](042-verdict-config-unification#requirement-3-minio-verdict-write-through), [DP-3.4](design-rfc042-verdict-config-unification#d3-minio-verdict-write-through-cache)_

  - [ ] 2.5 Architecture guard: `_save_doc_meta` single-writer enforcement **(Amendment 2026-09-01)**

    - Write a test that verifies `_save_doc_meta` is only called from within `registry_mirror.py`
    - Use AST or grep to assert no other module imports or calls `_save_doc_meta` directly (the underscore prefix plus architecture guard provides defense-in-depth)
    - Mirror the existing pattern from `test_verdict_cas_guard_not_importable` (test_architecture_guards.py:415-419)
    - _Requirements: [R3.5](042-verdict-config-unification#requirement-3-minio-verdict-write-through), [DP-3.5](design-rfc042-verdict-config-unification#d3-minio-verdict-write-through-cache)_

  - [ ] 2.C Checkpoint — Verdict Persistence

    - Run existing verdict tests and verify all pass with the new single-writer pattern
    - Run `uv run pytest tests/ -k "verdict or registry or cas"` — all green
    - Verify the new `save_doc_meta` guard test passes
    - No regressions: `uv run pytest tests/`
    - Ask the user if questions arise before proceeding.

- [ ] 3. Verdict Computation — Ordering & Threshold Isolation (D1, D2)

  - [ ] 3.1 Define `PROMOTION_ORDER` constant

    - Create a module-level constant in verdict.py listing the six `_try_*` functions in explicit priority order:
      1. `_try_image_enrichment` (verdict.py:227)
      2. `_try_structural_pass` (verdict.py:272)
      3. `_try_ocr_promotion` (verdict.py:290)
      4. `_try_flat_promotion` (verdict.py:316)
      5. `_try_content_class_promotion` (verdict.py:342)
      6. `_try_small_doc_promotion` (verdict.py:363)
    - Document the ordering contract: changes to order require an RFC amendment
    - _Requirements: [R1.1](042-verdict-config-unification#requirement-1-promotion-evaluation-ordering-contract), [DP-1.1](design-rfc042-verdict-config-unification#d1-promotion-evaluation-ordering-contract)_

  - [ ] 3.2 Refactor `apply_promotions` to iterate `PROMOTION_ORDER`

    - Replace the implicit source-order iteration with explicit `for try_fn in PROMOTION_ORDER:` loop
    - Maintain VG-6 telemetry: all paths still evaluated unconditionally and recorded in `_matches`
    - Winner is still `_matches[0]` (first match in PROMOTION_ORDER) — document this invariant
    - _Requirements: [R1.2](042-verdict-config-unification#requirement-1-promotion-evaluation-ordering-contract), [DP-1.2](design-rfc042-verdict-config-unification#d1-promotion-evaluation-ordering-contract)_

  - [ ] 3.3 Absorb `CATEGORY_BC_PROMOTION_THRESHOLD` into PipelineConfig (D2) **(Amendment 2026-09-01: scope narrowed)**

    - `VerdictThresholds` (types.py:483-531) already isolates all thresholds — all `_try_*` functions receive `th: VerdictThresholds` as a parameter. No new dataclass needed.
    - The sole remaining leak: `CATEGORY_BC_PROMOTION_THRESHOLD` (config.py:17, bare float constant) is imported inside `VerdictThresholds.from_config()` at line 519 instead of being read from PipelineConfig
    - Move `CATEGORY_BC_PROMOTION_THRESHOLD` into PipelineConfig as a new field, update `VerdictThresholds.from_config()` to read it from config instead of importing the module constant
    - Verify no other module-level constants bypass PipelineConfig in the threshold chain
    - _Requirements: [R2.1](042-verdict-config-unification#requirement-2-promotion-threshold-isolation), [DP-2.1](design-rfc042-verdict-config-unification#d2-promotion-threshold-isolation)_

  - [ ]* 3.4 Add promotion determinism property test

    - Property: given the same `DocumentSignature` and the same `PipelineConfig` snapshot, `apply_promotions` always returns the same winner and `_matches` list
    - Use hypothesis or parametric fixtures with known document signatures
    - Cover edge cases: zero-content docs, image-dominant docs, hysteresis-eligible docs
    - **Property 1: Promotion Determinism**
    - **Validates: Requirements [R1](042-verdict-config-unification#requirement-1-promotion-evaluation-ordering-contract), [R2](042-verdict-config-unification#requirement-2-promotion-threshold-isolation)**

  - [ ] 3.5 Add golden-file tests for known promotion outcomes

    - Select 5 known-sensitive documents from corpus history (Chain 6, 19, 20 documents)
    - Record their DocumentSignature + expected promotion path + expected verdict
    - Assert `apply_promotions` produces the expected winner for each
    - NOTE: Golden files must be updated if `PROMOTION_ORDER` is intentionally changed
    - _Requirements: [R1.3](042-verdict-config-unification#requirement-1-promotion-evaluation-ordering-contract), [DP-1.3](design-rfc042-verdict-config-unification#d1-promotion-evaluation-ordering-contract)_

  - [ ] 3.C Checkpoint — Verdict Computation

    - Run `uv run pytest tests/ -k "promotion or verdict"` — all green
    - Corpus spot-check: run 5 known-sensitive documents through the pipeline and compare verdicts to golden-file expectations
    - Verify no verdict flips on stable documents
    - No regressions: `uv run pytest tests/`
    - Ask the user if questions arise before proceeding.

- [ ] 4. Integration — Cross-Cutting Tests (D6)

  - [ ] 4.1 End-to-end verdict pipeline test

    - Test the full flow: document → `evaluate_gates` → `apply_promotions` → `finalize_gate_and_route` → `_upsert_registry_row` → verify Postgres and MinIO hold identical verdict data
    - Cover three paths: normal (Postgres available), degraded (Postgres down, sidecar-only), and recovery (Postgres back, reconciliation)
    - Assert `consistency_regime` stamp is correct in each mode
    - _Requirements: [R6.1](042-verdict-config-unification#requirement-6-verdict-subsystem-integration-tests), [DP-6.1](design-rfc042-verdict-config-unification#d6-verdict-subsystem-integration-tests)_

  - [ ]* 4.2 Config consistency property test

    - Property: within a single document processing run, all configuration reads return the same values regardless of access path (PipelineConfig field vs former `os.environ` site)
    - Instrument a test run to capture all config accesses and assert no divergence
    - **Property 2: Config Consistency**
    - **Validates: Requirements [R4](042-verdict-config-unification#requirement-4-config-access-consolidation)**

  - [ ] 4.3 Degradation-mode test

    - Simulate Postgres unavailable mid-processing
    - Verify `_upsert_registry_row` stamps `consistency_regime=sidecar-only` and queues Redis retry
    - Verify MinIO CAS guard still enforces priority ordering from last known Postgres state
    - Verify a lower-priority re-ingestion during degradation is rejected by MinIO CAS
    - _Requirements: [R3.6](042-verdict-config-unification#requirement-3-minio-verdict-write-through), [R6.2](042-verdict-config-unification#requirement-6-verdict-subsystem-integration-tests), [DP-6.2](design-rfc042-verdict-config-unification#d6-verdict-subsystem-integration-tests)_

  - [ ] 4.F Final Checkpoint

    - Run full test suite: `uv run pytest tests/`
    - Run all architecture guard tests: `uv run pytest tests/ -k "architecture_guard"`
    - Corpus spot-check: 10 documents including all 5 from Checkpoint 3 plus 5 additional stable documents
    - Verify zero flaky test failures across 3 consecutive runs
    - Ask the user if questions arise before proceeding.

## Notes

- Tasks marked with `*` are property-based tests (optional for faster MVP but recommended for verdict subsystem correctness)
- Phase 1 (config + measurement guard) is safe to land independently — no verdict behavior changes
- Phase 2 must complete before Phase 3: single-writer persistence must be stable before changing computation logic, to avoid diagnosing persistence bugs as computation regressions
- The six `_try_*` promotion functions have canonical names (`_try_cat_a/b/c`, `_try_small_doc`) and alias names (`_try_ocr_promotion` etc.) defined at verdict.py:399-402; count confirmed at 6
- `save_doc_meta` currently has no CAS guard at all (MinIO guard was explicitly removed) — this is worse than the originally claimed `>` vs `>=` divergence
- **(Amendment 2026-09-01):** `VerdictThresholds` already isolates thresholds; D2 scope narrowed to absorbing `CATEGORY_BC_PROMOTION_THRESHOLD`. Caller migration for D3 expanded from 4 to 10+ sites. `consistency_regime` stamping already exists. Reconcile direction corrected (MinIO→Postgres, not reverse). `_postgres_authoritative` sentinel replaced by architecture guard + rename to `_save_doc_meta`.
- `os.environ` reads in startup-only files (tracing.py, subprocess_mgr.py, minio_client.py, constants.py, definitions.py) are intentionally left as live reads — they run once before PipelineConfig freezes
- Golden-file tests (Task 3.5) serve as a change-detection mechanism: any future verdict shift shows up as a test failure requiring explicit acknowledgment

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.4"], "description": "Config audit + measurement guard (parallel, no deps)" },
    { "id": 1, "tasks": ["1.2", "1.3"], "description": "Config routing + guard test (depend on 1.1 classification)" },
    { "id": 2, "tasks": ["1.C"], "description": "Foundation checkpoint" },
    { "id": 3, "tasks": ["2.1", "2.2", "2.3", "2.4", "2.5"], "description": "Verdict persistence single-writer (depend on foundation)" },
    { "id": 4, "tasks": ["2.C"], "description": "Persistence checkpoint" },
    { "id": 5, "tasks": ["3.1", "3.2", "3.3", "3.4", "3.5"], "description": "Verdict computation ordering + thresholds (depend on persistence)" },
    { "id": 6, "tasks": ["3.C"], "description": "Computation checkpoint" },
    { "id": 7, "tasks": ["4.1", "4.2", "4.3"], "description": "Integration tests (depend on computation)" },
    { "id": 8, "tasks": ["4.F"], "description": "Final checkpoint" }
  ]
}
```