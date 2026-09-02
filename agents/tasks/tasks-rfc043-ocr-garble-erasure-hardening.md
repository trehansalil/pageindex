---
id: "tasks-rfc043-ocr-garble-erasure-hardening"
title: "Tasks: OCR Recovery, Garble Defense & Erasure Hardening"
type: tasks
status: draft
date: "2026-09-01"
tags:
  - tasks
  - ocr-recovery
  - garble
  - erasure
aliases:
  - "tasks-rfc043-ocr-garble-erasure-hardening"
governs:
  - "[[RFC-043]]"
---

# Implementation Plan: OCR Recovery, Garble Defense & Erasure Hardening

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [[RFC-043]] |
| Design Document | [[design-rfc043-ocr-garble-erasure-hardening]] |

## Overview

Closes three validated structural gaps across the OCR recovery, garble detection, and erasure subsystems. Proceeds by subsystem isolation: Phase 1 fixes OCR recovery (verdict + gates), Phase 2 hardens garble detection (script module), Phase 3 hardens erasure (storage module). Total estimated effort: ~13 hours across 4 phases (revised from ~15h → ~14h → ~13h — D1 is test-only).

## Tasks

- [x] 1. OCR Recovery — Zero-Content Bypass & Flag Decoupling (D1, D2)

  - [x] 1.1 Lock zero-content recovery flow with regression tests (D1) **(Amendment 2026-09-01 v3)**

    - NO production code changes — zero-content recovery already works correctly
    - Write regression test verifying: `_eligible_low_content(state)` returns True when `node_count=0`, `ocr_escalation_low_content=True`, `NODE_COUNT_LOW in defects`
    - Write regression test verifying: `_recover_low_content_ocr` proceeds (does not skip) when `total_chars=0` (char floor skip guard: `0 >= 300` = False → recovery fires)
    - Write architecture guard verifying `_eligible_low_content` does NOT check `total_chars` — char floor must stay in recovery function (recovery.py:453), not eligibility predicate
    - Verified flow: `validate_tree` → `NODE_COUNT_LOW` gate → `_eligible_low_content` True → `_recover_low_content_ocr` proceeds → `evaluate_gates` post-recovery sees recovered signal
    - _Requirements: [R1](043-ocr-garble-erasure-hardening#requirement-1-zero-content-recovery-bypass), [DP-1](design-rfc043-ocr-garble-erasure-hardening#d1-zero-content-recovery-bypass)_

  - [x] 1.2 Decouple OCR escalation eligibility flags (D2)

    - In `_eligible_low_content` (gates.py:294-311), remove the `or config.image_dominant_ocr_escalation_enabled` clause
    - Verify `_eligible_low_content` gates solely on `config.ocr_escalation_low_content` after change
    - Verify `_eligible_garble` (gates.py:277-291) has no escalation flag (confirmed independent — no change needed)
    - Verify `_eligible_image_dominant` (gates.py:314-327) gates solely on `image_dominant_ocr_escalation_enabled` (no change needed)
    - _Requirements: [R2](043-ocr-garble-erasure-hardening#requirement-2-ocr-escalation-flag-decoupling), [DP-2](design-rfc043-ocr-garble-erasure-hardening#d2-ocr-escalation-flag-decoupling)_

  - [x] 1.3 Add architecture guard for flag independence

    - Write test in `test_architecture_guards.py` asserting `_eligible_low_content` source does not reference `image_dominant_ocr_escalation_enabled`
    - Pattern: grep-based guard matching `TestNoDirectGarbleProngsOutsideGarblePy` style
    - _Requirements: [R2.4](043-ocr-garble-erasure-hardening#requirement-2-ocr-escalation-flag-decoupling), [DP-2](design-rfc043-ocr-garble-erasure-hardening#d2-ocr-escalation-flag-decoupling)_

  - [x] 1.4 End-to-end test for zero-content recovery flow **(Amendment 2026-09-01 v3 — merged scope with 1.1)**

    - Create integration test with `node_count == 0` document exercising the full flow
    - Assert recovered document passes `evaluate_gates` post-recovery (no hard-fail)
    - Assert unrecoverable document gets `hard_fail_verdict="FAIL"/"zero_content"` from post-recovery `evaluate_gates` (correct behavior)
    - NOTE: Unit-level assertions (eligibility, char floor skip) moved to task 1.1
    - _Requirements: [R1](043-ocr-garble-erasure-hardening#requirement-1-zero-content-recovery-bypass), [DP-1](design-rfc043-ocr-garble-erasure-hardening#d1-zero-content-recovery-bypass)_

  - [x] 1.C Checkpoint — OCR Recovery

    - Run `uv run pytest tests/ -k "gate or recovery or architecture_guard"` and verify all pass
    - Run full suite: `uv run pytest tests/`
    - Verify no verdict regressions against corpus golden files

- [x] 2. Garble Defense — ScriptContext PF Enforcement (D3)

  - [x] 2.1 Deprecate `ScriptContext.from_script_str` and enforce PF parameter

    - Add `@deprecated("Use ScriptContext.from_document instead")` decorator to `from_script_str` (script.py:956-968)
    - Make `had_presentation_forms` a required keyword parameter (remove hardcoded `False` default)
    - Update any test fixtures that call `from_script_str` to pass `had_presentation_forms` explicitly
    - NOTE: Validation confirmed zero live production callers — only test code uses this factory
    - _Requirements: [R3.1, R3.2](043-ocr-garble-erasure-hardening#requirement-3-scriptcontext-presentation-forms-enforcement), [DP-3](design-rfc043-ocr-garble-erasure-hardening#d3-scriptcontext-pf-enforcement)_

  - [x] 2.2 Update existing architecture guard for PF hardcode **(Amendment 2026-09-01: no new guard needed)**

    - `TestPresentationFormsNotHardcoded` (test_architecture_guards.py:498-582) already guards PF hardcodes via AST parsing
    - Currently exempts `script.py` via `ALLOWED_FILES = {"script.py"}` (line 518)
    - After Task 2.1 fixes the hardcode: remove `"script.py"` from `ALLOWED_FILES` set
    - Verify the guard now catches any `had_presentation_forms=False` literal in ALL src/ files (no exemptions)
    - _Requirements: [R3.3](043-ocr-garble-erasure-hardening#requirement-3-scriptcontext-presentation-forms-enforcement), [DP-3](design-rfc043-ocr-garble-erasure-hardening#d3-scriptcontext-pf-enforcement)_

  - [x] 2.C Checkpoint — Garble Defense

    - Run `uv run pytest tests/ -k "script or presentation_forms or architecture_guard"` and verify all pass
    - Verify `from_script_str` deprecation warning fires in test output

- [x] 3. Erasure Hardening — Ordering Validation & Failure Loudness (D4, D5)

  - [x] 3.1 Verify current manifest passes ordering check (prerequisite)

    - Before adding validation, manually verify the current 11-step `_ERASURE_MANIFEST` (documents.py:551-622) ordering satisfies the data-flow dependencies:
      - Step 1 (uploads) produces `ctx.doc_name` — must precede steps 5, 7
      - Step 2d (verdicts) consumes `ctx.sha256` — must follow sidecar read
      - Step 3 (meta_json) is where `ctx.sha256` can be read — must precede step 2d
    - NOTE: Current ordering may actually violate this — step 2d (verdicts, step=2) comes BEFORE step 3 (meta_json, step=3). If so, the ordering needs correction first.
    - _Requirements: [R4](043-ocr-garble-erasure-hardening#requirement-4-erasure-manifest-ordering-validation), [DP-4](design-rfc043-ocr-garble-erasure-hardening#d4-erasure-manifest-ordering-validation)_

  - [x] 3.2 Extend ErasureStep with dependency tracking fields (D4) **(Amendment 2026-09-01: two-layer model)**

    - Add four fields to `ErasureStep` (documents.py:301-317):
      - `produces: frozenset[str] = frozenset()` — ctx.* fields this step populates
      - `consumes: frozenset[str] = frozenset()` — ctx.* fields this step needs
      - `reads: frozenset[str] = frozenset()` — sidecar objects read by this step
      - `deletes: frozenset[str] = frozenset()` — sidecar objects deleted by this step
    - Annotate existing manifest entries:
      - Step 1 (uploads): `produces={"ctx.doc_name"}`
      - Step 2d (verdicts): `reads={"processed/{id}.meta.json"}` — reads sha256 from sidecar internally
      - Step 3 (meta_json): `deletes={"processed/{id}.meta.json"}`
      - Step 5 (hash_cache): `consumes={"ctx.doc_name"}`
      - Step 7 (preloaded): `consumes={"ctx.doc_name"}`
    - NOTE: `_erase_verdicts` is self-contained — reads sidecar internally, doesn't consume `ctx.sha256` from prior step. Current ordering is correct.
    - **(Amendment 2026-09-01 v2)** Implementation notes for annotations:
      1. Step 1 `produces={"ctx.doc_name"}` is conditional — pre-loop `load_doc` recovery takes priority; annotate as conditional
      2. Step 2d `produces={"ctx.sha256"}` has no downstream consumer — annotate as self-contained or omit
      3. Step 6 (`registry`) Postgres pool dependency (`settings.registry_enabled and settings.postgres_dsn`, then `get_pool() is not None`) is runtime availability, not ordering — correctly excluded from DAG
    - _Requirements: [R4](043-ocr-garble-erasure-hardening#requirement-4-erasure-manifest-ordering-validation), [DP-4](design-rfc043-ocr-garble-erasure-hardening#d4-erasure-manifest-ordering-validation)_

  - [x] 3.3 Extend validate_erasure_manifest with two-layer ordering check (D4) **(Amendment 2026-09-01)**

    - After existing PREFIX completeness check, add two ordering validations:
      1. **Context-field ordering:** for each step with non-empty `consumes`, verify all consumed values are in the `produces` set of an earlier step
      2. **Sidecar ordering:** for each step with non-empty `reads`, verify the read sidecar is not in the `deletes` set of any earlier step
    - Raise `ValueError` with clear message naming the unsatisfied dependency and the step
    - Import-time enforcement via module-level `validate_erasure_manifest()` call
    - _Requirements: [R4.1, R4.2](043-ocr-garble-erasure-hardening#requirement-4-erasure-manifest-ordering-validation), [DP-4](design-rfc043-ocr-garble-erasure-hardening#d4-erasure-manifest-ordering-validation)_

  - [x] 3.4 Upgrade delete_doc skip logging and add partial_purge flag (D5)

    - Change skip logging for `required=False` steps from DEBUG to WARNING level
    - Add structured fields to log: `step_name`, `missing_dep`, `doc_id`
    - Add `partial_purge: bool` to the return dict — True when any step skipped due to missing dependency
    - _Requirements: [R5.1, R5.2](043-ocr-garble-erasure-hardening#requirement-5-erasure-failure-loudness), [DP-5](design-rfc043-ocr-garble-erasure-hardening#d5-erasure-failure-loudness)_

  - [x] 3.5 Add sha256 fallback lookup for verdicts step (D5)

    - Before skipping the verdicts erasure step due to missing `ctx.sha256`, attempt fallback lookup from Postgres registry row
    - Use existing `upsert_doc` connection pool — `SELECT sha256 FROM doc_registry WHERE doc_id = ?`
    - Wrap in try/except — fallback is best-effort; if Postgres unavailable, log WARNING and skip as before
    - _Requirements: [R5.3](043-ocr-garble-erasure-hardening#requirement-5-erasure-failure-loudness), [DP-5](design-rfc043-ocr-garble-erasure-hardening#d5-erasure-failure-loudness)_

  - [x] 3.C Checkpoint — Erasure Hardening

    - Run `uv run pytest tests/ -k "erasure or delete_doc"` and verify all pass
    - Verify `validate_erasure_manifest` passes for current manifest at import time
    - Test partial purge scenario: mock step 1 failure, assert `partial_purge=True` in return

- [x] 4. Integration Tests

  - [x] 4.1 Cross-zone regression test

    - End-to-end test: ingest a zero-content image-only PDF → verify OCR recovery fires → verify verdict is not FAIL/zero_content
    - Test with `image_dominant_ocr_escalation_enabled=False` → verify low-content recovery still works independently
    - _Requirements: [R1, R2](043-ocr-garble-erasure-hardening), [DP-1, DP-2](design-rfc043-ocr-garble-erasure-hardening)_

  - [x] 4.2 Erasure end-to-end test

    - Test full `delete_doc` cascade with all 11 steps
    - Test with simulated step 1 failure → assert `partial_purge=True`, WARNING logs present, steps 5/7 logged as skipped
    - Test sha256 fallback: mock sidecar missing but registry available → assert verdicts step still executes
    - _Requirements: [R4, R5](043-ocr-garble-erasure-hardening), [DP-4, DP-5](design-rfc043-ocr-garble-erasure-hardening)_

  - [x] 4.F Final Checkpoint

    - Full test suite: `uv run pytest tests/`
    - Verify all architecture guards pass
    - Verify `validate_erasure_manifest` import-time check passes

## Notes

- Phase 1 and Phase 2 are independent — can be parallelized
- Phase 3 depends on neither Phase 1 nor 2 — all three phases are independently landable
- D1 is ZERO risk (test-only — zero-content recovery already works correctly, no production code changes)
- Task 3.1 is a prerequisite for 3.2-3.3: if current ordering violates the dependency DAG, fix ordering first
- `TestPresentationFormsNotHardcoded` (test_architecture_guards.py:498-582) may already cover D3's guard — check before writing duplicate
- The `@deprecated` decorator may not exist in the codebase yet — use `warnings.warn(DeprecationWarning)` pattern (existing at queries.py:213)
- **(Amendment 2026-09-01):** D1 redesigned as deferred-hint pattern — `evaluate_gates` returns `recovery_hint`, indexer recovery loop handles actual recovery (RecoveryMixin methods need indexer context). D2 reframed as intentional behavior change with operator migration note. D3 guard already exists (`TestPresentationFormsNotHardcoded` with `ALLOWED_FILES` exemption) — remove exemption, don't duplicate. D4 DAG model rethought: two-layer (ctx-field + sidecar-object) dependencies; current ordering is correct; `_erase_verdicts` is self-contained.
- **(Amendment 2026-09-01 v2):** D1 redesigned AGAIN — v2 review discovered `evaluate_gates` runs AFTER recovery loop (inside `_persist_flat/tree_result`), not before. Deferred-hint pattern dropped; fix redirected to `_eligible_low_content`/`_eligible_image_dominant` recovery predicates. `GateOutcome.recovery_hint` field removed from design. D1 effort reduced from ~3h to ~2h (predicate hardening, not evaluate_gates modification). D4 gains 3 implementation notes: conditional `ctx.doc_name`, self-contained `ctx.sha256`, infrastructure deps out of scope. Total effort revised to ~14h.
- **(Amendment 2026-09-01 v3):** D1 confirmed as non-issue — zero-content recovery already works correctly today. `_eligible_low_content` checks flags+defect only (no char threshold); `_recover_low_content_ocr` char floor skip guard (`0 >= 300 = False`) correctly allows zero-content docs through. D1 reframed as test-coverage lock (regression tests + architecture guard). D1 effort reduced from ~2h to ~1h. D4 annotation 3 corrected: registry guard checks `settings.registry_enabled and settings.postgres_dsn` (was missing `postgres_dsn`). Total effort revised to ~13h.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "2.1", "3.1"], "description": "Core changes (parallel — independent subsystems)" },
    { "id": 1, "tasks": ["1.3", "1.4", "2.2", "3.2"], "description": "Guards + annotations (depend on core changes)" },
    { "id": 2, "tasks": ["1.C", "2.C", "3.3", "3.4", "3.5"], "description": "Checkpoints + erasure validation/logging" },
    { "id": 3, "tasks": ["3.C"], "description": "Erasure checkpoint" },
    { "id": 4, "tasks": ["4.1", "4.2"], "description": "Integration tests (depend on all phases)" },
    { "id": 5, "tasks": ["4.F"], "description": "Final checkpoint" }
  ]
}
```
