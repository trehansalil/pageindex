<!-- Space: CITRA -->
<!-- Title: Tasks: RFC-017 — OCR / Image-Block Pipeline Decoupling -->
<!-- Folder: Tasks -->

# Tasks: RFC-017 — OCR / Image-Block Pipeline Decoupling

## Traceability

- RFC: [RFC-017: OCR / Image-Block Pipeline Decoupling](../rfcs/017-ocr-image-block-decoupling.md)
  - [Context](../rfcs/017-ocr-image-block-decoupling.md#context)
  - [Hard Rule constraints](../rfcs/017-ocr-image-block-decoupling.md#hard-rule-constraints-claudemd-binding)
  - [Decisions](../rfcs/017-ocr-image-block-decoupling.md#decisions)
  - [D0](../rfcs/017-ocr-image-block-decoupling.md#d0--page-coverage-filter-in-_recover_picture_text)
  - [D1](../rfcs/017-ocr-image-block-decoupling.md#d1--standalone-image-enrichment-via-synthetic-pictureresult)
  - [Implementation Plan](../rfcs/017-ocr-image-block-decoupling.md#implementation-plan)
  - [Test Strategy](../rfcs/017-ocr-image-block-decoupling.md#test-strategy)
  - [Risks](../rfcs/017-ocr-image-block-decoupling.md#risks)
- Design: [design-rfc017-ocr-image-block-decoupling.md](../designs/design-rfc017-ocr-image-block-decoupling.md)
  - [Traceability](../designs/design-rfc017-ocr-image-block-decoupling.md#traceability)
  - [Overview](../designs/design-rfc017-ocr-image-block-decoupling.md#overview)
  - [Key Design Principles](../designs/design-rfc017-ocr-image-block-decoupling.md#key-design-principles)
  - [Architecture Decisions](../designs/design-rfc017-ocr-image-block-decoupling.md#architecture-decisions)
  - [Service Contracts](../designs/design-rfc017-ocr-image-block-decoupling.md#service-contracts)
  - [Design Service: converters.py](../designs/design-rfc017-ocr-image-block-decoupling.md#1-converterspy-srcpageindex_mcpconverterspy)
  - [Design Service: client.py](../designs/design-rfc017-ocr-image-block-decoupling.md#2-clientpy-srcpageindex_mcpclientpy)
  - [Correctness Properties](../designs/design-rfc017-ocr-image-block-decoupling.md#correctness-properties)
  - [Property 1 — page-coverage filter excludes full-page regions](../designs/design-rfc017-ocr-image-block-decoupling.md#property-1-page-coverage-filter-excludes-full-page-regions)
  - [Property 2 — standalone image produces synthetic PictureResult](../designs/design-rfc017-ocr-image-block-decoupling.md#property-2-standalone-image-produces-synthetic-pictureresult)
  - [Error Handling](../designs/design-rfc017-ocr-image-block-decoupling.md#error-handling)
  - [Testing Strategy](../designs/design-rfc017-ocr-image-block-decoupling.md#testing-strategy)
  - [Sequence Diagrams](../designs/design-rfc017-ocr-image-block-decoupling.md#sequence-diagrams)
- PRD: [PRD.md](../../PRD.md)

## Overview

This plan implements the two P0 fixes from [RFC-017](../rfcs/017-ocr-image-block-decoupling.md#decisions) that decouple the per-picture OCR/VLM enrichment pipeline (RFC-015 D6) from the proven page-level OCR escalation pipeline (OCR-01, RFC-005 Fix 3): [D0](../rfcs/017-ocr-image-block-decoupling.md#d0--page-coverage-filter-in-_recover_picture_text) adds a page-coverage area filter inside `_recover_picture_text()` in `src/pageindex_mcp/converters.py` so full-page `PictureItem` regions (misclassified scanned pages) fall through to page-level OCR escalation instead of being fragmented into image blocks, and [D1](../rfcs/017-ocr-image-block-decoupling.md#d1--standalone-image-enrichment-via-synthetic-pictureresult) creates a synthetic `PictureResult` in the standalone image branch of `src/pageindex_mcp/client.py` so `.jpg`/`.png`/`.tiff` uploads flow through the existing `splice_figure_markers` + `_enrich_image_blocks` enrichment path instead of losing chart content to a bare `<!-- image -->` marker. Work is organized as [Batch 0: Core Fixes](#1-batch-0-core-fixes) (the two production code changes) followed by [Batch 1: Tests](#2-batch-1-tests) (the four unit tests from the RFC's [Test Strategy](../rfcs/017-ocr-image-block-decoupling.md#test-strategy)), matching the RFC's [Implementation Plan](../rfcs/017-ocr-image-block-decoupling.md#implementation-plan) batch numbering exactly.

## Tasks

- [X] <a id="1-batch-0-core-fixes"></a>1. Batch 0 — Core fixes ([D0](../rfcs/017-ocr-image-block-decoupling.md#d0--page-coverage-filter-in-_recover_picture_text), [D1](../rfcs/017-ocr-image-block-decoupling.md#d1--standalone-image-enrichment-via-synthetic-pictureresult))

  *RFC [Implementation Plan](../rfcs/017-ocr-image-block-decoupling.md#implementation-plan) Batch 0, Steps 1-4*

  - [X] <a id="11-add-page-coverage-threshold-constant"></a>1.1 Add `_PICTURE_PAGE_COVERAGE_THRESHOLD` constant ([D0](../rfcs/017-ocr-image-block-decoupling.md#d0--page-coverage-filter-in-_recover_picture_text))

    - In `src/pageindex_mcp/converters.py`, near line 1244 (immediately after the existing `_IMAGE_ENRICH_CONCURRENCY` constant, before the `PictureResult` `TypedDict`), add:
      ```python
      _PICTURE_PAGE_COVERAGE_THRESHOLD = float(
          os.getenv("PICTURE_PAGE_COVERAGE_THRESHOLD", "0.6")
      )
      ```
    - Default `0.6` per the RFC; must be configurable via the `PICTURE_PAGE_COVERAGE_THRESHOLD` env var (no code change required for operators to raise it — see [Risk 1](../rfcs/017-ocr-image-block-decoupling.md#risks))
    - No new imports required — `os` is already imported at module top in `converters.py`
    - _Requirements:_ [D0](../rfcs/017-ocr-image-block-decoupling.md#d0--page-coverage-filter-in-_recover_picture_text) | [Design Property 1](../designs/design-rfc017-ocr-image-block-decoupling.md#property-1-page-coverage-filter-excludes-full-page-regions) | [Design Service: converters.py](../designs/design-rfc017-ocr-image-block-decoupling.md#1-converterspy-srcpageindex_mcpconverterspy)
  - [X] <a id="12-add-area-check-in-recover-picture-text"></a>1.2 Add area check in `_recover_picture_text` Phase 1 loop ([D0](../rfcs/017-ocr-image-block-decoupling.md#d0--page-coverage-filter-in-_recover_picture_text))

    - In `src/pageindex_mcp/converters.py`, inside `_recover_picture_text()`'s Phase 1 serial crop loop (currently lines 1376-1386), after `rect = _bbox_to_fitz_rect(...)` / the `if rect is None: continue` guard and before `pix = page.get_pixmap(clip=rect, dpi=300)`, insert:
      ```python
      # D0: skip regions covering >60% of page area — these are full scanned
      # pages, not embedded charts. Page-level OCR escalation (OCR-01) handles them.
      page_area = page.rect.width * page.rect.height
      if page_area > 0 and (rect.width * rect.height) / page_area > _PICTURE_PAGE_COVERAGE_THRESHOLD:
          continue
      ```
    - Guard `page_area > 0` prevents a divide-by-zero on a degenerate/zero-size page rect
    - Skipped regions must simply not appear in the `crops` dict — no entry, no `recovered[i]` downstream — so `splice_figure_markers`'s existing marker-count mismatch guard (`converters.py:1455-1463`) is the natural degrade path if the picture count no longer matches marker count
    - Do not touch Phase 2 (the OCR thread pool) — the filter only needs to act before `get_pixmap` is called, per [D0](../rfcs/017-ocr-image-block-decoupling.md#d0--page-coverage-filter-in-_recover_picture_text)'s rationale that this is pure arithmetic inside the existing fitz scope (HR4-clean, no new AGPL surface)
    - _Requirements:_ [D0](../rfcs/017-ocr-image-block-decoupling.md#d0--page-coverage-filter-in-_recover_picture_text) | [Design Property 1](../designs/design-rfc017-ocr-image-block-decoupling.md#property-1-page-coverage-filter-excludes-full-page-regions) | [Design Service: converters.py](../designs/design-rfc017-ocr-image-block-decoupling.md#1-converterspy-srcpageindex_mcpconverterspy) | [Error Handling](../designs/design-rfc017-ocr-image-block-decoupling.md#error-handling)
  - [X] <a id="13-add-synthetic-pictureresult-for-standalone-images"></a>1.3 Add synthetic `PictureResult` for standalone images ([D1](../rfcs/017-ocr-image-block-decoupling.md#d1--standalone-image-enrichment-via-synthetic-pictureresult))

    - In `src/pageindex_mcp/client.py`, add `PictureResult` to the existing import from `.converters` and add `from pathlib import Path` (or reuse an existing `Path` import if already present) to the top-of-file imports
    - In the standalone image branch (`elif ext in _IMAGE_EXTS:`, currently lines 529-541), immediately after the `image_to_markdown()` call, insert:
      ```python
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
    - `ocr_text=""` deliberately — `image_to_markdown()` already ran full-page Tesseract; the per-picture OCR text would be redundant per [D1](../rfcs/017-ocr-image-block-decoupling.md#d1--standalone-image-enrichment-via-synthetic-pictureresult)'s rationale
    - This must overwrite the branch-local `pic_results` (initialized to `[]` at line ~409) so the existing flat-branch code at `client.py:782-801` (`splice_figure_markers` + `_enrich_image_blocks`) picks it up unmodified — no changes to that downstream code are needed or in scope
    - Do not attempt to detect or de-duplicate multiple `<!-- image -->` markers here — that degradation path already exists in `splice_figure_markers`'s marker-count mismatch guard (`converters.py:1455-1463`) and is verified by [Task 2.2](#22-write-p0a-standalone-image-enrichment-tests)
    - _Requirements:_ [D1](../rfcs/017-ocr-image-block-decoupling.md#d1--standalone-image-enrichment-via-synthetic-pictureresult) | [Design Property 2](../designs/design-rfc017-ocr-image-block-decoupling.md#property-2-standalone-image-produces-synthetic-pictureresult) | [Design Service: client.py](../designs/design-rfc017-ocr-image-block-decoupling.md#2-clientpy-srcpageindex_mcpclientpy) | [HR3](../rfcs/017-ocr-image-block-decoupling.md#hard-rule-constraints-claudemd-binding) | [HR2](../rfcs/017-ocr-image-block-decoupling.md#hard-rule-constraints-claudemd-binding)
  - [X] <a id="14-checkpoint-batch-0"></a>1.4 Checkpoint — Batch 0

    - Run `uv run pytest tests/test_image_blocks.py tests/test_vlm_fallback.py tests/test_client_contract.py -q` and confirm no regressions from the [Task 1.1](#11-add-page-coverage-threshold-constant)/[Task 1.2](#12-add-area-check-in-recover-picture-text)/[Task 1.3](#13-add-synthetic-pictureresult-for-standalone-images) changes before adding new tests
    - Confirm `converters.py` still imports cleanly (`python -c "import src.pageindex_mcp.converters"` or equivalent) and `client.py`'s new `Path`/`PictureResult` imports do not collide with existing names
    - Manually re-read the edited `_recover_picture_text` Phase 1 loop and standalone image branch end-to-end to verify against [D0](../rfcs/017-ocr-image-block-decoupling.md#d0--page-coverage-filter-in-_recover_picture_text) and [D1](../rfcs/017-ocr-image-block-decoupling.md#d1--standalone-image-enrichment-via-synthetic-pictureresult) code blocks verbatim (per the codebase lesson: verify source before asserting a fix landed)
    - _Requirements:_ [D0](../rfcs/017-ocr-image-block-decoupling.md#d0--page-coverage-filter-in-_recover_picture_text) | [D1](../rfcs/017-ocr-image-block-decoupling.md#d1--standalone-image-enrichment-via-synthetic-pictureresult)
- [X] <a id="2-batch-1-tests"></a>2. Batch 1 — Tests ([RFC Test Strategy](../rfcs/017-ocr-image-block-decoupling.md#test-strategy))

  *RFC [Implementation Plan](../rfcs/017-ocr-image-block-decoupling.md#implementation-plan) Batch 1, Steps 5-8*

  - [X] <a id="21-write-p0b-page-coverage-filter-tests"></a>2.1 Write P0b page-coverage filter tests ([D0](../rfcs/017-ocr-image-block-decoupling.md#d0--page-coverage-filter-in-_recover_picture_text))

    - In `tests/test_image_blocks.py`, add `test_page_coverage_filter_skips_large_region`: construct (or fake via a minimal fitz page/region fixture) a region whose bbox covers 80% of the page area, call `_recover_picture_text()`, and assert that region's index is **not** present in the returned `crops`/`recovered` dict
    - Add `test_page_coverage_filter_keeps_small_region`: a region at 30% page area must be **present** in the result with valid, non-empty `png_bytes`
    - Add `test_page_coverage_threshold_configurable`: with `PICTURE_PAGE_COVERAGE_THRESHOLD=0.9` set (env var or monkeypatched module constant), a region at 80% page area must be **kept** — proves the threshold is not hardcoded
    - Follow existing fixture conventions in `tests/test_image_blocks.py` for constructing a minimal single-page PDF via `fitz` (or reuse whatever helper the file already uses for `_recover_picture_text` tests, e.g. `tests/test_vlm_fallback.py` / `tests/test_rfc010_converters.py` patterns) rather than inventing a new PDF-fixture style
    - **Validates:** [RFC Test Strategy](../rfcs/017-ocr-image-block-decoupling.md#test-strategy) rows `test_page_coverage_filter_skips_large_region`, `test_page_coverage_filter_keeps_small_region`, `test_page_coverage_threshold_configurable`
    - _Requirements:_ [D0](../rfcs/017-ocr-image-block-decoupling.md#d0--page-coverage-filter-in-_recover_picture_text) | [Design Property 1](../designs/design-rfc017-ocr-image-block-decoupling.md#property-1-page-coverage-filter-excludes-full-page-regions) | [Testing Strategy](../designs/design-rfc017-ocr-image-block-decoupling.md#testing-strategy) | [Risk 1](../rfcs/017-ocr-image-block-decoupling.md#risks)
  - [X] <a id="22-write-p0a-standalone-image-enrichment-tests"></a>2.2 Write P0a standalone image enrichment tests ([D1](../rfcs/017-ocr-image-block-decoupling.md#d1--standalone-image-enrichment-via-synthetic-pictureresult))

    - In `tests/test_image_blocks.py`, add `test_standalone_image_produces_synthetic_pic_result`: drive the client's standalone-image branch (or the extracted code path added in [Task 1.3](#13-add-synthetic-pictureresult-for-standalone-images)) with a `.jpg` fixture file, and assert `pic_results` has exactly one entry whose `png_bytes` equals the source file's raw bytes, `page == 1`, `bbox == {"l": 0, "t": 0, "r": 0, "b": 0}`, and `ocr_text == ""`
    - Add `test_standalone_image_marker_mismatch_degrades`: construct markdown containing 3 `<!-- image -->` markers paired with only the single synthetic `PictureResult` from [Task 1.3](#13-add-synthetic-pictureresult-for-standalone-images), call `splice_figure_markers()` directly, and assert the returned markdown is **unchanged** (the existing marker-count mismatch guard at `converters.py:1455-1463` bails, per [Risk 2](../rfcs/017-ocr-image-block-decoupling.md#risks))
    - Mock or stub `image_to_markdown()` / avoid a real Tesseract subprocess call where the test only needs to verify the `pic_results` construction, consistent with how other tests in `tests/test_image_blocks.py` and `tests/test_client_contract.py` isolate the client from real OCR binaries
    - **Validates:** [RFC Test Strategy](../rfcs/017-ocr-image-block-decoupling.md#test-strategy) rows `test_standalone_image_produces_synthetic_pic_result`, `test_standalone_image_marker_mismatch_degrades`
    - _Requirements:_ [D1](../rfcs/017-ocr-image-block-decoupling.md#d1--standalone-image-enrichment-via-synthetic-pictureresult) | [Design Property 2](../designs/design-rfc017-ocr-image-block-decoupling.md#property-2-standalone-image-produces-synthetic-pictureresult) | [Testing Strategy](../designs/design-rfc017-ocr-image-block-decoupling.md#testing-strategy) | [Risk 2](../rfcs/017-ocr-image-block-decoupling.md#risks)
  - [X] <a id="23-checkpoint-batch-1"></a>2.3 Checkpoint — Batch 1

    - Run `uv run pytest tests/test_image_blocks.py -q` and confirm all 4 new tests ([Task 2.1](#21-write-p0b-page-coverage-filter-tests), [Task 2.2](#22-write-p0a-standalone-image-enrichment-tests)) pass alongside the existing suite
    - Run the full suite `uv run pytest -q` to confirm no cross-file regressions (particularly `tests/test_client_contract.py`, `tests/test_rfc010_converters.py`, `tests/test_vlm_fallback.py`, `tests/test_imgblock_audit_findings.py` which touch adjacent code paths)
    - Confirm the RFC's [Test Strategy](../rfcs/017-ocr-image-block-decoupling.md#test-strategy) table is fully covered — 3 D0 rows + 2 D1 rows, 5 total assertions
    - _Requirements:_ [RFC Test Strategy](../rfcs/017-ocr-image-block-decoupling.md#test-strategy) | [D0](../rfcs/017-ocr-image-block-decoupling.md#d0--page-coverage-filter-in-_recover_picture_text) | [D1](../rfcs/017-ocr-image-block-decoupling.md#d1--standalone-image-enrichment-via-synthetic-pictureresult)

## Notes

- [D0](../rfcs/017-ocr-image-block-decoupling.md#d0--page-coverage-filter-in-_recover_picture_text) (page-coverage filter) and [D1](../rfcs/017-ocr-image-block-decoupling.md#d1--standalone-image-enrichment-via-synthetic-pictureresult) (synthetic PictureResult) are independent changes in disjoint files (`converters.py` vs `client.py`) — [Task 1.1](#11-add-page-coverage-threshold-constant)/[Task 1.2](#12-add-area-check-in-recover-picture-text) and [Task 1.3](#13-add-synthetic-pictureresult-for-standalone-images) can be implemented in either order or in parallel, but both must land before [Task 1.4](#14-checkpoint-batch-0)'s checkpoint per the RFC's [Implementation Plan](../rfcs/017-ocr-image-block-decoupling.md#implementation-plan) batch structure.
- Out of scope for this plan (explicitly deferred per RFC [Context § Out of scope](../rfcs/017-ocr-image-block-decoupling.md#context)): P1 separate kill-switches, P2 image blocks as prose signals, P3 skip per-picture OCR on escalation, P4 thread-local boundary completion, P5 prose-fallback verification. Do not expand scope to these during implementation.
- [Risk 1](../rfcs/017-ocr-image-block-decoupling.md#risks) (0.6 threshold too aggressive for large infographics) is mitigated by the `PICTURE_PAGE_COVERAGE_THRESHOLD` env var added in [Task 1.1](#11-add-page-coverage-threshold-constant) — no code change needed to retune per-corpus.
- [Risk 2](../rfcs/017-ocr-image-block-decoupling.md#risks) (multiple `<!-- image -->` markers from one standalone image) is covered by existing `splice_figure_markers` behavior, verified (not newly built) in [Task 2.2](#22-write-p0a-standalone-image-enrichment-tests).
- [Risk 3](../rfcs/017-ocr-image-block-decoupling.md#risks) (large synthetic `png_bytes` in memory for big TIFFs) matches existing PDF-crop behavior and is already addressed by `_enrich_image_blocks` popping `png_bytes` post-upload (audit finding 11) — no new mitigation required in this plan.
- HR3/HR4/HR5 compliance is structural (no new LLM egress, no new AGPL import, filter improves — not degrades — tree quality) per the RFC's [Hard Rule constraints table](../rfcs/017-ocr-image-block-decoupling.md#hard-rule-constraints-claudemd-binding); no dedicated task is needed to "add" compliance, only to preserve it during [Task 1.2](#12-add-area-check-in-recover-picture-text)/[Task 1.3](#13-add-synthetic-pictureresult-for-standalone-images).

## Task Dependency Graph

```json
{
  "waves": [
    {
      "wave": 1,
      "tasks": ["1.1", "1.3"]
    },
    {
      "wave": 2,
      "tasks": ["1.2"]
    },
    {
      "wave": 3,
      "tasks": ["1.4"]
    },
    {
      "wave": 4,
      "tasks": ["2.1", "2.2"]
    },
    {
      "wave": 5,
      "tasks": ["2.3"]
    }
  ]
}
```
