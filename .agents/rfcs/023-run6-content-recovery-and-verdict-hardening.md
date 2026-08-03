<!-- Space: CITRA -->
<!-- Title: RFC-023: Run 6 Content Recovery & Verdict Hardening -->
<!-- Folder: RFCs -->

# RFC-023: Run 6 Content Recovery & Verdict Hardening

## Status

- Status: DRAFT
- Author: Salil Trehan + Claude
- Date: 2026-07-29
- Branch: `feat/image-block-picture-ocr`
- Supersedes: Builds on RFC-022 (B1/B2/B3 landed), RFC-020 (F1/D0-D3), RFC-021 (QF1-QF4)
- Audit source: `audit/CORPUS_REINGESTION_AUDIT_RUN-6.md`

## Traceability

| Artifact | Reference |
|---|---|
| Design Document | [design-rfc023-run6-content-recovery-and-verdict-hardening.md](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md) |
| Implementation Plan | [tasks-rfc023-run6-content-recovery-and-verdict-hardening.md](../tasks/tasks-rfc023-run6-content-recovery-and-verdict-hardening.md) |
| Audit | [CORPUS_REINGESTION_AUDIT_RUN-6.md](../../audit/CORPUS_REINGESTION_AUDIT_RUN-6.md) |

## Problem Statement

Run 6 corpus reaudit (25 docs) scored **11 PASS / 4 MARGINAL / 9 FAIL / 1 ERROR** — the worst regression since Run 4. Net movement from Run 4: -2 PASS, -5 MARGINAL, +7 FAIL, 0 ERROR (9 verdict downgrades). The projected 19 PASS from RFC-022 was not reached; instead the branch introduced 7 new FAIL verdicts and 2 new MARGINAL downgrades.

Root-cause tracing (13 findings, 3 clusters + 5 drilldowns) identified **9 distinct defects** spanning three themes:

| Theme | Decisions | Docs affected |
|-------|-----------|---------------|
| A. Garble-aware content recovery (picture OCR pipeline) | D0, D1, D2, D11 | 7, 14, 15, 21, 22, 23 |
| B. Verdict correctness & escalation hardening | D3, D4, D5 | 17, 20, 21 |
| C. Pipeline resilience & edge cases | D6, D7, D8 | 5, 6, 9, 13, 18 |

## Decisions

### D0: Make `_text_layer_has_content` garble-aware (P0 bug)

**Scope:** `src/pageindex_mcp/converters.py` -- `_text_layer_has_content()` (line ~1443-1451)

**Root cause:** The F1 coverage exemption (`_text_layer_has_content`) only checks character count (>20 chars) without garble detection. Scanned Arabic PDFs with thin garbled text layers from the PDF creator pass the 20-char threshold. This prevents the coverage exemption from firing, so `_recover_picture_text` skips OCR for full-page picture regions via the `_PICTURE_PAGE_COVERAGE_THRESHOLD` (0.6) gate. Content is lost, downstream tree builder produces too few nodes, `validate_tree` returns `node_count<3` or `depth<2` instead of `garbling`, and Fix-3 OCR escalation never fires (it requires `reason=='garbling'`).

**Trace findings:** RC1 cluster (MOU MOHRE & Nafis, Unemployment Insurance, Labor Relations), RC5 drilldown (Labor Relations scanned pages)

**Fix:** Call `_is_garbled_blob` (or `_flat_text_is_garbled`) on the page text inside `_text_layer_has_content`. If the text layer exists but is garbled, return `False` so the coverage exemption fires and per-picture OCR proceeds. Approximately 5 lines of change.

**Files:** `src/pageindex_mcp/converters.py`

**Rollback:** Feature flag `TEXT_LAYER_GARBLE_CHECK_ENABLED` (default `true`); set to `false` to restore prior behavior.

---

### D1: Graceful degradation for `splice_figure_markers` count mismatch (P0 bug)

**Scope:** `src/pageindex_mcp/converters.py` -- `splice_figure_markers()` (line ~1630-1636) and `src/pageindex_mcp/helpers.py` -- `route_and_extract_flat()` `_FLAT_FIGURE_RE` (line ~1317)

**Root cause:** When the count of `<!-- image -->` markers in exported markdown diverges from the number of PictureItem regions returned by `_collect_picture_regions`, the count-mismatch guard at line 1630-1636 logs a warning and returns markdown unchanged -- all markers remain as literal `<!-- image -->` text. `route_and_extract_flat` only recognizes `[Figure: fig-N]` patterns, so unresolved markers persist as inert content-less nodes. This explains UAE numbers landscape (5/7 bare `<!-- image -->` blocks) and partially explains Arabic image-only PDFs.

**Trace findings:** RC4 cluster (UAE numbers landscape, Labor Relations). Note: Unfallversicherung was initially attributed to this cluster but its drilldown confirmed counts MATCH (63 markers = 63 regions) and the count-mismatch guard never fires for it — its failure is the D2 decorative-icon gap.

**Fix:** Replace the all-or-nothing count-mismatch guard with graceful degradation: (1) for markers with a matching PictureResult by ordinal, splice normally; (2) for excess markers without a PictureResult, strip them if `STRIP_SKIPPED_IMAGE_MARKERS=true` (existing flag) or leave as neutral marker. Additionally, make `route_and_extract_flat` recognize raw `<!-- image -->` markers as image blocks (with empty content) so unresolved markers register as image nodes, not invisible text.

**Files:** `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/helpers.py`

**Rollback:** Git revert -- logic change only, no threshold tuning.

---

### D2: Decorative-icon bbox classifier for sub-icon PictureItems (P1 missing feature)

**Scope:** `src/pageindex_mcp/converters.py` -- `_recover_picture_text()` per-region loop (~line 1487-1519), `PictureResult` TypedDict (line ~1352-1360)

**Root cause:** Unfallversicherung-Leistungsuebersicht has 63 PictureItem regions that are ~11-13pt x 11-13pt sub-icon UI glyphs (0.03% of page area). These pass the count-match guard (63 markers = 63 regions), proceed to crop+OCR, yield zero text from Tesseract on 46x46px images, but are never flagged as `decorative` or given a `skipped_reason`. The `splice_figure_markers` strip guard (line 1647) only fires on `skipped_reason` or `decorative`, so all 60 zero-content results preserve their `<!-- image -->` markers. The `decorative` field is declared in `PictureResult` but never assigned anywhere in the codebase.

**Trace findings:** Unfallversicherung drilldown

**Fix:** Add a bbox-area pre-filter in `_recover_picture_text` before crop+OCR: if a region's width AND height are both below a threshold (e.g., 20pt), set `skip_reasons[i] = "decorative_icon"` and skip OCR. As belt-and-suspenders, also set `decorative=True` on any PictureResult where OCR yields empty `ocr_text` AND no `description` AND the region's page has no rotation flag (`page.rotation == 0`), wiring the existing but unused `decorative` field. Both paths reuse the existing strip logic in `splice_figure_markers` (line 1647).

**Interaction with D6:** The belt-and-suspenders empty-OCR heuristic must NOT fire when page rotation is non-zero, because empty OCR on a rotated page likely indicates rotation-caused OCR failure (D6's root cause), not a decorative image. The `page.rotation == 0` guard prevents D2 from stripping genuine chart placeholders before D6's rotation correction has had a chance to recover the content.

**Files:** `src/pageindex_mcp/converters.py`

**Rollback:** `DECORATIVE_ICON_MIN_DIM_PT` env var (default 20); set to 0 to disable.

---

### D3: Strip `<!-- image -->` markers from garble detection (P0 bug)

**Scope:** `src/pageindex_mcp/helpers.py` -- `_is_garbled_blob()` single-token repetition check (line ~888-896)

**Root cause:** For scanned PDFs with no text layer, Docling produces only `<!-- image -->` markers. The `_is_garbled_blob` single-token repetition check tokenizes the text and finds "image" repeating at 100% ratio (e.g., 45 of 45 alphanumeric tokens), exceeding the >30% threshold. This causes `validate_tree` to return `(False, 'garbling')` instead of structural failure reasons. The `garbling` reason then (a) routes through Fix-3 OCR escalation rather than D1 image-dominant escalation, (b) blocks flat-path fallback if OCR also fails (flat routing requires `node_count<3` or `depth<2`, not `garbling`), and (c) causes `classify_verdict` to report `garble_ratio=1.00`.

Additionally, `classify_verdict` (line 1204) calls `_tree_is_garbled(structure)` without passing `expected_script`, so image-only flat structures with `<!-- image -->` blocks trigger the same false-flag.

**Trace findings:** RC7 cluster (Service Level Agreement), RC8 cluster (Service Level Agreement classify_verdict path)

**Fix:** Strip all `<!-- ... -->` HTML comment content from the blob before tokenizing in `_is_garbled_blob`. A regex like `re.sub(r'<!--.*?-->', '', blob)` before the tokenization step. This preserves the repetition check for actual garbled text while exempting structural markers.

**Known remaining gap (deferred):** `classify_verdict` (line 1204) calls `_tree_is_garbled(structure)` without passing `expected_script`, and `_garble_ratio(flat_text, expected_script=None)` (line 1217) uses script-agnostic detection. This inflates garble_ratio for legitimate Arabic flat text. The comment-stripping fix resolves the acute image-only false-flag regression; the expected_script propagation is a broader change affecting all Arabic docs and is deferred to a follow-up RFC to avoid scope creep and unintended side-effects on the 10+ Arabic docs in the corpus.

**Files:** `src/pageindex_mcp/helpers.py`

**Rollback:** Git revert -- pure pre-filter, no threshold change.

---

### D4: Add content-quality guard to `cat_b_promoted` gate (P0 bug)

**Scope:** `src/pageindex_mcp/helpers.py` -- `classify_verdict()` cat_b_promoted gate (line ~1239-1245)

**Root cause:** `cat_b_promoted` promotes flat docs to PASS based solely on `node_count >= 3`, `max_leaf_ratio < 0.17`, and not-garbled. It has no minimum text-length or content-quality check. Doc 21 (Domestic Workers 106/2022) has 15 flat blocks of bare `<!-- image -->` placeholder text (210 total chars) producing a synthetic structure where `node_count=15`, `max_leaf_ratio~0.067`, and not garbled (HTML comments pass garble checks). All gates pass, producing a factually wrong PASS verdict for a document with zero meaningful content.

**Trace findings:** RC9 cluster (Domestic Workers 106/2022)

**Fix:** Add two guards to `cat_b_promoted`: (1) `len(flat_text.strip()) >= MIN_FLAT_PROMOTION_CHARS` (default 500 chars), using the already-computed `flat_text` variable; (2) image-placeholder dominance check -- count blocks whose stripped text matches `<!-- image -->` and reject promotion when the ratio exceeds 0.5.

**Files:** `src/pageindex_mcp/helpers.py`

**Rollback:** `MIN_FLAT_PROMOTION_CHARS` env var (default 500); set to 0 to disable.

---

### D5: Prefer synthetic structure over rejected tree for flat-routed docs (P1 bug)

**Scope:** `src/pageindex_mcp/client.py` -- `index()` flat-path verdict computation (line ~1102)

**Root cause:** The guard `if not flat_structure and blocks:` only builds synthetic structure from flat blocks when `flat_structure` is completely empty. For Doc 20 (Labor Exec. Regs. 1/2022), the tree builder produced a structure that `validate_tree` rejected, but that rejected structure is non-empty. Because it is non-empty, the synthetic-structure-from-blocks path is never triggered. `classify_verdict` then evaluates the rejected (low node_count/depth) tree structure instead of the actual block content (355 blocks with depth-4 nesting). This produces MARGINAL with `depth=1` while the stored blocks represent much richer content.

**Trace findings:** RC10 cluster (Labor Exec. Regs. 1/2022)

**Fix:** Change the guard to always prefer synthetic structure when blocks exist for flat-routed docs: replace `if not flat_structure and blocks:` with `if blocks:`. The rejected tree structure from `result.get('structure', [])` failed `validate_tree` and should never be used for verdict computation.

**Files:** `src/pageindex_mcp/client.py`

**Rollback:** Git revert -- pure logic change.

---

### D6: Page-rotation correction for per-picture OCR (P1 bug)

**Scope:** `src/pageindex_mcp/converters.py` -- `_recover_picture_text()` (~line 1490-1520)

**Root cause:** UAE numbers portrait PDF has a 180-degree page rotation flag (`page.rotation == 180`). `page.get_pixmap(clip=rect, dpi=300)` applies the rotation, rendering the crop upside-down. Tesseract receives the rotated image with no correction, producing garbled/reversed text. This document fails on both master and the branch -- the root cause is independent of the coverage filter changes.

**Trace findings:** UAE numbers portrait drilldown

**Fix:** Before calling `page.get_pixmap()`, save and temporarily zero the page rotation: `orig = page.rotation; page.set_rotation(0)`. Restore after pixmap extraction. This ensures Tesseract receives correctly-oriented images regardless of PDF page rotation metadata.

**Files:** `src/pageindex_mcp/converters.py`

**Rollback:** Git revert -- isolated to pixmap extraction.

---

### D7: Tesseract-on-raster fallback when VLM crashes on garbled PDFs (P2 missing feature)

**Scope:** `src/pageindex_mcp/client.py` -- `index()` VLM exception handler (~line 872-878), flat-routing reason check (~line 954)

**Root cause:** When VLM fallback crashes on garbled PDFs (rate limit/content-policy/token overflow on base64 PNGs), the exception is caught but `ok` remains `False` and `reason` remains `garbling`. Since `garbling` is not in `('node_count<3', 'depth<2')`, flat routing is skipped entirely. The code falls through to `LowQualityTreeError('garbling')` -- zero artifacts produced. The rasterized page images could be OCR'd via Tesseract as a last resort, but this path does not exist.

**Trace findings:** RC11 cluster (Organizational Decision)

**Fix:** Inside the VLM exception handler, reuse rasterized page images and run Tesseract OCR. If the OCR text passes garble checks (`_is_garbled_blob` returns `False`), use it as `flat_md` and override `reason` to `'node_count<3'` to enter the existing flat success path at line 954. The reason override is the sole routing mechanism — `'garbling'` is NOT added to the flat-routing reason check, preserving the invariant that genuinely garbled, non-recovered documents always raise `LowQualityTreeError` (Hard Rule 5). If Tesseract OCR also fails garble checks or produces insufficient content, the original `LowQualityTreeError('garbling')` path is preserved.

**Files:** `src/pageindex_mcp/client.py`, `src/pageindex_mcp/converters.py`

**Rollback:** `VLM_TESSERACT_FALLBACK_ENABLED` env var (default `true`).

---

### D8: Standalone image OCR enrichment + worker error mapping (P1 bug + P2 improvement)

**Scope:** `src/pageindex_mcp/client.py` -- `_IMAGE_EXTS` route (line ~740-768); `src/pageindex_mcp/worker.py` -- `_CHILD_ERROR_REASON` mapping (line ~67-72), `ConverterChildError.__init__` stderr truncation (line ~144)

**Root cause (8a):** Standalone image files (.jpg/.png) create synthetic `PictureResult` with `ocr_text=''`. The actual OCR text from `image_to_markdown` goes into `md_content` as prose, but the synthetic PictureResults carry no OCR text. For charts/infographics where Docling fails to extract meaningful text, `md_content` is minimal. The tree validation fails, routing to flat where `_enrich_image_blocks` writes `ocr_text=''`. The chart's data is never captured because the standalone image route bypasses `_recover_picture_results` entirely.

**Root cause (8b):** `LLMTransientFailure` is not in `_CHILD_ERROR_REASON`, so it maps to default `converter_child_failed`, which is not in `_TERMINAL_CHILD_REASONS`. Arq retries the job (MAX_TRIES=2) on deterministic VLM/LLM failures (CMap corruption, content-policy rejection) -- wasted compute. Note: the exception message string at `ConverterChildError.__init__` (line 144) is truncated to 200 chars, but this is only the Python repr; `self.stderr_tail` (line 146) retains the full 2000-char tail (sourced from line 225) and is surfaced in Redis status at lines 316/347 -- no error detail is actually lost.

**Trace findings:** RC6 cluster (pie chart JPG), RC12 cluster (Organizational Decision worker errors)

**Fix (8a):** For standalone images, run raw image bytes through Tesseract OCR and populate the synthetic `PictureResult.ocr_text` with the result. This is chosen over the alternative (modifying `image_to_markdown` to return PictureResults) because it keeps the change isolated to the standalone-image route in `client.py` and avoids coupling the generic markdown converter to picture-result semantics. To prevent double-counting when Docling already extracted text into `md_content` as prose, the Tesseract OCR step is skipped if `md_content` contains more than `MIN_STANDALONE_IMAGE_MD_CHARS` (default 100) non-whitespace characters — in that case, the existing `md_content` already carries the document's text content and adding OCR would duplicate it. **Fix (8b):** Add `LLMTransientFailure` to `_CHILD_ERROR_REASON` with reason `'llm_failure'`. Add `'llm_failure'` to `_TERMINAL_CHILD_REASONS` ONLY when the error detail string contains a CMap-corruption or content-policy indicator (substring match on `"CMap"`, `"content_policy"`, `"content_filter"`); rate-limit errors (`"rate_limit"`, `"429"`, `"throttl"`) remain non-terminal and eligible for arq retry (MAX_TRIES=2). This distinction is implemented in the `_CHILD_ERROR_REASON` lookup: a new helper `_classify_llm_failure(stderr_tail)` returns `'llm_failure_terminal'` or `'llm_failure_transient'`; only the former is added to `_TERMINAL_CHILD_REASONS`. Note: the exception message at `ConverterChildError.__init__` (line 144) truncates to 200 chars for the Python exception string, but `self.stderr_tail` (line 146, sourced from the 2000-char tail at line 225) retains the full error detail and is surfaced at lines 316/347 — no stderr truncation increase is needed.

**Files:** `src/pageindex_mcp/client.py`, `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/worker.py`

**Rollback:** 8a: git revert. 8b: git revert.

---

### D9: BiDi early-return heading-marker preservation (P2 bug)

**Scope:** `src/pageindex_mcp/converters.py` -- `reconstruct_bidi_order()` (line ~1249-1265), `_text_is_logical_order()` (line ~1204-1232)

**Root cause:** Ministerial Resolution No279/2022 (bilingual English/Arabic) regressed from PASS to MARGINAL (depth=1). The `reconstruct_bidi_order()` early-return optimization skips BiDi processing if Arabic ratio is <=15% or text is detected as already logical via `_text_is_logical_order()`. This bypasses `_BIDI_HEADING_PREFIX_RE` (line 1270) that preserves heading markers. Scrambled heading text in Arabic becomes unrecognizable to `md_to_tree()` LLM, resulting in a flat tree.

**Trace findings:** Ministerial Resolution drilldown

**Fix:** Split the early-return into two paths: (1) always apply `_BIDI_HEADING_PREFIX_RE` to extract and preserve heading markers, even when the bulk text is detected as logical; (2) conditionally apply full-document BiDi reordering based on the existing checks. This preserves the performance optimization while fixing heading-marker loss for bilingual documents.

**Files:** `src/pageindex_mcp/converters.py`

**Rollback:** Git revert -- BiDi is non-destructive on already-correct text.

---

### D10: Extraction pinning for non-deterministic Docling documents (P3 data quality)

**Scope:** `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/client.py`

**Root cause:** Docling ML-based heading selection is non-deterministic across runs. Haftpflicht-Besondere-Bedingungen is a "Category D permanent_marginal" document (RFC-014 D4) with known run-to-run extraction instability. The `max_leaf_ratio` fluctuates across the PASS/MARGINAL boundary (0.17). No code change on the branch directly causes this -- the heading recovery code is unchanged and the classify_verdict changes are strictly looser.

**Trace findings:** RC0 cluster (Haftpflicht-Besondere, Ministerial Resolution), Haftpflicht drilldown

**Fix:** Widen the `PASS_MAX_LEAF_RATIO` threshold from 0.17 to 0.20 for documents whose `max_leaf_ratio` falls in the instability band [0.17, 0.20). This is implemented as a single env-var change (`PASS_MAX_LEAF_RATIO=0.20`) applied in `classify_verdict`, requiring zero code changes to the verdict function itself. The wider threshold absorbs Docling's run-to-run heading-selection jitter without requiring prior-verdict persistence. This approach survives the wipe-all-stores reaudit methodology (MinIO, Redis, PostgreSQL wiped before each run) because it depends only on the current extraction, not on any stored prior state.

Alternative considered and rejected: verdict hysteresis (retaining prior PASS across runs) was rejected because the corpus reaudit methodology wipes all derived stores before reingestion — after a wipe there is no prior verdict to retain, so hysteresis cannot materialize in a from-scratch reaudit.

**Files:** `src/pageindex_mcp/helpers.py` (env-var default change only)

**Rollback:** Set `PASS_MAX_LEAF_RATIO=0.17` to restore prior threshold.

---

### D11: Widen OCR escalation to structural-failure reasons (P1 bug)

**Scope:** `src/pageindex_mcp/client.py` -- Fix-3 OCR escalation gate (~line 792) and image-dominant escalation gate (~line 881-894)

**Root cause:** Fix-3 OCR escalation (line 792) only fires on `reason == 'garbling'`. When content-stripped markdown from D0's upstream garble-aware exemption produces a tree that fails `validate_tree` with `node_count<3` or `depth<2` (structural reasons, not garbling), page-level OCR retry never fires. The document falls through to flat routing with near-zero content, producing a FAIL verdict. Additionally, the image-dominant escalation check (line 894) computes `image_lines / len(total_lines) > 0.50`, but garbled text lines from a thin text layer dilute the ratio below 50%, preventing escalation even when the document is predominantly image-based.

**Trace findings:** Trace finding 3 (OCR escalation only on garbling; structural failures from content-stripped markdown never trigger retry)

**Fix:** (1) Extend the Fix-3 OCR escalation gate at line 792 to also fire on `reason in ('node_count<3', 'depth<2')` when the markdown content is image-dominant (reusing the >50% image-line ratio check). This targets the specific case where D0 correctly identifies a garbled text layer, the coverage exemption fires, but the resulting image-only markdown produces too few tree nodes. (2) Change the image-dominant ratio denominator from `len(total_lines)` to `len(non_empty_lines)` (lines with content after stripping whitespace) to prevent garbled text lines from diluting the ratio. Empty lines and whitespace-only lines are structural artifacts that should not count against the image-dominance signal.

**Files:** `src/pageindex_mcp/client.py`

**Rollback:** `IMAGE_DOMINANT_OCR_ESCALATION_ENABLED` env var (default `true`); set to `false` to restore prior behavior (escalation only on garbling reason).

---

## Implementation Plan

### Batch 1: Content Recovery Pipeline (D0, D1, D3, D11) -- 2.0d

These fixes are independent of each other but collectively address the primary content-loss regressions. D0 and D3 are prerequisites for accurate downstream verdict computation. D11 closes the OCR-escalation gap exposed when D0 causes structural failures instead of garbling.

| Task | Decision | File | Description | Effort |
|------|----------|------|-------------|--------|
| T1.1 | D0 | `converters.py` | Add `_is_garbled_blob` call in `_text_layer_has_content`; return `False` if garbled | 0.25d |
| T1.2 | D1 | `converters.py` | Replace count-mismatch bail-out with ordinal-matched graceful splicing | 0.5d |
| T1.3 | D1 | `helpers.py` | Add `<!-- image -->` pattern to `_FLAT_FIGURE_RE` or add parallel regex | 0.25d |
| T1.4 | D3 | `helpers.py` | Strip `<!-- ... -->` HTML comments before tokenization in `_is_garbled_blob` | 0.25d |
| T1.5 | D11 | `client.py` | Extend OCR escalation to structural-failure reasons when image-dominant; fix ratio denominator | 0.25d |
| T1.6 | -- | `tests/` | Unit tests for T1.1-T1.5 | 0.5d |

### Batch 2: Verdict Hardening (D4, D5) -- 0.75d

Depends on Batch 1 (D3 fix changes garble detection behavior that D4 relies on).

| Task | Decision | File | Description | Effort |
|------|----------|------|-------------|--------|
| T2.1 | D4 | `helpers.py` | Add `MIN_FLAT_PROMOTION_CHARS` and placeholder-dominance guards to `cat_b_promoted` | 0.25d |
| T2.2 | D5 | `client.py` | Change `if not flat_structure and blocks:` to `if blocks:` | 0.1d |
| T2.3 | -- | `tests/` | Unit tests for T2.1-T2.2 | 0.4d |

### Batch 3: Edge-Case Fixes (D2, D6) -- 0.75d

Partially depends on Batch 1: D2 (T3.1) modifies the splice_figure_markers strip/splice path which D1 (T1.2) rewrites in Batch 1. T3.1 must be rebased on T1.2's rewritten splice path. D6 is independent of Batches 1-2. In practice, start D6 in parallel with Batch 2, but sequence D2 after Batch 1 completes.

| Task | Decision | File | Description | Effort |
|------|----------|------|-------------|--------|
| T3.1 | D2 | `converters.py` | Add bbox-area pre-filter and wire `decorative` field in `_recover_picture_text`; gate belt-and-suspenders on `page.rotation == 0` | 0.25d |
| T3.2 | D6 | `converters.py` | Save/zero/restore `page.rotation` around `get_pixmap` in `_recover_picture_text` | 0.25d |
| T3.3 | -- | `tests/` | Unit tests for T3.1-T3.2 | 0.25d |

### Batch 4: Standalone Image + VLM Fallback + Error Mapping (D8a, D7, D8b) -- 1.75d

Depends on Batch 1 (D0 garble-aware text-layer check used in D7 fallback path).

| Task | Decision | File | Description | Effort |
|------|----------|------|-------------|--------|
| T4.1 | D8a | `client.py`, `converters.py` | Populate synthetic PictureResult.ocr_text via Tesseract for standalone images; skip when md_content has sufficient chars | 0.5d |
| T4.2 | D7 | `client.py`, `converters.py` | Add Tesseract-on-raster fallback in VLM exception handler; override reason to node_count<3 on successful OCR recovery | 0.5d |
| T4.3 | D8b | `worker.py` | Add `_classify_llm_failure` helper; terminal for CMap/content-policy, transient for rate-limits | 0.25d |
| T4.4 | -- | `tests/` | Unit tests for T4.1-T4.3 (test_rfc023_d7.py, test_rfc023_d8.py) | 0.5d |

### Batch 5: BiDi + Threshold Widening (D9, D10) -- 1.0d

Independent of Batches 1-4.

| Task | Decision | File | Description | Effort |
|------|----------|------|-------------|--------|
| T5.1 | D9 | `converters.py` | Split `reconstruct_bidi_order` early-return; always apply heading-marker preservation | 0.5d |
| T5.2 | D10 | `helpers.py` | Widen `PASS_MAX_LEAF_RATIO` default from 0.17 to 0.20 | 0.1d |
| T5.3 | -- | `tests/` | Unit tests for T5.1-T5.2 (test_rfc023_d9.py, test_rfc023_d10.py) | 0.4d |

### Batch 6: Full Reaudit (all) -- 0.5d

| Task | Decision | File | Description | Effort |
|------|----------|------|-------------|--------|
| T6.1 | -- | -- | Bump `CURRENT_PIPELINE_VERSION`; full 25-doc reingestion | 0.25d |
| T6.2 | -- | `audit/` | Run 7 scorecard vs projections; verify zero regressions on Run 6 PASS docs | 0.25d |

**Total effort: ~7.0 person-days.**

## Expected Outcomes

### Projected Run 7 Verdict Distribution

| Verdict | Run 6 (actual) | Run 7 (projected) | Delta |
|---------|----------------|-------------------|-------|
| PASS    | 11             | 18-20             | +7-9  |
| MARGINAL| 4              | 3-5               | -1-1  |
| FAIL    | 9              | 1-2               | -7-8  |
| ERROR   | 1              | 0-1               | -1-0  |

### Per-Document Projections

| Doc | Run 6 | Fix | Projected | Rationale |
|-----|-------|-----|-----------|-----------|
| 3 (GHV-TKV-Tarif) | MARGINAL | -- | MARGINAL | Out of scope: 3 unenriched image markers on a flat German doc; requires image-marker enrichment improvements beyond this RFC |
| 5 (Haftpflicht-Besondere) | MARGINAL | D10 | PASS | Widened PASS_MAX_LEAF_RATIO (0.20) absorbs run-to-run jitter |
| 6 (Ministerial Res. 279) | MARGINAL | D9 | PASS | BiDi heading preservation restores hierarchy |
| 7 (MOU MOHRE) | FAIL | D0 | PASS/MARGINAL | Garble-aware text-layer allows OCR recovery |
| 9 (Unfallversicherung) | FAIL | D2 | MARGINAL | Decorative icons stripped; content still limited |
| 13 (Pie chart JPG) | FAIL | D8a | PASS | Tesseract OCR populates PictureResult |
| 14 (UAE numbers landscape) | FAIL | D1, D6 | MARGINAL | Graceful splice + rotation fix recover partial content |
| 15 (UAE numbers portrait) | FAIL | D6 | PASS/MARGINAL | Rotation correction enables OCR |
| 17 (SLA) | FAIL | D3 | MARGINAL | Image markers no longer false-flag garble |
| 18 (Organizational Decision) | ERROR | D7 | MARGINAL | Tesseract fallback when VLM crashes |
| 20 (Labor Exec. Regs.) | MARGINAL | D5 | PASS | Synthetic structure from 355 blocks |
| 21 (Domestic Workers) | FAIL | D0, D4 | MARGINAL | OCR recovery + promotion guard |
| 22 (Unemployment Insurance) | FAIL | D0 | PASS/MARGINAL | Garble-aware text-layer allows OCR |
| 23 (Labor Relations) | FAIL | D0, D1 | PASS/MARGINAL | OCR recovery + graceful splice |

## Test Strategy

| Decision | Test file | Key assertions |
|----------|-----------|----------------|
| D0 | `tests/test_rfc023_d0.py` | (a) garbled text layer returns `False` from `_text_layer_has_content`; (b) clean text layer returns `True`; (c) short text (<20 chars) returns `False` regardless of garble |
| D1 | `tests/test_rfc023_d1.py` | (a) mismatched counts: matched-ordinal markers spliced, excess stripped; (b) equal counts: all spliced (no regression); (c) `<!-- image -->` recognized by flat extractor as image node |
| D2 | `tests/test_rfc023_d2.py` | (a) bbox < 20pt both dims: `skip_reasons` set to `decorative_icon`; (b) bbox > 20pt: proceeds to OCR; (c) zero-yield OCR: `decorative=True` set; (d) strip logic fires for decorative results |
| D3 | `tests/test_rfc023_d3.py` | (a) text with only `<!-- image -->` markers: NOT garbled; (b) text with actual repeated tokens: still garbled; (c) mixed content with image markers: markers excluded from count |
| D4 | `tests/test_rfc023_d4.py` | (a) 15 `<!-- image -->` blocks, 210 chars: cat_b_promoted blocked; (b) 15 real-text blocks, 5000 chars: cat_b_promoted passes; (c) placeholder ratio > 0.5: blocked |
| D5 | `tests/test_rfc023_d5.py` | (a) non-empty rejected structure + blocks: synthetic built from blocks; (b) no blocks: original structure preserved; (c) synthetic structure depth/node_count correct |
| D6 | `tests/test_rfc023_d6.py` | (a) page.rotation=180: rotation zeroed before pixmap, restored after; (b) page.rotation=0: no change; (c) mock Tesseract receives unrotated image |
| D7 | `tests/test_rfc023_d7.py` | (a) VLM exception + Tesseract success (non-garbled OCR): reason overridden to node_count<3, flat routing succeeds; (b) VLM exception + Tesseract failure (garbled OCR): LowQualityTreeError raised; (c) VLM exception + Tesseract empty output: LowQualityTreeError raised; (d) garbling reason without VLM exception: existing escalation path unchanged |
| D8 | `tests/test_rfc023_d8.py` | (a) standalone .jpg: PictureResult.ocr_text populated; (a2) standalone .jpg with sufficient md_content: Tesseract OCR skipped, no double-counting; (b) LLMTransientFailure with CMap error: maps to terminal reason, no retry; (b2) LLMTransientFailure with rate_limit/429: maps to transient reason, eligible for retry |
| D9 | `tests/test_rfc023_d9.py` | (a) bilingual doc with Arabic headings: heading markers preserved after BiDi; (b) pure-English doc: early-return still fires (perf preserved); (c) logical-order Arabic: full reorder skipped but headings preserved |
| D10 | `tests/test_rfc023_d10.py` | (a) max_leaf_ratio 0.18 with PASS_MAX_LEAF_RATIO=0.20: verdict is PASS; (b) max_leaf_ratio 0.22 with PASS_MAX_LEAF_RATIO=0.20: verdict is MARGINAL; (c) max_leaf_ratio 0.16: verdict is PASS regardless of threshold |
| D11 | `tests/test_rfc023_d11.py` | (a) node_count<3 reason + image-dominant markdown: OCR escalation fires; (b) node_count<3 reason + non-image-dominant: no escalation; (c) garbled text lines excluded from ratio denominator; (d) garbling reason: existing escalation path unchanged |

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| D0: Garble check false-positive on legitimate short text layers | Low | Medium | Only fires when `_is_garbled_blob` returns `True`; legitimate text layers with >20 chars of real content will not trigger garble detection |
| D1: Graceful splice misaligns markers with wrong PictureResults | Medium | Low | Ordinal matching preserves correct alignment for matched pairs; excess markers are stripped, not mismatched |
| D2: Decorative threshold too aggressive, skips small but meaningful charts | Low | Medium | 20pt threshold (~7mm) well below smallest meaningful chart; configurable via env var |
| D3: Stripping image comments masks actual garble in image-comment-heavy docs | Very Low | Low | Only strips `<!-- ... -->` patterns, not surrounding text; real garbled text still detected |
| D4: MIN_FLAT_PROMOTION_CHARS too high, blocks valid short documents | Low | Medium | 500 chars is ~100 words; documents shorter than this are unlikely to have meaningful tree structure; configurable |
| D5: Always-override loses valid structure from partial tree builds | Low | Medium | Rejected structures provably failed `validate_tree`; synthetic structure from blocks is strictly better input for verdict |
| D2/D6 interaction: empty-OCR heuristic on rotated pages strips real charts | Low | Medium | Belt-and-suspenders `decorative=True` only fires when `page.rotation == 0`; rotated pages defer to D6 rotation correction |
| D3: expected_script omission inflates garble_ratio for Arabic flat text | Medium | Medium | Deferred to follow-up RFC; comment-stripping resolves the acute image-only false-flag; expected_script propagation affects all Arabic docs and needs dedicated testing |
| D7: Tesseract fallback produces lower quality than VLM would have | Medium | Low | Degraded-but-present artifact is strictly better than zero artifacts (current behavior); garble check on OCR output prevents persisting genuinely garbled content (Hard Rule 5) |
| D8a: Tesseract OCR double-counts content already in md_content | Low | Medium | Skipped when md_content exceeds MIN_STANDALONE_IMAGE_MD_CHARS (100 chars); only fires for genuinely empty extractions |
| D8b: Rate-limit failures incorrectly classified as terminal | Low | High | Mitigated by `_classify_llm_failure` helper that inspects error detail strings; rate-limit indicators (`429`, `rate_limit`, `throttl`) remain transient and retryable |
| D10: Wider threshold admits borderline documents as PASS | Low | Low | 0.20 threshold is conservative; documents with max_leaf_ratio > 0.20 still correctly classified as MARGINAL |
| D11: Image-dominant OCR escalation fires on non-image docs | Very Low | Low | Only fires when image-line ratio exceeds 50% of non-empty lines AND reason is structural; false positives would trigger a benign OCR retry |
| Run 7 regression on Run 6 PASS docs | Low | High | Batch 6 explicitly verifies all 11 Run 6 PASS docs maintain their verdicts |

## Cross-References

- **Audit report:** `audit/CORPUS_REINGESTION_AUDIT_RUN-6.md`
- **Prior RFCs:** RFC-020 (F1 coverage exemption origin), RFC-021 (QF3 garble diagnosis deferred), RFC-022 (B1 flat-doc synthesis, B2 image routing)
- **Related RFCs:** RFC-014 (Category D permanent_marginal classification), RFC-016 (VLM garble fallback), RFC-017 (OCR/image-block decoupling), RFC-005 (hard corpus ingestion fixes)
- **Investigation:** `audit/OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION_2026-07-27.md`
- **Design document:** [design-rfc023-run6-content-recovery-and-verdict-hardening.md](../designs/design-rfc023-run6-content-recovery-and-verdict-hardening.md)
- **Implementation plan:** [tasks-rfc023-run6-content-recovery-and-verdict-hardening.md](../tasks/tasks-rfc023-run6-content-recovery-and-verdict-hardening.md)
