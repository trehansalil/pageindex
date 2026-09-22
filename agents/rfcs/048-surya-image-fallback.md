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
- VLM integration for images (remains disabled per [[RFC-004]]).

## Glossary

| Term | Definition |
|------|------------|
| Standalone Image | A `.jpg`/`.png`/`.jpeg`/`.tiff`/`.tif`/`.bmp`/`.webp` file ingested directly (not embedded in a PDF). Handled by the `ext in _IMAGE_EXTS` branch in `_convert_to_tree`. |
| Quality Gate | The threshold check on Tesseract output (character yield, garble status) that decides whether to attempt Surya fallback. |
| Surya Service | The `services/surya-ocr-service/` HTTP microservice running Surya OCR ≥0.22, currently exposed at port 8207. |

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

## Decision Summary

Surya OCR is wired as a quality-gated fallback in the standalone image ingestion path (`_convert_to_tree`, `ext in _IMAGE_EXTS` branch). The existing `SURYA_FALLBACK_ENABLED` config flag gates the feature. A new `/ocr/image` endpoint on the Surya service accepts raw images directly. The fallback fires only when Tesseract output is poor (low char yield + garbled/wrong-script), preserving the fast Tesseract-first path for images where it already works.

## Consequences

- Doc 13 (pie chart) is expected to recover from ERROR to PASS/MARGINAL: Surya yields 799 chars at 93% confidence vs Tesseract's 440.
- The Surya service gains a second endpoint, increasing its API surface slightly.
- Image ingestion latency increases only for the quality-gated fallback path (poor Tesseract output); happy-path images see no change.
- The `SURYA_FALLBACK_ENABLED` flag remains the single gate for both PDF density recovery (RFC-047 D8) and image fallback (this RFC), keeping operational control simple.

## Traceability

| Artifact | Reference |
|----------|-----------|
| Design   | [[design-rfc048-surya-image-fallback]] |
| Tasks    | [[tasks-rfc048-surya-image-fallback]] |
| Supersedes | N/A |
| Prior art | [[RFC-047]] D8 (Surya PDF density fallback) |
| Evidence | `agents/spikes/ocr_eval_rfc046/eval_report.md` (multi-engine eval) |