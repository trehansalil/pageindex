---
zone_name: OCR Pipeline Conflation
severity: high
bug_count: 3
status: improved
audit_date: 2026-08-29
audit_run: POST-FIX-WAVE3-VERIFY
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE3-VERIFY.md
key_files:
  - src/pageindex_mcp/converters/pictures.py
  - src/pageindex_mcp/client/images.py
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/config.py
tags:
  - zone-spec
  - high
  - ocr-pipeline
  - conflation
scorecard_verdict: regressed
scorecard_date: 2026-08-29
scorecard_run: POST-FIX-WAVE3-VERIFY
---

## Mechanism

The OCR subsystem has three independently-gated escalation triggers (garble, per-picture, low-content) plus a fourth structural-failure/image-dominant path, but these are **not fully independent** in practice.

### Core Problem: Single Pipeline Handles Two Strategies

**Per-picture OCR fires unconditionally** during PDF-to-markdown conversion, including during `force_full_page_ocr` escalation calls, so two competing Tesseract passes run on the same region.

Text recovered by per-picture OCR moves from prose blocks into image-block `ocr_text` fields, which:
- `content_class` computation is blind to
- `flat_char_count` metrics are blind to

### Content-Type Boundary Gaps

1. **Standalone image files:** Bypass the PDF picture-enrichment splice path entirely, losing chart/picture OCR content
2. **Page coverage filter:** P0b `_PICTURE_PAGE_COVERAGE_THRESHOLD` (default 0.6) only filters regions above 60%; charts at 15% page area still re-OCR'd even when Docling's text layer already extracted labels
3. **Storage duplication:** P0a fix (commit cad3f63) duplicated N PictureResults for standalone images, creating storage waste

### Zone-2 Re-entry Guard: Incomplete Fix

The `force_full_page_ocr_applied` parameter was added to short-circuit the second invocation, but the root structural issue remains — a single conversion function (`pdf_to_markdown_docling`) handles two conceptually different OCR strategies.

## Code Evidence

### _recover_picture_results (pictures.py:1036-1123)

Gated on:
```python
decide_ocr_strategy(
  ocr_escalation_enabled=pipeline_config.ocr_escalation_per_picture,
  has_image_markers=_IMAGE_MARKER in md,
  full_page_already_applied=force_full_page_ocr_applied
)
```

Fires as part of:
1. Standard conversion flow
2. `force_full_page_ocr` re-extraction (when re-entry guard is missing)

### Zone-2 Re-entry Guard

When `force_full_page_ocr_applied=True`:
"A full-page OCR retry has already re-extracted all page content including picture regions. Per-picture OCR would duplicate that work, so we short-circuit to []"

**Problem:** The guard only prevents duplication; it doesn't fix the underlying architectural issue of conflated strategies.

### Text-Layer Probe (pictures.py:240-275)

```python
_text_layer_has_content():
  calls detect_garble on extracted text layer content as OCR-skip probe
```

This adds another garble-evaluation call site with its own text derivation, creating inconsistency with the main detection pipeline.

### Three Independent OCR Triggers (config.py:382-384)

| Trigger | Purpose |
|---|---|
| ocr_escalation_garble | Garble-based escalation |
| ocr_escalation_per_picture | Per-picture region OCR |
| ocr_escalation_low_content | Content-volume escalation |

These are independently-gated but share the same pipeline function.

### Coverage Threshold (config.py)

`_PICTURE_PAGE_COVERAGE_THRESHOLD` (default 0.6):
- Only filters regions above 60% page coverage
- Sub-threshold charts at ~15% page area still get re-OCR'd
- Inefficient when Docling's text layer already extracted content cleanly

## Evidence History

| Artifact | Finding |
|---|---|
| Chains 4, 5, 9 | Theme recurrence: pipeline conflation |
| feat/image-block-picture-ocr | Per-picture OCR fires during force_full_page_ocr calls |
| P0b threshold | Only filters oversized regions; allows sub-threshold re-OCR |
| D1 text-layer probe | Fix implemented but left UNCOMMITTED |
| Standalone image f057fafe | Pie-chart jpg blocks show literal '<!-- image -->' with wedge/label text lost |
| P0a fix (cad3f63) | Duplicated N PictureResults for standalone images, creating storage waste |

## Architecture Defect Pattern

The root issue: **one function, two strategies**

### Current State
```
pdf_to_markdown_docling()
├─ Per-picture OCR (picture regions)
└─ Full-page OCR (entire page)
└─ Re-entry guard (Zone-2 patch)
```

Both strategies use the same conversion function, creating:
- Duplicated work on re-entry
- Blind spots in content metrics
- Coverage gaps for new content types

### Content-Type Coverage Gaps

| Content Type | Handling | Gap |
|---|---|---|
| PDF with pictures | splice_figure_markers → picture-enrichment | ✓ Works |
| Standalone image | image_to_markdown() directly | Bypasses splice |
| Mixed standalone + PDF | No unified path | Route-dependent behavior |

## Related Chains

- Chain 4: Initial conflation detection
- Chain 5: Re-entry duplication
- Chain 9: Coverage gaps analysis
