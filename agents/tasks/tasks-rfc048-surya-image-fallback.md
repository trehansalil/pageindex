<!-- Space: CITRA -->
<!-- Title: Implementation Plan: Surya OCR Fallback for Standalone Images -->
<!-- Folder: Tasks -->

---
id: "tasks-rfc048-surya-image-fallback"
title: "Tasks: Surya OCR Fallback for Standalone Images"
type: tasks
status: implemented
date: "2026-09-22"
tags:
  - tasks
  - ocr
  - image-pipeline
aliases:
  - "tasks-rfc048-surya-image-fallback"
governs:
  - "[[RFC-048]]"
---

# Implementation Plan: Surya OCR Fallback for Standalone Images

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [[RFC-048]] |
| Design Document | [[design-rfc048-surya-image-fallback]] |
| PRD / Requirements | [[PRD]] |

## Overview

Two deliverables: (D1) add `/ocr/image` to the Surya service, (D2) wire quality-gated Surya fallback into the standalone image path in the indexer. Reuses existing Surya config and `SuryaRecoveryResult`. Estimated effort: ~~4–6 hours~~ → **5–7 hours** (revised 2026-09-22: GB-7 — accounts for TypedDict handling, splice-ordering precision, and additional test for Property 1a).

## Tasks

- [x] 1. D1: Surya Service `/ocr/image` Endpoint

  - [x] 1.1 Add `/ocr/image` route to `services/surya-ocr-service/app.py`

    - Add `ImageOcrRequest` model (file upload via multipart)
    - Add `ImageOcrResponse` model matching `/ocr/pdf` response shape (`total_text`, `total_char_count`, `total_avg_confidence`, `regions`, `elapsed_s`)
    - Implement `POST /ocr/image` handler: receive image file → `PIL.Image.open` → `_ocr_image` (existing) → build response
    - Return HTTP 400 on `PIL.UnidentifiedImageError` or empty file
    - _Requirements: RFC-048 R1 AC1–AC3_

  - [x] 1.2 Write tests for `/ocr/image` endpoint

    - Test: valid PNG → 200 with text + confidence
    - Test: valid JPEG → 200
    - Test: invalid file (random bytes) → 400
    - Test: empty file → 400
    - Test: response schema matches `/ocr/pdf` shape (same field names)
    - _Requirements: RFC-048 R1 AC1–AC3, Design Property 6_

- [x] 2. Checkpoint — D1

  - Verify `/ocr/image` endpoint works against a running Surya service (manual smoke test with the corpus pie chart image)
  - Run `make test PYTEST_ARGS="tests/test_d8_surya_fallback.py -q"` to confirm existing Surya tests still pass

- [x] 3. D2: Quality-Gated Surya Fallback in Image Path

  - [x] 3.0 Add `SURYA = "surya"` member to `OcrEngine` enum in `src/pageindex_mcp/picture_plane.py` **(Added 2026-09-22: GA-2 — enum member does not exist yet; required before task 3.2 can set `state.ocr_engine`)**
    - _Requirements: RFC-048 R2 AC5, Design Property 3_

  - [x] 3.1 Add `_surya_image_ocr` helper to `src/pageindex_mcp/client/indexer.py`

    - Async function: sends image bytes to `{surya_url}/ocr/image` via httpx
    - Returns `SuryaRecoveryResult | None` (reuse existing dataclass)
    - Catches all exceptions → returns `None` (fail-open, Property 2)
    - Logs failure via `logger.warning`
    - _Requirements: RFC-048 R2 AC4_

  - [x] 3.2 Wire quality gate + Surya fallback into `_convert_to_tree` image branch **(Amendment 2026-09-22: GA-1 — gate runs AFTER `pic_results` construction; only replaces `standalone_ocr_text` + `pic_results[0].ocr_text`, never `md_content`)**

    - **Site:** BETWEEN `pic_results` construction (~line 1344) and `splice_picture_text_for_tree` (~line 1353). Gate must run before splice so Surya text propagates into `md_content` via the existing splice step. **(Amendment 2026-09-22: GB-2 — before splice, not after both)**
    - Compute `_tess_garbled` via `detect_garble(standalone_ocr_text, ...)`
    - Compute `_tess_chars` as whitespace-stripped char count
    - Quality gate: `_tess_chars <= MIN_STANDALONE_IMAGE_MD_CHARS or _tess_garbled` (OR, not AND — GA-3)
    - If gate fails AND `settings.surya_fallback_enabled`: call `_surya_image_ocr`
    - Compare: Surya wins if (more chars AND not garbled) OR (Tesseract garbled AND Surya not garbled)
    - If Surya wins: replace `standalone_ocr_text` and `pic_results[0]["ocr_text"]` (dict assignment — `PictureResult` is a `TypedDict`, not `NamedTuple`); do NOT touch `md_content` (Property 1a) **(Amendment 2026-09-22: GB-1 — `_replace` → dict assignment)**
    - Set `state.ocr_engine = str(OcrEngine.SURYA)` (requires task 3.0)
    - Emit `surya_image_fallback` decision event with full attrs
    - Map response field `total_char_count` → dataclass `total_chars` in `_surya_image_ocr` (GA-4)
    - _Requirements: RFC-048 R2 AC1–AC6, Design Properties 1, 1a, 2–4, 5a, 5c_ **(Amendment 2026-09-22: GC-1 — added 5a winner-selection, 5c decision event)**

  - [x] 3.3 Register `surya_image_fallback` decision event

    - Add to `src/pageindex_mcp/obs/decision_points.py`
    - Choices: `recovery_succeeded`, `recovery_insufficient`, `recovery_failed`, `not_attempted`, `gate_not_triggered`
    - Attrs: `tesseract_chars`, `surya_chars`, `tesseract_garbled`, `surya_garbled`, `winner`, `surya_confidence`, `surya_duration_s`
    - _Requirements: RFC-048 R2 AC6_

  - [x] 3.4 Write tests for Surya image fallback

    - Test: `SURYA_FALLBACK_ENABLED=false` → no HTTP call to Surya (Property 4)
    - Test: Tesseract output good (above threshold, not garbled) → no Surya attempt (Property 1)
    - Test: Tesseract output poor + Surya yields better → Surya text used, `ocr_engine="surya"` (Property 3)
    - Test: Tesseract output poor + Surya yields worse → Tesseract kept
    - Test: Tesseract output poor + Surya garbled → Tesseract kept
    - Test: Surya service timeout → Tesseract kept, no exception (Property 2)
    - Test: Surya service HTTP error → same as timeout
    - Test: decision event emitted with correct choice and attrs
    - Test: Surya wins → `pic_results[0]["ocr_text"]` == Surya text AND `md_content` markers (`<!-- image -->`) intact at gate time (Property 1a) **(Added 2026-09-22: GB-3)**
    - _Requirements: RFC-048 R2 AC1–AC6, Design Properties 1, 1a, 2–4, 5a, 5c_ **(Amendment 2026-09-22: GC-1 — added 5a winner-selection, 5c decision event)**

- [x] 4. Final Checkpoint

  - Run `make test` (full suite) to verify no regressions
  - Run corpus ingest with `SURYA_FALLBACK_ENABLED=true` on the pie chart image → verify PASS/MARGINAL verdict
  - Verify decision event `surya_image_fallback` appears in logs with `recovery_succeeded`

## Notes

- No new config fields needed — reuses `SURYA_FALLBACK_ENABLED`, `SURYA_SERVICE_URL`, `SURYA_FALLBACK_TIMEOUT_S` from RFC-047 D8.
- `SuryaRecoveryResult` dataclass (already in `indexer.py`) is reused as-is.
- ~~The `OcrEngine.SURYA` enum value must already exist from RFC-047 D8; verify before implementing.~~ **(Amendment 2026-09-22: GA-2 — CONFIRMED it does NOT exist. Task 3.0 adds it as a prerequisite.)**
- Tests should use `unittest.mock.patch` on `httpx.AsyncClient` to mock the Surya service, same pattern as `tests/test_d8_surya_fallback.py`.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"], "label": "D1: Surya /ocr/image endpoint" },
    { "id": 1, "tasks": ["2"], "label": "Checkpoint D1" },
    { "id": 2, "tasks": ["3.0", "3.1", "3.2", "3.3", "3.4"], "label": "D2: Quality-gated fallback", "intra_wave_order": "3.0 → 3.1/3.2 → 3.3/3.4 (3.0 is prerequisite for 3.2; 3.3 can parallel 3.1)" },
    { "id": 3, "tasks": ["4"], "label": "Final checkpoint" }
  ]
}
```