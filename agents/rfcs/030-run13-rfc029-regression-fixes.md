<!-- Space: CITRA -->
<!-- Title: RFC-030: Run 13 RFC-029 Regression Fixes: Fence-Toggle Content Loss, OCR Retry Floor, and Validate-Tree Recovery Gaps -->
<!-- Folder: RFCs -->

# RFC-030: Run 13 RFC-029 Regression Fixes: Fence-Toggle Content Loss, OCR Retry Floor, and Validate-Tree Recovery Gaps

**Run:** 13
**Audit:** [audit/CORPUS_REINGESTION_AUDIT_RUN-13.md](../../audit/CORPUS_REINGESTION_AUDIT_RUN-13.md)
**Status:** Draft

## Summary

Run 13 audited all 25 corpus documents and produced 7 PASS / 9 MARGINAL / 5 FAIL / 4 ERROR, a net regression from Run 12's 10 PASS / 10 MARGINAL / 4 FAIL / 1 ERROR. Five documents improved (notably حقوق الإنسان ERROR to PASS, القرار التنظيمي FAIL to PASS). Twelve documents regressed, driven by three systemic root-cause clusters all traceable to RFC-029's implementation: (1) four new validate_tree failure reasons added to helpers.py but never wired into client.py recovery routing, causing 3 PASS-to-ERROR regressions; (2) fence-stripping in route_and_extract_flat that drops ALL content inside fenced code blocks, causing 89-100% content loss for Arabic documents whose Docling markdown contains stray or content-wrapping fence markers; (3) the _repeating_token_density short-text floor making the OCR retry guardrail arithmetically impossible to win for no-text-layer PDFs. Additionally, judge-severity downgrades on byte-identical metrics reflect missing calibration rules (RFC-029 D6 Phase B) that were marked complete but never written to the skill files.

## Decisions

### D0: Fix fence-toggle content destruction in route_and_extract_flat

**Scope:** RFC-029 D3 added a naive fence-parity toggle (in_fence flag) in route_and_extract_flat (helpers.py:2711-2726) that silently swallows ALL content between triple-backtick lines. Docling wraps substantial Arabic content in fenced blocks; stray or odd-count fence markers from layout misclassification cause total or partial content loss. SLA doc: 264 blocks to 0; MOU: 89% loss; qerar-106: truncation at Article 4; Reitlehrer: 32% char reduction. Affects 4-5 documents directly.

**Root Cause:** RFC-029 D3 commit 08b6eea added an in_fence boolean toggle at helpers.py:2711 that flips on any line starting with triple-backtick. While in_fence is True, every subsequent line is skipped with no block emitted and no recovery if the fence is never re-closed. The test the RFC-029 author wrote (test_rfc029_d3.py::TestEdgeCases::test_unclosed_fence_content_is_skipped) explicitly asserts that content after an unclosed opening fence is silently dropped -- confirming this was known-but-unguarded behavior. Docling's MarkdownDocSerializer wraps DocItems classified as CODE in fence markers, and layout misclassification of stamps/signatures in Arabic government PDFs produces stray fence lines with odd parity, causing permanent content loss.

**Rationale:** This is the highest-impact content-destruction regression in Run 13. The fence-toggle was added to strip code-block formatting noise, but it operates as a naive parity toggle with zero validation. A single stray backtick-fence line (common in Docling output for Arabic documents with stamps/signatures misclassified as CODE by the Heron RT-DETRv2 layout model) permanently toggles in_fence to True, causing all subsequent content to be silently discarded. In Run 12, route_and_extract_flat had NO fence-handling code, so stray backtick lines fell through as ordinary noise text without blocking extraction.

**Affected Documents:**
- اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf (MARGINAL->FAIL, 264 blocks->0, total loss)
- MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf (MARGINAL->FAIL, 89% content loss)
- قرار مجلس الوزراء رقم (106) لسنة 2022 (MARGINAL->FAIL, truncation at Article 4)
- مرسوم بقانون اتحادي رقم (13) لسنة 2022 (FAIL->FAIL, 0 nodes/0 chars, total collapse)
- Reitlehrer - Schäden am Berittpferd.pdf (PASS->MARGINAL, 32% char reduction)

**Files / Functions:**
- `src/pageindex_mcp/helpers.py :: route_and_extract_flat() -- fence-toggle at lines 2711-2726, HR-separator stripping at lines 2733-2737`
- `src/pageindex_mcp/client.py :: index() -- add post-extraction zero-block guard for flat-routing output path`
- `tests/test_rfc029_d3.py :: TestEdgeCases::test_unclosed_fence_content_is_skipped -- update to assert content preservation instead of silent drop`

**Fix:** Three changes: (1) Replace the naive in_fence parity toggle with a fence-delimiter-only stripping approach: instead of skipping ALL lines while in_fence is True, skip only the triple-backtick delimiter lines themselves and let enclosed content fall through to the normal prose/table parsers. Alternatively, pre-scan lines for fence-marker count; if odd, treat only paired fences as real fences and let unpaired trailing markers pass through as noise text. (2) Add a post-extraction zero-block guard in the flat-routing caller in client.py: if route_and_extract_flat returns (content_class, []) from non-empty input markdown, treat it as an extraction failure requiring escalation (re-run without the fence heuristic, or raise the same error path used for tree-routed docs) instead of persisting a 0-block flat.json. (3) Review HR-separator stripping (lines 2733-2737) for over-aggressiveness contributing to char-count reduction in Reitlehrer.

**Effort:** Medium (~4-6 hours). Fence logic rewrite is ~2h, zero-block guard is ~1h, HR review is ~1h, testing across all 25 corpus docs is ~2h.

**Test Strategy:** Unit test: markdown with paired fence blocks preserves enclosed content as prose blocks. Unit test: markdown with an odd number of fence markers (unclosed fence) preserves all content after the stray marker. Unit test: zero-block output from non-empty markdown triggers escalation, not silent persistence. Regression test: re-ingest the SLA doc, MOU, qerar-106, marsoom-13, and Reitlehrer; verify block counts recover to Run 12 levels or better. Integration test: verify all flat-routed corpus documents produce non-zero blocks when input markdown is non-empty (tree-routed documents produce nodes, not blocks, and are not subject to this check).

---

### D1: Fix _repeating_token_density short-text floor breaking OCR retry guardrail

**Scope:** _repeating_token_density hard-codes return 0.0 when text has <20 alnum tokens (client.py:1094-1095). For no-text-layer PDFs the pre-retry snapshot always has <20 tokens, so _pre_density is always 0.0, making the win condition (_post_density < 0.0) arithmetically impossible. The OCR retry always loses and reverts to the garbled pre-retry tree even when the retry produced valid content. Additionally, the revert path (lines 1144-1147) rolls back result/ok/reason but not md_content/tmp_md_path/pic_results, creating a tree-vs-markdown state mismatch.

**Root Cause:** RFC-029 D4 (Task 3.3) added a keep-best guardrail at client.py:1083-1148 using _repeating_token_density to compare pre-retry and post-retry trees. The helper returns 0.0 for text with fewer than 20 alnum tokens (line 1094-1095), which is guaranteed for the pre-retry snapshot of a no-text-layer PDF. The win condition _post_density < _pre_density * 0.80 becomes _post_density < 0.0, which is never true. The revert path at lines 1144-1147 only restores result/ok/reason but leaves md_content/tmp_md_path/pic_results pointing to the post-retry (good OCR) data, creating a mismatch between the reverted tree and the un-reverted flat-routing markdown.

**Rationale:** This bug defeats the entire purpose of the OCR retry path for exactly the class of documents it was designed to rescue -- no-text-layer scanned PDFs. The قرار مجلس الوزراء رقم (1) document lost 69% of its content (48k to 14.8k chars) because the retry produced good OCR output but the density comparison forced a revert to the near-empty pre-retry tree, while the un-reverted md_content still held the good OCR markdown, creating an inconsistent state where only a fraction of the recovered content survived into the final flat output.

**Affected Documents:**
- قرار مجلس الوزراء رقم (1) لسنة 2022 في شأن اللائحة التنفيذية (MARGINAL->MARGINAL stall, 69% content loss: 48k->14.8k chars)

**Files / Functions:**
- `src/pageindex_mcp/client.py :: _repeating_token_density() at lines 1083-1096 -- short-text floor logic`
- `src/pageindex_mcp/client.py :: index() OCR retry keep-best block at lines 1111-1148 -- win condition and revert path`

**Fix:** Two changes: (1) In _repeating_token_density, return None (not 0.0) when token count is below 20. In the D4 comparator block, when _pre_density is None, short-circuit retry_wins=True -- any real OCR recovery beats a near-empty/garbled pre-retry snapshot. (2) Make the revert at client.py:1144-1147 atomic: restore md_content, tmp_md_path, and pic_results alongside result/ok/reason, so the tree path and downstream flat-routing path cannot diverge on which markdown was actually used.

**Effort:** Small (~2-3 hours). The density floor fix is a 5-line change; the atomic revert requires snapshotting 3 additional variables before retry and restoring them on revert; testing is ~1h.

**Test Strategy:** Unit test: _repeating_token_density returns None for text with <20 alnum tokens. Unit test: when _pre_density is None, retry_wins is True regardless of _post_density value. Unit test: when retry loses, verify md_content/tmp_md_path/pic_results are reverted alongside result/ok/reason (all state consistent). Regression test: re-ingest قرار مجلس الوزراء رقم (1); verify chars recover to ~48k level.

---

### D2: Persist trees with unhandled validate_tree failure reasons as FAIL instead of raising ERROR

**Scope:** RFC-029 added 4 new validate_tree failure reasons (suspect_density, low_content_density, empty_node_contamination, arabic_low_content_ratio) but none are handled in client.py recovery paths. OCR escalation only handles garbling/node_garbling/visual_order_garble. Flat routing only handles node_count<3/depth<2. Unhandled reasons fall through to raise LowQualityTreeError (terminal ERROR) with no recovery opportunity. This is the single highest-impact systemic bug in Run 13, responsible for 3 PASS-to-ERROR regressions and 1 FAIL-to-ERROR regression.

**Root Cause:** RFC-029 D1 added low_content_density (helpers.py:1342-1356), D2 added suspect_density and arabic_low_content_ratio (helpers.py:1357-1375), and empty_node_contamination was added as a validation check. However, client.py::index() was never updated to handle these new reason strings. The OCR escalation condition at line ~1011 only checks for garbling/node_garbling/visual_order_garble. The flat-routing condition at line ~1387 only checks for node_count<3/depth<2. All four new reasons fall through the if/elif chain to the default raise LowQualityTreeError at line 1639.

Critically, classify_verdict (helpers.py:1541-1553) ALREADY maps all four of these reasons to hard FAIL verdicts -- with an explicit comment at line 1539-1540 that empty_node_contamination "is a hard FAIL (CLAUDE.md Hard Rule 5), not a MARGINAL, so no promotion branch can override it." This confirms RFC-029's design intent was: validate_tree flags the problem, classify_verdict assigns a FAIL verdict, and the document is persisted with that FAIL verdict. The current behavior -- raising LowQualityTreeError to produce an unrecoverable ERROR -- was never the intended outcome; it is a wiring omission.

**warid-597 reconciliation:** The audit's live verification (P1) shows warid-597 was "never persisted -- meta.json lookup returns NoSuchKey, pipeline timed out before any output was written." This is a different failure mode from the suspect_density LowQualityTreeError hypothesis. The timeout may be caused by an infinite loop or hang in the OCR escalation path for 42-page scanned Arabic PDFs -- possibly introduced by RFC-029's garble-gate or OCR-handling changes. D2's persist-with-FAIL fix will NOT resolve a timeout. warid-597 requires separate investigation to determine whether the job dies before reaching validate_tree (timeout) or after (LowQualityTreeError). If the timeout is confirmed, it is a separate defect requiring its own fix (possibly a job-level timeout guard or OCR path performance investigation).

**Rationale:** The simplest fix matching RFC-029's own design intent (evidenced by the classify_verdict mappings) is: persist the tree with its validate_tree-assigned reason, let classify_verdict score it as FAIL, and store the artifact. This preserves the document for inspection, avoids the unrecoverable ERROR state, and does not destroy valid hierarchy by routing to flat extraction or trigger unnecessary OCR retries. Routing low_content_density or suspect_density documents to flat extraction would destroy any valid hierarchy the tree contains -- a Penal Code at 408.2 chars/node has genuine hierarchical structure that should not be flattened. Similarly, routing empty_node_contamination to OCR retry assumes the problem is OCR-related, which may not be the case for layout-misclassification defects.

**Affected Documents:**
- FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE - Copy.pdf (PASS->ERROR, low_content_density at 408.2 chars/node)
- federal_decree_law_no_33_of_2021 - Copy.pdf (PASS->ERROR, low_content_density at 54.3 chars/node)
- مرسوم بقانون اتحادي رقم (33) لسنة 2021 (PASS->ERROR, low_content_density at 459.4 chars/node)
- وارد رقم 597 (FAIL->ERROR, suspect_density at 1303 chars/page < 1500 threshold -- BUT audit shows doc was never persisted due to timeout, so this reason may not be the actual failure mode; see warid-597 reconciliation above)

**Files / Functions:**
- `src/pageindex_mcp/client.py :: index() -- the if/elif chain ending at line ~1639 (raise LowQualityTreeError) -- extend the flat-routing condition or add a new branch`

**Fix:** Add a branch in the client.py if/elif chain (before the terminal `raise LowQualityTreeError` at line 1639) that catches the four unhandled reasons and falls through to the persist path instead of raising. Specifically: when `reason` starts with `low_content_density`, `suspect_density`, `empty_node_contamination`, or equals `arabic_low_content_ratio`, set `ok = False` but skip the raise -- allowing the code to reach save_doc and classify_verdict, which will assign the appropriate FAIL verdict (already implemented at helpers.py:1541-1553). The tree is persisted as-is with a FAIL gate verdict, preserving the artifact for human inspection rather than producing an unrecoverable ERROR.

This is NOT a recovery routing change -- no flat extraction, no OCR retry. The document keeps its tree structure and gets a FAIL verdict, which is the correct severity for a quality concern that does not warrant total rejection.

For warid-597 specifically: add an open investigation item to determine whether the actual failure is a timeout (pre-validate_tree) or a LowQualityTreeError (post-validate_tree). If timeout, the fix is a job-level timeout guard or OCR-path performance fix, not a validate_tree routing change.

**Effort:** Small (~2-3 hours). The persist-with-FAIL path is a conditional expansion of ~5 lines. warid-597 timeout investigation is separate effort (~2-4 hours).

**Test Strategy:** Unit test: construct a tree triggering low_content_density; verify it is persisted (save_doc called) with classify_verdict returning FAIL, not raising LowQualityTreeError. Unit test: same for suspect_density, empty_node_contamination, arabic_low_content_ratio. Unit test: verify the persisted tree structure is unchanged (no flat extraction, no OCR retry). Regression test: verify all current PASS documents still route through the normal tree path. Regression test: re-ingest Penal Code, federal_decree_law_no_33 EN, marsoom-33 AR; verify they produce FAIL instead of ERROR and their tree artifacts are persisted for inspection.

---

### D3: Lower low_content_density threshold to stop rejecting well-structured legal trees

**Scope:** The low_content_density gate (helpers.py:1342-1356) uses a 500 chars/node threshold (`_RFC029_MIN_CHARS_PER_NODE`, env `RFC029_MIN_CHARS_PER_NODE`, default 500) that is too aggressive for well-structured legal documents with fine-grained article hierarchies. Three previously-PASS documents are rejected: Penal Code at 408.2 chars/node (606 articles), federal_decree_law_no_33 at 54.3 chars/node (dense article numbering), marsoom-33 at 459.4 chars/node. The gate fires when total_nodes >= 200 (helpers.py:1349).

**Root Cause Analysis and Trace Reconciliation:**

The original trace attributed fed-33's 502->2042 node explosion (chars_per_node 221->54.3) to `_segment_table_nodes`. However, `_segment_table_nodes` is called ONLY at client.py:1066 (OCR retry path) and client.py:1215 (VLM fallback path) -- it is NOT called on the primary tree-build path at client.py:981. Verified: `grep -n '_segment_table_nodes' src/pageindex_mcp/client.py` returns exactly two call sites (1066, 1215), neither on the primary path.

Therefore, if fed-33 traversed the primary path (no OCR retry, no VLM fallback), `_segment_table_nodes` cannot explain the node explosion. The 502->2042 expansion must have a different cause -- likely `split_oversized_leaf_nodes` (which IS on the primary path) or a change in the upstream Docling/splitter producing more granular nodes. Alternatively, if fed-33 DID traverse the OCR retry path (e.g., because its text layer triggered garbling detection), then `_segment_table_nodes` could have fired there, but the trace does not document which path fed-33 actually took.

Regardless of the node-explosion root cause, the actionable fix is the same: the 500 chars/node threshold is too aggressive. A Penal Code with 606 articles averaging ~360 chars/article is well-structured, not degenerate. The gate was calibrated against a single problematic document (marsoom-13 at ~200 chars/node) but rejects legitimate legal trees in the 300-500 chars/node range.

**Note on _segment_table_nodes wiring:** The original D3 proposed adding `_segment_table_nodes` to the primary tree-build path. This is REMOVED from the fix because: (a) it would mechanically reduce chars_per_node for ALL documents with tables, making more documents hit the density gate; (b) the GHV-TKV-Tarif stall (table stays merged) is a separate concern that should be addressed independently once the density gate threshold is correct; (c) running segmentation before validate_tree vs after produces opposite density-gate outcomes, and this ordering question should not be conflated with the threshold fix. GHV-TKV-Tarif table segmentation is deferred to a follow-up task.

**Affected Documents:**
- FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE - Copy.pdf (PASS->ERROR, 408.2 chars/node < 500 threshold)
- federal_decree_law_no_33_of_2021 - Copy.pdf (PASS->ERROR, 54.3 chars/node -- node-explosion root cause unclear, see analysis above)
- مرسوم بقانون اتحادي رقم (33) لسنة 2021 (PASS->ERROR, 459.4 chars/node < 500 threshold)

**Files / Functions:**
- `src/pageindex_mcp/helpers.py :: _RFC029_MIN_CHARS_PER_NODE at line 1102 -- lower default from 500 to 150`
- `src/pageindex_mcp/helpers.py :: validate_tree() docstring at line 1289 -- fix total_nodes >= 3 vs >= 200 mismatch`

**Fix:** Two changes: (1) Lower the `_RFC029_MIN_CHARS_PER_NODE` default from 500 to 150 chars/node. This retains the gate's purpose (catching degenerate fragmentation where nodes average <100-150 chars) while permitting well-formed legal tree structures (300-500 chars/node range) AND accommodating fed-33's 54.3 chars/node (which, while low, was previously a PASS document with a valid depth-4 hierarchical tree). The threshold is env-configurable (`RFC029_MIN_CHARS_PER_NODE`) so it can be tuned without code changes. (2) Fix the docstring at helpers.py line 1289 to match the actual code (total_nodes >= 200, not >= 3).

Note: With D2's persist-with-FAIL fix, even documents below the threshold will be persisted rather than ERROR'd, so the threshold primarily determines PASS/MARGINAL vs FAIL classification, not the ERROR/non-ERROR boundary. This reduces the urgency of exact threshold calibration.

**Effort:** Small (~1-2 hours). Threshold change is a 1-line edit, docstring fix is trivial. Testing requires verifying the 3 affected docs recover.

**Test Strategy:** Unit test: tree with 300 nodes at 300 chars/node passes low_content_density gate (was rejected at 500 threshold). Unit test: tree with 300 nodes at 50 chars/node still fails low_content_density gate. Unit test: tree with 200 nodes at 160 chars/node passes (above 150 threshold). Regression test: re-ingest Penal Code, federal_decree_law_no_33 EN, marsoom-33 AR; verify they produce PASS or MARGINAL (not ERROR or FAIL from the density gate). Note: fed-33's recovery to PASS depends on whether the node-explosion root cause is also addressed; if chars_per_node remains 54.3, the 150 threshold still rejects it, but D2 ensures it persists as FAIL rather than ERROR. A full PASS recovery for fed-33 requires separate investigation into the node-expansion mechanism.

---

### D4: Extend garble gate to inspect node title field

**Scope:** _garble_check_nodes (helpers.py:1153-1186) only inspects node.get('text'), never node.get('title'). RTL-reversed titles (23/24 nodes in siyasat-hawkama) are invisible to the garble gate. classify_verdict also uses _tree_is_garbled which flattens only text fields via _flatten_tree_text. The garble gate has zero coverage of the title field, making title-level corruption a permanent blind spot.

**Root Cause:** _garble_check_nodes iterates over nodes and calls garble detection functions only on node.get('text'). The 'title' field is never read or inspected. _flatten_tree_text (used by _tree_is_garbled and classify_verdict) concatenates only text fields from the tree, excluding titles entirely. This means RTL-reversed, garbled, or corrupted titles are invisible to all automated quality checks.

**Rationale:** Node titles are first-class structural metadata used for navigation, search, and tree display. RTL-reversed titles make the tree structurally unusable for Arabic documents even when body text is correct. The garble gate's blind spot means this defect class can never be detected or escalated to OCR retry, persisting indefinitely across runs. The siyasat-hawkama document has 23/24 reversed titles and was correctly flagged by the Run 13 judge but would never be caught by the pipeline's own quality gates.

**Affected Documents:**
- سياسة حوكمة و إدارة البيانات - Copy.pdf (PASS->MARGINAL, 23/24 node titles fully character-reversed)

**Files / Functions:**
- `src/pageindex_mcp/helpers.py :: _garble_check_nodes() at lines 1153-1186 -- add title field inspection`
- `src/pageindex_mcp/helpers.py :: _flatten_tree_text() -- add title text to concatenation`
- `src/pageindex_mcp/helpers.py :: _tree_is_garbled() -- inherits fix via _flatten_tree_text`

**Fix:** Two changes: (1) In _garble_check_nodes, after inspecting node.get('text'), also inspect node.get('title') with the same garble/RTL-reversal checks (including _word_has_reversed_morphology for Arabic titles). If either field is garbled, mark the node as garbled. (2) In _flatten_tree_text, include title text in the concatenation (prepend each node's title to its text with a separator) so that _tree_is_garbled and classify_verdict can detect title-level corruption in their aggregate garble checks.

**Effort:** Small (~2 hours). Adding title inspection to _garble_check_nodes is a few lines of code mirroring the existing text inspection. _flatten_tree_text change is a 1-line addition per node.

**Test Strategy:** Unit test: node with garbled title but clean text is detected by _garble_check_nodes. Unit test: node with RTL-reversed Arabic title is detected by _word_has_reversed_morphology check. Unit test: _flatten_tree_text output includes title text. Regression test: re-ingest siyasat-hawkama; verify the garble gate detects the 23/24 reversed titles and triggers OCR escalation or appropriate routing.

---

### D5: Wire _check_bidi_coherence into validate_tree pipeline

**Scope:** _check_bidi_coherence is defined TWICE (helpers.py lines 936 and 1028) but never called from any pipeline path. RFC-029 D0 intended this as a validate_tree gate for detecting visual-order Arabic (reversed morphology). The function is dead code -- fully implemented but completely unwired. Both MOU and siyasat-hawkama documents would benefit from bidi coherence detection to catch reversed Arabic in body text and titles respectively.

**Root Cause:** RFC-029 D0 implemented _check_bidi_coherence at two locations in helpers.py (lines 936 and 1028) but never added a call site in validate_tree or any other pipeline function. The function exists as dead code. The duplicate definition is likely a copy-paste error during RFC-029 implementation.

**Rationale:** The bidi coherence check was designed and implemented in RFC-029 D0 specifically to detect visual-order Arabic text (where characters appear in display order rather than logical order, producing reversed morphology). It is a targeted, already-written check that addresses a known blind spot in the garble gate. The duplicate definition suggests a merge/copy error. Wiring it in completes RFC-029 D0's original design intent with no new algorithm development required.

**Affected Documents:**
- MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf (reversed Arabic in body text)
- سياسة حوكمة و إدارة البيانات - Copy.pdf (reversed Arabic in 23/24 node titles)

**Files / Functions:**
- `src/pageindex_mcp/helpers.py :: _check_bidi_coherence() at lines 936 and 1028 -- deduplicate, keep one definition`
- `src/pageindex_mcp/helpers.py :: validate_tree() -- add _check_bidi_coherence call after existing _tree_is_rtl_reversed check`
- `src/pageindex_mcp/client.py :: index() -- add 'visual_order_garble' to flat-routing or OCR-escalation whitelist if bidi-incoherent docs should recover instead of ERROR`

**Fix:** Three changes: (1) Remove the duplicate _check_bidi_coherence definition (keep the more complete one, likely at line 1028). (2) Wire the single _check_bidi_coherence into validate_tree as an additive gate after the existing _tree_is_rtl_reversed check, returning (False, 'visual_order_garble') when the check fails. (3) Ensure 'visual_order_garble' is already in the client.py OCR-escalation condition (it should be per existing code at line ~1011); if not, add it.

**Effort:** Small (~1-2 hours). Deduplication is trivial, wiring is a single function call addition in validate_tree, client.py change is a conditional check.

**Test Strategy:** Unit test: tree with visual-order Arabic text (reversed morphology) triggers _check_bidi_coherence failure. Unit test: validate_tree returns (False, 'visual_order_garble') for bidi-incoherent trees. Unit test: verify only one definition of _check_bidi_coherence exists after fix. Integration test: re-ingest MOU and siyasat-hawkama; verify bidi coherence check fires and routes to appropriate recovery path.

---

### D6: Write judge calibration rules to corpus-ingest-score skill file

**Scope:** RFC-029 D6 Phase B judge calibration rules were marked complete (task 1.7) but never written to .claude/skills/corpus-ingest-score/SKILL.md. Two rules are missing: (1) stability rule -- stored PASS with byte-identical metrics must not be downgraded without citing a specific new defect; (2) severity-anchoring rule -- flat/chart docs with <1000 chars and zero enrichments anchor to MARGINAL not FAIL when extraction has not regressed. Without these, the Opus judge applies inconsistent scrutiny across runs on byte-identical content.

**Root Cause:** RFC-029 D6 Phase B specified two calibration rules and task 1.7 was marked complete, but the rules were never actually written to .claude/skills/corpus-ingest-score/SKILL.md. The skill file (lines 34-42) still only contains the old RFC-028 D6 guidance about image markers in hierarchical docs. Without the stability and severity-anchoring rules, the Opus judge has no constraint against applying different scrutiny levels to identical content across runs.

**Rationale:** Judge non-determinism on unchanged content creates phantom regressions that waste investigation time and obscure real pipeline defects. Haftpflicht (stored PASS, 132 nodes/53K chars, depth 2 unchanged from Run 12) was downgraded to MARGINAL on 'depth-2-too-shallow' scrutiny that Run 12's judge did not apply. Image pie chart (stored MARGINAL, 6 nodes/489 chars byte-identical across 3 runs) was escalated to FAIL on RTL garbling Run 12's judge did not flag. Both are pure judge non-determinism with no underlying pipeline change. The calibration rules stabilize verdicts for unchanged content while still allowing downgrades when real new defects are found.

**Affected Documents:**
- Haftpflicht-Allgemeine-Bedingungen.pdf.pdf (PASS->MARGINAL, judge downgrade on byte-identical depth-2 tree)
- image pie chart about labor distribution in january 2025 - Copy.jpg (MARGINAL->FAIL, judge escalation on byte-identical 489-char content)
- Federal Decree-Law No. (47) of 2021 - Copy.pdf (PASS->MARGINAL, near-identical metrics, judge scrutiny increase)

**Files / Functions:**
- `.claude/skills/corpus-ingest-score/SKILL.md -- add two calibration rules after existing RFC-028 D6 paragraph (lines 34-42)`
- `.claude/skills/corpus-score-diff/SKILL.md -- add consistency check note for byte-identical artifacts across runs`

**Fix:** Add two calibration rules to .claude/skills/corpus-ingest-score/SKILL.md: (1) Stability rule: 'When stored gate verdict is PASS and extraction metrics (node count, char count, depth) are within 10% of the prior run (widened from byte-identical to accommodate non-deterministic Docling output variations, e.g., Haftpflicht chars changed ~6% between runs with no pipeline change), the judge MUST NOT downgrade unless it can cite a specific content-quality defect (garbling, content loss, structural corruption) not present in the prior run finding. Known persistent limitations (e.g. shallow depth for a complex document) that were tolerated in the prior run are NOT grounds for downgrade.' (2) Severity-anchoring rule: 'For flat/chart-heavy docs (content_class starts with flat_) with <1000 total chars and zero picture enrichments, anchor severity to MARGINAL (not FAIL) when the extraction layer has not regressed -- the missing enrichment is a known pipeline gap, not a per-run regression.' Also add a consistency check note to .claude/skills/corpus-score-diff/SKILL.md for byte-identical artifacts across runs.

**Effort:** Small (~1 hour). Pure documentation/configuration change with no code modifications.

**Test Strategy:** Manual verification: re-score Haftpflicht and image pie chart with the updated skill rules; verify Haftpflicht retains PASS and image pie chart retains MARGINAL when metrics are byte-identical to prior run. Process validation: review the skill file diff to confirm both rules are correctly placed and worded. Regression test: run full corpus scoring and verify no PASS documents are downgraded on unchanged metrics.


## Implementation Plan

| Batch | Decisions | Rationale |
|-------|-----------|-----------|
| 1 | D3, D2 | D3 (threshold reduction) must land first because it determines which documents still trigger `low_content_density`. D2 (persist-with-FAIL instead of ERROR) depends on D3's threshold to determine which documents fall through to persist vs pass validation. Together they resolve all 3 PASS-to-ERROR regressions. Order: D3 threshold first, then D2 persist-with-FAIL wiring. |
| 2 | D0, D1 | Critical content-loss fixes with no dependency on Batch 1. D0 fixes total content loss from fence-toggle for multiple Arabic documents (0-block extraction). D1 fixes OCR retry never winning for no-text-layer PDFs. Both are independent of each other and of the validate_tree routing fixes. |
| 3 | D4, D5, D6 | Lower-severity improvements with no upstream dependencies. D4 and D5 are both garble/bidi detection enhancements in helpers.py that can be implemented in parallel. D6 is a config-only change to skill files with no code impact. |

## Test Strategy

| Decision | Title | Test Approach |
|----------|-------|---------------|
| D0 | Fix fence-toggle content destruction in route_and_extract_flat | Unit test: markdown with paired fence blocks preserves enclosed content as prose blocks. Unit test: markdown with an odd number of fence markers (unclosed fence) preserves all content after the stray marker. Unit test: zero-block output from non-empty markdown triggers escalation, not silent persistence. Regression test: re-ingest the SLA doc, MOU, qerar-106, marsoom-13, and Reitlehrer; verify block counts recover to Run 12 levels or better. Integration test: verify all flat-routed corpus documents produce non-zero blocks when input markdown is non-empty (tree-routed documents produce nodes, not blocks, and are not subject to this check). |
| D1 | Fix _repeating_token_density short-text floor breaking OCR retry guardrail | Unit test: _repeating_token_density returns None for text with <20 alnum tokens. Unit test: when _pre_density is None, retry_wins is True regardless of _post_density value. Unit test: when retry loses, verify md_content/tmp_md_path/pic_results are reverted alongside result/ok/reason (all state consistent). Regression test: re-ingest قرار مجلس الوزراء رقم (1); verify chars recover to ~48k level. |
| D2 | Persist trees with unhandled validate_tree failure reasons as FAIL instead of raising ERROR | Unit test: construct a tree triggering low_content_density; verify it is persisted (save_doc called) with classify_verdict returning FAIL, not raising LowQualityTreeError. Unit test: same for suspect_density, empty_node_contamination, arabic_low_content_ratio. Unit test: verify the persisted tree structure is unchanged (no flat extraction, no OCR retry). Regression test: verify all current PASS documents still route through the normal tree path. Regression test: re-ingest Penal Code, federal_decree_law_no_33 EN, marsoom-33 AR; verify they produce FAIL instead of ERROR and their tree artifacts are persisted. |
| D3 | Lower low_content_density threshold to stop rejecting well-structured legal trees | Unit test: tree with 300 nodes at 300 chars/node passes low_content_density gate (was rejected at 500 threshold). Unit test: tree with 300 nodes at 50 chars/node still fails low_content_density gate. Unit test: tree with 200 nodes at 160 chars/node passes (above 150 threshold). Regression test: re-ingest Penal Code, federal_decree_law_no_33 EN, marsoom-33 AR; verify they produce PASS or MARGINAL (not ERROR or FAIL from the density gate). Note: fed-33 recovery to PASS depends on separate node-expansion investigation; with D2 it persists as FAIL rather than ERROR regardless. |
| D4 | Extend garble gate to inspect node title field | Unit test: node with garbled title but clean text is detected by _garble_check_nodes. Unit test: node with RTL-reversed Arabic title is detected by _word_has_reversed_morphology check. Unit test: _flatten_tree_text output includes title text. Regression test: re-ingest siyasat-hawkama; verify the garble gate detects the 23/24 reversed titles and triggers OCR escalation or appropriate routing. |
| D5 | Wire _check_bidi_coherence into validate_tree pipeline | Unit test: tree with visual-order Arabic text (reversed morphology) triggers _check_bidi_coherence failure. Unit test: validate_tree returns (False, 'visual_order_garble') for bidi-incoherent trees. Unit test: verify only one definition of _check_bidi_coherence exists after fix. Integration test: re-ingest MOU and siyasat-hawkama; verify bidi coherence check fires and routes to appropriate recovery path. |
| D6 | Write judge calibration rules to corpus-ingest-score skill file | Manual verification: re-score Haftpflicht and image pie chart with the updated skill rules; verify Haftpflicht retains PASS and image pie chart retains MARGINAL when metrics are byte-identical to prior run. Process validation: review the skill file diff to confirm both rules are correctly placed and worded. Regression test: run full corpus scoring and verify no PASS documents are downgraded on unchanged metrics. |

## Risks

- D0 fence-fix risk: changing fence stripping to delimiter-only removal may re-introduce formatting noise (raw backtick lines appearing as text content) for documents where fence stripping was actually beneficial. Mitigation: test against all 25 corpus docs to verify no currently-PASS documents regress.
- D2 persist-with-FAIL risk: persisting trees that fail validation (instead of rejecting them as ERROR) stores artifacts with known quality defects. These FAIL artifacts may be returned to downstream consumers who expect only PASS/MARGINAL documents. Mitigation: downstream query tools already filter by verdict; FAIL documents are excluded from default query results. The FAIL verdict explicitly signals "do not use without manual review." This is strictly better than ERROR (no artifact at all), which provides zero diagnostic value.
- D2 warid-597 timeout risk: D2's persist-with-FAIL fix will NOT resolve warid-597 if the actual failure mode is a timeout/hang before reaching validate_tree (as the audit's live verification suggests). warid-597 may require a separate investigation and fix (job-level timeout guard, OCR path performance). Mitigation: add warid-597 timeout investigation as a follow-up task; do not claim D2 fixes this document.
- D3 threshold reduction risk: lowering low_content_density from 500 to 150 chars/node weakens the gate's ability to catch genuinely over-fragmented trees. A degenerate tree with 1000 nodes at 151 chars/node would now pass. Mitigation: with D2's persist-with-FAIL, even trees below the threshold are persisted (as FAIL) rather than silently lost, so a too-permissive threshold causes MARGINAL-instead-of-FAIL misclassification rather than data loss. The threshold is env-configurable (`RFC029_MIN_CHARS_PER_NODE`) for tuning without code changes.
- D3 fed-33 incomplete recovery: lowering the threshold to 150 still rejects fed-33 at 54.3 chars/node. With D2, fed-33 will persist as FAIL (instead of ERROR), which is an improvement but not a full recovery to its Run-12 PASS state. Full PASS recovery requires investigating the node-expansion mechanism (502->2042 nodes) which is NOT addressed in this RFC. The root cause may be `split_oversized_leaf_nodes`, upstream Docling changes, or (if fed-33 traversed OCR retry) `_segment_table_nodes` -- determining which requires replaying the document with debug logging.
- D3 _segment_table_nodes primary-path wiring (deferred): GHV-TKV-Tarif's table segmentation stall (table stays merged in oversized node because `_segment_table_nodes` is not on the primary path) is NOT addressed in this RFC. Adding `_segment_table_nodes` to the primary path requires resolving the validate_tree ordering question (before vs after segmentation produces opposite density-gate outcomes) and is deferred to a follow-up task.
- D1 retry-always-wins risk: making the OCR retry unconditionally win when pre-density is None means a retry that produces worse output (more garbled, less content) than the pre-retry snapshot will still be accepted. Mitigation: add a minimum absolute quality check on the post-retry output (e.g., require at least N chars or N nodes) rather than purely short-circuiting to retry_wins=True.
- D4 title garble detection risk: adding title inspection to the garble gate may trigger false positives for documents with legitimate mixed-script titles (e.g., Arabic title containing English technical terms or abbreviations). Mitigation: tune the garble threshold for title text separately from body text, with a higher tolerance for mixed-script patterns in short title strings.
- D5 bidi coherence wiring risk: the function has been dead code since RFC-029; it has never been tested against the full corpus in a live pipeline run. Unknown false-positive rate may cause unexpected ERRORs or routing changes for documents that currently pass. Mitigation: run in audit-only mode first (log but do not act on bidi coherence failures) for one corpus cycle before enabling routing consequences.
- D6 judge calibration risk: the stability rule uses a 10% metric tolerance window (widened from the trace's "byte-identical" specification) to accommodate legitimate minor metric fluctuations across re-ingestions (e.g., Haftpflicht chars changed ~6% between runs due to non-deterministic Docling output). The 10% window may be too wide for small documents where a 10% char change is significant. Mitigation: require the judge to explicitly cite the stability rule when retaining a prior verdict, creating an audit trail for review. Consider tightening to 5% after one corpus cycle of data.
- Cross-decision interaction risk: D3 (threshold) and D2 (persist-with-FAIL) modify the validation and persistence paths. Implementation order: D3 first (threshold reduction), then D2 (persist-with-FAIL wiring). D0 and D1 are independent and can be implemented in parallel in Batch 2. Re-ingest after each batch to isolate effects.
- Regression reintroduction risk: several fixes effectively revert or weaken RFC-029 additions. If the original problems RFC-029 was designed to fix (e.g., degenerate tree acceptance, fence noise in flat output) reappear, the net effect may be zero or negative progress. Mitigation: track the specific documents RFC-029 improved (القرار التنظيمي, حقوق الإنسان, cabinet_resolution_21, world-stats-pocketbook) and verify they do not regress.

## Out of Scope

- [2] Vector-icon table cell extraction (Unfallversicherung): Pre-existing Docling TableFormer limitation across all runs, not an RFC-029 regression. Requires upstream Docling enhancement or VLM-based cell recovery -- large complexity, no quick fix.
- [3] D5c chart-page-detection heuristic never implemented: Missing feature from RFC-029 that was incorrectly marked complete. Large complexity requiring PictureItem synthesis and density heuristics. Should be tracked as a separate RFC.
- [12] Test trace entry: Invalid/placeholder trace data ('test: test'), not a real finding.
- [14] Federal Decree-Law No. 47 article ordering: Upstream Docling reading-order issue for multi-column/footnote layouts, not a tree-assembly defect. Output is byte-identical across Run 11-13, confirming no RFC-029 involvement. Requires Docling layout-model improvement.
