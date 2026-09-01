<!-- Space: CITRA -->
<!-- Title: Implementation Plan: Recurring Defect Consolidation -->
<!-- Folder: Tasks -->

---
id: "tasks-rfc041-recurring-defect-consolidation"
title: "Tasks: Recurring Defect Consolidation"
type: tasks
status: draft
date: "2026-08-31"
tags:
  - tasks
  - garble
  - verdict
  - recovery
  - test-oracle
  - rfc-lifecycle
aliases:
  - "tasks-rfc041-recurring-defect-consolidation"
governs:
  - "[[RFC-041]]"
---

# Implementation Plan: Recurring Defect Consolidation

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [RFC-041](../rfcs/041-recurring-defect-consolidation.md) |
| Design Document | [design-rfc041-recurring-defect-consolidation](../designs/design-rfc041-recurring-defect-consolidation.md) |
| PRD / Requirements | [[PRD]] |
| Hard Rules | [CLAUDE.md HR5](../../CLAUDE.md) — never silently persist low-quality tree |
| Implementation order | [RFC-041 Sequencing](../rfcs/041-recurring-defect-consolidation.md#sequencing) |
| Test strategy | [RFC-041 Test Strategy](../rfcs/041-recurring-defect-consolidation.md#test-strategy) |
| Correctness properties | [Design Properties 1–10](../designs/design-rfc041-recurring-defect-consolidation.md#correctness-properties) |

## Overview

Consolidates 10 interdependent deliverables ([RFC-041 D1–D10](../rfcs/041-recurring-defect-consolidation.md#decision-summary)) across 5 waves into a dependency-ordered implementation plan. Proceeds from immediate quick wins (wave 0) through foundation consolidation (waves 1–2) to test coverage (wave 3) and lifecycle enforcement (wave 4), validating [10 correctness properties](../designs/design-rfc041-recurring-defect-consolidation.md#correctness-properties) at each checkpoint.

## Tasks

- [ ] <a id="1-wave-0--immediate-quick-wins-d4"></a>1. Wave 0 — Immediate Quick Wins ([D4](../rfcs/041-recurring-defect-consolidation.md#d4-recovery-dispatch-cross-tuple-dedup-requirement-4))

  *[RFC-041 Wave 0](../rfcs/041-recurring-defect-consolidation.md#sequencing): Zero/low risk, independent quick wins*

  - [ ] <a id="13-recovery-dispatch-cross-tuple-dedup-d4"></a>1.3 Recovery dispatch cross-tuple dedup ([D4](../rfcs/041-recurring-defect-consolidation.md#d4-recovery-dispatch-cross-tuple-dedup-requirement-4))

    - Change recovery dispatch to dedup by method name across ALL gate tuples (not per-tuple)
    - Add `full_page_already_applied` guard at `_recover_image_dominant_ocr` entry
    - Add test: two co-firing defects (NODE_COUNT_LOW + DEPTH_LOW) with same recovery method — method executes once
    - _Requirements:_ [RFC-041 D4](../rfcs/041-recurring-defect-consolidation.md#d4-recovery-dispatch-cross-tuple-dedup-requirement-4) | [Design Property 4](../designs/design-rfc041-recurring-defect-consolidation.md#property-4-recovery-dedup-idempotency) | [Design Service: recovery.py](../designs/design-rfc041-recurring-defect-consolidation.md#5-recoverypy) | [Design Sequence: Recovery Dispatch Flow](../designs/design-rfc041-recurring-defect-consolidation.md#recovery-dispatch-flow--d3--d4)

  - [ ] <a id="14-consolidate-vlm-fallback-triple-block-d4"></a>1.4 Consolidate VLM fallback triple-block ([D4](../rfcs/041-recurring-defect-consolidation.md#d4-recovery-dispatch-cross-tuple-dedup-requirement-4))

    - Collapse three identical `_attempt_tesseract_raster_recovery` fallback blocks in `_recover_vlm_fallback` (:650,:668,:686) into one
    - Add test: VLM fallback with tesseract raster recovery — single block executes
    - _Requirements:_ [RFC-041 D4](../rfcs/041-recurring-defect-consolidation.md#d4-recovery-dispatch-cross-tuple-dedup-requirement-4) | [Design Property 4](../designs/design-rfc041-recurring-defect-consolidation.md#property-4-recovery-dedup-idempotency) | [Design Service: recovery.py](../designs/design-rfc041-recurring-defect-consolidation.md#5-recoverypy)

  - [ ] <a id="16-zone-5-ng5-verification"></a>1.6 Zone 5 (NG5) verification **(Added: review v2 2026-09-01)**

    - Grep for the retry-loop fix claimed to cover Zone 5 (converter chain HR4 enforcement)
    - Validate the fix against the Zone 5 audit spec (`audit/zones/zone5_*`)
    - If the retry-loop fix covers Zone 5: document the evidence and mark NG5 as verified-deferred
    - If the retry-loop fix does NOT cover Zone 5: escalate to user for scoping — either absorb into RFC-041 as a new deliverable or create a successor RFC
    - _Rationale:_ Prevents repeating the RFC-040 orphan pattern where Zone 2 was deferred on an unvalidated claim
    - _Requirements:_ [RFC-041 NG5](../rfcs/041-recurring-defect-consolidation.md#non-goals)

  - [ ] <a id="15-checkpoint--wave-0"></a>1.5 Checkpoint — Wave 0

    - Run `uv run pytest` and verify all existing tests pass
    - Verify [Property 4](../designs/design-rfc041-recurring-defect-consolidation.md#property-4-recovery-dedup-idempotency) — new tests pass
    - Verify Zone 5 (NG5) verification outcome documented **(Added: review v2 2026-09-01)**
    - Ask the user if questions arise before proceeding.

- [ ] <a id="2-wave-1--garble-and-text-accessor-foundation-d1-d2"></a>2. Wave 1 — Garble & Text Accessor Foundation ([D1](../rfcs/041-recurring-defect-consolidation.md#d1-garble-entry-point-consolidation-requirement-1), [D2](../rfcs/041-recurring-defect-consolidation.md#d2-unified-block-text-accessor-requirement-2))

  *[RFC-041 Wave 1](../rfcs/041-recurring-defect-consolidation.md#sequencing): Unify garble + text accessors before state routing*

  - [ ] <a id="21-garble-entry-point-consolidation-d1"></a>2.1 Garble entry point consolidation ([D1](../rfcs/041-recurring-defect-consolidation.md#d1-garble-entry-point-consolidation-requirement-1))

    - Replace `garble_prongs` call in `_garble_check_nodes` fallback (:745–750) with `detect_garble`
    - Rename `garble_prongs` → `_garble_prongs`
    - Remove `garble_prongs` from `helpers/__init__.py` import (:100) and `__all__` re-export (:312) — garble.py has no `__all__` of its own
    - Add test: fallback path produces same result as direct `detect_garble` call
    - Add test: document below `garble_digit_floor` — fallback now consistently handled by `detect_garble`
    - _Requirements:_ [RFC-041 D1](../rfcs/041-recurring-defect-consolidation.md#d1-garble-entry-point-consolidation-requirement-1) | [Design Property 1](../designs/design-rfc041-recurring-defect-consolidation.md#property-1-garble-detection-convergence) | [Design Service: garble.py](../designs/design-rfc041-recurring-defect-consolidation.md#1-garblepy) | [Design Sequence: Garble Detection Flow](../designs/design-rfc041-recurring-defect-consolidation.md#garble-detection-flow--d1--d10)

  - [ ] <a id="22-block-text-accessor-unification-d2"></a>2.2 Block text accessor unification ([D2](../rfcs/041-recurring-defect-consolidation.md#d2-unified-block-text-accessor-requirement-2))

    - Create `BlockTextPurpose` enum in `flat.py`: `GARBLE_CHECK`, `SEARCH`, `CHAR_COUNT`, `DISPLAY`
    - Create two-tier API **(review v2 2026-09-01)**: `block_text(block: dict, purpose: BlockTextPurpose) -> str` for single-block extraction + `doc_text(data: dict, purpose: BlockTextPurpose) -> str` for whole-document iteration. `doc_text` iterates blocks and delegates to `block_text` per block.
    - **Interface note:** `_flat_search_text` operates on the **entire document** (`data: dict`) while `_flat_block_primary_text` operates on a **single block**. `block_text` alone cannot replace `_flat_search_text` — `doc_text` is required for whole-doc callers.
    - Refactor `_flat_block_primary_text` to delegate to `block_text(block, CHAR_COUNT)`
    - Refactor `_flat_search_text` to delegate to `doc_text(data, SEARCH)` — which internally calls `block_text(block, SEARCH)` per block
    - Refactor `_node_text_parts` (tree_validation.py :51) to delegate to `block_text(block, CHAR_COUNT)`
    - Zone-9 header-only-table fix applies to all purposes via `block_text`
    - Verify `helpers/rag.py` (~:190) callers work correctly with `block_text(block, SEARCH)` — validate search quality impact alongside verdict corpus diff
    - **Added (root-cause review 2026-08-31):** Refactor `helpers/garble.py` internal callers: `_node_text_parts` at :648,:685 → `block_text(block, GARBLE_CHECK)`; `_flat_block_primary_text` at :780 → `block_text(block, CHAR_COUNT)`. Regression-test garble scores for table-heavy docs to ensure per-node table-content check (:692-695) is stable
    - Add test: all three accessor paths produce consistent text for table blocks
    - Add test: garble.py internal callers produce same garble scores pre/post migration (regression guard)
    - Add test: `block_text` with each purpose for table, paragraph, image blocks
    - _Requirements:_ [RFC-041 D2](../rfcs/041-recurring-defect-consolidation.md#d2-unified-block-text-accessor-requirement-2) | [Design Property 2](../designs/design-rfc041-recurring-defect-consolidation.md#property-2-block-text-consistency) | [Design Service: flat.py](../designs/design-rfc041-recurring-defect-consolidation.md#2-flatpy) | [Design Service: tree_validation.py](../designs/design-rfc041-recurring-defect-consolidation.md#3-tree_validationpy)

  - [ ] <a id="23-ci-lint-for-garble-prongs-and-block-text-d1-d2"></a>2.3 CI lint for `garble_prongs` and `block['text']` ([D1](../rfcs/041-recurring-defect-consolidation.md#d1-garble-entry-point-consolidation-requirement-1), [D2](../rfcs/041-recurring-defect-consolidation.md#d2-unified-block-text-accessor-requirement-2))

    - Add CI grep rule blocking direct `_garble_prongs` calls outside `garble.py`
    - Add CI grep rule flagging direct `block['text']` access outside `block_text()`
    - Add test: CI lint test for both patterns
    - _Requirements:_ [RFC-041 D1](../rfcs/041-recurring-defect-consolidation.md#d1-garble-entry-point-consolidation-requirement-1) | [RFC-041 D2](../rfcs/041-recurring-defect-consolidation.md#d2-unified-block-text-accessor-requirement-2) | [Design Property 1](../designs/design-rfc041-recurring-defect-consolidation.md#property-1-garble-detection-convergence) | [Design Property 2](../designs/design-rfc041-recurring-defect-consolidation.md#property-2-block-text-consistency)

  - [ ] <a id="24-fix-arabic-dead-code-d10"></a>2.4 Fix 'Arabic' vs 'Arab' dead code ([D10](../rfcs/041-recurring-defect-consolidation.md#d10-dead-code-and-accessor-parity-fixes-requirement-8))

    - Change `garble.py` :583 comparison from `'Arabic'` to `'Arab'` to match `_infer_script` return value
    - Add test: Arabic-script text now hits the garble detection path (previously dead code)
    - **Depends on Task 2.1 (D1)** — D1 consolidates the garble entry point; D10's Arabic fix lands in the consolidated path
    - _Requirements:_ [RFC-041 D10](../rfcs/041-recurring-defect-consolidation.md#d10-dead-code-and-accessor-parity-fixes-requirement-8) | [Design Property 9](../designs/design-rfc041-recurring-defect-consolidation.md#property-9-dead-code-elimination) | [Design Service: garble.py](../designs/design-rfc041-recurring-defect-consolidation.md#1-garblepy)

  - [ ] <a id="25-apply-zone-9-fix-to-flat-search-text-d10"></a>2.5 Apply Zone-9 fix to `_flat_search_text` ([D10](../rfcs/041-recurring-defect-consolidation.md#d10-dead-code-and-accessor-parity-fixes-requirement-8))

    - Add header-only-table fallback to `_flat_search_text` (flat.py :200) matching `_flat_block_primary_text` (:192)
    - Add test: header-only table block returns header text from `_flat_search_text`
    - _Requirements:_ [RFC-041 D10](../rfcs/041-recurring-defect-consolidation.md#d10-dead-code-and-accessor-parity-fixes-requirement-8) | [Design Property 10](../designs/design-rfc041-recurring-defect-consolidation.md#property-10-accessor-parity) | [Design Service: flat.py](../designs/design-rfc041-recurring-defect-consolidation.md#2-flatpy)

  - [ ] <a id="26-thread-pre-nfkc-scriptcontext-d10c"></a>2.6 Thread pre-NFKC ScriptContext to post-NFKC call sites ([D10c](../rfcs/041-recurring-defect-consolidation.md#d10-dead-code-accessor-parity-and-zone-2-pf-remediation-requirement-8)) **(Added: root-cause review 2026-08-31)**

    **Zone 2 remediation — absorbs orphaned NG2 scope**

    **Files:** `pictures.py` (:272,:393), `client/recovery.py` (:125), `client/indexer.py` (:514,:1015,:1041), `helpers/garble.py` (:855), `images.py` (:145), `tree_validation.py` (:392), `verdict.py` (:257) **(3 sites added: review v2 2026-09-01)**

    - Thread pre-NFKC `ScriptContext` (computed before NFKC decomposition in `_pre_inference_normalize`) to all **10** `_infer_presentation_forms` call sites that currently construct `ScriptContext` from post-NFKC text **(corrected from 7: review v2 2026-09-01)**
    - Each call site must receive a `ScriptContext` where `had_presentation_forms` was computed on the original (pre-NFKC) text, not on the decomposed text where PF codepoints no longer exist
    - This may require adding a `pre_nfkc_script_context: ScriptContext | None` parameter to functions in the recovery/pictures/indexer/images/tree_validation/verdict call chains
    - Add test: for each of the 10 sites, Arabic text with presentation forms is correctly detected as having PFs (currently returns False for all 10)
    - Add test: `_infer_presentation_forms` result matches between pre-NFKC and post-NFKC paths for Latin text (no change expected)
    - **Depends on Task 2.4 (D10a)** — the `"Arabic"` → `"Arab"` fix must land first so `detect_garble`'s own safety net works while D10c is in progress
    - **Corpus diff expected:** 0-3 docs with PF-bearing Arabic text may see garble scores change as PFs are now correctly detected
    - _Requirements:_ [RFC-041 D10c](../rfcs/041-recurring-defect-consolidation.md#d10-dead-code-accessor-parity-and-zone-2-pf-remediation-requirement-8)

  - [ ] <a id="27-checkpoint--wave-1"></a>2.7 Checkpoint — Wave 1 **(renumbered from 2.6: review v2 2026-09-01 — resolved ID collision with D10c task)**

    - Run `uv run pytest` and verify all tests pass
    - Verify [Property 1](../designs/design-rfc041-recurring-defect-consolidation.md#property-1-garble-detection-convergence), [Property 2](../designs/design-rfc041-recurring-defect-consolidation.md#property-2-block-text-consistency), [Property 9](../designs/design-rfc041-recurring-defect-consolidation.md#property-9-dead-code-elimination), [Property 10](../designs/design-rfc041-recurring-defect-consolidation.md#property-10-accessor-parity) — new tests pass
    - Corpus diff: expect 0–2 verdict changes from D1 (short-text rule in fallback), 0–3 from D2 (Zone-9 propagation), 0–1 from D10 Arabic fix
    - Verify CI lints block `_garble_prongs` and `block['text']` violations
    - **Rollback gate:** If corpus diff exceeds forecast, restore original accessor functions and `garble_prongs` export before proceeding
    - **Zone 2 ownership gate:** Resolve Zone 2 ownership (new successor RFC or RFC-041 amendment) by this checkpoint — see [RFC-041 Risk 8](../rfcs/041-recurring-defect-consolidation.md#risks)
    - Ask the user if questions arise before proceeding.

- [ ] <a id="3-wave-2--state-routing-and-heuristic-registration-d3-d5"></a>3. Wave 2 — State Routing & Heuristic Registration ([D3](../rfcs/041-recurring-defect-consolidation.md#d3-recovery-state-single-writer-enforcement-requirement-3), [D5](../rfcs/041-recurring-defect-consolidation.md#d5-heuristic-registry-requirement-5))

  *[RFC-041 Wave 2](../rfcs/041-recurring-defect-consolidation.md#sequencing): Route state through single writer; register heuristics (scaffolding — visibility, not removal); consolidate verdict authority. Depends on [Wave 1](#2-wave-1--garble-and-text-accessor-foundation-d1-d2).*

  - [ ] <a id="31-recovery-state-single-writer-enforcement-d3"></a>3.1 Recovery state single-writer enforcement ([D3](../rfcs/041-recurring-defect-consolidation.md#d3-recovery-state-single-writer-enforcement-requirement-3))

    - Extend `finalize_gate_and_route` (types.py :358) with `recovery_method: str | None = None`, `recovery_succeeded: bool | None = None`, `force_route: Route | None = None`, and `force_ok: bool | None = None` parameters
    - When `force_route` is provided, it takes precedence over `decide_route(first_defect)` — serves the 5 intentional override sites:
      - `:602` — RTL flat-vs-tree text comparison → `force_route=Route.FLAT`
      - `:658,:676,:694` — successful tesseract-raster recovery after VLM failure → `force_route=Route.FLAT`
      - `:738` — content-density flat-prefer heuristic → `force_route=Route.FLAT, force_ok=False`
      - `:768` — landscape-picture-detection reroute → `force_route=Route.FLAT, force_ok=False`
    - Replace all 11 direct mutations in recovery.py with `finalize_gate_and_route` calls (6 `state.route =` + 2 `state.ok =` + 3 `state.rtl_decision = None`)
    - Fix `_defect_from_reason_str` (types.py :350-355): raise `ValueError` on unrecognized reason strings instead of returning `TreeDefect.OK`
    - Add deprecation warning to legacy-tuple code path in `finalize_gate_and_route` (:378-381)
    - Add `__setattr__` guard on `ExtractionState` fields `route`, `ok`, `reason`, `first_defect`, `gate_result` — only `finalize_gate_and_route` and `from_gate_result` may write them
    - Add test: each of the 5 override sites produces same route/ok as current behavior (regression guard)
    - Add test: direct assignment to guarded fields raises `AttributeError`
    - Add test: unrecognized reason string in `_defect_from_reason_str` raises `ValueError`
    - Add CI lint test: direct `state.route =` or `state.ok =` in recovery.py fails. **CI lint exempts `types.py` `from_gate_result` (:154, :168) and `finalize_gate_and_route` (:388) as legitimate writers**
    - _Requirements:_ [RFC-041 D3](../rfcs/041-recurring-defect-consolidation.md#d3-recovery-state-single-writer-enforcement-requirement-3) | [Design Property 3](../designs/design-rfc041-recurring-defect-consolidation.md#property-3-single-writer-invariant) | [Design Service: recovery.py](../designs/design-rfc041-recurring-defect-consolidation.md#5-recoverypy) | [Design Service: types.py](../designs/design-rfc041-recurring-defect-consolidation.md#4-typesspy) | [Design Sequence: Recovery Dispatch Flow](../designs/design-rfc041-recurring-defect-consolidation.md#recovery-dispatch-flow--d3--d4)

  - [ ] <a id="32-heuristic-registry-module-d5"></a>3.2 Heuristic registry module ([D5](../rfcs/041-recurring-defect-consolidation.md#d5-heuristic-registry-requirement-5))

    - Create `src/pageindex_mcp/helpers/heuristic_registry.py`
    - Implement `HeuristicEntry` dataclass: name, rfc_origin, created, expiry, graduation_criteria
    - Implement `HeuristicRegistry` class: `register()`, `fire()`, `is_expired()`, `list_expired()`
    - Wire Prometheus counter per heuristic (fire count) and gauge for expired status
    - Add test: registered heuristic increments counter on fire
    - Add test: expired heuristic logs warning on fire
    - Add test: `list_expired()` returns only expired entries
    - **(Added: root-cause review 2026-08-31)** Add CI dead-heuristic scan: after test suite runs with `coverage.py`, check each registered heuristic's code path for >0 branch coverage. Emit CI warning for zero-coverage entries (potentially dead code). Example: garble.py:583 `"Arabic"` comparison would have been flagged
    - _Requirements:_ [RFC-041 D5](../rfcs/041-recurring-defect-consolidation.md#d5-heuristic-registry-requirement-5) | [Design Property 5](../designs/design-rfc041-recurring-defect-consolidation.md#property-5-heuristic-expiry-visibility) | [Design Service: heuristic_registry.py](../designs/design-rfc041-recurring-defect-consolidation.md#7-heuristic-registrypy) | [Design Sequence: Verdict Promotion Flow](../designs/design-rfc041-recurring-defect-consolidation.md#verdict-promotion-flow--d5)

  - [ ] <a id="33-register-existing-heuristics-d5"></a>3.3 Register existing heuristics ([D5](../rfcs/041-recurring-defect-consolidation.md#d5-heuristic-registry-requirement-5))

    - Register `source_selection` bypass (verdict.py :479) — RFC-022, expiry: 90 days from registration (default; per-heuristic override requires documented justification)
    - Register `_ARABIC_FLAT_PREFER_MULTIPLIER` (recovery.py :74) — RFC-027, expiry: 90 days from registration (default; per-heuristic override requires documented justification)
    - Register `force_verdict_override` (queries.py) — RFC-034, expiry: 90 days from registration (default; per-heuristic override requires documented justification)
    - Register each `_try_*` promotion function — respective RFC origins
    - Add test: all registered heuristics have valid RFC origin and non-null expiry
    - _Requirements:_ [RFC-041 D5](../rfcs/041-recurring-defect-consolidation.md#d5-heuristic-registry-requirement-5) | [Design Property 5](../designs/design-rfc041-recurring-defect-consolidation.md#property-5-heuristic-expiry-visibility) | [Design Service: verdict.py](../designs/design-rfc041-recurring-defect-consolidation.md#6-verdictpy)

  - [ ] <a id="34-checkpoint--wave-2"></a>3.4 Checkpoint — Wave 2

    - Run `uv run pytest` and verify all tests pass
    - Verify [Property 3](../designs/design-rfc041-recurring-defect-consolidation.md#property-3-single-writer-invariant), [Property 5](../designs/design-rfc041-recurring-defect-consolidation.md#property-5-heuristic-expiry-visibility) — new tests pass
    - Corpus diff for D3: expect 0 verdict changes (state routing, not detection)
    - Verify Prometheus counters appear for registered heuristics
    - Ask the user if questions arise before proceeding.

- [ ] <a id="4-wave-3--triad-test-oracle-d6-d7"></a>4. Wave 3 — Triad Test Oracle ([D6](../rfcs/041-recurring-defect-consolidation.md#d6-golden-file-pipeline-snapshot-tests-requirement-6), [D7](../rfcs/041-recurring-defect-consolidation.md#d7-property-based-triad-tests-requirement-6))

  *[RFC-041 Wave 3](../rfcs/041-recurring-defect-consolidation.md#sequencing): Pin triad behavior with golden files + property tests. Depends on [Wave 1](#2-wave-1--garble-and-text-accessor-foundation-d1-d2) and [Wave 2](#3-wave-2--state-routing-and-heuristic-registration-d3-d5).*

  - [ ] <a id="41-golden-file-pipeline-snapshot-tests-d6"></a>4.1 Golden-file pipeline snapshot tests ([D6](../rfcs/041-recurring-defect-consolidation.md#d6-golden-file-pipeline-snapshot-tests-requirement-6))

    - Create `tests/test_triad_golden.py`
    - Create `tests/golden_files/` directory with 8–12 archetype JSON snapshots
    - Archetype candidates: Arabic garbled, table-heavy, image-dominant, mixed-script bilingual, flat-prose enriched, scanned-image OCR, minimal-tree, near-empty
    - Each snapshot captures: garble_result, gate_result, verdict, recovery_eligibility, recovery_outcome, re_verdict
    - Any code change shifting a verdict produces a visible diff
    - Create `scripts/update_golden_files.py` for intentional snapshot regeneration
    - **Validates:** [Design Property 6](../designs/design-rfc041-recurring-defect-consolidation.md#property-6-triad-monotonicity), [Design Property 7](../designs/design-rfc041-recurring-defect-consolidation.md#property-7-triad-idempotency) | [RFC-041 D6](../rfcs/041-recurring-defect-consolidation.md#d6-golden-file-pipeline-snapshot-tests-requirement-6) | [RFC-041 Test Strategy: D6 row](../rfcs/041-recurring-defect-consolidation.md#test-strategy)
    - _Requirements:_ [RFC-041 D6](../rfcs/041-recurring-defect-consolidation.md#d6-golden-file-pipeline-snapshot-tests-requirement-6) | [Design Service: verdict.py](../designs/design-rfc041-recurring-defect-consolidation.md#6-verdictpy)

  - [ ] <a id="42-property-based-triad-tests-d7"></a>4.2 Property-based triad tests ([D7](../rfcs/041-recurring-defect-consolidation.md#d7-property-based-triad-tests-requirement-6))

    - Create `tests/test_triad_properties.py`
    - Hypothesis strategies for: `TreeGateResult`, `GarbleConfig`, `ScriptContext`, `BlobKind`
    - Property 6a: garble detection converges across all paths (per-node, per-block, whole-tree fallback) — `test_garble_convergence_across_paths`
    - Property 6b: garble detected ⇒ hard-fail or marginal, never PASS via promotion — `test_garble_never_passes` — **xfail until D5** (`source_selection` bypass at `verdict.py:479` currently grants unconditional PASS) **(split from Property 6: review v2 2026-09-01)**
    - Property: `_keep_best_wins` never reverts objectively better retries
    - Property: no-op recovery preserves PASS
    - CI configuration: `max_examples=200`
    - Nightly configuration: `max_examples=10000`
    - **Validates:** [Design Property 6](../designs/design-rfc041-recurring-defect-consolidation.md#property-6-triad-monotonicity), [Design Property 7](../designs/design-rfc041-recurring-defect-consolidation.md#property-7-triad-idempotency) | [RFC-041 D7](../rfcs/041-recurring-defect-consolidation.md#d7-property-based-triad-tests-requirement-6) | [RFC-041 Test Strategy: D7 row](../rfcs/041-recurring-defect-consolidation.md#test-strategy)
    - _Requirements:_ [RFC-041 D7](../rfcs/041-recurring-defect-consolidation.md#d7-property-based-triad-tests-requirement-6) | [Design Service: recovery.py](../designs/design-rfc041-recurring-defect-consolidation.md#5-recoverypy) | [Design Service: verdict.py](../designs/design-rfc041-recurring-defect-consolidation.md#6-verdictpy)

  - [ ] <a id="43-checkpoint--wave-3"></a>4.3 Checkpoint — Wave 3

    - Run `uv run pytest` and verify all tests pass including golden-file and property-based tests
    - Verify [Property 6](../designs/design-rfc041-recurring-defect-consolidation.md#property-6-triad-monotonicity), [Property 7](../designs/design-rfc041-recurring-defect-consolidation.md#property-7-triad-idempotency) — property tests pass with `max_examples=200`
    - Verify golden-file snapshots capture post-consolidation state
    - Ask the user if questions arise before proceeding.

- [ ] <a id="5-wave-4--rfc-lifecycle-and-triage-d8-d9"></a>5. Wave 4 — RFC Lifecycle & Triage ([D8](../rfcs/041-recurring-defect-consolidation.md#d8-rfc-lifecycle-ci-gate-requirement-7), [D9](../rfcs/041-recurring-defect-consolidation.md#d9-rfc-gap-triage-requirement-7))

  *[RFC-041 Wave 4](../rfcs/041-recurring-defect-consolidation.md#sequencing): CI gate + triage. Can run in parallel with [Wave 3](#4-wave-3--triad-test-oracle-d6-d7).*

  - [ ] <a id="51-rfc-lifecycle-ci-gate-d8"></a>5.1 RFC lifecycle CI gate ([D8](../rfcs/041-recurring-defect-consolidation.md#d8-rfc-lifecycle-ci-gate-requirement-7))

    - Create `.github/workflows/rfc-lifecycle-lint.yml`
    - Create supporting script (`scripts/rfc_lifecycle_lint.py`)
    - Parse `agents/rfcs/*.md` frontmatter for status
    - Parse `agents/tasks/*.md` for checked/unchecked items, GATE markers
    - Detect: later-phase checked + earlier GATE unchecked (merge-blocking)
    - Detect: all-tasks-done drafts (advisory warning)
    - Detect: unresolved Open Questions (advisory warning)
    - **(Added: root-cause review 2026-08-31)** Create zone-ownership manifest `audit/zones/ZONE_OWNERSHIP.yaml` — bootstrap from `audit/zones/_index.md`, mapping each zone to its owning RFC deliverable(s). **(Amendment 2026-09-01):** Define the YAML schema in the design doc. Prefer machine-generation from `_index.md` + RFC frontmatter over hand-maintenance to avoid bootstrap-maintenance coupling (NG-5 from review v2).
    - **(Added)** Detect: zone with unresolved bugs whose owning RFC is closed and no successor RFC deliverable owns them (merge-blocking) — catches the RFC-040 scope-narrowing pattern
    - **(Added)** Each RFC checkpoint task must update `ZONE_OWNERSHIP.yaml` to reflect transferred/resolved zone ownership
    - Add test: tasks file with skipped GATE — lint reports failure
    - Add test: draft with all tasks checked — lint reports warning
    - **(Added)** Add test: closed RFC with orphaned zone bugs — lint reports failure
    - **(Added)** Add test: zone transferred to successor RFC — lint reports clean
    - **Validates:** [Design Property 8](../designs/design-rfc041-recurring-defect-consolidation.md#property-8-rfc-lifecycle-gate-soundness) | [RFC-041 D8](../rfcs/041-recurring-defect-consolidation.md#d8-rfc-lifecycle-ci-gate-requirement-7) | [RFC-041 Test Strategy: D8 row](../rfcs/041-recurring-defect-consolidation.md#test-strategy)
    - _Requirements:_ [RFC-041 D8](../rfcs/041-recurring-defect-consolidation.md#d8-rfc-lifecycle-ci-gate-requirement-7) | [Design Service: rfc-lifecycle-lint.yml](../designs/design-rfc041-recurring-defect-consolidation.md#8-github-workflows-rfc-lifecycle-lintyml-new)

  - [ ] <a id="52-rfc-gap-triage-d9"></a>5.2 RFC gap triage ([D9](../rfcs/041-recurring-defect-consolidation.md#d9-rfc-gap-triage-requirement-7))

    - Create GitHub issue: RFC-037 Release B corpus validation skipped
    - Create GitHub issue: RFC-033 D2 Part B bidi enforcement — gated on unexecuted re-ingest; `bidi_coherence_enforce` zero consumers
    - Create GitHub issue: RFC-040 Open Questions 1–2 (flat_prose exception, bilingual recovery)
    - Create GitHub issue: RFC-033 Out of Scope items 7–10b — five deferred defects
    - Each issue: force decision — implement, defer-with-date, or wont-fix
    - _Requirements:_ [RFC-041 D9](../rfcs/041-recurring-defect-consolidation.md#d9-rfc-gap-triage-requirement-7) | [RFC-041 Risk 5](../rfcs/041-recurring-defect-consolidation.md#risks)

  - [ ] <a id="53-checkpoint--wave-4"></a>5.3 Checkpoint — Wave 4

    - Verify all 4 GitHub issues created and labeled
    - Verify RFC lifecycle CI gate passes on current branch
    - Verify [Property 8](../designs/design-rfc041-recurring-defect-consolidation.md#property-8-rfc-lifecycle-gate-soundness) — lint test passes
    - Ask the user if questions arise before proceeding.

- [ ] <a id="35a-rfc037-release-b-validation"></a>3.5a. RFC-037 Release B Validation (D11 pre-gate) **(Added: root-cause review 2026-08-31)**

  **HARD GATE: D11 (Task 3.5) MUST NOT start until this task is complete.**

  D11 concentrates all 5 verdict write paths onto the RFC-037 D5 Postgres `_UPSERT_SQL` max-priority-wins CAS arbiter (storage/verdict.py:97-99). Release B was supposed to validate this arbiter via corpus diff but was skipped while Release C executed. Without validation, D11 turns a 5-writer distributed problem into a single-point-of-failure problem on an unproven arbiter.

    - Run full corpus re-ingestion and compare Postgres registry verdicts vs MinIO `.meta.json` sidecar verdicts for all documents
    - Verify: zero verdict downgrades (Postgres max-priority-wins CAS never stores a lower-priority verdict than what exists)
    - Verify: Postgres and MinIO agree on final verdict for all documents (sidecar backfill is consistent)
    - Verify: concurrent writes (simulated via parallel ingestion of same doc) resolve to highest-priority verdict
    - If validation fails: file bug against RFC-037 D5 and activate D11 contingency plan **(review v2 2026-09-01)**:
      - D11 proceeds with **dual-write with soft CAS**: `write_verdict` writes to both MinIO (authoritative) and Postgres (advisory, best-effort CAS)
      - Reconcile sweep (`_drain_verdict_retry_queue`) treats Postgres as non-authoritative until CAS guard is separately validated
      - This unblocks D11's routing consolidation while deferring Postgres-as-authority to a post-fix Release B re-validation
    - _Requirements:_ RFC-037 Release B | RFC-041 D11 dependency

- [ ] <a id="35-verdict-authority-consolidation-d11"></a>3.5. Verdict Authority Consolidation (D11)

  **Prerequisite:** Task 3.5a (RFC-037 Release B validation) must be complete with zero verdict downgrades. D11 enshrines the CAS guard as the canonical write path — it must be validated before consolidation.

  **Files:** `storage/verdict.py`, `worker/registry_mirror.py`, `registry/queries.py`, `registry_backfill/reconcile.py`, `promotion_sweep.py`, `preprocess_client.py`

    - Route `write_verdict` (storage/verdict.py:201) through `_upsert_registry_row` instead of calling `save_doc_meta` directly (MinIO-only write → Postgres-first + sidecar backfill)
    - **CORRECTED (root-cause review 2026-08-31):** `promotion_sweep.run_sweep` (promotion_sweep.py:35) and `preprocess_client.recompute_verdicts` (preprocess_client.py:232) are already `async def` functions that `await` other calls in scope — calling `asyncio.run()` inside them would crash with `RuntimeError: asyncio.run() cannot be called from a running event loop`. These callers SHALL `await _upsert_registry_row(...)` directly. Only provide a sync wrapper if a genuinely synchronous caller is discovered during implementation
    - Verify `_drain_verdict_retry_queue` (reconcile.py:34) uses `upsert_doc` with the same CAS guard as the primary path — no special-case SQL or priority downgrades
    - Register `force_verdict_override` with `HeuristicRegistry` (D5): RFC origin = RFC-037, expiry = 90 days post-D11
    - Add CI grep guard: flag direct `save_doc_meta` calls with verdict keys outside `_upsert_registry_row` and `write_verdict`
    - Test: `write_verdict("doc_x", ...)` → verify both Postgres row and MinIO sidecar updated
    - Test: write via `_drain_verdict_retry_queue` → verify same CAS guard applies (winning row matches primary path)
    - Test: `force_verdict_override` increments HeuristicRegistry counter and logs warning if past expiry
    - Verify [Property 11](../designs/design-rfc041-recurring-defect-consolidation.md#property-11-verdict-authority-single-path) — no MinIO-only verdict write when registry is enabled
    - Run corpus diff: 0 expected verdict changes (routing change only)
    - Ask the user if questions arise before proceeding.

- [ ] <a id="6-final-checkpoint"></a>6. Final Checkpoint

  - Run `uv run pytest` — full test suite including golden-file and property-based tests
  - Verify all 11 correctness properties ([Properties 1–11](../designs/design-rfc041-recurring-defect-consolidation.md#correctness-properties)) have passing tests
  - Run 3 consecutive test runs — verify zero flaky failures
  - Verify CI lints block: `_garble_prongs` calls, `block['text']` access, `state.route =` assignments, `save_doc_meta` verdict writes, skipped GATE tasks
  - Verify Prometheus heuristic counters and expired gauges are active
  - Corpus re-ingest with full scoring against golden-file archetypes
  - Ask the user if questions arise before proceeding.

## Notes

- [RFC-041 D10](../rfcs/041-recurring-defect-consolidation.md#d10-dead-code-and-accessor-parity-fixes-requirement-8): D10a (`'Arabic'` → `'Arab'`) activates dead garble logic for Arabic-script text. Verify with Arabic corpus documents (MOU MOHRE, القرار التنظيمي) — may change 0–1 verdicts.
- [RFC-041 D2](../rfcs/041-recurring-defect-consolidation.md#d2-unified-block-text-accessor-requirement-2): `block_text` unification touches flat.py and tree_validation.py. Grep ALL call sites of `_flat_block_primary_text`, `_flat_search_text`, `_node_text_parts` before merge.
- [RFC-041 D3](../rfcs/041-recurring-defect-consolidation.md#d3-recovery-state-single-writer-enforcement-requirement-3): `finalize_gate_and_route` extension uses backwards-compatible default parameters — existing callers unaffected.
- [RFC-041 D5](../rfcs/041-recurring-defect-consolidation.md#d5-heuristic-registry-requirement-5): Heuristic expiry dates SHALL be concrete (90-day default) per [RFC-041 D5 acceptance criterion 4](../rfcs/041-recurring-defect-consolidation.md#d5-heuristic-registry-requirement-5). Per-heuristic override requires documented justification.
- **Zone 2 ownership:** Must be resolved (new successor RFC or RFC-041 amendment) by Wave 1 checkpoint — see [RFC-041 Risk 8](../rfcs/041-recurring-defect-consolidation.md#risks).
- **RFC-037 Release B:** Must be validated (or equivalent corpus-diff verifying zero verdict downgrades) before D11 consolidation — see [RFC-041 Risk 9](../rfcs/041-recurring-defect-consolidation.md#risks).
- **Effort estimate:** ~20 days, range 17–24 (revised from 15; review v2 2026-09-01). D3 `__setattr__` guard requires test migration (~dozens of direct assignments); D10c has 10 call sites not 7 (80–120 lines across 5 files); D11 gated on Release B with dual-write contingency. Zone 5 verification task added.
- [RFC-041 Risk 1](../rfcs/041-recurring-defect-consolidation.md#risks): D2 accessor unification may surface hidden consumers depending on divergent behavior. Mitigation: corpus diff + call-site grep.
- [RFC-041 Risk 4](../rfcs/041-recurring-defect-consolidation.md#risks): Golden-file brittleness — intentional threshold changes require `scripts/update_golden_files.py` + review.
- [RFC-041 Risk 5](../rfcs/041-recurring-defect-consolidation.md#risks): D8 retroactive enforcement will flag existing RFCs. D9 triage clears backlog before gate becomes merge-blocking.
- [RFC-041 Open Question 1](../rfcs/041-recurring-defect-consolidation.md#open-questions): HeuristicRegistry scope — resolve during [Task 3.2](#32-heuristic-registry-module-d5).
- [RFC-041 Open Question 2](../rfcs/041-recurring-defect-consolidation.md#open-questions): Golden-file archetype selection — resolve during [Task 4.1](#41-golden-file-pipeline-snapshot-tests-d6).

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "tasks": ["1.3", "1.4", "1.6"],
      "description": "Immediate quick wins — D4 + Zone 5 verification"
    },
    {
      "id": 1,
      "tasks": ["2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7"],
      "depends_on": [0],
      "description": "Garble + text accessor foundation + dead code fixes + Zone 2 PF remediation — D1, D2, D10a/b/c"
    },
    {
      "id": 2,
      "tasks": ["3.1", "3.2", "3.3", "3.5a", "3.5"],
      "depends_on": [1],
      "description": "State routing + heuristic registry (scaffolding) + Release B validation + verdict authority — D3, D5, D11. Note: 3.5 hard-gated on 3.5a"
    },
    {
      "id": 3,
      "tasks": ["4.1", "4.2"],
      "depends_on": [1, 2],
      "description": "Triad test oracle — D6, D7"
    },
    {
      "id": 4,
      "tasks": ["5.1", "5.2"],
      "depends_on": [],
      "description": "RFC lifecycle — D8, D9 (can run parallel with wave 3)"
    },
    {
      "id": 5,
      "tasks": ["6"],
      "depends_on": [3, 4],
      "description": "Final checkpoint"
    }
  ]
}
```
