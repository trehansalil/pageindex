# OCR / Image-Block Pipeline Conflation — Investigation Report

**Date:** 2026-07-27
**Branch:** `feat/image-block-picture-ocr`
**Status:** Investigation complete — regression hypothesis **CONFIRMED**
**Method:** 6-agent parallel investigation (CodeGraph trace, Serena symbol read, git diff analysis, doc_store/MinIO comparison, past-decision search, client.py OCR escalation trace) on Sonnet models, orchestrated from Opus coordinator. Code exploration via codebase-memory CodeGraph + Serena LSP strictly in parallel.

---

## Executive Summary

The `feat/image-block-picture-ocr` branch introduced a per-picture OCR + VLM description pipeline to capture non-textual content (charts, infographics, photos) as first-class retrievable artifacts. **However, this pipeline conflates with the proven page-level OCR escalation pipeline**, causing OCR-recovered text from scanned/image-dominant PDFs to be structurally reclassified from `prose` blocks into `image`-block `ocr_text` fields — degrading retrieval granularity, content classification accuracy, and text continuity.

The conflation is **architectural, not a simple bug**: the per-picture OCR runs unconditionally inside `pdf_to_markdown_docling()` (including during `force_full_page_ocr` escalation calls), and Docling's layout model has no gate distinguishing "small embedded chart" from "whole scanned page classified as a PictureItem."

Additionally, the existing [IMAGE_BLOCK_INGESTION_SCALING_AUDIT_2026-07-21](IMAGE_BLOCK_INGESTION_SCALING_AUDIT_2026-07-21.md) found 15 verified findings including a **critical thread-local boundary bug** (finding 1) that makes the entire image-block enrichment feature silently no-op in production.

---

## Root Cause Chain

### 1. Per-picture OCR fires unconditionally inside the converter

`pdf_to_markdown_docling()` (converters.py:1611-1772) always calls `_recover_picture_results()` at line 1772 before returning. This function:

- Enumerates **every** `PictureItem` from Docling's layout model via `_collect_picture_regions()` (converters.py:1280)
- Crops each region from the raw PDF via PyMuPDF at 300 DPI
- Runs Tesseract OCR on each crop independently via `_tesseract_ocr_image()` (converters.py:1318)
- Returns `pic_results` alongside the markdown

**There is no filter** distinguishing a small chart (intended target) from a whole scanned page misclassified as a `PictureItem` by Docling's RT-DETRv2 layout model.

### 2. OCR escalation also triggers per-picture OCR

When `client.py::index()` detects garbling or image-dominance and escalates via `pdf_to_markdown_docling(file_path, force_full_page_ocr=True, langs)`, the per-picture OCR runs **again** on the re-extracted document. This means:

- Full-page OCR produces text in the markdown body (correct)
- `_recover_picture_results` crops residual `PictureItem` regions and OCRs them **a second time** via a separate Tesseract invocation (redundant/conflicting)
- Both OCR passes compete, and the per-picture results override the inline text for flat-routed docs

### 3. Text reclassification from prose to image blocks

**Before this branch (master):**

- `<!-- image -->` markers were neutral text that fell through into `prose` blocks in `route_and_extract_flat()`
- Any chart text recovered via OCR was appended as `> [Chart text]: ...` and swept into the same prose block
- All extracted text was searchable via `block["text"]` uniformly

**After this branch:**

- `splice_figure_markers()` (converters.py:1440) replaces `<!-- image -->` with `[Figure: fig-N]`
- `route_and_extract_flat()` (helpers.py:1426) now recognizes `_FLAT_FIGURE_RE` and creates `{"role": "image", "index": N, "ocr_text": ...}` blocks
- OCR text moves from `block["text"]` (prose) to `block["ocr_text"]` (image block)
- The `content_class` computation only counts `{"table", "kv", "prose"}` signals — image blocks are invisible to classification

### 4. Scanned pages can bypass the 50% image-dominant threshold

The D1 image-dominant escalation (client.py:643-700) only fires when >50% of markdown lines are `<!-- image -->`. A mixed document where scanned pages are interspersed with text pages may not cross this threshold, so:

- The page-level `force_full_page_ocr` rescue never fires
- The scanned page's content is recovered only via the lower-fidelity per-picture crop OCR
- That text lands as `ocr_text` on an `image` block rather than flowing as `prose`

### 5. Kill-switch coupling

`_OCR_ESCALATION` (converters.py:1240, duplicated in client.py:115) gates **both** the page-level OCR escalation ladder AND the per-picture crop OCR. Toggling it to address one behavior inadvertently disables the other.

---

## Evidence Summary

| Evidence Source                                 | Finding                                                                                                                                 |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| CodeGraph trace (`_recover_picture_results`)  | Fires unconditionally inside`pdf_to_markdown_docling`, including during `force_full_page_ocr` escalation — no page-coverage filter |
| Serena symbol read (`route_and_extract_flat`) | `content_signals` excludes image blocks; OCR text in image blocks never counted toward `content_class`                              |
| Git diff analysis                               | OCR text MOVED from`block["text"]` prose to `block["ocr_text"]` image field — topological relocation, not duplication              |
| Past-decision search (RFC-005/010/016)          | OCR escalation was designed as a text-recovery ladder, image blocks as a separate non-text capture concern — never meant to overlap    |
| Audit 2026-07-21, Finding 1                     | Thread-local boundary makes entire enrichment pipeline dead code in production (`asyncio.to_thread` boundary)                         |
| Audit 2026-07-21, Finding 6                     | Tree-route markdown received unresolvable`[Figure: fig-N]` markers (fixed in working tree)                                            |
| Audit 2026-07-21, Finding 4                     | Sparse-key ordinal drift could attach wrong figure's OCR to wrong block                                                                 |
| `fix2-fix4-table-format-findings.md` memory   | Pre-existing warning:`.jpg` Tesseract OCR route never fires; pie-chart data lost as bare `<!-- image -->`                           |

---

## Affected Document Classes

| Document Type                                              | Impact         | Mechanism                                                                                                                                                                                                                        |
| ---------------------------------------------------------- | -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Fully scanned PDFs** (Arabic, garbled text layer)  | HIGH           | Docling classifies entire pages as PictureItems; per-picture OCR fragments text into image blocks; page-level OCR escalation fires but per-picture OCR runs again redundantly                                                    |
| **Pure image files** (.jpg, .png)                    | **HIGH** | C5`image_to_markdown()` path **never calls** `_enrich_image_blocks` or `splice_figure_markers` — chart/picture content becomes literal `<!-- image -->` strings with zero OCR text captured (confirmed via MinIO) |
| **Mixed text + chart PDFs** (UAE numbers, stat docs) | MEDIUM         | Chart regions captured as image blocks, BUT some blocks get empty`ocr_text` (decorative gate drops short crops) and rotated charts produce garbled OCR without rotation correction                                             |
| **Flat docs with many small figures**                | LOW-MEDIUM     | May cross 50% image-line threshold by marker density (not content area), triggering unnecessary force_full_page_ocr                                                                                                              |
| **pymupdf4llm-routed PDFs**                          | NONE           | `_pdf_to_markdown_no_pics()` always returns `pics=[]`; no picture pipeline involvement                                                                                                                                       |

## MinIO Evidence (doc_store vs processed outputs)

### Confirmed: Standalone image files lose all chart content

**`image pie chart about labor distribution in january 2025 - Copy.jpg`** (doc_id `f057fafe-...`)

- Processed as `content_class: flat_prose`
- Blocks: `[{"role":"prose","text":"<!-- image -->"},{"role":"title","text":"توزيع العمالة..."},{"role":"prose","text":"<!-- image -->"},{"role":"prose","text":"... footnote ..."}]`
- The pie chart's numeric labels/wedge text = **completely lost** (literal `<!-- image -->` string, zero OCR)
- Surrounding title/footnote text was OCR'd (proving Tesseract ran at the page level)
- **Root cause:** `client.py:529-541` image branch calls `image_to_markdown()` → `_run_md_to_tree()` directly, never calls `_enrich_image_blocks()`/`splice_figure_markers()`

### Confirmed: PDF picture blocks have partial loss + rotation garbling

**`uae_numbers_english_page_16_17_landscape - Copy.pdf`** (doc_id `ea779d28-...`)

- 7 image blocks; `index:0` has **no `ocr_text` key** (dropped by `_PICTURE_OCR_MIN_CHARS` decorative gate)
- Indices 1-6 got real OCR: `"Billion Dirhams 1975 1980 1985 1990..."`

**`uae_numbers_english_page_16_17_portrait - Copy.pdf`** (doc_id `f282f8ec-...`)

- Image OCR text present but garbled/reversed: `"0c0e GLO OLOZ G00C 0002 G66L O66L SG86L O86L SZ/6L"`
- Consistent with OCR on a rotated crop (landscape chart in portrait page) without rotation correction

### Non-regression: Arabic text extraction OK

**`وارد رقم 597...pdf`** — text extraction looks correct (`verdict: PASS`, `cat_b_promoted`), Arabic prose/title blocks are legible. The known BiDi/reshaping issue in tree-doc titles is pre-existing (memory: `fix1-redesign-and-tessdata-prebake.md`), not this branch.

---

## Fix Recommendations (Priority-ordered)

### P0a: Add picture enrichment to standalone image file path

**File:** `client.py:529-541` (image branch in `index()`)
**What:** The `.jpg/.png/.tiff` ingestion branch calls `image_to_markdown()` → `_run_md_to_tree()` directly and never runs `splice_figure_markers()` or `_enrich_image_blocks()`. Chart/picture content in standalone images becomes literal `<!-- image -->` strings with zero OCR text. Either:

- Route standalone images through the same picture-enrichment pipeline as PDF image blocks, OR
- Ensure `image_to_markdown()` itself handles embedded `<!-- image -->` markers by running Tesseract on the full image (which it already does — the issue is that Docling still emits `<!-- image -->` markers for sub-regions even when the source is a pure image file)

### P0b: Decouple per-picture OCR from page-level OCR escalation

**File:** `converters.py:1496-1531` (`_recover_picture_results`)
**What:** Add a page-coverage filter: skip any picture region whose bbox area exceeds N% of the page area (e.g., >60%). Full-page-sized regions should be handled by the page-level OCR escalation, not per-picture cropping.

```python
# In _recover_picture_text, after computing rect:
page_area = page.rect.width * page.rect.height
region_area = rect.width * rect.height
if region_area / page_area > 0.6:  # full-page scan, not a chart
    continue
```

### P1: Separate kill-switches for escalation vs enrichment

**Files:** `converters.py:1240`, `client.py:115`
**What:** Split `_OCR_ESCALATION` into two independent env vars:

- `OCR_ESCALATION` — controls page-level garbling/image-dominant retry (existing behavior)
- `IMAGE_ENRICH` — controls per-picture crop/OCR/VLM enrichment (new behavior)

### P2: Count image blocks with substantial OCR text as prose signals

**File:** `helpers.py:1540` (`route_and_extract_flat`)
**What:** When an image block's `ocr_text` exceeds a threshold (e.g., >100 chars), also add `"prose"` to `content_signals` so that documents whose real content was captured via image-block OCR get correctly classified.

### P3: Skip per-picture OCR on force_full_page_ocr escalation calls

**File:** `converters.py:1772` (end of `pdf_to_markdown_docling`)
**What:** When `force_full_page_ocr=True`, the call is a page-level text-recovery attempt — per-picture OCR is redundant and produces competing results. Either skip `_recover_picture_results` entirely on escalation calls, or pass the force flag through and let the function decide.

### P4: Fix the thread-local boundary bug (audit finding 1)

**Status:** Working tree shows a partial fix (return-value plumbing instead of thread-local). Needs completion and testing under `asyncio.to_thread` to confirm `pic_results` actually reaches `_enrich_image_blocks`.

### P5: Restore prose fallback for unresolved figure markers

**File:** `helpers.py:1485-1510` (`route_and_extract_flat` figure-marker block)
**What:** When `splice_figure_markers` bails on marker-count mismatch (returns markdown with bare `<!-- image -->`), the `_FLAT_FIGURE_RE` regex won't match, and `<!-- image -->` falls into prose accumulation as literal text. This is the correct fallback behavior — but verify that `_flat_search_text` still captures the chart text that was appended as blockquote on master (it should, since it would be in a prose block's `text`).

---

## Constraint Compliance Flags

- **VLM stays off by default** — RFC-004 user-LOCKED; nothing in the fix plan depends on VLM
- **Granite-258M permanently rejected** — user-LOCKED 2026-06-12
- **Every new LLM call must ride ZDR-routed client** — HR3; audit findings 2-3 flagged two violations
- **Flat extraction stays pure/in-process** — FLAT-01/FLAT-05; fixes must not introduce LLM/MinIO into `route_and_extract_flat`
- **Never claim vectorless beats vector on accuracy** — HR1; all retrieval improvements positioned on architecture

---

## Addendum 2026-07-28: RFC-022 B3 Diagnosis (GHV-TKV-Tarif.pdf, Task 3.1)

**Status:** Diagnosis complete — the three hypothesized causes in [RFC-022 B3](../agents/rfcs/022-run5-verdict-bugfixes.md#b3-ghv-tkv-ocr-splice-regression) (P0b filter too aggressive / post-processing dropping OCR / OCR-escalation decoupling incomplete) are **all ruled out** for Doc 3. Root cause is a **fourth, previously undocumented mechanism**: table-block content is invisible to text-based char-count scoring.

**Method:** Live trace against `doc_store/GHV-TKV-Tarif.pdf` (the actual corpus file) through `pdf_to_markdown_docling` → `splice_picture_text_for_tree` → `splice_figure_markers` → `route_and_extract_flat`, instrumented with debug logging added at each splice call site in `client.py` (`_log_pic_splice_trace`, RFC-022 Task 3.1).

**Findings:**

1. **P0b (page-coverage filter) does not engage.** Docling detects 4 `PictureItem` regions on the doc's single page. Their coverage is 5.4%, 0.9%, 0.1%, 0.1% of page area — all far under the 60% `_PICTURE_PAGE_COVERAGE_THRESHOLD` (converters.py:1341/1501). The filter never fires; it is not the cause.

2. **The 4 regions are correctly handled, not lost:**
   - Region 0 (5.4% coverage): `skipped_reason="clip_text"` — PyMuPDF finds 380 chars of raw text under this bbox, which is the pricing table Docling *already* extracted as a proper markdown table elsewhere in the output. Correctly skipped (D1, RFC-018); no content loss.
   - Regions 1-3 (<1% coverage each): genuine tiny logo/footnote-marker crops. Tesseract OCR runs on each and returns text below `_PICTURE_OCR_MIN_CHARS` (20 chars) — correctly treated as decorative, `ocr_text=""`.
   - Net: **zero genuine chart/infographic content is lost in the per-picture OCR pipeline** for this document. The pipeline is working as designed.

3. **The real 4,267→375 char drop happens downstream in `route_and_extract_flat`, not in OCR splice.** Live trace: raw Docling markdown is 13,022 chars (single page, 3 well-formed markdown tables + prose). After `route_and_extract_flat`, `content_class="flat_mixed"`, 23 blocks, **but `sum(len(b.get("text","")))` across all blocks is exactly 375** — matching the audit's reported figure. Cause: `role="table"` blocks carry **no `"text"` key by design** (helpers.py:1393-1398) — parsed cell content lives in `headers` / `rows` / `row_records` instead, consumed separately by `_flat_search_text` (helpers.py:2055-2062) for retrieval. Any code that measures document content via `block.get("text", "")` — including the corpus audit's own char-count diagnostic — sees 0 chars for all 3 table blocks and silently misses ~180+ pricing figures that are present and correctly structured in the output.

4. **Secondary, minor contributor:** of the 4 `<!-- image -->` markers, 1 (region 0) is correctly stripped by `splice_figure_markers` (`skipped_reason` set → `STRIP_SKIPPED_IMAGE_MARKERS=true` default removes it). The other 3 (regions 1-3) have no `ocr_text`/`description` **and** no `skipped_reason` (OCR genuinely ran, just returned nothing — that code path never sets `skipped_reason`), so they fall through `splice_figure_markers`'s `_repl` unresolved and survive into prose blocks as literal `"<!-- image -->"` text (14 chars × 3 = 42 chars of noise). This matches the investigation's P5 "correct fallback" behavior — it is cosmetic, not the source of the regression.

5. **Cross-cutting risk for the RFC-022 B1-Fix:** the proposed synthetic-structure builder in `client.py` (`{"title": "", "text": b.get("text", "")}` for each block) inherits the exact same blind spot. For any table-heavy flat doc, B1's synthetic structure will still see near-empty content and fail to promote, because it does not fold `row_records` into node text either.

**Recommended fix direction for Task 3.2 (not implemented here — diagnosis only):**
- Primary: wherever flat-doc content is measured for scoring/promotion (the B1-Fix synthetic-structure builder, and any audit char-count tooling), include verbalized `row_records` text for `role=="table"` blocks alongside `block["text"]` — e.g. `b.get("text", "") or " ".join(b.get("row_records", []))`. Small, low-risk, mirrors the pattern `_flat_search_text` already uses.
- Secondary (cosmetic, optional): also strip bare unresolved `<!-- image -->` markers in `splice_figure_markers` when OCR ran but returned nothing (not just when `skipped_reason` is set), to remove the ~42-char literal-marker noise from prose blocks.
- None of the three RFC-022 B3 hypotheses (P0b too aggressive, post-processing dropping valid OCR, OCR-decoupling incomplete) apply to Doc 3 — no code change to the picture-OCR/escalation pipeline itself is needed for this document.

## Related Artifacts

- [IMAGE_BLOCK_INGESTION_SCALING_AUDIT_2026-07-21](IMAGE_BLOCK_INGESTION_SCALING_AUDIT_2026-07-21.md) — 15 verified findings against the same branch
- `agents/contracts/ocr-01.yaml` — OCR escalation contract (OCR-01-C1/C2/C3)
- `agents/contracts/conv-01.yaml` — Format converter contract (CONV-01-C5 image dispatch)
- `agents/rfcs/010-corpus-gap-remediation.md` — D1 image-dominant escalation design
- `agents/rfcs/016-vlm-garble-fallback.md` — VLM last-resort garble fallback
- `fix2-fix4-table-format-findings.md` (memory) — Pre-existing warning about `<!-- image -->` data loss
