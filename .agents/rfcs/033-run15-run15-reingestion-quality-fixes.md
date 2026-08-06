<!-- Space: CITRA -->
<!-- Title: RFC-033: Run-15 Corpus Re-ingestion Quality Fixes -->
<!-- Folder: RFCs -->

# RFC-033: Run-15 Corpus Re-ingestion Quality Fixes

**Run:** 15
**Audit:** [audit/CORPUS_REINGESTION_AUDIT_RUN-15.md](../../audit/CORPUS_REINGESTION_AUDIT_RUN-15.md)
**Status:** Draft

## Summary

Run 15 corpus re-ingestion audit (2026-08-06, branch feat/pdf-inspector-shadow-pilot) scored all 25 documents in the corpus. The tally is 11 PASS, 12 MARGINAL, 1 FAIL, 1 ERROR. Compared to Run 14, five documents improved (MARGINAL to PASS) and one worsened (FAIL to MARGINAL with a content-identity caveat). The single FAIL is a stored-verdict-vs-audit divergence (stored PASS, audit FAIL) on an Arabic governance policy with 79% garbled nodes undetected by the gate. The single ERROR is a transient MinIO read race during scoring -- artifacts exist at publish time. Persistent structural weaknesses include Arabic flat-tree collapse (depth=1), garble-gate false positives on mixed-script Arabic, missing German clause heading injection, table segmentation gaps on the primary path, and an unimplemented RFC-022 image_standalone override.

## Decisions

### D0: Wire hysteresis snapshot into corpus reingestion pipeline

**Scope:** snapshot_prior_verdicts() exists (RFC-026 D3) but is never called before MinIO wipe during full re-ingestion. Wire the call into both corpus-ingest and corpus-ingest-score skills so find_prior_verdict() has data, preventing false PASS-to-MARGINAL regressions on byte-identical trees whose max_leaf_ratio falls in the 0.30-0.40 hysteresis band.

**Root Cause:** RFC-026 D3 Task 3.1 implemented snapshot_prior_verdicts() in storage.py (line 747) and the find_prior_verdict() fallback (lines 726-743) but explicitly scoped out operational wiring: 'the wipe itself is an operational/pipeline step, out of scope for this task.' That wiring was never completed. classify_verdict() at line 1578-1589 uses PASS_MAX_LEAF_RATIO=0.30 as the base threshold, widened to 0.40 only when prior_verdict='PASS'. The federal_decree_law_no_33 document (502 nodes, depth 4, 286/502 top-level) hits max_leaf_ratio >= 0.30, fails without hysteresis, then also fails cat_c_promoted (requires < 0.17), falling to MARGINAL.

**Rationale:** Without the snapshot, find_prior_verdict() always returns None during full re-ingestion, disabling the +0.10 PASS_HYSTERESIS_BAND. Documents with max_leaf_ratio between 0.30 and 0.40 regress from PASS to MARGINAL on byte-identical trees -- a false regression caused by operational wiring, not extraction quality.

**Affected Documents:**
- federal_decree_law_no_33_of_2021_regarding_the_regulation_of_employment_relationship_and_its_amendments - Copy.pdf

**Files / Functions:**
- `src/pageindex_mcp/storage.py: snapshot_prior_verdicts() (line 747), find_prior_verdict() (lines 675, 726-743)`
- `.claude/skills/corpus-ingest-score/SKILL.md: add pre-wipe snapshot instruction`
- `.claude/skills/corpus-ingest/SKILL.md: add pre-wipe snapshot instruction`
- `.claude/workflows/corpus-ingest.js: wipe agent prompt (~lines 44-58) — actual wipe call site`
- `.claude/workflows/corpus-ingest-score.js: wipe agent prompt (~lines 49-63) — actual wipe call site`

**Fix:** Create a wipe_processed() utility in storage.py that: (1) calls snapshot_prior_verdicts() which writes to a **separate MinIO prefix** `snapshots/_prior_verdicts.json` (NOT under `processed/`), then (2) removes all `processed/*` objects. The snapshot MUST be stored outside the `processed/` prefix so the subsequent wipe does not delete it. Update find_prior_verdict() (lines 726-743) to read from `snapshots/_prior_verdicts.json` instead of `processed/_prior_verdicts.json`. Update both skill SKILL.md files AND both workflow JS files (.claude/workflows/corpus-ingest.js lines 44-58 and .claude/workflows/corpus-ingest-score.js lines 49-63) to call wipe_processed() instead of raw MinIO delete. The workflow JS agent prompts are the actual wipe call sites used by the pipeline -- updating only the SKILL.md files would leave the operational pipeline unfixed.

**Effort:** Small (~40 lines, 2 hours). No new logic required; wiring existing functions and relocating snapshot prefix.

**Test Strategy:** Unit test: call wipe_processed(), verify `snapshots/_prior_verdicts.json` exists in MinIO AFTER `processed/*` is deleted (snapshot survives wipe). Unit test: verify find_prior_verdict() reads from `snapshots/_prior_verdicts.json` and returns the correct prior verdict. Integration test: ingest a doc with PASS verdict, run wipe_processed(), re-ingest the same doc, verify find_prior_verdict() returns the prior PASS and the hysteresis band activates (max_leaf_ratio 0.30-0.40 still yields PASS).

---

### D1: Fix garble-ratio full-text tautology and flatten-text separator

**Scope:** Two coupled bugs in helpers.py garble detection: (1) _garble_ratio on full text always returns 1.0 when _tree_is_garbled is True because it evaluates the same checks on the same text -- the windowed ratio path is dead code. (2) _flatten_tree_text joins titles+text with no separator (lines 555-565), creating artificial Arabic-Latin-Arabic glued patterns at node boundaries that cause false garble detection.

**Root Cause:** _garble_ratio (line 1439) re-runs _is_garbled_blob + _has_sparse_mojibake on the full concatenated text -- the same checks _tree_is_garbled already ran. When _tree_is_garbled=True, full_garbled=1.0 and max(1.0, window_ratio)=1.0 always. The windowed ratio (designed for fine-grained measurement) never influences the result. _flatten_tree_text (lines 554-565) concatenates node titles and text with no separator between them, creating boundary artifacts.

**Rationale:** The SLA doc sits at the 2% sparse-mojibake threshold boundary. Because _flatten_tree_text concatenates without separators, legitimate Arabic node titles glue onto Latin characters from adjacent nodes, creating artificial mixed-script patterns that trip _has_sparse_mojibake. The full-text tautology then locks garble_ratio to 1.0 instead of the fine-grained windowed measurement, causing non-deterministic PASS/MARGINAL flips between runs on a clean document.

**Affected Documents:**
- اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf

**Files / Functions:**
- `src/pageindex_mcp/helpers.py: _garble_ratio() (line 1439), _flatten_tree_text() (lines 554-565), classify_verdict() (lines 1559, 1572)`

**Fix:** Remove the full-text binary check from _garble_ratio so it ONLY computes the windowed ratio. _tree_is_garbled already gates entry into the garble-ratio path in classify_verdict, making the full-text check redundant and harmful. Additionally, insert a newline separator between concatenated title/text parts in _flatten_tree_text to prevent artificial glued-script patterns.

**Effort:** Small (~20 lines changed, 1-2 hours). Removing dead code path and adding a separator.

**Test Strategy:** Unit test: build a synthetic tree with Arabic title nodes adjacent to Latin text nodes, verify _flatten_tree_text produces newline-separated output (matching the fix's newline separator). Unit test: verify _garble_ratio returns the windowed ratio (not 1.0) when individual windows have varying garble levels. Regression test: re-score the SLA doc and verify stable MARGINAL or PASS verdict across multiple runs (no flipping).

---

### D2: Arabic single-letter fragment detection and bidi coherence enforcement

**Scope:** Two gaps in Arabic garble detection: (1) _is_garbled_blob has no heuristic for single-letter Arabic fragment decomposition. (2) _check_bidi_coherence (RFC-030 D5) runs in audit-only mode (BIDI_COHERENCE_ENFORCE=false). Documents with 79% garbled nodes store PASS verdicts.

**Root Cause:** _is_garbled_blob (line 863) checks for PUA characters and specific mojibake patterns but has no heuristic for single-letter Arabic fragment decomposition (Arabic words decomposed into individual letters with spaces). _check_bidi_coherence was wired into validate_tree by RFC-030 D5 but defaults to audit-only mode (BIDI_COHERENCE_ENFORCE=false at line 1288), logging warnings without affecting the stored verdict.

**Rationale:** The governance policy doc has 79% of nodes containing Arabic single-letter fragment garbling (each glyph stored as a separate character with inter-character spaces -- a common PDF text-layer extraction failure). Neither the PUA-based garble check nor the bidi coherence audit catches this pattern because: (a) single-letter fragments are non-PUA valid Arabic code points, and (b) bidi coherence enforcement is disabled. The stored verdict is PASS while the audit verdict is FAIL.

**Affected Documents:**
- سياسة حوكمة و إدارة البيانات - Copy.pdf (primary target: stored PASS, audit FAIL, 79% garbled nodes)
- حقوق الإنسان (blast-radius: 347 nodes / 394,717 chars, stored PASS, has bidi-reversed node titles تايوتحملا / ةصالخلا -- enforcement will cap verdict at MARGINAL via bidi_degraded flag, but will NOT cause ingestion failure since enforcement is verdict-only)

**Files / Functions:**
- `src/pageindex_mcp/helpers.py: _is_garbled_blob() (line 863), _check_bidi_coherence() / validate_tree() (line 1288), classify_verdict() (bidi_degraded flag consumer), _garble_check_nodes()`

**Fix:** (a) Add a single-letter Arabic fragment detection heuristic to _is_garbled_blob: when Arabic-script characters are present and >40% of whitespace-delimited tokens containing Arabic chars are single characters (excluding the conjunction particle 'wa'), flag as garbled. (b) Promote BIDI_COHERENCE_ENFORCE to enforced (default true) with a **verdict-only gate**: enforcement affects the verdict returned by classify_verdict but does NOT gate persistence via validate_tree's LowQualityTreeError. This prevents currently-PASS documents (e.g. حقوق الإنسان with 347 nodes / 394,717 chars and known bidi-reversed titles like تايوتحملا, ةصالخلا) from becoming ingestion failures. The enforcement check in validate_tree (~line 1288) must be changed from raising LowQualityTreeError to setting a `bidi_degraded` flag that classify_verdict reads to cap the verdict at MARGINAL. Acceptance threshold for full persistence-gating promotion: <2% false-positive rate across a full corpus cycle, measured from the `bidi_coherence_violations` counter in the Run 15+ audit logs (written to `audit/CORPUS_REINGESTION_AUDIT_RUN-*.md` scorecard rows). (c) Add the single-letter-fragment check to _garble_check_nodes per-node inspection for the per-node garble ratio gate.

**Effort:** Medium (~60-80 lines, 3-4 hours). New detection heuristic plus careful threshold tuning to avoid false positives on legitimate single-character Arabic particles.

**Test Strategy:** Unit test: construct text with Arabic single-letter fragments (e.g. 'م ا د ة' instead of 'مادة'), verify _is_garbled_blob returns True. Unit test: verify the conjunction particle 'wa' exclusion does not inflate the fragment ratio. Integration test: re-score the governance policy doc with enforcement enabled, verify verdict changes from PASS to FAIL/MARGINAL. Negative test: verify clean Arabic docs (e.g. مرسوم 13, مرسوم 33) do not false-trigger the fragment detector.

---

### D3: Add retry logic to MinIO read path in ingest+score pipeline

**Scope:** minio_helper.py cmd_meta and cmd_tree each make a single get_object call with no retry. Transient MinIO read failures produce permanent ERROR verdicts despite artifacts existing. Add exponential-backoff retry (3 attempts) to minio_helper.py read commands and add retry instruction to corpus-ingest-score Stage 2 agent prompt.

**Root Cause:** minio_helper.py cmd_meta (line 36) and cmd_tree (line 41) each attempt exactly one get_object call with no retry, and the Stage 2 agent prompt (corpus-ingest-score.js lines 242-271) contains no instruction to retry on failure. When a transient MinIO read failure (NoSuchKey, ConnectionError, network timeout) occurs between Stage 1 completion and Stage 2 fetch, the document gets a permanent ERROR verdict.

**Rationale:** The converter child writes synchronously (save_doc at client.py:1787, then save_doc_meta at client.py:1820 via put_object) before emitting its success JSON on stdout, so the write IS committed before Stage 1 reports success. The gap is purely in the read path: a single-attempt read with no retry. This is not a recent regression -- the design gap has existed since the incremental pipeline was introduced but only manifested when a transient read failure hit the القرار التنظيمي document this run.

**Affected Documents:**
- القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf

**Files / Functions:**
- `scripts/minio_helper.py: cmd_meta() (line 36), cmd_tree() (line 41)`
- `.claude/workflows/corpus-ingest-score.js: Stage 2 agent prompt (lines 242-271)`

**Fix:** Add retry-with-backoff (3 attempts, 2s/4s/8s delays) to minio_helper.py cmd_meta and cmd_tree for transient S3 errors (NoSuchKey, ConnectionError, network timeouts). Add a retry instruction to the Stage 2 agent prompt: 'If minio_helper.py returns NoSuchKey, wait 5 seconds and retry up to 3 times before concluding the artifacts are missing.'

**Effort:** Small (~25 lines, 1 hour). Standard retry wrapper around existing get_object calls.

**Test Strategy:** Unit test: mock get_object to raise NoSuchKey on first two calls, succeed on third -- verify cmd_meta returns valid JSON. Unit test: mock get_object to raise on all 3 attempts -- verify clean error message (not silent swallow). Integration test: verify the القرار التنظيمي doc scores successfully on re-run (artifacts confirmed to exist in MinIO at publish time).

---

### D4: Extend _ARTICLE_RE to match parenthesized article numbering

**Scope:** _ARTICLE_RE in converters.py:226 requires 'Article \d+' but UAE/English legal docs use 'Article (N)'. Extend the regex to accept both forms so _segment_label and _containment_depths can assign proper depth to these headings.

**Root Cause:** _ARTICLE_RE at converters.py:226 is compiled as r'^(?:Art(?:icle|\.)\s+\d+|§\s*\d+)' which requires digits immediately after a space. 'Article (47)' does not match because '(' appears before the digit. _segment_label (line 298) returns [], _containment_depths (line 360) returns None, and _relevel_by_containment (line 384) no-ops, leaving all Article headings flat.

**Rationale:** Two documents (Federal Decree-Law No. (47) and cabinet_resolution_no_96) use parenthesized article numbering. _segment_label returns [] for these headings, _containment_depths returns None, and _relevel_by_containment leaves all Article headings at their original flat level. The _OVERSIZED_ORDINAL_RE in helpers.py:1798 handles parens but only fires for nodes >50k chars -- it cannot help when md_to_tree already produced many small flat nodes.

**Affected Documents:**
- Federal Decree-Law No. (47) of 2021 - Copy.pdf
- cabinet_resolution_no_96_of_2023_regarding_an_alternative_end_of_service_benefits_system - Copy.pdf

**Files / Functions:**
- `src/pageindex_mcp/converters.py: _ARTICLE_RE (line 226), _segment_label() (line 298), _containment_depths() (line 360), _relevel_by_containment() (line 384)`

**Fix:** Widen _ARTICLE_RE to accept optional parentheses: r'^(?:Art(?:icle|\.)\s+\(?\s*\d+|§\s*\(?\s*\d+)'. This makes _segment_label extract the number from 'Article (47)' as label ['47'], enabling containment-based depth assignment.

**Effort:** Small (~5 lines, 30 minutes). Single regex change with immediate downstream effect.

**Test Strategy:** Unit test: verify _segment_label('Article (47) - Title') returns ['47']. Unit test: verify _segment_label('Article 47 - Title') still returns ['47'] (no regression). Integration test: re-ingest Federal Decree-Law (47) and verify depth increases from 2 to 3+ with Article nodes properly nested.

---

### D5: Add German clause-pattern heading injection (Ziffer/Ziff.)

**Scope:** German AHB/insurance documents use Ziffer N / Ziff. N clause numbering that Docling does not detect as headings. Implement _inject_german_structural_headings analogous to _inject_arabic_structural_headings to promote these prose patterns to headings. Also extend to English Article (N) patterns when Docling misses them entirely.

**Root Cause:** Docling does not detect 'Ziffer N' / 'Ziff. N' prose patterns as section headers. There is no _inject_german_structural_headings function analogous to _inject_arabic_structural_headings (converters.py:98, called at line 2759). These prose lines stay as plain text and the relevel chain cannot promote them.

**Rationale:** The heading-depth recovery chain (_relevel_by_containment/_relevel_by_numbering/_relevel_by_outline) can only re-level EXISTING headings, not create new ones from prose. Without heading injection, German clause-numbered documents remain at depth 2 (too shallow for 16-page legal conditions). The Arabic injection pattern (_inject_arabic_structural_headings at converters.py:2759-2760) already demonstrates the correct approach.

**Affected Documents:**
- Haftpflicht-Allgemeine-Bedingungen.pdf.pdf

**Files / Functions:**
- `src/pageindex_mcp/converters.py: new _inject_german_clause_headings(), new _inject_english_article_headings(), call site at pdf_to_markdown_docling() (line 2759-2760)`

**Fix:** Add _inject_german_clause_headings (regex for 'Ziffer N' / 'Ziff. N' at line start, promote to ## heading) and _inject_english_article_headings (for 'Article (N)' at line start when Docling missed them). Call both alongside the Arabic injection at converters.py:2759-2760 inside pdf_to_markdown_docling.

**Effort:** Medium (~50-70 lines, 2-3 hours). Two new injection functions following the established _inject_arabic_structural_headings pattern.

**Test Strategy:** Unit test: pass markdown with 'Ziffer 1 Haftung' as prose line, verify output has '## Ziffer 1 Haftung'. Unit test: pass markdown with 'Article (3) Definitions' as prose line, verify output has '## Article (3) Definitions'. Negative test: verify lines like 'see Ziffer 1 above' mid-sentence are NOT promoted. Integration test: re-ingest Haftpflicht-Allgemeine and verify depth increases from 2 to 3+.

---

### D6: Call _segment_table_nodes on primary tree-build path

**Scope:** _segment_table_nodes is called only on garble-recovery paths (client.py:1126, 1312) but not on the primary tree-build path (client.py:1031). Table-heavy docs traversing the primary path without garble issues get unsegmented flat table leaves.

**Root Cause:** _segment_table_nodes is called at client.py:1126 and 1312 (garble-recovery paths) but not at client.py:1031 (primary tree-build path, after split_oversized_leaf_nodes). Documents that pass garble checks cleanly never get their tables segmented.

**Rationale:** GHV-TKV-Tarif is a pricing table document that traverses the primary path without garble issues, so _segment_table_nodes never fires, leaving the multi-column table as a single flat leaf (4 nodes, leaf_concentration=0.65, stored MARGINAL). RFC-030 explicitly identified and DEFERRED this fix; this RFC picks it up.

**Affected Documents:**
- GHV-TKV-Tarif.pdf

**Files / Functions:**
- `src/pageindex_mcp/client.py: primary tree-build path (line 1031), image-escalation path (line 1428)`

**Fix:** Add result['structure'] = _segment_table_nodes(result.get('structure', [])) after line 1031 in client.py and line 1428 in the image-escalation path. Run _segment_table_nodes BEFORE validate_tree so the segmented structure is what gets validated.

**Effort:** Small (~10 lines, 1 hour). Adding existing function call to additional code paths.

**Test Strategy:** Unit test: build a tree with a single large TABLE node, verify _segment_table_nodes splits it into per-section sub-nodes. Integration test: re-ingest GHV-TKV-Tarif and verify node count increases from 4 and leaf_concentration drops below 0.65. Regression test: verify documents already on garble-recovery paths (where _segment_table_nodes already runs) produce identical output.

---

### D7: Implement RFC-022 B2 Part A: image_standalone content_class override

**Scope:** RFC-022 B2 Part A (extension-based content_class='image_standalone' override for _IMAGE_EXTS files) was marked complete but never implemented. Bare .jpg files get classified as flat_prose/flat_mixed instead of routing through _classify_image_verdict.

**Root Cause:** RFC-022 B2 Part A was marked complete in the task tracker but never implemented in client.py. The all(b.get('role')=='image') check at client.py:1606 fails for .jpg files because OCR text creates prose blocks (not all role='image'). The extension-based override that was supposed to force content_class='image_standalone' for _IMAGE_EXTS files was never added.

**Rationale:** The pie chart .jpg file goes through route_and_extract_flat which classifies it as flat_mixed (OCR text creates prose blocks, not all role='image'). Without image_standalone content_class, classify_verdict hits the flat_prose promotion gate where 489 chars < 500 MIN_IMAGE_PROMOTED_CHARS char floor, yielding MARGINAL instead of routing through _classify_image_verdict (which has no char floor and would return PASS given ratio=1.0).

**Affected Documents:**
- image pie chart about labor distribution in january 2025 - Copy.jpg

**Files / Functions:**
- `src/pageindex_mcp/client.py: after line 1608 (existing all-blocks-are-image check), add extension-based override`

**Fix:** Add extension-based content_class override in client.py after line 1608: when ext in _IMAGE_EXTS and _IMAGE_STANDALONE_PIPELINE_ENABLED, force content_class='image_standalone' regardless of what route_and_extract_flat returned. With this fix, classify_verdict routes through _classify_image_verdict(image_enrichment_ratio) at helpers.py:1522 which returns PASS when ratio>=0.8 (the pie chart has ratio=1.0).

**Effort:** Small (~10 lines, 30 minutes). Straightforward conditional assignment implementing an already-designed feature.

**Test Strategy:** Unit test: mock a .jpg file path with flat_mixed content_class, verify the override sets content_class='image_standalone'. Integration test: re-ingest the pie chart .jpg and verify verdict changes from MARGINAL (image_enrichment_promoted_below_char_floor) to PASS via _classify_image_verdict. Negative test: verify .pdf files with mixed image/text blocks are NOT overridden to image_standalone.

---

### D8: Harden Arabic OCR tree-building against Tesseract RTL-reversed text

**Scope:** Scanned Arabic PDFs where Tesseract produces mirror-reversed text (e.g. ةداملا instead of المادة) defeat the forward-oriented Arabic stem regexes in numbering_depth/_relevel_by_containment. The document falls to the flat path (FLAT-03-C1 design, intentional) but loses hierarchical structure.

**Root Cause:** Tesseract RTL-direction bug produces mirror-reversed Arabic on some scanned inputs depending on page layout/skew. Heading lines read e.g. '# (1) ةداملا' instead of '# المادة (1)'. The forward-oriented regexes _AR_PART_RE, _AR_ARTICLE_RE, _AR_WORD_RE (converters.py ~lines 155-214) used by numbering_depth() and _relevel_by_containment cannot match reversed text. validate_tree's RTL-reversal detector (helpers.py ~1279) does not fire cleanly on this noisy/partially-reversed OCR output.

**Rationale:** The scanned قرار مجلس الوزراء رقم (1) document has NO embedded text layer. OCR escalation fires correctly and recovers 38,342 chars with 146 heading lines after _inject_arabic_structural_headings runs. However, the OCR text is Tesseract-mirror-reversed Arabic. The forward-oriented Arabic stem regexes (_AR_PART_RE / _AR_ARTICLE_RE / _AR_WORD_RE at converters.py ~lines 155-214) cannot match reversed text, so the tree collapses to depth<2 and falls to the flat path. The flat path separately recovers 56 clean, correctly-oriented title blocks -- indicating the flat-path OCR source is strictly better for this document.

**Affected Documents:**
- قرار مجلس الوزراء رقم (1) لسنة 2022 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل.pdf

**Files / Functions:**
- `src/pageindex_mcp/converters.py: _AR_PART_RE, _AR_ARTICLE_RE, _AR_WORD_RE (~lines 155-214), numbering_depth(), _relevel_by_containment(), _inject_arabic_structural_headings()`
- `src/pageindex_mcp/client.py: OCR escalation path (~lines 1370-1425), FLAT-03-C1 design (lines 1478-1484)`
- `src/pageindex_mcp/helpers.py: validate_tree RTL-reversal detector (~line 1279)`

**Fix:** Two complementary fixes: (1) Add reversed-pattern variants to Arabic regex matching: extend _AR_PART_RE / _AR_ARTICLE_RE / _AR_WORD_RE to also match reversed variants of مادة/باب/فصل (cheap, catches this exact failure mode). (2) Add a per-line or per-block character-reversal detection function that checks against a known-good Arabic word list, and when reversal is detected, flip the text before feeding into _inject_arabic_structural_headings. Lower priority: compare tree-path OCR vs flat-path OCR quality and reuse whichever source proves not reversed.

**Effort:** Large (~100-120 lines, 4-6 hours). Reversal detection heuristic, reversed regex variants, and integration with the OCR escalation path. Requires careful testing against both reversed and non-reversed Arabic inputs.

**Test Strategy:** Unit test: verify reversed regex variants match 'ةداملا' as equivalent to 'المادة'. Unit test: verify reversal detection correctly identifies mirror-reversed Arabic text and returns the corrected form. Integration test: re-ingest the scanned قرار مجلس الوزراء رقم (1) doc and verify tree depth increases from 1 to 3+. Negative test: verify non-reversed Arabic documents (e.g. مرسوم 13, مرسوم 33) are not affected by the reversal detection (no false positives).


## Implementation Plan

| Batch | Decisions | Rationale |
|-------|-----------|-----------|
| 0 | D0, D1, D3, D4 | Independent small-complexity fixes with no cross-dependencies: hysteresis wiring (D0), garble-ratio tautology (D1), MinIO retry (D3), and article regex (D4). All are bug fixes that can be implemented and tested in parallel. |
| 1 | D6, D7 | Small-complexity fixes in client.py that benefit from Batch 0 being stable first. D6 (table segmentation on primary path) and D7 (image_standalone override) both modify the tree-build/verdict flow in client.py. While D1 modifies helpers.py (not client.py), the verdict-flow changes in D6/D7 are easier to validate after D1's garble-ratio fix is in place, since D1 affects which documents reach the primary path vs garble-recovery paths that D6 depends on. |
| 2 | D2, D5 | Medium-complexity feature additions: Arabic fragment detection + bidi enforcement (D2) depends on D1 (garble-ratio fix) being correct first. German heading injection (D5) is independent but medium complexity -- batching with D2 keeps batch sizes manageable. |
| 3 | D8 | Medium-complexity RTL-reversal hardening depends on D2 (Arabic garble detection improvements) being in place to avoid duplicate or conflicting Arabic text detection logic. |

## Test Strategy

| Decision | Title | Test Approach |
|----------|-------|---------------|
| D0 | Wire hysteresis snapshot into corpus reingestion pipeline | Unit test: call wipe_processed(), verify `snapshots/_prior_verdicts.json` exists in MinIO AFTER `processed/*` is deleted (snapshot survives wipe). Unit test: verify find_prior_verdict() reads from `snapshots/_prior_verdicts.json` and returns the correct prior verdict. Integration test: ingest a doc with PASS verdict, run wipe_processed(), re-ingest the same doc, verify find_prior_verdict() returns the prior PASS and the hysteresis band activates (max_leaf_ratio 0.30-0.40 still yields PASS). |
| D1 | Fix garble-ratio full-text tautology and flatten-text separator | Unit test: build a synthetic tree with Arabic title nodes adjacent to Latin text nodes, verify _flatten_tree_text produces newline-separated output (matching the fix's newline separator). Unit test: verify _garble_ratio returns the windowed ratio (not 1.0) when individual windows have varying garble levels. Regression test: re-score the SLA doc and verify stable MARGINAL or PASS verdict across multiple runs (no flipping). |
| D2 | Arabic single-letter fragment detection and bidi coherence enforcement | Unit test: construct text with Arabic single-letter fragments (e.g. 'م ا د ة' instead of 'مادة'), verify _is_garbled_blob returns True. Unit test: verify the conjunction particle 'wa' exclusion does not inflate the fragment ratio. Integration test: re-score the governance policy doc with enforcement enabled, verify verdict changes from PASS to FAIL/MARGINAL. Negative test: verify clean Arabic docs (e.g. مرسوم 13, مرسوم 33) do not false-trigger the fragment detector. Blast-radius test: verify حقوق الإنسان (known bidi-reversed titles) is capped at MARGINAL by bidi_degraded flag but does NOT raise LowQualityTreeError (tree is still persisted). |
| D3 | Add retry logic to MinIO read path in ingest+score pipeline | Unit test: mock get_object to raise NoSuchKey on first two calls, succeed on third -- verify cmd_meta returns valid JSON. Unit test: mock get_object to raise on all 3 attempts -- verify clean error message (not silent swallow). Integration test: verify the القرار التنظيمي doc scores successfully on re-run (artifacts confirmed to exist in MinIO at publish time). |
| D4 | Extend _ARTICLE_RE to match parenthesized article numbering | Unit test: verify _segment_label('Article (47) - Title') returns ['47']. Unit test: verify _segment_label('Article 47 - Title') still returns ['47'] (no regression). Integration test: re-ingest Federal Decree-Law (47) and verify depth increases from 2 to 3+ with Article nodes properly nested. |
| D5 | Add German clause-pattern heading injection (Ziffer/Ziff.) | Unit test: pass markdown with 'Ziffer 1 Haftung' as prose line, verify output has '## Ziffer 1 Haftung'. Unit test: pass markdown with 'Article (3) Definitions' as prose line, verify output has '## Article (3) Definitions'. Negative test: verify lines like 'see Ziffer 1 above' mid-sentence are NOT promoted. Integration test: re-ingest Haftpflicht-Allgemeine and verify depth increases from 2 to 3+. |
| D6 | Call _segment_table_nodes on primary tree-build path | Unit test: build a tree with a single large TABLE node, verify _segment_table_nodes splits it into per-section sub-nodes. Integration test: re-ingest GHV-TKV-Tarif and verify node count increases from 4 and leaf_concentration drops below 0.65. Regression test: verify documents already on garble-recovery paths (where _segment_table_nodes already runs) produce identical output. |
| D7 | Implement RFC-022 B2 Part A: image_standalone content_class override | Unit test: mock a .jpg file path with flat_mixed content_class, verify the override sets content_class='image_standalone'. Integration test: re-ingest the pie chart .jpg and verify verdict changes from MARGINAL (image_enrichment_promoted_below_char_floor) to PASS via _classify_image_verdict. Negative test: verify .pdf files with mixed image/text blocks are NOT overridden to image_standalone. |
| D8 | Harden Arabic OCR tree-building against Tesseract RTL-reversed text | Unit test: verify reversed regex variants match 'ةداملا' as equivalent to 'المادة'. Unit test: verify reversal detection correctly identifies mirror-reversed Arabic text and returns the corrected form. Integration test: re-ingest the scanned قرار مجلس الوزراء رقم (1) doc and verify tree depth increases from 1 to 3+. Negative test: verify non-reversed Arabic documents (e.g. مرسوم 13, مرسوم 33) are not affected by the reversal detection (no false positives). |

## Risks

- D2 (bidi coherence enforcement promotion) risks false positives on legitimate mixed-script Arabic documents if the single-letter fragment threshold is too aggressive. The SLA doc (D1) is already a false-positive victim of mixed-script detection -- adding another Arabic-specific detector without careful threshold tuning could widen false positives. Mitigation: tune against the full 25-doc corpus before enabling enforcement.
- D2 blast radius on currently-PASS documents: حقوق الإنسان (347 nodes, 394,717 chars, stored PASS) has audit-verified bidi-reversed node titles (تايوتحملا, ةصالخلا). Enforcement in validate_tree (~line 1288) gates persistence via LowQualityTreeError, not just verdicts. To prevent currently-persisted PASS documents from becoming ingestion failures, D2 implements enforcement as verdict-only (bidi_degraded flag caps verdict at MARGINAL) rather than persistence-gating. Full persistence-gating promotion requires <2% false-positive rate measured across a complete corpus cycle. The acceptance threshold and measurement location are specified in D2's Fix section.
- D5 (German heading injection) risks over-promoting mid-sentence references like 'see Ziffer 1 above' to headings. The Arabic injection function already handles this concern with line-start anchoring, but German prose patterns may differ. Mitigation: require the pattern to appear at the start of a line/paragraph, not mid-sentence.
- D6 (table segmentation on primary path) was explicitly deferred by RFC-030 -- there may have been an unstated reason for deferral (e.g. validate_tree ordering concerns or edge cases in table-heavy docs). Mitigation: run _segment_table_nodes BEFORE validate_tree and test against the full corpus to detect any ordering-dependent regressions.
- D8 (Arabic RTL reversal hardening) is the highest-complexity item and the reversal detection heuristic may be fragile across different Tesseract versions, page layouts, and Arabic dialects. The divergence between tree-path and flat-path OCR quality for the same document suggests a deeper non-determinism in the OCR pipeline that reversal detection alone may not fully resolve.
- D0 (hysteresis wiring) depends on the corpus-ingest and corpus-ingest-score skills being the sole entry points for re-ingestion. If a developer runs a manual MinIO wipe outside these skills, the snapshot will still be missed. Mitigation: the wipe_processed() utility makes the snapshot atomic with the wipe, so use it everywhere.
- D1 and D2 both modify garble detection in helpers.py and must be coordinated to avoid conflicting threshold changes. D1 removes the full-text tautology (lowering garble_ratio for some docs) while D2 adds a new detection heuristic (raising it for others). Test both together against the full corpus.

## Out of Scope

- [7] Docling TableFormer empty cell rendering for checkmark/symbol-only cells -- upstream limitation already mitigated by RFC-010 Gap 6b flag_empty_cells(); fixing requires upstream Docling/TableFormer changes (large complexity)
- [8] Chart-image OCR fragmented numeric labels -- persistent limitation of OCR-to-flat-block conversion for chart images requiring chart-data structuring logic (large complexity, research-grade problem)
- [9] Audit char-count accounting gap -- methodology error in audit measurement, not a code bug; flat_char_count correctly includes table row_records but audit sum uses block.get('text','') which misses them
- [10a] Content-filename mismatch for ward 597 -- source-file-level data quality issue where the PDF itself contains wrong content; pipeline correctly extracts what is in the file
- [10b] قرار مجلس الوزراء رقم (106) depth-1 flat-tree collapse -- Arabic legal document with embedded text layer (not scanned/OCR) whose headings lack markdown-detectable heading markers (no `#`, no bold patterns, no numbering that matches _AR_ARTICLE_RE). D8 does not apply because قرار 106 has clean extracted text (not Tesseract RTL-reversed). D5's heading injection targets German Ziffer/Ziff. patterns and English Article (N), not Arabic structural patterns without explicit numbering. The existing _inject_arabic_structural_headings (converters.py:2759) already fires but the document's heading patterns do not match the current Arabic stem regexes. Fixing this requires a new Arabic heading-discovery heuristic based on line-length/position analysis or Arabic NLP-based section detection -- research-grade complexity beyond this RFC's scope. The document holds MARGINAL with clean content extraction; the deficit is structural depth only.
