<!-- Space: CITRA -->
<!-- Title: RFC-016: VLM Last-Resort Garble Fallback (RFC-004 Approach B) -->
<!-- Folder: RFCs -->

---
id: RFC-016
title: VLM Last-Resort Garble Fallback (RFC-004 Approach B)
status: implemented
date: 2026-07-17
plan-impact: yes
supersedes-decisions-in: []
---

## Traceability

| Artifact | Reference |
|---|---|
| Design Document | [design-rfc016-vlm-garble-fallback.md](../designs/design-rfc016-vlm-garble-fallback.md) |
| Implementation Plan | [tasks-rfc016-vlm-garble-fallback.md](../tasks/tasks-rfc016-vlm-garble-fallback.md) |

## Context

RFC-004 (VLM-Based Document-Hierarchy Detection) was accepted with `VLM_MODE=disabled`
as its default — the full VLM hierarchy-detection cascade (D1/D2) was never built because
Phase 0 did not pass the go/no-go bar. However, a distinct failure class remained
unaddressed: **scanned documents with a garbled security-watermark text layer** that
defeats both the primary Docling extractor and the OCR escalation retry.

The motivating document is an Arabic government PDF ("وارد رقم 597 من مكتب أبوظبي
التنفيذي...") — a 42-page scanned document whose text layer consists of a security
watermark repeating `1651001429` ~3,500 times (89.4% digit ratio). The garble gate
correctly rejects this, and OCR escalation (Docling `force_full_page_ocr`) still picks up
the watermark. Real content exists only in the image layer.

This RFC implements **Approach B**: a simpler, scoped VLM fallback that rasterizes pages
via pypdfium2 and sends them to a vision LLM for text extraction, without attempting
hierarchy recovery. It fires only when garbling is the rejection reason, not for
structural failures like `node_count<3` or `depth<2`.

### Relationship to RFC-004

| RFC-004 concept | This RFC |
|-----------------|----------|
| D1/D2 VLM hierarchy-detection cascade | NOT built — Phase 0 failed |
| `VLM_MODE=disabled\|eval\|api\|inline` | Replaced by simpler `VLM_FALLBACK=true\|false` toggle |
| Granite-Docling-258M inline engine | NOT used — user-LOCKED rejected 2026-06-12 |
| `gpt-4.1` vision as frontier engine | Reused — routed via `get_openai_client()` / ZDR lever |
| pypdfium2-only rasterization (HR4) | Reused — no fitz/PyMuPDF in VLM path |
| `validate_tree()` as sole binding guard | Preserved — VLM output re-enters the standard pipeline |

## Decisions

### D1 — Feature toggle: `VLM_FALLBACK` + `VLM_MODEL`

Two new settings in `config.py:84-85`, loaded from env in `_load_settings()` at
`config.py:151-153`:

| Env var | Type | Default | Purpose |
|---------|------|---------|---------|
| `VLM_FALLBACK` | bool | `false` | Enable/disable the VLM last-resort fallback |
| `VLM_MODEL` | str | `gpt-4.1` | Vision-capable model name (supports `azure/` prefix) |

OFF by default. When the model name starts with `azure/`, `vlm_extract_markdown` strips
the prefix before passing to the OpenAI client (the Azure deployment name does not
include the provider prefix).

### D2 — Rasterizer: `rasterize_pdf_pages()` (`converters.py:1827-1850`)

Renders each PDF page to a base64 data-URI PNG via **pypdfium2 only** (HR4 — no
fitz/PyMuPDF). Fixed at 200 DPI per RFC-004 Phase 0 finding (144 DPI caused hierarchy
hallucinations in gpt-4.1). Each page is closed after render to bound memory.

### D3 — VLM extractor: `vlm_extract_markdown()` (`converters.py:1853-1937`)

Async function that:
1. Rasterizes all pages via `rasterize_pdf_pages()`
2. Sends each page image to the VLM via `get_openai_client()` (ZDR-compliant — uses
   the same `OPENAI_BASE_URL` lever as the rest of the pipeline, HR3)
3. Concurrency bounded by `asyncio.Semaphore(4)` — 4 pages in flight at a time
4. Retry pattern: one retry on `RateLimitError` / `APIConnectionError` with 2s backoff;
   other exceptions → empty string for that page (non-fatal per-page)
5. Assembles per-page markdown in page order, separated by `---` horizontal rules
6. Raises `RuntimeError` only if ALL pages return empty

Prompt instructs the VLM to: extract all visible text as markdown, preserve heading
hierarchy / tables / lists, ignore watermarks, preserve RTL script (Arabic), skip
image descriptions, return `<!-- blank page -->` for blank pages.

### D4 — Tree-path integration (`client.py:503-543`)

After the existing garble OCR escalation block and before the image-dominant retry,
a VLM fallback block fires when:

```python
not ok and reason == "garbling" and ext == ".pdf" and settings.vlm_fallback
```

The VLM markdown follows the same pattern as the OCR retry: write to temp file →
`_run_md_to_tree()` → `split_oversized_leaf_nodes()` → `validate_tree()`. On success
the tree is persisted normally. On garble-persist or error, falls through to the
existing terminal rejection.

Metric: `VLM_FALLBACK_TOTAL{result=recovered|still_garbled|error}`.

### D5 — Flat-path integration (`client.py:644-679`)

**Critical gap found during testing.** The Arabic PDF's watermark text layer produces a
very shallow tree → `validate_tree()` returns `(False, "node_count<3")`, not
`(False, "garbling")`. The D4 tree-path VLM block fires only on `reason=="garbling"`,
so it is skipped. The document enters the flat-doc routing path where
`_flat_text_is_garbled(flat_md)` catches it and overrides reason to `"garbling"` — but
at that point there was no VLM fallback.

**Fix:** Added a second VLM fallback block inside the flat-path garble gate. When
`_flat_text_is_garbled()` fires on a PDF with VLM enabled:

1. Calls `vlm_extract_markdown()` to extract clean text from page images
2. If VLM text passes `_flat_text_is_garbled()`: replaces `flat_md` with VLM output,
   resets `reason` back to `"node_count<3"` so the flat success path continues
3. If VLM text also fails garble check: `VLM_FALLBACK_TOTAL{result=still_garbled}`,
   falls through to terminal `LowQualityTreeError`
4. On exception: `VLM_FALLBACK_TOTAL{result=error}`, falls through

The `else:` branch after the garble gate was changed to `if reason != "garbling":` to
support the VLM recovery flow where reason is reset from `"garbling"` back to
`"node_count<3"`.

### D6 — Prometheus metric (`metrics.py:132-137`)

```python
VLM_FALLBACK_TOTAL = Counter(
    "pageindex_vlm_fallback_total",
    "VLM last-resort fallback attempts on garble-rejected PDFs whose OCR "
    "escalation also failed (RFC-004 Approach B).",
    ["result"],  # recovered | still_garbled | error
)
```

Shared across both the tree-path (D4) and flat-path (D5) integration points.

## Surfaces touched

| Module | Change |
|--------|--------|
| `config.py:84-85,151-153` | `vlm_fallback: bool` + `vlm_model: str` fields and env loading |
| `converters.py:1827-1937` | `rasterize_pdf_pages()` + `vlm_extract_markdown()` |
| `metrics.py:132-137` | `VLM_FALLBACK_TOTAL` counter |
| `client.py:503-543` | Tree-path VLM fallback block (after OCR escalation) |
| `client.py:644-679` | Flat-path VLM fallback block (inside `_flat_text_is_garbled` gate) |
| `.env` | `VLM_FALLBACK=true`, `VLM_MODEL=azure/gpt-4.1` |

## Verification

### Unit tests (`tests/test_vlm_fallback.py` — 7 contract tests, all pass)

| Test | Scenario | Assertion |
|------|----------|-----------|
| VLM-C1 | VLM recovers valid tree from garble-rejected PDF | Doc persisted as tree, metric `recovered` |
| VLM-C2 | VLM output also garbled | `LowQualityTreeError`, metric `still_garbled` |
| VLM-C3 | `vlm_extract_markdown` raises | Falls through to terminal rejection, metric `error` |
| VLM-C4 | `vlm_fallback=False` | VLM never called, garbling terminates as before |
| VLM-C5 | `reason="node_count<3"` (not garbling) | VLM tree-path skipped, routes to flat path |
| VLM-C6 | Flat-path garble gate + VLM recovers | Doc persisted as flat, metric `recovered` |
| VLM-C7 | Flat-path garble gate + VLM still garbled | `LowQualityTreeError`, metric `still_garbled` |

### End-to-end validation

The motivating Arabic PDF ("وارد رقم 597...") was processed through the converter CLI
with the fix applied:

```
Flat-path garble gate triggered → VLM fallback (azure/gpt-4.1) → recovered
doc_id=47e8f078-e237-4f3c-9e6a-e3f64eea7b5b
content_class=flat_mixed, 528 blocks
duration=142s (~3.4s/page for 42 pages), peak RSS ~6.3 GB
```

### Full suite

560 passed, 6 skipped, 0 failed (including the 7 VLM contract tests).

## Hard-Rule compliance

- **HR1** — No accuracy-superiority claim. The VLM fallback is positioned purely as a
  last-resort for garble-rejected docs, not a general quality improvement.
- **HR2** — No new derived stores beyond those already in the erasure cascade. VLM
  output enters the existing flat-doc or tree-doc persistence paths.
- **HR3** — VLM calls route through `get_openai_client()` which uses `OPENAI_BASE_URL`
  — the single ZDR lever. No separate `VLM_API_URL` / `VLM_API_KEY`.
- **HR4** — Rasterization uses pypdfium2 (BSD-3) exclusively. No fitz/PyMuPDF import
  in the VLM path. pypdfium2 is already a dependency (Docling transitive dep).
- **HR5** — VLM output re-enters the standard `validate_tree()` (tree path) or
  `_flat_text_is_garbled()` (flat path) gates. Never silently persisted.

## Operational notes

- **Cost:** ~42 VLM API calls per 42-page document at 200 DPI. At `gpt-4.1` pricing
  this is non-trivial — the feature is OFF by default and should be enabled only for
  corpora known to contain watermark-garbled scanned documents.
- **Latency:** ~142s for a 42-page document (bounded by Semaphore(4) concurrency).
  Well within `CHILD_TIMEOUT=870s`.
- **Memory:** Peak RSS ~6.3 GB during rasterization of a 42-page PDF. This is within
  the worker's capacity but notable for capacity planning.
- **Granite-258M:** NOT an option — permanently rejected (user-LOCKED 2026-06-12,
  RFC-004 Amendment 5). Any future on-prem VLM needs a different, GPU-class model.
