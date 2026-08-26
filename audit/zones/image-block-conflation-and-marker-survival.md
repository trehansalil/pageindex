---
zone_name: Image Block Conflation and Marker Survival
severity: medium
bug_count: 4
status: improved
audit_date: 2026-08-26
audit_run: POST-FIX-12
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-12.md
key_files:
  - src/pageindex_mcp/picture_plane.py
  - src/pageindex_mcp/converters/pictures.py
  - src/pageindex_mcp/client/indexer.py
tags:
  - zone-spec
  - medium
scorecard_verdict: regressed
scorecard_date: 2026-08-26
scorecard_run: POST-FIX-12
---
## Mechanism

The picture-splice pipeline has a structural conflation where per-picture OCR text is relocated from block['text'] (prose, visible to content_class) to block['ocr_text'] (image block, invisible to content_class), degrading retrieval granularity. The generative mechanism is an enrichment pipeline with incomplete state tracking.

The `_recover_picture_results` dense-fill fallback never distinguishes 'recovery attempted, found nothing' from 'never tried', so literal `<!-- image -->` markers survive verbatim into flat-doc output when neither ocr_text, desc, nor png_bytes exist. The `image_to_markdown()` path for standalone image files (.jpg/.png) never calls `_enrich_image_blocks` or `splice_figure_markers` at all, creating a complete bypass for an entire document type.

The D0 page-coverage >60% filter stops full-page scanned regions from being re-OCR'd but lets sub-60%-coverage charts bypass the gate, garbling small-font numerals.

## Evidence History

| RFC/Issue | Finding |
|---|---|
| RFC-017/018 D0 | N duplicate PictureResults to satisfy `splice_figure_markers` marker-count guard, relocating OCR text from prose to image blocks (6+ regressions Run 6) |
| D0/D1 filters | Unresolved `<!-- image -->` markers survive verbatim (GHV-TKV-Tarif: 3 of 4 markers survive as 42 chars noise) |
| Client.py C5 | `image_to_markdown()` path never calls `_enrich_image_blocks` or `splice_figure_markers` (pie-chart numeric labels completely lost) |
| D0 page-coverage | >60% coverage filter stops rescanning; sub-60%-coverage charts garble small-font numerals ('20l9 2O2O 202l' spliced over correct '2019 2020 2021') |

## Code Evidence

**decide_ocr_strategy** (picture_plane.py:357-423) — Per-picture OCR mode
```python
# Per-picture OCR returned when:
if ocr_escalation_enabled and has_image_markers:
    return OcrMode.PER_PICTURE  # Line ~416
```

**_text_layer_has_content** (pictures.py:267-299) — State tracking gap
```python
# Returns True (suppressing OCR) when text passes garble check
# No distinction between 'recovery attempted' and 'never tried'
return not detect_garble(text, script_context=_ctx, config=_garble_config)
```

**Dense-fill fallback**
```python
# _recover_picture_results has no 'skipped_reason' state for 'tried but found nothing'
# Falls back to literal marker text which is indistinguishable from 'never attempted recovery'
```

**Client image file bypass**
```python
# image_to_markdown() path for standalone image files (.jpg/.png)
# Never wired to the enrichment pipeline at all
# Confirmed by separate document_type='image' branch in decide_ocr_strategy
# that only fires when UNIFIED_OCR_PLAN_ENABLED
```

**Page-coverage filter interaction**
```python
# >60% coverage filter prevents rescanning full-page regions
# But sub-60%-coverage charts bypass the gate and get re-OCR'd at 300 DPI
# Garbling small-font numerals
```

## Key Files

- src/pageindex_mcp/picture_plane.py
- src/pageindex_mcp/converters/pictures.py
- src/pageindex_mcp/client/indexer.py
