<!-- Space: CITRA -->
<!-- Title: RFC-028: Run 11 Arabic Recovery, Garble Detection, and Timeout Wiring -->
<!-- Folder: RFCs -->

# RFC-028: Run 11 Arabic Recovery, Garble Detection, and Timeout Wiring

**Date:** 2026-08-03
**Run:** 11
**Baseline:** 7 PASS / 13 MARGINAL / 4 FAIL / 1 ERROR (25 docs, 24 persisted)
**Prior (Run 10):** 8 PASS / 7 MARGINAL / 10 FAIL / 0 ERROR

## Traceability

| Artifact | Reference |
|---|---|
| Design Document | [design-rfc028-run11-arabic-recovery-and-timeout-wiring.md](../designs/design-rfc028-run11-arabic-recovery-and-timeout-wiring.md) |
| Implementation Plan | [tasks-rfc028-run11-arabic-recovery-and-timeout-wiring.md](../tasks/tasks-rfc028-run11-arabic-recovery-and-timeout-wiring.md) |
| Audit | [CORPUS_REINGESTION_AUDIT_RUN-11.md](../../audit/CORPUS_REINGESTION_AUDIT_RUN-11.md) |

---

## Context

Run 11 is the first run after RFC-027 extraction-gate and Arabic-recovery fixes landed. The headline result is positive: FAIL count dropped from 10 to 4, and 5 previously zero-content Arabic PDFs now yield real content (MOU MOHRE 0->13.5k chars, SLA 0->27.9k chars, qerar 1/2022 0->47.6k chars, qerar 106/2022 0->26.1k chars, marsoom 13 60->5.9k chars). However, all 5 recovered Arabic docs land at MARGINAL with flat/depth-0 trees -- the Arabic structural heading injection (RFC-027 D4) has two bugs preventing marker promotion, and Arabic garble detection has two blind spots (presentation-forms and RTL reversal vocabulary).

The remaining defects cluster into four themes: (1) dead-code timeout bug causing world-stats-pocketbook to ERROR for the 3rd consecutive run, (2) Arabic heading injection bugs producing flat trees despite clean text recovery, (3) garble detection gaps (presentation-forms false-negative, RTL reversal vocabulary insufficiency), and (4) OCR retry/language-detection bugs causing content regression or wrong-language OCR on Arabic docs.

**Audit report:** `audit/CORPUS_REINGESTION_AUDIT_RUN-11.md`

---

## Fix Dimensions

### D0 -- Wire Dynamic Timeout into Worker Subprocess

**Root cause:** `chunked_docling_timeout_s()` exists in `converters.py` (line 2182) but is never imported or called by `worker.py`. The worker always uses the fixed `CHILD_TIMEOUT = 1770s` (`JOB_TIMEOUT 1800 - CHILD_GRACE_SECONDS 30`) at line 245. world-stats-pocketbook-2023.pdf is 292 pages / 6.4MB; Docling runs at ~5-10s/page on CPU, requiring 24-49 minutes. Even with RFC-027 D7's chunked-Docling route splitting the PDF into 2 chunks of ~150 pages, each chunk can take 12-24 minutes. Total processing routinely exceeds the fixed 1770s timeout, causing the worker to SIGTERM then SIGKILL the child subprocess before any artifacts are persisted. RFC-027 task 4.2 explicitly required wiring the dynamic timeout into `worker.py` and was marked complete, but the implementation only created the calculation function without ever calling it. This is a 3-consecutive-run persistent failure (Run 9 ERROR, Run 10 FAIL, Run 11 ERROR).

Additionally, the existing per-chunk constants are too low: `_CHUNKED_DOCLING_BASE_TIMEOUT_S = 300` and `_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S = 600` (converters.py:2178-2179) yield `chunked_docling_timeout_s(2) = 300 + 2*600 = 1500s`, which is *lower* than the current fixed `CHILD_TIMEOUT` of 1770s. The RFC's own root-cause estimate puts world-stats at 24-49 minutes (1440-2940s). Wiring the existing formula without raising the constants would *reduce* the timeout budget and still fail.

arq's `job_timeout` is a worker-level setting (not per-enqueued-job), so raising `JOB_TIMEOUT` to accommodate the dynamic maximum affects ALL jobs, extending worst-case slot occupancy and DLQ/retry latency for every document -- not just large chunked PDFs.

**Affected docs:** world-stats-pocketbook-2023.pdf (292 pages, no persisted artifacts in 3 consecutive runs)

**Fix:**
1. **Raise per-chunk timeout constants** in `converters.py`: set `_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S = 1500` (was 600). This gives `chunked_docling_timeout_s(2) = 300 + 2*1500 = 3300s` (~55 min), covering the 24-49 min observed range with margin. Single-chunk documents get `300 + 1500 = 1800s` (30 min), appropriate for up to 150 pages at 10s/page.
2. **Import and wire** `chunked_docling_timeout_s` in `worker.py`. Before spawning the child subprocess, compute `dynamic_timeout = chunked_docling_timeout_s(chunk_count)`.
3. **Use `max(CHILD_TIMEOUT, dynamic_timeout)`** as the `asyncio.timeout` value -- never go below the existing fixed timeout for non-chunked documents, only extend for large chunked PDFs. This also protects against PyPDF2 read failures (which return `page_count=0`, yielding `chunk_count=1` and a low dynamic timeout): `max(1770, 1800) = 1800`, preserving existing behavior.
4. **Raise `JOB_TIMEOUT` statically** from 1800 to 3630 (`max_dynamic_child_timeout 3300 + 300 buffer + CHILD_GRACE_SECONDS 30`). Since arq's `job_timeout` is worker-level, the outer timeout must statically accommodate the worst-case dynamic child. This doubles worst-case slot occupancy (30 min to ~60 min) for ALL jobs. With `MAX_JOBS = 1` per worker, a stuck large-PDF job blocks the worker for 60 min instead of 30 min before arq times it out. This is acceptable given that the alternative is world-stats never completing across 3+ runs.
5. **Delegate page-count computation to the converter child** (not the worker) to avoid drift: the worker passes the raw PDF path; the child (which already reads page count at converters.py:2318-2340 for routing) computes chunk_count and returns the dynamic timeout as part of its startup JSON handshake. This eliminates the risk of worker and child disagreeing on page count due to PyPDF2 failure in one process but not the other, or the child taking a non-Docling converter route. If the child reports a non-Docling route, the worker falls back to `CHILD_TIMEOUT`.

**Files:** `src/pageindex_mcp/worker.py`, `src/pageindex_mcp/converters.py`
**Severity:** Critical -- 3-consecutive-run persistent ERROR on a 292-page document; dead code from RFC-027 D7.
**Effort:** ~30 lines, 1.5 hours

### D1 -- Fix Arabic Structural Heading Injection (prev_blank + char limit)

**Root cause:** Two bugs in `_inject_arabic_structural_headings` (converters.py:85-115) cause flat/depth-0 trees for 5+ Arabic legal docs that have clean text recovery:

1. **prev_blank guard (line 104):** Requires a blank line before each marker, but scanned OCR output flows continuously without blank-line separators. After the first marker is promoted, `prev_blank = False`; without blank lines in OCR output, `prev_blank` never resets to True, so only the first structural marker gets promoted.

2. **len(t) <= 60 char limit (line 104):** Too tight for Arabic legal headings that commonly include article titles (66-76+ chars). Legitimate markers with titles like "المادة (3) نطاق التطبيق" are skipped.

Additionally, fused marker+title lines (e.g. "المادة (3) نطاق التطبيق" on one OCR line without preceding blank) are never split into a standalone heading plus remaining prose.

**Affected docs:** marsoom 33 (883 blocks, depth-1), qerar 1/2022 (356 blocks, depth-1), qerar 106/2022 (179 blocks, depth-1), SLA (259 blocks, depth-0), marsoom 13 (75 blocks, depth-0)

**Fix:**
1. Remove the `prev_blank` requirement for lines matching Arabic marker regex at line start -- structural markers should be promoted regardless of prior line content.
2. Raise the char limit from 60 to 100 to accommodate Arabic legal headings with titles.
3. Handle fused marker+title lines: when a marker pattern matches at line start but the full line exceeds 100 chars, split the marker portion into a standalone heading line.

**Files:** `src/pageindex_mcp/converters.py`, `tests/test_rfc027_d4.py`
**Severity:** High -- 5+ Arabic docs with clean text but completely flat trees.
**Effort:** ~25 lines, 1 hour

### D2 -- Arabic Presentation-Forms Garble Detection

**Root cause:** `_is_garbled_blob` (helpers.py:879-881) checks PUA (U+E000-F8FF) at 3% threshold but has NO check for Arabic Presentation Forms (U+FB50-FDFF, U+FE70-FEFF). These are positional glyph variants that indicate font-encoded garble -- text extraction emitted positional glyph forms instead of logical Arabic Unicode (U+0600-06FF). `_infer_script` (line 962) counts presentation-forms chars as valid Arabic (lines 971-976), reinforcing the false-negative. Documents with 93%+ presentation forms pass all garble checks and store PASS verdict with empty reason.

**Affected docs:** huquq al-insan (328 nodes, 498,928 chars, 93.6% presentation forms, stored PASS -- the single largest false-PASS in the corpus by char volume)

**Fix:** Add a presentation-forms ratio check in `_is_garbled_blob`, after the existing PUA check. Count chars in U+FB50-FDFF and U+FE70-FEFF ranges; if ratio of presentation-forms chars to total Arabic-range chars exceeds 0.50, return True (garbled). This catches documents where text extraction emitted positional glyph forms instead of logical Arabic Unicode.

**Note on `_infer_script`:** `_infer_script` (helpers.py:962-976) counts presentation-forms chars as valid Arabic, reinforcing the false-negative by classifying presentation-forms text as clean Arabic for all consumers of `_infer_script`. This is intentionally left as-is for D2: `_infer_script`'s purpose is script *identification* (is it Arabic vs Latin vs CJK?), and presentation forms *are* Arabic-script chars -- the problem is garble, not misidentified script. The garble detection fix in `_is_garbled_blob` is the correct layer. Changing `_infer_script` to exclude presentation forms would break downstream consumers that need to know the text is Arabic-script (e.g., RTL layout decisions) regardless of garble state.

**Files:** `src/pageindex_mcp/helpers.py`
**Severity:** High -- 498k garbled chars stored as PASS; largest single false-PASS in corpus.
**Effort:** ~15 lines, 45 min

### D3 -- Expand RTL Reversal Detection Vocabulary

**Root cause:** `_tree_is_rtl_reversed` (RFC-027 D3) compares `_arabic_readability_score` for forward vs `get_display()` reversed text, but `_arabic_readability_score` only checks 14 `_AR_COMMON_WORDS` and `_AR_DEFINITE_RE` matches. For specialized vocabulary (governance, data policy docs), both `orig_total` and `disp_total` are 0, so `disp_total > orig_total` is False and reversal goes undetected. `validate_tree` returns `(True, '')` and stored verdict is PASS with empty reason.

**Affected docs:** siyasat hawkama (24 nodes, 20,330 chars, 100% reversed RTL node titles, stored PASS with empty reason -- byte-identical to Run 10)

**Fix:**
1. Expand `_AR_COMMON_WORDS` (converters.py:1315) with governance/legal domain terms (حوكمة, بيانات, سياسة, إدارة, تنظيم, قرار, وزارة, لائحة, تنفيذية, مرسوم, etc.). Note: `_AR_COMMON_WORDS` lives in `converters.py` while `_tree_is_rtl_reversed` (the consumer) lives in `helpers.py` -- the fix must either move the word list to `helpers.py` or import it cross-module.
2. Add a character-level morphological reversal check in `_tree_is_rtl_reversed` (helpers.py:1047) as a vocabulary-independent signal: reversed Arabic produces morphologically invalid sequences (final-form chars at word start, initial-form at word end) detectable without vocabulary dependence.

**Files:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/converters.py`
**Severity:** High -- reversed Arabic text silently passes all gates (unchanged from Run 10).
**Effort:** ~30 lines, 1.5 hours

### D4 -- Fix-3 OCR Retry Keep-Best Instead of Unconditional Overwrite

**Root cause:** D2/Fix-3 low-content OCR retry (client.py:974-1046) unconditionally replaces `md_content` with retry output, causing content regression when retry OCR also fails. For al-qarar al-tanzimi, content regressed from 230 chars (Run 10) to 123 chars (Run 11) because the retry's Tesseract OCR on the PUA-encoded PDF produced even less output than the original extraction. The retry was designed to rescue near-zero-content docs, but the unconditional overwrite makes already-low-content docs worse when the retry also fails on the same underlying defect.

**Affected docs:** al-qarar al-tanzimi (230 chars -> 123 chars, content regression)

**Fix:** Snapshot pre-retry `total_chars` (from the tree/flat structure) before the retry. Run the retry. Compare post-retry `total_chars` against the snapshot. Keep whichever result has more content. This makes the retry a "try and keep if better" step rather than a blind overwrite.

**Files:** `src/pageindex_mcp/client.py`
**Severity:** Medium -- content regression on a doc the retry was designed to help.
**Effort:** ~15 lines, 45 min

### D5 -- Fix OCR Language Detection Source in _recover_picture_results

**Root cause:** `_recover_picture_results` (converters.py:2074) derives the Tesseract language list from `md` (the Docling markdown export) rather than the filename. For scanned Arabic PDFs where `md` is near-empty or all-digits, `detect_ocr_langs(md)` returns `['eng']` because the Arabic ratio is 0 and the function falls through to the default English. This causes all picture crops to be OCR'd with the wrong language model. `client.py` uses a union of `detect_ocr_langs(filename)` AND `detect_ocr_langs(md_content or '')` at the OCR escalation sites (lines 1002-1004 and 1171-1173), ensuring Arabic is detected from the filename even when md_content is empty. The `_recover_picture_results` call site was missed and uses only `detect_ocr_langs(md)` with no filename fallback.

Verified: `detect_ocr_langs('651001429 6 1 mo/2025/597 5/8/2025 51001429')` returns `['eng']` on ward-597's representative `md` content.

Additionally, each recovered fragment is persisted twice (raw `role:"image"` block + synthetic `role:"prose"` `> [Chart text]:` block), doubling the char count (1884 stored vs ~940 actual).

**Affected docs:** ward 597 (0 Arabic chars recovered; all 70 blocks are numeric-junk OCR'd with English model)

**Residual gap:** D5 fixes the language detection source and deduplication, but ward-597 has a deeper problem: Docling's layout model emits no full-page Picture region for the scanned Arabic body pages -- only tiny stamp/barcode bounding boxes are detected as Picture elements. Without a full-page Picture crop, `_recover_picture_results` never receives the Arabic body text for OCR regardless of language setting. D5 alone will improve language detection for the crops that *are* emitted, but ward-597 will likely remain at near-zero Arabic chars until a full-page OCR fallback is implemented for pages with no Picture region. This residual is explicitly out-of-scoped below (see "Full-page OCR fallback for scanned pages with no Picture region").

**Fix:**
1. Thread `filename` into `_recover_picture_results` and use `detect_ocr_langs(filename)` instead of `detect_ocr_langs(md)`.
2. De-duplicate persisted output: stop emitting both the raw `role:image.ocr_text` block AND the synthetic `role:prose` `[Chart text]` splice block for the same fragment.

**Files:** `src/pageindex_mcp/converters.py`
**Severity:** Medium -- wrong OCR language on Arabic docs; char-count inflation from duplication.
**Effort:** ~20 lines, 1 hour

### D6 -- Gate-vs-Judge Alignment for Image Markers in Hierarchical Docs

**Root cause:** `classify_verdict` (helpers.py:1244) only tracks `image_enrichment_ratio` for flat docs (`content_class` flat_prose/flat_mixed via the `image_enrichment_promoted` gate at line 1274). For hierarchical docs (`content_class=''` or `'unknown'`), `classify_verdict` is called at `client.py:1518` with `image_enrichment_ratio=None` and `content_class=''`, so unenriched `<!-- image -->` markers in tree node text are invisible to the code-level gate. The stored verdict correctly returns PASS (502 nodes, depth 4, good max_leaf_ratio), but the LLM audit judge sees 12 unenriched image markers and downgrades to MARGINAL.

This is a gate-vs-judge alignment gap, not a code bug in `classify_verdict` itself. The 12 markers are likely decorative seals/logos in a 502-node/110k-char document with zero content loss.

**Affected docs:** federal_decree_law_no_33 (502 nodes, 110k chars, PASS stored, MARGINAL judged)

**Fix:** Align the LLM audit judge prompt to not penalize decorative/seal image markers in hierarchical docs where text extraction is complete. The judge prompt lives in the corpus ingest+score audit pipeline (`.claude/skills/corpus-ingest-score` and related scoring scripts), not in `helpers.py` or `client.py`. The code-level `classify_verdict` in `helpers.py` already correctly returns PASS for this case -- no source-code change is needed there. The fix is a prompt-engineering change in the audit scoring pipeline.

**Files:** `.claude/skills/corpus-ingest-score` (audit judge prompt), `src/pageindex_mcp/helpers.py` (verify `classify_verdict` behavior only, no code change)
**Severity:** Low -- scoring-prompt alignment, no content loss.
**Effort:** ~10 lines, 30 min

### D7 -- Add Standalone Roman-Numeral Ordinal Splitting

**Root cause:** `_OVERSIZED_ORDINAL_RE` in `helpers.py` (line 1539-1554) has no pattern for standalone Roman numeral clause markers (I., II., III., IV. etc.). It only matches `Part [IVX]+` as a compound marker but not bare Roman numeral sub-clause numbering. Haftpflicht-Besondere-Bedingungen uses Roman-numeral sub-levels (I through XXVII) as its 27-clause structure, which the ordinal splitter cannot detect, leaving the tree at depth-2.

**Affected docs:** Haftpflicht-Besondere-Bedingungen (138k chars, depth-2, 27 Roman-numeral sub-clauses undetected)

**Fix:** Add a standalone Roman-numeral alternative to `_OVERSIZED_ORDINAL_RE`: e.g. `r'|(?P<roman>[IVX]+)\.\s'` matching patterns like "I. ", "II. ", "III. ". Add corresponding `_ordinal_value` handling to parse via `_roman_to_int`.

**Files:** `src/pageindex_mcp/helpers.py`
**Severity:** Low -- 1 doc, depth improvement from 2 to 3+.
**Effort:** ~15 lines, 45 min

---

## Out of Scope

- **Vector-icon table cell OCR for Unfallversicherung** (finding 5): large complexity, already identified as RFC-028 candidate by RFC-027. Requires per-cell rasterization+OCR or Docling upstream enhancement.
- **PUA-encoded PDF rasterize-then-OCR final fallback** (finding 10): large complexity, requires per-doc investigation. When both text-layer extraction AND OCR produce near-zero output on custom-font PUA PDFs, a full-page rasterize-then-OCR fallback is needed.
- **Table-aware splitting for rate-table PDFs like GHV-TKV-Tarif** (finding 4): medium complexity structural change to `split_oversized_leaf_nodes`. Table-boundary detection is a different splitting paradigm.
- **MOU document type routing** (finding 12): data quality issue, not a code bug. MOUs lack formal Arabic structural markers entirely. No code fix applicable without broader document-type detection.
- **Standalone image (.jpg) OCR quality and chart-data extraction** (finding 17): VLM is locked off per RFC-004. Chart semantic extraction requires VLM or dedicated chart-parsing library. Accepted gap.
- **Full-page OCR fallback for scanned pages with no Picture region** (ward-597, finding 16 defect 2): When Docling's layout model detects no full-page Picture bounding box on a scanned page (only small stamps/barcodes), `_recover_picture_results` never receives the Arabic body text for OCR. D5 fixes the language detection source for crops that are emitted, but does not add a fallback that rasterizes and OCR's full pages when no Picture region covers the body text. This requires a new page-level OCR escalation path (detecting pages with <N% text coverage and no Picture region, then rasterizing and OCR'ing the full page). Medium-large complexity; ward-597 will remain at near-zero Arabic chars until this is implemented in a future RFC.
- **Chart crop retention on clip_text_already_exported skip** (finding 11): medium complexity. Lower priority since VLM_DESCRIBE_IMAGES is locked off -- retained PNGs would have no consumer until VLM is enabled.

---

## Implementation Plan

### Batch 1 -- Critical Bugs with No Inter-Dependencies

| Decision | Summary | Rationale |
|----------|---------|-----------|
| D0 | Wire dynamic timeout into worker subprocess | Unblocks world-stats (3-run persistent ERROR); dead-code bug, self-contained in worker.py |
| D1 | Fix Arabic heading injection (prev_blank + char limit) | Unblocks 5+ Arabic docs stuck at depth-0; self-contained in converters.py |
| D2 | Arabic presentation-forms garble detection | Catches largest single false-PASS (498k garbled chars); self-contained in helpers.py |

All three touch different files/functions with no inter-dependencies.

### Batch 2 -- Arabic Recovery Hardening (Depends on Batch 1)

| Decision | Summary | Rationale |
|----------|---------|-----------|
| D3 | Expand RTL reversal detection vocabulary | No functional dependency on D2 (different functions: `_tree_is_rtl_reversed` vs `_is_garbled_blob`); batched here for combined Arabic-doc regression testing after D1 heading fixes |
| D4 | Fix-3 OCR retry keep-best | Touches client.py OCR retry path; should be tested after D1 Arabic heading fixes are in place to validate combined effect on Arabic docs |
| D5 | Fix OCR language detection source in _recover_picture_results | Touches converters.py OCR path; should be tested together with D4 after D1 Arabic heading fixes |

### Batch 3 -- Lower-Severity Improvements

| Decision | Summary | Rationale |
|----------|---------|-----------|
| D6 | Gate-vs-judge alignment for image markers | Scoring-prompt change with no code dependency on earlier batches |
| D7 | Roman numeral ordinal splitting | Independent feature addition |

---

## Test Strategy

| Decision | Test Approach | Key Assertions |
|----------|---------------|----------------|
| D0 | Unit: verify `_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S = 1500` and `chunked_docling_timeout_s(2) >= 3000`. Unit: mock child startup handshake reporting chunk_count=2, assert worker uses `max(CHILD_TIMEOUT, dynamic_timeout)` as `asyncio.timeout` value. Unit: verify `JOB_TIMEOUT = 3630` and `JOB_TIMEOUT > chunked_docling_timeout_s(2) + CHILD_GRACE_SECONDS`. Unit: mock child reporting non-Docling route, assert worker falls back to `CHILD_TIMEOUT`. Integration: process world-stats-pocketbook, assert completion without SIGTERM/SIGKILL. | Per-chunk constant raised; dynamic timeout > fixed timeout for 2-chunk PDF; `JOB_TIMEOUT` accommodates worst-case dynamic; child-reported route drives worker timeout; large PDF completes processing |
| D1 | Unit: pass Arabic markdown with consecutive article markers (no blank lines between them), assert all markers promoted to ATX headings. Unit: pass Arabic marker line of 75 chars, assert promotion (was rejected by 60-char limit). Unit: pass fused marker+title line without preceding blank, assert heading created. Regression: extend `tests/test_rfc027_d4.py` with continuous-OCR and long-title fixtures. | All article markers promoted regardless of prev_blank; Arabic docs depth >= 2; 60-char titles promoted |
| D2 | Unit: pass text blob with 93% Arabic Presentation Forms (U+FB50-FEFF), assert `_is_garbled_blob` returns True. Unit: pass text blob with 10% presentation forms, assert returns False (below threshold). | huquq al-insan verdict != PASS; presentation-forms ratio > 0.50 triggers garble detection |
| D3 | Unit: expand `_AR_COMMON_WORDS` test to include governance terms, verify siyasat-hawkama text scores higher forward than reversed. Unit: test morphological reversal check (final-form chars at word start detected as reversed). | siyasat-hawkama: `_tree_is_rtl_reversed` returns True; verdict != PASS |
| D4 | Unit: snapshot pre-retry chars, mock retry producing fewer chars, assert pre-retry content is kept. Unit: mock retry producing more chars, assert retry content replaces original. | al-qarar al-tanzimi: content >= 230 chars (no regression from Run 10) |
| D5 | Unit: mock `_recover_picture_results` with Arabic filename + near-empty md, assert `detect_ocr_langs` called with filename (returns `['ara']`), not md (would return `['eng']`). Unit: verify de-duplication of image+prose blocks for same fragment. | ward-597: OCR language includes Arabic; no double-counted blocks |
| D6 | Unit: call `classify_verdict` for hierarchical doc with 12 unenriched image markers, assert verdict is PASS (confirms existing code behavior is correct). Integration: run the audit scoring pipeline on federal_decree_law_no_33 with the updated judge prompt, assert LLM judge returns PASS (not MARGINAL). The prompt change is validated via integration test against the scoring pipeline, not via unit test on `classify_verdict`. | federal_decree_law_no_33: stored verdict PASS; audit judge verdict PASS (not downgraded for decorative markers) |
| D7 | Unit: pass text with "I. ...\nII. ...\nIII. ..." markers, assert `_OVERSIZED_ORDINAL_RE` matches all. Unit: verify `_ordinal_value` parses Roman numerals via `_roman_to_int`. Integration: process Haftpflicht-Besondere, assert depth >= 3. | Roman-numeral markers detected; Haftpflicht depth improves from 2 to 3+ |

---

## Risks

| Risk | Mitigation |
|------|------------|
| D0 dynamic timeout may over-provision time for moderately large docs (100-200 pages), holding arq worker slots longer | `max(CHILD_TIMEOUT, dynamic)` ensures non-chunked docs still use the original 1770s; only chunked PDFs (>150 pages) get extended timeouts. Cap maximum dynamic timeout at 3300s via the constants. Monitor arq queue depth after landing. |
| D0 static `JOB_TIMEOUT` raise (1800->3630) extends worst-case slot occupancy for ALL jobs, not just large chunked PDFs | arq `job_timeout` is worker-level, not per-job, so this is unavoidable. With `MAX_JOBS = 1`, the practical impact is a stuck job blocking the worker for ~60 min instead of ~30 min. DLQ/retry latency doubles for genuinely stuck jobs. Acceptable trade-off: world-stats has failed for 3 consecutive runs. If queue depth becomes a concern, add a second worker replica. |
| D1 removing prev_blank guard may over-promote lines that contain article references mid-paragraph (e.g. "المشار إليها في المادة 5") | Promotion is gated on regex matching at line START only (already done); mid-paragraph references have preceding text on the same line and will not match `^` anchor. Raise char limit to 100 (not unlimited) to filter genuinely long prose lines that happen to contain a marker word. |
| D2 presentation-forms threshold (0.50) may false-positive on docs with legitimate presentation-forms usage | Arabic Presentation Forms are glyph variants for typographic shaping; well-formed Arabic text uses logical Unicode (U+0600-06FF) + shaping at render time. A doc with >50% presentation forms has font-encoded garble by definition. Threshold is conservative. |
| D3 expanded vocabulary may still miss highly specialized domain terms | Morphological reversal check (final/initial-form position analysis) provides a vocabulary-independent fallback signal. Combined vocabulary + morphological approach reduces domain dependence. If both signals are insufficient on future docs, bigram frequency analysis can be added as a third signal in a follow-up. |
| D4 keep-best comparison based on `total_chars` may prefer a garbled-but-longer result over a shorter-but-cleaner one | Compare total_chars as primary signal; add secondary `_is_garbled_blob` check on both results to prefer the non-garbled one regardless of length |
| D5 threading filename into `_recover_picture_results` changes function signature | Callers of `_recover_picture_results` are internal (1-2 call sites in converters.py); signature change is mechanical |
| D6 audit judge prompt change may under-penalize genuine enrichment gaps on hierarchical docs where image markers carry real content | Gate the exemption on both (a) hierarchical doc with depth >= 2 AND (b) text extraction completeness (high char density per page); only exempt docs meeting both criteria |
| D7 standalone Roman-numeral regex may conflict with list-item numbering (e.g. "I. went to the store") | Require minimum 2 consecutive Roman-numeral matches within the same oversized leaf before splitting; single occurrences are ignored |
| D1/D3 interaction: D1 fixes heading promotion for Arabic docs, D3 fixes reversal detection; if a reversed Arabic doc gets headings promoted on reversed text, the tree structure improves but content is still backwards | Batch 2 sequences D3 after D1; reversed docs will have reversed headings after D1 (cosmetic improvement over flat), then D3 detects and routes to bidi repair. Net result is correct. |

---

## Estimated Effort

- D0: ~30 lines, 1.5 hours
- D1: ~25 lines, 1 hour
- D2: ~15 lines, 45 min
- D3: ~30 lines, 1.5 hours
- D4: ~15 lines, 45 min
- D5: ~20 lines, 1 hour
- D6: ~10 lines, 30 min
- D7: ~15 lines, 45 min

**Total:** ~155 lines, ~8 hours across 3 batches

---

## Cross-References

- **Audit report:** `audit/CORPUS_REINGESTION_AUDIT_RUN-11.md`
- **Prior RFC:** RFC-027 (Run 10, extraction gate integrity and Arabic content recovery)
- **D0 trace:** Finding #6 (world-stats-pocketbook timeout, dead-code `chunked_docling_timeout_s`)
- **D1 trace:** Findings #1 (Arabic heading injection cluster), #5 (marsoom 13 drilldown), #13 (SLA drilldown). Finding #12 (MOU) is traced here as context but scoped out -- see Out of Scope "MOU document type routing".
- **D2 trace:** Finding #7 (huquq al-insan presentation-forms garble)
- **D3 trace:** Finding #8 (siyasat-hawkama RTL reversal vocabulary gap)
- **D4 trace:** Finding #14 (al-qarar al-tanzimi content regression drilldown)
- **D5 trace:** Finding #15 (ward-597 OCR language detection + deduplication drilldown)
- **D6 trace:** Finding #0 (federal_decree_law_no_33 gate-vs-judge alignment)
- **D7 trace:** Finding #2 (Haftpflicht-Besondere Roman-numeral sub-levels)
- **Deferred from RFC-027:** Vector-icon table-cell recovery (Unfallversicherung), chart-aware promotion (landscape/portrait twins)
