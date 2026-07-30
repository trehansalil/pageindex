<!-- Space: CITRA -->
<!-- Title: RFC-018: Corpus Audit Remediation -->
<!-- Folder: RFCs -->

---

id: RFC-018
title: Corpus Audit Remediation
status: proposed
date: 2026-07-27
plan-impact: yes
supersedes-decisions-in: [RFC-017 D1]
---------------------------

## Context

The 25-doc corpus re-ingestion audit ([`audit/CORPUS_REINGESTION_AUDIT_2026-07-27.md`](../../audit/CORPUS_REINGESTION_AUDIT_2026-07-27.md)) validated the full ingestion pipeline on the `feat/image-block-picture-ocr` branch. Results: **7 PASS, 10 MARGINAL, 8 FAIL**. RFC-017's P0a (standalone image enrichment) and P0b (page-coverage filter) are implemented but **not working end-to-end**: P0a hits a marker-count mismatch on every standalone image, and P0b's area-only filter is insufficient for sub-60% chart regions with clean embedded text. Additionally, two failure modes outside RFC-017's scope dominate the failure count: Arabic RTL text reversal (7 docs) and garble-gate numeric-junk hole (3 docs).

Full investigation at [`audit/RFC018_INVESTIGATION_LOG_2026-07-27.md`](../../audit/RFC018_INVESTIGATION_LOG_2026-07-27.md).

### What this RFC covers

| Scope | Description |
|-------|-------------|
| D0: P0a marker-count fix | Match synthetic PictureResult count to Docling's `<!-- image -->` marker count for standalone images |
| D1: P0b text-layer availability check | Skip per-picture OCR when the PDF already has clean extractable text under the bbox |
| D2: Arabic RTL reversal hardening | Detect and correct Tesseract LTR-scanned Arabic where `reconstruct_bidi_order` is insufficient |
| D3: Garble-gate numeric-junk probe | Pre-conversion text-layer garble probe + per-node garble checks |

### Out of scope

| Item | Why |
|------|-----|
| P1: Separate kill-switches for escalation vs enrichment | Lower priority, no data loss — deferred |
| P2: Image blocks as prose signals in `content_signals` | Classification improvement — deferred |
| P3: Skip per-picture OCR on `force_full_page_ocr` calls | Optimization — deferred |
| P4: Thread-local boundary fix | Already partially fixed in working tree; completion deferred |
| F5: Image enrichment inconsistency | Symptom of D0/D1 + deferred P4 |

### 5 cross-cutting failure modes from audit

| # | Failure Mode | Docs Affected | Severity | This RFC |
|---|-------------|---------------|----------|----------|
| F1 | P0a marker-count mismatch | 1 (entry 13) | CRITICAL | **D0** |
| F2 | P0b text-layer gap (sub-60% charts) | 2 (entries 14-15) | HIGH | **D1** |
| F3 | Arabic RTL text reversal | 7 (entries 17-23, 25) | CRITICAL | **D2** |
| F4 | Garble-gate numeric-junk hole | 3 (entries 7, 18, 24) | MEDIUM | **D3** |
| F5 | Image enrichment inconsistency | 5 (entries 3, 9, 16, 17, 25) | MEDIUM | Resolved by D0/D1 + P4 |

## Hard Rule constraints (CLAUDE.md binding)

| Rule | Compliance |
|------|-----------|
| **HR1** — Never claim vectorless beats vector on accuracy | N/A — no positioning changes |
| **HR2** — Right-to-erasure cascade | No new derived stores. D0 uses existing `figures/<doc_id>/` prefix. D2/D3 are in-place text transforms |
| **HR3** — PII routing through ZDR tier | No new LLM egress. D0/D1 use local Tesseract only. D2 is pure unicode computation. D3 uses local fitz text extraction |
| **HR4** — AGPL-3.0 awareness | No new AGPL imports. D1/D3 use fitz already imported at `converters.py:1371`. D2 uses python-bidi already imported at `converters.py:1220` |
| **HR5** — Never silently persist low-quality tree | D3 **improves** quality gate — garbled text layers caught earlier trigger OCR escalation instead of silently persisting junk |

## User-locked constraints

| Constraint | Source | Impact |
|------------|--------|--------|
| VLM stays OFF by default | RFC-004, user-locked 2026-06-12 | No decision in this RFC depends on VLM |
| Granite-258M permanently rejected | User-locked 2026-06-12 | No local model invocations |

## Decisions

### D0 — Fix P0a marker-count mismatch for standalone images

**Problem:** RFC-017 D1 creates **1 synthetic PictureResult** for a standalone image (`client.py:540-545`). However, Docling's `export_to_markdown()` called inside `image_to_markdown()` produces **N `<!-- image -->` markers** — Docling's RT-DETRv2 layout model detects sub-regions even within a single-page image file. The count guard at `splice_figure_markers` (`converters.py:1462-1470`) detects `marker_count (N) != len(pics) (1)` and bails — returning markdown unchanged with bare `<!-- image -->` strings. No `[Figure: fig-N]` markers are produced, `_enrich_image_blocks` is a no-op, and chart content is completely lost.

**Evidence:** Audit entry 13 — pie chart JPG gets 0 figures, 0 enrichment, literal `<!-- image -->` strings in output. MinIO confirms `f057fafe-` has `content_class: flat_prose` with bare `<!-- image -->` in text blocks.

**Decision:** After `image_to_markdown()` returns, count the `<!-- image -->` markers in the produced markdown and create that many duplicate PictureResults — all pointing to the same source image bytes (the standalone image IS every detected sub-region).

**File:** `src/pageindex_mcp/client.py`, lines 537-545 (standalone image branch)

**Code change:**

```python
md_content = await asyncio.to_thread(image_to_markdown, file_path, img_langs)
# D0 (RFC-018): standalone image IS the picture — create N synthetic
# PictureResults matching the marker count so splice_figure_markers'
# count guard passes. All N point to the same source bytes.
img_bytes = await asyncio.to_thread(Path(file_path).read_bytes)
marker_count = md_content.count("<!-- image -->")
pic_results = [PictureResult(
    ocr_text="",
    page=1,
    bbox={"l": 0, "t": 0, "r": 0, "b": 0},
    png_bytes=img_bytes,
)] * max(1, marker_count)
```

**Rationale:** All N PictureResults contain the same `png_bytes` — the whole source image. This is correct because the source is a standalone image file, not a multi-page PDF with distinct charts. The count guard passes (`N == N`), `splice_figure_markers` runs, `_enrich_image_blocks` uploads the PNG to MinIO once per figure reference. `ocr_text=""` because `image_to_markdown()` already ran full-page Tesseract — per-picture OCR text would be redundant.

**Dense-list interaction:** `_recover_picture_results()` (line 1532) builds a dense list with empty `PictureResult()` placeholders for D0-filtered regions. For standalone images, `_recover_picture_results` is never called (the PDF converter path, not the image path), so no interaction.

**Supersedes:** RFC-017 D1 (single synthetic PictureResult). The marker-count edge case noted in RFC-017 as "correct degradation" is actually the **dominant** case.

### D1 — Text-layer availability check before per-picture OCR

**Problem:** The page-coverage filter (RFC-017 D0, `converters.py:1387-1390`) correctly skips PictureItems covering >60% of the page. But chart regions covering **<60% of the page** that sit on top of a **clean embedded text layer** still get per-picture OCR applied. The OCR crop produces garbled/fragmented text that replaces the accurate vector text already extracted by Docling into the markdown body.

**Evidence:** Audit entries 14-15 — UAE numbers landscape/portrait: 4 charts each ~30-40% page area, below 0.6 threshold, per-picture OCR fires, all quantitative data lost or reversed/scrambled.

**Decision:** In `_recover_picture_text()` Phase 1 loop, after the D0 area check at `converters.py:1390`, add a text-layer availability probe: extract text under the picture bbox using fitz's `page.get_text("text", clip=rect)`. If the clip contains more than `_PICTURE_OCR_MIN_CHARS` (20) characters of extractable text, skip per-picture OCR for that region — the text layer is clean and Docling already captured it.

**File:** `src/pageindex_mcp/converters.py`, lines 1390-1391 (Phase 1 loop, after area check)

**Code change:**

```python
# D0: skip regions covering >60% of page — full scanned pages, not charts.
page_area = page.rect.width * page.rect.height
if page_area > 0 and (rect.width * rect.height) / page_area > _PICTURE_PAGE_COVERAGE_THRESHOLD:
    continue
# D1 (RFC-018): skip per-picture OCR when clean text already exists under
# this bbox — Docling already extracted it into the markdown body.
clip_text = page.get_text("text", clip=rect).strip()
if len(clip_text) > _PICTURE_OCR_MIN_CHARS:
    continue
pix = page.get_pixmap(clip=rect, dpi=300)
```

**Rationale:** `page.get_text("text", clip=rect)` returns text from the PDF's embedded text layer within the given rectangle — no OCR, pure vector extraction. If it returns substantial text (>20 chars, matching the existing decorative gate `_PICTURE_OCR_MIN_CHARS`), the PDF has a clean text layer for that region and per-picture OCR would only degrade quality. `fitz` is already imported at line 1371 and the `page` object is already open. No new AGPL surface.

**Interaction with OCR escalation:** When `force_full_page_ocr=True`, Docling re-runs Tesseract on the whole page. The text layer in such PDFs is typically garbled (that's why escalation fired). `page.get_text("text", clip=rect)` would return the garbled text layer, which is likely <20 chars of useful content, so per-picture OCR would still fire. This is correct — scanned pages need per-picture OCR; vector-text pages don't.

### D2 — Arabic RTL reversal hardening

**Problem:** 7/25 documents (entries 17-23, 25) have Arabic text stored in reversed character order — e.g. "دراوملا ةرازو" instead of "وزارة الموارد". The existing `reconstruct_bidi_order()` (`converters.py:1204-1229`, RFC-015 D7) uses python-bidi's `get_display()` to convert visual-order Arabic back to logical reading order. It works for simple visual-order text. **However, Tesseract OCR on scanned Arabic may produce a different kind of reversal** — character sequences in LTR scan order rather than true visual order — that `get_display()` cannot fix because the UAX #9 algorithm assumes proper visual-order input.

**Root cause:** Tesseract reads text left-to-right by default. Even with `lang=ara`, it may emit characters in encounter order (LTR scan) rather than logical reading order. The resulting text has valid Arabic characters but reversed word order, and sometimes reversed character order within words. `reconstruct_bidi_order`'s `get_display()` assumes visual-order input (characters correct, just reversed) — but LTR-scanned Arabic is a different corruption that `get_display()` may not fully correct.

**Decision:** Add a post-bidi Arabic word-reversal detection + correction step in `_pre_inference_normalize()`, after `reconstruct_bidi_order` runs. The new `_fix_residual_rtl_reversal()` function:

1. Detects if a line has >50% Arabic characters (U+0600-U+06FF, U+FB50-FDFF, U+FE70-FEFF)
2. For such lines, checks whether reversing the word order produces a higher "readability score" (measured by common Arabic word patterns like definite articles ال, common prepositions في/من/على/إلى, and word-boundary coherence)
3. If reversal scores higher, applies the reversal

**File:** `src/pageindex_mcp/converters.py`, new function after `reconstruct_bidi_order` (line ~1230)

**Code sketch:**

```python
_AR_COMMON_WORDS = frozenset(["في", "من", "على", "إلى", "أن", "هذا", "هذه",
                               "التي", "الذي", "عن", "مع", "بين", "كان", "ما"])
_AR_DEFINITE_RE = re.compile(r"\bال\w+")

def _fix_residual_rtl_reversal(text: str) -> str:
    """Detect and correct residual Arabic word-reversal after bidi reordering."""
    if not text:
        return text
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        arabic = sum(1 for c in stripped if _is_arabic_char(c))
        if arabic / len(stripped) < 0.5:
            out.append(line)
            continue
        words = stripped.split()
        reversed_words = list(reversed(words))
        fwd_score = _arabic_readability_score(words)
        rev_score = _arabic_readability_score(reversed_words)
        if rev_score > fwd_score:
            indent = line[:len(line) - len(line.lstrip())]
            trail = line[len(line.rstrip()):]
            out.append(indent + " ".join(reversed_words) + trail)
        else:
            out.append(line)
    return "".join(out)

def _arabic_readability_score(words: list[str]) -> int:
    score = 0
    for w in words:
        if w in _AR_COMMON_WORDS:
            score += 2
        if _AR_DEFINITE_RE.match(w):
            score += 1
    return score

def _is_arabic_char(c: str) -> bool:
    cp = ord(c)
    return (0x0600 <= cp <= 0x06FF or 0xFB50 <= cp <= 0xFDFF or 0xFE70 <= cp <= 0xFEFF)
```

**Application point:** In `_pre_inference_normalize()` (`converters.py:1492`), after the `reconstruct_bidi_order` call:

```python
text = reconstruct_bidi_order(text)
text = _fix_residual_rtl_reversal(text)
```

**Rationale:** This is a heuristic — not all reversed Arabic can be detected by word-frequency scoring. But it covers the dominant pattern seen in the corpus (whole-line word-order reversal from Tesseract LTR scanning). The readability comparison ensures we only reverse when it improves the text — no false positives on already-correct text (forward score >= reverse score → no change). Pure local computation, no LLM (HR3).

**Risk:** Mixed-direction lines (Arabic + Latin/digits) may have partial reversals that word-level reversal cannot fix. This is accepted as a known limitation — a full fix requires Docling-level Tesseract `--psm` mode tuning for RTL pages.

### D3 — Garble-gate numeric-junk probe

**Problem:** The garble gate (`_is_garbled_blob()` at `helpers.py:567`) includes a digit-ratio check (>60% on blobs >500 chars) that should catch numeric-junk text layers. But `validate_tree` flattens **tree node text** — which contains Docling's **OCR output** (clean Arabic), not the raw PDF text layer. The raw text layer (e.g. "1651001429" × 3500 = 89.4% digit ratio in وارد 597) is never independently tested against the garble gate. Documents with garbled text layers that Docling happens to OCR cleanly pass the gate — but documents where Docling's OCR also fails (القرار التنظيمي, entry 18: mojibake Arabic, 68 unresolved `<!-- image -->` markers) fall through with junk.

**Evidence:** Audit entry 24 (وارد 597): numeric-junk text layer but Docling OCR recovered clean Arabic (lucky). Entry 18 (القرار التنظيمي): mojibake Arabic, 68 unresolved markers, PUA diluted across 99 nodes below 3% threshold. Entry 7: garbled text layer passed through.

**Decision:** Two-pronged fix:

**D3a — Pre-conversion text-layer probe:** Before Docling converter runs, extract a text sample from the first page via `fitz.open(path)[0].get_text()` and run `_is_garbled_blob()` on it. If garbled, pass `force_full_page_ocr=True` upfront — skip the initial non-OCR attempt and go straight to full-page Tesseract.

**File:** `src/pageindex_mcp/client.py`, in the PDF branch of `index()`, before the first `pdf_to_markdown_docling()` call

**Code sketch:**

```python
# D3a (RFC-018): pre-conversion text-layer probe. If the raw PDF text
# layer is garbled, skip straight to force_full_page_ocr=True.
pre_garbled = False
try:
    import fitz
    with fitz.open(file_path) as probe_pdf:
        if probe_pdf.page_count > 0:
            raw_text = probe_pdf[0].get_text()
            if raw_text.strip() and _is_garbled_blob(raw_text):
                pre_garbled = True
                logger.info("D3a: raw text layer garbled, forcing full-page OCR upfront")
except Exception:
    pass  # probe failure is non-fatal

if pre_garbled:
    md_content, pic_results = await asyncio.to_thread(
        pdf_to_markdown_docling, file_path, True, ocr_langs
    )
else:
    md_content, pic_results = await asyncio.to_thread(
        pdf_to_markdown_docling, file_path, False, ocr_langs
    )
```

**D3b — Per-node garble check:** In addition to the bulk flattened-text garble check in `validate_tree`, run `_is_garbled_blob()` on each tree node's text individually. A single mojibake node (e.g. القرار التنظيمي's PUA-heavy nodes) diluted across 99 clean nodes escapes bulk ratio detection but would be caught per-node.

**File:** `src/pageindex_mcp/helpers.py`, in `validate_tree()` or as a post-tree-build cleanup pass

**Code sketch:**

```python
def _garble_check_nodes(nodes: list[dict]) -> int:
    """Count nodes with garbled text. Returns garbled node count."""
    garbled = 0
    for node in nodes:
        text = node.get("text", "")
        if text.strip() and _is_garbled_blob(text):
            garbled += 1
        children = node.get("nodes") or []
        garbled += _garble_check_nodes(children)
    return garbled
```

If the garbled-node ratio exceeds a threshold (e.g. >10% of total nodes), surface as `low_quality_tree` error per HR5.

**Rationale:** D3a catches the common case (numeric-junk text layers) early, avoiding a wasted non-OCR conversion attempt. D3b catches the long-tail case (PUA-heavy nodes diluted in bulk) that D3a misses when the text layer is partially valid. Together they close the garble-gate hole identified in Fix-2/Fix-4 findings (memory: `fix2-fix4-table-format-findings.md`, known since 2026-06-30).

## Implementation Plan

| Batch | Step | Change | File | Decision |
|-------|------|--------|------|----------|
| 0 | 1 | Match marker count for standalone image PictureResults | `client.py:537-545` | D0 |
| 0 | 2 | Add text-layer check in `_recover_picture_text` Phase 1 loop | `converters.py:1390` | D1 |
| 0 | 3 | Add `_fix_residual_rtl_reversal` + `_arabic_readability_score` + `_is_arabic_char` | `converters.py:~1230` | D2 |
| 0 | 4 | Call `_fix_residual_rtl_reversal` in `_pre_inference_normalize` | `converters.py:1492` | D2 |
| 0 | 5 | Add pre-conversion text-layer garble probe in `index()` PDF branch | `client.py` (PDF branch) | D3a |
| 0 | 6 | Add `_garble_check_nodes` helper + per-node garble check | `helpers.py` | D3b |
| 1 | 7 | Test: standalone image marker-count match (N markers → N PictureResults) | `tests/test_image_blocks.py` | D0 |
| 1 | 8 | Test: text-layer check skips OCR when clip has text | `tests/test_image_blocks.py` | D1 |
| 1 | 9 | Test: text-layer check allows OCR when clip is empty | `tests/test_image_blocks.py` | D1 |
| 1 | 10 | Test: reversed Arabic word-order detected and corrected | `tests/test_rfc010_converters.py` | D2 |
| 1 | 11 | Test: correct Arabic text unchanged by reversal check | `tests/test_rfc010_converters.py` | D2 |
| 1 | 12 | Test: pre-conversion garble probe triggers on numeric-junk | `tests/test_client_contract.py` | D3a |
| 1 | 13 | Test: per-node garble check catches PUA-heavy node in clean tree | `tests/test_storage_meta.py` | D3b |

## Test Strategy

| Decision | Test | Assertion |
|----------|------|-----------|
| D0 | `test_standalone_image_marker_count_match` | Image producing 3 `<!-- image -->` markers → `pic_results` has 3 entries, all with same `png_bytes` |
| D0 | `test_standalone_image_single_marker` | Image producing 1 marker → 1 PictureResult (no regression) |
| D1 | `test_text_layer_skips_picture_ocr` | Region with >20 chars text under bbox → not in `crops` dict |
| D1 | `test_no_text_layer_allows_picture_ocr` | Region with empty text under bbox → present in `crops` dict |
| D1 | `test_text_layer_check_with_area_filter` | Region >60% page AND has text → skipped by area check (D0 takes precedence) |
| D2 | `test_reversed_arabic_word_order_fixed` | "دراوملا ةرازو" → "وزارة الموارد" (correct reading order) |
| D2 | `test_correct_arabic_unchanged` | "وزارة الموارد" → unchanged |
| D2 | `test_mixed_arabic_latin_preserved` | Lines with <50% Arabic → unchanged |
| D3a | `test_garble_probe_numeric_junk` | PDF with 89% digit text layer → `force_full_page_ocr=True` on first call |
| D3a | `test_garble_probe_clean_text` | PDF with clean text layer → `force_full_page_ocr=False` (normal path) |
| D3b | `test_per_node_garble_catches_pua_node` | Tree with 1 PUA-heavy node among 99 clean → garbled count = 1 |

## Risks

1. **D2 heuristic false positives.** The readability score may incorrectly reverse a line that was already in correct order. **Mitigation:** reversal only fires when `rev_score > fwd_score` (strict greater-than, not >=); for ambiguous text where both scores are equal, the original order is preserved. The common-word set is conservative (14 high-frequency function words).

2. **D3a fitz import in client.py.** `fitz` (PyMuPDF, AGPL-3.0) is already imported in `converters.py` but not in `client.py`. **Mitigation:** the import is guarded in a try/except and only fires for PDF files. HR4 compliance: `fitz` is already a transitive dependency via `pymupdf4llm` and `docling`.

3. **D1 text-layer false negatives.** Some PDFs have text layers that return characters from `page.get_text()` but are still garbled (mojibake, wrong encoding). The 20-char threshold may pass garbled text. **Mitigation:** if the text layer is garbled, the garble gate (D3a/D3b) or OCR escalation will catch it downstream. D1's job is only to avoid redundant OCR on clean text layers.

4. **D3b node-count threshold tuning.** The 10% garbled-node threshold may be too aggressive for documents with legitimate numeric nodes (financial tables). **Mitigation:** make the threshold configurable via env var `GARBLE_NODE_RATIO_THRESHOLD`. Start at 10% and tune based on corpus validation.

## Surfaces touched

| Module | Change |
|--------|--------|
| `src/pageindex_mcp/client.py` | D0: marker-count-matching PictureResults; D3a: pre-conversion garble probe |
| `src/pageindex_mcp/converters.py` | D1: text-layer check in `_recover_picture_text`; D2: `_fix_residual_rtl_reversal` + call in `_pre_inference_normalize` |
| `src/pageindex_mcp/helpers.py` | D3b: `_garble_check_nodes` per-node garble check |
| `tests/test_image_blocks.py` | D0/D1 tests |
| `tests/test_rfc010_converters.py` | D2 tests |
| `tests/test_client_contract.py` | D3a tests |
| `tests/test_storage_meta.py` | D3b tests |

## Past decisions referenced

| Decision | Source | Status |
|----------|--------|--------|
| VLM stays OFF by default | RFC-004, user-locked 2026-06-12 | ACTIVE |
| Granite-258M permanently rejected | User-locked 2026-06-12 | ACTIVE |
| Per-picture OCR is complementary to page-level | RFC-015 D6 | ACTIVE — D1 refines the boundary |
| OCR escalation designed as text-recovery ladder | RFC-005/010/016 | ACTIVE — D3a adds an earlier rung |
| Image blocks are separate non-text capture concern | RFC-015 D6 | ACTIVE — D0/D1 enforce separation |
| reconstruct_bidi_order for visual-order Arabic | RFC-015 D7 | ACTIVE — D2 adds post-bidi hardening |
| Garble-gate heuristics (digit, PUA, repetition) | RFC-005 D7 / RFC-013 | ACTIVE — D3a/D3b extend coverage |

## References

- [Investigation log](../../audit/RFC018_INVESTIGATION_LOG_2026-07-27.md) — root cause analysis for F1-F5
- [Corpus re-ingestion audit](../../audit/CORPUS_REINGESTION_AUDIT_2026-07-27.md) — triggering audit
- [OCR/image-block conflation investigation](../../audit/OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION_2026-07-27.md) — architectural conflation analysis
- [RFC-017: OCR/Image-Block Decoupling](017-ocr-image-block-decoupling.md) — predecessor, D1 superseded
- [RFC-015: Corpus Audit Remediation](015-corpus-audit-remediation.md) — D6 per-picture enrichment, D7 bidi
- [RFC-005: Hard Corpus Ingestion Fixes](005-hard-corpus-ingestion-fixes.md) — original OCR escalation + garble gate
- [RFC-010: Corpus Gap Remediation](010-corpus-gap-remediation.md) — D1 image-dominant escalation
- [RFC-016: VLM Garble Fallback](016-vlm-garble-fallback.md) — VLM last-resort escalation
