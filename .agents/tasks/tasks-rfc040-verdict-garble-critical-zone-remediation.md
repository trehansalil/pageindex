---
id: "tasks-rfc040-verdict-garble-critical-zone-remediation"
title: "Tasks: Verdict Gate & Garble Detection Critical Zone Remediation"
type: tasks
status: draft
date: "2026-08-27"
tags:
  - tasks
  - verdict
  - garble
  - wave-4
  - corpus-quality
aliases:
  - "tasks-rfc040-verdict-garble-critical-zone-remediation"
governs:
  - "[[RFC-040]]"
---

# Implementation Plan: Verdict Gate & Garble Detection Critical Zone Remediation

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [RFC-040](../rfcs/040-verdict-garble-critical-zone-remediation.md) |
| Design Document | [Design: RFC-040](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md) |
| Corpus Audit (source) | `audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-27_POST-RUN20.md` |
| Implementation Order | [RFC-040 §Sequencing](../rfcs/040-verdict-garble-critical-zone-remediation.md#sequencing) |
| Test Strategy | [RFC-040 §Test Strategy](../rfcs/040-verdict-garble-critical-zone-remediation.md#test-strategy) |
| Correctness Properties | [Design §Correctness Properties](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#correctness-properties) |
| Hard Rules | CLAUDE.md HR5 (no silent low-quality tree persistence) |

## Overview

This plan implements 6 deliverables ([D1](../rfcs/040-verdict-garble-critical-zone-remediation.md#d1-unconditional-structural-hard-fail-zone-1)–[D6](../rfcs/040-verdict-garble-critical-zone-remediation.md#d6-nfkc-before-bidi-reordering-zone-2)) targeting the two CRITICAL architectural defect zones surviving wave 1–3 remediation. Implementation proceeds in three batches ordered by risk: zero-risk refactors first ([D3](../rfcs/040-verdict-garble-critical-zone-remediation.md#d3-garble-detection-deduplication-zone-2), [D6](../rfcs/040-verdict-garble-critical-zone-remediation.md#d6-nfkc-before-bidi-reordering-zone-2)), detection fixes second ([D4](../rfcs/040-verdict-garble-critical-zone-remediation.md#d4-gate_table-reason-ordering-fix-zone-2), [D5](../rfcs/040-verdict-garble-critical-zone-remediation.md#d5-tessdata-latin-substitution-closure-zone-2)), verdict restructuring last ([D1](../rfcs/040-verdict-garble-critical-zone-remediation.md#d1-unconditional-structural-hard-fail-zone-1), [D2](../rfcs/040-verdict-garble-critical-zone-remediation.md#d2-ordered-promotion-pipeline-zone-1)). Each batch validates 2 of the 6 [correctness properties](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#correctness-properties) before proceeding.

## Tasks

- [x] <a id="1-batch-0-zero-risk-refactors-d3-d6"></a>1. Batch 0 — Zero-Risk Refactors ([D3](../rfcs/040-verdict-garble-critical-zone-remediation.md#d3-garble-detection-deduplication-zone-2), [D6](../rfcs/040-verdict-garble-critical-zone-remediation.md#d6-nfkc-before-bidi-reordering-zone-2))

  *[RFC-040 §Sequencing](../rfcs/040-verdict-garble-critical-zone-remediation.md#sequencing): D3 and D6 are behavior-preserving refactors with no corpus impact — land first to establish a clean base.*

  - [x] <a id="11-remove-duplicate-digit-floor-d3"></a>1.1 Remove duplicate digit-ratio floor guard ([D3](../rfcs/040-verdict-garble-critical-zone-remediation.md#d3-garble-detection-deduplication-zone-2))

    - In `_garble_check_nodes` (garble.py:696–698), remove the `if len(_concat) >= config.garble_digit_floor:` guard wrapping the `garble_prongs` call in the whole-tree fallback
    - Let `garble_prongs` apply its own floor internally (garble.py:380: `if len(norm) > cfg.garble_digit_floor`)
    - Verify that `_garble_check_flat_blocks` has no duplicate floor (it delegates to `detect_garble` → `garble_prongs` without an outer guard — confirm, do not change)
    - Add test `test_fallback_delegates_floor_to_garble_prongs`: document below `garble_digit_floor` in aggregate → fallback calls `garble_prongs` which skips digit-ratio prong → no false positive
    - _Requirements:_ [RFC-040 D3](../rfcs/040-verdict-garble-critical-zone-remediation.md#d3-garble-detection-deduplication-zone-2) | [Design Property 3](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#property-3-single-garble-floor) | [Design Service: Garble Detection](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#2-garble-detection-garblepy) | [Design Sequence: Garble Detection Flow](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#garble-detection-flow--d3-d4-d5-d6)

  - [x] <a id="12-reorder-nfkc-bidi-d6"></a>1.2 Reorder NFKC / bidi presentation-forms check ([D6](../rfcs/040-verdict-garble-critical-zone-remediation.md#d6-nfkc-before-bidi-reordering-zone-2))

    - In `_pre_inference_normalize` (normalize.py:129–161), move the `had_presentation_forms` computation (Arabic Presentation-Forms ratio > 50% of Arabic-range chars) to BEFORE the NFKC folding step
    - NFKC normalization continues to run afterward — downstream consumers are unaffected
    - Verify that `_gate_bidi_degraded` (gates.py:126–157) receives the pre-NFKC `had_presentation_forms` value via `ScriptContext`
    - Add test `test_presentation_forms_detected_before_nfkc`: text with U+FB50 codepoints → `had_presentation_forms=True`; same text after NFKC → codepoints absent, confirming detection must run first
    - _Requirements:_ [RFC-040 D6](../rfcs/040-verdict-garble-critical-zone-remediation.md#d6-nfkc-before-bidi-reordering-zone-2) | [Design Property 6](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#property-6-bidi-signal-preserved) | [Design Service: Normalization](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#5-normalization-normalizepy) | [Design Sequence: Garble Detection Flow](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#garble-detection-flow--d3-d4-d5-d6)

  - [x] <a id="13-checkpoint-batch-0"></a>1.3 Checkpoint — Batch 0

    - Run `uv run pytest` and verify all existing tests pass (zero regressions — these are behavior-preserving changes)
    - Verify [Property 3](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#property-3-single-garble-floor) and [Property 6](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#property-6-bidi-signal-preserved) tests pass
    - No corpus diff needed — these changes do not affect verdict distribution
    - Ask the user if questions arise before proceeding to [Batch 1](#2-batch-1-detection-fixes-d4-d5)

- [x] <a id="2-batch-1-detection-fixes-d4-d5"></a>2. Batch 1 — Detection Fixes ([D4](../rfcs/040-verdict-garble-critical-zone-remediation.md#d4-gate_table-reason-ordering-fix-zone-2), [D5](../rfcs/040-verdict-garble-critical-zone-remediation.md#d5-tessdata-latin-substitution-closure-zone-2))

  *[RFC-040 §Sequencing](../rfcs/040-verdict-garble-critical-zone-remediation.md#sequencing): D4 and D5 change corpus verdicts — more docs correctly flagged as garbled, more docs error on missing tessdata. Corpus diff required before merge.*

  - [x] <a id="21-fix-gate-table-reason-ordering-d4"></a>2.1 Fix GATE_TABLE reason-ordering ([D4](../rfcs/040-verdict-garble-critical-zone-remediation.md#d4-gate_table-reason-ordering-fix-zone-2))

    - In `tree_validation.py`, after GATE_TABLE iteration completes and a reason is selected, add a short-circuit: if `garbling` or `node_garbling` is in the set of fired defects AND the currently selected reason is something else (e.g. `node_count_low`), override the reason with `garbling`
    - This matches GATE_TABLE's already-intended severity=0 priority for garbling — the fix ensures reason-assignment respects that priority
    - Verify that OCR recovery dispatcher (`reason in ('garbling', 'node_garbling')` check in indexer.py) can now reach garbled minimal-tree documents
    - Add test `test_garble_reason_wins_over_node_count_low`: tree with 2 nodes (fires `node_count_low`) + garbled text (fires `garbling`) → reason=`garbling`, NOT `node_count_low`
    - _Requirements:_ [RFC-040 D4](../rfcs/040-verdict-garble-critical-zone-remediation.md#d4-gate_table-reason-ordering-fix-zone-2) | [Design Property 4](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#property-4-garble-reason-priority) | [Design Service: Tree Validation](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#3-tree-validation-tree_validationpy) | [Design Sequence: Garble Detection Flow](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#garble-detection-flow--d3-d4-d5-d6)

  - [x] <a id="22-close-tessdata-latin-substitution-d5"></a>2.2 Close tessdata Latin substitution hole ([D5](../rfcs/040-verdict-garble-critical-zone-remediation.md#d5-tessdata-latin-substitution-closure-zone-2))

    - In `ensure_tessdata` (ocr_langs.py:92–196), after resolving available languages, add a check: if the original request included non-Latin languages AND ALL non-Latin languages were dropped AND only Latin languages (`deu`, `eng`, etc.) remain, raise `TessdataUnavailableError`
    - This does NOT change behavior for purely Latin requests (e.g. German-only docs requesting `['deu']`) — the check only fires when non-Latin languages were explicitly requested and then silently dropped
    - Add test `test_tessdata_raises_on_latin_only_substitution`: request `['ara', 'eng']`, only `eng` tessdata available → `TessdataUnavailableError` raised
    - Add test `test_tessdata_allows_pure_latin_request`: request `['deu', 'eng']`, both available → no error
    - _Requirements:_ [RFC-040 D5](../rfcs/040-verdict-garble-critical-zone-remediation.md#d5-tessdata-latin-substitution-closure-zone-2) | [Design Property 5](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#property-5-tessdata-no-silent-substitution) | [Design Service: OCR Languages](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#4-ocr-languages-ocr_langspy) | [Design Sequence: Garble Detection Flow](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#garble-detection-flow--d3-d4-d5-d6)

  - [x] <a id="23-corpus-diff-batch-1"></a>2.3 Corpus diff — Batch 1

    - Run `make ingest` against the full 25-doc corpus with D4+D5 applied
    - Diff verdicts against Run-20 baseline (`audit/CORPUS_REINGESTION_AUDIT_RUN-20.md`)
    - Expected changes per [RFC-040 §Corpus Impact Forecast](../rfcs/040-verdict-garble-critical-zone-remediation.md#corpus-impact-forecast):
      - D4: 1–3 docs enter OCR retry that previously did not (reason was `node_count_low`, now `garbling`)
      - D5: 0–1 additional ERROR for docs relying on silent Latin tessdata substitution
    - Flag any UNEXPECTED verdict changes for review before merge

  - [x] <a id="24-checkpoint-batch-1"></a>2.4 Checkpoint — Batch 1

    - Run `uv run pytest` and verify all tests pass
    - Verify [Property 4](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#property-4-garble-reason-priority) and [Property 5](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#property-5-tessdata-no-silent-substitution) tests pass
    - Review corpus diff from [Task 2.3](#23-corpus-diff-batch-1) — confirm all verdict changes are expected
    - Ask the user if questions arise before proceeding to [Batch 2](#3-batch-2-verdict-restructure-d1-d2)

- [x] <a id="3-batch-2-verdict-restructure-d1-d2"></a>3. Batch 2 — Verdict Restructure ([D1](../rfcs/040-verdict-garble-critical-zone-remediation.md#d1-unconditional-structural-hard-fail-zone-1), [D2](../rfcs/040-verdict-garble-critical-zone-remediation.md#d2-ordered-promotion-pipeline-zone-1))

  *[RFC-040 §Sequencing](../rfcs/040-verdict-garble-critical-zone-remediation.md#sequencing): D1 changes verdict distribution; D2 is the structural cleanup of D1's behavioral change. Land together. Fixture regeneration in same PR.*

  - [x] <a id="31-unconditional-hard-fail-d1"></a>3.1 Implement unconditional structural hard-fail ([D1](../rfcs/040-verdict-garble-critical-zone-remediation.md#d1-unconditional-structural-hard-fail-zone-1))

    - In `apply_promotions` (verdict.py:402–513):
      1. Delete the `_has_image_rescue` variable and its conditional block (lines 461–471)
      2. Move the hard-fail check (`sig.max_leaf_ratio > th.hard_fail_max_leaf_ratio`) to immediately after the `image_standalone` early-return, BEFORE candidate collection
      3. Add an explicit named exception for image-enrichment: the hard-fail is bypassed ONLY when ALL of these hold:
         - `content_class in ("flat_prose", "flat_mixed")`
         - `image_enrichment_ratio >= 0.8`
         - `total_chars >= th.min_image_promoted_chars`
         - `sig.node_count >= 3`
         - `not sig.effectively_garbled`
         - `detect_garble` returns false on promoted text
    - In `_try_image_enrichment` (verdict.py:220–265), add `sig.node_count >= 3` and `not sig.effectively_garbled` guards
    - Add tests:
      - `test_hard_fail_unconditional`: doc with `max_leaf_ratio=1.0`, no image enrichment → FAIL (not MARGINAL)
      - `test_image_enrichment_exception_requires_all_guards`: doc with image enrichment but `node_count=1` → FAIL
      - `test_image_enrichment_exception_with_garble`: doc with image enrichment but garbled → FAIL
      - `test_image_enrichment_legitimate_exception`: `flat_prose`, ratio=0.9, 5000 chars, 5 nodes, not garbled → PASS
    - _Requirements:_ [RFC-040 D1](../rfcs/040-verdict-garble-critical-zone-remediation.md#d1-unconditional-structural-hard-fail-zone-1) | [Design Property 1](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#property-1-unconditional-hard-fail) | [Design Service: Verdict Engine](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#1-verdict-engine-verdictpy) | [Design Sequence: Verdict Computation Flow](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#verdict-computation-flow--d1-d2)

  - [x] <a id="32-ordered-promotion-pipeline-d2"></a>3.2 Replace candidate collection with ordered pipeline ([D2](../rfcs/040-verdict-garble-critical-zone-remediation.md#d2-ordered-promotion-pipeline-zone-1))

    - In `apply_promotions` (verdict.py:450–510):
      1. Replace the `candidates: list[PromotionCandidate] = []` / `_try_*` / `max(candidates, key=priority)` pattern with an ordered `if/elif` chain:
         ```
         1. if _try_image_enrichment → return (only within hard-fail exception)
         2. elif _try_structural_pass → return
         3. elif _try_ocr_promotion → return
         4. elif _try_flat_promotion → return
         5. elif _try_content_class_promotion → return
         6. elif _try_small_doc_promotion → return
         7. else → MARGINAL fallback
         ```
      2. Each `_try_*` function returns `VerdictResult | None` instead of `PromotionCandidate | None`
      3. Delete `PromotionCandidate` dataclass and its `priority` field from `types.py`
      4. Update all `_try_*` function signatures and return types
    - Add test `test_promotion_order_first_match_wins`: doc eligible for both structural-pass and flat-promotion → structural-pass wins (it comes first in the chain)
    - _Requirements:_ [RFC-040 D2](../rfcs/040-verdict-garble-critical-zone-remediation.md#d2-ordered-promotion-pipeline-zone-1) | [Design Property 2](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#property-2-ordered-promotion) | [Design Service: Verdict Engine](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#1-verdict-engine-verdictpy) | [Design Sequence: Verdict Computation Flow](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#verdict-computation-flow--d1-d2)

  - [x] <a id="33-fixture-regeneration"></a>3.3 Regenerate test fixtures

    - Identify all test fixtures in `tests/` that reference `PromotionCandidate`, `priority`, or assert specific `_has_image_rescue` behavior
    - Regenerate fixtures against the new verdict boundaries from D1+D2
    - Verify no test references stale threshold values (PASS_MAX_LEAF_RATIO=0.17 vs 0.30 history)
    - This MUST be in the same PR as D1+D2 to prevent calibration drift

  - [x] <a id="34-corpus-diff-batch-2"></a>3.4 Corpus diff — Batch 2

    - Run `make ingest` against the full 25-doc corpus with D1+D2 applied (on top of D3–D6 from Batches 0–1)
    - Diff verdicts against the post-Batch-1 baseline
    - Expected changes per [RFC-040 §Corpus Impact Forecast](../rfcs/040-verdict-garble-critical-zone-remediation.md#corpus-impact-forecast):
      - D1: 0–2 PASS→FAIL for docs currently rescued by image-enrichment bypass with inadequate content volume
    - Flag any UNEXPECTED verdict changes for review before merge

  - [x] <a id="35-checkpoint-batch-2"></a>3.5 Checkpoint — Batch 2 (Final)

    - Run `uv run pytest` and verify all tests pass (including regenerated fixtures)
    - Verify ALL 6 correctness properties pass:
      - [Property 1](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#property-1-unconditional-hard-fail) (D1)
      - [Property 2](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#property-2-ordered-promotion) (D2)
      - [Property 3](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#property-3-single-garble-floor) (D3)
      - [Property 4](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#property-4-garble-reason-priority) (D4)
      - [Property 5](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#property-5-tessdata-no-silent-substitution) (D5)
      - [Property 6](../designs/design-rfc040-verdict-garble-critical-zone-remediation.md#property-6-bidi-signal-preserved) (D6)
    - Review corpus diff from [Task 3.4](#34-corpus-diff-batch-2) — confirm all verdict changes are expected
    - Ask the user if questions arise before committing

## Notes

- [RFC-040 D1](../rfcs/040-verdict-garble-critical-zone-remediation.md#d1-unconditional-structural-hard-fail-zone-1): The `_has_image_rescue` bypass was introduced by RFC-022 B2 to handle flat image-enriched documents where `max_leaf_ratio=1.0` is structurally expected. The replacement (explicit exception with additional guards) preserves this legitimate use case while closing the bypass for near-empty/garbled docs.
- [RFC-040 D2](../rfcs/040-verdict-garble-critical-zone-remediation.md#d2-ordered-promotion-pipeline-zone-1): The `PromotionCandidate` dataclass deletion is a ~60-line net reduction. Any downstream code importing `PromotionCandidate` from `types.py` must be updated (search all imports).
- [RFC-040 D4](../rfcs/040-verdict-garble-critical-zone-remediation.md#d4-gate_table-reason-ordering-fix-zone-2): More documents entering OCR retry may increase arq worker load. Monitor `RECOVERY_TOTAL` metrics after deployment.
- [RFC-040 D5](../rfcs/040-verdict-garble-critical-zone-remediation.md#d5-tessdata-latin-substitution-closure-zone-2): This will cause bilingual Arabic/Latin documents to ERROR if Arabic tessdata is unavailable. [RFC-040 §Open Questions #2](../rfcs/040-verdict-garble-critical-zone-remediation.md#open-questions) asks whether a distinct English-only degradation path should be added — decide before implementing D5.
- [RFC-040 Risk](../rfcs/040-verdict-garble-critical-zone-remediation.md#effort-estimate): D1+D2 carry medium migration risk due to verdict distribution changes. The corpus diff in [Task 3.4](#34-corpus-diff-batch-2) is the primary risk mitigation gate.

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "name": "Batch 0 — Zero-Risk Refactors",
      "tasks": ["1.1", "1.2"],
      "depends_on": []
    },
    {
      "id": 1,
      "name": "Batch 0 Checkpoint",
      "tasks": ["1.3"],
      "depends_on": ["1.1", "1.2"]
    },
    {
      "id": 2,
      "name": "Batch 1 — Detection Fixes",
      "tasks": ["2.1", "2.2"],
      "depends_on": ["1.3"]
    },
    {
      "id": 3,
      "name": "Batch 1 Corpus Diff + Checkpoint",
      "tasks": ["2.3", "2.4"],
      "depends_on": ["2.1", "2.2"]
    },
    {
      "id": 4,
      "name": "Batch 2 — Verdict Restructure",
      "tasks": ["3.1", "3.2", "3.3"],
      "depends_on": ["2.4"]
    },
    {
      "id": 5,
      "name": "Batch 2 Corpus Diff + Final Checkpoint",
      "tasks": ["3.4", "3.5"],
      "depends_on": ["3.1", "3.2", "3.3"]
    }
  ]
}
```
