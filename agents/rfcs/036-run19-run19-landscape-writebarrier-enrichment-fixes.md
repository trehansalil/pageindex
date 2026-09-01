<!-- Space: CITRA -->
<!-- Title: RFC-036: Run-19 Landscape Runaway, Write-Barrier Retry, and Enrichment Propagation Fixes -->
<!-- Folder: RFCs -->

# RFC-036: Run-19 Landscape Runaway, Write-Barrier Retry, and Enrichment Propagation Fixes

**Run:** 19
**Audit:** [audit/CORPUS_REINGESTION_AUDIT_RUN-19.md](../../audit/CORPUS_REINGESTION_AUDIT_RUN-19.md)
**Status:** Draft

## Summary

Run 19 audited all 25 corpus documents and produced a tally of 9 PASS, 12 MARGINAL, 1 FAIL, and 3 ERROR. Compared to Run 18, three documents improved (Federal Decree-Law 47 FAIL->MARGINAL, Reitlehrer MARGINAL->PASS, سياسة حوكمة FAIL->PASS) and one showed a large content-volume recovery masked by an unchanged MARGINAL verdict (مرسوم 33: ~6K chars to 106K chars). The 3 ERRORs are world-stats-pocketbook (persistent converter timeout, no artifacts), وارد 597 (RTL reversal terminal rejection, no artifacts), and اتفاقية مستوى الخدمة (late-landing artifact missed the scorer polling window due to write-barrier delay). The 1 FAIL is uae_numbers_english_landscape (chart data fragmented into 71 singleton kv blocks by the RFC-035 D2 landscape reextract path). Key regressions trace to three uncommitted RFC-035 features (landscape reextract runaway, table-repair guard interactions) and one uncommitted RFC-034 fix (D19 enrichment density-preserve) that was staged but never committed before the audit.

## Decisions

### D0: Landscape reextract runaway: page cap, thread kill, splice fix, and fragmentation guard

**Scope:** RFC-035 D2 landscape rasterize-rotate-reextract path has three compounding defects: uncapped page count causing serial 300-DPI OCR re-runs, non-daemon ThreadPoolExecutor threads surviving timeout, and chart axis label fragmentation into 71+ singleton kv blocks.

**Root Cause:** Three compounding defects in the uncommitted RFC-035 D2 code: (1) _landscape_rasterize_rotate_reextract loops serially over every flagged page with no cap, so a document with many low-char landscape pages (e.g., world-stats-pocketbook with 292 pages of dense numeric tables triggering LANDSCAPE_CHAR_THRESHOLD=500) blows the per-chunk 1500s timeout. (2) ThreadPoolExecutor threads are non-daemon; future.result(timeout) only abandons waiting, and pool.shutdown(cancel_futures=True) only cancels unstarted futures -- the running thread keeps executing the landscape loop after timeout, preventing the child subprocess from exiting cleanly, which causes arq to kill the job at JOB_TIMEOUT with no clean error status. (3) Chart axis labels from 300-DPI rasterization are appended at document end (not spliced into page position) and then shattered by _segment_table_nodes into 71+ singleton kv blocks; the D0/D17 guards in _repair_docling_tables prevent previously-collapsed degenerate rows from collapsing.

**Rationale:** This is the root cause of the only FAIL verdict in Run 19 (uae_numbers_landscape) and a contributing factor to the world-stats-pocketbook ERROR (persistent timeout). The landscape reextract feature is new uncommitted code that did not exist in Run 16 when world-stats-pocketbook PASSed. Without a fix, any document with many landscape or low-char pages will either produce fragmented unusable output or timeout entirely.

**Affected Documents:**
- uae_numbers_english_page_16_17_landscape - Copy.pdf (FAIL)
- uae_numbers_english_page_16_17_portrait - Copy.pdf (MARGINAL)
- world-stats-pocketbook-2023.pdf (ERROR)

**Files / Functions:**
- `src/pageindex_mcp/converters.py :: _landscape_rasterize_rotate_reextract (line ~2060-2140) -- add MAX_LANDSCAPE_PAGES cap and monotonic deadline check`
- `src/pageindex_mcp/converters.py :: _pdf_to_markdown_docling_chunked (line ~2900-2999) -- replace ThreadPoolExecutor with subprocess or daemon-thread pool`
- `src/pageindex_mcp/converters.py :: pdf_to_markdown_docling (line ~3086-3134) -- splice landscape fallback markdown into correct page position instead of appending at end`
- `src/pageindex_mcp/helpers.py :: _segment_table_nodes -- add chart-content-aware guard that skips segmentation when rows are predominantly single-value axis labels (high singleton ratio)`
- `tests/test_rfc035_d2_landscape.py -- add multi-page integration test`

**Fix:** (1) Add MAX_LANDSCAPE_PAGES constant (e.g., 10) and a monotonic wall-clock deadline inside the per-page reextraction loop; bail early when either limit is reached. (2) Replace the plain ThreadPoolExecutor with either a subprocess-per-chunk approach or a daemon-thread pool that can be killed on timeout, so a FuturesTimeoutError at 1500s actually stops the work. (3) Re-tune LANDSCAPE_CHAR_THRESHOLD to also require picture/graphic regions (not just low char count) to avoid false-positive triggers on dense tabular pages. (4) Splice landscape fallback markdown into the correct page position in the document instead of blindly appending at end. (5) Add a singleton-ratio guard to _segment_table_nodes: when >60% of table rows are single-value cells (axis labels), skip segmentation and keep the block as a single TABLE node.

Note: The effective_timeout cap in worker.py (trace finding [10]) targets the pdf-inspector 16.5x multiplier mechanism, but PDF_INSPECTOR_PRECLASSIFY defaults to '0' and is unset in the current deployment, so the branch never executes. This sub-fix is therefore defensive-only and is deferred to a separate hardening pass rather than bundled here. Similarly, suppressing PictureResults with skipped_reason from triggering image_enrichment_promoted is addressed in D4 (skip metadata propagation) to avoid cross-batch coordination hazards.

**Effort:** Medium-Large (2-3 days). Three core sub-fixes touching converters.py (page cap + thread kill + page-position splice) and helpers.py (singleton guard). Each sub-fix is medium complexity individually but they interact and need integration testing with multi-page synthetic fixtures.

**Test Strategy:** Unit tests: (a) synthetic 20-page document with 15 landscape pages verifying MAX_LANDSCAPE_PAGES cap fires and only top-N pages are reextracted; (b) singleton-ratio guard test with fixture of 80% single-char kv rows asserting segmentation is skipped. Integration tests: (c) multi-landscape-page document sized to approach per-chunk timeout asserting graceful degradation within budget and no background thread surviving child process exit. Regression: re-run uae_numbers_landscape and world-stats-pocketbook through the pipeline and verify FAIL->MARGINAL and ERROR->clean-timeout-with-status respectively. Note on world-stats-pocketbook: trace finding [3] reports this as a pre-existing converter timeout (ERRORed 3+ consecutive runs), while finding [10] indicates Run 16 PASSed under pre-RFC-035 code. The success criterion for this document is that the landscape page cap prevents the runaway loop and the child exits cleanly with a timeout status (rather than being killed by arq with no status); whether the document fully completes depends on whether the non-landscape pages process within the timeout budget, which cannot be guaranteed at this stage.
---

### D1: Write barrier delay: cap schedule and catch PersistenceNotVisibleError in save callers

**Scope:** RFC-034 D18 _confirm_write_visible() adds up to 4.4s blocking delay per save call (8.8s worst-case). On exhaustion it raises PersistenceNotVisibleError which propagates as an unhandled RuntimeError, potentially doubling processing time if arq retries the job.

**Root Cause:** _confirm_write_visible() in storage.py uses _WRITE_BARRIER_DELAYS=(0.1, 0.3, 1.0, 3.0) totaling 4.4s per call, applied after both save_doc and save_doc_meta (worst-case 8.8s). On exhaustion, it raises PersistenceNotVisibleError(RuntimeError). Since RuntimeError is not handled in save_doc/save_doc_meta, it propagates to the child process, which maps it to the generic 'converter_child_failed' reason string. That string is not in _TERMINAL_CHILD_REASONS (worker.py:116), so arq retries the entire job (MAX_TRIES=2). For the Arabic SLA doc (slow bilingual/scanned content), this may have pushed completion past the scorer's 06:26-06:28 polling cohort.

**Evidence caveat:** The audit notes the Arabic SLA doc landed 3-5 minutes late and states it is "possibly interacting with the RFC-034 D18 write-barrier change." No worker logs confirming PersistenceNotVisibleError actually fired or an arq retry actually occurred for this document (doc_id d58be46f) have been located. The 8.8s worst-case barrier alone cannot explain 3-5 minutes of lateness. The retry hypothesis is plausible but unconfirmed. The delay reduction and catch-and-downgrade fix are justified regardless: the barrier budget is over-provisioned for MinIO's sub-100ms read-after-write consistency, and an unhandled exception from a successful put_object should not crash the child.

**Rationale:** This is the most likely cause of the اتفاقية مستوى الخدمة ERROR verdict in Run 19. The document actually processed successfully (28,202 chars, stored PASS) but landed after the scorer polling window. Reducing the barrier budget and catching PersistenceNotVisibleError prevents the exception from propagating and eliminates the retry path for this failure mode.

**Affected Documents:**
- اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf (ERROR, actually PASS post-score)

**Files / Functions:**
- `src/pageindex_mcp/storage.py :: _WRITE_BARRIER_DELAYS constant (line ~29) -- reduce from (0.1, 0.3, 1.0, 3.0) to (0.05, 0.1, 0.3) totaling 0.45s`
- `src/pageindex_mcp/storage.py :: save_doc() and save_doc_meta() -- wrap _confirm_write_visible calls in try/except PersistenceNotVisibleError, downgrade to warning log + metric`
- `src/pageindex_mcp/metrics.py :: add write_barrier_exhausted counter for observability`
- `tests/test_rfc034_d18_write_barrier.py -- update existing tests for new delay schedule and catch-and-downgrade behavior`

**Fix:** (1) Reduce _WRITE_BARRIER_DELAYS to (0.05, 0.1, 0.3) totaling 0.45s, since MinIO read-after-write consistency is typically sub-100ms. (2) In save_doc and save_doc_meta, wrap the _confirm_write_visible() call in a try/except PersistenceNotVisibleError block: log a warning with the key and increment a write_barrier_exhausted Prometheus counter, but do not re-raise. The put_object already succeeded, so stat_object flakiness is an observability gap, not data loss. This is the sole mechanism: by catching the exception at the call site, it never propagates to the child process, so the worker never sees it and the question of terminal vs. retryable classification does not arise. No changes to _TERMINAL_CHILD_REASONS are needed.

**Effort:** Small (0.5 day). One constant change, two try/except wrappers in save_doc/save_doc_meta, one new Prometheus counter. Existing test file covers the write barrier and needs updating for the new delay schedule.

**Test Strategy:** Unit tests: (a) verify _confirm_write_visible with new delay schedule totals <=0.45s; (b) mock _confirm_write_visible to raise PersistenceNotVisibleError and verify save_doc/save_doc_meta catch it, log a warning, increment write_barrier_exhausted counter, and return normally (no exception propagated). Integration: re-run the Arabic SLA doc and confirm it completes within the scorer polling window (processing_at within 2 minutes of batch start).
---

### D2: Land staged D19 enrichment density-preserve fix

**Scope:** RFC-034 D19 enrichment displacement fix (_ocr_information_density comparison) is fully implemented and staged in git but never committed, so it was inactive during Run 19.

**Root Cause:** The old enrichment logic at client.py (pre-D19) in _enrich_image_blocks can replace existing OCR text with placeholder description text. The D19 fix adds an _ocr_information_density() function that scores text by alnum+digit density and a density-guarded comparison in _enrich_image_blocks: when existing OCR density exceeds new text density by 1.5x, existing OCR is preserved; otherwise texts are concatenated. This fix exists only in git staged changes (git diff --cached shows the full implementation) but was never committed, so it was inactive during the Run-19 audit cycle.

**Rationale:** The D19 fix prevents a Layer-2 displacement bug where 489 chars of real OCR digits get replaced by 1,203 chars of generic placeholder text (ratio=0.50). The code is already written, tested, and staged -- it just needs to be committed. Without it, any valid OCR content is at risk of silent displacement by lower-density placeholder text. This is the lowest-effort highest-certainty fix in the RFC.

**Affected Documents:**
- image pie chart about labor distribution in january 2025 - Copy.jpg (MARGINAL -- D19 prevents displacement but does not fix underlying Tesseract garbling)

**Files / Functions:**
- `src/pageindex_mcp/client.py :: _ocr_information_density() -- new function, already staged`
- `src/pageindex_mcp/client.py :: _enrich_image_blocks() (line ~737-768) -- density-guarded OCR merge logic, already staged`
- `tests/test_rfc034_d19_enrichment.py -- existing test file for D19, already staged`

**Fix:** Commit the already-staged D19 changes as-is. The implementation adds _ocr_information_density(text) which scores by (alnum + digits) / max(len(text), 1), and modifies _enrich_image_blocks to compare existing vs new OCR density: if existing_density > new_density * 1.5, preserve existing; otherwise concatenate both. Verify all existing tests pass after commit. No code changes needed -- this is purely a commit-what-is-staged operation.

**Effort:** Trivial (< 1 hour). Code is written and staged. Verify tests pass, commit.

**Test Strategy:** Run existing test_rfc034_d19_enrichment.py to confirm all tests pass. Verify _ocr_information_density returns expected scores for known inputs (high-digit OCR text vs low-density placeholder). Verify _enrich_image_blocks preserves high-density existing OCR when new text is low-density. Re-run the pie chart JPG through the pipeline and confirm OCR text is preserved (not displaced by placeholder).

---

### D3: RTL reversal: add flat-fallback routing instead of terminal rejection

**Scope:** validate_tree returning reason='rtl_reversal' hits the terminal-raise list with no flat fallback, causing zero output for documents where the tree path fails. For documents where both tree and flat paths produce garbled text from a defective source layer, the flat-path garble gate correctly rejects the flat text too, and the document remains ERROR.

**Root Cause:** validate_tree (helpers.py:1375) returns reason='rtl_reversal'. The reconstruct_bidi_order repair (client.py:1418-1441) does not converge because the underlying text is numeric junk, not genuinely reversed Arabic. The RFC-033 D8 flat-comparison reroute (client.py:1451-1475) only reclassifies to 'node_count<3' when flat text is NOT reversed while tree text IS reversed -- but for وارد 597 both paths produce reversed/garbled text. Then 'rtl_reversal' hits the terminal-raise list at client.py:1992, raising LowQualityTreeError and preventing any artifact persistence. The flat routing whitelist at client.py:1709 only accepts 'node_count<3' and 'depth<2', explicitly excluding 'rtl_reversal'. This has been stable across runs 12-19.

**Rationale:** Adding 'rtl_reversal' to the flat routing whitelist allows documents with this rejection reason to attempt flat extraction instead of being terminally rejected. This benefits RTL documents where the tree extraction fails but the flat extraction produces clean text (e.g., a document with genuine Arabic content that Docling's tree builder scrambles but PyMuPDF's flat extraction handles correctly). For وارد 597 specifically, the source PDF's text layer is numeric junk -- BOTH tree and flat paths produce garbled text. The flat-path garble gate (_flat_text_is_garbled, client.py:~1747) correctly detects this and overrides the reason to 'garbling', which triggers the terminal LowQualityTreeError raise. Therefore, وارد 597 remains ERROR with zero output after this fix. This is the correct outcome: CLAUDE.md Hard Rule 5 prohibits silently persisting a low-quality tree, and the garble gate enforces this. The fix does not change وارد 597's verdict; it unblocks the flat-fallback path for future RTL documents with clean flat text that are currently rejected unnecessarily.

**Affected Documents:**
- وارد رقم 597 من مكتب أبوظبي التنفيذي (ERROR -- remains ERROR; source text layer is numeric junk on both paths, garble gate correctly rejects)

**Files / Functions:**
- `src/pageindex_mcp/client.py :: flat routing whitelist (line ~1709) -- add 'rtl_reversal' to accepted reasons`
- `src/pageindex_mcp/client.py :: terminal-raise list (line ~1992) -- remove 'rtl_reversal' from terminal raises when flat fallback is available`
- `src/pageindex_mcp/client.py :: index() RTL repair path (line ~1418-1475) -- add condition: when reconstruct_bidi_order fails, route to flat regardless of whether flat text is also garbled (the garble gate downstream will catch it)`

**Fix:** (1) Add 'rtl_reversal' to the flat routing whitelist at client.py:1709 alongside 'node_count<3' and 'depth<2'. (2) In the index() RTL repair path, when reconstruct_bidi_order fails to converge, allow the flat routing path to execute regardless of the D8 flat-comparison result. The downstream flat-path garble gate (_flat_text_is_garbled, client.py:~1747) remains the safety net: if the flat text is also garbled, it overrides the reason to 'garbling' and the document is rejected per Hard Rule 5. If the flat text is clean, the document persists as a flat artifact. No garble gate bypass is introduced.

**Effort:** Small (0.5-1 day). A whitelist addition plus a conditional routing change. The existing flat-path garble gate provides the safety net.

**Test Strategy:** Unit tests: (a) validate_tree returning 'rtl_reversal' with clean flat text routes to flat extraction and produces a PASS/MARGINAL artifact; (b) validate_tree returning 'rtl_reversal' with garbled flat text triggers the flat-path garble gate and raises LowQualityTreeError (ERROR verdict, zero output -- confirming Hard Rule 5 enforcement); (c) synthetic RTL document where tree extraction fails but flat extraction succeeds, verifying the routing produces a valid artifact. Integration: re-run وارد 597 through the pipeline and confirm it still produces ERROR (garble gate rejects the numeric-junk flat text), but with improved diagnostic logging showing the flat-fallback path was attempted before rejection. Separately, construct a synthetic RTL test document with clean flat text to verify the routing fix produces a flat artifact.
---

### D4: Propagate PictureResult skip metadata to image blocks and suppress false enrichment verdicts

**Scope:** _enrich_image_blocks does not propagate skipped_reason or decorative flags from PictureResult to image blocks, causing audit scoring to count intentionally-skipped decorative icons as unenriched gaps. Additionally, landscape_fallback_picture PictureResults with skipped_reason trigger false image_enrichment_promoted verdicts in classify_verdict.

**Root Cause:** _enrich_image_blocks in client.py (line ~737) matches PictureResults to image blocks but only writes page, bbox, ocr_text, description, and figure_path onto the block dict. When _recover_picture_text correctly skips a region (decorative_icon filter for sub-20pt elements, OCR min-chars gate yielding decorative=True, page_coverage threshold, clip_text_already_exported), the resulting PictureResult carries skipped_reason and decorative metadata but these fields are never propagated to the block. Image blocks end up with page=0, bbox={} and no explanation. classify_verdict's image_enrichment_promoted path in helpers.py (line ~1668-1675) then counts these as unenriched gaps, degrading the verdict even though the skip was correct. Separately, landscape_fallback_picture PictureResults emitted by the D0 landscape path carry skipped_reason but are not filtered from the image_enrichment_promoted calculation, causing false-positive verdict promotions.

**Rationale:** This affects 2 documents' verdicts: GHV-TKV-Tarif (MARGINAL due to 3/4 unenriched image markers that are actually decorative animal silhouettes/logo) and Unfallversicherung (MARGINAL due to 95% unenriched image markers that are actually 60/63 small table-cell checkmark icons correctly filtered by decorative_icon gate). Propagating skip metadata to blocks and excluding tagged-decorative blocks from the unenriched count would likely move both documents toward PASS. The landscape PictureResult suppression prevents false enrichment-promotion verdicts for any document processed through the landscape reextract path.

**Affected Documents:**
- GHV-TKV-Tarif.pdf (MARGINAL -- 3/4 decorative animal silhouettes/logo correctly skipped but counted as unenriched)
- Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf (MARGINAL -- 60/63 small table-cell icons/checkmarks correctly filtered but counted as unenriched)
- uae_numbers_english_page_16_17_landscape - Copy.pdf (false enrichment-promotion from landscape fallback PictureResults)

**Files / Functions:**
- `src/pageindex_mcp/client.py :: _enrich_image_blocks() (line ~737) -- after matching PictureResult to block, propagate pr.get('skipped_reason') and pr.get('decorative') onto block dict`
- `src/pageindex_mcp/helpers.py :: classify_verdict() image_enrichment_promoted path (line ~1668-1675) -- exclude blocks tagged decorative=True or with known skipped_reason from unenriched count`
- `src/pageindex_mcp/converters.py :: _recover_picture_text -- verify all skip paths set skipped_reason on PictureResult dict consistently`
- `src/pageindex_mcp/converters.py :: landscape_fallback_picture PictureResult emission -- ensure skipped_reason is set on landscape fallback PictureResults so they are excluded from enrichment counting`

**Fix:** (1) In _enrich_image_blocks, after matching a PictureResult to an image block, add: if pr.get('skipped_reason'): block['skipped_reason'] = pr['skipped_reason']; if pr.get('decorative'): block['decorative'] = True. (2) In classify_verdict's image_enrichment_promoted calculation, filter out blocks where block.get('decorative') is True or block.get('skipped_reason') is truthy from the unenriched count. (3) Audit _recover_picture_text to verify all skip paths (decorative_icon, OCR min-chars, page_coverage, clip_text_already_exported) consistently set skipped_reason on the PictureResult dict. (4) Suppress PictureResults that carry skipped_reason from contributing to image_enrichment_promoted verdicts -- this covers both the decorative-icon case and the landscape-fallback case in a single filter.

**Effort:** Small (0.5-1 day). Two field-propagation additions in client.py, one filter condition in helpers.py, and one consistency audit of _recover_picture_text skip paths. No architectural changes.

**Test Strategy:** Unit tests: (a) _enrich_image_blocks with PictureResult containing skipped_reason='decorative_icon' verifying block dict gets skipped_reason and decorative fields; (b) classify_verdict with image blocks containing decorative=True verifying they are excluded from unenriched count; (c) _recover_picture_text for each skip path verifying PictureResult carries skipped_reason; (d) PictureResult with skipped_reason asserting image_enrichment_promoted is NOT set. Integration: re-run GHV-TKV-Tarif and Unfallversicherung through the pipeline and verify decorative icons are tagged in output and no longer counted as unenriched gaps in verdict.


## Implementation Plan

| Batch | Decisions | Rationale |
|-------|-----------|-----------|
| 0 | D0, D1, D2 | Critical severity fixes with no inter-dependencies. D0 fixes the landscape runaway causing timeouts for world-stats and fragmentation for UAE docs (5 sub-fixes in converters.py and helpers.py). D1 fixes write-barrier delay and catch-and-downgrade of PersistenceNotVisibleError in storage.py. D2 lands already-staged D19 code (zero new implementation, just commit). All three are independent and address the highest-impact regressions from Run-19. |
| 1 | D3, D4 | Lower severity improvements that depend on Batch 0 being stable. D3 (RTL flat fallback) unblocks the flat-fallback path for future RTL documents with clean flat text; does not change وارد 597's ERROR verdict. D4 (skip metadata propagation + landscape PictureResult suppression) reduces false audit flags for decorative/skipped images; absorbs the enrichment-verdict suppression originally in D0 to avoid cross-batch coordination hazards on the same classify_verdict code path. Both are client.py/helpers.py changes that benefit from the converter stability established in Batch 0. |

## Test Strategy

| Decision | Title | Test Approach |
|----------|-------|---------------|
| D0 | Landscape reextract runaway: page cap, thread kill, splice fix, and fragmentation guard | Unit tests: (a) synthetic 20-page document with 15 landscape pages verifying MAX_LANDSCAPE_PAGES cap fires and only top-N pages are reextracted; (b) singleton-ratio guard test with fixture of 80% single-char kv rows asserting segmentation is skipped. Integration tests: (c) multi-landscape-page document sized to approach per-chunk timeout asserting graceful degradation within budget and no background thread surviving child process exit. Regression: re-run uae_numbers_landscape and world-stats-pocketbook; verify FAIL->MARGINAL and ERROR->clean-timeout-with-status respectively. |
| D1 | Write barrier delay: cap schedule and catch PersistenceNotVisibleError in save callers | Unit tests: (a) verify _confirm_write_visible with new delay schedule totals <=0.45s; (b) mock _confirm_write_visible to raise PersistenceNotVisibleError and verify save_doc/save_doc_meta catch it, log a warning, increment write_barrier_exhausted counter, and return normally. Integration: re-run the Arabic SLA doc and confirm it completes within the scorer polling window. |
| D2 | Land staged D19 enrichment density-preserve fix | Run existing test_rfc034_d19_enrichment.py to confirm all tests pass. Verify _ocr_information_density returns expected scores for known inputs (high-digit OCR text vs low-density placeholder). Verify _enrich_image_blocks preserves high-density existing OCR when new text is low-density. Re-run the pie chart JPG through the pipeline and confirm OCR text is preserved (not displaced by placeholder). Commit using selective staging (`git add -p` or explicit file paths) to isolate D19 hunks from unrelated uncommitted changes in client.py/converters.py/helpers.py/storage.py. |
| D3 | RTL reversal: add flat-fallback routing instead of terminal rejection | Unit tests: (a) validate_tree returning 'rtl_reversal' with clean flat text routes to flat extraction and produces artifact; (b) validate_tree returning 'rtl_reversal' with garbled flat text triggers garble gate and raises LowQualityTreeError (ERROR, zero output); (c) synthetic RTL document where tree fails but flat succeeds. Integration: re-run وارد 597 and confirm it still ERRORs (garble gate rejects numeric-junk flat text) with improved diagnostic logging. Separately, synthetic RTL test doc with clean flat text verifies routing produces a flat artifact. |
| D4 | Propagate PictureResult skip metadata to image blocks and suppress false enrichment verdicts | Unit tests: (a) _enrich_image_blocks with PictureResult containing skipped_reason='decorative_icon' verifying block dict gets skipped_reason and decorative fields; (b) classify_verdict with image blocks containing decorative=True verifying they are excluded from unenriched count; (c) _recover_picture_text for each skip path verifying PictureResult carries skipped_reason; (d) PictureResult with skipped_reason asserting image_enrichment_promoted is NOT set. Integration: re-run GHV-TKV-Tarif and Unfallversicherung through the pipeline and verify decorative icons are tagged in output and no longer counted as unenriched gaps in verdict. |

## Risks

- D0 thread-kill fix may require replacing ThreadPoolExecutor with subprocess-per-chunk, which is a larger refactor than patching the existing pool; if the subprocess approach introduces IPC overhead, chunked Docling conversion could slow down for all documents, not just landscape-heavy ones.
- D0 MAX_LANDSCAPE_PAGES cap is a heuristic that may cause some legitimately landscape-heavy documents (e.g., presentation decks) to lose content on pages beyond the cap; the cap value needs calibration against the corpus.
- D0 LANDSCAPE_CHAR_THRESHOLD re-tuning (requiring picture/graphic regions in addition to low char count) may miss pages that genuinely need landscape reextraction but lack detectable picture regions in the primary extraction.
- D0 fix (4) (splice landscape fallback markdown into correct page position instead of appending at end) changes document block ordering for every document that triggers the landscape path, not just the regressed ones. A regression test against the broader corpus should verify that splice-induced ordering does not degrade currently-passing documents. The integration test for uae_numbers_landscape exercises this path, but a broader spot-check of any other document that triggers landscape reextraction is recommended.
- D0 world-stats-pocketbook success criterion is ambiguous: trace finding [3] calls it a pre-existing timeout (ERRORed 3+ consecutive runs) while finding [10] says Run 16 PASSed under pre-RFC-035 code. The regression test targets clean-timeout-with-status (the landscape page cap prevents the runaway loop and the child exits with a timeout status rather than being killed by arq); full completion is not guaranteed and depends on whether non-landscape pages fit within the timeout budget.
- D1 reducing write barrier delays from 4.4s to 0.45s assumes MinIO read-after-write consistency is sub-100ms; if the deployment environment has higher-latency MinIO (e.g., network-attached storage), the reduced budget may trigger false exhaustion warnings more frequently.
- D1 downgrading PersistenceNotVisibleError to a warning means the pipeline will silently proceed even when stat_object genuinely cannot see the written object; if MinIO has an actual write failure (not just visibility delay), the warning path masks data loss. The put_object call itself succeeds before _confirm_write_visible runs, so a stat_object failure is a visibility issue, not a write failure -- but this distinction depends on MinIO's consistency guarantees.
- D1 retry hypothesis is unconfirmed: no worker logs showing PersistenceNotVisibleError fired or arq retried doc_id d58be46f have been located. The fix is justified on engineering grounds (over-provisioned barrier budget, unhandled exception from a successful write) regardless of whether the retry actually caused the 3-5 minute delay for this specific document.
- D2 git status shows client.py modified alongside many other uncommitted RFC-034/RFC-035 changes in converters.py, helpers.py, and storage.py. Committing "as-is" with a broad `git add` will drag unrelated uncommitted work into the commit. D2 must use selective staging (`git add -p` or explicit file paths) to isolate the D19 hunks in client.py and tests/test_rfc034_d19_enrichment.py from other uncommitted changes.
- D3 adding rtl_reversal to the flat routing whitelist may cause documents with genuinely reversed Arabic (not numeric junk) to persist as flat artifacts with garbled content rather than being rejected; the flat-path garble gate is the safety net but its detection threshold may not catch all cases.
- D3 does not change وارد 597's ERROR verdict: the document's source text layer is numeric junk on both tree and flat paths, so the garble gate correctly rejects the flat text. Improving this document's outcome requires a secondary OCR engine or VLM fallback (out of scope, tracked under [4,9,12]).
- D4 excluding decorative-tagged blocks from the unenriched count could mask genuine enrichment failures if _recover_picture_text incorrectly classifies a content-bearing image as decorative; the decorative_icon sub-20pt threshold and OCR min-chars gate need to be validated against edge cases.
- All five decisions modify code that is currently uncommitted on the feat/pdf-inspector-shadow-pilot branch; landing these fixes requires careful staging order to avoid conflicts with the existing uncommitted RFC-035 and RFC-034 changes already in the working tree.

### D5: Extend Arabic structural heading injection to cover قرار/مرسوم/قانون patterns

**Scope:** `_inject_arabic_structural_headings()` in `converters.py` (line 135) detects باب/فصل/قسم/جزء/مادة but not قرار (resolution), مرسوم (decree), or قانون (law) — even though `_AR_KNOWN_WORDS` (line 105) already lists them for reversal detection. Five Arabic legal documents stall at MARGINAL with flat/collapsed hierarchy because their primary structural markers are unrecognized.

**Root Cause:** `_AR_PART_RE` (line 86) matches باب|فصل|قسم|جزء only. `_AR_ARTICLE_RE` (line 87) matches مادة only. Documents structured around قرار (Cabinet Resolutions), مرسوم (Federal Decree-Laws), and قانون (Laws) have their structural markers emitted as body text by Docling, so `_inject_arabic_structural_headings` never promotes them to headings. The reversal detection at `_AR_KNOWN_WORDS` already recognizes these words but only for OCR direction detection, not heading promotion.

**Rationale:** This is the root cause of 5 stalled MARGINAL verdicts (cabinet_resolution_no_21, قرار مجلس الوزراء رقم 1, قرار مجلس الوزراء رقم 106, مرسوم بقانون اتحادي رقم 13, مرسوم بقانون اتحادي رقم 33). The fix is a small regex extension to existing, proven post-processing injection infrastructure — no new architecture needed.

**Affected Documents:**
- cabinet_resolution_no_21_of_2020 (MARGINAL — depth 3, Article/sub-clause hierarchy collapsed)
- قرار مجلس الوزراء رقم (1) لسنة 2022 (MARGINAL — depth 1, 308 nodes all flat, 0 structural markers)
- قرار مجلس الوزراء رقم (106) لسنة 2022 (MARGINAL — depth 0, scanned Arabic, structural collapse)
- مرسوم بقانون اتحادي رقم (13) لسنة 2022 (MARGINAL — 0 nodes, depth 1)
- مرسوم بقانون اتحادي رقم (33) لسنة 2021 (MARGINAL — hierarchy collapse, 234 nodes at depth 4 but content-density concern)

**Files / Functions:**
- `src/pageindex_mcp/converters.py :: _AR_PART_RE (line 86) -- extend to include قرار|مرسوم|قانون`
- `src/pageindex_mcp/converters.py :: _AR_MARKER_CAPTURE_RE (line 100) -- extend to include new markers`
- `src/pageindex_mcp/converters.py :: _inject_arabic_structural_headings (line 135) -- add depth-tier mapping: قرار/مرسوم/قانون as '#' (part-level), existing مادة stays '##' (article-level)`
- `tests/test_rfc036_d5_arabic_headings.py -- new test file`

**Fix:** (1) Extend `_AR_PART_RE` from `(?:باب|فصل|قسم|جزء)` to `(?:باب|فصل|قسم|جزء|قرار|مرسوم|قانون)`. (2) Extend `_AR_MARKER_CAPTURE_RE` to include the new markers with their parenthetical numeral patterns (e.g., "قرار مجلس الوزراء رقم (1)"). (3) Add reversed-form stems to the second alternative in `_AR_PART_RE`: قرار→رارق, مرسوم→موسرم (already in `_AR_KNOWN_WORDS_REVERSED`), قانون→نوناق. (4) Optionally add a third heading depth `###` for sub-structural patterns (فقرة paragraph, بند clause) if observed in the stalled documents. (5) For scanned Arabic documents (قرار 106), verify that the OCR char density is sufficient for heading detection to fire — if not, this is a D7 OCR-quality dependency.

**Effort:** Small (0.5-1 day). Regex extensions to existing patterns + new test file. The injection infrastructure is proven (RFC-027 D4, RFC-028 D1, RFC-033 D8).

**Test Strategy:** Unit tests: (a) synthetic Arabic text with قرار/مرسوم/قانون markers verifying heading injection at correct depth; (b) reversed OCR variants of the new markers; (c) mid-paragraph citations ("...المشار إليها في القرار رقم 5 من...") NOT promoted. Integration: re-run the 5 affected documents and verify depth improvement.
---

### D6: Depth-adequacy scoring proportional to document complexity

**Scope:** `classify_verdict` in `helpers.py` (line 1592) uses a fixed `depth >= 2` threshold for PASS eligibility regardless of document complexity. A 595-node penal code and a 10-node single-page document both pass at depth 2, even though the penal code's Book/Chapter/Section/Article hierarchy demands depth 4+.

**Root Cause:** `classify_verdict`'s depth check (lines 1707-1713) is a binary `depth >= 2` gate. Documents with high structural complexity (many nodes, many pages, legal hierarchy) score PASS at depth 2 despite having a provably collapsed tree. The current depth threshold was designed as a minimal quality floor, not as a complexity-proportional scoring criterion.

**Rationale:** 2 documents stall at MARGINAL solely because depth 2 is too shallow for their structural complexity: FEDERAL LAW NO (3) OF 1987 (595 nodes, depth 2, penal code) and Haftpflicht-Allgemeine-Bedingungen (136 nodes, depth 2, German insurance general conditions). Adding a complexity-proportional depth expectation to `classify_verdict` surfaces these as MARGINAL with a clear diagnostic reason rather than silently passing with a collapsed tree.

**Affected Documents:**
- FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE (MARGINAL — 595 nodes, depth 2)
- Haftpflicht-Allgemeine-Bedingungen.pdf.pdf (MARGINAL — 136 nodes, depth 2)

**Files / Functions:**
- `src/pageindex_mcp/helpers.py :: classify_verdict() (line ~1707-1713) -- add complexity-proportional depth expectation`
- `tests/test_rfc036_d6_depth_adequacy.py -- new test file`

**Fix:** (1) After the existing `depth >= 2` PASS gate, add a secondary depth-adequacy check: compute `expected_min_depth = 2 + floor(log2(node_count / 50))` (capped at 5). When `depth < expected_min_depth`, set verdict to MARGINAL with reason `depth_inadequate` and include `expected_min_depth` and `actual_depth` in diagnostic metadata. (2) The formula yields: nodes < 100 → depth 2 (unchanged), 100-199 → depth 3, 200-399 → depth 4, 400+ → depth 5. (3) This is a verdict-scoring change only — it does not affect `validate_tree`'s persistence gating (Hard Rule 5 enforcement is unchanged).

**Effort:** Small (0.5 day). One conditional in `classify_verdict` + new test file.

**Test Strategy:** Unit tests: (a) 50-node tree at depth 2 → PASS (baseline unchanged); (b) 200-node tree at depth 2 → MARGINAL with reason `depth_inadequate`; (c) 200-node tree at depth 4 → PASS; (d) 600-node tree at depth 2 → MARGINAL; (e) 600-node tree at depth 5 → PASS. Verify formula boundary conditions at 100, 200, 400 node thresholds.
---

### D7: OCR engine evaluation spike — PaddleOCR and Docling OCR service wrappers

**Scope:** `_recover_picture_text` in `converters.py` (line 1724) shells out to Tesseract CLI for picture OCR. Tesseract garbles chart data, Arabic numerals, and mixed-script content. Two alternative OCR engines (PaddleOCR and Docling's built-in OCR) should be evaluated side-by-side as service wrappers under `services/`.

**Root Cause:** Tesseract CLI produces garbled chart OCR (truncated numerals, Arabic misreads like ذكق for ذكور) affecting at least 1 corpus document (pie chart JPG — MARGINAL) and contributing to poor OCR quality on scanned Arabic documents. The project's `services/` directory already contains `services/docling-service` as a precedent for service-level separation.

**Rationale:** This is a time-boxed evaluation spike, not a production deployment. The deliverable is a comparison report of Tesseract vs PaddleOCR vs Docling OCR on the corpus's chart/table/scanned-Arabic images, with accuracy metrics and a recommendation. No production code changes to `converters.py` until the spike is evaluated.

**Affected Documents:**
- image pie chart about labor distribution in january 2025 - Copy.jpg (MARGINAL — Tesseract garbling)
- قرار مجلس الوزراء رقم (106) لسنة 2022 (MARGINAL — scanned Arabic, below-average OCR density)
- وارد رقم 597 (ERROR — numeric-junk text layer, would benefit from secondary OCR if flat text is replaced)

**Files / Functions:**
- `services/paddleocr-service/ -- new service wrapper: FastAPI app exposing POST /ocr endpoint, accepts image bytes, returns OCR text`
- `services/docling-ocr-service/ -- new service wrapper: FastAPI app routing image bytes through Docling's OCR pipeline, returns OCR text`
- `scripts/ocr_spike_eval.py -- evaluation script: runs all three engines (Tesseract, PaddleOCR, Docling OCR) on corpus chart/table images, computes accuracy metrics, writes comparison report`

**Fix:** (1) Create `services/paddleocr-service/` with a minimal FastAPI app wrapping PaddleOCR (multilingual model, GPU-optional). Expose `POST /ocr` accepting PNG bytes, returning `{text, confidence, lang}`. Dockerfile + requirements.txt. (2) Create `services/docling-ocr-service/` with a minimal FastAPI app routing image bytes through Docling's EasyOCR-based pipeline. Same API contract. (3) Write `scripts/ocr_spike_eval.py` that extracts test images from the corpus (chart images, scanned Arabic pages), runs all three engines, and computes: character accuracy (vs ground truth where available), structural coherence (are numbers/labels in correct read order), Arabic-specific metrics (diacritics preservation, numeral accuracy). (4) Both service wrappers run under the same `docker-compose` or single FastAPI deployment alongside the existing `services/docling-service`.

**Effort:** Medium (1-2 days). Two minimal service wrappers + evaluation script. No production integration — spike only.

**Test Strategy:** The spike itself IS the test: run all three engines on corpus images, compare outputs, write a recommendation report. Success criterion: identify which engine (if any) improves chart/Arabic OCR accuracy over Tesseract baseline by >= 20% on the test set. If neither clears the bar, document the finding and close the spike.

---

### D4 Amendment: Add content-quality gate to image_enrichment_promoted

**Amendment to D4:** In addition to the original D4 fix (propagate skip metadata, exclude decorative blocks), add a content-quality validation gate to `classify_verdict`'s `image_enrichment_promoted` path (helpers.py lines 1654-1675). Currently, promotion fires when `image_enrichment_ratio >= 0.80` based solely on metadata presence — it does not validate whether the enriched content is structurally usable.

**Additional Fix:** After excluding decorative/skipped blocks from the unenriched count (original D4), add a content-quality check before allowing `image_enrichment_promoted` to set verdict=PASS: (1) compute a singleton-ratio across the document's blocks — if > 60% of blocks are single-value kv singletons (same threshold as D0's `_segment_table_nodes` guard), the content is structurally fragmented and promotion is suppressed; (2) compute a coherent-block ratio — enriched blocks must contain >= 50 chars each on average to count as substantive enrichment; bare axis labels, single numbers, or placeholder text do not qualify. When either check fails, `image_enrichment_promoted` is NOT set, and the document falls through to the standard `max_leaf_ratio` verdict path.

**Additional Affected Documents:**
- uae_numbers_english_page_16_17_landscape - Copy.pdf (false enrichment promotion with 71 singleton kv blocks)
- uae_numbers_english_page_16_17_portrait - Copy.pdf (false enrichment promotion with 89% singleton fragmentation)

**Additional Test Strategy:** (a) Document with 80% singleton-ratio and image_enrichment_ratio >= 0.80 — verify promotion is suppressed; (b) document with enriched blocks averaging 30 chars each — verify promotion is suppressed; (c) document with enriched blocks averaging 200 chars and low singleton ratio — verify promotion fires as before.

## Implementation Plan (Updated)

| Batch | Decisions | Rationale |
|-------|-----------|-----------|
| 0 | D0, D1, D2 | Critical severity fixes with no inter-dependencies. D0 fixes the landscape runaway causing timeouts for world-stats and fragmentation for UAE docs (5 sub-fixes in converters.py and helpers.py). D1 fixes write-barrier delay and catch-and-downgrade of PersistenceNotVisibleError in storage.py. D2 lands already-staged D19 code (zero new implementation, just commit). All three are independent and address the highest-impact regressions from Run-19. |
| 1 | D3, D4 (amended), D5 | D3 (RTL flat fallback) unblocks the flat-fallback path for future RTL documents with clean flat text. D4 (amended: skip metadata propagation + content-quality gate + landscape PictureResult suppression) reduces false audit flags for decorative/skipped images and prevents false enrichment promotions on structurally fragmented content. D5 (Arabic heading extension) extends the existing injection infrastructure to cover قرار/مرسوم/قانون patterns. All are client.py/helpers.py/converters.py changes that benefit from the converter stability established in Batch 0. |
| 2 | D6, D7 | Lower-priority improvements. D6 (depth-adequacy scoring) adds complexity-proportional depth expectations to classify_verdict — a scoring-only change with no persistence-gating impact. D7 (OCR spike) is a time-boxed evaluation with no production code changes; its deliverable is a comparison report, not an integration. Both are independent and can land after Batch 1 stabilizes. |

## Out of Scope

- [7] Arabic character reordering artifacts in source PDF text layer (PyMuPDF extracts 'املادة' instead of 'المادة'). Source data quality issue, not a code defect. NFKC normalization cannot fix character reordering. Would require fuzzy ordinal matching in split_oversized_leaf_nodes -- high risk of false positives.
- [7] Arabic character reordering artifacts in source PDF text layer (PyMuPDF extracts 'املادة' instead of 'المادة'). Source data quality issue, not a code defect. NFKC normalization cannot fix character reordering. Would require fuzzy ordinal matching in split_oversized_leaf_nodes -- high risk of false positives.
- [8] Docling-internal reading-order scramble for Federal Decree-Law No. 47. Upstream bug in docling-hierarchical-pdf's layout/reading-order clustering. The postprocessor logs the inconsistency but only degrades gracefully. Nothing downstream validates emitted heading order against page/position provenance. Fix belongs upstream in Docling, not in pageindex_mcp post-processing.
