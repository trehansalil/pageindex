<!-- Space: CITRA -->
<!-- Title: Implementation Plan: RFC-023 Run 6 Content Recovery & Verdict Hardening -->
<!-- Folder: Tasks -->

# Implementation Plan: RFC-023 Run 6 Content Recovery & Verdict Hardening

## Traceability

| Artifact               | Reference                                                                                                                              |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Governing RFC(s)       | [RFC-023: Run 6 Content Recovery &amp; Verdict Hardening](../rfcs/023-run6-content-recovery-and-verdict-hardening.md)                   |
| Design Document        | [design-rfc023-run6-content-recovery-and-verdict-hardening.md](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md) |
| Hard Rules (binding)   | [CLAUDE.md § Hard Rules](../../CLAUDE.md#hard-rules)                                                                                   |
| Implementation Order   | [RFC-023 Implementation Plan](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#implementation-plan)                           |
| Test Strategy          | [RFC-023 Test Strategy](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#test-strategy)                                       |
| Correctness Properties | [Design § Correctness Properties](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#correctness-properties)      |

## Overview

This plan implements the 11 in-scope defect fixes from [RFC-023&#39;s Implementation Plan](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#implementation-plan) across four existing modules (`converters.py`, `helpers.py`, `client.py`, `worker.py`), organized into six batches matching the RFC's own batching and dependency ordering. Batches 1-2 land the content-recovery and verdict-hardening fixes that are prerequisites for accurate downstream classification (D0, D1, D3, D11, then D4, D5); Batch 3 lands edge-case fixes that must be rebased on Batch 1's splice-path rewrite (D2, D6); Batch 4 lands standalone-image OCR enrichment, VLM fallback, and worker error mapping (D8a, D7, D8b); Batch 5 lands the independent BiDi and threshold-widening fixes (D9, D10); Batch 6 runs the full 25-doc corpus reaudit and verifies the projected verdict distribution with zero regressions on Run-6 PASS docs. Each task validates one or more of the [12 correctness properties](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#correctness-properties) defined in the design document.

## Tasks

- [X] <a id="1-batch-1--content-recovery-pipeline-d0-d1-d3-d11"></a>1. Batch 1: Content Recovery Pipeline ([RFC-023 D0](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d0-make-_text_layer_has_content-garble-aware-p0-bug), [D1](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d1-graceful-degradation-for-splice_figure_markers-count-mismatch-p0-bug), [D3](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d3-strip--image---markers-from-garble-detection-p0-bug), [D11](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d11-widen-ocr-escalation-to-structural-failure-reasons-p1-bug))

  *[RFC-023 Batch 1: Content Recovery Pipeline](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#batch-1-content-recovery-pipeline-d0-d1-d3-d11----20d) — these fixes are independent of each other but collectively address the primary content-loss regressions; D0 and D3 are prerequisites for accurate downstream verdict computation.*

  - [X] <a id="11-garble-aware-text-layer-exemption-d0"></a>1.1 Add garble check to `_text_layer_has_content` (D0)

    - In `src/pageindex_mcp/converters.py`, call `_is_garbled_blob` (or `_flat_text_is_garbled`) on the page text inside `_text_layer_has_content`
    - Return `False` if the text layer exists but is garbled, so the coverage exemption fires and per-picture OCR proceeds
    - Add `TEXT_LAYER_GARBLE_CHECK_ENABLED` env var (default `true`) for rollback
    - _Requirements:_ [RFC-023 D0](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d0-make-_text_layer_has_content-garble-aware-p0-bug) | [Design Property 1](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-1-garble-aware-text-layer-exemption-d0) | [Design Service: converters.py](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#1-converterspy--picturetext-recovery) | [Design Sequence: Garble-Aware Picture Recovery Flow](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#garble-aware-picture-recovery-flow-d0-d1-d2-d3-d11)
  - [X] <a id="12-graceful-marker-splicing-converterspy-d1"></a>1.2 Replace count-mismatch bail-out with ordinal-matched graceful splicing (D1)

    - In `src/pageindex_mcp/converters.py`, rewrite `splice_figure_markers()` (line ~1630-1636): for markers with a matching `PictureResult` by ordinal, splice normally; for excess markers without a matching region, strip them if `STRIP_SKIPPED_IMAGE_MARKERS=true` or leave as a neutral marker
    - Remove the all-or-nothing bail-out that currently returns markdown unchanged on any count mismatch
    - _Requirements:_ [RFC-023 D1](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d1-graceful-degradation-for-splice_figure_markers-count-mismatch-p0-bug) | [Design Property 2](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-2-graceful-marker-splicing-d1) | [Design Service: converters.py](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#1-converterspy--picturetext-recovery) | [Design Sequence: Garble-Aware Picture Recovery Flow](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#garble-aware-picture-recovery-flow-d0-d1-d2-d3-d11)
  - [X] <a id="13-flat-figure-regex-image-marker-recognition-d1"></a>1.3 Recognize raw `<!-- image -->` markers as image blocks in flat extraction (D1)

    - In `src/pageindex_mcp/helpers.py`, extend `_FLAT_FIGURE_RE` (line ~1317) or add a parallel regex so `route_and_extract_flat()` recognizes unresolved `<!-- image -->` markers as image nodes with empty content, not invisible text
    - NOTE: depends on Task 1.2's graceful-splicing rewrite producing well-defined excess markers to recognize
    - _Requirements:_ [RFC-023 D1](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d1-graceful-degradation-for-splice_figure_markers-count-mismatch-p0-bug) | [Design Property 2](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-2-graceful-marker-splicing-d1) | [Design Service: helpers.py](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#2-helperspy--garble-detection--verdict-classification)
  - [X] <a id="14-strip-html-comments-from-garble-detection-d3"></a>1.4 Strip HTML comments before tokenization in `_is_garbled_blob` (D3)

    - In `src/pageindex_mcp/helpers.py`, add `re.sub(r'<!--.*?-->', '', blob)` before the tokenization step in `_is_garbled_blob()` (line ~888-896)
    - Preserve the repetition check for actual garbled text while exempting structural `<!-- ... -->` markers
    - Note (deferred, out of scope): `classify_verdict`'s missing `expected_script` propagation to `_tree_is_garbled`/`_garble_ratio` (line ~1204/1217) is NOT fixed here — tracked as a follow-up per [RFC-023 D3 Known remaining gap](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d3-strip--image---markers-from-garble-detection-p0-bug)
    - _Requirements:_ [RFC-023 D3](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d3-strip--image---markers-from-garble-detection-p0-bug) | [Design Property 4](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-4-image-marker-garble-exemption-d3) | [Design Service: helpers.py](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#2-helperspy--garble-detection--verdict-classification) | [Design Sequence: Garble-Aware Picture Recovery Flow](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#garble-aware-picture-recovery-flow-d0-d1-d2-d3-d11)
  - [X] <a id="15-widen-ocr-escalation-to-structural-failures-d11"></a>1.5 Extend OCR escalation to structural-failure reasons when image-dominant (D11)

    - In `src/pageindex_mcp/client.py`, extend the Fix-3 OCR escalation gate (~line 792) to also fire on `reason in ('node_count<3', 'depth<2')` when markdown content is image-dominant (reusing the >50% image-line ratio check)
    - Change the image-dominant ratio denominator (~line 894) from `len(total_lines)` to `len(non_empty_lines)` so garbled/whitespace lines don't dilute the ratio
    - Add `IMAGE_DOMINANT_OCR_ESCALATION_ENABLED` env var (default `true`) for rollback
    - NOTE: depends on Task 1.1 (D0) — this closes the OCR-escalation gap exposed when D0 causes structural failures instead of the `garbling` reason
    - _Requirements:_ [RFC-023 D11](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d11-widen-ocr-escalation-to-structural-failure-reasons-p1-bug) | [Design Property 12](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-12-structural-failure-ocr-escalation-d11) | [Design Service: client.py](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#3-clientpy--ingestion-orchestration--escalation) | [Design Sequence: Garble-Aware Picture Recovery Flow](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#garble-aware-picture-recovery-flow-d0-d1-d2-d3-d11)
  - [X] 1.6 Unit tests for Tasks 1.1-1.5

    - Write `tests/test_rfc023_d0.py`: (a) garbled text layer returns `False`; (b) clean text layer returns `True`; (c) short text (<20 chars) returns `False` regardless of garble
    - Write `tests/test_rfc023_d1.py`: (a) mismatched counts — matched-ordinal markers spliced, excess stripped; (b) equal counts — all spliced (no regression); (c) `<!-- image -->` recognized by flat extractor as image node
    - Write `tests/test_rfc023_d3.py`: (a) text with only `<!-- image -->` markers is NOT garbled; (b) text with actual repeated tokens still garbled; (c) mixed content with image markers excludes markers from the repetition count
    - Write `tests/test_rfc023_d11.py`: (a) `node_count<3` reason + image-dominant markdown triggers escalation; (b) `node_count<3` + non-image-dominant does not; (c) garbled text lines excluded from ratio denominator; (d) `garbling` reason path unchanged
    - **Property 1: Garble-aware text-layer exemption** — **Validates: [RFC-023 D0](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d0-make-_text_layer_has_content-garble-aware-p0-bug) | [Design Property 1](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-1-garble-aware-text-layer-exemption-d0) | [RFC Test Strategy: D0 row](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#test-strategy)**
    - **Property 2: Graceful marker splicing** — **Validates: [RFC-023 D1](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d1-graceful-degradation-for-splice_figure_markers-count-mismatch-p0-bug) | [Design Property 2](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-2-graceful-marker-splicing-d1) | [RFC Test Strategy: D1 row](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#test-strategy)**
    - **Property 4: Image-marker garble exemption** — **Validates: [RFC-023 D3](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d3-strip--image---markers-from-garble-detection-p0-bug) | [Design Property 4](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-4-image-marker-garble-exemption-d3) | [RFC Test Strategy: D3 row](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#test-strategy)**
    - **Property 12: Structural-failure OCR escalation** — **Validates: [RFC-023 D11](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d11-widen-ocr-escalation-to-structural-failure-reasons-p1-bug) | [Design Property 12](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-12-structural-failure-ocr-escalation-d11) | [RFC Test Strategy: D11 row](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#test-strategy)**
  - [X] <a id="17-checkpoint--batch-1"></a>1.7 Checkpoint — Batch 1: Content Recovery Pipeline

    - Run `uv run pytest tests/test_rfc023_d0.py tests/test_rfc023_d1.py tests/test_rfc023_d3.py tests/test_rfc023_d11.py` and verify all pass
    - Verify [Design Properties 1, 2, 4, 12](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#correctness-properties) hold
    - Spot-check doc 7 (MOU MOHRE), doc 17 (SLA), doc 22 (Unemployment Insurance) locally against [RFC-023 Per-Document Projections](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#per-document-projections)
    - Ask the user if questions arise before proceeding
- [X] <a id="2-batch-2--verdict-hardening-d4-d5"></a>2. Batch 2: Verdict Hardening ([RFC-023 D4](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d4-add-content-quality-guard-to-cat_b_promoted-gate-p0-bug), [D5](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d5-prefer-synthetic-structure-over-rejected-tree-for-flat-routed-docs-p1-bug))

  *[RFC-023 Batch 2: Verdict Hardening](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#batch-2-verdict-hardening-d4-d5----075d) — depends on Batch 1 (D3's fix changes garble detection behavior that D4 relies on). NOTE: depends on [Task 1.4 (D3)](#14-strip-html-comments-from-garble-detection-d3).*

  - [X] <a id="21-content-quality-guard-for-cat_b_promoted-d4"></a>2.1 Add `MIN_FLAT_PROMOTION_CHARS` and placeholder-dominance guards to `cat_b_promoted` (D4)

    - In `src/pageindex_mcp/helpers.py`, add two guards to `cat_b_promoted` gate (~line 1239-1245): (1) `len(flat_text.strip()) >= MIN_FLAT_PROMOTION_CHARS` (default 500), using the already-computed `flat_text` variable; (2) image-placeholder dominance check — reject promotion when the ratio of `<!-- image -->`-matching blocks exceeds 0.5
    - Add `MIN_FLAT_PROMOTION_CHARS` env var (default 500; set to 0 to disable) for rollback
    - _Requirements:_ [RFC-023 D4](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d4-add-content-quality-guard-to-cat_b_promoted-gate-p0-bug) | [Design Property 5](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-5-flat-promotion-content-quality-guard-d4) | [Design Service: helpers.py](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#2-helperspy--garble-detection--verdict-classification) | [Design Sequence: Flat-Routing Verdict Computation Flow](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#flat-routing-verdict-computation-flow-d4-d5)
  - [X] <a id="22-prefer-synthetic-structure-for-flat-routed-docs-d5"></a>2.2 Prefer synthetic structure over rejected tree for flat-routed docs (D5)

    - In `src/pageindex_mcp/client.py`, change the guard at `index()` (~line 1102) from `if not flat_structure and blocks:` to `if blocks:`
    - Ensure the rejected tree structure from `result.get('structure', [])` is never used for verdict computation once blocks exist
    - _Requirements:_ [RFC-023 D5](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d5-prefer-synthetic-structure-over-rejected-tree-for-flat-routed-docs-p1-bug) | [Design Property 6](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-6-synthetic-structure-preference-for-flat-routed-docs-d5) | [Design Service: client.py](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#3-clientpy--ingestion-orchestration--escalation) | [Design Sequence: Flat-Routing Verdict Computation Flow](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#flat-routing-verdict-computation-flow-d4-d5)
  - [X] <a id="23-batch-2-unit-tests"></a>2.3 Unit tests for Tasks 2.1-2.2

    - Write `tests/test_rfc023_d4.py`: (a) 15 `<!-- image -->` blocks, 210 chars — `cat_b_promoted` blocked; (b) 15 real-text blocks, 5000 chars — `cat_b_promoted` passes; (c) placeholder ratio > 0.5 — blocked
    - Write `tests/test_rfc023_d5.py`: (a) non-empty rejected structure + blocks — synthetic built from blocks; (b) no blocks — original structure preserved; (c) synthetic structure depth/node_count correct
    - **Property 5: Flat-promotion content-quality guard** — **Validates: [RFC-023 D4](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d4-add-content-quality-guard-to-cat_b_promoted-gate-p0-bug) | [Design Property 5](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-5-flat-promotion-content-quality-guard-d4) | [RFC Test Strategy: D4 row](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#test-strategy)**
    - **Property 6: Synthetic-structure preference for flat-routed docs** — **Validates: [RFC-023 D5](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d5-prefer-synthetic-structure-over-rejected-tree-for-flat-routed-docs-p1-bug) | [Design Property 6](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-6-synthetic-structure-preference-for-flat-routed-docs-d5) | [RFC Test Strategy: D5 row](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#test-strategy)**
  - [X] <a id="24-checkpoint--batch-2"></a>2.4 Checkpoint — Batch 2: Verdict Hardening

    - Run `uv run pytest tests/test_rfc023_d4.py tests/test_rfc023_d5.py` and verify all pass
    - Verify [Design Properties 5, 6](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#correctness-properties) hold
    - Spot-check doc 20 (Labor Exec. Regs.) and doc 21 (Domestic Workers) against [RFC-023 Per-Document Projections](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#per-document-projections)
    - Cross-reference [Checkpoint 1.7](#17-checkpoint--batch-1) passed before proceeding
    - Ask the user if questions arise before proceeding
- [X] <a id="3-batch-3--edge-case-fixes-d2-d6"></a>3. Batch 3: Edge-Case Fixes ([RFC-023 D2](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d2-decorative-icon-bbox-classifier-for-sub-icon-pictureitems-p1-missing-feature), [D6](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d6-page-rotation-correction-for-per-picture-ocr-p1-bug))

  *[RFC-023 Batch 3: Edge-Case Fixes](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#batch-3-edge-case-fixes-d2-d6----075d) — D2 modifies the splice/strip path rewritten by [Task 1.2](#12-graceful-marker-splicing-converterspy-d1) in Batch 1 and must be rebased on it; D6 is independent and may start in parallel with Batch 2.*

  - [X] <a id="31-decorative-icon-bbox-classifier-d2"></a>3.1 Add bbox-area pre-filter and wire `decorative` field (D2)

    - In `src/pageindex_mcp/converters.py`, add a bbox-area pre-filter in `_recover_picture_text()` (~line 1487-1519): if a region's width AND height are both below `DECORATIVE_ICON_MIN_DIM_PT` (default 20pt), set `skip_reasons[i] = "decorative_icon"` and skip crop+OCR
    - As belt-and-suspenders, set `decorative=True` on any `PictureResult` where OCR yields empty `ocr_text` AND no `description` AND `page.rotation == 0`
    - Gate the belt-and-suspenders path strictly on `page.rotation == 0` so it never fires on rotated pages (defers to [Task 3.2 (D6)](#32-page-rotation-correction-for-picture-ocr-d6))
    - NOTE: rebase on [Task 1.2](#12-graceful-marker-splicing-converterspy-d1)'s rewritten splice/strip path — both consume `skip_reasons`/`decorative` in `splice_figure_markers` (line ~1647)
    - Add `DECORATIVE_ICON_MIN_DIM_PT` env var (default 20; set to 0 to disable)
    - _Requirements:_ [RFC-023 D2](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d2-decorative-icon-bbox-classifier-for-sub-icon-pictureitems-p1-missing-feature) | [Design Property 3](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-3-decorative-icon-suppression-d2) | [Design Service: converters.py](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#1-converterspy--picturetext-recovery) | [Design Sequence: Garble-Aware Picture Recovery Flow](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#garble-aware-picture-recovery-flow-d0-d1-d2-d3-d11)
  - [X] <a id="32-page-rotation-correction-for-picture-ocr-d6"></a>3.2 Save/zero/restore `page.rotation` around `get_pixmap` (D6)

    - In `src/pageindex_mcp/converters.py`, in `_recover_picture_text()` (~line 1490-1520), before calling `page.get_pixmap()`, save and temporarily zero the page rotation (`orig = page.rotation; page.set_rotation(0)`); restore in a `finally` block after pixmap extraction
    - Ensure restoration happens regardless of whether OCR succeeds or raises
    - _Requirements:_ [RFC-023 D6](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d6-page-rotation-correction-for-per-picture-ocr-p1-bug) | [Design Property 7](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-7-rotation-corrected-picture-ocr-d6) | [Design Service: converters.py](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#1-converterspy--picturetext-recovery)
  - [X] <a id="33-batch-3-unit-tests"></a>3.3 Unit tests for Tasks 3.1-3.2

    - Write `tests/test_rfc023_d2.py`: (a) bbox < 20pt both dims — `skip_reasons` set to `decorative_icon`; (b) bbox > 20pt — proceeds to OCR; (c) zero-yield OCR — `decorative=True` set; (d) strip logic fires for decorative results
    - Write `tests/test_rfc023_d6.py`: (a) `page.rotation=180` — rotation zeroed before pixmap, restored after; (b) `page.rotation=0` — no change; (c) mock Tesseract receives unrotated image
    - Add explicit D2/D6 interaction test: empty-OCR region on `page.rotation != 0` must NOT set `decorative=True`
    - **Property 3: Decorative-icon suppression** — **Validates: [RFC-023 D2](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d2-decorative-icon-bbox-classifier-for-sub-icon-pictureitems-p1-missing-feature) | [Design Property 3](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-3-decorative-icon-suppression-d2) | [RFC Test Strategy: D2 row](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#test-strategy)**
    - **Property 7: Rotation-corrected picture OCR** — **Validates: [RFC-023 D6](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d6-page-rotation-correction-for-per-picture-ocr-p1-bug) | [Design Property 7](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-7-rotation-corrected-picture-ocr-d6) | [RFC Test Strategy: D6 row](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#test-strategy)**
  - [X] <a id="34-checkpoint--batch-3"></a>3.4 Checkpoint — Batch 3: Edge-Case Fixes

    - Run `uv run pytest tests/test_rfc023_d2.py tests/test_rfc023_d6.py` and verify all pass
    - Verify [Design Properties 3, 7](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#correctness-properties) hold, including the D2/D6 interaction guard
    - Spot-check doc 9 (Unfallversicherung) and doc 15 (UAE numbers portrait) against [RFC-023 Per-Document Projections](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#per-document-projections)
    - Ask the user if questions arise before proceeding
- [X] <a id="4-batch-4--standalone-image--vlm-fallback--error-mapping-d8a-d7-d8b"></a>4. Batch 4: Standalone Image + VLM Fallback + Error Mapping ([RFC-023 D8a](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d8-standalone-image-ocr-enrichment--worker-error-mapping-p1-bug--p2-improvement), [D7](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d7-tesseract-on-raster-fallback-when-vlm-crashes-on-garbled-pdfs-p2-missing-feature), [D8b](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d8-standalone-image-ocr-enrichment--worker-error-mapping-p1-bug--p2-improvement))

  *[RFC-023 Batch 4: Standalone Image + VLM Fallback + Error Mapping](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#batch-4-standalone-image--vlm-fallback--error-mapping-d8a-d7-d8b----175d) — depends on Batch 1 (D0's garble-aware text-layer check is reused in D7's fallback path). NOTE: depends on [Task 1.1](#11-garble-aware-text-layer-exemption-d0).*

  - [X] <a id="41-standalone-image-ocr-enrichment-d8a"></a>4.1 Populate synthetic `PictureResult.ocr_text` via Tesseract for standalone images (D8a)

    - In `src/pageindex_mcp/client.py`'s `_IMAGE_EXTS` route (~line 740-768), run raw image bytes through Tesseract OCR and populate the synthetic `PictureResult.ocr_text`
    - Skip the Tesseract step when `md_content` already contains more than `MIN_STANDALONE_IMAGE_MD_CHARS` (default 100) non-whitespace characters, to avoid double-counting content Docling already extracted
    - Add `MIN_STANDALONE_IMAGE_MD_CHARS` env var (default 100)
    - _Requirements:_ [RFC-023 D8](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d8-standalone-image-ocr-enrichment--worker-error-mapping-p1-bug--p2-improvement) | [Design Property 9](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-9-standalone-image-ocr-enrichment--terminal-error-classification-d8) | [Design Service: client.py](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#3-clientpy--ingestion-orchestration--escalation)
  - [X] <a id="42-tesseract-on-raster-vlm-fallback-d7"></a>4.2 Add Tesseract-on-raster fallback in VLM exception handler (D7)

    - In `src/pageindex_mcp/client.py`'s VLM exception handler (~line 872-878), reuse rasterized page images and run Tesseract OCR when the VLM call raises
    - If the OCR text passes `_is_garbled_blob` (returns `False`), use it as `flat_md` and override `reason` to `'node_count<3'` to enter the existing flat success path (~line 954)
    - Do NOT add `'garbling'` itself to the flat-routing reason check — the reason override is the sole routing mechanism, preserving [CLAUDE.md HR5](../../CLAUDE.md#hard-rules): genuinely garbled, non-recovered documents must still raise `LowQualityTreeError`
    - If Tesseract OCR also fails garble checks or produces insufficient content, preserve the original `LowQualityTreeError('garbling')` path
    - Add `VLM_TESSERACT_FALLBACK_ENABLED` env var (default `true`)
    - _Requirements:_ [RFC-023 D7](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d7-tesseract-on-raster-fallback-when-vlm-crashes-on-garbled-pdfs-p2-missing-feature) | [Design Property 8](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-8-tesseract-on-raster-vlm-fallback-d7) | [Design Service: client.py](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#3-clientpy--ingestion-orchestration--escalation) | [Design Sequence: VLM-Crash Tesseract Fallback Flow](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#vlm-crash-tesseract-fallback-flow-d7) | [CLAUDE.md HR5](../../CLAUDE.md#hard-rules)
  - [X] <a id="43-llm-failure-terminal-vs-transient-classification-d8b"></a>4.3 Add `_classify_llm_failure` helper; terminal for CMap/content-policy, transient for rate-limits (D8b)

    - In `src/pageindex_mcp/worker.py`, add `LLMTransientFailure` to `_CHILD_ERROR_REASON` mapping (~line 67-72), routed through a new `_classify_llm_failure(stderr_tail)` helper
    - `_classify_llm_failure` returns `'llm_failure_terminal'` if `stderr_tail` contains `"CMap"`, `"content_policy"`, or `"content_filter"`; otherwise returns `'llm_failure_transient'` (covers `"rate_limit"`, `"429"`, `"throttl"`, and any unrecognized detail — fails open toward retry)
    - Add `'llm_failure_terminal'` (only, not `'llm_failure_transient'`) to `_TERMINAL_CHILD_REASONS` so rate-limit errors remain eligible for arq retry (MAX_TRIES=2)
    - No change needed to `ConverterChildError.__init__` (line ~144) — `stderr_tail` (line ~146, sourced from the 2000-char tail at line ~225) already retains full error detail independent of the 200-char message truncation, and is already surfaced at lines ~316/347
    - _Requirements:_ [RFC-023 D8](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d8-standalone-image-ocr-enrichment--worker-error-mapping-p1-bug--p2-improvement) | [Design Property 9](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-9-standalone-image-ocr-enrichment--terminal-error-classification-d8) | [Design Service: worker.py](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#4-workerpy--arq-error-mapping)
  - [X] <a id="44-batch-4-unit-tests"></a>4.4 Unit tests for Tasks 4.1-4.3

    - Write `tests/test_rfc023_d7.py`: (a) VLM exception + Tesseract success (non-garbled OCR) — reason overridden to `node_count<3`, flat routing succeeds; (b) VLM exception + Tesseract failure (garbled OCR) — `LowQualityTreeError` raised; (c) VLM exception + Tesseract empty output — `LowQualityTreeError` raised; (d) `garbling` reason without VLM exception — existing escalation path unchanged
    - Write `tests/test_rfc023_d8.py`: (a) standalone `.jpg` — `PictureResult.ocr_text` populated; (a2) standalone `.jpg` with sufficient `md_content` — Tesseract OCR skipped, no double-counting; (b) `LLMTransientFailure` with CMap error — maps to terminal reason, no retry; (b2) `LLMTransientFailure` with rate_limit/429 — maps to transient reason, eligible for retry
    - Add explicit D8b boundary test: `stderr_tail` containing both a rate-limit indicator AND a CMap indicator — terminal classification must take precedence
    - **Property 8: Tesseract-on-raster VLM fallback** — **Validates: [RFC-023 D7](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d7-tesseract-on-raster-fallback-when-vlm-crashes-on-garbled-pdfs-p2-missing-feature) | [Design Property 8](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-8-tesseract-on-raster-vlm-fallback-d7) | [RFC Test Strategy: D7 row](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#test-strategy)**
    - **Property 9: Standalone-image OCR enrichment + terminal-error classification** — **Validates: [RFC-023 D8](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d8-standalone-image-ocr-enrichment--worker-error-mapping-p1-bug--p2-improvement) | [Design Property 9](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-9-standalone-image-ocr-enrichment--terminal-error-classification-d8) | [RFC Test Strategy: D8 row](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#test-strategy)**
  - [X] <a id="45-checkpoint--batch-4"></a>4.5 Checkpoint — Batch 4: Standalone Image + VLM Fallback + Error Mapping

    - Run `uv run pytest tests/test_rfc023_d7.py tests/test_rfc023_d8.py` and verify all pass
    - Verify [Design Properties 8, 9](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#correctness-properties) hold
    - Spot-check doc 13 (pie chart JPG) and doc 18 (Organizational Decision) against [RFC-023 Per-Document Projections](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#per-document-projections)
    - Ask the user if questions arise before proceeding
- [X] <a id="5-batch-5--bidi--threshold-widening-d9-d10"></a>5. Batch 5: BiDi + Threshold Widening ([RFC-023 D9](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d9-bidi-early-return-heading-marker-preservation-p2-bug), [D10](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d10-extraction-pinning-for-non-deterministic-docling-documents-p3-data-quality))

  *[RFC-023 Batch 5: BiDi + Threshold Widening](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#batch-5-bidi--threshold-widening-d9-d10----10d) — independent of Batches 1-4; may run in parallel with any prior batch.*

  - [X] <a id="51-bidi-heading-marker-preservation-d9"></a>5.1 Split `reconstruct_bidi_order` early-return; always apply heading-marker preservation (D9)

    - In `src/pageindex_mcp/converters.py`, split the early-return in `reconstruct_bidi_order()` (~line 1249-1265) into two paths: (1) always apply `_BIDI_HEADING_PREFIX_RE` (line ~1270) to extract and preserve heading markers, even when bulk text is detected as logical via `_text_is_logical_order()` (~line 1204-1232); (2) conditionally apply full-document BiDi reordering based on the existing checks
    - Preserve the existing performance optimization for pure-English/logical-order text
    - _Requirements:_ [RFC-023 D9](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d9-bidi-early-return-heading-marker-preservation-p2-bug) | [Design Property 10](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-10-bidi-heading-marker-preservation-d9) | [Design Service: converters.py](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#1-converterspy--picturetext-recovery)
  - [X] <a id="52-widen-pass_max_leaf_ratio-threshold-d10"></a>5.2 Widen `PASS_MAX_LEAF_RATIO` default from 0.17 to 0.20 (D10)

    - In `src/pageindex_mcp/helpers.py`, change the `PASS_MAX_LEAF_RATIO` env-var default from 0.17 to 0.20 in `classify_verdict`
    - No other code changes required — this is a single env-var default change absorbing Docling's run-to-run heading-selection jitter
    - _Requirements:_ [RFC-023 D10](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d10-extraction-pinning-for-non-deterministic-docling-documents-p3-data-quality) | [Design Property 11](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-11-extraction-jitter-threshold-widening-d10) | [Design Service: helpers.py](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#2-helperspy--garble-detection--verdict-classification)
  - [X] <a id="53-batch-5-unit-tests"></a>5.3 Unit tests for Tasks 5.1-5.2

    - Write `tests/test_rfc023_d9.py`: (a) bilingual doc with Arabic headings — heading markers preserved after BiDi; (b) pure-English doc — early-return still fires (perf preserved); (c) logical-order Arabic — full reorder skipped but headings preserved
    - Write `tests/test_rfc023_d10.py`: (a) `max_leaf_ratio` 0.18 with `PASS_MAX_LEAF_RATIO=0.20` — verdict is PASS; (b) `max_leaf_ratio` 0.22 with `PASS_MAX_LEAF_RATIO=0.20` — verdict is MARGINAL; (c) `max_leaf_ratio` 0.16 — verdict is PASS regardless of threshold
    - **Property 10: BiDi heading-marker preservation** — **Validates: [RFC-023 D9](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d9-bidi-early-return-heading-marker-preservation-p2-bug) | [Design Property 10](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-10-bidi-heading-marker-preservation-d9) | [RFC Test Strategy: D9 row](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#test-strategy)**
    - **Property 11: Extraction-jitter threshold widening** — **Validates: [RFC-023 D10](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d10-extraction-pinning-for-non-deterministic-docling-documents-p3-data-quality) | [Design Property 11](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-11-extraction-jitter-threshold-widening-d10) | [RFC Test Strategy: D10 row](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#test-strategy)**
  - [X] <a id="54-checkpoint--batch-5"></a>5.4 Checkpoint — Batch 5: BiDi + Threshold Widening

    - Run `uv run pytest tests/test_rfc023_d9.py tests/test_rfc023_d10.py` and verify all pass
    - Verify [Design Properties 10, 11](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#correctness-properties) hold
    - Spot-check doc 5 (Haftpflicht-Besondere) and doc 6 (Ministerial Resolution 279) against [RFC-023 Per-Document Projections](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#per-document-projections)
    - Ask the user if questions arise before proceeding
- [x] <a id="6-batch-6--full-reaudit"></a>6. Batch 6: Full Reaudit (all decisions)

  *[RFC-023 Batch 6: Full Reaudit](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#batch-6-full-reaudit-all----05d) — depends on all of Batches 1-5 completing and passing their checkpoints.*

  - [x] <a id="61-bump-pipeline-version-and-reingest-corpus"></a>6.1 Bump `CURRENT_PIPELINE_VERSION`; full 25-doc reingestion

    - Bump `CURRENT_PIPELINE_VERSION` to invalidate cached pipeline results
    - Wipe all derived stores (MinIO, Redis, PostgreSQL) per the corpus-reaudit methodology
    - Run `uv run python preprocess_client.py --bg` (or foreground) against the full 25-doc corpus in `doc_store/`
    - NOTE: depends on [Checkpoint 1.7](#17-checkpoint--batch-1), [Checkpoint 2.4](#24-checkpoint--batch-2), [Checkpoint 3.4](#34-checkpoint--batch-3), [Checkpoint 4.5](#45-checkpoint--batch-4), [Checkpoint 5.4](#54-checkpoint--batch-5) all passing
    - _Requirements:_ [RFC-023 Batch 6](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#batch-6-full-reaudit-all----05d)
  - [x] <a id="62-run-7-scorecard-and-regression-verification"></a>6.2 Run 7 scorecard vs projections; verify zero regressions on Run 6 PASS docs

    - Produce a Run 7 scorecard and compare against the [RFC-023 Projected Run 7 Verdict Distribution](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#projected-run-7-verdict-distribution) (18-20 PASS, 3-5 MARGINAL, 1-2 FAIL, 0-1 ERROR) and [Per-Document Projections](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#per-document-projections) table
    - Explicitly verify all 11 Run-6 PASS docs retain PASS in Run 7, per [RFC-023 Risk: Run 7 regression on Run 6 PASS docs](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#risk-assessment)
    - Write the Run 7 scorecard to `audit/` following the existing corpus-audit report convention
    - _Requirements:_ [RFC-023 Expected Outcomes](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#expected-outcomes)
  - [x] <a id="63-final-checkpoint--full-corpus-reaudit"></a>6.3 Final checkpoint — Full corpus reaudit

    - Run `uv run pytest` (full suite) and verify all [Design Properties 1-12](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#correctness-properties) pass
    - Verify Run 7 scorecard shows zero regressions on the 11 Run-6 PASS docs (Task 6.2)
    - Verify the per-document projections in [RFC-023 Per-Document Projections](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#per-document-projections) are met or exceeded
    - Ask the user if questions arise before proceeding

## Notes

- Doc 3 (GHV-TKV-Tarif) is explicitly out of scope and stays MARGINAL — do not attempt to fix it as part of this RFC; see [RFC-023 Per-Document Projections](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#per-document-projections).
- D3's `expected_script` propagation gap (script-agnostic garble detection inflating `garble_ratio` for Arabic flat text) is deliberately deferred to a follow-up RFC — do not expand [Task 1.4](#14-strip-html-comments-from-garble-detection-d3)'s scope to cover it; see [RFC-023 D3 Known remaining gap](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d3-strip--image---markers-from-garble-detection-p0-bug).
- [Task 3.1 (D2)](#31-decorative-icon-bbox-classifier-d2) MUST be rebased on [Task 1.2 (D1)](#12-graceful-marker-splicing-converterspy-d1)'s rewritten splice/strip path — both tasks touch `splice_figure_markers`'s strip logic.
- [Task 3.1 (D2)](#31-decorative-icon-bbox-classifier-d2)'s belt-and-suspenders `decorative=True` heuristic MUST be gated on `page.rotation == 0` to avoid masking [Task 3.2 (D6)](#32-page-rotation-correction-for-picture-ocr-d6)'s rotation-caused OCR failures as decorative icons; see [RFC-023 D2 Interaction with D6](../rfcs/023-run6-content-recovery-and-verdict-hardening.md#d2-decorative-icon-bbox-classifier-for-sub-icon-pictureitems-p1-missing-feature).
- [Task 4.2 (D7)](#42-tesseract-on-raster-vlm-fallback-d7) MUST NOT add `'garbling'` to the flat-routing reason check directly — the reason override to `'node_count<3'` is the sole routing mechanism, preserving [CLAUDE.md HR5](../../CLAUDE.md#hard-rules) (no silent low-quality persistence).
- Every fix in Batches 1-5 ships with a named env var defaulting to the fixed behavior (see each task's rollback note) — this permits isolating a regression to a single fix during Batch 6's reaudit without a full revert.
- Property-based tests are not used at MVP scope for this RFC (see [Design § Property-Based Testing Configuration](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md#property-based-testing-configuration)) — all `*`-marked tasks are targeted unit tests against exact fixture values, not generated inputs.
- Tests marked with `*` are still required (not optional) for this RFC given the Hard Rule 5 quality-gate implications of D0/D3/D4/D7/D11 — do not skip them for a faster MVP.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.4"] },
    { "id": 1, "tasks": ["1.2", "1.5"], "depends_on": { "1.2": [], "1.5": ["1.1"] } },
    { "id": 2, "tasks": ["1.3", "1.6"], "depends_on": { "1.3": ["1.2"], "1.6": ["1.1", "1.2", "1.3", "1.4", "1.5"] } },
    { "id": 3, "tasks": ["1.7"], "depends_on": { "1.7": ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6"] } },
    { "id": 4, "tasks": ["2.1", "2.2", "3.2"], "depends_on": { "2.1": ["1.4"], "2.2": ["1.7"], "3.2": [] } },
    { "id": 5, "tasks": ["2.3", "3.1"], "depends_on": { "2.3": ["2.1", "2.2"], "3.1": ["1.2"] } },
    { "id": 6, "tasks": ["2.4", "3.3"], "depends_on": { "2.4": ["2.1", "2.2", "2.3"], "3.3": ["3.1", "3.2"] } },
    { "id": 7, "tasks": ["3.4", "4.1", "4.2", "4.3"], "depends_on": { "3.4": ["3.1", "3.2", "3.3"], "4.1": ["1.7"], "4.2": ["1.1"], "4.3": [] } },
    { "id": 8, "tasks": ["4.4"], "depends_on": { "4.4": ["4.1", "4.2", "4.3"] } },
    { "id": 9, "tasks": ["4.5", "5.1", "5.2"], "depends_on": { "4.5": ["4.4"], "5.1": [], "5.2": [] } },
    { "id": 10, "tasks": ["5.3"], "depends_on": { "5.3": ["5.1", "5.2"] } },
    { "id": 11, "tasks": ["5.4"], "depends_on": { "5.4": ["5.1", "5.2", "5.3"] } },
    { "id": 12, "tasks": ["6.1"], "depends_on": { "6.1": ["1.7", "2.4", "3.4", "4.5", "5.4"] } },
    { "id": 13, "tasks": ["6.2"], "depends_on": { "6.2": ["6.1"] } },
    { "id": 14, "tasks": ["6.3"], "depends_on": { "6.3": ["6.2"] } }
  ]
}
```
