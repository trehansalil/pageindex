<!-- Space: CITRA -->
<!-- Title: RFC-024: Run 7 Verdict Stability & Recovery Gaps -->
<!-- Folder: RFCs -->

# RFC-024: Run 7 Verdict Stability & Recovery Gaps

## Status

- Status: DRAFT
- Author: Salil Trehan + Claude
- Date: 2026-07-30
- Branch: TBD
- Supersedes: Builds on RFC-023 (D0-D11 landed), RFC-022 (B1/B2/B3 landed). **D5 explicitly supersedes RFC-023 D7 test case (d)** -- see D5 Supersession note.
- Audit source: `audit/CORPUS_REINGESTION_AUDIT_RUN-7.md`

## Traceability

| Artifact | Reference |
|---|---|
| Design Document | [design-rfc024-run7-verdict-stability-and-recovery-gaps.md](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md) |
| Implementation Plan | [tasks-rfc024-run7-verdict-stability-and-recovery-gaps.md](../tasks/tasks-rfc024-run7-verdict-stability-and-recovery-gaps.md) |
| Audit | [CORPUS_REINGESTION_AUDIT_RUN-7.md](../../audit/CORPUS_REINGESTION_AUDIT_RUN-7.md) |

## Problem Statement

Run 7 corpus reaudit (25 docs) shows residual instability: verdicts that oscillate between runs due to extraction non-determinism, content recovery pipelines that crash on single-region failures losing all results for a document, and splitter/ordinal patterns that do not recognize MOU/decree document structures common in the Arabic legal corpus. Additionally, the chart/infographic content recovery path discards readable PDF text-layer content when Docling misclassifies it as PictureItems, and the D7 Tesseract fallback shares a single point of failure with the VLM rasterization backend.

Root-cause tracing (9 findings: 2 clusters + 7 drilldowns) identified **7 distinct defects** spanning four themes:

| Theme | Decisions | Docs affected |
|-------|-----------|---------------|
| A. Verdict stability (threshold jitter) | D0 | 8 (Reitlehrer) |
| B. Content recovery pipeline gaps | D1, D2, D4, D5 | 14 (UAE landscape), 18 (Organizational Decision), chart/infographic PDFs |
| C. Splitter coverage for MOU/decree docs | D3 | 7 (MOU MOHRE), 21 (Domestic Workers 106/2022) |
| D. Audit tooling measurement bug | D6 | 3 (GHV-TKV-Tarif), 9 (Unfallversicherung) |

## Decisions

### D0: Widen PASS_MAX_LEAF_RATIO default from 0.20 to 0.30 (P1 bug)

**Scope:** `src/pageindex_mcp/helpers.py` -- `classify_verdict()` PASS gate (line ~1229-1236)

**Root cause:** RFC-023 D10 widened PASS_MAX_LEAF_RATIO from 0.17 to 0.20 to absorb Doc 5's jitter, but Doc 8 (Reitlehrer, 10 nodes, depth 2) has max_leaf_ratio that jitters between ~0.17 and 0.2571 across runs due to Docling's non-deterministic heading selection. The hard threshold comparison at line 1233 (`max_leaf_ratio < _pass_max_leaf`) flips the verdict from PASS to MARGINAL with no hysteresis or tolerance band.

**Trace finding:** Cluster finding -- PASS_MAX_LEAF_RATIO threshold oscillation

**Fix:** Widen the `PASS_MAX_LEAF_RATIO` env-var default from 0.20 to 0.30. This absorbs Doc 8's observed jitter (0.2571) with margin. Risk is low: `max_leaf_ratio > 0.75` is already a hard FAIL gate, and documents with genuinely lopsided trees (>0.30) will still correctly land MARGINAL. The env var already exists for tuning.

**Files:** `src/pageindex_mcp/helpers.py`

**Rollback:** Set `PASS_MAX_LEAF_RATIO=0.20` env var to restore prior behavior.

---

### D1: Capture clip_text into PictureResult when Docling misclassifies text as images (P0 missing feature)

**Scope:** `src/pageindex_mcp/converters.py` -- `_recover_picture_text()` (line ~1540-1544 clip_text skip path, line ~1574-1585 OCR path)

**Root cause:** Docling's layout model classifies primary text-layer content of chart/infographic PDFs as PictureItems, emitting `<!-- image -->` markers instead of text. The per-picture OCR recovery pipeline attempts Tesseract OCR on rendered crops, but consistently fails for chart-type content (vector-art labels). The existing `clip_text` check at line 1542 reads text-layer content via `page.get_text('text', clip=rect)` but uses it only to SKIP the region (assuming Docling already extracted it), never to CAPTURE it. No fallback reads the PDF text layer directly when Docling misclassifies text as images and OCR fails. The raw text layer is present (842-843 chars visible via pypdfium2) but never surfaced.

**Trace finding:** Cluster finding -- clip_text discard path in _recover_picture_text

**Fix:** Two changes:

(1) **Clip-text capture with duplicate-content guard.** When `_recover_picture_text` encounters a region where `clip_text` has meaningful content (>`_PICTURE_OCR_MIN_CHARS`), apply a **containment check** before capturing: normalize both `clip_text` and the Docling-exported markdown body (strip whitespace, NFKC-fold, collapse runs of spaces) and test whether a substantial substring of `clip_text` (>=60% of its normalized chars) already appears in the normalized markdown body. If it does, skip as today (`reason='clip_text_already_exported'`). If it does NOT, capture `page.get_text('text', clip=rect)` into `PictureResult.ocr_text` with `reason='clip_text_captured'`. This guard is necessary because every region processed by `_recover_picture_text` is a PictureItem by construction (that is the function's input set), so "is a PictureItem" alone provides no discrimination -- the real question is whether the text-layer content for this region was already emitted by Docling into the markdown export despite the region being image-classified. The containment check answers that question robustly across whitespace/reflow differences.

Implementation detail: the normalized markdown body should be computed **once per page** (not per region) and passed into the per-region loop to avoid O(n^2) re-normalization. A helper `_normalize_for_containment(text: str) -> str` (NFKC + whitespace collapse + lowercase) is shared between the guard and test assertions.

(2) **Document-level text-layer fallback.** When Docling produces mostly `<!-- image -->` markers and very little text (<100 chars excluding markers), read the full page text layer via pypdfium2 and use it as supplementary content.

**Files:** `src/pageindex_mcp/converters.py`

**Rollback:** `CLIP_TEXT_CAPTURE_ENABLED` env var (default `true`); set to `false` to restore skip behavior.

---

### D2: Per-region try/except in Phase 1 crop loop (P0 bug)

**Scope:** `src/pageindex_mcp/converters.py` -- `_recover_picture_text()` Phase 1 crop loop (lines ~1518-1566) and `_recover_picture_results()` outer except (line ~1766)

**Root cause:** The Phase 1 crop loop has no per-region try/except around `page.get_pixmap(clip=rect, dpi=300)`. When PyMuPDF raises "Invalid bandwriter header dimensions/setup" for ANY single region (confirmed on UAE numbers landscape Doc 14), the exception propagates to `_recover_picture_results`' outer except (line 1766), which returns an empty list `[]`. ALL picture results for the entire document are lost, even though other regions might have cropped and OCR'd successfully. For Doc 14, this crash on one degenerate region kills recovery for all 7 picture regions.

**Trace finding:** Cluster finding -- Phase 1 crop loop crash propagation

**Fix:** Wrap the per-region body inside the Phase 1 loop in a try/except that catches `Exception`, logs a warning with the region index and error, and continues to the next region. On failure, record `skip_reasons[i] = 'crop_error'` so the ordinal stays dense. This matches the existing dense-list contract in `_recover_picture_results`' docstring ("sparse recovery must never shift ordinals"). The outer except at line 1766 remains as last-resort guard.

**Files:** `src/pageindex_mcp/converters.py`

**Rollback:** Git revert -- structural change only, no threshold tuning.

---

### D3: Extend ordinal splitter regex for MOU/decree documents (P1 missing feature)

**Scope:** `src/pageindex_mcp/helpers.py` -- `_OVERSIZED_ORDINAL_RE` (lines ~1438-1446), `_ordinal_value()` (lines ~1493-1507), `_has_heading_markers()` (lines ~1672-1684), `split_oversized_leaf_nodes()` (lines ~1687-1764)

**Root cause:** `split_oversized_leaf_nodes()` requires either (a) leaf text > 50,000 chars OR (b) matching ordinal markers in `_OVERSIZED_ORDINAL_RE`. Docs 7 (MOU MOHRE, leaf_concentration=0.50) and 21 (Domestic Workers, leaf_concentration=0.37) use MOU/decree-specific structural markers (Clause, Part, Annex, Arabic `بند`/`باب`) that are NOT recognized by the regex, which only covers Article/Section/Schedule/`مادة`. The splitter's secondary trigger (`_has_heading_markers`) returns False, leaves never enter ordinal-based splitting, and content concentrates in a few leaves, locking both documents at MARGINAL.

Additionally, OCR-recovered text lacks ATX markdown headings (`# ##` etc.), so `extract_nodes_from_markdown` lumps content under whichever heading the tree builder last saw. A secondary `leaf_concentration`-aware splitting pass would help redistribute text in these cases.

**Trace findings:** Cluster finding (Docs 7+21 splitter gap), Drilldown findings (Doc 7 MOU markers, Doc 21 Arabic `بند` markers)

**Fix:** Two changes (additive, with regression guard on non-Arabic docs -- see Risk Assessment for prose false-positive mitigation):

1. **Extend `_OVERSIZED_ORDINAL_RE`** to recognize:
   - `Clause\s+\(?\s*(?P<clause>\d+(?:\.\d+)?)` (case-insensitive)
   - `Part\s+\(?\s*(?P<part>(?:[IVX]+|\d+)(?:\.\d+)?)` (case-insensitive)
   - `بند\s*\(?\s*(?P<band>[\d٠-٩]+(?:[.٫][\d٠-٩]+)?)` (Arabic clause)
   - `باب\s*\(?\s*(?P<bab>[\d٠-٩]+(?:[.٫][\d٠-٩]+)?)` (Arabic chapter)
   - `Annex\s+\(?\s*(?P<annex>[A-Z]|\d+(?:\.\d+)?)` (case-insensitive)

2. **Update `_ordinal_value()`** to extract the new capture groups (`clause`, `part`, `band`, `bab`, `annex`) alongside existing markers (`art`, `sec`, `s`, `sched`, `mada`). **Critically**, extend the conversion logic beyond `int()` to handle the three value types these groups can capture:
   - **Arabic-Indic digits** (existing path): translate via `ARABIC_INDIC_MAP` then `int()`.
   - **Roman numerals** (new, for `part` group): add a `_roman_to_int(s: str) -> int` helper that converts `[IVX]+` tokens (covers Part I through Part XXXIX, sufficient for treaty/MOU structures). Return `(roman_int,)` tuple.
   - **Bare Latin letters** (new, for `annex` group): convert via `ord(ch) - ord('A') + 1`, yielding A=1, B=2, etc. Return `(letter_int,)` tuple.
   
   The `_ordinal_value()` function must dispatch on group name: `part` values are tried as int first, then Roman; `annex` values are tried as int first, then single-letter. All other groups (`clause`, `band`, `bab`) contain only digits/Arabic-Indic digits and follow the existing `int()` path. This ensures no `ValueError` from `int('IV')` or `int('A')`.

3. **(Lower priority)** Add a secondary splitting strategy for leaves with high `leaf_concentration` even under 50k chars: split on paragraph boundaries (blank-line-separated blocks) when the tree's `max_leaf_ratio` exceeds `PASS_MAX_LEAF_RATIO` (same env var as D0, default 0.30) and ordinal splitting fails. Using the same threshold as D0 ensures the two mechanisms stay aligned -- a leaf_ratio below the PASS gate never triggers the paragraph-split fallback. This handles docs where OCR recovery text lacks any structural markers.

**Files:** `src/pageindex_mcp/helpers.py`

**Rollback:** Git revert -- additive regex patterns only. The paragraph-boundary fallback (item 3) gets its own env var `LEAF_CONCENTRATION_PARAGRAPH_SPLIT_ENABLED` (default `true`).

---

### D4: Dual rasterization backend for Tesseract fallback (P1 bug)

**Scope:** `src/pageindex_mcp/converters.py` -- `tesseract_ocr_pdf_pages()` (line ~2287-2307) and `rasterize_pdf_pages()` (line ~2261-2284)

**Root cause:** D7's `tesseract_ocr_pdf_pages` and the VLM fallback's `vlm_extract_markdown` both depend on the same `rasterize_pdf_pages` function (pypdfium2). When CMap corruption in the source PDF causes pypdfium2 page rendering to fail, both paths crash at the identical call, creating a shared single point of failure. Doc 18 (Organizational Decision) consistently errors because CMap corruption crashes pypdfium2, and D7 was designed as an LLM-free local alternative but re-rasterizes from scratch using the same backend. The D7 test suite only tests reason-override logic in isolation, never exercising the actual rasterization path.

**Trace finding:** Cluster finding -- shared SPOF in rasterize_pdf_pages

**Fix:** Add a `rasterize_pdf_pages_fitz(pdf_path, dpi)` function that uses PyMuPDF's `fitz.Page.get_pixmap()` (already imported and used in `_recover_picture_text` for image cropping). Modify `tesseract_ocr_pdf_pages` to try pypdfium2 first, then fall back to fitz on failure. This isolates D7 rasterization from VLM rasterization. Note: fitz is AGPL (PyMuPDF) but is already an accepted transitive dependency (used in the pre-garble probe at client.py:597-611 and picture cropping).

**Files:** `src/pageindex_mcp/converters.py`, `tests/test_rfc023_d7.py`

**Rollback:** `D7_FITZ_FALLBACK_ENABLED` env var (default `true`); set to `false` to use pypdfium2 only.

---

### D5: D7 Tesseract recovery for VLM-succeeds-but-garbled path (P1 missing feature)

**Scope:** `src/pageindex_mcp/client.py` -- `index()` VLM fallback block (lines ~867-937)

**Root cause:** D7's Tesseract-on-raster fallback is structurally nested inside the VLM `except Exception as vlm_exc:` block (client.py:894-937). It is only reachable when `vlm_extract_markdown` or `_run_md_to_tree` raises an exception. If the VLM succeeds but produces garbled output (CMap corruption causes wrong glyph rendering in the rasterized images), `validate_tree` returns `(False, 'garbling')` and the try block completes normally -- the except block (and D7) is never entered. The code falls through to `LowQualityTreeError('garbling')`. This means D7 cannot recover documents where the VLM "succeeds" but produces unusable garbled output.

**Trace finding:** Cluster finding -- D7 structural nesting blocks garbled-VLM recovery

**Fix:** After the VLM's `validate_tree` at line 890-893, when `ok` is still False and reason is `'garbling'` (VLM succeeded but tree is garbled), invoke the same D7 Tesseract-on-raster recovery logic. Extract the D7 logic into a helper function (e.g., `_attempt_tesseract_raster_recovery(file_path, tess_langs, ...)`) to avoid duplication between the try block (VLM-succeeds-but-garbled) and the except block (VLM-crashes).

**Supersession note:** This decision explicitly **supersedes RFC-023 D7 test case (d)** ("garbling reason without VLM exception -- existing escalation path unchanged"). RFC-023 D7 case (d) asserted that a garbling reason in the non-exception path should fall through to `LowQualityTreeError` unchanged. D5 inverts that behavior: the garbled-VLM path now triggers Tesseract recovery instead of immediate failure. Task T3.3 must **rewrite `test_rfc023_d7.py` case (d)** to assert the new behavior: `validate_tree` returning `(False, 'garbling')` in the VLM try-block now invokes `_attempt_tesseract_raster_recovery` rather than falling through to `LowQualityTreeError`. The old assertion becomes a regression test for the `D7_GARBLE_RECOVERY_ENABLED=false` env-var path.

**Files:** `src/pageindex_mcp/client.py`, `tests/test_rfc023_d7.py`

**Rollback:** `D7_GARBLE_RECOVERY_ENABLED` env var (default `true`); set to `false` to preserve prior behavior where garbled VLM output falls through to LowQualityTreeError (matching RFC-023 D7 case (d) original assertion).

---

### D6: Fix audit tooling char-count measurement for flat docs (P2 data quality)

**Scope:** Audit report generation tooling and `audit/CORPUS_REINGESTION_AUDIT_RUN-7.md`

**Root cause:** The audit report's per-document char counts for flat docs use `block.get('text', '')` instead of the existing `_flat_block_text()` helper (helpers.py:2082-2100). This undercounts table-heavy documents because `role='table'` blocks store content in `row_records`, not `text` (by design, FLAT-05-C1). Doc 3 (GHV-TKV-Tarif) is reported as "333 chars" but actually contains 8,110 chars (3 fully-parsed tariff tables); Doc 9 (Unfallversicherung) is reported as "381 chars" but actually contains 7,297 chars (4 benefit-comparison tables). The production verdict pipeline already uses `_flat_block_text()` correctly (RFC-022 B3 fix at client.py:1171-1183, which literally names Doc 3 in its comment), so the stored verdicts are correct -- only the audit/diagnostic reporting is wrong. No pipeline code change is needed for either document.

**Trace findings:** Drilldown findings for Doc 3 and Doc 9

**Fix:** Update the corpus-audit reporting code (wherever it generates the per-document summary rows in `audit/CORPUS_REINGESTION_AUDIT_RUN-7.md`) to call `_flat_block_text(b)` (or `_flat_search_text(b)`, helpers.py:2103-2121) per block instead of `block.get('text', '')`. Correct the Doc 3 and Doc 9 rows in the Run 7 audit report. Consider persisting a `_flat_block_text`-derived char count into `save_flat_doc`'s meta so future audits do not need to re-derive it.

**Files:** `audit/CORPUS_REINGESTION_AUDIT_RUN-7.md`, corpus-cycle and corpus-score-diff skill prompts (the agent-driven audit process, not standalone scripts -- no audit generation scripts exist in `scripts/`), `src/pageindex_mcp/client.py` (persist char count in meta -- mandatory, see T4.3)

**Rollback:** Not applicable -- reporting-only change.

---

## Implementation Plan

### Batch 1: Pipeline Resilience & Content Recovery (D1, D2) -- 1.5d

These two fixes address content loss in the picture recovery pipeline. D2 (crash isolation) is a prerequisite for D1's clip_text capture to work correctly on documents with mixed good/bad regions. Both modify `converters.py` in non-overlapping functions.

| Task | Decision | File | Description | Effort |
|------|----------|------|-------------|--------|
| T1.1 | D2 | `converters.py` | Wrap per-region body in Phase 1 crop loop with try/except; record `skip_reasons[i] = 'crop_error'` on failure | 0.25d |
| T1.2 | D1 | `converters.py` | Route `clip_text` content into `PictureResult.ocr_text` when region is a PictureItem; add document-level text-layer fallback for image-dominant pages | 0.75d |
| T1.3 | -- | `tests/` | Unit tests for T1.1-T1.2 | 0.5d |

### Batch 2: Splitter & Threshold Hardening (D0, D3) -- 1.5d

Independent of Batch 1. D0 is a one-line threshold change. D3 extends the ordinal regex and adds the optional paragraph-boundary fallback.

| Task | Decision | File | Description | Effort |
|------|----------|------|-------------|--------|
| T2.1 | D0 | `helpers.py` | Widen `PASS_MAX_LEAF_RATIO` default from 0.20 to 0.30 | 0.1d |
| T2.2 | D3 | `helpers.py` | Extend `_OVERSIZED_ORDINAL_RE` with Clause/Part/Annex/`بند`/`باب` patterns; update `_ordinal_value()` | 0.5d |
| T2.3 | D3 | `helpers.py` | (Optional) Add leaf_concentration-aware paragraph-boundary splitting fallback | 0.5d |
| T2.4 | -- | `tests/` | Unit tests for T2.1-T2.3 | 0.4d |

### Batch 3: Rasterization & VLM-Garble Recovery (D4, D5) -- 1.35d

D4 provides the fitz rasterization backend that D5's extracted helper function will use. D5 depends on D4 being available.

| Task | Decision | File | Description | Effort |
|------|----------|------|-------------|--------|
| T3.0 | D4 | spike | **Pre-implementation spike**: render Doc 18 (Organizational Decision) pages with `fitz.Page.get_pixmap()` to confirm fitz survives CMap corruption that crashes pypdfium2. If fitz also fails, escalate to Ghostscript rasterization or downgrade Doc 18 Expected Outcome to ERROR. | 0.1d |
| T3.1 | D4 | `converters.py` | Add `rasterize_pdf_pages_fitz()` function; modify `tesseract_ocr_pdf_pages` to try pypdfium2 then fitz | 0.5d |
| T3.2 | D5 | `client.py` | Extract D7 Tesseract logic into `_attempt_tesseract_raster_recovery()` helper; invoke in try block after VLM garble detection | 0.25d |
| T3.3 | D5 | `tests/` | Unit tests for T3.1-T3.2; **rewrite `test_rfc023_d7.py` case (d)** to assert garbled-VLM triggers recovery (supersedes RFC-023 D7 case (d)); preserve old case (d) assertion under `D7_GARBLE_RECOVERY_ENABLED=false` env-var test; extend `test_rfc023_d7.py` with fitz rasterization path tests | 0.5d |

### Batch 4: Audit Tooling (D6) -- 0.6d

T4.1-T4.3 are independent of Batches 1-3 and can run in parallel with them.

| Task | Decision | File | Description | Effort |
|------|----------|------|-------------|--------|
| T4.1 | D6 | corpus-cycle skill prompt, corpus-score-diff skill | Update char-count measurement to use `_flat_block_text()`. Note: no standalone audit generation scripts exist in `scripts/`; the erroneous 333/381-char figures originated from the agent/skill-driven corpus-cycle audit process that reads `processed/*.meta.json` via `block.get('text', '')`. The fix target is the corpus-cycle and corpus-score-diff skill prompts that drive per-document scoring. | 0.25d |
| T4.2 | D6 | `audit/` | Correct Doc 3 and Doc 9 rows in Run 7 audit report | 0.1d |
| T4.3 | D6 | `client.py` | **(Mandatory)** Persist `_flat_block_text`-derived char count in `save_flat_doc` meta. This is the durable fix that prevents future audits from re-deriving char counts incorrectly -- T4.1 alone fixes the current audit tooling but any new audit code path would repeat the error without persisted ground-truth counts. | 0.25d |

### Batch 5: Reingestion Verification (Run 8) -- 0.25d

**Must run after Batches 1-4 complete.** The Run 8 reaudit exercises D0-D6 changes and validates the Expected Outcomes table. Running it in parallel with Batches 1-3 would validate nothing.

| Task | Decision | File | Description | Effort |
|------|----------|------|-------------|--------|
| T5.1 | -- | -- | Bump `CURRENT_PIPELINE_VERSION`; full 25-doc reingestion for Run 8; verify Expected Outcomes table | 0.25d |

**Total effort: ~5.0 person-days.**

## Expected Outcomes

### Projected Run 8 Verdict Changes

| Doc | Run 7 | Fix | Projected | Rationale |
|-----|-------|-----|-----------|-----------|
| 3 (GHV-TKV-Tarif) | MARGINAL | D6 | MARGINAL | Verdict correct; only audit reporting fixed. MARGINAL due to depth=1 + high leaf_concentration (0.4698) |
| 7 (MOU MOHRE) | MARGINAL | D3 | PASS | Extended ordinal regex enables Clause/Part splitting; leaf_concentration drops below 0.30 |
| 8 (Reitlehrer) | MARGINAL* | D0 | PASS | Widened threshold (0.30) absorbs jitter range (0.17-0.2571) |
| 9 (Unfallversicherung) | MARGINAL | D6 | MARGINAL | Verdict correct; only audit reporting fixed. MARGINAL due to depth=1 + high leaf_concentration (0.4566) |
| 14 (UAE landscape) | FAIL/MARGINAL | D1, D2 | MARGINAL | D2 crash isolation recovers non-degenerate regions; D1 clip-text capture needed for chart-type content where Tesseract consistently fails on vector-art labels. D2 alone (crash isolation without text-layer capture) likely yields empty OCR for surviving regions. |
| 18 (Organizational Decision) | ERROR | D4, D5 | MARGINAL | **T3.0 spike confirmed (Checkpoint 3.5)**: `rasterize_pdf_pages_fitz()` renders all 35/35 pages of Doc 18's source PDF cleanly via `fitz.Page.get_pixmap()`, so fitz fallback rasterization is available and D5's garble-recovery path is reachable; Doc 18 is expected to land MARGINAL in Run 8 rather than ERROR. |
| 21 (Domestic Workers) | MARGINAL | D3 | PASS | `بند` recognition enables ordinal splitting; leaf_concentration drops below 0.30 |

\* Doc 8 oscillated PASS/MARGINAL between runs; D0 stabilizes at PASS.

## Test Strategy

| Decision | Test file | Key assertions |
|----------|-----------|----------------|
| D0 | `tests/test_rfc024_d0.py` | (a) max_leaf_ratio 0.25 with default threshold 0.30: verdict PASS; (b) max_leaf_ratio 0.35: verdict MARGINAL; (c) max_leaf_ratio 0.19: PASS regardless |
| D1 | `tests/test_rfc024_d1.py` | (a) PictureItem region with meaningful clip_text (>min chars) NOT present in exported markdown (containment <60%): ocr_text populated from text layer via clip_text_captured path; (b) region with empty clip_text: proceeds to Tesseract OCR; (c) image-dominant page (<100 chars excluding markers): full-page text-layer fallback fires; (d) region clip_text where >=60% normalized content already appears in Docling markdown body: skip with reason='clip_text_already_exported' (no double-capture); (e) containment check is robust to whitespace/reflow differences (NFKC + whitespace collapse + lowercase) |
| D2 | `tests/test_rfc024_d2.py` | (a) single degenerate region raises Exception: only that region skipped, others proceed; (b) skip_reasons[i] = 'crop_error' recorded; (c) ordinal density preserved (no shift); (d) all regions fail: empty result returned gracefully |
| D3 | `tests/test_rfc024_d3.py` | (a) text with "Clause 1 ... Clause 2 ... Clause 3": `_has_heading_markers` returns True, splitting fires; (b) text with "بند ١ ... بند ٢ ... بند ٣" (>=3 markers, satisfying `_longest_increasing_run >= min_segments`): markers captured, ordinal run formed, split succeeds; (c) existing Article/Section/مادة patterns: no regression; (d) leaf_concentration paragraph-boundary fallback: splits on blank lines when max_leaf_ratio > `PASS_MAX_LEAF_RATIO`; (e) "Part IV ... Part V ... Part VI" with Roman numerals: `_ordinal_value` returns correct int tuples via `_roman_to_int`; (f) "Annex A ... Annex B ... Annex C" with bare letters: `_ordinal_value` returns correct int tuples via `ord()` conversion |
| D4 | `tests/test_rfc024_d4.py` | (a) pypdfium2 raises on CMap-corrupt PDF: fitz fallback fires, returns page images; (b) pypdfium2 succeeds: fitz not called; (c) both fail: error propagated cleanly; (d) `D7_FITZ_FALLBACK_ENABLED=false`: fitz fallback disabled |
| D5 | `tests/test_rfc024_d5.py` + `tests/test_rfc023_d7.py` | (a) VLM succeeds but validate_tree returns (False, 'garbling'): D7 helper invoked; (b) VLM succeeds and validate_tree returns (True, ...): D7 helper NOT invoked; (c) VLM crashes (except block): D7 helper invoked as before; (d) D7 helper extracted: both call sites use same function; (e) **Rewrite test_rfc023_d7.py case (d)**: garbling-without-exception now triggers recovery (supersedes RFC-023 D7 case (d)); old assertion preserved under `D7_GARBLE_RECOVERY_ENABLED=false` env var path |
| D6 | `tests/test_rfc024_d6.py` | (a) flat doc with table blocks: char count uses _flat_block_text, includes row_records; (b) flat doc with only text blocks: char count unchanged; (c) persisted meta (if implemented) matches _flat_block_text total |

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| D0: Wider threshold (0.30) admits genuinely lopsided trees as PASS | Low | Low | max_leaf_ratio > 0.75 remains a hard FAIL gate; 0.30 is conservative for docs with >10 nodes and depth >=2 |
| D0: **Recurrence -- third consecutive widening for same jitter mode** (RFC-023 D10: 0.17->0.20; now 0.20->0.30). A future doc could jitter past 0.30 in Run 9. | Medium | Low | Acknowledged: pure widening is a stopgap. If Run 9 or later produces another boundary jitter, the next RFC must implement hysteresis / tolerance-band / prior-verdict anchoring rather than widening again. The env-var mechanism already supports per-deployment tuning. |
| D0 vs D3: Threshold inconsistency -- D3 item 3 hard-codes 0.25 as paragraph-split trigger while D0 raises PASS gate to 0.30; a leaf_ratio of 0.26-0.29 passes PASS gate without paragraph-split ever firing | Low | Low | Align: change D3 item 3 paragraph-split threshold to use `PASS_MAX_LEAF_RATIO` env var (same as D0) rather than a hard-coded 0.25, so both thresholds move together. |
| D1: Clip-text capture double-counts content already in Docling's markdown export | Medium | Medium | Containment guard: normalize both clip_text and Docling markdown body (NFKC + whitespace collapse + lowercase), skip capture when >=60% of clip_text content already present in markdown body. Computed once per page. |
| D1: Document-level text-layer fallback fires on docs where Docling intentionally image-routed (e.g., scanned pages) | Low | Medium | Only fires when Docling text output <100 chars excluding markers AND text layer is non-garbled |
| D2: Silently skipping crop errors masks systematic PyMuPDF issues | Low | Low | Warning logged with region index and error detail; skip_reasons preserved in result for audit traceability |
| D3: New ordinal patterns match false positives in prose text (e.g., "Part 2 of the agreement" in German T&C or English decrees) | Medium | Medium | Regex requires whitespace + digit/numeral after keyword; existing strictly-increasing-run guard (min_segments=3, lines 1737-1763) rejects non-sequential matches. **However, "zero regression risk on non-Arabic docs" claim removed** -- Run 7 PASS German/English docs will be re-scanned by the new patterns, and Run 8 reaudit (Batch 5) must explicitly verify no PASS->MARGINAL regressions from false-positive splits. |
| D3: Paragraph-boundary splitting over-fragments content | Low | Medium | Only fires when leaf_concentration > `PASS_MAX_LEAF_RATIO` (aligned with D0 threshold) after ordinal splitting fails; splits on blank-line boundaries (natural paragraph breaks), not arbitrary character offsets; configurable via env var |
| D4: Fitz rasterization produces different pixel output than pypdfium2 | Medium | Low | Both render from the same PDF; minor pixel differences do not affect Tesseract OCR quality. Fitz is already used for crop rendering in _recover_picture_text |
| D4: **Fitz may also fail on Doc 18's CMap-corrupt pages** -- no evidence that fitz renders them successfully where pypdfium2 crashes | Medium | Medium | **Pre-implementation spike required**: before T3.1, render Doc 18 pages with `fitz.Page.get_pixmap()` in a standalone test to confirm fitz survives the CMap corruption. If fitz also crashes, D4 needs a fallback-of-the-fallback (e.g., Ghostscript rasterization) or Doc 18 stays ERROR and the Expected Outcomes table is downgraded. Added as T3.0 spike task. |
| D5: D7 Tesseract recovery on garbled VLM output produces lower-quality tree than accepting the garbled tree | Low | Low | Garble check on Tesseract OCR output prevents persisting genuinely garbled content (Hard Rule 5); degraded-but-present artifact is strictly better than LowQualityTreeError |
| D6: Persisting _flat_block_text char count in meta increases storage slightly | Very Low | Very Low | Single integer field per flat doc; negligible storage impact |
| Run 8 regression on Run 7 PASS docs | Medium | High | Batch 5 reaudit (T5.1) explicitly verifies all Run 7 PASS docs maintain their verdicts. Risk elevated from Low to Medium given D3's new patterns scanning existing PASS docs. |

## Cross-References

- **Audit report:** `audit/CORPUS_REINGESTION_AUDIT_RUN-7.md`
- **Prior RFCs:** RFC-023 (D0 garble-aware text-layer, D7 Tesseract fallback, D10 threshold widening), RFC-022 (B3 _flat_block_text fix), RFC-020 (F1 coverage exemption)
- **Related RFCs:** RFC-018 (corpus audit remediation), RFC-019 (corpus reingestion Phase 2)
- **Design document:** [design-rfc024-run7-verdict-stability-and-recovery-gaps.md](../designs/design-rfc024-run7-verdict-stability-and-recovery-gaps.md)
- **Implementation plan:** [tasks-rfc024-run7-verdict-stability-and-recovery-gaps.md](../tasks/tasks-rfc024-run7-verdict-stability-and-recovery-gaps.md)
