<!-- Space: CITRA -->
<!-- Title: RFC-017: OCR / Image-Block Pipeline Decoupling -->
<!-- Folder: RFCs -->

---

id: RFC-017
title: OCR / Image-Block Pipeline Decoupling
status: proposed
date: 2026-07-27
plan-impact: yes
supersedes-decisions-in: []
---------------------------

## Traceability

| Artifact | Reference |
|---|---|
| Design Document | [design-rfc017-ocr-image-block-decoupling.md](../designs/design-rfc017-ocr-image-block-decoupling.md) |
| Implementation Plan | [tasks-rfc017-ocr-image-block-decoupling.md](../tasks/tasks-rfc017-ocr-image-block-decoupling.md) |
| Audit | [OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION_2026-07-27.md](../../audit/OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION_2026-07-27.md) |

## Context

The `feat/image-block-picture-ocr` branch introduced a per-picture OCR + VLM description pipeline (RFC-015 D6) to capture non-textual content (charts, infographics, photos) as first-class retrievable artifacts. **However, this pipeline conflates with the proven page-level OCR escalation pipeline** (OCR-01, RFC-005 Fix 3), causing OCR-recovered text from scanned/image-dominant PDFs to be structurally reclassified from `prose` blocks into `image`-block `ocr_text` fields — degrading retrieval granularity, content classification accuracy, and text continuity.

### What this RFC covers

| Scope                            | Description                                                                             |
| -------------------------------- | --------------------------------------------------------------------------------------- |
| P0b: Page-coverage filter        | Skip PictureItems covering >60% of page area in per-picture OCR                         |
| P0a: Standalone image enrichment | Create synthetic PictureResult for`.jpg/.png/.tiff` files so enrichment pipeline runs |

### Out of scope

| Item                                      | Why                                                          |
| ----------------------------------------- | ------------------------------------------------------------ |
| P1: Separate kill-switches                | Lower priority, no data loss — deferred to follow-up RFC    |
| P2: Image blocks as prose signals         | Classification improvement — deferred to follow-up RFC      |
| P3: Skip per-picture OCR on escalation    | Optimization — deferred to follow-up RFC                    |
| P4: Thread-local boundary fix             | Already partially fixed in working tree; completion deferred |
| P5: Prose fallback for unresolved markers | Already correct behavior — verify only                      |

### Investigation evidence

Full investigation report with MinIO evidence at [`audit/OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION_2026-07-27.md`](../../audit/OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION_2026-07-27.md). Key findings:

1. `_recover_picture_results()` fires unconditionally inside `pdf_to_markdown_docling()` — no page-coverage filter
2. OCR text **moved** from `block["text"]` (prose) to `block["ocr_text"]` (image block) — not duplicated, relocated
3. Standalone image files (.jpg/.png) **never run** enrichment — `client.py:529-541` image branch never calls `_enrich_image_blocks()` or `splice_figure_markers()`
4. `content_signals` in `route_and_extract_flat()` excludes image blocks entirely — documents with content trapped in image blocks get misclassified
5. The entire enrichment pipeline is dead code in production (thread-local boundary bug, audit finding 1)

## Hard Rule constraints (CLAUDE.md binding)

| Rule                                                             | Compliance                                                                                                                                                                 |
| ---------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **HR1** — Never claim vectorless beats vector on accuracy | N/A — no positioning changes                                                                                                                                              |
| **HR2** — Right-to-erasure cascade                        | `save_figure()` uses `figures/<doc_id>/` prefix; erasure deletes by doc_id prefix. No new derived stores introduced                                                    |
| **HR3** — PII routing through ZDR tier                    | No new LLM egress. Standalone image uses local Tesseract only. Page-coverage filter is pure arithmetic                                                                     |
| **HR4** — AGPL-3.0 awareness                              | No new AGPL imports.`_recover_picture_text` already imports fitz; coverage filter adds arithmetic inside existing fitz scope                                             |
| **HR5** — Never silently persist low-quality tree         | Page-coverage filter**improves** quality — full-page scans flow through `validate_tree()` / `force_full_page_ocr` instead of being fragmented into image blocks |

## Decisions

### D0 — Page-coverage filter in `_recover_picture_text`

**Problem:** `_collect_picture_regions()` (`converters.py:1281`) returns ALL `PictureItem` regions from Docling's layout model without any size filtering. Docling's RT-DETRv2 model classifies entire scanned pages as `PictureItem`. The per-picture OCR pipeline then crops these full-page regions at 300 DPI, runs Tesseract on each crop independently, and produces fragmented text in `image` blocks instead of contiguous `prose` blocks. This text is invisible to `content_signals` in `route_and_extract_flat()` (`helpers.py:1540`) which only counts `{"table", "kv", "prose"}`.

**Decision:** In `_recover_picture_text()` (`converters.py:1376`), after computing `rect` from the region's bbox and before cropping via `get_pixmap`, compute the ratio of the region's area to the page's area. Skip any region where this ratio exceeds `_PICTURE_PAGE_COVERAGE_THRESHOLD` (default 0.6, configurable via `PICTURE_PAGE_COVERAGE_THRESHOLD` env var). Full-page-sized regions are scanned pages that should be handled by the page-level OCR escalation (OCR-01-C1), not per-picture cropping.

**File:** `src/pageindex_mcp/converters.py`

**New constant** (near line 1244, after `_PICTURE_OCR_MIN_CHARS`):

```python
_PICTURE_PAGE_COVERAGE_THRESHOLD = float(
    os.getenv("PICTURE_PAGE_COVERAGE_THRESHOLD", "0.6")
)
```

**Change in Phase 1 crop loop** (lines 1376-1386):

```python
page = pdf[page_index]
rect = _bbox_to_fitz_rect(region["bbox"], page.rect.height, fitz)
if rect is None:
    continue
# D0: skip regions covering >60% of page area — these are full scanned
# pages, not embedded charts. Page-level OCR escalation (OCR-01) handles them.
page_area = page.rect.width * page.rect.height
if page_area > 0 and (rect.width * rect.height) / page_area > _PICTURE_PAGE_COVERAGE_THRESHOLD:
    continue
pix = page.get_pixmap(clip=rect, dpi=300)
```

**Rationale:** A `PictureItem` covering >60% of the page is a full scanned page, not an embedded chart. The page-level OCR escalation (OCR-01-C1) handles these correctly — it re-runs the entire page through `force_full_page_ocr` and produces prose blocks. Cropping them as individual charts fragments the text and reclassifies it from prose to image blocks, degrading both retrieval and classification.

### D1 — Standalone image enrichment via synthetic PictureResult

**Problem:** The standalone image branch in `client.py:529-541` calls `image_to_markdown()` → `_run_md_to_tree()` directly. It never calls `splice_figure_markers()` or `_enrich_image_blocks()`. The `pic_results` variable stays at its initialized value of `[]` (line 409). Chart content in standalone images (`.jpg`, `.png`, `.tiff`) becomes literal `<!-- image -->` strings with zero enrichment — confirmed via MinIO evidence: the pie chart JPG (`f057fafe-...`) has its numeric labels and wedge text completely lost.

**Decision:** After `image_to_markdown()` returns, read the source image file's bytes and create a single synthetic `PictureResult` with:

- `png_bytes` = source file bytes (the image IS the picture)
- `ocr_text` = `""` (page-level Tesseract already ran inside `image_to_markdown()`)
- `page` = 1
- `bbox` = `{"l": 0, "t": 0, "r": 0, "b": 0}` (full image, no sub-region)

The downstream flat-branch code (`splice_figure_markers` + `_enrich_image_blocks` at `client.py:782-801`) will process this result normally — replacing `<!-- image -->` with `[Figure: fig-0]` and uploading the PNG to MinIO via `save_figure()`.

**File:** `src/pageindex_mcp/client.py`, lines 529-541 (standalone image branch)

**Code** (after the `image_to_markdown()` call):

```python
md_content = await asyncio.to_thread(image_to_markdown, file_path, img_langs)
# D1: standalone image IS the picture — create a synthetic PictureResult
# so the flat-branch enrichment pipeline (splice_figure_markers +
# _enrich_image_blocks) can process any <!-- image --> markers.
img_bytes = await asyncio.to_thread(Path(file_path).read_bytes)
pic_results = [PictureResult(
    ocr_text="",
    page=1,
    bbox={"l": 0, "t": 0, "r": 0, "b": 0},
    png_bytes=img_bytes,
)]
```

**Rationale:** `ocr_text=""` because `image_to_markdown()` already ran full-page Tesseract — the per-picture OCR text would be redundant. The synthetic result exists to:

1. Carry the PNG bytes for MinIO storage (via `save_figure()` in `_enrich_image_blocks`)
2. Let `splice_figure_markers` produce `[Figure: fig-0]` markers instead of bare `<!-- image -->`
3. Allow `_enrich_image_blocks` to populate the `figure_path` field on flat blocks

**Edge case — multiple `<!-- image -->` markers:** If `image_to_markdown` produces multiple `<!-- image -->` markers (Docling detecting sub-regions within the image), the marker-count mismatch guard in `splice_figure_markers` (`converters.py:1455-1463`) will bail and leave markers as-is. This is correct degradation — we have one source image file producing one `PictureResult`, not multiple charts.

## Implementation Plan

| Batch | Step | Change                                                          | File                           |
| ----- | ---- | --------------------------------------------------------------- | ------------------------------ |
| 0     | 1    | Add`_PICTURE_PAGE_COVERAGE_THRESHOLD` constant                | `converters.py:1244`         |
| 0     | 2    | Add area check in`_recover_picture_text` Phase 1 loop         | `converters.py:1380`         |
| 0     | 3    | Import`PictureResult` + `Path` in client.py                 | `client.py` imports          |
| 0     | 4    | Add synthetic`pic_results` in standalone image branch         | `client.py:535`              |
| 1     | 5    | Add P0b test: page-coverage filter skips large regions          | `tests/test_image_blocks.py` |
| 1     | 6    | Add P0b test: page-coverage filter keeps small regions          | `tests/test_image_blocks.py` |
| 1     | 7    | Add P0a test: standalone image produces synthetic PictureResult | `tests/test_image_blocks.py` |
| 1     | 8    | Add P0a test: marker-count mismatch degrades gracefully         | `tests/test_image_blocks.py` |

## Test Strategy

| Decision | Test                                                    | Assertion                                                                                                        |
| -------- | ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| D0       | `test_page_coverage_filter_skips_large_region`        | Region at 80% page area → not in`crops` dict                                                                  |
| D0       | `test_page_coverage_filter_keeps_small_region`        | Region at 30% page area → present in`crops` dict with valid PNG bytes                                         |
| D0       | `test_page_coverage_threshold_configurable`           | `PICTURE_PAGE_COVERAGE_THRESHOLD=0.9` → region at 80% is kept                                                 |
| D1       | `test_standalone_image_produces_synthetic_pic_result` | `.jpg` file → `pic_results` has exactly 1 entry with `png_bytes` == source bytes                          |
| D1       | `test_standalone_image_marker_mismatch_degrades`      | Image with 3`<!-- image -->` markers + 1 PictureResult → `splice_figure_markers` returns markdown unchanged |

## Risks

1. **0.6 threshold too aggressive for large infographics.** Some documents may have infographics covering >60% of a page. **Mitigation:** threshold is configurable via `PICTURE_PAGE_COVERAGE_THRESHOLD` env var. If a specific corpus has large charts, operators can raise it to 0.8 or 0.9.
2. **Multiple `<!-- image -->` markers from standalone image.** Docling may detect sub-regions within a standalone image and emit multiple markers. **Mitigation:** `splice_figure_markers`'s marker-count mismatch guard (converters.py:1455) bails and leaves markers as-is. No data loss vs current behavior.
3. **Synthetic PictureResult size for large images.** A 10MB TIFF file would produce a 10MB `png_bytes` entry in memory. **Mitigation:** this matches the existing behavior for PDF picture crops (which can also be large at 300 DPI); `_enrich_image_blocks` pops `png_bytes` after uploading to MinIO (audit finding 11 fix).

## Surfaces touched

| Module                              | Change                                                                                                                    |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `src/pageindex_mcp/converters.py` | `_PICTURE_PAGE_COVERAGE_THRESHOLD` constant + area check in `_recover_picture_text` Phase 1 loop                      |
| `src/pageindex_mcp/client.py`     | Synthetic`PictureResult` after `image_to_markdown()` in standalone image branch; `PictureResult` + `Path` imports |
| `tests/test_image_blocks.py`      | P0a/P0b unit tests                                                                                                        |

## References

- [Investigation report](../../audit/OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION_2026-07-27.md)
- [Image block ingestion scaling audit](../../audit/IMAGE_BLOCK_INGESTION_SCALING_AUDIT_2026-07-21.md)
- [OCR escalation contract](../contracts/ocr-01.yaml) (OCR-01-C1/C2/C3)
- [Format converter contract](../contracts/conv-01.yaml) (CONV-01-C5)
- [RFC-005: Hard corpus ingestion fixes](005-hard-corpus-ingestion-fixes.md) — original OCR escalation design
- [RFC-010: Corpus gap remediation](010-corpus-gap-remediation.md) — D1 image-dominant escalation
- [RFC-015: Corpus audit remediation](015-corpus-audit-remediation.md) — D6 per-picture enrichment
- [RFC-016: VLM garble fallback](016-vlm-garble-fallback.md) — VLM last-resort escalation
