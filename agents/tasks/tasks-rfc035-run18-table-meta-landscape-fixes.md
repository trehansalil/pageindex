<!-- Space: CITRA -->
<!-- Title: Tasks: RFC-035 -- Run-18 Table Header Collapse, Content-Class Gap, and Landscape Extraction -->
<!-- Folder: Tasks -->

# Tasks: RFC-035 -- Run-18 Table Header Collapse, Content-Class Gap, and Landscape Extraction

## Traceability

| Artifact             | Reference                                                                                                                                  |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Governing RFC(s)     | [RFC-035: Run-18 Table Header Collapse, Content-Class Gap, and Landscape Extraction](../rfcs/035-run18-run18-table-meta-landscape-fixes.md) |
| Design Document      | [design-rfc035-run18-table-meta-landscape-fixes.md](../designs/design-rfc035-run18-table-meta-landscape-fixes.md)                           |
| Audit                | [audit/CORPUS_REINGESTION_AUDIT_RUN-18.md](../../audit/CORPUS_REINGESTION_AUDIT_RUN-18.md)                                                  |
| Hard Rules (binding) | [CLAUDE.md § Hard Rules](../../CLAUDE.md#hard-rules)                                                                                       |

## Overview

Three independent code-level fixes land in ascending complexity order: D0 guards `_repair_docling_tables`'s degenerate-row collapse against false positives on first post-separator body rows (converters.py); D1 threads `inspector_class` through `classify_verdict` so tree-path documents without `content_class` reach the correct promotion path (helpers.py), preserving the FLAT-02 flat-vs-tree sidecar invariant; D2 adds a landscape-orientation detection probe and a rasterize-rotate-reextract fallback with routing re-evaluation for rotated pages (converters.py / client.py). Each batch is unit-tested and property-tested in isolation; corpus re-ingestion and verification are handled by the separate corpus-cycle skill and are explicitly out of scope for these tasks.

## Tasks

- [x] <a id="1-batch-1--d0-degenerate-row-collapse-guard"></a>1. Batch 1 -- D0 Degenerate-Row Collapse Guard ([RFC-035 D0](../rfcs/035-run18-run18-table-meta-landscape-fixes.md#d0-fix-degenerate-row-collapse-treating-gfm-table-body-rows-immediately-after-separator-as-degenerate), [Design Architecture Decisions](../designs/design-rfc035-run18-table-meta-landscape-fixes.md#architecture-decisions), [Design Flow: D0](../designs/design-rfc035-run18-table-meta-landscape-fixes.md#flow-d0-degenerate-row-collapse-with-prev_was_separator-guard))

  - [X] <a id="task-1-1"></a>1.1 Add `prev_was_separator` tracking flag to `_repair_docling_tables`

    - In `src/pageindex_mcp/converters.py::_repair_docling_tables`, introduce a boolean `prev_was_separator` initialized to `False` before the row-processing loop.
    - Set `prev_was_separator = True` at the point where a separator row (`|---|---|`) is detected (existing detection logic near lines 2667-2671).
    - Reset `prev_was_separator = False` after any non-separator row is processed (both guarded and collapsed paths), so the flag only ever protects the single row immediately following a separator.
    - _Requirements: RFC-035 D0 Fix | Design Property 1_
  - [X] <a id="task-1-2"></a>1.2 Insert the guard before the degenerate-row collapse branch

    - In the degenerate-row collapse branch (converters.py lines 2674-2691), before the existing collapse logic, add: `if prev_was_separator: new_line = "| " + " | ".join(cells) + " |"; out.append(new_line); continue`.
    - Ensure the guard fires only when the row passes the existing degenerate check (`unique_vals == 1 and len(cells) > _RFC029_TABLE_MIN_COLLAPSE_COLS`) AND `prev_was_separator` is `True`.
    - Ensure the guard runs independently of and does not disturb the existing RFC-034 D17 mixed-script (Arabic+Latin) guard at lines 2677-2687 -- both guards must be able to fire on their own conditions before the collapse at line 2688.
    - Emit the preserved row using the same normalized minimal single-space-padding format as the rest of the function's output (no raw verbatim line append), per Design Key Design Principle 3.
    - _Requirements: RFC-035 D0 Fix | Design Architecture Decisions D0 | Design Property 1_
  - [x] <a id="task-1-3"></a>1.3 Unit tests for the `prev_was_separator` guard

    - **Property 1: First post-separator body rows are never collapsed**
    - Test: GFM table with header row, separator row, and a first body row where all cells are identical (simulating Docling's repeated-label emission) -- assert the row is preserved in normalized form and the collapsed-row counter does not increment for it.
    - Test: a genuine degenerate row NOT immediately following a separator -- assert it IS still collapsed to a single-cell row (regression guard for pre-existing behavior).
    - Test: first AND second post-separator rows both have identical cells -- assert only the first is guarded and the second is collapsed (documents the intentional single-row scope limitation).
    - Test: flag reset -- a degenerate row at row 3+ (after a guarded row 1 and a normal row 2) must still be collapsed, proving `prev_was_separator` resets after the first non-separator row.
    - **Validates: RFC-035 D0 | Design Property 1 | Design Testing Strategy D0**
  - [x] <a id="task-1-4"></a>1.4 Property-based test for collapse trigger conditions

    - **Property 1 (generalized): Collapse fires only under all three conditions simultaneously**
    - Generate random GFM tables varying cell counts and identical/non-identical cell values; assert collapse only fires when (a) all cells are identical, (b) cell count exceeds `_RFC029_TABLE_MIN_COLLAPSE_COLS`, AND (c) the row does not immediately follow a separator.
    - **Validates: RFC-035 D0 | Design Property 1 | Design Testing Strategy Property-Based Tests**
- [x] <a id="2-checkpoint--batch-1"></a>2. Checkpoint -- Batch 1

  - Run `uv run pytest tests/ -k "repair_docling_tables or prev_was_separator or degenerate"` and verify all Batch 1 unit and property tests pass.
  - Confirm no regression in existing RFC-034 D17 mixed-script guard tests.
  - Ask the user if questions arise before proceeding.
- [x] <a id="3-batch-2--d1-inspector_class-threading"></a>3. Batch 2 -- D1 Inspector-Class Threading Through `classify_verdict` ([RFC-035 D1](../rfcs/035-run18-run18-table-meta-landscape-fixes.md#d1-expose-pdf-inspector-classification-to-scoring-for-tree-path-documents), [Design Architecture Decisions](../designs/design-rfc035-run18-table-meta-landscape-fixes.md#architecture-decisions), [Design Flow: D1](../designs/design-rfc035-run18-table-meta-landscape-fixes.md#flow-d1-classify_verdict-with-inspector_class-for-tree-path-documents))

  - [X] <a id="task-3-1"></a>3.1 Investigate the Run-16 vs Run-18 Reitlehrer scoring delta

    - Compare `classify_verdict` logic and thresholds as of Run 16 against the current Run 18 state to determine whether the Reitlehrer regression (PASS to MARGINAL) is caused by a scoring-criteria change between runs rather than a pipeline data change.
    - Document the finding inline as a code comment or commit message note near the `classify_verdict` cat_c branch: state explicitly whether D1 (threading `inspector_class`) is the correct remediation, or whether a threshold revert is warranted instead.
    - If the investigation concludes a threshold revert is the correct fix instead of D1, stop and flag this to the user before proceeding with 3.2-3.4 (per Design Launch Constraint 2).
    - _Requirements: RFC-035 D1 Fix (investigation step) | Design Launch Constraint 2_
  - [X] <a id="task-3-2"></a>3.2 Add `inspector_class` optional parameter to `classify_verdict`

    - In `src/pageindex_mcp/helpers.py::classify_verdict`, add a new optional parameter (e.g., `inspector_class: str | None = None`).
    - When `content_class` is empty/falsy (tree-path document) and `inspector_class` is present, use it to inform the cat_c promotion branch's confidence/threshold logic without changing the branch selection itself (content_class remains the sole selector between cat_a/cat_b/cat_c).
    - When `content_class` is non-empty (flat-doc), `inspector_class` must have no effect -- the existing cat_a (`ocr_*`)/cat_b (`flat_*`) dispatch is unchanged (Design Property 2d).
    - When `inspector_class` is absent or unrecognized, the existing cat_c promotion path must fire unchanged (graceful degradation, per Design Error Handling item 5).
    - Do NOT set `content_class` on tree-path documents and do NOT write `content_class` into tree-doc sidecars anywhere in this change.
    - _Requirements: RFC-035 D1 Fix | Design Architecture Decisions D1 | Design Property 2_
  - [x] <a id="task-3-3"></a>3.3 Thread `inspector_class` from `client.index()` into the `classify_verdict` call site

    - In `src/pageindex_mcp/client.py::index()`, the `classify_verdict` call site (line 2017-2019) executes BEFORE the `meta` dict is constructed (line 2021) and BEFORE `meta['inspector_class']` is set (line 2057-2058, inside the PDF-only `ext == ".pdf"` branch) -- `meta['inspector_class']` does not exist yet at that point, so do NOT read it off `meta`. Instead, pass `pdf_classification.get('pdf_type') if pdf_classification else None` directly into the `classify_verdict` call as the new `inspector_class` argument (`pdf_classification` is already an `index()` parameter, declared at line 804, and is in scope at line 2017).
    - Confirm no changes are needed to `save_doc_meta` or the sidecar writer -- `inspector_class` is already in `_META_FIELDS` (storage.py) and already persisted via the existing (unchanged) `meta['inspector_class']` write at line 2057-2058; this task only wires `pdf_classification` into the verdict call site, upstream of and independent from that existing write.
    - _Requirements: RFC-035 D1 Fix | Design Architecture Decisions D1_
  - [x] <a id="task-3-4"></a>3.4 Unit tests for `classify_verdict` inspector_class threading

    - **Property 2: Tree-doc sidecars never contain content_class; classify_verdict uses inspector_class for tree-path promotion**
    - Test: call `classify_verdict(structure=valid_tree, content_class='', validate_reason=None, inspector_class='text_based')` with a tree that would reach cat_c -- assert it reaches `cat_c_promoted` (not cat_a or cat_b).
    - Test: call `classify_verdict` with `content_class='flat_mixed'` and `inspector_class='text_based'` -- assert it reaches cat_b (content_class takes precedence over inspector_class).
    - Test: call `classify_verdict` with `content_class=''` and `inspector_class=None` -- assert it falls through to default cat_c behavior (backward compatibility).
    - **Validates: RFC-035 D1 | Design Property 2 | Design Testing Strategy D1**
  - [x] <a id="task-3-5"></a>3.5 Sidecar invariant test

    - **Property 2 (invariant): content_class exclusivity to flat-doc sidecars**
    - Construct a tree-doc meta dict containing `inspector_class` but no `content_class`, run it through `save_doc_meta`, read back the sidecar, and assert the `content_class` key is absent while the `inspector_class` key is present.
    - Additionally assert `read_registry_fields` called with `content_class=None` selects `processed/<id>.json` (not `.flat.json`), confirming the FLAT-02-C1/C3 discriminator is unaffected by this change.
    - **Validates: RFC-035 D1 | Design Property 2b/2c | Design Testing Strategy D1 (Sidecar invariant test)**
  - [x] <a id="task-3-6"></a>3.6 Property-based test for content_class/inspector_class precedence

    - **Property 2 (generalized): content_class always takes routing precedence**
    - Generate random `(content_class, inspector_class)` pairs; assert `content_class` always determines cat_a/cat_b/cat_c branch selection and `inspector_class` only ever influences behavior within the cat_c branch.
    - **Validates: RFC-035 D1 | Design Property 2 | Design Testing Strategy Property-Based Tests**
- [x] <a id="4-checkpoint--batch-2"></a>4. Checkpoint -- Batch 2

  - Run `uv run pytest tests/ -k "classify_verdict or inspector_class or content_class"` and verify all Batch 2 unit and property tests pass.
  - Confirm the Task 3.1 investigation finding is documented and, if it concluded D1 is not the correct fix, confirm the user has been consulted before Batch 2 work is considered complete.
  - Confirm no `.meta.json` fixture or test asserts `content_class` presence on a tree-doc sidecar.
  - Ask the user if questions arise before proceeding.
- [x] <a id="5-batch-3--d2-landscape-detection-and-fallback"></a>5. Batch 3 -- D2 Landscape Orientation Detection and Rasterize-Rotate-Reextract Fallback ([RFC-035 D2](../rfcs/035-run18-run18-table-meta-landscape-fixes.md#d2-landscape-oriented-page-extraction-for-tabularchart-content), [Design Architecture Decisions](../designs/design-rfc035-run18-table-meta-landscape-fixes.md#architecture-decisions), [Design Flow: D2](../designs/design-rfc035-run18-table-meta-landscape-fixes.md#flow-d2-landscape-detection-and-rasterize-rotate-reextract-fallback))

  - [X] <a id="task-5-1"></a>5.1 Implement Phase 1: pre-extraction landscape orientation probe

    - In `src/pageindex_mcp/converters.py::pdf_to_markdown_docling`, add a pre-extraction probe using PyMuPDF's `page.rotation` attribute (rotation % 180 != 0) combined with a `page.width > page.height` geometric heuristic.
    - Tag pages identified as landscape in extraction metadata (a per-page flag threaded alongside existing extraction state).
    - Ensure the probe is read-only and does not alter the primary (non-fallback) extraction path -- portrait-only documents must be unaffected (Design Testing Strategy D0-analog regression concern for D2).
    - _Requirements: RFC-035 D2 Fix (Phase 1) | Design Architecture Decisions D2_
  - [X] <a id="task-5-2"></a>5.2 Add `LANDSCAPE_CHAR_THRESHOLD` config and below-threshold detection

    - Add a configurable threshold (default `<500 chars/page`) via a `LANDSCAPE_CHAR_THRESHOLD` env var / config setting.
    - After primary Docling extraction, for pages tagged landscape in 5.1, compare the extracted char count against the threshold to determine whether the Phase 2 fallback should trigger.
    - _Requirements: RFC-035 D2 Fix (Phase 2 trigger) | Design Architecture Decisions D2_
  - [x] <a id="task-5-3"></a>5.3 Implement Phase 2: rasterize-rotate-reextract fallback

    - When a landscape-tagged page is below `LANDSCAPE_CHAR_THRESHOLD`, rasterize the page at 300 DPI via PyMuPDF, rotate the rasterized image to portrait orientation, and re-extract via Docling (or OCR fallback if Docling re-extraction is unavailable).
    - On rasterization or rotation failure, log a warning and fall through to the original (degraded) extraction rather than raising an exception -- the document must proceed with its original low-char-count extraction and let `classify_verdict`'s node_count/depth/max_leaf_ratio logic surface the resulting MARGINAL/FAIL verdict naturally (Design Error Handling item 6).
    - _Requirements: RFC-035 D2 Fix (Phase 2) | Design Architecture Decisions D2 | Design Error Handling item 6_
  - [x] <a id="task-5-4"></a>5.4 Wire routing re-evaluation for fallback re-extraction output

    - At the boundary between `converters.py` and `client.py::index()`, ensure re-extracted content from the Phase 2 fallback (including any `PictureResults`) is fed back through the same classification/routing logic the portrait companion traverses.
    - If the re-extracted output triggers Docling's picture detection (producing `PictureResults`), the document must route to the flat-mixed path rather than remaining on the tree path -- this re-evaluation is mandatory per Design Launch Constraint 5 and must not be skipped even if the char-count threshold alone is satisfied.
    - Do not suppress any `bidi_degraded`/`visual_order_garble` gate that `validate_tree` may raise on re-extracted content; the D2 fallback must not bypass existing garble detection (Design Error Handling item 2).
    - _Requirements: RFC-035 D2 Fix (Routing interaction) | Design Key Design Principle 8 | Design Launch Constraint 5_
  - [x] <a id="task-5-5"></a>5.5 Unit tests for orientation probe

    - **Property 3 (partial): Landscape pages are correctly tagged**
    - Test: mock a PyMuPDF page with `rotation=90` and `width>height` -- assert it is tagged as landscape.
    - Test: mock a portrait page (`rotation=0`, `height>width`) -- assert it is NOT tagged as landscape.
    - **Validates: RFC-035 D2 | Design Property 3 | Design Testing Strategy D2**
  - [x] <a id="task-5-6"></a>5.6 Unit tests for fallback trigger/skip logic

    - **Property 3 (partial): Fallback triggers only when landscape AND below-threshold**
    - Test: mock a landscape-tagged page with 200 chars extracted -- assert the rasterize-rotate-reextract path is invoked.
    - Test: mock a landscape-tagged page with 2000 chars extracted -- assert the fallback is NOT invoked (above threshold).
    - Test: mock a portrait page with 200 chars extracted -- assert the fallback is NOT invoked (not landscape; guards against false-positive rescue of legitimately sparse pages like cover/divider pages).
    - **Validates: RFC-035 D2 | Design Property 3 | Design Testing Strategy D2**
  - [x] <a id="task-5-7"></a>5.7 Unit test for rasterization failure fallthrough

    - Test: mock a rasterization failure (PyMuPDF render raises) during the Phase 2 fallback -- assert the function logs a warning and returns the original (pre-fallback) extraction output rather than raising.
    - **Validates: RFC-035 D2 | Design Error Handling item 6**
  - [x] <a id="task-5-8"></a>5.8 Unit test for routing re-evaluation

    - Test: mock a Phase 2 re-extraction that produces `PictureResults` -- assert the document classification output routes to flat-mixed, not the tree path.
    - Test: mock a Phase 2 re-extraction that produces no `PictureResults` -- assert the document remains on its original routing path (no incorrect forced reroute).
    - **Validates: RFC-035 D2 | Design Key Design Principle 8 | Design Launch Constraint 5**
- [x] <a id="6-checkpoint--batch-3"></a>6. Checkpoint -- Batch 3

  - Run `uv run pytest tests/ -k "landscape or orientation or rasterize or pdf_to_markdown_docling"` and verify all Batch 3 unit tests pass.
  - Confirm portrait-only document tests are unaffected by the new probe/fallback code paths (no regression).
  - Confirm the routing re-evaluation guard (Task 5.4/5.8) is present and cannot be bypassed by the char-count check alone.
  - Ask the user if questions arise before proceeding.
- [x] <a id="7-final-checkpoint"></a>7. Final Checkpoint

  - Run `uv run pytest` (full suite) and verify zero regressions across Batches 1-3.
  - Confirm all three fixes (D0, D1, D2) are independently unit-testable and were not required to depend on one another's implementation.
  - Confirm no task in this file performed corpus ingestion, re-ingestion, or verification -- those are explicitly deferred to the corpus-cycle skill per this file's scope.
  - Ask the user if questions arise before proceeding.

## Notes

- [Task 1.1](#task-1-1) through [1.2](#task-1-2)'s `prev_was_separator` guard is deliberately narrow per [Design Launch Constraint 1](../designs/design-rfc035-run18-table-meta-landscape-fixes.md#launch-constraints): it shields only the single first post-separator row. [Task 1.3](#task-1-3)'s third test case exists specifically to document (not "fix") this scope limitation -- do not expand the guard to multi-row sequences without a follow-up RFC, per the governing RFC's stated Risks section.
- [Task 3.1](#task-3-1) is a hard prerequisite for [Tasks 3.2](#task-3-2)-[3.6](#task-3-6): per [Design Launch Constraint 2](../designs/design-rfc035-run18-table-meta-landscape-fixes.md#launch-constraints), if the Run-16 vs Run-18 investigation concludes the Reitlehrer regression is a scoring-threshold change rather than a pipeline data change, threading `inspector_class` (D1) may not be the correct remediation at all. Do not proceed past 3.1 on autopilot if the investigation is inconclusive or points elsewhere -- surface it.
- [Task 3.2](#task-3-2)'s `inspector_class` parameter must NEVER cause `content_class` to be written into a tree-doc sidecar. This is the FLAT-02-C1/C3 invariant ([storage.py:504-507](../../src/pageindex_mcp/storage.py), [helpers.py:203-209](../../src/pageindex_mcp/helpers.py)) and is independently re-verified by [Task 3.5](#task-3-5)'s sidecar invariant test -- treat any failure of that test as a blocking regression, not a flaky test.
- [Task 5.4](#task-5-4)'s routing re-evaluation is not optional polish: per [Design Launch Constraint 5](../designs/design-rfc035-run18-table-meta-landscape-fixes.md#launch-constraints), shipping Phase 2 (5.3) without it can meet the raw char-count success criterion while the document silently stays on the tree path and still loses chart content -- this would be a false-positive "fix." [Task 5.8](#task-5-8) exists to catch exactly this failure mode in tests.
- [Task 5.3](#task-5-3)'s failure-fallthrough behavior (log-and-degrade, never raise) is intentional per [Design Error Handling item 6](../designs/design-rfc035-run18-table-meta-landscape-fixes.md#error-handling) -- a rasterization failure must not crash the ingestion job; it must let the existing MARGINAL/FAIL verdict machinery handle the degraded output.
- D0, D1, and D2 are independent decisions with no cross-batch code dependency; they are sequenced 1→2→3 by ascending implementation complexity (per the RFC's own Implementation Plan), not by a technical prerequisite, so the corpus-cycle skill may validate them incrementally and attribute any regression to a single batch.
- Per the dispatch instruction, corpus ingestion/re-ingestion/verification steps are explicitly excluded from this tasks file -- Integration Tests described in the Design Testing Strategy (re-ingest cabinet_resolution_no_21, Reitlehrer, uae_numbers_english_page_16_17_landscape) are the corpus-cycle skill's responsibility, not a task here.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["task-1-1", "task-3-1", "task-5-1"] },
    { "id": 1, "tasks": ["task-1-2", "task-3-2", "task-5-2"] },
    { "id": 2, "tasks": ["task-1-3", "task-1-4", "task-3-3", "task-5-3"] },
    { "id": 3, "tasks": ["task-3-4", "task-3-5", "task-3-6", "task-5-4"] },
    { "id": 4, "tasks": ["task-5-5", "task-5-6", "task-5-7", "task-5-8"] },
    { "id": 5, "tasks": ["2-checkpoint--batch-1", "4-checkpoint--batch-2", "6-checkpoint--batch-3"] },
    { "id": 6, "tasks": ["7-final-checkpoint"] }
  ]
}
```
