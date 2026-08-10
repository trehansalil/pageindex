<!-- Space: CITRA -->
<!-- Title: Tasks: RFC-036 -- Run-19 Landscape Runaway, Write-Barrier Retry, and Enrichment Propagation Fixes -->
<!-- Folder: Tasks -->

# Tasks: RFC-036 -- Run-19 Landscape Runaway, Write-Barrier Retry, and Enrichment Propagation Fixes

## Traceability

| Artifact             | Reference                                                                                                                                          |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Governing RFC(s)     | [RFC-036: Run-19 Landscape Runaway, Write-Barrier Retry, and Enrichment Propagation Fixes](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md) |
| Design Document      | [design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md)         |
| Audit                | [audit/CORPUS_REINGESTION_AUDIT_RUN-19.md](../../audit/CORPUS_REINGESTION_AUDIT_RUN-19.md)                                                          |
| Hard Rules (binding) | [CLAUDE.md § Hard Rules](../../CLAUDE.md#hard-rules)                                                                                                 |

## Overview

Five decisions land in two batches, ordered by severity per the RFC's own [Implementation Plan](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#implementation-plan). Batch 0 closes the three critical Run-19 regressions: D0 (five sub-fixes: page cap, thread kill, threshold retune, page-position splice, and a singleton-ratio fragmentation guard) closes out the uncommitted RFC-035 D2 landscape reextract runaway in `converters.py`/`helpers.py`; D1 shrinks the write-barrier delay schedule and catches `PersistenceNotVisibleError` at the `save_doc`/`save_doc_meta` call sites in `storage.py`; D2 commits the already-staged RFC-034 D19 enrichment density-preserve fix in `client.py` with zero new implementation. Batch 1 lands two lower-severity improvements that depend on Batch 0 stability: D3 adds `rtl_reversal` to the flat-routing whitelist in `client.py` so RTL documents with a failed tree path but clean flat text can still persist, while the existing garble gate remains the safety net for genuinely garbled sources; D4 propagates `PictureResult` skip metadata (`skipped_reason`, `decorative`) onto image blocks and filters them out of `classify_verdict`'s unenriched count in `helpers.py`, also suppressing false `image_enrichment_promoted` verdicts from landscape-fallback pictures. All five decisions modify code currently uncommitted on `feat/pdf-inspector-shadow-pilot`; each batch is unit-tested in isolation, and corpus re-ingestion/verification is deferred to the corpus-cycle skill, out of scope for this tasks file.

## Tasks

- [ ] <a id="1-batch-0--critical-fixes-d0-d1-d2"></a>1. Batch 0 -- Critical Fixes (D0, D1, D2) ([RFC-036 D0](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d0-landscape-reextract-runaway-page-cap-thread-kill-splice-fix-and-fragmentation-guard), [RFC-036 D1](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d1-write-barrier-delay-cap-schedule-and-catch-persistencenotvisibleerror-in-save-callers), [RFC-036 D2](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d2-land-staged-d19-enrichment-density-preserve-fix), [Design Architecture Decisions](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#architecture-decisions), [Design High-Level Pipeline Flow](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#high-level-pipeline-flow))

  - [ ] <a id="1-1-d0a-add-max_landscape_pages-cap-and-deadline"></a>1.1 D0a: Add `MAX_LANDSCAPE_PAGES` cap and monotonic deadline

    - In `src/pageindex_mcp/converters.py::_landscape_rasterize_rotate_reextract` (line ~2060-2140), introduce a `MAX_LANDSCAPE_PAGES` constant (e.g. 10) and a monotonic wall-clock deadline check at the top of the per-page reextraction loop.
    - Bail out of the loop early -- returning whatever pages have already been reextracted -- once either the page-count cap or the deadline is reached; do not raise.
    - Ensure the cap and deadline are read from module-level constants (not hardcoded inline) so they can be tuned without touching the loop body.
    - _Requirements: [RFC-036 D0 Fix](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d0-landscape-reextract-runaway-page-cap-thread-kill-splice-fix-and-fragmentation-guard) | [Design Property 1: Landscape page cap bounds reextraction](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-1-landscape-page-cap-bounds-reextraction) | [Design converters.py](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#1-converterspy) | [Design Flow: Landscape Reextract (D0)](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#landscape-reextract-flow--d0)_
  - [ ] <a id="1-2-d0b-replace-threadpoolexecutor-with-daemon-or-subprocess"></a>1.2 D0b: Replace `ThreadPoolExecutor` with a killable daemon-thread pool or subprocess

    - In `src/pageindex_mcp/converters.py::_pdf_to_markdown_docling_chunked` (line ~2900-2999), replace the plain `ThreadPoolExecutor` with either a subprocess-per-chunk approach or a daemon-thread pool that can be terminated on timeout.
    - Ensure a `FuturesTimeoutError` at the existing 1500s per-chunk budget actually stops in-flight work (not just abandons the wait), so the child process can exit cleanly instead of surviving past `arq`'s `JOB_TIMEOUT` with no clean error status.
    - Verify no background thread/process outlives the parent's exit after a timeout fires.
    - _Requirements: [RFC-036 D0 Fix](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d0-landscape-reextract-runaway-page-cap-thread-kill-splice-fix-and-fragmentation-guard) | [Design Property 2: Thread pool cleanup on timeout](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-2-thread-pool-cleanup-on-timeout) | [Design converters.py](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#1-converterspy) | [Design Flow: Landscape Reextract (D0)](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#landscape-reextract-flow--d0)_
  - [ ] <a id="1-3-d0c-retune-landscape_char_threshold"></a>1.3 D0c: Re-tune `LANDSCAPE_CHAR_THRESHOLD` to require picture/graphic regions

    - In `src/pageindex_mcp/converters.py`, update the `LANDSCAPE_CHAR_THRESHOLD` trigger condition so it fires only when a page is both below the char-count threshold AND has a detectable picture/graphic region, avoiding false-positive triggers on dense tabular pages (e.g. world-stats-pocketbook's 292 numeric-table pages).
    - Preserve the existing threshold constant/value; only the trigger condition (char-count alone vs. char-count + region detection) changes.
    - _Requirements: [RFC-036 D0 Fix](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d0-landscape-reextract-runaway-page-cap-thread-kill-splice-fix-and-fragmentation-guard) | [Design converters.py](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#1-converterspy) | [Design Flow: Landscape Reextract (D0)](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#landscape-reextract-flow--d0)_
  - [ ] <a id="1-4-d0d-splice-landscape-markdown-at-page-position"></a>1.4 D0d: Splice landscape fallback markdown at correct page position

    - In `src/pageindex_mcp/converters.py::pdf_to_markdown_docling` (line ~3086-3134), change landscape fallback markdown emission from append-at-document-end to splice-at-correct-page-position, using the page index already tracked by the reextraction loop.
    - Verify this does not alter block ordering for documents that never trigger the landscape path (splice logic must be a no-op unless landscape fallback fired).
    - _Requirements: [RFC-036 D0 Fix](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d0-landscape-reextract-runaway-page-cap-thread-kill-splice-fix-and-fragmentation-guard) | [Design Property 3: Landscape content spliced at page position](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-3-landscape-content-spliced-at-page-position) | [Design converters.py](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#1-converterspy) | [Design Flow: Landscape Reextract (D0)](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#landscape-reextract-flow--d0)_
  - [ ] <a id="1-5-d0e-singleton-ratio-guard-in-_segment_table_nodes"></a>1.5 D0e: Singleton-ratio fragmentation guard in `_segment_table_nodes`

    - In `src/pageindex_mcp/helpers.py::_segment_table_nodes`, add a chart-content-aware guard: when more than 60% of table rows are single-value cells (axis labels), skip segmentation entirely and keep the block as a single `TABLE` node instead of shattering it into singleton kv blocks.
    - Ensure the guard runs independently of and does not disturb the existing D0/D17 degenerate-row-collapse guards in `_repair_docling_tables` (RFC-035) -- this is a separate function and a separate failure mode (fragmentation into many nodes, not collapse into one row).
    - _Requirements: [RFC-036 D0 Fix](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d0-landscape-reextract-runaway-page-cap-thread-kill-splice-fix-and-fragmentation-guard) | [Design Property 4: Singleton ratio guard prevents fragmentation](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-4-singleton-ratio-guard-prevents-fragmentation) | [Design helpers.py](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#4-helperspy) | [Design Flow: Landscape Reextract (D0)](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#landscape-reextract-flow--d0)_
  - [ ] <a id="1-6-d1-reduce-write-barrier-delays-and-catch-persistencenotvisibleerror"></a>1.6 D1: Reduce write-barrier delays and catch `PersistenceNotVisibleError` in save callers

    - In `src/pageindex_mcp/storage.py`, reduce the `_WRITE_BARRIER_DELAYS` constant (line ~29) from `(0.1, 0.3, 1.0, 3.0)` (4.4s total) to `(0.05, 0.1, 0.3)` (0.45s total).
    - In `save_doc()` and `save_doc_meta()`, wrap the `_confirm_write_visible()` call in a `try/except PersistenceNotVisibleError` block: on exhaustion, log a warning with the key and increment a new `write_barrier_exhausted` Prometheus counter, but do not re-raise -- `put_object` has already succeeded, so this is an observability gap, not data loss.
    - Add the `write_barrier_exhausted` counter in `src/pageindex_mcp/metrics.py`.
    - Do not modify `_TERMINAL_CHILD_REASONS` in `worker.py` -- catching at the call site means the exception never reaches the child process, so no reason-string classification change is needed.
    - _Requirements: [RFC-036 D1 Fix](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d1-write-barrier-delay-cap-schedule-and-catch-persistencenotvisibleerror-in-save-callers) | [Design Property 5: Write barrier budget capped](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-5-write-barrier-budget-capped) | [Design Property 6: PersistenceNotVisibleError never propagates](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-6-persistencenotvisibleerror-never-propagates) | [Design storage.py](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#2-storagepy) | [Design Flow: Write Barrier (D1)](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#write-barrier-flow--d1)_
  - [ ] <a id="1-7-d2-commit-staged-d19-enrichment-density-preserve"></a>1.7 D2: Commit the already-staged D19 enrichment density-preserve fix

    - Run `git diff --cached -- src/pageindex_mcp/client.py tests/test_rfc034_d19_enrichment.py` to confirm the staged `_ocr_information_density()` function and the density-guarded `_enrich_image_blocks()` merge logic (client.py line ~737-768) are present and match the RFC's description.
    - Verify `_ocr_information_density(text)` scores `(alnum + digits) / max(len(text), 1)` and that `_enrich_image_blocks` preserves existing OCR when `existing_density > new_density * 1.5`, otherwise concatenates both texts.
    - Use selective staging (`git add -p` or explicit file paths limited to the D19 hunks in `client.py` and `tests/test_rfc034_d19_enrichment.py`) to isolate this commit from other uncommitted RFC-034/RFC-035 changes already sitting in `converters.py`, `helpers.py`, and `storage.py` on this branch.
    - Commit with no new code changes -- this is a commit-what-is-staged operation.
    - _Requirements: [RFC-036 D2 Fix](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d2-land-staged-d19-enrichment-density-preserve-fix) | [Design Property 7: Staged D19 density-preserve active](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-7-staged-d19-density-preserve-active) | [Design client.py](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#3-clientpy)_
  - [ ] <a id="1-8-d0-unit-tests"></a>1.8 D0 unit tests

    - **Property 1: Landscape page cap bounds reextraction** -- synthetic 20-page document with 15 landscape-tagged pages; assert `MAX_LANDSCAPE_PAGES` fires and only the top-N pages are reextracted.
    - **Property 2: Thread pool cleanup on timeout** -- multi-landscape-page document sized to approach the per-chunk timeout; assert graceful degradation within budget and no background thread/process surviving child-process exit.
    - **Property 3: Landscape content spliced at page position** -- assert fallback markdown lands at the correct page index, not appended at document end; assert no ordering change for documents that never trigger the landscape path.
    - **Property 4: Singleton ratio guard prevents fragmentation** -- fixture with 80% single-char kv rows; assert `_segment_table_nodes` skips segmentation and keeps a single `TABLE` node.
    - Regression: re-run `uae_numbers_english_page_16_17_landscape` and `world-stats-pocketbook-2023` fixtures through the pipeline and verify FAIL->MARGINAL and ERROR->clean-timeout-with-status respectively.
    - **Validates: [Design Property 1](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-1-landscape-page-cap-bounds-reextraction), [Property 2](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-2-thread-pool-cleanup-on-timeout), [Property 3](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-3-landscape-content-spliced-at-page-position), [Property 4](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-4-singleton-ratio-guard-prevents-fragmentation) | [RFC-036 D0 Test Strategy](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d0-landscape-reextract-runaway-page-cap-thread-kill-splice-fix-and-fragmentation-guard)**
  - [ ] <a id="1-9-d1-unit-tests"></a>1.9 D1 unit tests

    - **Property 5: Write barrier budget capped** -- verify `_confirm_write_visible` with the new delay schedule totals <=0.45s.
    - **Property 6: PersistenceNotVisibleError never propagates** -- mock `_confirm_write_visible` to raise `PersistenceNotVisibleError` and verify `save_doc`/`save_doc_meta` catch it, log a warning, increment `write_barrier_exhausted`, and return normally with no exception propagated.
    - Integration: re-run the Arabic SLA doc (`اتفاقية مستوى الخدمة`) fixture and confirm completion within the scorer polling window (`processing_at` within 2 minutes of batch start).
    - **Validates: [Design Property 5](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-5-write-barrier-budget-capped), [Property 6](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-6-persistencenotvisibleerror-never-propagates) | [RFC-036 D1 Test Strategy](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d1-write-barrier-delay-cap-schedule-and-catch-persistencenotvisibleerror-in-save-callers)**
- [ ] <a id="1-10-checkpoint--batch-0"></a>1.10 Checkpoint -- Batch 0

  - Run `uv run pytest tests/ -k "landscape or rasterize or segment_table_nodes or write_barrier or persistence_not_visible or rfc034_d19"` and verify all Batch 0 unit tests pass, including [Design Property 1](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-1-landscape-page-cap-bounds-reextraction) through [Property 7](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-7-staged-d19-density-preserve-active).
  - Confirm the D2 commit (Task 1.7) contains only the staged D19 hunks and no unrelated uncommitted changes from converters.py/helpers.py/storage.py.
  - Confirm no background thread or subprocess from the D0b fix (Task 1.2) survives a simulated timeout in a manual smoke test.
  - Ask the user if questions arise before proceeding.

- [ ] <a id="2-batch-1--improvements-d3-d4"></a>2. Batch 1 -- Improvements (D3, D4) ([RFC-036 D3](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d3-rtl-reversal-add-flat-fallback-routing-instead-of-terminal-rejection), [RFC-036 D4](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d4-propagate-pictureresult-skip-metadata-to-image-blocks-and-suppress-false-enrichment-verdicts), [Design Architecture Decisions](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#architecture-decisions), [Design High-Level Pipeline Flow](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#high-level-pipeline-flow))

  - [ ] <a id="2-1-d3-add-rtl_reversal-to-flat-routing-whitelist"></a>2.1 D3: Add `rtl_reversal` to the flat-routing whitelist

    - In `src/pageindex_mcp/client.py`, add `'rtl_reversal'` to the flat routing whitelist (line ~1709) alongside the existing `'node_count<3'` and `'depth<2'` reasons.
    - Remove `'rtl_reversal'` from the terminal-raise list (line ~1992) when a flat fallback is available, so it no longer unconditionally raises `LowQualityTreeError` before the flat path gets a chance to run.
    - In the `index()` RTL repair path (line ~1418-1475), when `reconstruct_bidi_order` fails to converge, route to flat extraction regardless of the RFC-033 D8 flat-comparison result -- do not gate flat routing on flat text also being non-reversed.
    - Do not bypass the downstream flat-path garble gate (`_flat_text_is_garbled`, line ~1747): if flat text is also garbled, it must still override the reason to `'garbling'` and raise `LowQualityTreeError` per Hard Rule 5.
    - _Requirements: [RFC-036 D3 Fix](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d3-rtl-reversal-add-flat-fallback-routing-instead-of-terminal-rejection) | [Design Property 8: RTL reversal routes to flat fallback](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-8-rtl-reversal-routes-to-flat-fallback) | [Design Property 9: Garble gate rejects garbled flat text](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-9-garble-gate-rejects-garbled-flat-text) | [Design client.py](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#3-clientpy) | [Design Flow: Flat Fallback Routing (D3)](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#flat-fallback-routing-flow--d3)_
  - [ ] <a id="2-2-d4-propagate-skip-metadata-and-suppress-false-verdicts"></a>2.2 D4: Propagate skip metadata to image blocks and suppress false enrichment verdicts

    - In `src/pageindex_mcp/client.py::_enrich_image_blocks()` (line ~737), after matching a `PictureResult` to an image block: `if pr.get('skipped_reason'): block['skipped_reason'] = pr['skipped_reason']`; `if pr.get('decorative'): block['decorative'] = True`.
    - In `src/pageindex_mcp/helpers.py::classify_verdict()`'s `image_enrichment_promoted` path (line ~1668-1675), filter out blocks where `block.get('decorative')` is `True` or `block.get('skipped_reason')` is truthy from the unenriched count.
    - Audit `src/pageindex_mcp/converters.py::_recover_picture_text` to verify all four skip paths (`decorative_icon` sub-20pt filter, OCR min-chars gate, `page_coverage` threshold, `clip_text_already_exported`) consistently set `skipped_reason` on the `PictureResult` dict.
    - In `src/pageindex_mcp/converters.py`, ensure `landscape_fallback_picture` `PictureResult` emissions also set `skipped_reason` so they are excluded from `image_enrichment_promoted` counting via the same filter -- this covers both the decorative-icon case and the D0 landscape-fallback case in a single filter, avoiding cross-batch coordination with Batch 0's converters.py changes.
    - _Requirements: [RFC-036 D4 Fix](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d4-propagate-pictureresult-skip-metadata-to-image-blocks-and-suppress-false-enrichment-verdicts) | [Design Property 10: Skip metadata propagated to image blocks](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-10-skip-metadata-propagated-to-image-blocks) | [Design Property 11: Decorative blocks excluded from unenriched count](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-11-decorative-blocks-excluded-from-unenriched-count) | [Design client.py](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#3-clientpy) | [Design helpers.py](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#4-helperspy) | [Design Flow: Enrichment Skip Propagation (D4)](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#enrichment-skip-propagation-flow--d4)_
  - [ ] <a id="2-3-d3-unit-tests"></a>2.3 D3 unit tests

    - **Property 8: RTL reversal routes to flat fallback** -- `validate_tree` returning `'rtl_reversal'` with clean flat text routes to flat extraction and produces a PASS/MARGINAL artifact; synthetic RTL document where tree extraction fails but flat extraction succeeds, verifying the routing produces a valid artifact.
    - **Property 9: Garble gate rejects garbled flat text** -- `validate_tree` returning `'rtl_reversal'` with garbled flat text triggers the flat-path garble gate and raises `LowQualityTreeError` (ERROR verdict, zero output), confirming Hard Rule 5 enforcement.
    - Integration: re-run `وارد رقم 597` and confirm it still produces ERROR (garble gate rejects numeric-junk flat text), but with improved diagnostic logging showing the flat-fallback path was attempted before rejection.
    - **Validates: [Design Property 8](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-8-rtl-reversal-routes-to-flat-fallback), [Property 9](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-9-garble-gate-rejects-garbled-flat-text) | [RFC-036 D3 Test Strategy](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d3-rtl-reversal-add-flat-fallback-routing-instead-of-terminal-rejection)**
  - [ ] <a id="2-4-d4-unit-tests"></a>2.4 D4 unit tests

    - **Property 10: Skip metadata propagated to image blocks** -- `_enrich_image_blocks` with a `PictureResult` containing `skipped_reason='decorative_icon'` verifies the block dict gets `skipped_reason` and `decorative` fields; `_recover_picture_text` for each of the four skip paths verifies the `PictureResult` carries `skipped_reason`.
    - **Property 11: Decorative blocks excluded from unenriched count** -- `classify_verdict` with image blocks containing `decorative=True` verifies they are excluded from the unenriched count; `PictureResult` with `skipped_reason` asserts `image_enrichment_promoted` is NOT set, including for `landscape_fallback_picture`-sourced results.
    - Integration: re-run `GHV-TKV-Tarif` and `Unfallversicherung-Leistungsuebersicht-2025-001` and verify decorative icons are tagged in output and no longer counted as unenriched gaps in the verdict.
    - **Validates: [Design Property 10](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-10-skip-metadata-propagated-to-image-blocks), [Property 11](../designs/design-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#property-11-decorative-blocks-excluded-from-unenriched-count) | [RFC-036 D4 Test Strategy](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d4-propagate-pictureresult-skip-metadata-to-image-blocks-and-suppress-false-enrichment-verdicts)**
  - [ ] <a id="2-5-d5-extend-arabic-heading-injection"></a>2.5 D5: Extend Arabic structural heading injection

    - In `src/pageindex_mcp/converters.py`, extend `_AR_PART_RE` (line 86) from `(?:باب|فصل|قسم|جزء)` to `(?:باب|فصل|قسم|جزء|قرار|مرسوم|قانون)`.
    - Add reversed-form stems to the second alternative: قرار→رارق, مرسوم→موسرم (already in `_AR_KNOWN_WORDS_REVERSED`), قانون→نوناق.
    - Extend `_AR_MARKER_CAPTURE_RE` (line 100) to include the new markers with their parenthetical numeral patterns (e.g., "قرار مجلس الوزراء رقم (1)").
    - In `_inject_arabic_structural_headings` (line 135), map قرار/مرسوم/قانون to `#` (part-level), keeping existing مادة at `##` (article-level).
    - _Requirements: [RFC-036 D5](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d5-extend-arabic-structural-heading-injection-to-cover-قرارمرسومقانون-patterns)_
  - [ ] <a id="2-6-d5-unit-tests"></a>2.6 D5 unit tests

    - **Property 12: Arabic heading injection covers قرار/مرسوم/قانون** -- synthetic Arabic text with قرار/مرسوم/قانون markers verifying heading injection at correct depth (`#` for part-level).
    - Reversed OCR variants of new markers (e.g., رارق for قرار) inject correctly.
    - Mid-paragraph citations ("...المشار إليها في القرار رقم 5 من...") are NOT promoted.
    - Integration: re-run the 5 affected Arabic documents and verify depth improvement.
    - _Requirements: [RFC-036 D5 Test Strategy](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d5-extend-arabic-structural-heading-injection-to-cover-قرارمرسومقانون-patterns)_
- [ ] <a id="2-7-checkpoint--batch-1"></a>2.7 Checkpoint -- Batch 1

  - Run `uv run pytest tests/ -k "rtl_reversal or flat_routing or enrich_image_blocks or classify_verdict or image_enrichment_promoted or arabic_heading"` and verify all Batch 1 unit tests pass.
  - Confirm `وارد رقم 597` still ERRORs (Hard Rule 5 enforced, no silently-persisted garbled tree).
  - Confirm the D4 filter in `classify_verdict` covers decorative-icon, landscape-fallback, and content-quality-gate cases.
  - Confirm D5's regex extensions do not break existing Arabic heading injection for documents currently at PASS.
  - Ask the user if questions arise before proceeding.

- [ ] <a id="3-batch-2--lower-priority-d6-d7"></a>3. Batch 2 -- Lower Priority (D6, D7)

  - [ ] <a id="3-1-d6-depth-adequacy-scoring"></a>3.1 D6: Depth-adequacy scoring in classify_verdict

    - In `src/pageindex_mcp/helpers.py::classify_verdict()` (line ~1707-1713), after the existing `depth >= 2` PASS gate, add a secondary depth-adequacy check.
    - Compute `expected_min_depth = 2 + floor(log2(node_count / 50))` (capped at 5).
    - When `depth < expected_min_depth`, set verdict to MARGINAL with reason `depth_inadequate` and include `expected_min_depth` and `actual_depth` in diagnostic metadata.
    - This is a verdict-scoring change only -- it does NOT affect `validate_tree`'s persistence gating (Hard Rule 5 enforcement unchanged).
    - _Requirements: [RFC-036 D6](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d6-depth-adequacy-scoring-proportional-to-document-complexity)_
  - [ ] <a id="3-2-d6-unit-tests"></a>3.2 D6 unit tests

    - 50-node tree at depth 2 → PASS (baseline unchanged).
    - 200-node tree at depth 2 → MARGINAL with reason `depth_inadequate`.
    - 200-node tree at depth 4 → PASS.
    - 600-node tree at depth 2 → MARGINAL.
    - 600-node tree at depth 5 → PASS.
    - Boundary conditions at 100, 200, 400 node thresholds.
    - _Requirements: [RFC-036 D6 Test Strategy](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d6-depth-adequacy-scoring-proportional-to-document-complexity)_
  - [ ] <a id="3-3-d7-ocr-spike-service-wrappers"></a>3.3 D7: OCR engine evaluation spike -- PaddleOCR and Docling OCR service wrappers

    - Create `services/paddleocr-service/` with a minimal FastAPI app wrapping PaddleOCR (multilingual model, GPU-optional). Expose `POST /ocr` accepting PNG bytes, returning `{text, confidence, lang}`. Include Dockerfile + requirements.txt.
    - Create `services/docling-ocr-service/` with a minimal FastAPI app routing image bytes through Docling's EasyOCR-based pipeline. Same API contract as PaddleOCR wrapper.
    - Write `scripts/ocr_spike_eval.py` that extracts test images from the corpus, runs all three engines (Tesseract, PaddleOCR, Docling OCR), and computes accuracy metrics.
    - Both services run alongside existing `services/docling-service` under the same deployment.
    - _Requirements: [RFC-036 D7](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d7-ocr-engine-evaluation-spike--paddleocr-and-docling-ocr-service-wrappers)_
  - [ ] <a id="3-4-d7-spike-evaluation"></a>3.4 D7: Run spike evaluation and write comparison report

    - Run `scripts/ocr_spike_eval.py` against corpus chart/table/scanned-Arabic images.
    - Write comparison report with: character accuracy, structural coherence, Arabic-specific metrics.
    - Success criterion: identify which engine (if any) improves chart/Arabic OCR accuracy over Tesseract baseline by >= 20%.
    - If neither clears the bar, document finding and close spike.
    - _Requirements: [RFC-036 D7 Test Strategy](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d7-ocr-engine-evaluation-spike--paddleocr-and-docling-ocr-service-wrappers)_
- [ ] <a id="3-5-checkpoint--batch-2"></a>3.5 Checkpoint -- Batch 2

  - Run `uv run pytest` (full suite) and verify zero regressions across all batches.
  - Confirm D6's depth-adequacy formula does not downgrade currently-PASS documents.
  - Confirm D7 spike report is written with clear recommendation.
  - Ask the user if questions arise before proceeding.

- [ ] <a id="4-final-checkpoint"></a>4. Final Checkpoint

  - Run `uv run pytest` (full suite) and verify zero regressions across Batch 0, Batch 1, and Batch 2.
  - Confirm all 8 decisions (D0's 5 sub-fixes, D1, D2, D3, D4 amended, D5, D6, D7) are independently unit-testable.
  - Confirm no task in this file performed corpus ingestion, re-ingestion, or verification -- those are explicitly deferred to the corpus-cycle skill per this file's scope.
  - Confirm staging/commit order does not conflict with remaining uncommitted RFC-035 changes.
  - Ask the user if questions arise before proceeding.

## Notes

- [Task 1.1](#1-1-d0a-add-max_landscape_pages-cap-and-deadline) through [1.5](#1-5-d0e-singleton-ratio-guard-in-_segment_table_nodes) are five distinct sub-fixes for the single D0 decision; per [RFC-036 D0](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d0-landscape-reextract-runaway-page-cap-thread-kill-splice-fix-and-fragmentation-guard) they compound and require integration testing together (Task 1.8), even though each is independently medium-complexity.
- [Task 1.4](#1-4-d0d-splice-landscape-markdown-at-page-position) changes block ordering for every document that triggers the landscape path, not just the regressed ones, per [RFC-036 Risks](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#risks). A broader corpus spot-check beyond the `uae_numbers_english_page_16_17_landscape` integration test is recommended during the corpus-cycle skill's validation pass, not as a task here.
- [Task 1.6](#1-6-d1-reduce-write-barrier-delays-and-catch-persistencenotvisibleerror)'s downgrade of `PersistenceNotVisibleError` to a warning is deliberate: `put_object` has already succeeded before `_confirm_write_visible` runs, so a `stat_object` failure at that point is a visibility issue, not a write failure, per [RFC-036 D1 Rationale](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d1-write-barrier-delay-cap-schedule-and-catch-persistencenotvisibleerror-in-save-callers). The retry-caused-the-delay hypothesis for the Arabic SLA doc is unconfirmed (no worker logs located for doc_id d58be46f) -- the fix is justified on engineering grounds regardless.
- [Task 1.7](#1-7-d2-commit-staged-d19-enrichment-density-preserve) is a commit-only operation with zero new implementation; do not modify the staged `_ocr_information_density()` or `_enrich_image_blocks()` logic beyond what is already staged, per [RFC-036 D2 Fix](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d2-land-staged-d19-enrichment-density-preserve-fix). Selective staging (`git add -p`) is mandatory, not optional, since `client.py` has other uncommitted hunks alongside D19.
- [Task 2.1](#2-1-d3-add-rtl_reversal-to-flat-routing-whitelist)'s fix does NOT change `وارد رقم 597`'s ERROR verdict -- both tree and flat paths produce garbled text from a numeric-junk source layer, and the garble gate correctly rejects it per Hard Rule 5. The fix unblocks the flat-fallback path for *future* RTL documents with clean flat text; [Task 2.3](#2-3-d3-unit-tests)'s regression test exists to confirm the verdict is unchanged, not to confirm improvement.
- [Task 2.2](#2-2-d4-propagate-skip-metadata-and-suppress-false-verdicts) absorbs the landscape-fallback `PictureResult` suppression that D0's Rationale explicitly deferred to D4 rather than bundling into Batch 0, to avoid cross-batch coordination hazards on the same `classify_verdict` code path -- do not duplicate this filter logic in Batch 0.
- D0, D1, and D2 (Batch 0) have no cross-decision code dependency and may be implemented in any order within Batch 0; D3 and D4 (Batch 1) similarly have no cross-decision dependency on each other but both assume Batch 0's converters.py/helpers.py changes are stable, per [RFC-036 Implementation Plan](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#implementation-plan).
- Per the dispatch instruction, corpus ingestion/re-ingestion/verification steps are explicitly excluded from this tasks file -- the Integration Tests described in [RFC-036 Test Strategy](../rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#test-strategy) (re-ingest uae_numbers_landscape, world-stats-pocketbook, the Arabic SLA doc, وارد 597, GHV-TKV-Tarif, Unfallversicherung) are the corpus-cycle skill's responsibility, not a task here.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1-1-d0a-add-max_landscape_pages-cap-and-deadline", "1-2-d0b-replace-threadpoolexecutor-with-daemon-or-subprocess", "1-3-d0c-retune-landscape_char_threshold", "1-5-d0e-singleton-ratio-guard-in-_segment_table_nodes", "1-6-d1-reduce-write-barrier-delays-and-catch-persistencenotvisibleerror", "1-7-d2-commit-staged-d19-enrichment-density-preserve"] },
    { "id": 1, "tasks": ["1-4-d0d-splice-landscape-markdown-at-page-position"] },
    { "id": 2, "tasks": ["1-8-d0-unit-tests", "1-9-d1-unit-tests"] },
    { "id": 3, "tasks": ["1-10-checkpoint--batch-0"] },
    { "id": 4, "tasks": ["2-1-d3-add-rtl_reversal-to-flat-routing-whitelist", "2-2-d4-propagate-skip-metadata-and-suppress-false-verdicts", "2-5-d5-extend-arabic-heading-injection"] },
    { "id": 5, "tasks": ["2-3-d3-unit-tests", "2-4-d4-unit-tests", "2-6-d5-unit-tests"] },
    { "id": 6, "tasks": ["2-7-checkpoint--batch-1"] },
    { "id": 7, "tasks": ["3-1-d6-depth-adequacy-scoring", "3-3-d7-ocr-spike-service-wrappers"] },
    { "id": 8, "tasks": ["3-2-d6-unit-tests", "3-4-d7-spike-evaluation"] },
    { "id": 9, "tasks": ["3-5-checkpoint--batch-2"] },
    { "id": 10, "tasks": ["4-final-checkpoint"] }
  ]
}
```
