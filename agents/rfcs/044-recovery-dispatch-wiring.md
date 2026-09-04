---
id: RFC-044
title: Recovery Dispatch Wiring & OCR Decision Authority
type: rfc
status: draft
date: 2026-09-02
plan-impact: no
tags:
  - rfc
  - recovery-dispatch
  - ocr-authority
  - gate-masking
  - re-entry-guard
aliases:
  - RFC-044
  - Recovery Dispatch Wiring
governs:
  - "[[design-rfc044-recovery-dispatch-wiring]]"
  - "[[tasks-rfc044-recovery-dispatch-wiring]]"
supersedes: []
---

## Context

The POST-RFC043 architecture defect zones audit (2026-09-02) flagged two zones that share a common structural root: the relationship between gate declarations, recovery dispatch, and OCR decision authority.

**Zone 1** (OCR Pipeline Decision & Recovery Cascade, 12 bugs, critical) identifies three interacting structural causes: multiple independent OCR decision sites making contradictory verdicts, a mutable cross-call re-entry guard (`full_page_already_applied`) whose correctness depends on call ordering, and recovery methods that the zone report claims are "fully implemented but never called."

**Zone 6** (Gate-to-Recovery Dispatch Wiring Gap, 6 bugs, high, status=new) claims the GATES table declares recovery functions and eligibility predicates but "the runtime dispatcher never calls them," characterizing this as a declarative-specification-vs-runtime-execution disconnect.

**Critical correction:** Verification against the current tree (HEAD 896d455, branch ICR-97-rfc43-ocr-garble-erasure-hardening) reveals that both zone reports are **factually stale** on their central claims. The GATES-driven recovery dispatch loop already exists and runs at `client/indexer.py:1489-1514` (landed in commit fdd023e under RFC-043). It iterates every GateSpec in severity order, checks `recovery_eligible`, dispatches via `getattr(self, fn_name)(...)`, and deduplicates by method name. The four recovery methods (`_recover_garble_ocr`, `_recover_low_content_ocr`, `_recover_image_dominant_ocr`, `_recover_vlm_fallback`) have non-zero callers. Similarly, the gate-masking claim (NODE_COUNT_LOW severity=1 masks NODE_GARBLING severity=3) is addressed by a D4 override in `validate_tree` (tree_validation.py:431-440) that promotes garble-type defects to primary, and by `all_defects` field propagation through eligibility predicates. These fixes are exercised by passing tests in `test_garble.py`, `test_gates.py`, and `test_verdict.py`.

This RFC therefore **re-scopes** from "build the missing dispatcher" to addressing the **real remaining gaps** validated against current code:

1. The `full_page_already_applied` re-entry guard is checked inconsistently: `_recover_image_dominant_ocr` checks it (recovery.py:487), but `_recover_garble_ocr` and `_recover_low_content_ocr` do not -- a standing redundant-OCR bug.
2. `_eligible_rtl` (gates.py:325-327) still checks only `state.first_defect`, not `_all_defects(state)`, unlike all three other eligibility predicates -- RTL_REVERSAL co-firing behind a higher-severity defect would skip RTL recovery.
3. The `decide_ocr_strategy` call in `_convert_to_tree` (indexer.py:780-794) computes an OcrDecision purely for logging and never acts on it -- a dead call site that misleads readers and auditors.
4. `force_full_page` (Site 3) reaches Docling's OCR engine BEFORE `decide_ocr_strategy` is consulted, inverting cause and effect -- the "authoritative" decision function is told what already happened rather than deciding what should happen.
5. The `UNIFIED_OCR_PLAN_ENABLED` image branch in `decide_ocr_strategy` is unreachable dead code: neither call site ever passes `document_type='image'`, and standalone-image handling has its own separate path.

### Relationship to Prior RFCs

- [[RFC-043]]: Landed the GATES-driven recovery loop (indexer.py:1489-1514), `all_defects` field, D4 garble-priority override, and eligibility predicates using `_all_defects()`. This RFC addresses validated remaining gaps in that work.
- [[RFC-042]]: Covers verdict computation and config unification. No overlap.
- [[RFC-041]]: Original recovery method implementations and gate restructuring. RFC-043 wired them; this RFC hardens the wiring.
- [[RFC-029]]/[[RFC-030]]/[[RFC-036]]/[[RFC-038]]: Prior attempts at dispatch wiring that produced point patches without structural guarantees. This RFC addresses why those fixes did not hold (no exhaustiveness assertion spanning gates.py + recovery.py + indexer.py dispatch).

## Goals

- Eliminate redundant full-page OCR retries by adding the missing `full_page_already_applied` guard to `_recover_garble_ocr` and `_recover_low_content_ocr`
- Fix the `_eligible_rtl` predicate to check `_all_defects(state)` consistently with all other eligibility predicates
- Remove the dead `decide_ocr_strategy` call in `_convert_to_tree` that misleads audits
- Remove unreachable `UNIFIED_OCR_PLAN_ENABLED` image branch (dead code)
- Phase and scope the `force_full_page`-vs-`decide_ocr_strategy` authority inversion for safe incremental delivery
- Reduce the test suite from ~2050 to ~1000 tests by removing redundant, superseded, and architecture-guard-subsumed tests **(Amendment 2026-09-04)**

## Non-Goals

- Full OCR decision-site consolidation (merging `decide_ocr_strategy` / `_text_layer_has_content` / `force_full_page` / per-picture OCR into one authority) -- no prior RFC has attempted this; it is higher-risk, larger scope, and deferred to a follow-up RFC
- Moving the recovery dispatch loop from `index()` into `_convert_to_tree` -- the current location is correct and the encapsulation boundary is sound
- Changing GATES severity ordering or the D4 garble-priority override -- both are validated and passing tests
- Adding new recovery strategies or OCR escalation paths
- Restructuring the converter chain or Docling integration

## Glossary

| Term | Definition |
|------|------------|
| GATES-driven recovery loop | The dispatcher at indexer.py:1489-1514 that iterates GateSpec entries, checks `recovery_eligible`, and invokes `recovery_fns` via `getattr`. Landed in RFC-043 (commit fdd023e). |
| Re-entry guard | `state.full_page_already_applied` -- a mutable flag set after any full-page OCR pass succeeds. Intended to prevent redundant OCR retries. |
| Guard inconsistency | `_recover_image_dominant_ocr` checks the guard before retrying; `_recover_garble_ocr` and `_recover_low_content_ocr` do not. A document that already got full-page OCR and still fails with GARBLING triggers a redundant second OCR pass. |
| `_eligible_rtl` asymmetry | The only recovery-eligibility predicate that checks `state.first_defect` instead of `_all_defects(state)`. RTL_REVERSAL co-firing behind another defect would silently skip RTL recovery. |
| Dead call site | The `decide_ocr_strategy` invocation at indexer.py:780-794 that computes an OcrDecision and only logs it. The real live call is inside `_recover_picture_results` in the converter chain. |
| Authority inversion | `force_full_page` (Site 3) causes Docling to run full-page OCR before `decide_ocr_strategy` (Site 1) is ever consulted. The "single decision point" function learns what already happened rather than deciding what should happen. |
| `all_defects` | `TreeGateResult.all_defects: frozenset[TreeDefect]` -- every gate that fired during validate_tree's exhaustive evaluation, not just the primary defect. Used by `_eligible_garble`, `_eligible_low_content`, `_eligible_image_dominant` but NOT by `_eligible_rtl`. |
| D4 garble-priority override | validate_tree logic (tree_validation.py:431-440) that promotes garble-type defects to primary when co-firing with non-garble defects. Ensures OCR recovery dispatches correctly. |

## Requirements

### Requirement 1: Re-entry Guard Consistency

**User Story:** As the indexing pipeline, I want all OCR recovery methods to respect the `full_page_already_applied` guard consistently, so that a document does not receive redundant full-page OCR passes that waste compute and risk content-destruction regressions.

#### Acceptance Criteria

1. `_recover_garble_ocr` (recovery.py:400-432) SHALL check `if state.full_page_already_applied: return` before calling `_execute_ocr_retry`, matching the existing guard in `_recover_image_dominant_ocr` (recovery.py:487).
2. `_recover_low_content_ocr` (recovery.py:434-468) SHALL check `if state.full_page_already_applied: return` before calling `_execute_ocr_retry`.
3. A unit test SHALL verify: when `state.full_page_already_applied=True`, neither `_recover_garble_ocr` nor `_recover_low_content_ocr` calls `_execute_ocr_retry`.
4. An architecture guard test SHALL verify all three `_recover_*_ocr` methods contain a `full_page_already_applied` guard (AST or source-text inspection).

### Requirement 2: RTL Eligibility Predicate Consistency

**User Story:** As the indexing pipeline, I want `_eligible_rtl` to check `_all_defects(state)` so that RTL_REVERSAL recovery fires even when RTL_REVERSAL is a secondary defect behind a higher-severity primary, matching the behavior of all other eligibility predicates.

#### Acceptance Criteria

1. `_eligible_rtl` (gates.py:325-327) SHALL check `TreeDefect.RTL_REVERSAL in _all_defects(state)` instead of `state.first_defect == TreeDefect.RTL_REVERSAL`.
2. A unit test SHALL verify: when RTL_REVERSAL fires as a secondary defect behind NODE_COUNT_LOW (severity=1), `_eligible_rtl` returns True.
3. A unit test SHALL verify: when RTL_REVERSAL is the only defect, `_eligible_rtl` returns True (no regression).
4. An architecture guard test SHALL verify all four `_eligible_*` predicates use `_all_defects(state)` and none check `state.first_defect` directly.
5. `_recover_rtl_repair` (recovery.py:529) and `_recover_rtl_flat_compare` (recovery.py:588) SHALL gate on `TreeDefect.RTL_REVERSAL in _all_defects(state)` (using `state.gate_result.all_defects`) instead of `state.first_defect == TreeDefect.RTL_REVERSAL`, so that RTL recovery methods actually perform work when dispatched for a secondary RTL_REVERSAL defect. **(Amendment 2026-09-04, iteration 3)**
6. An architecture guard test SHALL verify no `_recover_rtl_*` method body in recovery.py contains `state.first_defect == TreeDefect.RTL_REVERSAL`. **(Note: `_recover_vlm_fallback`:659 also uses `first_defect` for Tesseract raster gating — deliberately excluded from D2 scope.) (Amendment 2026-09-04, iteration 3)**

### Requirement 3: Dead OCR Decision Call Removal

**User Story:** As a developer reading the codebase, I want only the live `decide_ocr_strategy` call site to exist, so that audits and readers do not chase phantom bugs at a log-only call site.

#### Acceptance Criteria

1. THE `decide_ocr_strategy` call at indexer.py:780-794 SHALL be removed (the call, its parameter construction, and its logger.debug block).
2. THE live call site in `converters/pictures.py::_recover_picture_results` SHALL remain unchanged.
3. An architecture guard test SHALL verify exactly one call site for `decide_ocr_strategy` exists in `src/` (excluding tests).

### Requirement 4: Unreachable UNIFIED_OCR_PLAN_ENABLED Image Branch Removal

**User Story:** As a developer, I want dead code paths removed rather than maintained, so that future audits do not flag unreachable branches as live behavior.

#### Acceptance Criteria

1. THE `UNIFIED_OCR_PLAN_ENABLED` feature flag and its gated branch inside `decide_ocr_strategy` for `document_type='image'` SHALL be removed.
2. THE `document_type` parameter of `decide_ocr_strategy` SHALL remain (it is part of the Zone-8 typed contract), but the image-specific short-circuit branch SHALL be deleted since it is unreachable.
3. IF standalone-image OCR handling is needed in the future, it SHALL be routed through `decide_ocr_strategy` with a call site that actually passes `document_type='image'` -- but that work is out of scope for this RFC.
4. A test SHALL verify `decide_ocr_strategy` behavior is unchanged for `document_type='pdf'` (the only value ever passed in production).
5. **(Amendment 2026-09-03):** THE three existing tests in `test_gates.py::TestDecideOcrStrategyDocumentType` that exercise the removed flag and image branch (`test_image_document_type_returns_full_page_with_splice_when_unified_enabled`, `test_image_document_type_ignored_when_unified_disabled`, `test_image_type_carries_custom_ocr_langs`) SHALL be deleted, since they test unreachable dead code that no longer exists.
6. **(Amendment 2026-09-04, iteration 4):** THE fourth test `test_pdf_document_type_preserves_existing_truth_table` (~line 798) monkeypatches `UNIFIED_OCR_PLAN_ENABLED`; the monkeypatch SHALL be stripped but the parametrized truth table SHALL be retained (it validates R4.4 PDF-path parity).
7. **(Amendment 2026-09-04, iteration 4):** ALL docstring and inline comment references to `UNIFIED_OCR_PLAN_ENABLED` in `picture_plane.py` (docstring ~line 378, inline comment ~lines 385-387) SHALL be removed so that Property 4's `grep -rn UNIFIED_OCR_PLAN_ENABLED src/` architecture guard passes.

### Requirement 5: Force-Full-Page Authority Documentation

**User Story:** As an architect reviewing the OCR pipeline, I want the authority inversion between `force_full_page` and `decide_ocr_strategy` explicitly documented with a concrete remediation plan, so that the next RFC addressing OCR consolidation has a clear starting point.

#### Acceptance Criteria

1. THE `force_full_page` assignment (indexer.py:536) SHALL receive an inline comment documenting the authority inversion: it causes Docling to run full-page OCR before `decide_ocr_strategy` is consulted, and explains why this ordering exists (pre-conversion probe cannot compute `has_image_markers`).
2. THE `decide_ocr_strategy` docstring SHALL be amended to note its actual authority scope: "first conversion pass only, post-conversion diagnostic; recovery-pass OCR decisions are made independently by recovery methods in client/recovery.py."
3. A design note SHALL be added to this RFC's design document outlining the phased consolidation approach: Phase A (this RFC) removes dead/misleading call sites; Phase B (future RFC) inverts the relationship so `force_full_page` inputs feed `decide_ocr_strategy` rather than bypassing it; Phase C (future RFC) folds recovery-pass OCR decisions into the same typed authority.

### Requirement 6: Test Suite Reduction **(Amendment 2026-09-04)**

**User Story:** As a developer, I want the test suite reduced from ~2050 tests to ~1000 tests, so that test runs are faster, maintenance burden is lower, and redundant coverage is eliminated without sacrificing correctness guarantees.

#### Acceptance Criteria

1. A full inventory of tests SHALL be produced, categorized by file, type (unit/integration/architecture guard/regression), and coverage overlap.
2. Tests that exercise code removed or superseded by RFC-041 through RFC-044 SHALL be identified and deleted.
3. Tests whose assertions are subsumed by architecture guards SHALL be consolidated or removed.
4. Fixture-heavy integration tests that differ only in one parameter SHALL be parametrized into single test methods.
5. The post-reduction suite SHALL pass in full (`uv run pytest`), all architecture guards SHALL hold, and no corpus verdict regressions SHALL occur.
6. Final test count SHALL be ≤1100 (target ~1000, ±10% margin).

## Decision Summary

### D1: Re-entry Guard Consistency (Requirement 1)

Add `if state.full_page_already_applied: return` as the first guard in both `_recover_garble_ocr` (after the existing `if state.ok or ext != ".pdf": return` at recovery.py:415-416) and `_recover_low_content_ocr` (after recovery.py:449-450). This matches the existing pattern in `_recover_image_dominant_ocr` (recovery.py:487). The guard prevents a document that already received full-page OCR (either from the pre-conversion `force_full_page` path or from a prior recovery step) from triggering a redundant, wasteful second OCR retry. No behavior change for documents that have not yet had full-page OCR. ~1h.

### D2: RTL Eligibility Predicate Consistency (Requirement 2)

Change `_eligible_rtl` (gates.py:325-327) from `state.first_defect == TreeDefect.RTL_REVERSAL` to `TreeDefect.RTL_REVERSAL in _all_defects(state)`. This aligns it with `_eligible_garble`, `_eligible_low_content`, and `_eligible_image_dominant`, all of which were explicitly patched in RFC-043 to use `_all_defects(state)` with docstrings explaining the Zone-1 fix rationale. The asymmetry in `_eligible_rtl` appears to be an oversight rather than intentional exclusion -- no docstring or comment explains why RTL should behave differently. If RTL_REVERSAL co-fires behind NODE_COUNT_LOW or GARBLING (which the D4 override would promote to primary), RTL recovery would currently be silently skipped. ~1.5h **(revised 2026-09-04, iteration 3: +0.5h for recovery method guard patching)**.

**(Amendment 2026-09-04, iteration 3):** The eligibility predicate fix alone is insufficient. Both RTL recovery methods (`_recover_rtl_repair` at recovery.py:529 and `_recover_rtl_flat_compare` at recovery.py:588) also gate on `state.first_defect == TreeDefect.RTL_REVERSAL` internally. Without patching these internal guards, D2's eligibility change makes the GATES loop dispatch the methods, but they return immediately — making D2 a no-op for the secondary-defect case. Extend D2 to also patch the two recovery-method guards. `_recover_vlm_fallback`:659 uses `first_defect` for its Tesseract raster fallback gating — this is deliberately out of D2 scope and documented as such.

### D3: Dead OCR Decision Call Removal (Requirement 3)

Delete the `decide_ocr_strategy` call at indexer.py:780-794 and its surrounding logger.debug block. This call computes an OcrDecision that is never acted upon -- the real live call site is inside `_recover_picture_results` in the converter chain. The dead call misleads auditors (the POST-RFC043 zone audit cited it as evidence of "multiple independent OCR decision sites making contradictory verdicts" -- one of those "sites" is a log statement). Removing it reduces the apparent decision surface from 4 sites to 3 genuine ones. ~0.5h.

### D4: Unreachable Image Branch Removal (Requirement 4)

Delete the `UNIFIED_OCR_PLAN_ENABLED` flag (picture_plane.py:349-352) and its gated branch inside `decide_ocr_strategy`. Neither call site ever passes `document_type='image'`, and standalone-image handling has its own entirely separate path in `_convert_to_tree` (the `elif ext in _IMAGE_EXTS:` branch) that never calls `decide_ocr_strategy`. The branch is confirmed unreachable dead code regardless of the flag's value. The `document_type` parameter stays as part of the Zone-8 typed contract; only the image-specific short-circuit is removed. **(Amendment 2026-09-03):** Also delete the 3 tests in `test_gates.py::TestDecideOcrStrategyDocumentType` that exercise the removed flag/branch. ~1h (revised from ~0.5h to include test cleanup).

### D5: Force-Full-Page Authority Documentation (Requirement 5)

This is a documentation-and-scoping decision, not a code restructuring. The authority inversion exists because `force_full_page` runs during pre-conversion (before markdown content exists, so `has_image_markers` is unknowable) while `decide_ocr_strategy` needs post-conversion signals. Fixing this inversion requires restructuring the conversion pipeline so that a lightweight pre-scan produces the inputs `decide_ocr_strategy` needs, which then gates whether Docling runs native full-page OCR -- this is Phase B/C scope, not Phase A. D5 adds inline documentation at the key sites and a design note for the follow-up RFC. ~1h.

### D6: Test Suite Reduction (Requirement 6) **(Amendment 2026-09-04)**

Audit the full test suite (~2050 tests across all prior RFCs) for redundancy, superseded behavior, and architecture-guard subsumption. The primary consolidation targets are: (a) tests exercising code removed by RFC-041→044 dead-code cleanup, (b) tests duplicating assertions that architecture guards now enforce structurally, (c) fixture-heavy integration tests that can be parametrized. This is a bulk-cleanup wave that runs after all RFC-044 code changes land and verify. ~8h (revised 2026-09-04 iteration 4 from ~6h: +2h coverage baseline/floor).

## Implementation Plan

### Sequencing

1. **Phase 1: Guard Fixes** (D1, D2) -- coupled within the gate/recovery subsystem. D1 and D2 are independent of each other but both touch the same test infrastructure.
2. **Phase 2: Dead Code Removal** (D3, D4) -- independent of Phase 1. Can be parallelized.
3. **Phase 3: Documentation** (D5) -- depends on D3/D4 being finalized (the documented authority scope must reflect the post-cleanup state).
4. **Phase 4: Integration Tests** -- cross-decision regression validation against the corpus.
5. **Phase 5: Test Suite Reduction** (D6) -- depends on all prior phases; Wave 7 runs only after 6.F. **(Amendment 2026-09-04, iteration 4)**

### Effort Estimate

| Phase | Deliverable | Effort | Risk |
|-------|------------|--------|------|
| 1 | D1: Re-entry guard consistency | ~1h | Low -- adding a guard that 1 of 3 methods already has |
| 1 | D2: RTL eligibility fix + recovery method guard patching | ~1.5h | Low -- aligning with 3 existing predicates + patching 2 recovery method guards **(revised 2026-09-04, iteration 3)** |
| 2 | D3: Dead call site removal | ~0.5h | Low -- removing log-only code |
| 2 | D4: Unreachable branch removal + test cleanup | ~1h | Low -- removing dead code and 3 dead-code tests **(revised 2026-09-03: +0.5h for test cleanup)** |
| 3 | D5: Authority documentation | ~1h | Low -- documentation only |
| 4 | Integration tests (consolidated) | ~1.5h | Low **(revised 2026-09-04: 5.1/5.2/5.3 dropped/absorbed, only 5.4 slimmed remains)** |
| 5 | D6: Test suite reduction audit | ~8h | Medium — bulk deletion requires careful coverage validation; +2h for coverage baseline capture and floor checks **(revised 2026-09-04 iteration 4 from ~6h)** |
| **Total** | | **~17h** | **(revised 2026-09-04 iteration 4 from ~15h: D6 +2h coverage baseline/floor)** |

> **Note (Amendment 4):** D6 is a bundled maintenance deliverable, thematically distinct from D1–D5's dispatch-wiring hardening. It should not grow further in scope.

## Test Strategy

- **D1:** Unit test: mock `_execute_ocr_retry`, set `state.full_page_already_applied=True`, call `_recover_garble_ocr` and `_recover_low_content_ocr`, assert `_execute_ocr_retry` was not called. Architecture guard: AST-inspect all three `_recover_*_ocr` methods for `full_page_already_applied` guard presence.
- **D2:** Unit test: construct ExtractionState with `gate_result.all_defects={NODE_COUNT_LOW, RTL_REVERSAL}` and `first_defect=NODE_COUNT_LOW`, assert `_eligible_rtl(state)` returns True. Regression test: RTL_REVERSAL as sole defect still returns True. Architecture guard: grep/AST-verify no `_eligible_*` predicate uses `state.first_defect`. **(Amendment 3 extension):** Additionally verify `_recover_rtl_repair` and `_recover_rtl_flat_compare` perform work (invoke `reconstruct_bidi_order` or `_reconvert_and_revalidate`) when dispatched for secondary RTL_REVERSAL — not merely that they are called. Architecture guard: no `_recover_rtl_*` method body in recovery.py contains `state.first_defect == TreeDefect.RTL_REVERSAL`.
- **D3:** Architecture guard: count `decide_ocr_strategy(` call sites in `src/` (excluding tests), assert exactly 1.
- **D4:** Unit test: `decide_ocr_strategy(document_type='pdf', ...)` produces identical results before and after removal. Verify `UNIFIED_OCR_PLAN_ENABLED` string does not appear in `src/`.
- **D5:** No automated tests (documentation deliverable). Validated by design-doc review.
- **Integration:** Run corpus scoring against the 3 documents from the RFC-029 Run 13 regression (if available) and any documents with co-firing RTL_REVERSAL + other defects to verify correct multi-defect recovery dispatch.



## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| D1 guard prevents garble recovery that would have succeeded on second OCR pass | Low | Medium | The guard only fires when a prior full-page OCR already ran and the document STILL shows garble -- a second identical OCR pass is extremely unlikely to produce a different result. If it did, the prior pass's output would already be in `state.md_content` and the guard's purpose (preventing content-destruction regressions from redundant OCR) outweighs the marginal recovery chance. |
| D2 RTL eligibility change causes RTL recovery to fire on documents where it previously did not | Low | Low | RTL recovery is internally guarded (`_recover_rtl_repair` checks `state.ok`, bidi signals, `bidi_renorm_applied`, etc.). **(Amendment 2026-09-04, iteration 3):** The dominant internal guard — `state.first_defect == TreeDefect.RTL_REVERSAL` at recovery.py:529 and :588 — is also patched by D2 to use `_all_defects(state)`, so RTL recovery methods both become eligible AND perform work for secondary RTL_REVERSAL. The change means the eligibility check no longer silently skips when RTL_REVERSAL is a secondary defect. |
| D3 removal of dead call site breaks logging that operators rely on | Low | Low | The log line is at DEBUG level. If post-conversion OcrDecision logging is needed, it can be added to the live call site in `_recover_picture_results`. |
| D4 removal of UNIFIED_OCR_PLAN_ENABLED breaks future image-routing plans | Low | Low | The flag is currently unreachable dead code. Any future image-routing work must add a call site that passes `document_type='image'` regardless, so removing the dead branch does not block that work. |
| Zone reports are re-used as evidence for future audits without re-verification | Medium | High | This RFC explicitly documents that Zone 1 and Zone 6 claims are stale against current HEAD. The traceability table links to both zone specs. Recommend updating zone frontmatter to `status: partially-addressed` after this RFC lands. |
| D6 test reduction over-deletes, causing silent coverage loss **(Amendment 2026-09-04)** | Medium | High | Batch-and-verify approach (test after each deletion batch), architecture guards as structural coverage backstop, coverage baseline capture with ≥baseline−1pt floor, corpus regression check at the end. All architecture guard classes must survive by name. |

## Consequences

- Documents that already received full-page OCR (from `force_full_page` or a prior recovery step) will no longer trigger a redundant second OCR retry via `_recover_garble_ocr` or `_recover_low_content_ocr` -- expect reduced compute cost per failed document, no change to recovery outcomes.
- RTL_REVERSAL co-firing as a secondary defect behind NODE_COUNT_LOW, DEPTH_LOW, or GARBLING will now trigger RTL recovery -- a small population of documents with mixed defects may see improved recovery outcomes. **(Amendment 2026-09-04, iteration 3):** This consequence requires the Amendment 3 extension to D2 — patching both the eligibility predicate (gates.py) and the recovery-method internal guards (recovery.py:529, :588). Without both changes, the predicate dispatches the methods but they return immediately.
- Auditors reading `_convert_to_tree` will see one fewer `decide_ocr_strategy` call, reducing the apparent decision surface to 3 genuine sites (down from the 4 cited in the zone report).
- The `UNIFIED_OCR_PLAN_ENABLED` env var ceases to exist -- operators with it set in their environment will see no behavior change (it was always dead code).
- The authority inversion between `force_full_page` and `decide_ocr_strategy` is now explicitly documented, providing a clear starting point for a future consolidation RFC.
- Zone 1 and Zone 6 bug counts should decrease on the next audit re-run: D1 closes the inconsistent-guard bug (1 bug from Zone 1), D2 closes the RTL masking gap (1 bug from Zone 1), D3 closes the dead-call-site finding (1 bug from Zone 1), D4 closes the unreachable-branch finding (1 bug from Zone 1).

## Amendment History

### Amendment 1 (2026-09-03): Review iteration 1 — test cleanup + VLM escape-hatch

**Trigger:** Multi-agent review flagged GAP-1 (D4 breaks 3 existing tests), GAP-2 (D3 import cleanup), GAP-3 (no VLM escape-hatch test), GAP-4 (optimistic effort).

**Changes:** R4.5 added (delete 3 dead-code tests); D4 effort revised ~0.5h→~1h; Task 5.4 (VLM escape-hatch) added.

### Amendment 2 (2026-09-04): Review iteration 2 — test consolidation + Wave 7

**Trigger:** 4-agent review flagged GAP-5 (OcrDecision/OcrMode import check), GAP-6 (import os removal), Wave 5 test overlap, user-directed test reduction.

**Changes:** Task 3.1 expanded (GAP-5); Task 3.2 expanded (GAP-6); Wave 5 consolidated (5.1-5.3 dropped, 5.4 slimmed); Wave 7 added (test suite reduction ~2050→~1000); R6/D6 added; effort revised ~8h→~14h.

### Amendment 3 (2026-09-04): Review iteration 3 — D2 no-op fix + D6 currency

**Trigger:** 6-agent review found D2 eligibility fix is inert — both RTL recovery methods (`_recover_rtl_repair`:529, `_recover_rtl_flat_compare`:588) also gate on `state.first_defect`. Design doc D6 sections not propagated.

**Changes:** R2.5-R2.6 added (patch recovery method guards + arch guard); D2 effort revised ~1h→~1.5h; D2 risk/consequences corrected; R6 moved into Requirements section; D6 moved into Decision Summary; D6 added to Goals/Risks; test count refreshed ~2038→~2050; Amendment History section added to RFC; total effort revised ~14h→~15h.

### Amendment 4 (2026-09-04): Review iteration 4 — D4 fourth test + docstring residue + D6 effort

**Trigger:** 3-agent assess+review found two D4 consequence-of-deletion gaps missed by all prior iterations: (1) fourth test `test_pdf_document_type_preserves_existing_truth_table` (~line 798) monkeypatches `UNIFIED_OCR_PLAN_ENABLED` and will raise `AttributeError` once the flag is deleted; (2) docstring and inline comment references to `UNIFIED_OCR_PLAN_ENABLED` in `picture_plane.py` break Property 4's grep guard. Plus 6 minor propagation/wording fixes.

**Changes:** R4.6-R4.7 added (fourth test strip-not-delete, docstring/comment removal); RFC Sequencing adds Phase 5 (D6); Test Strategy D2 bullet extended with Amendment 3 recovery-method work assertion; D6 effort revised ~6h→~8h (total ~15h→~17h) with D6 scope-drift note; Task 3.2 line range corrected (388-415→398-409) and expanded (fourth test, docstring/comment bullets, grep checkpoint); Task 3.5 rewritten as confirmation step; Task 2.1b adds circular-import note; Task 7.1 adds coverage-baseline capture; Task 7.4 adds coverage-floor check; Property 6(c) reworded; Design D4 lists fourth test treatment and docstring/comment deletion targets.

### Amendment 5 (2026-09-04): Review iteration 5 — convergence verification

**Trigger:** 6-agent verification pass confirmed all Amendment 4 changes correctly propagated; found one residual D6 effort prose mismatch and one structural asymmetry (Design doc missing Amendment History section).

**Changes:** D6 Decision Summary prose effort corrected ~6h→~8h (line 177); Design doc gains `## Amendment History` section (closing GAP-11); Task 3.C gains tests/-side `UNIFIED_OCR_PLAN_ENABLED` zero-hit grep. No scope changes; convergence declared.

## Traceability

| Artifact | Reference |
|----------|-----------|
| Design | [[design-rfc044-recovery-dispatch-wiring]] |
| Tasks | [[tasks-rfc044-recovery-dispatch-wiring]] |
| Supersedes | N/A |
| Zone Specs | [[ocr-pipeline-decision-recovery-cascade]] (Zone 1, critical, 12 bugs), [[gate-to-recovery-dispatch-wiring-gap]] (Zone 6, high, 6 bugs) |
| Prior Art | [[RFC-043]] (GATES loop + all_defects), [[RFC-041]] (recovery methods), [[RFC-029]]/[[RFC-030]] (original dispatch gap + point patches) |
| Evidence: Dispatcher exists | `src/pageindex_mcp/client/indexer.py:1489-1514`, commit fdd023e |
| Evidence: D4 override exists | `src/pageindex_mcp/helpers/tree_validation.py:431-440` |
| Evidence: Guard inconsistency | `recovery.py:487` (checked) vs `recovery.py:400-432` and `recovery.py:434-468` (not checked) |
| Evidence: RTL asymmetry | `gates.py:325-327` (`first_defect`) vs `gates.py:276-322` (`_all_defects`) |
| Evidence: Dead call site | `indexer.py:780-794` (log-only) vs `converters/pictures.py:1065-1070` (live) |
| Evidence: Unreachable branch | `picture_plane.py:349-352` (flag) + no call site passes `document_type='image'` |
