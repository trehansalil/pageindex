<!-- Space: CITRA -->

<!-- Title: Implementation Plan: RFC-024 Run 7 Verdict Stability & Recovery Gaps -->

<!-- Folder: Tasks -->

# Implementation Plan: RFC-024 Run 7 Verdict Stability & Recovery Gaps

## Traceability

| Artifact               | Reference                                                                                                                        |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Governing RFC(s)       | [RFC-024: Run 7 Verdict Stability &amp; Recovery Gaps](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md)                   |
| Design Document        | [design-rfc024-run7-verdict-stability-and-recovery-gaps.md](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md) |
| Hard Rules (binding)   | [CLAUDE.md § Hard Rules](../../CLAUDE.md#hard-rules)                                                                             |
| Implementation Order   | [RFC-024 Implementation Plan](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#implementation-plan)                        |
| Test Strategy          | [RFC-024 Test Strategy](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#test-strategy)                                    |
| Correctness Properties | [Design § Correctness Properties](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#correctness-properties)   |

## Overview

This plan implements the 7 in-scope defect fixes from [RFC-024&#39;s Implementation Plan](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#implementation-plan) across `converters.py`, `helpers.py`, `client.py`, and the agent-driven corpus-cycle/corpus-score-diff skill prompts, organized into five batches matching the RFC's own batching and dependency ordering. Batch 1 lands the picture-recovery resilience fixes (D2's crash isolation lands first as a prerequisite for D1's clip-text capture to work correctly on documents with mixed good/bad regions). Batch 2 lands the independent splitter and threshold-widening fixes (D0, D3). Batch 3 lands the rasterization dual-backend and VLM-garble recovery fixes, where D4 must land before D5 since D5's extracted helper calls D4's dual-backend rasterization function. Batch 4 lands the audit-tooling char-count fix (D6), independent of Batches 1-3 and runnable in parallel. Batch 5 runs the full 25-doc Run 8 reaudit and verifies the projected verdict distribution with zero regressions on Run 7 PASS docs. Each task validates one or more of the [7 correctness properties](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#correctness-properties) defined in the design document.

## Tasks

- [X] <a id="1-batch-1--pipeline-resilience--content-recovery-d1-d2"></a>1. Batch 1: Pipeline Resilience & Content Recovery ([RFC-024 D1](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d1-capture-clip_text-into-pictureresult-when-docling-misclassifies-text-as-images-p0-missing-feature), [D2](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d2-per-region-tryexcept-in-phase-1-crop-loop-p0-bug))

  *[RFC-024 Batch 1: Pipeline Resilience &amp; Content Recovery](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#batch-1-pipeline-resilience--content-recovery-d1-d2----15d) — D2 (crash isolation) is a prerequisite for D1's clip_text capture to work correctly on documents with mixed good/bad regions; both modify `converters.py` in non-overlapping code paths.*

  - [X] <a id="11-per-region-tryexcept-in-phase-1-crop-loop-d2"></a>1.1 Wrap per-region body in Phase 1 crop loop with try/except (D2)

    - In `src/pageindex_mcp/converters.py`, wrap the per-region body of the Phase 1 crop loop (~line 1518-1566) — specifically the `page.get_pixmap(clip=rect, dpi=300)` call — in a `try/except Exception`
    - On exception, log a warning with the region index and error detail, set `skip_reasons[i] = 'crop_error'`, and `continue` to the next region without aborting the loop
    - Leave the outer except in `_recover_picture_results` (~line 1766) in place as a last-resort guard for the all-regions-fail case
    - _Requirements:_ [RFC-024 D2](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d2-per-region-tryexcept-in-phase-1-crop-loop-p0-bug) | [Design Property 3](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-3-per-region-crop-isolation-d2) | [Design Service: converters.py](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#1-converterspy--picture-recovery--rasterization) | [Design Sequence: Picture Recovery Resilience Flow](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#picture-recovery-resilience-flow-d1-d2)
  - [X] <a id="12-clip-text-capture-with-containment-guard-d1"></a>1.2 Capture `clip_text` into `PictureResult.ocr_text` with containment guard (D1)

    - In `src/pageindex_mcp/converters.py`, add `_normalize_for_containment(text: str) -> str` (NFKC-fold + whitespace-collapse + lowercase), computed once per page against the full Docling-exported markdown body (not per region, to avoid O(n²) re-normalization)
    - In `_recover_picture_text`, when a region's `clip_text` (via `page.get_text('text', clip=rect)`) exceeds `_PICTURE_OCR_MIN_CHARS` (~line 1540-1544 skip path), normalize it and test whether ≥60% of its normalized chars appear as a substring of the once-per-page-normalized markdown body
    - If contained ≥60%: skip as today with `reason='clip_text_already_exported'`
    - If NOT contained ≥60%: capture `clip_text` into `PictureResult.ocr_text` with `reason='clip_text_captured'`; do NOT proceed to Tesseract OCR for that region
    - If `clip_text` is empty/below `_PICTURE_OCR_MIN_CHARS`: proceed to the existing Tesseract OCR path (~line 1574-1585) unchanged
    - Add `CLIP_TEXT_CAPTURE_ENABLED` env var (default `true`) for rollback
    - NOTE: depends on [Task 1.1](#11-per-region-tryexcept-in-phase-1-crop-loop-d2) — clip-text capture on mixed good/bad-region documents relies on D2's crash isolation to reach every healthy region
    - _Requirements:_ [RFC-024 D1](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d1-capture-clip_text-into-pictureresult-when-docling-misclassifies-text-as-images-p0-missing-feature) | [Design Property 2](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-2-clip-text-capture-with-containment-guard-d1) | [Design Service: converters.py](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#1-converterspy--picture-recovery--rasterization) | [Design Sequence: Picture Recovery Resilience Flow](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#picture-recovery-resilience-flow-d1-d2)
  - [X] <a id="13-batch-1-unit-tests"></a>1.3 Unit tests for Tasks 1.1-1.2 + document-level text-layer fallback (D1)

    - Write `tests/test_rfc024_d2.py`: (a) single degenerate region raises `Exception` — only that region skipped, others proceed; (b) `skip_reasons[i] = 'crop_error'` recorded; (c) ordinal density preserved (no shift in surviving regions' indices); (d) all regions fail — empty result returned gracefully via the outer except
    - Write `tests/test_rfc024_d1.py`: (a) `PictureItem` region with meaningful `clip_text` NOT present in exported markdown (containment <60%) — `ocr_text` populated via `clip_text_captured`; (b) region with empty `clip_text` — proceeds to Tesseract OCR; (c) image-dominant page (<100 chars excluding markers) — full-page text-layer fallback fires (document-level fallback, second half of D1's fix); (d) region `clip_text` where ≥60% normalized content already appears in the Docling markdown body — skip with `reason='clip_text_already_exported'` (no double-capture); (e) containment check is robust to whitespace/reflow differences (NFKC + whitespace-collapse + lowercase)
    - Implement the document-level text-layer fallback in `converters.py`: when Docling markdown is <100 chars excluding `<!-- image -->` markers, read the full page text layer via pypdfium2 and use it as supplementary content
    - **Property 2: Clip-text capture with containment guard** — **Validates: [RFC-024 D1](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d1-capture-clip_text-into-pictureresult-when-docling-misclassifies-text-as-images-p0-missing-feature) | [Design Property 2](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-2-clip-text-capture-with-containment-guard-d1) | [RFC Test Strategy: D1 row](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#test-strategy)**
    - **Property 3: Per-region crop isolation** — **Validates: [RFC-024 D2](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d2-per-region-tryexcept-in-phase-1-crop-loop-p0-bug) | [Design Property 3](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-3-per-region-crop-isolation-d2) | [RFC Test Strategy: D2 row](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#test-strategy)**
  - [X] <a id="14-checkpoint--batch-1"></a>1.4 Checkpoint — Batch 1: Pipeline Resilience & Content Recovery

    - Run `uv run pytest tests/test_rfc024_d1.py tests/test_rfc024_d2.py` and verify all pass
    - Verify [Design Properties 2, 3](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#correctness-properties) hold
    - Spot-check doc 14 (UAE landscape) against the [RFC-024 Projected Run 8 Verdict Changes](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#projected-run-8-verdict-changes) table (expected: FAIL/MARGINAL → MARGINAL)
    - Ask the user if questions arise before proceeding
- [x] <a id="2-batch-2--splitter--threshold-hardening-d0-d3"></a>2. Batch 2: Splitter & Threshold Hardening ([RFC-024 D0](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d0-widen-pass_max_leaf_ratio-default-from-020-to-030-p1-bug), [D3](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d3-extend-ordinal-splitter-regex-for-moudecree-documents-p1-missing-feature))

  *[RFC-024 Batch 2: Splitter &amp; Threshold Hardening](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#batch-2-splitter--threshold-hardening-d0-d3----15d) — independent of Batch 1; both fixes land in `helpers.py` and may proceed in parallel with Batch 1.*

  - [X] <a id="21-widen-pass_max_leaf_ratio-default-from-020-to-030-d0"></a>2.1 Widen `PASS_MAX_LEAF_RATIO` default from 0.20 to 0.30 (D0)

    - In `src/pageindex_mcp/helpers.py`, change the `PASS_MAX_LEAF_RATIO` env-var default from 0.20 to 0.30 in `classify_verdict()` (~line 1229-1236)
    - No other code changes required — this is a single env-var default change absorbing Doc 8's observed jitter range (0.17-0.2571); the `max_leaf_ratio > 0.75` hard-FAIL gate is untouched
    - _Requirements:_ [RFC-024 D0](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d0-widen-pass_max_leaf_ratio-default-from-020-to-030-p1-bug) | [Design Property 1](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-1-pass_max_leaf_ratio-threshold-widening-d0) | [Design Service: helpers.py](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#2-helperspy--verdict-threshold--ordinal-splitter) | [Design Sequence: Ordinal Splitter &amp; Paragraph-Boundary Fallback Flow](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#ordinal-splitter--paragraph-boundary-fallback-flow-d0-d3)
  - [X] <a id="22-extend-_oversized_ordinal_re-and-_ordinal_value-d3"></a>2.2 Extend `_OVERSIZED_ORDINAL_RE` with Clause/Part/Annex/`بند`/`باب` patterns; update `_ordinal_value()` (D3)

    - In `src/pageindex_mcp/helpers.py`, extend `_OVERSIZED_ORDINAL_RE` (~line 1438-1446) with case-insensitive named groups: `Clause\s+\(?\s*(?P<clause>\d+(?:\.\d+)?)`, `Part\s+\(?\s*(?P<part>(?:[IVX]+|\d+)(?:\.\d+)?)`, `بند\s*\(?\s*(?P<band>[\d٠-٩]+(?:[.٫][\d٠-٩]+)?)`, `باب\s*\(?\s*(?P<bab>[\d٠-٩]+(?:[.٫][\d٠-٩]+)?)`, `Annex\s+\(?\s*(?P<annex>[A-Z]|\d+(?:\.\d+)?)`
    - Update `_ordinal_value()` (~line 1493-1507) to dispatch on capture-group name: `clause`/`band`/`bab` follow the existing Arabic-Indic-map-then-`int()` path; `part` tries `int()` first, then falls back to a new `_roman_to_int(s: str) -> int` helper for `[IVX]+` tokens (covers Part I through Part XXXIX); `annex` tries `int()` first, then falls back to `ord(ch) - ord('A') + 1` for bare Latin letters
    - Update `_has_heading_markers()` (~line 1672-1684) to recognize the new markers as heading signals
    - Ensure no group ever raises `ValueError` from `int('IV')` or `int('A')` — the try-int-then-fallback dispatch must catch and route correctly for every new group
    - Existing `Article`/`Section`/`Schedule`/`مادة` patterns and their conversion paths remain unchanged (regression guard)
    - _Requirements:_ [RFC-024 D3](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d3-extend-ordinal-splitter-regex-for-moudecree-documents-p1-missing-feature) | [Design Property 4](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-4-extended-ordinal-splitter-recognition-d3) | [Design Service: helpers.py](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#2-helperspy--verdict-threshold--ordinal-splitter) | [Design Sequence: Ordinal Splitter &amp; Paragraph-Boundary Fallback Flow](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#ordinal-splitter--paragraph-boundary-fallback-flow-d0-d3)
  - [X] <a id="23-leaf_concentration-aware-paragraph-boundary-splitting-fallback-d3"></a>2.3 (Lower priority) Add leaf_concentration-aware paragraph-boundary splitting fallback (D3)

    - In `src/pageindex_mcp/helpers.py`'s `split_oversized_leaf_nodes()` (~line 1687-1764), add a secondary splitting strategy for leaves with high `leaf_concentration` even under 50k chars: split on paragraph boundaries (blank-line-separated blocks) when the tree's `max_leaf_ratio` exceeds `PASS_MAX_LEAF_RATIO` — the SAME env var as [Task 2.1](#21-widen-pass_max_leaf_ratio-default-from-020-to-030-d0) (default 0.30), not an independently hard-coded threshold — and ordinal splitting (Task 2.2) fails
    - Add `LEAF_CONCENTRATION_PARAGRAPH_SPLIT_ENABLED` env var (default `true`) for rollback
    - This is additive and handles docs where OCR-recovered text lacks ATX markdown headings and any structural ordinal markers
    - NOTE: depends on [Task 2.1](#21-widen-pass_max_leaf_ratio-default-from-020-to-030-d0) — the shared `PASS_MAX_LEAF_RATIO` env var must already reflect the widened 0.30 default before this fallback's trigger condition is meaningful
    - _Requirements:_ [RFC-024 D3](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d3-extend-ordinal-splitter-regex-for-moudecree-documents-p1-missing-feature) | [Design Property 4](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-4-extended-ordinal-splitter-recognition-d3) | [Design Service: helpers.py](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#2-helperspy--verdict-threshold--ordinal-splitter) | [Design Sequence: Ordinal Splitter &amp; Paragraph-Boundary Fallback Flow](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#ordinal-splitter--paragraph-boundary-fallback-flow-d0-d3)
  - [X] <a id="24-batch-2-unit-tests"></a>2.4 Unit tests for Tasks 2.1-2.3

    - Write `tests/test_rfc024_d0.py`: (a) `max_leaf_ratio` 0.25 with default threshold 0.30 — verdict PASS; (b) `max_leaf_ratio` 0.35 — verdict MARGINAL; (c) `max_leaf_ratio` 0.19 — PASS regardless of threshold
    - Write `tests/test_rfc024_d3.py`: (a) text with "Clause 1 ... Clause 2 ... Clause 3" — `_has_heading_markers` returns `True`, splitting fires; (b) text with "بند ١ ... بند ٢ ... بند ٣" (≥3 markers, satisfying the longest-increasing-run guard with `min_segments >= 3`) — markers captured, ordinal run formed, split succeeds; (c) existing Article/Section/مادة patterns — no regression; (d) leaf_concentration paragraph-boundary fallback splits on blank lines when `max_leaf_ratio > PASS_MAX_LEAF_RATIO`; (e) "Part IV ... Part V ... Part VI" with Roman numerals — `_ordinal_value` returns correct int tuples via `_roman_to_int`; (f) "Annex A ... Annex B ... Annex C" with bare letters — `_ordinal_value` returns correct int tuples via `ord()` conversion; (g) "Part 2 of the agreement" (English prose, non-sequential) does NOT trigger a spurious split — false-positive regression guard
    - **Property 1: PASS_MAX_LEAF_RATIO threshold widening** — **Validates: [RFC-024 D0](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d0-widen-pass_max_leaf_ratio-default-from-020-to-030-p1-bug) | [Design Property 1](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-1-pass_max_leaf_ratio-threshold-widening-d0) | [RFC Test Strategy: D0 row](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#test-strategy)**
    - **Property 4: Extended ordinal splitter recognition** — **Validates: [RFC-024 D3](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d3-extend-ordinal-splitter-regex-for-moudecree-documents-p1-missing-feature) | [Design Property 4](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-4-extended-ordinal-splitter-recognition-d3) | [RFC Test Strategy: D3 row](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#test-strategy)**
  - [X] <a id="25-checkpoint--batch-2"></a>2.5 Checkpoint — Batch 2: Splitter & Threshold Hardening

    - Run `uv run pytest tests/test_rfc024_d0.py tests/test_rfc024_d3.py` and verify all pass
    - Verify [Design Properties 1, 4](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#correctness-properties) hold, including the D3 false-positive regression guard (test 2.4g)
    - Spot-check doc 8 (Reitlehrer), doc 7 (MOU MOHRE), and doc 21 (Domestic Workers) against the [RFC-024 Projected Run 8 Verdict Changes](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#projected-run-8-verdict-changes) table (expected: all three MARGINAL → PASS)
    - Ask the user if questions arise before proceeding
- [x] <a id="3-batch-3--rasterization--vlm-garble-recovery-d4-d5"></a>3. Batch 3: Rasterization & VLM-Garble Recovery ([RFC-024 D4](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d4-dual-rasterization-backend-for-tesseract-fallback-p1-bug), [D5](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d5-d7-tesseract-recovery-for-vlm-succeeds-but-garbled-path-p1-missing-feature))

  *[RFC-024 Batch 3: Rasterization &amp; VLM-Garble Recovery](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#batch-3-rasterization--vlm-garble-recovery-d4-d5----135d) — D4 provides the fitz rasterization backend that D5's extracted helper function will use; D5 depends on D4 being available.*

  - [X] <a id="31-pre-implementation-spike-fitz-rasterization-survives-cmap-corruption-d4"></a>3.1 Pre-implementation spike: fitz rasterization survives CMap corruption (D4)

    - Render Doc 18 (Organizational Decision) pages with `fitz.Page.get_pixmap()` in a standalone test/script to confirm fitz survives the CMap corruption that crashes pypdfium2
    - If fitz renders successfully: proceed to [Task 3.2](#32-add-rasterize_pdf_pages_fitz-and-pypdfium2-then-fitz-fallback-d4) as planned
    - If fitz ALSO fails on Doc 18's CMap-corrupt pages: escalate to Ghostscript rasterization as a fallback-of-the-fallback, OR downgrade Doc 18's Expected Outcome to `ERROR` in the [RFC-024 Projected Run 8 Verdict Changes](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#projected-run-8-verdict-changes) table and proceed without a Doc-18-specific fix
    - _Requirements:_ [RFC-024 T3.0](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#batch-3-rasterization--vlm-garble-recovery-d4-d5----135d) | [Design Property 5](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-5-dual-rasterization-backend-fallback-d4) | [RFC Risk: D4 Fitz may also fail](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#risk-assessment)
  - [X] <a id="32-add-rasterize_pdf_pages_fitz-and-pypdfium2-then-fitz-fallback-d4"></a>3.2 Add `rasterize_pdf_pages_fitz()`; modify `tesseract_ocr_pdf_pages` to try pypdfium2 then fitz (D4)

    - In `src/pageindex_mcp/converters.py`, add `rasterize_pdf_pages_fitz(pdf_path, dpi) -> list` using `fitz.Page.get_pixmap()`, reusing the pattern already proven in `_recover_picture_text`'s image-cropping path
    - Modify `tesseract_ocr_pdf_pages()` (~line 2287-2307) to try the existing `rasterize_pdf_pages()` (pypdfium2, ~line 2261-2284) first; on `Exception`, fall back to `rasterize_pdf_pages_fitz()`
    - Add `D7_FITZ_FALLBACK_ENABLED` env var (default `true`); when `false`, use pypdfium2 only (restores prior behavior)
    - NOTE: depends on [Task 3.1](#31-pre-implementation-spike-fitz-rasterization-survives-cmap-corruption-d4)'s spike confirming fitz survives the target CMap corruption before this task is implemented
    - _Requirements:_ [RFC-024 D4](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d4-dual-rasterization-backend-for-tesseract-fallback-p1-bug) | [Design Property 5](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-5-dual-rasterization-backend-fallback-d4) | [Design Service: converters.py](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#1-converterspy--picture-recovery--rasterization) | [Design Sequence: Dual-Rasterization VLM-Garble Recovery Flow](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#dual-rasterization-vlm-garble-recovery-flow-d4-d5)
  - [X] <a id="33-extract-_attempt_tesseract_raster_recovery-and-invoke-on-garbled-vlm-success-d5"></a>3.3 Extract `_attempt_tesseract_raster_recovery()`; invoke in try block after VLM garble detection (D5)

    - In `src/pageindex_mcp/client.py`, extract the existing D7 Tesseract-on-raster recovery logic (currently nested inside the `except Exception as vlm_exc:` block, ~line 894-937) into a standalone `_attempt_tesseract_raster_recovery(file_path, tess_langs, ...)` helper
    - After the VLM's `validate_tree` call (~line 890-893), when `ok` is `False` and `reason == 'garbling'` (VLM succeeded but tree is garbled), invoke `_attempt_tesseract_raster_recovery` from the try block — this is the new reachability path D5 adds
    - Keep the existing except-block call site (VLM crashes) calling the SAME extracted helper, so both call sites share one implementation
    - Do NOT add `'garbling'` itself to any flat-routing reason check directly — the reason override to `'node_count<3'` on non-garbled recovered OCR text remains the sole routing mechanism, preserving [CLAUDE.md HR5](../../CLAUDE.md#hard-rules)
    - Add `D7_GARBLE_RECOVERY_ENABLED` env var (default `true`); when `false`, a `'garbling'` reason with no VLM exception falls through to `LowQualityTreeError` unchanged (matches pre-D5 RFC-023 D7 case (d) behavior)
    - NOTE: depends on [Task 3.2](#32-add-rasterize_pdf_pages_fitz-and-pypdfium2-then-fitz-fallback-d4) — the extracted helper calls `converters.tesseract_ocr_pdf_pages`, which now uses D4's dual-backend rasterization
    - _Requirements:_ [RFC-024 D5](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d5-d7-tesseract-recovery-for-vlm-succeeds-but-garbled-path-p1-missing-feature) | [Design Property 6](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-6-garbled-vlm-tesseract-recovery-d5) | [Design Service: client.py](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#3-clientpy--vlm-garble-recovery--audit-meta-persistence) | [Design Sequence: Dual-Rasterization VLM-Garble Recovery Flow](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#dual-rasterization-vlm-garble-recovery-flow-d4-d5) | [CLAUDE.md HR5](../../CLAUDE.md#hard-rules)
  - [x] <a id="34-batch-3-unit-tests--rewrite-test_rfc023_d7py-case-d"></a>3.4 Unit tests for Tasks 3.1-3.3; rewrite `test_rfc023_d7.py` case (d) (D5 Supersession)

    - Write `tests/test_rfc024_d4.py`: (a) pypdfium2 raises on CMap-corrupt PDF — fitz fallback fires, returns page images; (b) pypdfium2 succeeds — fitz not called; (c) both fail — error propagated cleanly; (d) `D7_FITZ_FALLBACK_ENABLED=false` — fitz fallback disabled
    - Write `tests/test_rfc024_d5.py`: (a) VLM succeeds but `validate_tree` returns `(False, 'garbling')` — `_attempt_tesseract_raster_recovery` invoked; (b) VLM succeeds and `validate_tree` returns `(True, ...)` — helper NOT invoked; (c) VLM crashes (except block) — helper invoked as before; (d) both call sites use the same extracted function
    - **Rewrite `tests/test_rfc023_d7.py` case (d)** (supersedes [RFC-023 D7 case (d)](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d5-d7-tesseract-recovery-for-vlm-succeeds-but-garbled-path-p1-missing-feature)): assert that `validate_tree` returning `(False, 'garbling')` in the VLM try-block now invokes `_attempt_tesseract_raster_recovery` rather than falling through to `LowQualityTreeError`; preserve the OLD assertion as a new test under `D7_GARBLE_RECOVERY_ENABLED=false`
    - Extend `tests/test_rfc023_d7.py` with fitz-rasterization-path coverage (mocked pypdfium2 failure → fitz success, exercised through the D7/D5 call sites)
    - **Property 5: Dual rasterization backend fallback** — **Validates: [RFC-024 D4](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d4-dual-rasterization-backend-for-tesseract-fallback-p1-bug) | [Design Property 5](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-5-dual-rasterization-backend-fallback-d4) | [RFC Test Strategy: D4 row](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#test-strategy)**
    - **Property 6: Garbled-VLM Tesseract recovery** — **Validates: [RFC-024 D5](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d5-d7-tesseract-recovery-for-vlm-succeeds-but-garbled-path-p1-missing-feature) | [Design Property 6](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-6-garbled-vlm-tesseract-recovery-d5) | [RFC Test Strategy: D5 row](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#test-strategy)**
  - [x] <a id="35-checkpoint--batch-3"></a>3.5 Checkpoint — Batch 3: Rasterization & VLM-Garble Recovery

    - Run `uv run pytest tests/test_rfc024_d4.py tests/test_rfc024_d5.py tests/test_rfc023_d7.py` and verify all pass — **24 passed**
    - Verify [Design Properties 5, 6](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#correctness-properties) hold — **confirmed** (fitz fallback fires on pypdfium2 failure and is skipped otherwise; garbled-VLM success now routes into `_attempt_tesseract_raster_recovery` via the same helper as the except-block call site)
    - Confirmed the [Task 3.1](#31-pre-implementation-spike-fitz-rasterization-survives-cmap-corruption-d4) spike outcome by re-running `rasterize_pdf_pages_fitz()` directly against Doc 18's source PDF: **fitz survives, 35/35 pages render cleanly**. Doc 18's Expected Outcome is reconciled to `MARGINAL` (unconditional) in the [RFC-024 Projected Run 8 Verdict Changes](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#projected-run-8-verdict-changes) table
    - No questions arose; proceeding
- [x] <a id="4-batch-4--audit-tooling-d6"></a>4. Batch 4: Audit Tooling ([RFC-024 D6](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d6-fix-audit-tooling-char-count-measurement-for-flat-docs-p2-data-quality))

  *[RFC-024 Batch 4: Audit Tooling](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#batch-4-audit-tooling-d6----06d) — independent of Batches 1-3; may run in parallel with any prior batch.*

  - [X] <a id="41-update-char-count-measurement-to-use-_flat_block_text-d6"></a>4.1 Update char-count measurement in audit tooling to use `_flat_block_text()` (D6)

    - Update the corpus-cycle and corpus-score-diff skill prompts (the agent-driven audit process that reads `processed/*.meta.json` via `block.get('text', '')`) to call `_flat_block_text(b)` (or `_flat_search_text(b)`, `helpers.py:2103-2121`) per block instead
    - Note: no standalone audit-generation scripts exist in `scripts/` — this is a skill-prompt change, not a code change, per [RFC-024 D6 Files](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d6-fix-audit-tooling-char-count-measurement-for-flat-docs-p2-data-quality)
    - _Requirements:_ [RFC-024 D6](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d6-fix-audit-tooling-char-count-measurement-for-flat-docs-p2-data-quality) | [Design Property 7](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-7-flat-doc-char-count-measurement-consistency-d6) | [Design Service: Audit Tooling](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#4-audit-tooling--corpus-cycle--corpus-score-diff-skill-prompts)
  - [X] <a id="42-correct-doc-3-and-doc-9-rows-in-run-7-audit-report-d6"></a>4.2 Correct Doc 3 and Doc 9 rows in the Run 7 audit report (D6)

    - Update `audit/CORPUS_REINGESTION_AUDIT_RUN-7.md`: Doc 3 (GHV-TKV-Tarif) corrected from "333 chars" to 8,110 chars (3 fully-parsed tariff tables); Doc 9 (Unfallversicherung) corrected from "381 chars" to 7,297 chars (4 benefit-comparison tables)
    - Both docs' verdicts remain MARGINAL — this is a reporting-only correction; no verdict recomputation is needed since the production pipeline already computed them correctly (RFC-022 B3)
    - _Requirements:_ [RFC-024 D6](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d6-fix-audit-tooling-char-count-measurement-for-flat-docs-p2-data-quality) | [Design Property 7](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-7-flat-doc-char-count-measurement-consistency-d6)
  - [X] <a id="43-persist-_flat_block_text-derived-char-count-in-save_flat_doc-meta-d6-mandatory"></a>4.3 (Mandatory) Persist `_flat_block_text`-derived char count in `save_flat_doc` meta (D6)

    - In `src/pageindex_mcp/client.py`'s `save_flat_doc()`, compute `sum(len(_flat_block_text(b)) for b in blocks)` and persist it as a new `flat_char_count` field in `processed/*.meta.json`
    - This is the durable fix that prevents future audit code paths from repeating the `block.get('text', '')` error — Task 4.1 alone fixes only the current audit tooling
    - _Requirements:_ [RFC-024 D6](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d6-fix-audit-tooling-char-count-measurement-for-flat-docs-p2-data-quality) | [Design Property 7](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-7-flat-doc-char-count-measurement-consistency-d6) | [Design Service: client.py](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#3-clientpy--vlm-garble-recovery--audit-meta-persistence) | [Design Data Model: FlatDocMeta](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#core-entities-converterspy--helperspy--clientpy-meta--in-memory--one-new-persisted-field)
  - [X] <a id="44-batch-4-unit-tests"></a>4.4 Unit tests for Task 4.3

    - Write `tests/test_rfc024_d6.py`: (a) flat doc with table blocks — char count uses `_flat_block_text`, includes `row_records` content; (b) flat doc with only text blocks — char count unchanged from prior behavior (regression guard); (c) persisted `flat_char_count` meta field matches the `_flat_block_text`-derived total
    - **Property 7: Flat-doc char-count measurement consistency** — **Validates: [RFC-024 D6](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d6-fix-audit-tooling-char-count-measurement-for-flat-docs-p2-data-quality) | [Design Property 7](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-7-flat-doc-char-count-measurement-consistency-d6) | [RFC Test Strategy: D6 row](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#test-strategy)**
  - [x] <a id="45-checkpoint--batch-4"></a>4.5 Checkpoint — Batch 4: Audit Tooling

    - Run `uv run pytest tests/test_rfc024_d6.py` and verify all pass
    - Verify [Design Property 7](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#correctness-properties) holds
    - Confirm the corrected Doc 3 / Doc 9 rows are committed in `audit/CORPUS_REINGESTION_AUDIT_RUN-7.md`
    - Ask the user if questions arise before proceeding
- [ ] <a id="5-batch-5--reingestion-verification-run-8"></a>5. Batch 5: Reingestion Verification (Run 8)

  *[RFC-024 Batch 5: Reingestion Verification](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#batch-5-reingestion-verification-run-8----025d) — must run after Batches 1-4 complete; running it in parallel with Batches 1-3 would validate nothing.*

  - [x] <a id="51-bump-pipeline-version-and-reingest-corpus-for-run-8"></a>5.1 Bump `CURRENT_PIPELINE_VERSION`; full 25-doc reingestion for Run 8

    - Bump `CURRENT_PIPELINE_VERSION` to invalidate cached pipeline results
    - Wipe all derived stores (MinIO, Redis, PostgreSQL) per the corpus-reaudit methodology
    - Run `uv run python preprocess_client.py --bg` (or foreground) against the full 25-doc corpus in `doc_store/`
    - NOTE: depends on [Checkpoint 1.4](#14-checkpoint--batch-1), [Checkpoint 2.5](#25-checkpoint--batch-2), [Checkpoint 3.5](#35-checkpoint--batch-3), [Checkpoint 4.5](#45-checkpoint--batch-4) all passing
    - _Requirements:_ [RFC-024 Batch 5](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#batch-5-reingestion-verification-run-8----025d)
  - [ ] <a id="52-run-8-scorecard-and-regression-verification"></a>5.2 Run 8 scorecard vs projections; verify zero regressions on Run 7 PASS docs

    - Produce a Run 8 scorecard and compare against the [RFC-024 Projected Run 8 Verdict Changes](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#projected-run-8-verdict-changes) table (Docs 3, 9 stay MARGINAL; Docs 7, 8, 21 → PASS; Doc 14 → MARGINAL; Doc 18 → MARGINAL conditional on the T3.0 spike outcome)
    - Explicitly verify zero PASS→MARGINAL regressions on Run 7 PASS docs caused by [D3](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d3-extend-ordinal-splitter-regex-for-moudecree-documents-p1-missing-feature)'s new ordinal patterns scanning existing non-Arabic PASS docs, per [RFC-024 Risk: Run 8 regression on Run 7 PASS docs](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#risk-assessment)
    - Write the Run 8 scorecard to `audit/` following the existing corpus-audit report convention
    - _Requirements:_ [RFC-024 Expected Outcomes](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#expected-outcomes)
  - [ ] <a id="53-final-checkpoint--full-corpus-reaudit"></a>5.3 Final checkpoint — Full corpus reaudit

    - Run `uv run pytest` (full suite) and verify all [Design Properties 1-7](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#correctness-properties) pass
    - Verify the Run 8 scorecard shows zero regressions on Run 7 PASS docs (Task 5.2)
    - Verify the per-document projections in [RFC-024 Projected Run 8 Verdict Changes](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#projected-run-8-verdict-changes) are met or exceeded, accounting for Doc 18's conditional outcome
    - Ask the user if questions arise before proceeding

## Notes

- [Task 1.2 (D1)](#12-clip-text-capture-with-containment-guard-d1) MUST be rebased on [Task 1.1 (D2)](#11-per-region-tryexcept-in-phase-1-crop-loop-d2)'s crash-isolation fix — D1's capture only reaches every healthy region on mixed good/bad-region documents once D2 lands.
- [Task 2.3 (D3 item 3)](#23-leaf_concentration-aware-paragraph-boundary-splitting-fallback-d3) MUST use the SAME `PASS_MAX_LEAF_RATIO` env var as [Task 2.1 (D0)](#21-widen-pass_max_leaf_ratio-default-from-020-to-030-d0), not an independently hard-coded threshold — see [RFC-024 Risk: D0 vs D3 threshold inconsistency](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#risk-assessment).
- [Task 3.2 (D4)](#32-add-rasterize_pdf_pages_fitz-and-pypdfium2-then-fitz-fallback-d4) MUST NOT proceed before [Task 3.1](#31-pre-implementation-spike-fitz-rasterization-survives-cmap-corruption-d4)'s spike confirms fitz survives Doc 18's CMap corruption — if the spike fails, escalate per the spike task's contingency rather than implementing D4 blind.
- [Task 3.3 (D5)](#33-extract-_attempt_tesseract_raster_recovery-and-invoke-on-garbled-vlm-success-d5) MUST NOT add `'garbling'` to the flat-routing reason check directly — the reason override to `'node_count<3'` is the sole routing mechanism, preserving [CLAUDE.md HR5](../../CLAUDE.md#hard-rules) (no silent low-quality persistence).
- [Task 3.4](#34-batch-3-unit-tests--rewrite-test_rfc023_d7py-case-d) explicitly supersedes RFC-023 D7 test case (d) — do not treat the rewritten assertion as a regression; the old assertion is preserved as a new test gated on `D7_GARBLE_RECOVERY_ENABLED=false`, per [RFC-024 D5 Supersession note](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d5-d7-tesseract-recovery-for-vlm-succeeds-but-garbled-path-p1-missing-feature).
- [Task 4.2 (D6)](#42-correct-doc-3-and-doc-9-rows-in-run-7-audit-report-d6) does NOT change Doc 3 or Doc 9's verdicts — both remain MARGINAL by design; only the reported char count is corrected, per [RFC-024 D6](../rfcs/024-run7-verdict-stability-and-recovery-gaps.md#d6-fix-audit-tooling-char-count-measurement-for-flat-docs-p2-data-quality).
- Every fix in Batches 1-3 ships with a named env var defaulting to the fixed behavior (see each task's rollback note) — this permits isolating a regression to a single fix during Batch 5's reaudit without a full revert.
- Property-based tests are not used at MVP scope for this RFC (see [Design § Property-Based Testing Configuration](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md#property-based-testing-configuration)) — all `*`-marked tasks are targeted unit tests against exact fixture values, not generated inputs.
- Tests marked with `*` are still required (not optional) for this RFC given the Hard Rule 5 quality-gate implications of D1/D5 — do not skip them for a faster MVP.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "3.1", "4.1"] },
    { "id": 1, "tasks": ["1.2", "2.2", "3.2", "4.2"], "depends_on": { "1.2": ["1.1"], "2.2": [], "3.2": ["3.1"], "4.2": ["4.1"] } },
    { "id": 2, "tasks": ["1.3", "2.3", "4.3"], "depends_on": { "1.3": ["1.1", "1.2"], "2.3": ["2.1", "2.2"], "4.3": ["4.1"] } },
    { "id": 3, "tasks": ["1.4", "2.4", "3.3", "4.4"], "depends_on": { "1.4": ["1.1", "1.2", "1.3"], "2.4": ["2.1", "2.2", "2.3"], "3.3": ["3.2"], "4.4": ["4.3"] } },
    { "id": 4, "tasks": ["2.5", "3.4", "4.5"], "depends_on": { "2.5": ["2.1", "2.2", "2.3", "2.4"], "3.4": ["3.1", "3.2", "3.3"], "4.5": ["4.1", "4.2", "4.3", "4.4"] } },
    { "id": 5, "tasks": ["3.5"], "depends_on": { "3.5": ["3.1", "3.2", "3.3", "3.4"] } },
    { "id": 6, "tasks": ["5.1"], "depends_on": { "5.1": ["1.4", "2.5", "3.5", "4.5"] } },
    { "id": 7, "tasks": ["5.2"], "depends_on": { "5.2": ["5.1"] } },
    { "id": 8, "tasks": ["5.3"], "depends_on": { "5.3": ["5.2"] } }
  ]
}
```
