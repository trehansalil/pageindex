<!-- Space: CITRA -->
<!-- Title: RFC-048: Surya Image Fallback -->
<!-- Folder: RFCs -->

---
id: "RFC-048"
title: "Surya OCR Fallback for Standalone Images"
type: rfc
status: draft
date: "2026-09-22"
plan-impact: "no"
tags:
  - rfc
  - ocr
  - image-pipeline
aliases:
  - "RFC-048"
  - "Surya Image Fallback"
governs:
  - "[[design-rfc048-surya-image-fallback]]"
  - "[[tasks-rfc048-surya-image-fallback]]"
supersedes: []
---

## Context

The standalone image pipeline (`.jpg`, `.png`, `.tiff` etc.) relies exclusively on Tesseract OCR via `image_to_markdown` (Docling's `StandardPdfPipeline` with `force_full_page_ocr=True`) and `_tesseract_ocr_image` as a sparse-output fallback. This works acceptably for Latin-script images but fails for mixed-script and Arabic-dominant content:

- **Doc 13** (pie chart with Arabic labels ذكور/إناث + English numerals): Tesseract yields 440 chars; the job has regressed to ERROR (timeout before `save_doc`) due to tessdata availability issues for mixed Arabic+English lang combos.
- **Scanned Arabic PDFs** with image-dominant content produce 0 chars because Tesseract yields nothing from Arabic script images and VLM is disabled by design ([[RFC-004]]).

[[RFC-047]] D8 shipped Surya OCR as a density-recovery fallback for Arabic PDF failures. The Surya service (`services/surya-ocr-service/`) is deployed and operational. The RFC-046 multi-engine eval (`agents/spikes/ocr_eval_rfc046/eval_report.md`) shows Surya produces **799 chars at 92.94% confidence** on the image file vs Tesseract's 440 — an 82% improvement. Across all scanned/mixed Arabic documents, Surya wins or ties on every one with consistently >92% confidence.

This RFC extends the Surya fallback to the standalone image path, quality-gated so it fires only when Tesseract output is poor.

## Goals

- Recover standalone image documents that Tesseract handles poorly (mixed-script, Arabic) by falling back to Surya OCR.
- Add a dedicated `/ocr/image` endpoint to the Surya service for direct image OCR (avoiding PDF wrapping overhead).
- Maintain the existing Tesseract-first path for images where it already works (Latin-script).

## Non-Goals

- Replacing Tesseract as the primary image OCR engine (Surya is a quality-gated fallback only).
- Wiring Surya into the image-dominant PDF recovery path (`_recover_image_dominant_ocr`) — that stays with [[RFC-047]] D8's PDF-specific path; can be revisited if corpus failures warrant.
- General multi-engine arbitration for images (the `arbitrate.py` framework is not used — this is a targeted recovery, same pattern as RFC-047 D8).
- VLM integration for images ~~(remains disabled per [[RFC-004]])~~ **(Amendment 1, 2026-09-23: VLM is now enabled for standalone images via Pillow, as part of the parallel post-validation fallback. VLM remains disabled for PDFs per [[RFC-004]].)**

## Glossary

| Term | Definition |
|------|------------|
| Standalone Image | A `.jpg`/`.png`/`.jpeg`/`.tiff`/`.tif`/`.bmp`/`.webp` file ingested directly (not embedded in a PDF). Handled by the `ext in _IMAGE_EXTS` branch in `_convert_to_tree`. |
| Quality Gate | The threshold check on Tesseract output (character yield, garble status) that decides whether to attempt Surya fallback. |
| Surya Service | The `services/surya-ocr-service/` HTTP microservice running Surya OCR ≥0.22, currently exposed at port 8207. |
| Post-Validation Retry | **(Amendment 1)** A second fallback window that opens AFTER `validate_tree` condemns a standalone image, bypassing the initial quality gate's sensitivity limitations. |
| Parallel Image Fallback | **(Amendment 1)** When the post-validation retry fires, both VLM (via Pillow) and Surya OCR run concurrently; the pipeline waits for both and selects the best result by non-garbled character count and confidence. |

## Requirements

### Requirement 1: Surya Service Image Endpoint

**User Story:** As the ingestion pipeline, I want to send a raw image file to the Surya service and receive OCR text back, so that I don't need to wrap images as PDFs.

#### Acceptance Criteria

1. WHEN a POST request is sent to `/ocr/image` with an image file, THE Surya Service SHALL return OCR text, character count, and confidence.
2. IF the image cannot be decoded, THEN THE Surya Service SHALL return HTTP 400 with a descriptive error.
3. THE `/ocr/image` response schema SHALL match the existing `/ocr/pdf` response structure (total_text, total_char_count, total_avg_confidence) for client consistency.

### Requirement 2: Quality-Gated Surya Fallback in Image Path

**User Story:** As a document corpus operator, I want images that Tesseract handles poorly to automatically fall back to Surya OCR, so that mixed-script and Arabic images produce usable content instead of failing.

#### Acceptance Criteria

1. WHEN Tesseract output for a standalone image is below `MIN_STANDALONE_IMAGE_MD_CHARS` OR garble-detected/wrong-script, THE pipeline SHALL attempt Surya OCR as a fallback. **(Amendment 2026-09-22: changed AND → OR per review GA-3; garbled output is poor regardless of char count.)**
2. IF Surya yields more non-garbled characters than Tesseract, THEN THE pipeline SHALL use Surya's output.
3. IF `SURYA_FALLBACK_ENABLED` is `false`, THEN THE pipeline SHALL NOT attempt Surya fallback (Tesseract-only, current behavior preserved).
4. IF the Surya service is unavailable or times out, THEN THE pipeline SHALL log the failure and continue with Tesseract output (no hard failure).
5. THE pipeline SHALL record the OCR engine used (`tesseract` or `surya`) in `state.ocr_engine` for attribution (RFC-046 D2 compatibility).
6. THE pipeline SHALL emit a `surya_image_fallback` decision event with attrs: `tesseract_chars`, `surya_chars`, `tesseract_garbled`, `surya_garbled`, `winner`, `surya_confidence`, `surya_duration_s`.

### Requirement 3: Post-Validation Parallel Image Fallback **(Amendment 1, 2026-09-23)**

**User Story:** As a document corpus operator, I want images that pass the initial Surya gate but are subsequently condemned by `validate_tree` to get a second chance via parallel VLM + Surya OCR, so that garbling detected only at tree-validation depth is still recoverable.

**Motivation:** Run 21 corpus rerun (2026-09-23, `audit/CORPUS_REINGESTION_AUDIT_RUN-21_RFC048.md` Finding 1) showed Doc 13 (pie chart) still REJECTED. Tesseract produced 303 chars flagged as non-garbled by the Requirement 2 gate, but `validate_tree` caught garbling (`depth<2`, `node_garbling`) that the gate's simpler check missed. The Surya fallback window had already closed. This requirement adds a second window at the point where the deeper analysis runs.

#### Acceptance Criteria

1. WHEN `validate_tree` rejects a standalone image for garbling, depth inadequacy, or node-level garbling AND `SURYA_FALLBACK_ENABLED` is `true`, THE pipeline SHALL attempt both VLM and Surya OCR in parallel as a post-validation fallback.
2. THE pipeline SHALL use `PIL.Image.open()` (Pillow) to load standalone images for VLM processing, replacing PDFium which cannot open non-PDF formats.
3. THE pipeline SHALL wait for BOTH engines to complete and select the result with the highest non-garbled character count. Where character counts are equal, the result with higher confidence wins.
4. IF neither engine produces a non-garbled result with more characters than the original Tesseract output, THE pipeline SHALL proceed with existing recovery escalation (no change to current behavior).
5. IF `SURYA_FALLBACK_ENABLED` is `false`, THE pipeline SHALL NOT attempt the post-validation fallback (current behavior preserved).
6. IF one engine fails or times out, THE pipeline SHALL use the other engine's result if it is non-garbled and improves on the Tesseract baseline (graceful degradation, not mutual failure).
7. THE pipeline SHALL emit a `post_validation_image_fallback` decision event with attrs: `trigger_reason` (the `validate_tree` rejection reason), `vlm_chars`, `vlm_garbled`, `vlm_confidence`, `vlm_duration_s`, `surya_chars`, `surya_garbled`, `surya_confidence`, `surya_duration_s`, `winner` (`vlm` | `surya` | `neither`), `original_tesseract_chars`.
8. THE pipeline SHALL record the final OCR engine used in `state.ocr_engine` (values: `tesseract`, `surya`, `vlm`, `surya_post_validation`, `vlm_post_validation`) for RFC-046 D2 attribution compatibility.

## Decision Summary

Surya OCR is wired as a quality-gated fallback in the standalone image ingestion path (`_convert_to_tree`, `ext in _IMAGE_EXTS` branch), with a **two-window** design **(Amendment 1)**:

1. **Initial gate (Requirement 2):** Fires when Tesseract output is poor (low char yield OR garbled/wrong-script). Runs Surya only.
2. **Post-validation retry (Requirement 3, Amendment 1):** Fires when `validate_tree` condemns the image for garbling/depth/quality that the initial gate's simpler check missed. Runs VLM + Surya in parallel, picks the best result.

The existing `SURYA_FALLBACK_ENABLED` config flag gates both windows. A new `/ocr/image` endpoint on the Surya service accepts raw images directly. VLM for standalone images uses Pillow (`PIL.Image.open()`) instead of PDFium, which cannot open non-PDF formats. The fast Tesseract-first path for images where it already works is preserved — neither fallback window fires on the happy path.

## Consequences

- Doc 13 (pie chart) is expected to recover from REJECTED to PASS/MARGINAL via the **post-validation retry** (Requirement 3), not the initial gate (Requirement 2). Run 21 confirmed the initial gate does not fire for this document (303 chars, non-garbled at gate level); recovery depends on `validate_tree` condemning the output and the parallel VLM+Surya fallback producing a better result. The eval evidence (Surya: 799 chars at 93% confidence) supports this expectation.
- The Surya service gains a second endpoint (`/ocr/image`), increasing its API surface slightly.
- Image ingestion latency increases only for the fallback paths (initial gate or post-validation retry); happy-path images see no change. The post-validation retry adds latency only when `validate_tree` condemns the image — it is the second-chance path, not the happy path.
- The `SURYA_FALLBACK_ENABLED` flag remains the single gate for PDF density recovery (RFC-047 D8), image initial fallback (Requirement 2), and post-validation parallel fallback (Requirement 3).
- **(Amendment 1)** VLM is now available as a fallback for standalone images, using Pillow instead of PDFium. This is scoped to standalone images only — VLM for PDFs remains disabled per [[RFC-004]].
- **(Amendment 1)** The parallel execution model (VLM + Surya, best-result selection) provides resilience: if one engine is unavailable or produces garbled output, the other can still recover the document.

## Amendment History

### Amendment 1 (2026-09-23): Post-validation Surya retry + parallel VLM fallback

**Trigger:** Run 21 corpus rerun (`audit/CORPUS_REINGESTION_AUDIT_RUN-21_RFC048.md`) showed Doc 13 still REJECTED. The initial Surya gate (Requirement 2) did not fire: Tesseract produced 303 chars flagged as non-garbled by the gate, but `validate_tree` caught garbling the gate's simpler check missed. The Surya fallback window had already closed.

**Changes:**

1. **Added Requirement 3** — post-validation parallel image fallback with 8 acceptance criteria. When `validate_tree` condemns a standalone image, both VLM and Surya OCR run in parallel; the best result (by non-garbled char count) wins.
2. **VLM enabled for standalone images** — uses `PIL.Image.open()` (Pillow) instead of PDFium, which cannot open non-PDF formats. VLM for PDFs remains disabled per [[RFC-004]]. Non-Goals bullet updated to reflect the change.
3. **Decision Summary rewritten** — now describes the two-window design (initial gate + post-validation retry).
4. **Consequences updated** — Doc 13 recovery now attributed to the post-validation retry, not the initial gate. Parallel execution resilience noted.
5. **Glossary extended** — added Post-Validation Retry and Parallel Image Fallback terms.

**What did NOT change:** Requirements 1 and 2 are unchanged. The `/ocr/image` endpoint, the initial quality gate, the `SURYA_FALLBACK_ENABLED` flag, and the Tesseract-first happy path are all preserved.

## Traceability

| Artifact | Reference |
|----------|-----------|
| Design   | [[design-rfc048-surya-image-fallback]] |
| Tasks    | [[tasks-rfc048-surya-image-fallback]] |
| Supersedes | N/A |
| Prior art | [[RFC-047]] D8 (Surya PDF density fallback) |
| Evidence | `agents/spikes/ocr_eval_rfc046/eval_report.md` (multi-engine eval) |
| Run 21 evidence | `audit/CORPUS_REINGESTION_AUDIT_RUN-21_RFC048.md` (Amendment 1 trigger — gate sensitivity gap, Finding 1) |
| D9 baseline | `agents/baselines/rfc047-d9-final-baseline.md` (0 FAIL / 25 docs, 2026-09-22) |