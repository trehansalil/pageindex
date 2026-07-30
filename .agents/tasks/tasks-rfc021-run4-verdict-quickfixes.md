<!-- Space: CITRA -->
<!-- Title: Tasks: RFC-021 Run 4 Verdict Quick-Fixes -->
<!-- Folder: Tasks -->

# Implementation Plan: RFC-021 Run 4 Verdict Quick-Fixes — Threshold Tuning, Garble Gate Precision, OCR Deferral

## Traceability

| Artifact               | Reference                                                                                                                                                        |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Governing RFC          | [RFC-021: Run 4 Verdict Quick-Fixes](../rfcs/021-run4-verdict-quickfixes.md)                                                                                    |
| Design Document        | [Design: RFC-021 Run 4 Verdict Quick-Fixes](../designs/design-rfc021-run4-verdict-quickfixes.md)                                                                |
| PRD / Requirements     | `PRD.md`                                                                                                                                                         |
| Hard Rules             | [CLAUDE.md HR5](../../CLAUDE.md) (no silent low-quality tree)                                                                                                    |
| Implementation Order   | [RFC-021 §Implementation Plan](../rfcs/021-run4-verdict-quickfixes.md#implementation-plan)                                                                       |
| Test Plan              | [RFC-021 §Test Plan](../rfcs/021-run4-verdict-quickfixes.md#test-plan)                                                                                           |
| Correctness Properties | [Design §Correctness Properties](../designs/design-rfc021-run4-verdict-quickfixes.md#correctness-properties)                                                     |
| Rollback Strategy      | [RFC-021 §Rollback Strategy](../rfcs/021-run4-verdict-quickfixes.md#rollback-strategy)                                                                           |
| Predecessors           | RFC-017 (P0a/P0b) → RFC-018 (D0-D3) → RFC-019 (D0-D4) → RFC-020 (F0-F5) → this RFC-021                                                                         |

## Overview

This plan implements four quick-fix categories ([QF1](../rfcs/021-run4-verdict-quickfixes.md#qf1-f2d2-forced-ocr-regression-docs-7-20-21)–[QF4](../rfcs/021-run4-verdict-quickfixes.md#qf4-verdict_reason-input-probe-not-output-quality-docs-20-21)) identified by Run 4 corpus reaudit (13 PASS / 9 MARGINAL / 2 FAIL / 1 ERROR), proceeding through the [RFC-021 implementation phases](../rfcs/021-run4-verdict-quickfixes.md#implementation-plan) and validating against the nine [correctness properties](../designs/design-rfc021-run4-verdict-quickfixes.md#correctness-properties) in the design document. Total effort: **~4.0 person-days** across 6 phases on branch `feat/run4-verdict-quickfixes` (Phase 3: 0.5-0.75d diagnosis-first; Phase 6: ~1.25d with `_IMAGE_EXTS` reconciliation). [QF1](../rfcs/021-run4-verdict-quickfixes.md#qf1-fix-defer-ocr-escalation-from-pre-garble-probe-to-fix-3-retry-path) is the critical-path item and lands first; [QF2a-LT](../rfcs/021-run4-verdict-quickfixes.md#qf2a-lt-dedicated-image-file-pipeline-long-term-follow-up) (Phase 6) ships independently as a long-term follow-up. Target: [19-20 PASS on Run 5](../rfcs/021-run4-verdict-quickfixes.md#expected-outcomes).

## Tasks

- [x] <a id="1-phase-1--qf1-ocr-deferral"></a>1. Phase 1 — [QF1](../rfcs/021-run4-verdict-quickfixes.md#qf1-fix-defer-ocr-escalation-from-pre-garble-probe-to-fix-3-retry-path) OCR Deferral (0.75 d)

  *[RFC-021 §Implementation Plan — Phase 1](../rfcs/021-run4-verdict-quickfixes.md#implementation-plan): the forced-OCR regression ([QF1 root cause](../rfcs/021-run4-verdict-quickfixes.md#qf1-f2d2-forced-ocr-regression-docs-7-20-21)) — pre-garble probe forces OCR on primary attempt, destroying PictureItem segmentation and collapsing tree for docs 7, 20, 21.*

  - [x] <a id="11-remove-forced-ocr-from-pre-garble-probe"></a>1.1 Remove forced-OCR from pre-garble probe (P0, effort: M)

    - In `src/pageindex_mcp/client.py:553-556`: remove the block that calls `conv_fn(file_path, True)` when `pre_garbled=True` and converter is docling
    - Keep `pre_garbled` flag for logging/diagnostics only — pre-garble probe still detects garbled text layers, but does NOT force OCR on primary conversion
    - Primary attempt runs normally (preserving Docling's PictureItem segmentation); existing Fix-3 retry path (`client.py:729-751`) handles OCR escalation when `validate_tree` returns `reason="garbling"`
    - Add `PRE_GARBLE_FORCE_OCR_ENABLED` env var (default `false`; `true` restores pre-RFC-021 behavior as rollback lever)
    - Log INFO when pre-garble probe fires but OCR deferral is active (flag detected, deferring to Fix-3 retry)
    - _Requirements:_ [RFC-021 QF1](../rfcs/021-run4-verdict-quickfixes.md#qf1-fix-defer-ocr-escalation-from-pre-garble-probe-to-fix-3-retry-path) | [Design Property 1](../designs/design-rfc021-run4-verdict-quickfixes.md#property-1-pictureitem-preservation) | [Design Service: client.py](../designs/design-rfc021-run4-verdict-quickfixes.md#1-clientpy) | [Design AD1](../designs/design-rfc021-run4-verdict-quickfixes.md#ad1-defer-ocr-to-fix-3-retry-qf1) | [Design Sequence: Pre-Garble Probe Flow](../designs/design-rfc021-run4-verdict-quickfixes.md#pre-garble-probe-flow-qf1)

  - [x] <a id="12-qf1-unit-and-integration-tests"></a>1.2 QF1 unit and integration tests (P0, effort: M)

    - Unit: (a) `pre_garbled=True` does NOT invoke `conv_fn(file_path, True)` on primary attempt; (b) PictureItems preserved in primary output; (c) Fix-3 retry path still fires when `validate_tree` returns `reason="garbling"`; (d) `PRE_GARBLE_FORCE_OCR_ENABLED=true` restores old behavior
    - Integration: Arabic scanned-page fixture produces tree `depth >= 2` with PictureItems intact on primary attempt
    - **Validates:** [Design Property 1](../designs/design-rfc021-run4-verdict-quickfixes.md#property-1-pictureitem-preservation) | [RFC-021 QF1 test plan row](../rfcs/021-run4-verdict-quickfixes.md#test-plan)
    - _Requirements:_ [RFC-021 QF1](../rfcs/021-run4-verdict-quickfixes.md#qf1-fix-defer-ocr-escalation-from-pre-garble-probe-to-fix-3-retry-path) | [Design Property 1](../designs/design-rfc021-run4-verdict-quickfixes.md#property-1-pictureitem-preservation) | [Design Sequence: Pre-Garble Probe Flow](../designs/design-rfc021-run4-verdict-quickfixes.md#pre-garble-probe-flow-qf1)

  - [x] <a id="12a-update-d3a-assertions-in-test_client_contract"></a>1.2a Update D3a assertions in test_client_contract.py (P0, effort: S)

    - Update `tests/test_client_contract.py` D3a block (lines 619-720) — these assert `conv_mock` is called with `(file_path, True, ocr_lang_override=...)` when page-0 text is garbled; QF1 inverts this behavior
    - Assert that `conv_mock` is called WITHOUT `force_full_page_ocr=True` on primary attempt when `pre_garbled=True`
    - Assert that the rollback path (`PRE_GARBLE_FORCE_OCR_ENABLED=true`) restores the current call including `ocr_lang_override=detect_ocr_langs(filename)`
    - _Requirements:_ [RFC-021 QF1](../rfcs/021-run4-verdict-quickfixes.md#qf1-fix-defer-ocr-escalation-from-pre-garble-probe-to-fix-3-retry-path) | [Design Property 1](../designs/design-rfc021-run4-verdict-quickfixes.md#property-1-pictureitem-preservation)

  - [ ] <a id="13-checkpoint--phase-1"></a>1.3 Checkpoint — Phase 1

    - Run `uv run pytest` — all tests green
    - Spot-reingest doc 7 (MOU MOHRE) — verify PictureItems preserved and tree depth restored
    - Cross-ref: [RFC-021 §Validation Checkpoints](../rfcs/021-run4-verdict-quickfixes.md#validation-checkpoints)

- [x] <a id="2-phase-2--qf2-verdict-threshold-tuning"></a>2. Phase 2 — [QF2](../rfcs/021-run4-verdict-quickfixes.md#qf2-verdict-threshold-harshness-smallflat-docs-docs-8-13-14-19) Verdict Threshold Tuning (0.75 d)

  *[RFC-021 §Implementation Plan — Phase 2](../rfcs/021-run4-verdict-quickfixes.md#implementation-plan): PASS gate too strict for small/flat docs with good content ([QF2 root cause](../rfcs/021-run4-verdict-quickfixes.md#qf2-verdict-threshold-harshness-smallflat-docs-docs-8-13-14-19)). Three sub-fixes: image-enrichment promotion ([QF2a](../rfcs/021-run4-verdict-quickfixes.md#qf2a-image-enrichment-promotion-path)), max_leaf_ratio relaxation ([QF2b](../rfcs/021-run4-verdict-quickfixes.md#qf2b-relax-max_leaf_ratio-for-primary-pass-gate)), small-doc exemption ([QF2c](../rfcs/021-run4-verdict-quickfixes.md#qf2c-small-doc-exemption)).*

  - [x] <a id="21-implement-qf2a-image-enrichment-promotion"></a>2.1 Implement QF2a image-enrichment promotion (P0, effort: M)

    - In `src/pageindex_mcp/helpers.py:886-904`: add new promotion path BEFORE the MARGINAL fallthrough (after existing cat_b/cat_c promotions)
    - When `content_class in ("flat_prose", "flat_mixed")` and `image_enrichment_ratio >= 0.8`: return `"PASS", "image_enrichment_promoted"`
    - Ratio computed in `client.py` from flat `blocks` after `_enrich_image_blocks()` runs (line 969): `image_blocks = [b for b in blocks if b.get("role") == "image"]`, `enriched_count = sum(1 for b in image_blocks if b.get("ocr_text") or b.get("description") or b.get("figure_path"))`, `image_enrichment_ratio = enriched_count / len(image_blocks) if image_blocks else None`
    - In `src/pageindex_mcp/client.py:980`: pass the computed `image_enrichment_ratio` to `classify_verdict`
    - Add `image_enrichment_ratio: float | None = None` parameter to `classify_verdict` signature
    - Pre-implementation check: verify docs 13/14 have `max_leaf_ratio <= 0.75` (or they will hit the FAIL gate at `helpers.py:876-877` before reaching the promotion)
    - _Requirements:_ [RFC-021 QF2a](../rfcs/021-run4-verdict-quickfixes.md#qf2a-image-enrichment-promotion-path) | [Design Property 2](../designs/design-rfc021-run4-verdict-quickfixes.md#property-2-image-enrichment-promotion) | [Design Service: helpers.py](../designs/design-rfc021-run4-verdict-quickfixes.md#2-helperspy) | [Design AD2](../designs/design-rfc021-run4-verdict-quickfixes.md#ad2-image-enrichment-promotion-qf2a) | [Design Sequence: Verdict Computation Flow](../designs/design-rfc021-run4-verdict-quickfixes.md#verdict-computation-flow-qf2qf4)

  - [x] <a id="22-implement-qf2b-max_leaf_ratio-relaxation"></a>2.2 Implement QF2b max_leaf_ratio relaxation (P0, effort: S)

    - In `src/pageindex_mcp/helpers.py:883`: change primary PASS gate threshold from `max_leaf_ratio < 0.15` to `max_leaf_ratio < 0.17`
    - Aligns primary PASS gate with `CATEGORY_BC_PROMOTION_THRESHOLD` (0.17) already used at `helpers.py:896` and `helpers.py:903`
    - Add `PASS_MAX_LEAF_RATIO` env var (default `0.17`; set to `0.15` to restore old behavior)
    - _Requirements:_ [RFC-021 QF2b](../rfcs/021-run4-verdict-quickfixes.md#qf2b-relax-max_leaf_ratio-for-primary-pass-gate) | [Design Property 4](../designs/design-rfc021-run4-verdict-quickfixes.md#property-4-pass-gate-threshold-consistency) | [Design Service: helpers.py](../designs/design-rfc021-run4-verdict-quickfixes.md#2-helperspy) | [Design AD4](../designs/design-rfc021-run4-verdict-quickfixes.md#ad4-threshold-alignment-qf2b) | [Design Sequence: Verdict Computation Flow](../designs/design-rfc021-run4-verdict-quickfixes.md#verdict-computation-flow-qf2qf4)

  - [x] <a id="23-implement-qf2c-small-doc-exemption"></a>2.3 Implement QF2c small-doc exemption (P1, effort: M)

    - In `src/pageindex_mcp/helpers.py`, after cat_b/cat_c and QF2a promotion paths, BEFORE MARGINAL fallthrough: add small-doc exemption
    - Tightened conditions: `node_count <= 10` AND `max_leaf_ratio < 0.20` AND `not effectively_garbled` (from QF4) AND `len(flat_text) < 15_000` AND `len(text) > 100` AND `content_class in ("flat_prose", "flat_mixed")`
    - Returns `"PASS", "small_doc_promoted"`
    - Note: preserves existing guardrail test `test_cat_b_above_017_stays_marginal` (ratio=0.20, 10 nodes) because 0.20 is NOT < 0.20
    - Pre-implementation check: verify whether QF2b alone (0.15->0.17) rescues doc 8 — if yes, defer QF2c
    - Uses `effectively_garbled` from QF4 (not binary `garbled`)
    - Add `SMALL_DOC_PROMOTION_ENABLED` env var (default `true`; `false` disables exemption)
    - _Requirements:_ [RFC-021 QF2c](../rfcs/021-run4-verdict-quickfixes.md#qf2c-small-doc-exemption) | [Design Property 5](../designs/design-rfc021-run4-verdict-quickfixes.md#property-5-small-doc-promotion-safety) | [Design Service: helpers.py](../designs/design-rfc021-run4-verdict-quickfixes.md#2-helperspy) | [Design AD5](../designs/design-rfc021-run4-verdict-quickfixes.md#ad5-small-doc-exemption-qf2c) | [Design Sequence: Verdict Computation Flow](../designs/design-rfc021-run4-verdict-quickfixes.md#verdict-computation-flow-qf2qf4)

  - [x] <a id="24-qf2-unit-tests"></a>2.4 QF2 unit tests (P0, effort: M)

    - QF2a: (a) `flat_prose` + `image_enrichment_ratio=1.0` -> PASS, reason=`"image_enrichment_promoted"`; (b) `image_enrichment_ratio=0.5` -> MARGINAL (below 0.8 threshold); (c) non-flat content_class -> no promotion; (d) `image_enrichment_ratio=None` -> no change
    - QF2b: (a) `max_leaf_ratio=0.16`, other PASS conditions met -> PASS; (b) `max_leaf_ratio=0.18` -> MARGINAL; (c) `PASS_MAX_LEAF_RATIO=0.15` -> old behavior restored; (d) existing PASS docs still PASS
    - QF2c: (a) 8-node, ratio=0.15, len<15000, clean doc -> PASS, reason=`"small_doc_promoted"`; (b) 11-node doc -> no exemption; (c) ratio=0.20, 10 nodes -> no exemption (preserves `test_cat_b_above_017_stays_marginal`); (d) `effectively_garbled` small doc -> no exemption; (e) `SMALL_DOC_PROMOTION_ENABLED=false` -> no exemption; (f) len>=15000 -> no exemption
    - **Validates:** [Design Property 2](../designs/design-rfc021-run4-verdict-quickfixes.md#property-2-image-enrichment-promotion) | [Design Property 4](../designs/design-rfc021-run4-verdict-quickfixes.md#property-4-pass-gate-threshold-consistency) | [Design Property 5](../designs/design-rfc021-run4-verdict-quickfixes.md#property-5-small-doc-promotion-safety) | [Design Property 9](../designs/design-rfc021-run4-verdict-quickfixes.md#property-9-zero-regression-on-existing-pass) | [RFC-021 QF2 test plan rows](../rfcs/021-run4-verdict-quickfixes.md#test-plan)
    - _Requirements:_ [RFC-021 QF2a](../rfcs/021-run4-verdict-quickfixes.md#qf2a-image-enrichment-promotion-path) | [RFC-021 QF2b](../rfcs/021-run4-verdict-quickfixes.md#qf2b-relax-max_leaf_ratio-for-primary-pass-gate) | [RFC-021 QF2c](../rfcs/021-run4-verdict-quickfixes.md#qf2c-small-doc-exemption)

  - [ ] <a id="25-checkpoint--phase-2"></a>2.5 Checkpoint — Phase 2

    - Run `uv run pytest` — all tests green
    - Spot-reingest docs 8 (Reitlehrer), 13 (Pie chart), 14 (UAE landscape), 19 (Data Governance) — verify each promotes to PASS via the expected path
    - Cross-ref: [RFC-021 §Validation Checkpoints](../rfcs/021-run4-verdict-quickfixes.md#validation-checkpoints)

- [x] <a id="3-phase-3--qf3-garble-gate-precision"></a>3. Phase 3 — [QF3](../rfcs/021-run4-verdict-quickfixes.md#qf3-fix-garble-gate-precision-for-bilingual-docs) Garble Gate Precision (0.5-0.75 d)

  *[RFC-021 §Implementation Plan — Phase 3](../rfcs/021-run4-verdict-quickfixes.md#implementation-plan): garble gate false-positive on bilingual content ([QF3 root cause](../rfcs/021-run4-verdict-quickfixes.md#qf3-garble-gate-false-positive-on-bilingual-docs-doc-17)). Diagnosis-first approach: identify the actual firing mechanism before implementing a fix. Prior assumptions (`_MD_FORMAT_RE` exclusion and `_COMMON_WORDS` bilingual guard) are proven NO-OPs.*

  - [x] <a id="31-diagnose-doc-17-garble-trigger"></a>3.1 Diagnose doc 17 garble trigger (QF3-D) (P0, effort: M)

    - Extract doc 17's flattened tree text from its stored `processed/*.json`
    - Run through each garble sub-check independently:
      - `_is_garbled_blob(text, expected_script=None)` — log each sub-prong result
      - `_has_sparse_mojibake(text)` — log match count, word count, ratio vs 0.02 threshold
      - `_tree_is_garbled(structure)` — log per-node vs full-blob results
    - Identify the firing mechanism and its actual values
    - Write diagnosis report with proposed fix
    - _Requirements:_ [RFC-021 QF3](../rfcs/021-run4-verdict-quickfixes.md#qf3-fix-garble-gate-precision-for-bilingual-docs)

  - [x] <a id="32-implement-qf3-fix-based-on-diagnosis"></a>3.2 Implement QF3 fix based on diagnosis (P0, effort: S-M)

    - Design and implement fix based on measured data from [3.1](#31-diagnose-doc-17-garble-trigger)
    - Most likely: threshold calibration or `_MIXED_SCRIPT_RE` pattern refinement
    - NOT: `_MD_FORMAT_RE` exclusion (proven NO-OP) or `_COMMON_WORDS` bilingual guard (proven NO-OP)
    - _Requirements:_ [RFC-021 QF3](../rfcs/021-run4-verdict-quickfixes.md#qf3-fix-garble-gate-precision-for-bilingual-docs) | [Design Service: helpers.py](../designs/design-rfc021-run4-verdict-quickfixes.md#2-helperspy)

  - [x] <a id="33-qf3-unit-tests"></a>3.3 QF3 unit tests (P0, effort: M)

    - Tests determined by diagnosis outcome ([3.1](#31-diagnose-doc-17-garble-trigger)) and fix implementation ([3.2](#32-implement-qf3-fix-based-on-diagnosis))
    - Must include: (a) doc 17 bilingual Arabic/English text no longer flagged as garbled; (b) actual garbled text still detected; (c) regression guards for existing garble detection
    - **Validates:** [Design Property 6](../designs/design-rfc021-run4-verdict-quickfixes.md#property-6-markdown-token-exclusion-precision) | [Design Property 7](../designs/design-rfc021-run4-verdict-quickfixes.md#property-7-bilingual-content-recognition) | [RFC-021 QF3 test plan rows](../rfcs/021-run4-verdict-quickfixes.md#test-plan)
    - _Requirements:_ [RFC-021 QF3](../rfcs/021-run4-verdict-quickfixes.md#qf3-fix-garble-gate-precision-for-bilingual-docs)

  - [ ] <a id="34-checkpoint--phase-3"></a>3.4 Checkpoint — Phase 3

    - Run `uv run pytest` — all tests green
    - Spot-reingest doc 17 (SLA Agreement) — verify bilingual Arabic/English content no longer flagged as garbled; verdict promotes to PASS
    - Note: diagnosis-first approach — Phase 3 checkpoint may be reached only after 3.1 diagnosis determines the fix
    - Cross-ref: [RFC-021 §Validation Checkpoints](../rfcs/021-run4-verdict-quickfixes.md#validation-checkpoints)

- [x] <a id="4-phase-4--qf4-garble-ratio"></a>4. Phase 4 — [QF4](../rfcs/021-run4-verdict-quickfixes.md#qf4-fix-garble-ratio-check-in-classify_verdict) Garble Ratio (0.5 d)

  *[RFC-021 §Implementation Plan — Phase 4](../rfcs/021-run4-verdict-quickfixes.md#implementation-plan): stored verdict reflects input text-layer garbling, not output quality ([QF4 root cause](../rfcs/021-run4-verdict-quickfixes.md#qf4-verdict_reason-input-probe-not-output-quality-docs-20-21)). Any garble in flattened text flags the whole document; no ratio threshold distinguishes a garbled cover page from a fully garbled document.*

  - [x] <a id="41-implement-garble-ratio-function"></a>4.1 Implement `_garble_ratio()` function (P0, effort: M)

    - In `src/pageindex_mcp/helpers.py`: add `_garble_ratio(text: str, expected_script: str | None = None) -> float`
    - Window size 2000 (NOT 500) — ensures digit-ratio prong (`len(blob) > 500`) can fire within windows
    - Each window runs BOTH `_is_garbled_blob` AND `_has_sparse_mojibake` — preserves RFC-015 D8 sparse detection
    - Full-text check runs in parallel: `max(full_garbled, window_ratio)` ensures additive-only
    - `_tree_is_garbled` binary gate preserved (not replaced) — QF4 adds ratio overlay only
    - In `classify_verdict` (`helpers.py:881` / `helpers.py:907-908`): replace boolean garble check with ratio-based check
    - Hoist `_flatten_tree_text(structure)` call to avoid double computation (already called later at `helpers.py:890`)
    - `garble_ratio = _garble_ratio(flat_text, expected_script=expected_script)` — flag garbled only when `garble_ratio > GARBLE_WINDOW_RATIO_THRESHOLD`
    - Add `GARBLE_WINDOW_RATIO_THRESHOLD` env var (default `0.05`; set to `0.0` to restore old any-garble-flags-all behavior) — read at call-time, not module-level
    - `classify_verdict` does NOT need `expected_script` parameter change — `_garble_ratio` inherits None
    - Include `garble_ratio` in returned meta dict for diagnostics
    - _Requirements:_ [RFC-021 QF4](../rfcs/021-run4-verdict-quickfixes.md#qf4-fix-garble-ratio-check-in-classify_verdict) | [Design Property 8](../designs/design-rfc021-run4-verdict-quickfixes.md#property-8-garble-ratio-windowed-accuracy) | [Design Service: helpers.py](../designs/design-rfc021-run4-verdict-quickfixes.md#2-helperspy) | [Design AD8](../designs/design-rfc021-run4-verdict-quickfixes.md#ad8-garble-ratio-windowing-qf4) | [Design Sequence: Verdict Computation Flow](../designs/design-rfc021-run4-verdict-quickfixes.md#verdict-computation-flow-qf2qf4)

  - [x] <a id="42-qf4-unit-tests"></a>4.2 QF4 unit tests (P0, effort: M)

    - (a) 18000-char clean text + 2000-char garbled suffix -> `_garble_ratio` returns ~0.10 (1/10 windows garbled), above 0.05 threshold -> flagged garbled; adjust clean/garbled ratio for below-threshold test
    - (b) Fully garbled text -> `_garble_ratio` returns ~1.0 -> flagged garbled
    - (c) 50% garbled text -> `_garble_ratio` returns ~0.5 -> flagged garbled
    - (d) `GARBLE_WINDOW_RATIO_THRESHOLD=0.0` -> any garble flags document (binary behavior restoration)
    - (e) Empty text -> `_garble_ratio` returns 1.0 (defensive)
    - (f) `garble_ratio` present in returned meta dict
    - (g) Regression guard: fully numeric-junk text -> full-text digit-ratio fires, ratio=1.0 (preserves وارد 597 class detection)
    - (h) Regression guard: sparse-mojibake text -> full-text `_has_sparse_mojibake` fires, ratio=1.0 (preserves RFC-015 D8 detection)
    - **Validates:** [Design Property 8](../designs/design-rfc021-run4-verdict-quickfixes.md#property-8-garble-ratio-windowed-accuracy) | [RFC-021 QF4 test plan row](../rfcs/021-run4-verdict-quickfixes.md#test-plan)
    - _Requirements:_ [RFC-021 QF4](../rfcs/021-run4-verdict-quickfixes.md#qf4-fix-garble-ratio-check-in-classify_verdict) | [Design Property 8](../designs/design-rfc021-run4-verdict-quickfixes.md#property-8-garble-ratio-windowed-accuracy)

  - [ ] <a id="43-checkpoint--phase-4"></a>4.3 Checkpoint — Phase 4

    - Run `uv run pytest` — all tests green
    - Spot-reingest docs 20 (Labor Exec Regs), 21 (Domestic Workers) — verify garble ratio check passes (cover page noise below threshold); verdict promotes to PASS via combination of [QF1](#11-remove-forced-ocr-from-pre-garble-probe) + QF4
    - Cross-ref: [RFC-021 §Validation Checkpoints](../rfcs/021-run4-verdict-quickfixes.md#validation-checkpoints)

- [ ] <a id="5-phase-5--full-corpus-reaudit"></a>5. Phase 5 — Full Corpus Reaudit (0.5 d)

  *[RFC-021 §Implementation Plan — Phase 5](../rfcs/021-run4-verdict-quickfixes.md#implementation-plan): full 25-doc reingestion to validate projected outcomes and verify zero regressions on Run 4's 13 PASS docs.*

  - [ ] <a id="51-full-25-doc-reingestion-and-run-5-scorecard"></a>5.1 Full 25-doc reingestion and Run 5 scorecard (P0, effort: L)

    - Full batch reingestion via `preprocess_client.py`
    - Produce Run 5 audit scorecard against [RFC-021 projected impact](../rfcs/021-run4-verdict-quickfixes.md#expected-outcomes) (target: 19-20 PASS, 2-3 MARGINAL, 2 FAIL, 1 ERROR)
    - Per-doc checks against [RFC-021 per-doc projections](../rfcs/021-run4-verdict-quickfixes.md#expected-outcomes):
      - Doc 7 (MOU MOHRE): [QF1](#11-remove-forced-ocr-from-pre-garble-probe) -> PASS (PictureItems preserved, tree restored)
      - Doc 8 (Reitlehrer): [QF2b](#22-implement-qf2b-max_leaf_ratio-relaxation) / [QF2c](#23-implement-qf2c-small-doc-exemption) -> PASS
      - Doc 13 (Pie chart): [QF2a](#21-implement-qf2a-image-enrichment-promotion) -> PASS (enrichment ratio 2/2 = 1.0)
      - Doc 14 (UAE landscape): [QF2a](#21-implement-qf2a-image-enrichment-promotion) -> PASS (enrichment ratio 4/5 = 0.8)
      - Doc 17 (SLA Agreement): [QF3](#32-implement-qf3-fix-based-on-diagnosis) -> PASS (bilingual not flagged)
      - Doc 19 (Data Governance): [QF2b](#22-implement-qf2b-max_leaf_ratio-relaxation) -> PASS (max_leaf_ratio 0.16 < 0.17)
      - Docs 20, 21: [QF1](#11-remove-forced-ocr-from-pre-garble-probe) + [QF4](#41-implement-garble-ratio-function) -> PASS
    - Verify zero regressions on Run 4's 13 PASS docs — [Design Property 9](../designs/design-rfc021-run4-verdict-quickfixes.md#property-9-zero-regression-on-existing-pass)
    - Record results in a Run 5 audit file under `audit/`; explain any variance from projection
    - Cross-ref: [RFC-021 §Validation Checkpoints](../rfcs/021-run4-verdict-quickfixes.md#validation-checkpoints) | [RFC-021 §Expected Outcomes](../rfcs/021-run4-verdict-quickfixes.md#expected-outcomes)
    - _Requirements:_ [RFC-021 QF1](../rfcs/021-run4-verdict-quickfixes.md#qf1-fix-defer-ocr-escalation-from-pre-garble-probe-to-fix-3-retry-path) | [RFC-021 QF2](../rfcs/021-run4-verdict-quickfixes.md#proposed-fixes) | [RFC-021 QF3](../rfcs/021-run4-verdict-quickfixes.md#qf3-fix-garble-gate-precision-for-bilingual-docs) | [RFC-021 QF4](../rfcs/021-run4-verdict-quickfixes.md#qf4-fix-garble-ratio-check-in-classify_verdict) | [Design Properties 1-9](../designs/design-rfc021-run4-verdict-quickfixes.md#correctness-properties)

- [x] <a id="6-phase-6--qf2a-lt-dedicated-image-pipeline"></a>6. Phase 6 — [QF2a-LT](../rfcs/021-run4-verdict-quickfixes.md#qf2a-lt-dedicated-image-file-pipeline-long-term-follow-up) Dedicated Image Pipeline (1.25 d)

  *[RFC-021 §Implementation Plan — Phase 6](../rfcs/021-run4-verdict-quickfixes.md#implementation-plan): architecturally correct long-term fix for standalone image files. Ships independently after Phase 5 — [QF2a](#21-implement-qf2a-image-enrichment-promotion) is working for immediate needs.*

  - [x] <a id="61-implement-image_standalone-content-class-detection"></a>6.1 Implement `image_standalone` content-class detection (P2, effort: M)

    - In `src/pageindex_mcp/client.py`, in `index()` (around line 520): detect image file extensions using existing `_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".tif"}` (defined at `client.py:231/681`) — do NOT define a new set
    - `.gif`/`.webp` are NOT in `_SUPPORTED` — they need `_SUPPORTED` update or deferral (`.bmp` also not in `_SUPPORTED`)
    - Set `content_class = "image_standalone"` — skip tree/flat path entirely
    - Add `IMAGE_STANDALONE_PIPELINE_ENABLED` env var (default `true`; `false` falls back to [QF2a](#21-implement-qf2a-image-enrichment-promotion) promotion path)
    - Route to image-specific processing: existing `_IMAGE_EXTS` route at `client.py:231/681` already runs Tesseract-OCR + synthetic `PictureResult`s — reconcile, do not duplicate
    - _Requirements:_ [RFC-021 QF2a-LT](../rfcs/021-run4-verdict-quickfixes.md#qf2a-lt-dedicated-image-file-pipeline-long-term-follow-up) | [Design Property 3](../designs/design-rfc021-run4-verdict-quickfixes.md#property-3-image-standalone-routing) | [Design Service: client.py](../designs/design-rfc021-run4-verdict-quickfixes.md#1-clientpy) | [Design AD3](../designs/design-rfc021-run4-verdict-quickfixes.md#ad3-dedicated-image-pipeline-qf2a-lt)

  - [x] <a id="62-implement-classify-image-verdict"></a>6.2 Implement `_classify_image_verdict()` (P2, effort: M)

    - In `src/pageindex_mcp/helpers.py`: add `_classify_image_verdict(image_enrichment_ratio: float | None) -> tuple[str, str]`
    - Receives `image_enrichment_ratio: float | None` parameter (not `structure`)
    - PASS: `image_enrichment_ratio` is not None and >= 0.8
    - MARGINAL: images detected but enrichment ratio < 0.8
    - FAIL: no images detected at all (ratio is None or 0)
    - In `classify_verdict`: early return `_classify_image_verdict(image_enrichment_ratio)` when `content_class == "image_standalone"` — no tree/flat metrics applied
    - _Requirements:_ [RFC-021 QF2a-LT](../rfcs/021-run4-verdict-quickfixes.md#qf2a-lt-dedicated-image-file-pipeline-long-term-follow-up) | [Design Property 3](../designs/design-rfc021-run4-verdict-quickfixes.md#property-3-image-standalone-routing) | [Design Service: helpers.py](../designs/design-rfc021-run4-verdict-quickfixes.md#2-helperspy) | [Design AD3](../designs/design-rfc021-run4-verdict-quickfixes.md#ad3-dedicated-image-pipeline-qf2a-lt)

  - [x] <a id="63-image-specific-meta-fields"></a>6.3 Image-specific meta fields (P2, effort: S)

    - `_classify_image_verdict` returns meta dict with image-relevant fields only: `total_images`, `enriched_images`, `enrichment_methods`, `verdict`, `verdict_reason`
    - No `max_leaf_ratio`, `node_count`, `depth` — these are meaningless for images
    - Persisted in `processed/*.meta.json` as usual
    - _Requirements:_ [RFC-021 QF2a-LT](../rfcs/021-run4-verdict-quickfixes.md#qf2a-lt-dedicated-image-file-pipeline-long-term-follow-up) | [Design Property 3](../designs/design-rfc021-run4-verdict-quickfixes.md#property-3-image-standalone-routing)

  - [x] <a id="64-qf2a-lt-tests"></a>6.4 QF2a-LT tests (P2, effort: M)

    - Update existing tests in `test_image_blocks.py` and `test_imgblock_audit_findings.py`
    - Unit: (a) `.jpg` file -> `content_class="image_standalone"` detected; (b) `.pdf` file -> NOT `image_standalone`; (c) enriched image -> PASS verdict; (d) un-enriched image -> MARGINAL; (e) no images detected -> FAIL; (f) `IMAGE_STANDALONE_PIPELINE_ENABLED=false` -> falls back to standard path with [QF2a](#21-implement-qf2a-image-enrichment-promotion) promotion
    - Integration: `.jpg` fixture file -> end-to-end ingestion produces PASS verdict with `image_standalone` content class and image-specific meta fields
    - **Validates:** [Design Property 3](../designs/design-rfc021-run4-verdict-quickfixes.md#property-3-image-standalone-routing) | [RFC-021 QF2a-LT](../rfcs/021-run4-verdict-quickfixes.md#qf2a-lt-dedicated-image-file-pipeline-long-term-follow-up)
    - _Requirements:_ [RFC-021 QF2a-LT](../rfcs/021-run4-verdict-quickfixes.md#qf2a-lt-dedicated-image-file-pipeline-long-term-follow-up) | [Design Property 3](../designs/design-rfc021-run4-verdict-quickfixes.md#property-3-image-standalone-routing)

  - [ ] <a id="65-checkpoint--phase-6"></a>6.5 Checkpoint — Phase 6

    - Run `uv run pytest` — all tests green
    - Reingest doc 13 (Pie chart) as `.jpg` — verify `image_standalone` routing and PASS verdict via `_classify_image_verdict`
    - Cross-ref: [RFC-021 §Validation Checkpoints](../rfcs/021-run4-verdict-quickfixes.md#validation-checkpoints)

## Notes

- [QF1](../rfcs/021-run4-verdict-quickfixes.md#qf1-fix-defer-ocr-escalation-from-pre-garble-probe-to-fix-3-retry-path) is the highest-impact fix — [QF1 root cause](../rfcs/021-run4-verdict-quickfixes.md#qf1-f2d2-forced-ocr-regression-docs-7-20-21) destroys PictureItem segmentation for ALL pre-garbled docs (3 documents directly affected). It must land before Phase 5 reaudit can validate the full projected improvement
- Docs 20, 21 need both [QF1](#11-remove-forced-ocr-from-pre-garble-probe) (OCR deferral) AND [QF4](#41-implement-garble-ratio-function) (garble ratio) — either fix alone is insufficient ([RFC-021 §Per-Doc Projections](../rfcs/021-run4-verdict-quickfixes.md#expected-outcomes))
- [QF2b](../rfcs/021-run4-verdict-quickfixes.md#qf2b-relax-max_leaf_ratio-for-primary-pass-gate) is a 0.02 threshold relaxation (0.15 -> 0.17), aligned with existing `CATEGORY_BC_PROMOTION_THRESHOLD`; [risk assessment](../rfcs/021-run4-verdict-quickfixes.md#risk-assessment) rates false-promotion likelihood as Low
- [QF2c](../rfcs/021-run4-verdict-quickfixes.md#qf2c-small-doc-exemption) is the most aggressive sub-fix — tightened to `max_leaf_ratio < 0.20` + `node_count <= 10` + `len(flat_text) < 15_000` + `not effectively_garbled` (from QF4) + `len(text) > 100`; gated behind env var; full-corpus regression in [Phase 5](#51-full-25-doc-reingestion-and-run-5-scorecard) validates safety ([RFC-021 §Risk Assessment](../rfcs/021-run4-verdict-quickfixes.md#risk-assessment))
- [QF3](../rfcs/021-run4-verdict-quickfixes.md#qf3-fix-garble-gate-precision-for-bilingual-docs) is diagnosis-first: prior assumptions (`_MD_FORMAT_RE` exclusion and `_COMMON_WORDS` bilingual guard) are proven NO-OPs; Phase 3 diagnoses the actual firing mechanism before implementing a fix
- [QF4](../rfcs/021-run4-verdict-quickfixes.md#qf4-fix-garble-ratio-check-in-classify_verdict) 2000-char window size ensures digit-ratio prong (`len(blob) > 500`) can fire within windows — [RFC-021 Open Question 4](../rfcs/021-run4-verdict-quickfixes.md#open-questions) notes this may need tuning based on Run 5 results
- [QF2a-LT](../rfcs/021-run4-verdict-quickfixes.md#qf2a-lt-dedicated-image-file-pipeline-long-term-follow-up) (Phase 6) ships independently after Phase 5 — [QF2a](#21-implement-qf2a-image-enrichment-promotion) handles immediate needs; Phase 6 is the architecturally correct long-term path
- Phases 1-4 are sequential in the implementation plan but Phase 3 and Phase 4 are independent of each other — they can be parallelized if needed
- Each phase is an isolated commit with env-var rollback levers (see [RFC-021 §Rollback Strategy](../rfcs/021-run4-verdict-quickfixes.md#rollback-strategy))
- All fixes apply to future ingestions only — realized scorecard requires the Run 5 reaudit ([Task 5.1](#51-full-25-doc-reingestion-and-run-5-scorecard))
- [RFC-021 Open Question 1](../rfcs/021-run4-verdict-quickfixes.md#open-questions): QF1 interaction with QF3 — removing forced OCR on docs 20/21 may change garble patterns; QF3 diagnosis phase will determine the actual fix, which is independent of OCR path

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "name": "QF1 OCR deferral implementation",
      "tasks": ["1.1"],
      "depends_on": []
    },
    {
      "id": 1,
      "name": "QF1 tests + QF2/QF3-diagnosis/QF4 parallel starts",
      "tasks": ["1.2", "1.2a", "2.1", "2.2", "2.3", "3.1", "4.1"],
      "depends_on": [0]
    },
    {
      "id": 2,
      "name": "Phase 1 checkpoint + QF2/QF4 tests + QF3 fix (post-diagnosis)",
      "tasks": ["1.3", "2.4", "3.2", "3.3", "4.2"],
      "depends_on": [1]
    },
    {
      "id": 3,
      "name": "Phase 2/3/4 checkpoints",
      "tasks": ["2.5", "3.4", "4.3"],
      "depends_on": [2]
    },
    {
      "id": 4,
      "name": "Full corpus reaudit — Run 5",
      "tasks": ["5.1"],
      "depends_on": [3]
    },
    {
      "id": 5,
      "name": "QF2a-LT dedicated image pipeline",
      "tasks": ["6.1", "6.2", "6.3"],
      "depends_on": [4]
    },
    {
      "id": 6,
      "name": "QF2a-LT tests + checkpoint",
      "tasks": ["6.4", "6.5"],
      "depends_on": [5]
    }
  ]
}
```
