---
zone_name: OCR Pipeline Flag Conflation and Re-entry Hazards
severity: critical
bug_count: 7
status: regressed
audit_date: 2026-08-26
audit_run: POST-FIX-12
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-12.md
key_files:
  - src/pageindex_mcp/picture_plane.py
  - src/pageindex_mcp/client/recovery.py
  - src/pageindex_mcp/converters/pictures.py
  - src/pageindex_mcp/config.py
tags:
  - zone-spec
  - critical
scorecard_verdict: regressed
scorecard_date: 2026-08-26
scorecard_run: POST-FIX-12
---
## Mechanism

The OCR pipeline has three interacting structural defects: cross-module implicit state coupling without type-system enforcement, re-entry guards checked after branch conditions that short-circuit around them, and arithmetic that makes OCR retry impossible for the exact documents that need it most.

1. **Branch order defeats re-entry guard:** `decide_ocr_strategy` checks UNIFIED_OCR_PLAN_ENABLED + document_type='image' branch BEFORE full_page_already_applied re-entry guard, so for image documents the unified-plan branch always wins even after full-page OCR has already run — a duplicate-OCR hazard.

2. **Legacy interface with reduced parameters:** `decide_ocr_mode` still exists as thin wrapper with only 3 of 8 parameters, missing document_type/ocr_langs discrimination.

3. **Keep-best guardrail arithmetic makes retry impossible:** For no-text-layer PDFs, when pre-retry chars are zero, `post_retry_chars < pre_retry.total_chars` is always false (0 < 0). Falls through to garble comparison where empty text returns empty prongs, setting retry_wins=True. But interaction with density comparison at 0.80 threshold means a 69% content loss gets reverted every single retry attempt.

## Evidence History

| RFC/Issue | Finding |
|---|---|
| RFC-029 D4 | Keep-best guardrail makes OCR retry arithmetically impossible for no-text-layer PDFs (69% loss reverted every time) |
| OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION P1 | Single `_OCR_ESCALATION` kill-switch gates both page-level and per-picture mechanisms; proposed split never landed |
| Per-picture OCR | Fires unconditionally inside `pdf_to_markdown_docling`, runs SECOND time during page-level `force_full_page_ocr` escalation (competing OCR passes) |
| RFC-025 D1 | `_text_layer_has_content` from header/footer text disabled picture OCR (503k→382 chars) |
| D0 page-coverage | >60% filter stops rescanning full-page regions but lets sub-60%-coverage charts bypass gate, garbling small-font numerals |

## Code Evidence

**decide_ocr_strategy** (picture_plane.py:357-423) — Branch order
```python
# UNIFIED_OCR_PLAN_ENABLED check BEFORE re-entry guard
if UNIFIED_OCR_PLAN_ENABLED and document_type == 'image':
    return OcrMode.FULL_PAGE  # Line ~389, returns before guard
if full_page_already_applied:  # Line ~397, never reached for image docs
    return OcrMode.NONE
```

**decide_ocr_mode** (picture_plane.py:430-448) — Reduced interface
```python
# Only 3 parameters vs. 8 in decide_ocr_strategy
def decide_ocr_mode(ocr_escalation_enabled, has_image_markers, force_full_page):
    # Missing document_type and ocr_langs discrimination
```

**_execute_ocr_retry** (recovery.py:82-303) — Arithmetic deadlock
```python
# Keep-best comparison at ~line 228
if post_retry_chars < pre_retry.total_chars:
    retry_wins = False  # For equal chars (both zero), falls through
else:
    # Density comparison with 0.80 threshold
    _density_improved = _post_density < _pre_density * 0.80
    retry_wins = _density_improved
```

**_text_layer_has_content** (pictures.py:267-299)
```python
# Suppresses OCR regardless of header/footer-only status
return not detect_garble(text, script_context=_ctx, config=_garble_config)
```

## Key Files

- src/pageindex_mcp/picture_plane.py
- src/pageindex_mcp/client/recovery.py
- src/pageindex_mcp/converters/pictures.py
- src/pageindex_mcp/config.py
