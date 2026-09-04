---
id: tasks-rfc044-recovery-dispatch-wiring
title: "Tasks: Recovery Dispatch Wiring & OCR Decision Authority"
type: tasks
status: draft
date: 2026-09-02
tags:
  - tasks
  - recovery-dispatch
  - ocr-authority
  - re-entry-guard
  - dead-code-removal
aliases:
  - tasks-rfc044-recovery-dispatch-wiring
governs:
  - "[[RFC-044]]"
---

# Implementation Plan: Recovery Dispatch Wiring & OCR Decision Authority

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [[RFC-044]] |
| Design Document | [[design-rfc044-recovery-dispatch-wiring]] |

## Overview

Hardens the GATES-driven recovery dispatch wiring landed in RFC-043 by closing five validated gaps: inconsistent re-entry guard enforcement across OCR recovery methods (D1), asymmetric RTL eligibility predicate in gates.py AND recovery.py RTL methods (D2, extended Amendment 3), dead `decide_ocr_strategy` call site (D3), unreachable `UNIFIED_OCR_PLAN_ENABLED` image branch (D4), and undocumented `force_full_page` authority inversion (D5). A sixth deliverable (D6) audits the full test suite (~2050 tests) for consolidation to ~1000. Proceeds in 7 waves: Wave 1 adds re-entry guards (D1), Wave 2 fixes RTL eligibility in predicates and recovery methods (D2), Wave 3 removes dead code (D3/D4), Wave 4 documents authority scope (D5), Wave 5 adds integration tests (consolidated), Wave 6 runs cross-zone regression validation, Wave 7 audits and reduces the test suite. Waves 1+2 and Wave 3 are independent and can be parallelized. Total estimated effort: ~17h across 7 waves **(revised 2026-09-04 iteration 4 from ~15h: D6 +2h for coverage baseline/floor; Amendment 4)**.

## Tasks

- [x] 1. Re-entry Guard Consistency (D1)

  - [x] 1.1 Add `full_page_already_applied` guard to `_recover_garble_ocr`

    - In `_recover_garble_ocr` (recovery.py:400-432), insert `if state.full_page_already_applied: return` AFTER the `if state.ok or ext != ".pdf": return` guard (recovery.py:415-416) and BEFORE `if not pipeline_config.ocr_escalation_garble: return`
    - Add inline comment: `# D1: re-entry guard -- prior OCR pass already ran`
    - Match the exact guard pattern in `_recover_image_dominant_ocr` (recovery.py:485-488)
    - _Requirements: [R1.1](044-recovery-dispatch-wiring#requirement-1-re-entry-guard-consistency), [DP-D1](design-rfc044-recovery-dispatch-wiring#d1-re-entry-guard-consistency)_
    - _Dependencies: none (foundation task)_

  - [x] 1.2 Add `full_page_already_applied` guard to `_recover_low_content_ocr`

    - In `_recover_low_content_ocr` (recovery.py:434-468), insert `if state.full_page_already_applied: return` AFTER the `if state.ok or ext != ".pdf": return` guard (recovery.py:449-450) and BEFORE `if not pipeline_config.ocr_escalation_low_content: return`
    - Add inline comment: `# D1: re-entry guard -- prior OCR pass already ran`
    - Match the exact guard pattern in `_recover_image_dominant_ocr` (recovery.py:485-488)
    - _Requirements: [R1.2](044-recovery-dispatch-wiring#requirement-1-re-entry-guard-consistency), [DP-D1](design-rfc044-recovery-dispatch-wiring#d1-re-entry-guard-consistency)_
    - _Dependencies: none (parallel with 1.1)_

  - [x] 1.3 Unit tests for re-entry guard enforcement

    - Write test: mock `_execute_ocr_retry`, set `state.full_page_already_applied=True`, call `_recover_garble_ocr`, assert `_execute_ocr_retry` was NOT called
    - Write test: mock `_execute_ocr_retry`, set `state.full_page_already_applied=True`, call `_recover_low_content_ocr`, assert `_execute_ocr_retry` was NOT called
    - Write regression test: set `state.full_page_already_applied=False` for both methods, assert `_execute_ocr_retry` IS called (no regression from D1 guard)
    - **(Amendment 2026-09-04, absorbed from dropped Task 5.2):** Write guard-cascading test: first recovery sets `full_page_already_applied=True`, second recovery respects it (triple-OCR prevention). This is unit-level, not integration — mock `_execute_ocr_retry` to return `True` (applied), verify the flag propagates and subsequent recovery returns early.
    - _Requirements: [R1.3](044-recovery-dispatch-wiring#requirement-1-re-entry-guard-consistency), [DP-D1](design-rfc044-recovery-dispatch-wiring#d1-re-entry-guard-consistency)_
    - _Dependencies: 1.1, 1.2_

  - [x] 1.4 Architecture guard for re-entry guard exhaustiveness

    - Write test in `test_architecture_guards.py` asserting ALL three `_recover_*_ocr` methods (`_recover_garble_ocr`, `_recover_low_content_ocr`, `_recover_image_dominant_ocr`) contain a `full_page_already_applied` guard
    - Pattern: AST or source-text inspection of method bodies, matching `TestNoDirectGarbleProngsOutsideGarblePy` style
    - Verify: `grep -n 'full_page_already_applied' recovery.py` returns hits in all three method bodies
    - _Requirements: [R1.4](044-recovery-dispatch-wiring#requirement-1-re-entry-guard-consistency), [Property 1](design-rfc044-recovery-dispatch-wiring#property-1-re-entry-guard-exhaustiveness)_
    - _Dependencies: 1.1, 1.2_

  - [x] 1.C Checkpoint -- Re-entry Guard

    - Run `uv run pytest tests/ -k "recovery or architecture_guard"` and verify all pass
    - Run full suite: `uv run pytest tests/`
    - Verify no verdict regressions against corpus golden files

- [x] 2. RTL Eligibility Predicate Consistency (D2)

  - [x] 2.1 Fix `_eligible_rtl` to use `_all_defects(state)`

    - In `_eligible_rtl` (gates.py:325-327), replace `state.first_defect == TreeDefect.RTL_REVERSAL` with `TreeDefect.RTL_REVERSAL in _all_defects(state)`
    - Update docstring to include Zone-1 fix annotation: "Zone-1 fix: checks *all* active defects (not just first_defect) so RTL_REVERSAL firing as a secondary defect behind a higher-severity primary still triggers RTL-specific recovery."
    - Match docstring style of `_eligible_garble`, `_eligible_low_content`, `_eligible_image_dominant` (all have Zone-1 annotations from RFC-043)
    - _Requirements: [R2.1](044-recovery-dispatch-wiring#requirement-2-rtl-eligibility-predicate-consistency), [DP-D2](design-rfc044-recovery-dispatch-wiring#d2-rtl-eligibility-predicate-consistency)_
    - _Dependencies: Wave 1 complete (shared test infrastructure in gates/recovery subsystem)_

  - [x] 2.1b Patch RTL recovery methods to use `_all_defects(state)` **(Amendment 3, 2026-09-04)**

    - In `_recover_rtl_repair` (recovery.py:529), replace `state.first_defect == TreeDefect.RTL_REVERSAL` with `TreeDefect.RTL_REVERSAL in _all_defects(state)`
    - In `_recover_rtl_flat_compare` (recovery.py:586-592), replace `state.first_defect == TreeDefect.RTL_REVERSAL` with `TreeDefect.RTL_REVERSAL in _all_defects(state)`
    - Import `_all_defects` from `..helpers.gates` at the top of recovery.py (or use `state.gate_result.all_defects` directly via the helper pattern). **(Amendment 4):** Verify no circular import — `gates.py` has no `client/` dependency, so `recovery.py` importing from `..helpers.gates` is safe.
    - Note: `_recover_vlm_fallback`:659 also uses `first_defect` for Tesseract raster gating — deliberately out of D2 scope
    - _Requirements: [R2.5](044-recovery-dispatch-wiring#requirement-2-rtl-eligibility-predicate-consistency), [DP-D2 Change location 2](design-rfc044-recovery-dispatch-wiring#d2-rtl-eligibility-predicate-consistency)_
    - _Dependencies: 2.1_

  - [x] 2.2 Unit tests for RTL eligibility with co-firing defects

    - Write test: construct ExtractionState with `gate_result.all_defects={NODE_COUNT_LOW, RTL_REVERSAL}` and `first_defect=NODE_COUNT_LOW`, assert `_eligible_rtl(state)` returns True
    - Write test: construct ExtractionState with `gate_result.all_defects={GARBLING, RTL_REVERSAL}` and garble-promoted primary via D4 override, assert `_eligible_rtl(state)` returns True
    - Write regression test: RTL_REVERSAL as sole defect (`all_defects={RTL_REVERSAL}`), assert `_eligible_rtl(state)` returns True (no regression from D2)
    - Write test: RTL_REVERSAL not present (`all_defects={NODE_COUNT_LOW}`), assert `_eligible_rtl(state)` returns False
    - **(Amendment 2026-09-04, absorbed from dropped Task 5.3):** Write test: RTL_REVERSAL as secondary defect behind NODE_COUNT_LOW, verify `_eligible_rtl` returns True AND that the GATES loop dispatches an RTL recovery method. The test MUST assert the recovery method *performs work* (e.g., `reconstruct_bidi_order` or `_reconvert_and_revalidate` is invoked), not merely that it is dispatched — otherwise the test passes against the pre-Amendment-3 no-op. **(Amendment 3)** This is a predicate-level unit test, not a full integration test — existing `TestRecoveryDispatchCrossTupleDedup` already covers loop-level dedup.
    - _Requirements: [R2.2, R2.3](044-recovery-dispatch-wiring#requirement-2-rtl-eligibility-predicate-consistency), [DP-D2 edge cases](design-rfc044-recovery-dispatch-wiring#d2-rtl-eligibility-predicate-consistency)_
    - _Dependencies: 2.1_

  - [x] 2.3 Architecture guard for predicate symmetry

    - Write test in `test_architecture_guards.py` asserting NO `_eligible_*` predicate in gates.py references `state.first_defect` directly
    - Pattern: source-text or AST inspection of all four `_eligible_*` function bodies (`_eligible_garble`, `_eligible_low_content`, `_eligible_image_dominant`, `_eligible_rtl`)
    - Verify all four use `_all_defects(state)` for defect membership checks
    - Verify: `grep -n 'first_defect' gates.py` returns zero hits inside any `_eligible_*` function body
    - Additionally verify no `_recover_rtl_*` method body in recovery.py contains `state.first_defect == TreeDefect.RTL_REVERSAL` (Property 2 extension, Amendment 3). Note: `_recover_vlm_fallback`:659 uses `first_defect` for a different purpose (Tesseract raster gating on GARBLING/NODE_GARBLING) and is excluded from this guard.
    - _Requirements: [R2.4, R2.6](044-recovery-dispatch-wiring#requirement-2-rtl-eligibility-predicate-consistency), [Property 2](design-rfc044-recovery-dispatch-wiring#property-2-eligibility-predicate-symmetry)_
    - _Dependencies: 2.1, 2.1b_

  - [x] 2.C Checkpoint -- RTL Eligibility

    - Run `uv run pytest tests/ -k "gate or eligible or rtl or architecture_guard"` and verify all pass
    - Run full suite: `uv run pytest tests/`

- [x] 3. Dead Code Removal (D3, D4)

  - [x] 3.1 Remove dead `decide_ocr_strategy` call in `_convert_to_tree` (D3)

    - Delete lines indexer.py:778-794 -- the `decide_ocr_strategy(...)` call, its parameter construction, and the `logger.debug(...)` block (the "Zone-2: post-conversion OcrDecision" comment and all associated code)
    - Check whether `decide_ocr_strategy`, `OcrDecision`, and `OcrMode` are still imported elsewhere in `indexer.py`; if any import's only consumer was the dead call, remove from the import block **(Amendment 2026-09-04: expanded to cover OcrDecision/OcrMode per GAP-5)**. Note (Amendment 3): verified at HEAD 7bf4947 that `OcrDecision`/`OcrMode` are NOT actually imported in indexer.py — they appear only in comments (lines 535, 778). The check will resolve to removing only the `decide_ocr_strategy` import (indexer.py:91).
    - Verify the live call site in `converters/pictures.py::_recover_picture_results` (approximately pictures.py:1065-1070) remains unchanged
    - _Requirements: [R3.1, R3.2](044-recovery-dispatch-wiring#requirement-3-dead-ocr-decision-call-removal), [DP-D3](design-rfc044-recovery-dispatch-wiring#d3-dead-ocr-decision-call-removal)_
    - _Dependencies: none (independent of Waves 1/2)_

  - [x] 3.2 Remove `UNIFIED_OCR_PLAN_ENABLED` flag and unreachable branch (D4)

    - Delete the `UNIFIED_OCR_PLAN_ENABLED` flag definition (picture_plane.py:349-352) and its Zone-8 comment
    - Delete the `if UNIFIED_OCR_PLAN_ENABLED and document_type == "image":` branch inside `decide_ocr_strategy` (approximately picture_plane.py:398-409) **(Amendment 4: line range corrected from 388-415)**
    - Retain `document_type` parameter on `decide_ocr_strategy` (Zone-8 typed contract)
    - Retain `DocumentType = Literal["pdf", "image", "html", "text", "xlsx"]` (picture_plane.py:354, Zone-8 typed contract)
    - Verify whether `os` import (picture_plane.py:15) is still needed after removing `os.getenv` for the flag; remove if unused — `os.getenv` for `UNIFIED_OCR_PLAN_ENABLED` is the ONLY `os.` call in the file **(Amendment 2026-09-04, GAP-6: design doc D4 now lists this as explicit change location)**
    - **(Amendment 2026-09-03):** Delete the 3 tests in `test_gates.py::TestDecideOcrStrategyDocumentType` that exercise the removed flag and image branch: `test_image_document_type_returns_full_page_with_splice_when_unified_enabled` (line 764), `test_image_document_type_ignored_when_unified_disabled` (line 776), `test_image_type_carries_custom_ocr_langs` (line 831). These test unreachable dead code that no longer exists post-D4.
    - **(Amendment 4, 2026-09-04):** Strip the `UNIFIED_OCR_PLAN_ENABLED` monkeypatch from the fourth test `test_pdf_document_type_preserves_existing_truth_table` (~line 798) but retain the parametrized truth table (it validates R4.4 PDF-path parity). The test must pass without referencing the deleted flag.
    - **(Amendment 4, 2026-09-04):** Remove all docstring and inline comment references to `UNIFIED_OCR_PLAN_ENABLED` in `picture_plane.py`: docstring ~line 378 and inline comment ~lines 385-387. Run `grep -rn UNIFIED_OCR_PLAN_ENABLED src/ tests/` and expect zero hits before proceeding to Task 3.4.
    - _Requirements: [R4.1, R4.2, R4.5, R4.6, R4.7](044-recovery-dispatch-wiring#requirement-4-unreachable-unified_ocr_plan_enabled-image-branch-removal), [DP-D4](design-rfc044-recovery-dispatch-wiring#d4-unreachable-image-branch-removal)_
    - _Dependencies: none (parallel with 3.1, independent of Waves 1/2)_

  - [x] 3.3 Architecture guard for single live `decide_ocr_strategy` call site (D3)

    - Write test in `test_architecture_guards.py` asserting exactly ONE call site for `decide_ocr_strategy(` in `src/` (excluding test files and the function definition itself)
    - Pattern: `grep -rn 'decide_ocr_strategy(' src/ --include='*.py' | grep -v 'def decide_ocr_strategy' | grep -v test`
    - Assert exactly one match (the live call in `converters/pictures.py`)
    - _Requirements: [R3.3](044-recovery-dispatch-wiring#requirement-3-dead-ocr-decision-call-removal), [Property 3](design-rfc044-recovery-dispatch-wiring#property-3-single-live-call-site)_
    - _Dependencies: 3.1_

  - [x] 3.4 Architecture guard for no unreachable feature flags (D4)

    - Write test in `test_architecture_guards.py` asserting `UNIFIED_OCR_PLAN_ENABLED` does not appear anywhere in `src/` (excluding tests)
    - Pattern: `grep -rn 'UNIFIED_OCR_PLAN_ENABLED' src/` returns zero hits
    - _Requirements: [R4](044-recovery-dispatch-wiring#requirement-4-unreachable-unified_ocr_plan_enabled-image-branch-removal), [Property 4](design-rfc044-recovery-dispatch-wiring#property-4-no-unreachable-feature-flags)_
    - _Dependencies: 3.2_

  - [x] 3.5 Confirm `decide_ocr_strategy` PDF-path parity (D4) **(Amendment 4: rewritten as confirmation step)**

    - Confirm `test_pdf_document_type_preserves_existing_truth_table` passes after Task 3.2's monkeypatch strip. This test already covers the five parameter combinations (force_full_page, garble_status, has_image_markers+ocr_escalation, full_page_already_applied, defaults).
    - If any combination is missing from the retained truth table, add it as a new parametrize entry — but do NOT write a separate test class.
    - _Requirements: [R4.4, R4.6](044-recovery-dispatch-wiring#requirement-4-unreachable-unified_ocr_plan_enabled-image-branch-removal), [DP-D4](design-rfc044-recovery-dispatch-wiring#d4-unreachable-image-branch-removal)_
    - _Dependencies: 3.2_

  - [x] 3.C Checkpoint -- Dead Code Removal

    - Run `uv run pytest tests/ -k "ocr_strategy or decide_ocr or architecture_guard or picture_plane"` and verify all pass
    - Run full suite: `uv run pytest tests/`
    - Verify `grep -rn 'UNIFIED_OCR_PLAN_ENABLED' src/` returns zero hits
    - Verify `grep -rn 'UNIFIED_OCR_PLAN_ENABLED' tests/` returns zero hits **(Amendment 5)**
    - Verify `grep -rn 'decide_ocr_strategy(' src/ --include='*.py' | grep -v def | grep -v test` returns exactly one hit

- [x] 4. Authority Documentation (D5)

  - [x] 4.1 Add authority inversion comment to `force_full_page` assignment

    - At indexer.py:530-537, amend the existing comment block above the `force_full_page` assignment
    - Add lines documenting the authority inversion: "AUTHORITY INVERSION (RFC-044 D5): this decision causes Docling to run native full-page OCR BEFORE decide_ocr_strategy is consulted. decide_ocr_strategy learns what already happened (via full_page_already_applied) rather than deciding what should happen."
    - Add cross-reference: "See design-rfc044-recovery-dispatch-wiring.md D5 for phased plan."
    - Exact before/after text specified in [DP-D5](design-rfc044-recovery-dispatch-wiring#d5-force-full-page-authority-documentation)
    - _Requirements: [R5.1](044-recovery-dispatch-wiring#requirement-5-force-full-page-authority-documentation), [DP-D5](design-rfc044-recovery-dispatch-wiring#d5-force-full-page-authority-documentation)_
    - _Dependencies: 3.1 complete (D5 must reflect post-D3 state -- dead call site already removed)_

  - [x] 4.2 Amend `decide_ocr_strategy` docstring with authority scope

    - In `decide_ocr_strategy` (picture_plane.py), append authority-scope paragraph to the existing docstring
    - Text: "Authority scope (RFC-044 D5): this function is authoritative for the FIRST conversion pass only (post-conversion diagnostic in the converter chain via _recover_picture_results). Recovery-pass OCR decisions are made independently by recovery methods in client/recovery.py, which check their own flag gates and the full_page_already_applied re-entry guard but do NOT call this function. Pre-conversion full-page OCR is driven by force_full_page (indexer.py:536), which bypasses this function entirely. See design-rfc044 D5 for the phased consolidation plan."
    - _Requirements: [R5.2](044-recovery-dispatch-wiring#requirement-5-force-full-page-authority-documentation), [DP-D5](design-rfc044-recovery-dispatch-wiring#d5-force-full-page-authority-documentation)_
    - _Dependencies: 3.2 complete (docstring must reflect post-D4 state -- no image branch to mention)_

  - [x] 4.C Checkpoint -- Authority Documentation

    - Verify inline comment at indexer.py `force_full_page` contains "AUTHORITY INVERSION (RFC-044 D5)"
    - Verify `decide_ocr_strategy` docstring contains "Authority scope (RFC-044 D5)"
    - Run full suite: `uv run pytest tests/` (no behavioral changes -- documentation only)

- [x] 5. Integration Tests **(Amendment 2026-09-04: consolidated — dropped 5.1/5.2/5.3, slimmed 5.4)**

  > **Consolidation rationale:** Existing `TestIntegrationRecoveryLoopMultiDefect` and `TestRecoveryDispatchCrossTupleDedup` in test_recovery.py already cover multi-defect dispatch (5.1) and partial guard cascading (5.2). RTL secondary-defect testing (5.3) is better as a unit test absorbed into Task 2.2. Only the VLM escape-hatch assertion (5.4) is genuinely new. Net: 3 tasks dropped, 1 slimmed.
  >
  > **Architecture guard consolidation note:** Tasks 1.4, 2.3, 3.3, and 3.4 each add one architecture guard. Implementors SHOULD place all four in a single new class `TestRFC044RecoveryDispatchGuards` in `test_architecture_guards.py` with 4 methods, rather than 4 separate classes.

  - [x] 5.4 VLM escape-hatch test (slimmed) **(Amendment 2026-09-03, consolidated 2026-09-04)**

    - Add ONE test method to `TestRecoveryDispatchCrossTupleDedup` in test_recovery.py (not a standalone test class)
    - Test scenario: `state.full_page_already_applied=True`, document fails validate_tree with GARBLING
    - Assert `_recover_garble_ocr` returns immediately (D1 guard fires — OCR blocked)
    - Assert `_recover_vlm_fallback` IS called and runs (VLM is a distinct strategy, not blocked by `full_page_already_applied`)
    - _Requirements: [R1](044-recovery-dispatch-wiring#requirement-1-re-entry-guard-consistency), [Property 1](design-rfc044-recovery-dispatch-wiring#property-1-re-entry-guard-exhaustiveness)_
    - _Dependencies: Waves 1-4 complete_

  - [x] 5.C Checkpoint -- Integration Tests

    - Run `uv run pytest tests/ -k "dispatch or vlm_escape"` and verify all pass
    - Run full suite: `uv run pytest tests/`

- [ ] 6. Cross-Zone Regression Validation

  - [ ] 6.1 Corpus spot-check on garble-defect documents

    - Identify documents in corpus that previously triggered garble recovery (check test fixtures or doc_store/ for garble-classified PDFs)
    - Run ingestion with D1 guard active, verify no recovery regression (documents that recovered before D1 still recover -- they should have `full_page_already_applied=False` on first attempt)
    - Verify documents with `force_full_page=True` (pre-conversion probe) no longer receive a redundant second OCR pass via `_recover_garble_ocr` or `_recover_low_content_ocr`
    - _Requirements: [R1](044-recovery-dispatch-wiring), [Launch Constraint D1](design-rfc044-recovery-dispatch-wiring#launch-constraints)_
    - _Dependencies: all prior waves_

  - [ ] 6.2 Verify all five correctness properties hold

    - Run all architecture guards: `uv run pytest tests/ -k "architecture_guard"`
    - Verify Property 1: re-entry guard exhaustiveness -- all three `_recover_*_ocr` methods guarded (Task 1.4)
    - Verify Property 2: eligibility predicate symmetry -- no `_eligible_*` uses `state.first_defect` (Task 2.3)
    - Verify Property 3: single live call site -- `decide_ocr_strategy` has exactly one call in src/ (Task 3.3)
    - Verify Property 4: no unreachable feature flags -- `UNIFIED_OCR_PLAN_ENABLED` absent from src/ (Task 3.4)
    - Verify Property 5: recovery loop all-defects contract -- multi-defect dispatch fires both eligible recoveries (`TestIntegrationRecoveryLoopMultiDefect` in tests/test_recovery.py, which already covers multi-defect dispatch — Task 5.1 dropped in Amendment 2, repointed Amendment 3)
    - _Requirements: [Properties 1-5](design-rfc044-recovery-dispatch-wiring#correctness-properties)_
    - _Dependencies: all prior waves_

  - [ ] 6.3 Zone status update recommendation

    - After all tests pass, recommend updating zone frontmatter:
      - Zone 1 (`ocr-pipeline-decision-recovery-cascade`): update `status` to `partially-addressed` with note citing D1 (guard consistency), D2 (RTL eligibility), D3 (dead call site removed), D4 (dead branch removed)
      - Zone 6 (`gate-to-recovery-dispatch-wiring-gap`): update `status` to `resolved` with note citing RFC-043 dispatch loop (fdd023e) + RFC-044 hardening
    - NOTE: Do not update zone frontmatter automatically -- this is a recommendation for the operator to action after review
    - _Requirements: [RFC-044 Consequences](044-recovery-dispatch-wiring#consequences), [Risk: zone reports re-used without re-verification](044-recovery-dispatch-wiring#risks)_
    - _Dependencies: 6.1, 6.2_

  - [ ] 6.F Final Checkpoint

    - Full test suite: `uv run pytest tests/`
    - Verify all architecture guards pass
    - Verify all five correctness properties hold
    - Verify no corpus verdict regressions

- [ ] 7. Test Suite Reduction Audit **(Amendment 2026-09-04: Wave 7 — reduce ~2050 tests to ~1000)**

  > **Context:** The full test suite currently has ~2050 passing tests across prior RFCs. Many tests overlap, exercise superseded behavior, or duplicate coverage provided by architecture guards. This wave audits the entire suite for consolidation opportunities.

  - [ ] 7.1 Inventory and categorize all test files

    - List every test file in `tests/` with test count per file (via `uv run pytest --collect-only -q`)
    - Categorize by type: unit, integration, architecture guard, regression, fixture-heavy
    - Identify the top-10 files by test count — these are the consolidation targets
    - **(Amendment 4):** Capture coverage baseline: `uv run pytest --cov=pageindex_mcp --cov-report=term-missing` — record the overall coverage percentage as the floor reference for Task 7.4
    - _Dependencies: Waves 1-6 complete (all RFC-044 code changes landed and verified)_

  - [ ] 7.2 Identify redundant and superseded tests

    - For each test file with >50 tests: identify tests that exercise code removed or superseded by RFC-041 through RFC-044
    - Flag tests whose assertions are subsumed by architecture guards (e.g., tests checking a specific guard that an AST-level arch guard now covers structurally)
    - Flag tests that duplicate assertions across files (same mock setup, same assertions, different file)
    - Flag fixture-heavy integration tests that can be collapsed (multiple test methods that share 90%+ of setup, differing only in one parameter)
    - Produce a removal/merge manifest: test name, file, line, action (delete/merge/keep), rationale
    - _Dependencies: 7.1_

  - [ ] 7.3 Execute test consolidation

    - Delete tests flagged for removal in the manifest
    - Merge tests flagged for consolidation (parametrize where multiple tests differ by one input)
    - Verify no coverage regression: run `uv run pytest` after each batch of deletions
    - Target: reduce from ~2050 to ~1000 tests (net deletion of ~1050)
    - _Dependencies: 7.2_

  - [ ] 7.4 Validate post-reduction suite

    - Full test suite: `uv run pytest tests/`
    - Verify all architecture guards still pass
    - Verify all five correctness properties hold
    - Verify no corpus verdict regressions
    - **(Amendment 4):** Verify coverage ≥ baseline (from Task 7.1) − 1 percentage point: `uv run pytest --cov=pageindex_mcp --cov-report=term-missing`
    - Report final test count
    - _Dependencies: 7.3_

  - [ ] 7.F Final Checkpoint — Test Reduction

    - Confirm test count is ≤1100 (target ~1000, allow ±10% margin)
    - Document which test categories were reduced and by how much
    - Update any test-count references in RFC/design docs

## Notes

- Waves 1+2 (guard and eligibility fixes) and Wave 3 (dead code removal) are independent and can be parallelized -- they touch different files (recovery.py/gates.py vs indexer.py/picture_plane.py)
- Wave 4 (documentation) depends on Wave 3 being finalized -- documented authority scope must reflect the post-cleanup state (dead call site removed, unreachable branch removed)
- D1 and D2 are technically independent of each other but share test infrastructure in the gates/recovery subsystem; sequencing D2 after D1 avoids merge conflicts in test files
- D1 changes behavior: documents that previously received two full-page OCR passes will now receive one. Requires corpus spot-check (Task 6.1) per launch constraint
- D2 changes eligibility AND recovery method gating: documents with RTL_REVERSAL as a secondary defect will now enter RTL recovery AND the recovery methods will perform work (Amendment 3 extended D2 to patch `_recover_rtl_repair`:529 and `_recover_rtl_flat_compare`:588). RTL recovery has its own internal guards (`state.ok`, bidi signals, `decide_rtl` output), so blast radius is limited to eligibility and the RTL-specific recovery path
- D3 removes a DEBUG-level log line at indexer.py:787-794 -- operators monitoring post-conversion OcrDecision at DEBUG level lose this output; the live call site in `_recover_picture_results` already logs its own OcrDecision
- D4 removes the `UNIFIED_OCR_PLAN_ENABLED` env var -- operators with it set experience no behavior change (was always dead code, branch was unreachable)
- D5 is documentation-only -- no automated tests needed, validated by design-doc review and inline-comment verification
- VLM fallback (`_recover_vlm_fallback`) is deliberately NOT affected by D1's re-entry guard -- VLM is a distinct strategy (not a Docling/Tesseract re-run) and should fire even after a full-page OCR pass failed
- D1+D2 interaction is orthogonal: D1 limits OCR-based recovery (re-entry guard), D2 expands RTL-based recovery (eligibility). RTL methods do not call `_execute_ocr_retry`, so D1's guard does not affect them. OCR methods are not gated by `_eligible_rtl`, so D2 does not affect them

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 1, "tasks": ["1.1", "1.2", "1.3", "1.4"], "description": "Re-entry guard consistency (D1) -- foundation", "checkpoint": "1.C" },
    { "id": 2, "tasks": ["2.1", "2.1b", "2.2", "2.3"], "description": "RTL eligibility predicate + recovery method fix (D2) -- depends on Wave 1", "checkpoint": "2.C" },
    { "id": 3, "tasks": ["3.1", "3.2", "3.3", "3.4", "3.5"], "description": "Dead code removal (D3/D4) -- independent of Waves 1/2", "checkpoint": "3.C" },
    { "id": 4, "tasks": ["4.1", "4.2"], "description": "Authority documentation (D5) -- depends on Wave 3", "checkpoint": "4.C" },
    { "id": 5, "tasks": ["5.4"], "description": "Integration tests (consolidated 2026-09-04: 5.1-5.3 dropped/absorbed) -- depends on Waves 1-4", "checkpoint": "5.C" },
    { "id": 6, "tasks": ["6.1", "6.2", "6.3"], "description": "Cross-zone regression validation -- final gate", "checkpoint": "6.F" },
    { "id": 7, "tasks": ["7.1", "7.2", "7.3", "7.4"], "description": "Test suite reduction audit (~2050→~1000) -- depends on Waves 1-6", "checkpoint": "7.F" }
  ],
  "parallelizable": [
    ["wave-1", "wave-3"],
    ["wave-2", "wave-3"]
  ]
}
```

## Amendment History

### Amendment 1 (2026-09-03): Review iteration 1 — test cleanup + VLM escape-hatch

**Trigger:** Multi-agent review (root-cause validator, fix/implementation reviewer, lead analyst) independently flagged:
1. **GAP-1 (blocker):** D4 breaks 3 existing tests in `test_gates.py::TestDecideOcrStrategyDocumentType` (lines 764, 776, 831) that exercise the `UNIFIED_OCR_PLAN_ENABLED` image branch being removed. Task 3.2 was silent on cleanup.
2. **GAP-2 (warning):** D3 import cleanup (`decide_ocr_strategy` import in `indexer.py`) not specified as a change location in the design doc.
3. **GAP-3 (warning):** No integration test for the VLM escape-hatch path (D1 guard blocks OCR, VLM fallback still runs).
4. **GAP-4 (note):** Effort estimate optimistic at 6h.

**Changes applied:**
- R4: Added acceptance criterion 5 — delete 3 dead-code tests
- D4 decision summary: Added test cleanup scope, revised effort from ~0.5h to ~1h
- Task 3.2: Added subtask for deleting the 3 `TestDecideOcrStrategyDocumentType` tests
- Task 5.4: New — VLM escape-hatch integration test (OCR guard fires → VLM still runs)
- Wave 5 dependency graph: Added task 5.4
- Design doc D3: Added import cleanup as explicit change location
- Design doc D4: Added test cleanup with specific test names and line numbers
- Design doc risk mitigation: Added VLM escape-hatch row
- Effort estimate: Revised from ~6h to ~8h across all three artifacts

### Amendment 2 (2026-09-04): Review iteration 2 — test consolidation + Wave 7 test reduction

**Trigger:** Focused re-review by 4 agents (amendment-quality, D1+D2 edge cases, D3+D4 cleanup, Wave 5 test design) identified:
1. **Stale template line:** `_No amendments yet._` lingered after Amendment 1 history block. Removed.
2. **GAP-5 (warning):** Task 3.1 should check `OcrDecision`/`OcrMode` imports in indexer.py for cleanup, not just `decide_ocr_strategy`. Expanded Task 3.1 and design doc D3.
3. **GAP-6 (warning):** D4 design doc should list `import os` removal in picture_plane.py as explicit change location. Added to design doc D4 and Task 3.2.
4. **Wave 5 consolidation:** Existing tests cover 5.1 (multi-defect dispatch) and 5.2 (guard cascading). 5.3 (RTL secondary) better as unit test in Task 2.2. Only 5.4 (VLM escape-hatch) is genuinely new — slimmed to one method.
5. **Architecture guard consolidation:** Tasks 1.4+2.3+3.3+3.4 should be one class `TestRFC044RecoveryDispatchGuards`.
6. **Wave 7 (test suite reduction):** User directive to reduce ~2050 tests to ~1000. Added as new wave with inventory, audit, execution, and validation tasks.

**Changes applied:**
- Removed stale `_No amendments yet._` line
- Task 3.1: Expanded import check to cover `OcrDecision`/`OcrMode` (GAP-5)
- Task 3.2: Added explicit `import os` removal note with line reference (GAP-6)
- Task 1.3: Absorbed guard-cascading test from dropped Task 5.2
- Task 2.2: Absorbed RTL secondary-defect dispatch test from dropped Task 5.3
- Wave 5: Dropped Tasks 5.1, 5.2, 5.3; slimmed Task 5.4 to one method in `TestRecoveryDispatchCrossTupleDedup`
- Wave 5: Added architecture guard consolidation implementation note
- Wave 7: New — test suite reduction audit (Tasks 7.1–7.4, checkpoint 7.F)
- Dependency graph: Updated Wave 5 tasks, added Wave 7
- Overview: Revised 6→7 waves, ~8h→~14h effort
- Design doc D3: Added `OcrDecision`/`OcrMode` import check (GAP-5)
- Design doc D4: Added `import os` removal edge case (GAP-6)

### Amendment 3 (2026-09-04): Review iteration 3 — D2 no-op fix + D6 currency

**Trigger:** 6-agent review (artifact scanner, arch scout, prior-work historian, root-cause validator Opus, implementation reviewer, lead analyst Fable) found:
1. **GAP-7 (major):** D2 eligibility predicate fix is a no-op for the secondary-defect case. Both RTL recovery methods (`_recover_rtl_repair` at recovery.py:529 and `_recover_rtl_flat_compare` at recovery.py:588) gate on `state.first_defect == TreeDefect.RTL_REVERSAL` internally — the GATES loop dispatches them after the eligibility fix, but they return immediately. RFC Consequence #2 is false as written.
2. **GAP-8 (major):** Design doc Overview, mermaid diagram, Correctness Properties, and Risk Mitigation table never updated for D6 from Amendment 2.
3. **GAP-9 (minor):** Task 6.2 references dropped Task 5.1 for Property 5 verification.
4. **GAP-10 (minor):** D4 image branch line range in design doc off by 14 lines (388-415 vs actual 398-409).
5. **GAP-11 (minor):** RFC/design docs lack consolidated Amendment History section.
6. **GAP-12 (minor):** GAP-5 OcrDecision/OcrMode check will resolve to no-op (not imported in indexer.py).
7. **GAP-13 (minor):** Task 3.5 may duplicate existing PDF-path tests in test_gates.py.

**Changes applied:**
- R2.5-R2.6 added: RTL recovery method guards + architecture guard extension
- Task 2.1b added: patch `_recover_rtl_repair`:529 and `_recover_rtl_flat_compare`:588
- Task 2.2 strengthened: test must assert work performed, not just dispatch
- Task 2.3 extended: arch guard scans recovery.py RTL methods + gates.py predicates
- Task 6.2: repointed Property 5 from dropped Task 5.1 to `TestIntegrationRecoveryLoopMultiDefect`
- Task 3.1: clarifying note that OcrDecision/OcrMode not imported (GAP-5 resolves to no-op)
- Task 3.5: precondition to check existing tests before writing new ones
- D2 effort revised ~1h→~1.5h, total ~14h→~15h
- Test count refreshed ~2038→~2050
- Dependency graph Wave 2 updated to include Task 2.1b
- Notes section: D2 note updated for recovery method extension
- RFC: Amendment History section added, R2.5-R2.6 added, D2 decision/risks/consequences corrected
- Design doc: Overview, mermaid, D2, Service Contract 1, Property 2, Risk Mitigation, Launch Constraints all updated for D2 extension and D6 currency

### Amendment 4 (2026-09-04): Review iteration 4 — D4 fourth test + docstring residue + D6 effort

**Trigger:** 3-agent assess+review found two D4 consequence-of-deletion gaps missed by all prior iterations, plus 6 minor propagation/wording fixes.

**Gaps found:**
1. **GAP-14 (major):** Fourth test `test_pdf_document_type_preserves_existing_truth_table` (~line 798 in test_gates.py) monkeypatches `UNIFIED_OCR_PLAN_ENABLED` — will raise `AttributeError` once the flag is deleted by Task 3.2. Treatment: strip monkeypatch, retain parametrized truth table.
2. **GAP-15 (major):** Docstring (~line 378) and inline comment (~lines 385-387) in `picture_plane.py` reference `UNIFIED_OCR_PLAN_ENABLED` by name — Property 4's `grep` guard fails if these survive. Treatment: delete references alongside flag.
3. **GAP-16 (minor):** Task 3.2 line range stale (388-415 → 398-409, propagation miss from GAP-10).
4. **GAP-17 (minor):** D6 effort understated (~6h → ~8h) — coverage baseline capture and floor checks not budgeted.
5. **GAP-18 (minor):** RFC Sequencing missing Phase 5 (D6).
6. **GAP-19 (minor):** Test Strategy D2 bullet missing Amendment 3 recovery-method work assertion.
7. **GAP-20 (minor):** Task 2.1b missing circular-import safety note.
8. **GAP-21 (minor):** Property 6(c) wording imprecise on guard class name.

**Changes applied:**
- RFC: R4.6-R4.7 added (fourth test strip, docstring/comment removal); Sequencing Phase 5 added; Test Strategy D2 extended; D6 effort ~6h→~8h (total ~15h→~17h) with scope-drift note; Amendment 4 entry
- Design doc: D4 section expanded (fourth test treatment, docstring/comment deletion targets); D6 approach adds coverage baseline; Property 6(c) reworded with guard class name
- Tasks: Task 3.2 line range corrected + fourth test/docstring bullets + grep checkpoint added; Task 3.5 rewritten as confirmation step; Task 2.1b circular-import note; Task 7.1 coverage baseline capture; Task 7.4 coverage floor check; Overview effort ~15h→~17h; Amendment 4 entry

### Amendment 5 (2026-09-04): Review iteration 5 — convergence verification

**Trigger:** 6-agent verification pass confirmed all Amendment 4 changes correctly propagated; found one residual D6 effort prose mismatch and one structural asymmetry (Design doc missing Amendment History section).

**Changes applied:**
- RFC: D6 Decision Summary prose effort corrected ~6h→~8h; Amendment 5 entry
- Design doc: `## Amendment History` section added (closing GAP-11 structural asymmetry); Amendment 5 entry
- Tasks: Task 3.C expanded with tests/-side `UNIFIED_OCR_PLAN_ENABLED` zero-hit grep; Amendment 5 entry

