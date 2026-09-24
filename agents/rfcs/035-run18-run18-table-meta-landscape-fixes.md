<!-- Space: CITRA -->
<!-- Title: RFC-035: Run-18 Table Header Collapse, Content-Class Gap, and Landscape Extraction -->
<!-- Folder: RFCs -->

# RFC-035: Run-18 Table Header Collapse, Content-Class Gap, and Landscape Extraction

**Run:** 18
**Audit:** [audit/CORPUS_REINGESTION_AUDIT_RUN-18.md](../../audit/CORPUS_REINGESTION_AUDIT_RUN-18.md)
**Status:** Draft

## Summary

Run 18 audited all 24 of 25 corpus documents (world-stats-pocketbook-2023.pdf absent from store). The tally is 8 PASS, 12 MARGINAL, 3 FAIL, 1 ERROR. Compared to Run 16, there are 4 improvements (MARGINAL/ERROR/FAIL promoted to PASS or MARGINAL), 4 regressions (PASS/MARGINAL demoted), and several stalls where pre-existing defects persist unchanged. The 3 FAIL documents suffer from RTL garbling (Arabic governance policy), enrichment-route failure (German benefit-comparison table with checkmark icons), and flat-tree hierarchy collapse (UAE Penal Code). The 1 ERROR is ward-597, now correctly rejected by the tightened RTL-reversal gate.

## Decisions

### D0: Fix degenerate-row collapse treating GFM table body rows immediately after separator as degenerate

**Scope:** _repair_docling_tables in converters.py, specifically the degenerate-row collapse branch at line 2674-2691

**Root Cause:** The degenerate-row collapse heuristic at converters.py:2674-2691 treats any row where all cells have identical content and the cell count exceeds _RFC029_TABLE_MIN_COLLAPSE_COLS as a Docling merge artifact. It has no awareness of table structure. In GFM, the header row PRECEDES the separator row (|---|), so the row immediately after the separator is the first body row, not a header. However, the first body row(s) in Docling-emitted tables for cabinet_resolution_no_21 repeat column labels (e.g., 'Fee' repeated across all columns in a sub-header row that Docling places as the first body row), triggering the degenerate collapse. The bilingual guard (D17, lines 2677-2687) only exempts mixed-script rows, not same-script repeated-label rows.

**Rationale:** The cabinet_resolution_no_21 document (fee/fine schedules) has Docling-emitted tables where the first body row after the separator repeats column labels identically across all cells. The degenerate-row collapse logic (unique_vals==1 and len(cells)>_RFC029_TABLE_MIN_COLLAPSE_COLS) cannot distinguish this from a genuine Docling merge artifact, so it collapses the row into a single cell, destroying the table structure for Schedules 1-5. Note: the prev_was_separator guard only protects the single row immediately following the separator; multi-row repeated-label sequences beyond the first post-separator row are not shielded by this fix and may require a broader heuristic in a follow-up RFC if observed in practice.

**Affected Documents:**
- cabinet_resolution_no_21_of_2020_concerning_service_fees_and_administrative_fines_in_the_ministry_of_human_resources_and_emiratisation (1) - Copy.pdf

**Files / Functions:**
- `src/pageindex_mcp/converters.py::_repair_docling_tables (lines 2674-2691, degenerate-row collapse branch)`

**Fix:** Track whether the previous row was a separator row (|---|). When processing a row that passes the degenerate check (unique_vals==1 and len(cells)>threshold), skip the collapse if the immediately preceding row was a separator -- this row is the first body row and the repeated labels are structural, not a Docling merge artifact. Implementation: add a boolean prev_was_separator flag, set it to True when a separator row is detected (line 2667-2671), reset to False after each non-separator row. Insert a guard `if prev_was_separator: new_line = "| " + " | ".join(cells) + " |"; out.append(new_line); continue` before the collapse at line 2688. The preserved row MUST be re-emitted in normalized minimal-padding form (consistent with lines 2694-2696) rather than appending the raw original line verbatim, to maintain whitespace normalization consistency across all emitted rows.

**Limitations:** This guard only shields the single first post-separator row. If Docling emits multi-row repeated-label sequences beyond the first post-separator row, those rows will still be collapsed. The fix should be validated against the actual repaired markdown of cabinet_resolution_no_21 before claiming full resolution of the finding.

**Effort:** Small (1-2 hours). Single-function change, well-isolated logic, no cross-module dependencies.

**Test Strategy:** Unit test: construct a GFM table with a separator row followed by a row of identical cells (simulating a repeated-label first body row), feed it through _repair_docling_tables, assert all cells are preserved AND the output row uses normalized minimal-padding format. Regression test: construct a genuine degenerate row (NOT after a separator) and assert it IS still collapsed. Integration test (requires remote Docling/MinIO infrastructure): re-ingest cabinet_resolution_no_21 and verify Schedule 1-5 table body rows are intact in the processed JSON.

---

### D1: Expose pdf inspector classification to scoring for tree-path documents

**Scope:** Scoring logic in helpers.py (classify_verdict) and its consumption of tree-path metadata

**Root Cause:** The Reitlehrer document's MARGINAL verdict was attributed to 'missing content_class metadata.' However, the tree-path meta construction at client.py:2021-2034 was never designed to carry content_class -- this is by design. content_class presence in the .meta.json sidecar is the deliberate flat-vs-tree discriminator (FLAT-02-C1/C3, storage.py:504-507): read_registry_fields (storage.py:607+) selects processed/<id>.flat.json vs .json based on content_class, and helpers.py:203-209 uses content_class presence as the flat-doc adapter trigger. Populating content_class on tree-path documents would misroute downstream readers to nonexistent .flat.json artifacts.

The pdf_classification result IS already referenced at client.py:2057-2058, which writes `meta['inspector_class'] = pdf_classification.get('pdf_type')`. The inspector_class field is included in _META_FIELDS (storage.py lines 477, 544) and is already persisted in tree-doc sidecars. The value the original D1 wanted to expose already reaches the tree-doc sidecar under inspector_class -- but only for the sidecar write, not for scoring. The `classify_verdict(...)` call site itself is at client.py:2017-2019, which executes BEFORE the `meta` dict is constructed (line 2021) and well before `meta['inspector_class']` is set (line 2057-2058, inside the PDF-only `ext == ".pdf"` branch). `meta['inspector_class']` therefore does not exist yet at the point classify_verdict is invoked. However, `pdf_classification` itself is a parameter of `index()` (declared at line 804) and is in scope at line 2017, so the correct fix reads `pdf_classification.get('pdf_type') if pdf_classification else None` directly at the call site -- it does NOT read it back off the not-yet-built `meta` dict.

Furthermore, pdf_type values ('scanned', 'image_based', 'text_based', etc.) are a fundamentally different taxonomy from content_class values ('flat_prose', 'flat_mixed', 'image_standalone', 'ocr_*'). Writing pdf_type into content_class would break downstream scoring/routing (helpers.py:1644-1760) that dispatches on content_class.startswith('flat_') / content_class.startswith('ocr_') / content_class == 'image_standalone'.

The actual regression framing is also suspect: if the tree path never wrote content_class since inception, then Run 16's PASS verdict for Reitlehrer implies a scoring-criteria change between Run 16 and Run 18 -- not a pipeline data regression. This should be investigated as part of the fix.

**Rationale:** The correct fix is scoring-side, not pipeline-side. The classify_verdict function in helpers.py should read inspector_class (already available in tree-doc sidecars) when content_class is absent (i.e., for tree-path documents), and use it to inform verdict logic where appropriate. This avoids violating the FLAT-02-C1/C3 discriminator invariant and avoids mixing incompatible value vocabularies.

**Affected Documents:**
- Reitlehrer - Schaeden am Berittpferd.pdf
- Potentially other tree-path documents whose verdicts could benefit from inspector classification awareness

**Files / Functions:**
- `src/pageindex_mcp/helpers.py::classify_verdict (content_class consumption and category-specific promotion logic)`
- `src/pageindex_mcp/client.py::index() (call site change required -- classify_verdict is invoked at line 2017-2019, before the meta dict exists (line 2021) and before meta['inspector_class'] is set (line 2057-2058); the fix must pass pdf_classification.get('pdf_type') directly at the call site, not read it off meta)`
- `src/pageindex_mcp/storage.py (no changes needed -- inspector_class is already in _META_FIELDS)`

**Fix:** In classify_verdict (helpers.py), when the caller passes content_class="" (the default for tree-path documents), check whether inspector_class is available in the document metadata. If inspector_class is present and content_class is empty, use inspector_class to select the appropriate category-specific promotion path (cat_c for tree docs, with optional inspector-informed adjustments). This requires threading inspector_class through to classify_verdict as a new optional parameter, and updating the client.py:2017-2019 call site to pass `pdf_classification.get('pdf_type') if pdf_classification else None` (pdf_classification is already an `index()` parameter in scope at that line) -- NOT the full meta dict, since meta does not yet contain inspector_class at that point in the function. The fix must NOT set content_class on tree-path documents and must NOT write content_class into the tree-doc sidecar.

Additionally, investigate why Reitlehrer scored PASS in Run 16 without content_class: if the scoring criteria changed between runs, document the change and confirm that D1 is the correct remediation (rather than reverting a scoring threshold change).

**Effort:** Small-Medium (2-4 hours). Requires understanding classify_verdict's category dispatch logic and threading a new parameter, plus investigation of the Run 16 vs Run 18 scoring delta.

**Test Strategy:** Unit test: call classify_verdict with content_class="" and inspector_class="text_based" (simulating a tree-path document), verify it selects the correct promotion path without triggering flat-doc logic. Regression test: verify that content_class presence in .meta.json is still exclusive to flat-doc sidecars -- tree-doc sidecars must NOT contain content_class after the fix. Invariant test: verify read_registry_fields still correctly selects .flat.json vs .json based on content_class presence. Integration test: re-ingest Reitlehrer and verify the verdict improves without breaking the flat-vs-tree discriminator.

---

### D2: Landscape-oriented page extraction for tabular/chart content

**Scope:** converters.py Docling text extraction path, potentially client.py routing logic

**Root Cause:** Docling's text extraction pipeline does not adequately handle landscape-oriented pages. When pages are rotated 90 degrees, the text extraction coordinate system misaligns with the actual content layout, producing minimal text output. The pipeline has no orientation detection step and no fallback for pages where text extraction yields abnormally low character counts. The portrait version succeeds because Docling's picture detection fires on chart regions, but the landscape rotation prevents this detection from working.

**Rationale:** uae_numbers_english_page_16_17_landscape produces only 748 chars across 2 landscape pages, byte-identical across 4+ ingestion runs, confirming this is a deterministic architectural limitation rather than a transient failure. The portrait companion of the same content passes via flat-mixed path with picture results (764 chars + 4 chart PictureResults). The landscape version stays on the tree path and loses nearly all tabular/chart content.

**Affected Documents:**
- uae_numbers_english_page_16_17_landscape - Copy.pdf

**Files / Functions:**
- `src/pageindex_mcp/converters.py::pdf_to_markdown_docling (Docling export path)`
- `src/pageindex_mcp/client.py::index() (routing logic, potential fallback trigger)`

**Fix:** Two-phase implementation (VLM enrichment excised to a separate future RFC -- see Out of Scope). Phase 1 (detection): Add page-orientation detection using PyMuPDF page.rotation or page width>height heuristic in the pre-extraction probe. Tag pages as landscape in extraction metadata. Phase 2 (fallback): When landscape pages produce below-threshold character counts (e.g., <500 chars/page), trigger a fallback path: rasterize the page at 300 DPI, rotate the image to portrait orientation, and re-extract via Docling or OCR. The re-extraction MUST also re-evaluate routing: if the rotated image triggers Docling's picture detection (as it does for the portrait companion), the document should route to the flat-mixed path with PictureResults rather than staying on the tree path. Without this routing re-evaluation, the success criterion (char count > 748) could be met while the doc remains on the tree path and still loses chart content.

**Routing interaction:** The portrait companion of uae_numbers_english_page_16_17_landscape passes because it routes to the flat-mixed path with PictureResults (764 chars + 4 chart PictureResults). The Phase 2 rasterize-rotate-reextract fallback only helps if the re-extraction also changes routing so that picture detection and flat routing fire. The implementation must ensure the re-extracted content is fed back through the same classification/routing logic that the portrait version traverses.

**Effort:** Large (3-5 days). Phase 1 alone is small (half day), but the rasterization fallback (Phase 2) requires new image pipeline code, DPI configuration, rotation logic, and routing re-evaluation.

**Test Strategy:** Phase 1: Unit test orientation detection against known landscape and portrait PDFs. Phase 2: Integration test (requires remote Docling/MinIO infrastructure) that ingests uae_numbers_english_page_16_17_landscape and verifies char count exceeds 748 (the current floor). Comparison test: verify landscape and portrait versions of the same content produce comparable char counts (within 2x). Regression test: verify portrait-only documents are unaffected by the new detection/fallback logic. Routing test: verify that a landscape document whose rotated re-extraction triggers picture detection is routed to the flat-mixed path (not left on the tree path).


## Implementation Plan

| Batch | Decisions | Rationale |
|-------|-----------|-----------|
| 1 | D0 | Small-complexity fix, well-isolated in converters.py. Fixes a regression (PASS->MARGINAL on cabinet_resolution_no_21). |
| 2 | D1 | Small-medium complexity. Requires investigation of Run 16 vs Run 18 scoring delta before implementation, plus threading inspector_class through classify_verdict. No dependency on D0. |
| 3 | D2 | Large complexity, no dependency on D0/D1. Requires architecture decisions around orientation detection strategy and routing re-evaluation for rotated re-extractions. |

## Test Strategy

| Decision | Title | Test Approach |
|----------|-------|---------------|
| D0 | Fix degenerate-row collapse treating GFM table body rows immediately after separator as degenerate | Unit test: construct a GFM table with a separator row followed by a row of identical cells, feed it through _repair_docling_tables, assert all cells are preserved in normalized minimal-padding format. Regression test: construct a genuine degenerate row (NOT after a separator) and assert it IS still collapsed. Integration test (requires remote Docling/MinIO): re-ingest cabinet_resolution_no_21 and verify Schedule 1-5 table body rows are intact in the processed JSON. |
| D1 | Expose pdf inspector classification to scoring for tree-path documents | Unit test: call classify_verdict with content_class="" and inspector_class="text_based", verify correct promotion path without triggering flat-doc logic. Regression test: verify content_class in .meta.json remains exclusive to flat-doc sidecars. Invariant test: verify read_registry_fields selects .flat.json vs .json based on content_class presence. Integration test (requires remote Docling/MinIO): re-ingest Reitlehrer and verify verdict improves without breaking the flat-vs-tree discriminator. |
| D2 | Landscape-oriented page extraction for tabular/chart content | Phase 1: Unit test orientation detection against known landscape and portrait PDFs. Phase 2: Integration test (requires remote Docling/MinIO) that ingests uae_numbers_english_page_16_17_landscape and verifies char count exceeds 748. Comparison test: verify landscape and portrait versions produce comparable char counts (within 2x). Regression test: verify portrait-only documents are unaffected. Routing test: verify landscape doc whose rotated re-extraction triggers picture detection routes to flat-mixed path. |

## Risks

- D0: The prev_was_separator guard only shields the single first post-separator row. If Docling emits multi-row repeated-label sequences beyond that row, they will still be collapsed. This needs validation against the actual repaired markdown of cabinet_resolution_no_21 before claiming full resolution. A broader heuristic (e.g., collapse suppression for the first N body rows, or a minimum-unique-value threshold) may be needed in a follow-up RFC.
- D0: Upstream Docling service redeployments can change the table markdown format, potentially re-triggering the collapse condition in new ways. The fix addresses the symptom but not the root instability of depending on Docling's table formatting.
- D0: Integration tests for D0 (and D1/D2) require live re-ingestion against remote Docling/MinIO infrastructure. This is an environment prerequisite that must be satisfied before integration test execution.
- D1: The Run 16 PASS verdict for Reitlehrer with no content_class may indicate a scoring-criteria change between runs rather than a pipeline data regression. If the scoring threshold changed, D1 may not be the correct remediation -- reverting the threshold change might be simpler. This must be investigated before implementation.
- D1: Threading inspector_class through classify_verdict introduces a new parameter to a function with an already complex signature. Care must be taken to ensure the inspector_class-based promotion path does not interfere with existing content_class-based category dispatch.
- D1 (acknowledged, minor): The audit framed Reitlehrer as a between-runs regression ('content_class field dropped/unpopulated ... introduced between runs'), but the tree path never populated content_class since inception. This discrepancy affects whether D1 is purely a new feature vs a regression fix; the investigation step in the fix plan addresses this.
- D2: Page rasterization at 300 DPI for landscape fallback will significantly increase processing time and memory usage for landscape-heavy documents. A 292-page document with many landscape pages could exhaust memory or timeout.
- D2: The orientation detection heuristic (width > height) may produce false positives for unusual page sizes (e.g., A5 landscape that is actually portrait content). PyMuPDF page.rotation is more reliable but may not be set for all PDFs.
- D2 (acknowledged, minor): The portrait companion passes because it routes to the flat-mixed path with PictureResults; the Phase 2 rasterize-rotate-reextract only helps if the re-extraction also changes routing so picture detection and flat routing fire. The fix plan now explicitly requires routing re-evaluation (see D2 Fix and Routing interaction sections), but the interaction is complex and may surface edge cases during implementation.
- Cross-cutting: All three fixes target the converters/client/helpers pipeline. Deploying them together without incremental corpus validation risks compounding regressions that are harder to attribute to individual changes.

## Out of Scope

- Config-gated VLM image description for landscape chart-heavy pages (originally D2 Phase 3) -- excised from this RFC into a separate future RFC. Requires non-Granite VLM model selection decision (user-LOCKED constraint) and introduces a new external dependency and cost center. Overlaps the existing out-of-scope bullet below and is decision-blocked independently of D2 Phases 1+2.
- VLM-based chart structure enrichment (series extraction, numeric label parsing) for standalone images -- requires non-Granite VLM model selection decision (user-LOCKED constraint)
- ward-597 full-page OCR fallback for scanned Arabic pages where Docling detects no Picture region (tracked since RFC-028 D5, confirmed true-positive gate rejection in Run 18)
- Arabic garble-gate blind spot for single-letter fragment pattern (سياسة حوكمة) -- tracked under RFC-034 D21 and RFC-033 D2 Part B
- Depth-adequacy scoring proportional to document complexity (Haftpflicht depth-2 gap) -- classify_verdict threshold refinement deferred to a dedicated scoring-calibration RFC
- Icon/checkmark semantic enrichment in table contexts (Unfallversicherung) -- deferred per RFC-034 C6 as high-complexity
- English and Arabic heading injection for legal hierarchy (Book/Part/Chapter and مادة/باب/فصل patterns) -- medium-complexity work tracked in the full traces but not among the 3 assigned findings
- Remote Docling heading-injection post-processing gap (client.py remote path skips injectors) -- medium-complexity, related to hierarchy but not among assigned findings
