<!-- Space: CITRA -->
<!-- Title: RFC-029: Run 12 Arabic garble-gate fixes, thin-tree density gate, and extraction quality improvements -->
<!-- Folder: RFCs -->

# RFC-029: Run 12 Arabic garble-gate fixes, thin-tree density gate, and extraction quality improvements

**Run:** 12
**Status:** Draft

## Traceability

| Artifact | Reference |
|---|---|
| Design Document | [design-rfc029-run12-arabic-garble-gates-and-extraction-quality.md](../designs/design-rfc029-run12-arabic-garble-gates-and-extraction-quality.md) |
| Implementation Plan | [tasks-rfc029-run12-arabic-garble-gates-and-extraction-quality.md](../tasks/tasks-rfc029-run12-arabic-garble-gates-and-extraction-quality.md) |
| Audit | [CORPUS_REINGESTION_AUDIT_RUN-12.md](../../audit/CORPUS_REINGESTION_AUDIT_RUN-12.md) |
| Hard Rules (binding) | [CLAUDE.md § Hard Rules](../../CLAUDE.md#hard-rules) |

## Summary

Run 12 audited all 25 corpus documents and produced 10 PASS, 10 MARGINAL, 4 FAIL, and 1 ERROR. Five docs improved from Run 11 (three MARGINAL-to-PASS judge convergences, one FAIL-to-PASS judge convergence, one ERROR-to-MARGINAL where the world-stats-pocketbook now processes but with 10x char inflation). Two docs showed structural metric improvement but remained FAIL (warid-597 gained 54k chars of junk OCR; al-iqtisad gained 91 nodes but 53% are cross-document contamination). Four regressions occurred: the Human Rights PDF went from FAIL (328 nodes/498k chars artifact existed) to ERROR (no artifact at all -- NFKC garble-gate false positive); warid-597 stored verdict regressed from MARGINAL garbling to PASS; cabinet_resolution_no_21 regressed PASS-to-MARGINAL and uae_numbers_landscape regressed MARGINAL-to-FAIL (both judge-side non-determinism, not pipeline changes). The remaining docs stalled at their Run 11 verdicts with unchanged or near-identical metrics.

## Decisions

### D0: NFKC-normalize Arabic Presentation Forms before garble check

**Scope:** Arabic Presentation Forms characters (U+FB50-FDFF, U+FE70-FEFF) in the Human Rights PDF trigger the D2 garble check (>50% PF ratio). validate_tree rejects, OCR retry fails to improve, LowQualityTreeError is raised with no flat fallback. Affects 1 doc: huquq al-insan (Human Rights - Copy.pdf), which regressed from FAIL (328 nodes/498k chars artifact present) to ERROR (no artifact at all).

**Root Cause:** RFC-028 D2 added _is_garbled_blob (helpers.py:880-895) with a >50% Presentation Forms ratio threshold. The Human Rights PDF text layer uses PF characters (6/10 Arabic chars are PF). validate_tree rejects with reason=garbling. The OCR retry path (client.py:991-1082) runs force_full_page_ocr but produces fewer chars than the original, so the D4 best-of-two comparison restores the garbled-flagged original. reason=garbling is explicitly excluded from flat routing (FLAT-03-C2, client.py:1286), so LowQualityTreeError is raised and the worker maps it to terminal reason=low_quality_tree with no artifacts saved.

**Rationale:** This is the only pure regression to ERROR in Run 12. The document previously had a usable 498k-char artifact; now it has nothing. The root cause is that RFC-028 D2 added a presentation-forms garble check (helpers.py:883-896, comment: "font-encoded garble emits positional glyph variants") that correctly identifies PF-heavy text as suspicious, but the current response (unconditional rejection) is too coarse. Some Arabic PDF renderers emit PF characters as their native encoding, not as garble. NFKC normalization is the correct first treatment: it maps positional PF variants to their canonical Arabic equivalents (U+0600-06FF range). However, NFKC does NOT reorder visual-order text -- if the PDF emits glyphs in visual (display) order rather than logical order, NFKC will produce canonically-encoded but character-reversed Arabic. Therefore NFKC normalization must be paired with a post-normalization bidi-coherence check that detects reversed Arabic sequences.

**Affected Documents:**
- huquq al-insan - Copy.pdf (ERROR)

**Files / Functions:**
- `src/pageindex_mcp/converters.py :: _pre_inference_normalize -- add NFKC normalization for PF ranges`
- `src/pageindex_mcp/helpers.py :: _is_garbled_blob (lines 880-895) -- PF ratio check becomes moot after normalization`
- `src/pageindex_mcp/client.py :: index() garbling recovery path (lines 991-1082, 1286, 1538)`

**Fix:** Two-part fix: (1) Add a NFKC normalization step in _pre_inference_normalize (converters.py) that runs unicodedata.normalize('NFKC', text) to map Arabic Presentation Forms characters (U+FB50-FDFF, U+FE70-FEFF) to their canonical equivalents before the garble check runs. The normalization should run early in the pipeline, before md_to_tree and validate_tree, so that all downstream consumers see canonical Arabic. (2) Add a post-NFKC bidi-coherence check: sample 3-5 multi-word Arabic runs from the normalized output, verify they read right-to-left at the word level (check that Arabic word boundaries follow RTL logical order, not LTR visual order). If >50% of sampled runs fail the coherence check, flag as `reason=visual_order_garble` and route to OCR retry, not flat fallback. This prevents NFKC from laundering reversed visual-order Arabic past the gate.

**Effort:** Medium (~4-5 hours). Two-part fix: NFKC normalization is a well-defined Unicode operation (~1-2h), but the bidi-coherence check requires Arabic word-boundary detection and RTL-order validation (~2-3h) plus testing against all 8 Arabic corpus docs.

**Test Strategy:** Unit test: craft a string with >50% Arabic PF characters, verify _pre_inference_normalize maps them to canonical forms and _is_garbled_blob no longer flags the result. Unit test: craft a visual-order (reversed) Arabic string after NFKC normalization, verify the bidi-coherence check detects it and flags visual_order_garble. Integration test: re-ingest huquq al-insan and verify it produces a non-ERROR artifact (the primary goal is recovery from ERROR to a scoreable artifact; the Run 11 artifact at 328 nodes / ~498k chars was itself judged FAIL, so matching those metrics is a floor, not a success criterion -- content quality improvement for this document is tracked separately and may require follow-on work beyond NFKC normalization). Regression test: verify that genuinely garbled text (repeating digit sequences, random byte sequences) still triggers the garble check after normalization.

---

### D1: Content-density gate: prefer flat extraction when tree is thin

**Scope:** RFC-028 D1 _inject_arabic_structural_headings injects just enough headings for shallow Arabic docs to clear validate_tree thresholds (>=3 nodes, depth>=2), blocking the richer flat fallback. Affects 3 docs: marsoom 13 (FAIL, 6 nodes/1225 chars vs 75 blocks/5972 chars flat), qerar 1 (MARGINAL, flat depth=1), qerar 106 (MARGINAL, 15.6% fence leakage).

**Root Cause:** _inject_arabic_structural_headings (RFC-028 D1) removed the prev_blank guard and raised the char limit to 100, which injects enough headings for shallow Arabic docs to clear validate_tree thresholds (node_count>=3, depth>=2). This prevents the flat fallback path from triggering. For docs with flat peer numbering (articles 1,2,3 not 1.1,1.2), _relevel_by_containment cannot build deeper hierarchy, and _relevel_by_numbering maps all articles to the same depth level.

**Rationale:** The tree path produces a severely content-lossy result for these Arabic legal documents -- marsoom 13 loses ~80% of content (1225 chars tree vs 5972 chars flat). The injected headings are a workaround that creates just enough structure to pass validation but does not actually recover the document content. The flat path would produce a significantly better result.

**Affected Documents:**
- marsoom 13 (FAIL)
- qerar 1 (MARGINAL)
- qerar 106 (MARGINAL)

**Files / Functions:**
- `src/pageindex_mcp/helpers.py :: validate_tree -- add content-density comparison or chars-per-node floor`
- `src/pageindex_mcp/client.py :: index() tree-vs-flat decision logic -- add flat_char_count > N * tree_char_count preference`
- `src/pageindex_mcp/converters.py :: _inject_arabic_structural_headings -- consider tightening injection criteria`

**Fix:** Add a content-density comparison gate in the tree-vs-flat decision path: after tree passes validate_tree, also run route_and_extract_flat and compare char counts. When flat_char_count > 3x tree_char_count, prefer the flat result. Alternatively (or additionally), add a minimum chars-per-node floor to validate_tree so that a 6-node/1225-char tree (204 chars/node) does not pass when it is demonstrably thin. A floor of ~500 chars/node would catch marsoom 13 without affecting the 10 PASS docs.

**Effort:** Medium (~4-6 hours). Requires careful threshold tuning to avoid flipping currently-PASS docs to flat. Needs both the comparison logic and threshold calibration against all 25 corpus docs.

**Test Strategy:** Unit test: construct a tree with 6 nodes / 1200 chars and a flat extraction with 75 blocks / 6000 chars; verify the density gate prefers flat. Parameterized test across all 25 corpus docs: verify no PASS doc flips to flat under the new gate. Integration test: re-ingest marsoom 13 and verify it routes to flat with ~5972 chars rather than the thin 1225-char tree.

---

### D2: Post-OCR garble dilution: density floor for scanned Arabic PDFs

**Scope:** RFC-028 D5 improved Arabic OCR lang detection produces 54k chars for warid-597, diluting the repeating numeric-junk text layer below garble thresholds. validate_tree passes (111 nodes) and classify_verdict returns PASS on junk content. Stored verdict regressed from Run 11 MARGINAL garbling(ratio=1.00) to PASS.

**Root Cause:** RFC-028 D5 added 'ara' to Tesseract langs for Arabic-named PDFs via detect_ocr_langs union. The OCR retry now produces 54k chars (up from 1.8k with 'eng' model), diluting the digit-ratio from ~100% to <1% and dropping token-repetition far below 30%. All _is_garbled_blob thresholds pass. The D4 keep-best logic (client.py:1062-1081) keeps the 54k-char result over the 1.8k original. validate_tree passes (111 nodes, depth>=2), classify_verdict returns PASS.

**Rationale:** This is a gate regression -- the garble gate was correctly firing in Run 11 and now incorrectly passes. The 54k chars are OCR output over a junk text layer (repeating '1651001429' on every page), producing ~1303 chars/page which is 50% below the expected 2000-4000 chars/page density for a government document. The improved OCR language detection paradoxically removed the garble-gate safety net.

**Affected Documents:**
- warid 597 (FAIL, stored verdict regressed to PASS)

**Files / Functions:**
- `src/pageindex_mcp/helpers.py :: validate_tree -- add chars-per-page density floor`
- `src/pageindex_mcp/helpers.py :: classify_verdict -- add Arabic-content-ratio check for scanned PDFs`
- `src/pageindex_mcp/client.py :: index() D4 keep-best logic (lines 1062-1081) -- add pre/post-retry garble signal comparison`

**Fix:** Add a chars-per-page density floor to validate_tree or classify_verdict for scanned PDFs. When page_count is available and chars_per_page < MIN_SCANNED_DENSITY_FLOOR (e.g. 1500), flag as suspect density. Additionally, add an Arabic content validation heuristic: if the filename contains Arabic but the OCR output's meaningful-Arabic-char ratio is low (dominated by numeric junk and OCR noise), flag as garbled regardless of volume. A third complementary check: in the D4 keep-best logic, if the pre-retry was garbled and the post-retry has similar repeating-token patterns (same tokens just more of them), do not let char-count growth alone override the garble detection.

**Effort:** Medium (~4-6 hours). Requires access to page_count in validate_tree (may need to thread it through), threshold calibration, and careful testing to avoid false positives on legitimately sparse documents.

**Test Strategy:** Unit test: construct a 42-page tree with 54k chars of repeating numeric content; verify density floor flags it. Unit test: verify a 42-page tree with 54k chars of real Arabic content does NOT trigger the floor. Integration test: re-ingest warid-597 and verify stored verdict is MARGINAL or FAIL (not PASS). Regression test: verify all 10 current PASS docs still pass the density floor.

---

### D3: Strip fence markers and HR separators in flat extraction path

**Scope:** route_and_extract_flat has no filtering for markdown fence delimiters (```) and horizontal rules (---/===/***) produced by Docling. These fall through to the prose accumulator as noise blocks (15-20% of blocks in affected docs). Affects 3 docs: MOU MOHRE (MARGINAL, ~20% noise), SLA arabic (MARGINAL, 40/264 blocks are fences, 19 separator-noise blocks), qerar 106 (MARGINAL, 15.6% fence leakage).

**Root Cause:** route_and_extract_flat (helpers.py:2281-2359) has no special handling for markdown fence delimiters or horizontal rule separators. These Docling-produced formatting markers fall through to the prose accumulator (line 2357-2359) and become noise prose blocks. The _pre_inference_normalize chain runs before md_to_tree but does not strip fences/HRs either.

**Rationale:** This is the simplest fix in the set -- pure noise filtering with no semantic ambiguity. The fence markers and HR separators are Docling markdown formatting artifacts, not document content. Removing them directly improves content quality for all flat-path docs.

**Affected Documents:**
- MOU MOHRE (MARGINAL)
- SLA arabic (MARGINAL)
- qerar 106 (MARGINAL)

**Files / Functions:**
- `src/pageindex_mcp/helpers.py :: route_and_extract_flat (lines 2281-2359) -- add fence/HR skip logic at top of while loop`

**Fix:** Add fence-marker and HR-separator detection at the top of the route_and_extract_flat while loop: (1) skip lines matching ^```.*$ (fence delimiters) and toggle an in_fence boolean state to skip content inside code fences entirely, (2) skip lines matching ^-{3,}$ or ^={3,}$ or ^\*{3,}$ (thematic breaks / HR separators). This is pure filtering -- no structural changes to the extraction logic.

**Effort:** Small (~1-2 hours). Straightforward regex matching and state toggle. Well-bounded scope.

**Test Strategy:** Unit test: feed route_and_extract_flat a markdown string containing ``` fences, --- HRs, and === separators interspersed with real content; verify the output contains only real content blocks with zero fence/HR artifacts. Integration test: re-ingest MOU MOHRE and verify noise block percentage drops from ~20% to <5%. Count test: verify SLA arabic fence block count drops from 40 to 0.

---

### D4: Post-export table deduplication for Docling char inflation

**Scope:** Docling export_to_markdown() duplicates row text across all detected columns for irregular statistical tables and pads cells to widest-cell width, causing ~10x char inflation (world-stats-pocketbook: 9.4M chars, 80% whitespace). Affects 1 doc: world-stats-pocketbook-2023.pdf (MARGINAL).

**Root Cause:** Docling's export_to_markdown() table renderer has two behaviors for irregular statistical tables: (a) it assigns the SAME row text to every detected column (verified: 'Finland' node has rows like 'Fertility rate... 1.9 1.6 1.4' repeated byte-identically 5-8x across pipe-delimited columns), and (b) it pads every cell to the widest cell's width for GFM alignment (median whitespace run ~1031 chars/cell, 392 such runs in Finland's table alone). The chunked-Docling path simply concatenates chunk markdowns unchanged -- no duplication is introduced by pageindex code. The 19/20 unenriched image markers are a separate issue: those are legitimately decorative graphics (UN emblem, icons) with no text content, not a splice guard failure.

**Rationale:** The 10x char inflation is the single largest metric anomaly in the corpus. While the underlying data is present (non-whitespace content is ~1.89M chars / ~6.5k chars/page, close to expected), the inflated artifact wastes storage, degrades search relevance, and inflates token counts for any downstream LLM consumer. The fix is purely cosmetic -- collapsing duplicate columns and stripping padding does not alter the actual content.

**Affected Documents:**
- world-stats-pocketbook-2023.pdf (MARGINAL)

**Files / Functions:**
- `src/pageindex_mcp/converters.py :: add post-export table-repair pass after export_to_markdown() calls (lines ~2460, ~2525, ~2822)`
- `src/pageindex_mcp/converters.py :: _pdf_to_markdown_docling_chunked (lines 2289-2375) -- integrate repair pass`

**Fix:** Add a post-export table-repair pass that runs after every Docling export_to_markdown() call. The pass: (1) detects degenerate pipe-table rows where all column cells are byte-identical and collapses them to a single cell, (2) re-emits tables with minimal single-space column padding instead of Docling's GFM-aligned padding (strip trailing whitespace in each cell). This is a pure rendering-format fix -- no data is lost or altered, only duplicate columns are collapsed and whitespace padding is removed.

**Effort:** Medium (~4-5 hours). Requires pipe-table parsing, byte-identical column detection, and re-emission. Must handle edge cases like tables with intentionally identical columns (unlikely but possible). Needs validation across the full corpus to ensure no table content is lost.

**Test Strategy:** Unit test: construct a pipe-table with 5 byte-identical columns and 1000-char padding per cell; verify repair pass collapses to 1 column with minimal padding. Unit test: construct a pipe-table with legitimately different columns; verify repair pass leaves it unchanged. Integration test: re-ingest world-stats-pocketbook and verify total char count drops from ~9.4M to ~2M (closer to expected ~3k chars/page * 292 pages). Regression test: re-ingest GHV-TKV-Tarif (contains real tables) and verify no content loss.

---

### D5: Retain chart image context when picture skip-gates fire

**Scope:** Three related chart/image enrichment gaps: (a) _recover_picture_text skip gates produce empty PictureResults and splice_figure_markers strips the marker entirely, (b) standalone JPG route leaves ocr_text empty when Docling text exceeds MIN_STANDALONE_IMAGE_MD_CHARS, (c) Docling Heron RT-DETRv2 does not classify vector charts as PictureItem. Affects 3 docs: uae_numbers_portrait (MARGINAL), image pie chart JPG (MARGINAL), uae_numbers_landscape (FAIL).

**Root Cause:** (a) _recover_picture_text skip gates (clip_text_already_exported at converters.py:1881, page_coverage at converters.py:1849-1874) produce empty PictureResults. splice_figure_markers strips the <!-- image --> marker entirely (STRIP_SKIPPED_IMAGE_MARKERS=true default), so no indexed image block is created. (b) For standalone JPGs, client.py:928-930 (D8a gate) skips Tesseract OCR when Docling md_content > 100 chars, creating a synthetic PictureResult with ocr_text=''. The Docling-extracted text ends up in separate prose blocks, not linked to the image block. (c) Docling's Heron RT-DETRv2 layout model does not classify vector charts/infographics as PictureItem, so no <!-- image --> markers are emitted and the entire picture enrichment pipeline never runs.

**Rationale:** These three gaps share a common theme: chart content is lost because the picture enrichment pipeline either skips regions it should retain context for, or fails to detect chart regions at all. The landscape doc is the worst case -- 748 chars with zero image markers for a 2-page chart document. The fixes are layered: (a) and (b) are code-level plumbing fixes in the existing pipeline with low risk; (c) is an upstream Docling limitation requiring a new post-Docling chart-page-detection heuristic -- this is the highest-risk item in the RFC and is **independently deferrable** from (a) and (b). The implementation plan schedules (a,b) and (c) in separate batches so (c) can be rejected or deferred without blocking the lower-risk fixes.

**Affected Documents:**
- uae_numbers_portrait (MARGINAL)
- image pie chart JPG (MARGINAL)
- uae_numbers_landscape (FAIL)

**Files / Functions:**
- `src/pageindex_mcp/converters.py :: _recover_picture_text (lines 1849-1898) -- retain png_bytes and/or clip_text on skip`
- `src/pageindex_mcp/converters.py :: splice_figure_markers (lines 2071-2075) -- do not strip markers with retained context`
- `src/pageindex_mcp/client.py :: standalone image route (lines 928-946) -- copy Docling text into PictureResult.ocr_text`
- `src/pageindex_mcp/converters.py :: post-Docling chart-page heuristic (new) -- detect low-text-density pages and synthesize PictureItem regions`

**Fix:** Three layered fixes: (a) When _recover_picture_text skip gates fire (clip_text_already_exported or page_coverage), still retain the cropped png_bytes in PictureResult so the figure PNG is persisted and the image block maintains its semantic link. For clip_text_already_exported, also propagate the clip_text as ocr_text so splice_figure_markers emits a [Chart text] block. (b) For standalone images where Docling extracts >100 chars, copy the Docling-extracted md_content into the synthetic PictureResult's ocr_text field rather than leaving it empty, ensuring the image block carries chart textual content. (c) Add a post-Docling heuristic that detects pages with very low text content relative to page area (chart-dense pages with <200 chars/page but known visual content) and synthesizes PictureItem regions for them, triggering the picture enrichment pipeline.

**Effort:** Large (~8-10 hours). Three distinct code paths to modify. Fix (a) and (b) are straightforward (~2h each). Fix (c) requires a new heuristic with page rendering and text-density calculation (~4-6h), plus careful threshold tuning to avoid false-positive picture detection on legitimately sparse text pages.

**Test Strategy:** Fix (a): Unit test with a PictureResult that has clip_text_already_exported skip reason; verify png_bytes and ocr_text are retained, not empty. Integration test: re-ingest uae_numbers_portrait and verify image blocks carry chart text. Fix (b): Unit test with standalone JPG where Docling md_content > 100 chars; verify PictureResult.ocr_text contains the Docling text. Integration test: re-ingest image pie chart JPG and verify PictureResult enrichment count > 0. Fix (c): Unit test with a 2-page PDF where each page has <200 chars of text-layer content; verify post-Docling heuristic synthesizes PictureItem regions. Integration test: re-ingest uae_numbers_landscape and verify image markers appear.

---

### D6: LLM judge calibration: stability and severity anchoring rules

**Scope:** Opus audit judge non-determinism: byte-identical extractions between runs receive different verdicts. Affects 3 docs: cabinet_resolution_no_21 (PASS to MARGINAL), uae_numbers_landscape (MARGINAL to FAIL), cabinet_resolution_no_21 copy (PASS to MARGINAL). All have identical metrics between Run 11 and Run 12.

**Root Cause:** The LLM audit judge (Opus) applies different subjective scrutiny to byte-identical artifacts across runs. The stored gate verdicts from deterministic classify_verdict (helpers.py:1285) are unchanged, but the Opus judge in the corpus-ingest-score skill applies non-deterministic severity thresholds. Two new dimensions: (a) table-header malformation scrutiny (cabinet_resolution: judge now flags malformed multi-row headers it previously tolerated), (b) chart-content-loss severity (uae_numbers: judge escalates from MARGINAL to FAIL for the same 748-char artifact with zero enrichments).

**Verification prerequisite (must complete before applying the fix):** For cabinet_resolution_no_21, diff the stored table blocks between Run 11 and Run 12 JSON artifacts to confirm the extraction is truly byte-identical. The audit's own prescribed step ("Diff table blocks between runs' stored JSON before assuming code regression") has not been performed. If the diff reveals actual table-header malformation introduced by a code change, the real fix is a code decision addressing the malformation, not a judge calibration rule. The stability rule below applies ONLY if byte-identity is confirmed.

**Rationale:** Judge non-determinism undermines the corpus quality tracking loop. If the same artifact can receive PASS in one run and MARGINAL in the next without any pipeline change, regressions cannot be distinguished from scoring noise. RFC-028 D6 already fixed one instance of this pattern for image markers; this extends the same calibration approach to two new dimensions (table-header scrutiny and chart-content-loss severity).

**Affected Documents:**
- cabinet_resolution_no_21 (MARGINAL, was PASS)
- uae_numbers_landscape (FAIL, was MARGINAL)
- cabinet_resolution_no_21 copy (MARGINAL, was PASS)

**Files / Functions:**
- `.claude/skills/corpus-ingest-score/SKILL.md -- add two calibration rules to Judge Verdict guidance section`
- `.claude/skills/corpus-score-diff/SKILL.md -- add consistency check for byte-identical artifacts`

**Fix:** Two-phase fix: (Phase A -- verification) Diff stored JSON artifacts for cabinet_resolution_no_21 between Run 11 and Run 12 to confirm byte-identity. If the diff reveals actual table-header malformation not present in Run 11, file a follow-on code decision (not covered by this RFC) to fix the table-header rendering and do NOT apply the stability rule to this document. (Phase B -- calibration, applied only after Phase A confirms byte-identity) Extend the Judge Verdict guidance in corpus-ingest-score SKILL.md with two additional calibration rules: (1) Stability rule: when stored gate verdict is PASS and metrics are byte-identical to the prior run (confirmed by JSON diff, not assumed), the judge MUST NOT downgrade unless it can cite a specific content-quality defect not present in the prior run's finding (prevents re-scoring drift on stable artifacts). (2) Severity anchoring rule: for flat/chart docs with <1000 chars and zero enrichments, anchor the severity to MARGINAL (not FAIL) when the extraction layer itself has not regressed -- the missing enrichment is a known pipeline gap, not a per-run regression.

**Effort:** Small (~2-3 hours). Phase A is a one-off JSON diff (~30 min). Phase B is text changes to skill definition files. If Phase A reveals a real regression, a separate code decision will be needed (effort TBD).

**Test Strategy:** Phase A: diff cabinet_resolution_no_21 stored flat.json between Run 11 and Run 12; document whether table blocks are byte-identical or structurally changed. Phase B (contingent on Phase A confirming byte-identity): re-run corpus-ingest-score on cabinet_resolution_no_21 with the new calibration rules and verify it produces PASS (matching stored gate verdict and Run 11 judge verdict). Re-run on uae_numbers_landscape and verify it produces MARGINAL (not FAIL) given identical 748-char extraction. Verify the rules do not prevent legitimate downgrades by testing with a doc that has genuinely regressed metrics.

---

### D7: Tree-builder table-aware node segmentation

**Scope:** GHV-TKV-Tarif: tree node contains 5101 chars mixing prose and a full rate table (315 pipe chars) because tree-builder splits only on heading boundaries. split_oversized_leaf_nodes only fires at >50k chars. Affects 1 doc: GHV-TKV-Tarif.pdf (MARGINAL).

**Root Cause:** Tree-builder (helpers.py) creates nodes on heading boundaries only. Node 1 (title: Versicherungssumme) contains 5101 chars with both insurance-amount prose text AND a full dog rate table (315 pipe chars). split_oversized_leaf_nodes (helpers.py:1927) only fires at >50k chars, so this 5101-char mixed node passes unchallenged. The document is processed as tree (5 nodes, depth 2), not routed to the flat path, so _flat_parse_table never runs.

**Rationale:** This is a structural quality improvement. The current tree-builder creates nodes based solely on heading boundaries, which means prose and table content within the same heading section are merged into a single oversized node. For RAG retrieval, this means a query about dog insurance rates would pull back 5101 chars of mixed prose+table instead of a focused table node. The 50k-char split_oversized_leaf_nodes threshold is far too high to catch this.

**Affected Documents:**
- GHV-TKV-Tarif.pdf (MARGINAL)

**Files / Functions:**
- `src/pageindex_mcp/helpers.py :: split_oversized_leaf_nodes (line 1927) -- add table-header-based segmentation`
- `src/pageindex_mcp/helpers.py :: md_to_tree or a new _split_on_table_boundaries function -- detect pipe-table boundaries within a node`

**Fix:** Add table-header-based segmentation to the tree-builder: detect when a node body contains multiple distinct content types (prose followed by a pipe-table, identified by |---|---| separator lines) and split on those boundaries. The prose portion retains the original heading; the table portion becomes a child node with a synthesized heading (e.g., the table's first header row or 'Table: [parent heading]'). This should run after heading-based node construction but before validate_tree, with a lower size threshold than the current 50k (e.g., trigger when a node has >2000 chars AND contains a pipe-table with >5 rows).

**Effort:** Medium (~4-6 hours). Requires pipe-table boundary detection within node bodies, node splitting logic, and heading synthesis for the new table node. Must handle edge cases like tables at the start of a node, multiple tables in one node, and tables with no header row.

**Test Strategy:** Unit test: construct a node with 3000 chars of prose followed by a 20-row pipe table; verify it splits into two nodes (prose + table). Unit test: construct a node with only prose and no table; verify it is not split. Integration test: re-ingest GHV-TKV-Tarif and verify the Versicherungssumme node is split into prose and table child nodes. Regression test: re-ingest Haftpflicht-Allgemeine-Bedingungen (PASS, contains tables within nodes) and verify no content loss or verdict change.


### D8: Cross-document contamination gate for zero-body-text node clusters

**Scope:** The al-iqtisad PDF (القرار التنظيمي لوزارة الاقتصاد) has 53% of its tree nodes (48/91) contaminated with an unrelated Ministry of Education org chart, with all contaminated nodes carrying zero body text. The stored gate verdict is PASS -- the contamination is entirely uncaught by validate_tree and classify_verdict. Affects 1 doc.

**Root Cause:** validate_tree checks structural metrics (node_count >= 3, depth >= 2) and _is_garbled_blob checks text quality per-blob, but neither checks for cross-document content contamination or anomalous zero-body-text node clusters. The 48 contaminated nodes have titles (from the unrelated org chart) but empty bodies, which inflates node_count while contributing zero content. classify_verdict sees 91 nodes / 47k chars and returns PASS, unaware that half the tree is from a different document. The PUA-glyph encoding of the real Ministry of Economy content means even the non-contaminated nodes carry degraded text, compounding the problem.

**Rationale:** This is a gate hole explicitly flagged by the Run 12 audit: the stored verdict is PASS on a 53%-contaminated tree. The contamination pattern (large cluster of zero-body-text nodes with titles from an unrelated document) is detectable by a simple heuristic without requiring cross-document comparison.

**Affected Documents:**
- القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf (FAIL, stored verdict PASS)

**Files / Functions:**
- `src/pageindex_mcp/helpers.py :: validate_tree -- add zero-body-text node cluster check`
- `src/pageindex_mcp/helpers.py :: classify_verdict -- downgrade when zero-body fraction exceeds threshold`

**Fix:** Add a zero-body-text node fraction check to validate_tree: after tree construction, count nodes where body text (stripped whitespace) is empty. When the fraction of zero-body nodes exceeds a threshold (e.g., >30% of non-root nodes), flag the tree with `reason=empty_node_contamination`. classify_verdict should treat this as a gate failure (FAIL verdict), not PASS. This catches the specific contamination pattern where an unrelated document's headings are grafted in without corresponding body content. The threshold should be calibrated against the corpus: the al-iqtisad case has 53% zero-body nodes; the 10 PASS docs should all be well below 30%.

**Effort:** Small (~2-3 hours). Single threshold check in validate_tree, verdict adjustment in classify_verdict, straightforward testing.

**Test Strategy:** Unit test: construct a tree with 91 nodes where 48 have empty body text; verify validate_tree flags empty_node_contamination. Unit test: construct a healthy tree with <10% empty-body nodes; verify it passes. Integration test: re-ingest al-iqtisad and verify stored verdict is FAIL (not PASS). Regression test: verify all 10 current PASS docs still pass the zero-body fraction check.

---

## Implementation Plan

| Batch | Decisions | Rationale |
|-------|-----------|-----------|
| 1 | D0, D3, D6, D8 | Small, independent fixes with no cross-dependencies. D0 is critical (total document failure for Human Rights PDF). D3 is a small filter change. D6 is judge calibration (requires D6 verification step first). D8 is a small gate check for contamination. |
| 2 | D1, D2 | Both touch validate_tree and the tree-vs-flat routing in client.py. D1 (thin-tree density gate) and D2 (garble dilution floor) are related quality-gate hardening that should be designed together to avoid conflicting thresholds. D1 depends on D0 being landed since NFKC normalization changes what passes the garble check. |
| 3 | D4, D7 | Table-related improvements in converters.py and helpers.py. D4 (Docling table dedup) and D7 (tree-builder table segmentation) both address table handling but at different pipeline stages. Independent of batches 1-2. |
| 4 | D5(a,b) | Two plumbing fixes for chart/image enrichment: (a) retain png_bytes/clip_text when skip-gates fire, (b) copy Docling text into PictureResult for standalone JPGs. Independent of D5(c). |
| 5 | D5(c) | Highest-risk item: post-Docling chart-page-detection heuristic with new PictureItem synthesis. Benefits from D4 table dedup being landed first (cleaner char counts for page-density detection). May be deferred or rejected independently of D5(a,b). |

## Test Strategy

| Decision | Title | Test Approach |
|----------|-------|---------------|
| D0 | NFKC-normalize Arabic Presentation Forms before garble check | Unit test: craft a string with >50% Arabic PF characters, verify _pre_inference_normalize maps them to canonical forms and _is_garbled_blob no longer flags the result. Unit test: craft a visual-order (reversed) Arabic string after NFKC normalization, verify the bidi-coherence check detects it and flags visual_order_garble. Integration test: re-ingest huquq al-insan and verify it produces a non-ERROR artifact (recovery from ERROR is the primary goal; the Run 11 FAIL-quality artifact is a floor, not a target). Regression test: verify that genuinely garbled text (repeating digit sequences, random byte sequences) still triggers the garble check after normalization. |
| D1 | Content-density gate: prefer flat extraction when tree is thin | Unit test: construct a tree with 6 nodes / 1200 chars and a flat extraction with 75 blocks / 6000 chars; verify the density gate prefers flat. Parameterized test across all 25 corpus docs: verify no PASS doc flips to flat under the new gate. Integration test: re-ingest marsoom 13 and verify it routes to flat with ~5972 chars rather than the thin 1225-char tree. |
| D2 | Post-OCR garble dilution: density floor for scanned Arabic PDFs | Unit test: construct a 42-page tree with 54k chars of repeating numeric content; verify density floor flags it. Unit test: verify a 42-page tree with 54k chars of real Arabic content does NOT trigger the floor. Integration test: re-ingest warid-597 and verify stored verdict is MARGINAL or FAIL (not PASS). Regression test: verify all 10 current PASS docs still pass the density floor. |
| D3 | Strip fence markers and HR separators in flat extraction path | Unit test: feed route_and_extract_flat a markdown string containing ``` fences, --- HRs, and === separators interspersed with real content; verify the output contains only real content blocks with zero fence/HR artifacts. Integration test: re-ingest MOU MOHRE and verify noise block percentage drops from ~20% to <5%. Count test: verify SLA arabic fence block count drops from 40 to 0. |
| D4 | Post-export table deduplication for Docling char inflation | Unit test: construct a pipe-table with 5 byte-identical columns and 1000-char padding per cell; verify repair pass collapses to 1 column with minimal padding. Unit test: construct a pipe-table with legitimately different columns; verify repair pass leaves it unchanged. Integration test: re-ingest world-stats-pocketbook and verify total char count drops from ~9.4M to ~2M (closer to expected ~3k chars/page * 292 pages). Regression test: re-ingest GHV-TKV-Tarif (contains real tables) and verify no content loss. |
| D5 | Retain chart image context when picture skip-gates fire | Fix (a): Unit test with a PictureResult that has clip_text_already_exported skip reason; verify png_bytes and ocr_text are retained, not empty. Integration test: re-ingest uae_numbers_portrait and verify image blocks carry chart text. Fix (b): Unit test with standalone JPG where Docling md_content > 100 chars; verify PictureResult.ocr_text contains the Docling text. Integration test: re-ingest image pie chart JPG and verify PictureResult enrichment count > 0. Fix (c): Unit test with a 2-page PDF where each page has <200 chars of text-layer content; verify post-Docling heuristic synthesizes PictureItem regions. Integration test: re-ingest uae_numbers_landscape and verify image markers appear. |
| D6 | LLM judge calibration: stability and severity anchoring rules | Phase A: diff cabinet_resolution_no_21 stored flat.json between Run 11 and Run 12; document whether table blocks are byte-identical or structurally changed. Phase B (contingent on Phase A confirming byte-identity): re-run corpus-ingest-score on cabinet_resolution_no_21 with calibration rules, verify PASS. Re-run on uae_numbers_landscape, verify MARGINAL (not FAIL). Verify rules do not prevent legitimate downgrades on genuinely regressed docs. |
| D8 | Cross-document contamination gate for zero-body-text node clusters | Unit test: construct a tree with 91 nodes where 48 have empty body text; verify validate_tree flags empty_node_contamination. Unit test: healthy tree with <10% empty-body nodes passes. Integration test: re-ingest al-iqtisad and verify stored verdict is FAIL (not PASS). Regression test: verify all 10 current PASS docs still pass the zero-body fraction check. |
| D7 | Tree-builder table-aware node segmentation | Unit test: construct a node with 3000 chars of prose followed by a 20-row pipe table; verify it splits into two nodes (prose + table). Unit test: construct a node with only prose and no table; verify it is not split. Integration test: re-ingest GHV-TKV-Tarif and verify the Versicherungssumme node is split into prose and table child nodes. Regression test: re-ingest Haftpflicht-Allgemeine-Bedingungen (PASS, contains tables within nodes) and verify no content loss or verdict change. |

## Risks

- D0 NFKC normalization may alter Arabic text semantics in edge cases where Presentation Forms carry intentional glyph-shaping information (e.g., positional forms for calligraphic rendering). Mitigation: NFKC is the standard Unicode normalization for search/comparison and is widely used; verify against all 8 Arabic corpus docs.
- D0 RTL visual-order risk: NFKC maps presentation forms to canonical Arabic but does NOT reorder visual-order text. If the Human Rights PDF emits glyphs in visual (display) order rather than logical (reading) order, NFKC will produce canonically-encoded but character-reversed Arabic that passes the garble gate. Mitigation: the bidi-coherence check (part 2 of the D0 fix) samples multi-word Arabic runs and verifies RTL logical order; text failing this check is flagged as visual_order_garble and routed to OCR retry. The check must be validated against all 8 Arabic corpus docs to avoid false positives on legitimate Arabic text.
- D1 content-density gate threshold (flat > 3x tree) risks flipping currently-PASS tree docs to flat extraction, losing their hierarchical structure. Mitigation: parameterized sweep across all 25 corpus docs before landing the threshold; consider making it configurable via env var.
- D2 chars-per-page density floor requires page_count to be available in validate_tree, which may not be threaded through the current call chain. If page_count is unavailable, the floor cannot fire. Mitigation: verify page_count availability early; if missing, thread it through from the converter layer.
- D4 post-export table deduplication assumes that byte-identical columns are always Docling duplication artifacts. In theory, a real table could have identical columns (e.g., two regions with the same values). Mitigation: require ALL columns in a row to be identical before collapsing (not just any two); add a minimum column count threshold (e.g., only collapse when >3 columns are identical).
- D5 fix (c) post-Docling chart-page heuristic is the highest-risk item: false-positive PictureItem synthesis on legitimately sparse text pages could trigger unnecessary OCR and produce noise. Mitigation: conservative threshold (<200 chars/page AND page has visual content indicators like embedded images or vector paths); make the heuristic opt-in via env var initially.
- D7 table-aware node segmentation may interact poorly with _inject_arabic_structural_headings (D1 dependency) -- if Arabic docs have tables within injected heading sections, the table split could fragment the already-thin tree further. Mitigation: only apply table segmentation to nodes above a minimum char threshold (>2000 chars).
- Cross-decision interaction risk: D0, D1, D2, and D8 all modify the validate_tree / garble-gate / tree-vs-flat decision path. Landing them in the wrong order could produce unexpected routing changes. Mitigation: land D0 and D8 first (most isolated, different gate checks), then D3 (no routing impact), then D1+D2 together (both affect routing), then D4-D7.
- D5(c) post-Docling chart-page heuristic is independently deferrable from D5(a,b). If D5(c) proves too noisy or risky, D5(a,b) can land alone for incremental improvement. The implementation plan schedules them in separate batches (4 and 5) to enable this.
- D6 stability rule is built on an unverified premise: the assertion that cabinet_resolution_no_21 extractions are byte-identical between runs has not been confirmed by diffing stored JSON. If the diff reveals actual table-header malformation introduced by a code change, the stability rule would mask a real regression. Mitigation: Phase A (JSON diff) is a mandatory prerequisite before applying any calibration rules. The table-header malformation the judge flagged (duplicated/misaligned multi-row headers in all 6 fee-schedule tables) may be a real defect regardless of whether it is new or pre-existing; if confirmed as a code regression, a separate code decision will be filed.
- Judge calibration (D6) is a prompt-engineering fix, not a code fix, so its effectiveness depends on the LLM following the calibration rules consistently. The rules may need iteration across 2-3 scoring runs to stabilize. Mitigation: add the rules as MUST-level constraints (not suggestions) and verify with a dedicated calibration test set.
- D8 zero-body-text node fraction threshold (>30%) needs calibration against the full corpus to avoid false positives on documents that legitimately have title-only nodes (e.g., section headings with content in child nodes). Mitigation: parameterized sweep across all 25 corpus docs; count zero-body non-leaf nodes separately from zero-body leaf nodes (leaf nodes with empty bodies are the stronger contamination signal).

## Out of Scope

- Trace 4/10 (Unfallversicherung-Leistungsuebersicht empty cells): drilldown DISPROVED the cluster finding. Empty cells are intentional in the source PDF benefits-comparison table structure (category headers and unavailable benefits), not TableFormer rowspan data loss. No fix needed.
- Trace 12 world-stats-pocketbook 19/20 unenriched image markers: drilldown confirmed these are non-text decorative graphics (UN emblem, cover art, icons) where empty OCR is correct behavior, not a bug. Only the first region (cover paragraph rendered as image) legitimately has extractable text.
- سياسة حوكمة (governance policy) RTL-reversal verification: the Run 12 audit notes the FAIL-to-PASS improvement on this doc is "likely a judge-side scoring change rather than a fix to the underlying RTL-reversal defect" and recommends verifying against live node titles. This verification is deferred: D0's bidi-coherence check will provide a systematic mechanism to detect RTL-reversal across all Arabic docs. If the bidi check catches this doc post-D0, it will surface as a new finding in the next run's audit.
- al-iqtisad PUA-glyph content recovery: D8 addresses the gate hole (stored PASS on contaminated tree) but does not attempt to recover the real Ministry of Economy content from PUA-glyph-encoded source text. PUA recovery is a distinct problem requiring font CMap analysis or targeted OCR; it is deferred to a future RFC if D8 correctly gates the contaminated artifact.
