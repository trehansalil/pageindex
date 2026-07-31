# RFC-027: Run-10 Extraction Gate Integrity and Arabic Content Recovery

**Date:** 2026-07-31
**Run:** 10
**Baseline:** 8 PASS / 7 MARGINAL / 10 FAIL / 0 ERROR (25 docs, 24 persisted)
**Prior (Run 9):** 15 PASS / 8 MARGINAL / 0 FAIL / 1 ERROR

---

## Context

Run 10 is the first run after RFC-026 gate-hardening landed (commit 6113ba3). The FAIL count rose from 0 to 10, which is overwhelmingly correct: RFC-026 D0 (zero-content FAIL floor) and D1 (image-enrichment char floor) now surface pre-existing extraction failures that were previously soft-landed as MARGINAL or auto-promoted to PASS. Of the 10 FAILs, 5 are zero-content Arabic PDFs where OCR escalation never fires, 2 are near-zero-content Arabic PDFs with garbled/junk output, and 3 are quality-gate corrections on docs the Run 9 audit explicitly flagged as false-PASS.

The remaining defects cluster into two themes: (1) verdict-gate bypass paths that RFC-026 hardening exposed but did not close (inflated char counts from enrichment metadata, garble detection gaps in promotion branches, digit-noise bypassing garble gates), and (2) Arabic content recovery gaps (zero-content scanned PDFs never triggering OCR, reversed RTL text passing all checks, missing Arabic structural heading injection, Docling not recognizing Arabic chapter/article markers).

**Audit report:** `audit/CORPUS_REINGESTION_AUDIT_RUN-10.md`

---

## Fix Dimensions

### D0 -- Split _flat_block_text to Exclude Enrichment Metadata from Verdict Char Counts

**Root cause:** `_flat_block_text()` in `helpers.py` (line ~2249) conflates primary document text with image-enrichment metadata (`ocr_text`/`description`) for image blocks. This function feeds both `flat_char_count` computation (`client.py` line 1359) and the synthetic `flat_structure` passed to `classify_verdict()` (`client.py` lines 1330-1335). The RFC-026 D1 `image_enrichment_promoted` char floor check (helpers.py line 1216) then evaluates against inflated counts: Unfallversicherung shows 7,471 chars (enrichment included) instead of the real 492 chars of document text, clearing the 500-char floor and retaining a false PASS.

**Affected docs:** Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf (492 real chars inflated to 7,471; stored PASS via `image_enrichment_promoted` should be MARGINAL)

**Fix:** Split `_flat_block_text` into two functions:
1. `_flat_block_primary_text(block)` -- returns only `block.get('text', '')` plus table `row_records` (actual extracted document content)
2. `_flat_block_text(block)` -- existing function including `ocr_text`/`description` for search indexing

Use `_flat_block_primary_text` for: (a) computing `flat_char_count`, (b) building synthetic `flat_structure` for `classify_verdict`. Keep `_flat_block_text` for `_flat_search_text` and retrieval.

**Files:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/client.py`
**Severity:** High -- false PASS on a doc with 492 real chars.
**Effort:** ~20 lines, 45 min

### D1 -- Wire Garble Detection into image_enrichment_promoted Branch and Reorder D3B Check Post-Splice

**Root cause:** Two compounding gaps:

1. `classify_verdict()`'s `image_enrichment_promoted` branch (helpers.py lines 1207-1219) never calls `_is_garbled_blob` before returning PASS. It checks only `image_enrichment_ratio >= 0.8` and the D1 char floor. The existing garble detector at helpers.py line 883-887 ('>60% digits on blobs >500 chars') would catch the digit-noise in ward-597 (70.5% digit ratio on 3,277 chars), but is never invoked from this branch.

2. The D3B flat-path garble check (`client.py` line 1202: `_flat_text_is_garbled(flat_md, ...)`) runs BEFORE `splice_figure_markers` (line 1261) injects OCR content, so post-splice junk is never checked.

**Affected docs:** ward 597 (3,208 chars of barcode/digit noise, stored PASS via `image_enrichment_promoted`); general gap for any doc where image-enrichment OCR produces garbled/junk content

**Fix:**
1. In `classify_verdict`'s `image_enrichment_promoted` branch, call `_is_garbled_blob` on the flattened promoted text before returning PASS; if garbled, fall through to MARGINAL/FAIL.
2. Move/re-run D3B `_flat_text_is_garbled` check AFTER `splice_figure_markers` so image-OCR-derived content is included.
3. Exclude duplicate `> [Chart text]:` prose blocks from `image_enrichment_ratio` and char-floor calculations to prevent double-counting from a single OCR read.

**Files:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/client.py`, `src/pageindex_mcp/converters.py`
**Severity:** High -- garbled digit-noise docs reach PASS.
**Effort:** ~40 lines, 1.5 hours

### D2 -- Extend OCR Escalation to Low-Content and Garbled Arabic PDFs

**Root cause:** Scanned-only Arabic PDFs produce zero or near-zero content but the OCR escalation condition (`client.py` line 965) only fires on `reason='garbling'` or `'node_garbling'`. Zero-content docs get `reason='node_count<3'` and route to the flat path, persisting empty documents. Near-zero-content docs (e.g. مرسوم (13) 2022 with 38 chars, القرار التنظيمي with 230 garbled chars) similarly never trigger OCR escalation because they have *some* content -- just not enough to be useful, and in the garbled case the content is junk that the existing garble gate does not catch (it fires only on the `garbling` reason path). RFC-026 D0 correctly surfaces these as FAIL/`zero_content` -- the underlying defect is that OCR never fires.

**Affected docs:** MOU MOHRE (0 chars), اتفاقية SLA (0 chars), قرار 1/2022 (0 chars), قرار 106/2022 (0 chars), مرسوم (13) 2022 (38 chars), القرار التنظيمي (230 garbled chars)

**Fix:** Add a low-content OCR escalation branch at `client.py` line 965: when `reason == 'node_count<3'` and `total_chars < 300` and `ext == '.pdf'`, trigger `force_full_page_ocr` retry. The 300-char floor covers both truly zero-content docs and near-zero/garbled docs like مرسوم (38 chars) and القرار التنظيمي (230 garbled chars). This routes scanned Arabic PDFs through the Docling `force_full_page_ocr` path, which re-OCRs every page via Tesseract regardless of the text layer.

Note: `total_chars` is computed from the tree structure (sum of node text lengths), not from `validate_tree`'s return value. `validate_tree()` returns a 2-tuple `(bool, str)` per `helpers.py:1047`; `node_count` and `total_chars` are read from the structure object before the escalation check.

**Files:** `src/pageindex_mcp/client.py`, `src/pageindex_mcp/converters.py`
**Severity:** Critical -- 6 docs with complete or near-complete extraction failure.
**Effort:** ~30 lines, 2 hours (requires integration testing with scanned PDFs)

### D3 -- Add RTL Reversal Detection to validate_tree, Fix _text_is_logical_order, and Wire Repair-First Flow

**Root cause:** Neither `validate_tree()` nor `classify_verdict()` detects reversed-but-valid Arabic text. The garble gate (`_tree_is_garbled`) targets null bytes, replacement characters, PUA codepoints, and sparse mojibake -- reversed-but-valid Arabic characters pass all checks. For siyasat-hawkama (24 nodes, 20,330 chars, depth >= 2), `validate_tree` returns `(True, '')` and the doc gets PASS.

Additionally, `_text_is_logical_order` in `converters.py` (line ~1205, defined at that line; the return expression is in the function body near ~1233) has a false-positive: `return orig_total >= disp_total` yields `True` when both scores are 0 because the governance/data-policy vocabulary is absent from `_AR_COMMON_WORDS`, preventing `reconstruct_bidi_order` from fixing the reversal.

**Affected docs:** siyasat hawkama (24 nodes / 20,330 chars, 100% reversed RTL node titles, stored PASS)

**Fix:**
1. Fix `_text_is_logical_order`: require `orig_total > 0` with at least one positive match, e.g. `return sampled > 0 and orig_total > 0 and orig_total >= disp_total`. This unblocks `reconstruct_bidi_order` for documents where both scores are 0.
2. Add an RTL reversal prong to `validate_tree()`: compute forward-vs-reversed readability scores on Arabic-heavy trees; return `(False, 'rtl_reversal')` when reversed text scores higher.
3. Wire the escalation flow with **repair-first ordering**: when `validate_reason='rtl_reversal'`, first attempt `reconstruct_bidi_order` (which sub-fix #2 above enables by fixing the false-positive). If the repaired text passes a re-check (forward readability > reversed), accept the repaired tree. If repair does not converge (reversed still scores higher after bidi reconstruction), then route to FAIL. This prevents hard-FAILing a document that the bidi repair can actually fix.

The flow is: `validate_tree` detects reversal -> `reconstruct_bidi_order` attempts repair -> re-validate -> if still reversed, `classify_verdict` maps to FAIL; if repaired, proceed with the corrected tree.

**Files:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/client.py`
**Severity:** High -- reversed Arabic text silently passes all gates.
**Effort:** ~60 lines, 2.5 hours

### D4 -- Inject Arabic Structural Headings for Non-Heading Lines

**Root cause:** Docling does not classify Arabic structural markers (al-bab, al-fasl, al-maddah) as `SectionHeaderItem`s, so the converter emits no `#` markdown headings for Arabic chapter/article structure. The heading-depth recovery chain (`_recover_heading_depth` -> `_relevel_by_containment` / `_relevel_by_numbering` / `_relevel_by_outline`) can only assign depth to EXISTING headings -- it cannot create headings from plain prose lines. With no headings, `md_to_tree` produces a flat tree and the document falls to `flat_mixed`. The English version of the same labor law (doc #24, 502 nodes, depth 4) works because Docling detects English structural markers as headings.

**Affected docs:** marsoom-biqanoon labor law (871 blocks, 101k chars, flat/null depth, stored PASS `cat_b_promoted` but audit MARGINAL)

**Fix:** Add `_inject_arabic_structural_headings(md)` step in the converter pipeline before `md_to_tree`. Scan raw markdown for Arabic structural markers on non-heading lines matching `_AR_PART_RE` / `_AR_ARTICLE_RE` patterns (already defined in `converters.py` lines 79-80) and promote them to `#` headings at appropriate levels. The existing `_relevel_by_containment` and `_relevel_by_numbering` then assign correct depth.

**Files:** `src/pageindex_mcp/converters.py`
**Severity:** Medium -- Arabic legal docs lose all hierarchy structure.
**Effort:** ~40 lines, 1.5 hours

### D5 -- Relax Small-Doc Leaf-Ratio Threshold for Very Small Trees

**Root cause:** GHV-TKV-Tarif.pdf has 5 nodes with `leaf_concentration=0.39`, which exceeds `PASS_MAX_LEAF_RATIO=0.30`. The `small_doc_promoted` path (helpers.py lines 1298-1307) requires `max_leaf_ratio < 0.20`, so 0.39 also fails that. High leaf concentration is structurally inevitable for documents with `node_count <= 5`.

**Affected docs:** GHV-TKV-Tarif.pdf (5 nodes, 13,022 chars, `leaf_concentration=0.39`)

**Fix:** Add a specific dispensation for very small trees: raise `small_doc_promoted` `max_leaf_ratio` threshold from 0.20 to 0.40 only for documents with `node_count <= 5`. Documents with 6-10 nodes retain the existing 0.20 threshold to avoid promoting genuinely degenerate stub trees. A corpus-wide impact check must verify no docs with 3-5 nodes and leaf_concentration 0.20-0.40 are degenerate stubs before landing.

**Files:** `src/pageindex_mcp/helpers.py`
**Severity:** Low -- 1 doc, false positive from thresholds designed for larger trees.
**Effort:** ~5 lines, 20 min

### D6 -- Deduplicate Identical Adjacent Image Markers from Docling Standalone-Image Export

**Root cause:** Docling's `export_to_markdown()` produces duplicate consecutive `<!-- image -->` markers for certain JPGs when the same image region is exported twice. The `marker_count` drives `PictureResult` replication (RFC-018 D0 design at `client.py` lines 921-929), creating identical `fig-0`/`fig-1` blocks with duplicated enrichment metadata. The root defect is in Docling's image-to-markdown conversion, not in pageindex code, but a dedup guard prevents the downstream replication.

**Important constraint:** RFC-018 D0's marker-count-to-PictureResult replication path was explicitly built for multi-region standalone images. Legitimately distinct adjacent images (e.g. a page with two separate figures) also emit consecutive `<!-- image -->` markers, and each must be preserved. A blanket collapse of ALL consecutive markers would silently drop real regions/figures.

**Affected docs:** image pie chart about labor distribution (12 blocks, 978 chars, duplicate fig-0/fig-1, stored PASS but audit MARGINAL)

**Fix:** Collapse only *truly adjacent identical* markers, not all consecutive markers. In `client.py` before `marker_count = md_content.count("<!-- image -->")`:
1. Split markdown by `<!-- image -->` markers.
2. Compare the text segments between consecutive markers -- if two adjacent segments are empty or whitespace-only (no intervening content between the markers), collapse the duplicate.
3. This preserves markers that have distinct content between them (indicating separate image regions) while deduplicating Docling's spurious empty-gap duplicates.

Implementation: `md_content = re.sub(r'(<!-- image -->)\s*(?=<!-- image -->)', '', md_content)` -- this removes a marker only when it is immediately followed (modulo whitespace) by another identical marker, preserving the final marker and all markers with intervening content.

**Files:** `src/pageindex_mcp/client.py`
**Severity:** Low -- 1 doc, cosmetic duplication.
**Effort:** ~8 lines, 20 min

### D7 -- Add Page-Count Guard for Large-Document Docling Timeout (Chunked-Docling Route)

**Root cause:** world-stats-pocketbook-2023.pdf (292 pages, 6.4MB) exceeds both `CHILD_TIMEOUT` (1770s) and `docling_service_timeout_s` (600s). Docling's deep-learning layout pipeline (RT-DETRv2 + TableFormer) runs at ~5-10s/page on CPU, requiring 24-49 minutes for 292 pages. The converter child is killed (SIGTERM then SIGKILL) before any artifacts are persisted. This has stalled across 2 consecutive runs.

**Affected docs:** world-stats-pocketbook-2023.pdf (292 pages, processing job dies before persisting any artifacts)

**Licensing constraint (CLAUDE.md Hard Rule 4):** pymupdf4llm/PyMuPDF are AGPL-3.0. Serving them over a network is an unresolved legal decision. pymupdf4llm was deliberately moved behind the optional `agpl-fallback` extra (not installed by default `uv sync`), so a pymupdf4llm fallback would fail at import time in a standard install and introduces unresolved AGPL exposure. This decision therefore does NOT use pymupdf4llm as the fallback.

**Fix:** Add a `MAX_DOCLING_PAGES` config (default ~150) with a two-tier strategy for oversized PDFs:

1. **Primary: Chunked Docling** -- split the PDF into chunks of `MAX_DOCLING_PAGES` pages (using PyPDF2, which is already a dependency and MIT-licensed), process each chunk through the standard Docling pipeline independently, then concatenate the resulting markdown. Each chunk fits within the existing `CHILD_TIMEOUT`/`docling_service_timeout_s` window. The tree is built from the concatenated output.

2. **Fallback: Text-layer-only extraction** -- if chunked Docling still times out (e.g. pages are individually very heavy), fall back to PyPDF2 text extraction (`page.extract_text()`). This produces text-only output with no table/figure extraction but guarantees completion. The document lands at MARGINAL due to flat structure, which is correct for a degraded extraction.

3. **Dynamic timeout scaling** -- for the chunked path, scale `CHILD_TIMEOUT` proportionally: `base_timeout + (chunk_count * per_chunk_timeout)`.

Check page count before `_docling_converter().convert()` using PyPDF2's `PdfReader(path).pages` length.

**Files:** `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/worker.py`, `src/pageindex_mcp/config.py`, `src/pageindex_mcp/client.py`
**Severity:** Medium -- 1 doc, persistent timeout failure.
**Effort:** ~60 lines across 4 files, 3 hours (requires integration testing with large PDFs)

### D8 -- Correct Run-10 Audit Report: Landscape/Portrait Twin Scorecard and D4 Verification Gap

**Root cause:** Audit row #23 (landscape twin) reports stored verdict as PASS/unknown, but live MinIO `meta.json` shows MARGINAL/`flat_mixed`/`depth=1` (identical to portrait). Both twins have `content_class='flat_mixed'`, not unknown-vs-flat_mixed. The landscape entry should be in Stalls (matched MARGINAL), not Improvements. The D4 pre-publish verification missed row #23's stored verdict cross-check.

**Affected docs:** uae_numbers_english_page_16_17_landscape (audit row #23)

**Fix:**
1. Correct audit row #23: change verdict from PASS to MARGINAL, move pair from Improvements to Stalls.
2. Tighten D4 pre-publish verification to cross-check every scorecard row against stored gate verdict (row #23 shows the process can silently skip a doc).

**Files:** `audit/CORPUS_REINGESTION_AUDIT_RUN-10.md`
**Severity:** Low -- audit data quality, not code.
**Effort:** ~15 min

---

## Out of Scope

- **RFC-026 D6 CMap Latin-gibberish dictionary detection** (deferred from RFC-026, Haftpflicht depth-2 root cause) -- already tracked, not duplicated here.
- **Content-class classifier alignment for orientation twins** (portrait vs landscape getting different `content_class`) -- traced finding #2 shows the live data actually has identical classification; the asymmetry was an audit-reporting artifact, not a code bug.
- **Vector-icon table-cell recovery** (Unfallversicherung checkmark icons drawn as PDF path operators, invisible to text-layer extraction; trace finding #13) -- genuine gap but requires either cell-level rasterization+OCR or Docling upstream enhancement; too large for this RFC cycle. **Track as:** follow-up RFC-028 candidate or GitHub issue for cell-level rasterization investigation.
- **Chart-aware promotion path for flat_mixed docs dominated by numeric/label chart data** (both landscape/portrait twins stuck at MARGINAL) -- needs separate design work on content-class-aware promotion beyond leaf-ratio/depth gates. **Track as:** follow-up RFC-028 candidate for content-class-aware promotion design.

---

## Implementation Plan

### Batch 0 -- Independent Small Fixes (no cross-dependencies)

| Decision | Summary | Rationale |
|----------|---------|-----------|
| D0 | Split `_flat_block_text` | Prerequisite for D1's char-floor accuracy; self-contained refactor |
| D5 | Relax small-doc leaf-ratio | Self-contained threshold tweak |
| D6 | Dedup identical adjacent image markers | Self-contained regex guard; scoped to preserve multi-region markers |
| D8 | Audit report correction | Documentation fix, no code |

### Batch 1 -- Verdict Gate Garble Hardening + OCR Escalation

| Decision | Summary | Rationale |
|----------|---------|-----------|
| D1 | Garble detection in promotion branch + post-splice recheck | Depends on D0's split so char counts are accurate before garble-checking |
| D2 | Low-content OCR escalation (< 300 chars) | Independent but grouped as medium complexity; primary Arabic content recovery fix; covers zero-content and near-zero/garbled docs |

### Batch 2 -- Arabic Extraction Improvements

| Decision | Summary | Rationale |
|----------|---------|-----------|
| D3 | RTL reversal detection + `_text_is_logical_order` fix + repair-first flow | Arabic-specific gate addition with bidi repair attempt before FAIL |
| D4 | Arabic structural heading injection | Different code path in converters.py; benefits from D2 landing first (OCR escalation recovers raw content that heading injection then structures) |

### Batch 3 -- Large-Document Timeout Guard

| Decision | Summary | Rationale |
|----------|---------|-----------|
| D7 | Page-count guard + chunked-Docling fallback (no AGPL) | Operationally important but independent of verdict-gate fixes; touches 4 files, requires integration testing with actual large PDFs |

---

## Test Strategy

| Decision | Test Approach | Key Assertions |
|----------|---------------|----------------|
| D0 | Unit: mock block with `ocr_text`/`description`; assert `_flat_block_primary_text` excludes enrichment, `_flat_block_text` includes it. Integration: process Unfallversicherung, assert `flat_char_count` reflects primary text only. | `flat_char_count` == 492 (not 7,471); verdict == MARGINAL |
| D1 | Unit: pass 70%-digit blob to `image_enrichment_promoted` branch, assert garble detected. Unit: build flat_md, splice figure markers with junk OCR, assert post-splice garble check fires. | ward-597 verdict != PASS; digit-noise docs blocked at promotion |
| D2 | Integration: process a low-content scanned PDF (< 300 chars), assert OCR escalation triggers `force_full_page_ocr`. Unit: build a structure with `node_count=0`/`total_chars=0` and separately with `total_chars=38` and `total_chars=230`, mock `validate_tree` returning `(False, 'node_count<3')` for `.pdf`, read `total_chars` from the structure, assert escalation fires for all cases. Note: `validate_tree` returns a 2-tuple `(bool, str)` per `helpers.py:1047`; `node_count`/`total_chars` come from the structure object, not from validate's return. | Low-content Arabic PDFs (0-299 chars) produce > 0 chars after retry; docs with >= 300 real chars do NOT escalate |
| D3 | Unit: build tree with reversed Arabic text, assert `validate_tree` returns `(False, 'rtl_reversal')`. Unit: call `_text_is_logical_order` with both scores == 0, assert returns `False`. Unit: mock `reconstruct_bidi_order` succeeding (forward > reversed after repair), assert tree accepted (not FAIL). Unit: mock `reconstruct_bidi_order` failing to converge (reversed still > forward), assert verdict == FAIL. | siyasat-hawkama: repair attempted first; FAIL only if bidi repair does not converge; zero-score edge case handled |
| D4 | Unit: pass Arabic markdown with al-bab/al-fasl/al-maddah lines, assert `_inject_arabic_structural_headings` produces `#` headings. Integration: process Arabic labor law, assert depth >= 2. | Arabic legal docs get structured trees matching English twin |
| D5 | Unit: call `classify_verdict` with `node_count=5`, `leaf_concentration=0.39`, assert verdict == PASS via `small_doc_promoted`. Unit: call with `node_count=8`, `leaf_concentration=0.35`, assert verdict != PASS (threshold remains 0.20 for 6-10 nodes). Corpus-wide: grep all docs with node_count 3-5 and leaf_concentration 0.20-0.40 from Run 10 to verify no degenerate stubs. | GHV-TKV-Tarif verdict == PASS; no degenerate stub promotions |
| D6 | Unit: pass markdown with consecutive `<!-- image -->` markers, assert dedup reduces to single marker. Integration: process pie chart JPG, assert single `fig-0` block (no `fig-1` duplicate). | `marker_count` == 1 for single-image docs |
| D7 | Unit: mock 292-page PDF, assert chunked-Docling splitting into ceil(292/150)=2 chunks when page count > `MAX_DOCLING_PAGES`. Unit: mock chunked-Docling timeout, assert fallback to PyPDF2 text-layer extraction. Integration: process world-stats-pocketbook, assert completion within timeout. | Large PDFs complete processing via chunked route; no SIGTERM/SIGKILL; fallback produces text-only MARGINAL output |
| D8 | Manual: verify audit row #23 corrected; verify all scorecard rows carry stored-verdict cross-check. | Audit report accuracy |

---

## Risks

| Risk | Mitigation |
|------|------------|
| D0 split may miss call sites that need primary-only text | Grep all `_flat_block_text` call sites; verify each consumer's intent (verdict vs search) |
| D1 garble detection in promotion branch may false-positive on legitimate OCR text with high digit content (financial tables) | Gate on digit-ratio AND minimum content length; use existing `_is_garbled_blob` thresholds (>60% digits on >500 char blobs) which are already calibrated |
| D2 OCR escalation on low-content PDFs (< 300 chars) may trigger unnecessary re-OCR on docs with legitimate sparse content | Gate escalation on `ext == '.pdf'` and `total_chars < 300` only; the 300-char floor is calibrated to the corpus (highest affected doc is القرار التنظيمي at 230 garbled chars; legitimate sparse docs in the corpus all exceed 400 chars). Non-PDF formats are unaffected. |
| D3 RTL reversal detection may not generalize beyond the governance vocabulary | Use expanded Arabic common-words list covering legal, administrative, and general domains; readability scoring is vocabulary-dependent |
| D4 Arabic heading injection may over-promote lines that contain structural markers but are not headings (e.g., quoted references to articles) | Apply injection only to lines at the start of a text block (not mid-paragraph); require structural marker to be the dominant content of the line |
| D5 relaxed `max_leaf_ratio` (0.20 -> 0.40 for node_count <= 5) increases leaf-concentration allowance for very small docs, potentially promoting genuinely degenerate stub trees with 3-5 nodes | Scoped to `node_count <= 5` only (docs with 6+ nodes retain 0.20 threshold); run corpus-wide impact check: grep all docs with node_count 3-5 and leaf_concentration 0.20-0.40 from the prior run to verify no degenerate stubs would be promoted |
| D6 collapsing adjacent image markers risks dropping legitimate distinct adjacent images in multi-image documents | Dedup only markers with no intervening content (whitespace-only gap); markers separated by any text/content are preserved. RFC-018 D0 multi-region design is explicitly protected. |
| D7 chunked-Docling for large docs may produce discontinuities at chunk boundaries (split headings, broken tables) | Use page-boundary splitting only (no mid-page splits); accept minor heading-level discontinuities at chunk joins; the tree builder's `_relevel_by_containment` normalizes heading depth across the concatenated output. Text-layer-only fallback is a last resort with known quality trade-off (MARGINAL verdict). |
| D2/D7 interaction: forcing full-page OCR on low-content scanned Arabic PDFs (< 300 chars; one is 21 pages / 11.5MB, another 7MB) increases per-doc processing time on the same CPU-bound Docling/Tesseract pipeline that D7 exists to protect from timeouts | D2's affected docs are 7-21 pages (well under the 150-page `MAX_DOCLING_PAGES` threshold), so D7's chunked route does not apply to them. However, OCR escalation does increase processing time. Mitigation: D2 docs are small enough that `force_full_page_ocr` completes within existing `CHILD_TIMEOUT` for < 30 pages. The batch plan sequences D2 in Batch 1 and D7 in Batch 3; if D2 creates new timeouts before D7 lands, they will surface as ERROR (not silent data loss) and D7 can be expedited. |
| D8 audit correction may invalidate downstream analysis built on the incorrect row #23 data | Review all trace findings that reference landscape/portrait twin asymmetry; confirm they reference the corrected data |

---

## Estimated Effort

- D0: ~20 lines, 45 min
- D1: ~40 lines, 1.5 hours
- D2: ~30 lines, 2 hours
- D3: ~60 lines, 2.5 hours
- D4: ~40 lines, 1.5 hours
- D5: ~5 lines, 20 min
- D6: ~8 lines, 20 min
- D7: ~60 lines, 3 hours
- D8: ~15 min (audit doc edit)

**Total:** ~263 lines, ~11.5 hours across 4 batches

---

## Cross-References

- **Audit report:** `audit/CORPUS_REINGESTION_AUDIT_RUN-10.md`
- **Prior RFC:** RFC-026 (Run 9, verdict gate hardening + rotation detection) -- commit 6113ba3
- **D0 trace:** Finding #4 (Unfallversicherung `_flat_block_text` conflation)
- **D1 trace:** Findings #6 (ward-597 garble bypass), #15 (image_enrichment_promoted garble gap detail)
- **D2 trace:** Findings #0, #1, #5, #7 (zero-content Arabic PDFs, OCR escalation gap)
- **D3 trace:** Finding #8 (siyasat-hawkama RTL reversal)
- **D4 trace:** Finding #9 (marsoom-biqanoon flat tree, Arabic heading injection)
- **D5 trace:** Finding #3 (GHV-TKV-Tarif leaf-ratio false positive)
- **D6 trace:** Finding #14 (pie chart JPG duplicate markers)
- **D7 trace:** Finding #10 (world-stats-pocketbook timeout)
- **D8 trace:** Finding #12 (landscape/portrait twin audit-reporting defect)
- **Deferred from RFC-026:** D6 CMap Latin-gibberish detection (Haftpflicht root cause)
