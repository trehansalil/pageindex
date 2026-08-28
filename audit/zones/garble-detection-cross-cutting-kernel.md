---
zone_name: Garble Detection Cross-Cutting Kernel
severity: critical
wave: 2
priority: 2
status: triaged
audit_date: 2026-08-28
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md
tags:
  - zone-spec
  - critical
  - wave-2
---
## Mechanism to Eliminate

Single shared detect_garble kernel consumed by 13 callers across 9+ subsystems with three compounding blind spots:

1. **Presentation-forms blind spot**: 3 call sites hardcode had_presentation_forms=False (verdict.py _try_image_enrichment, images.py _attempt_tesseract_raster_recovery, indexer.py pre-garble probe), bypassing NFKC presentation-forms compensation. Arabic PDFs with presentation-form codepoints normalized away go undetected.

2. **Table-block blind spot**: _garble_check_nodes per-node loop uses node.get("text") which returns empty for table nodes (content in headers/rows/row_records). Per-node garble detection blind to table content; whole-tree fallback only fires when zero per-node garbles found.

3. **Numeric-junk blind spot**: garble_prongs digit_ratio gated by garble_digit_floor=500 skips text shorter than 500 chars, letting genuinely garbled short numeric-junk OCR noise pass unchecked.

4. **Dead code**: garble_prongs:410 assigns _effective_script but never uses it, leaving gap for script-mismatch Latin mojibake detection (Chain 5).

## Strategy

Type-safe contract:
1. Replace all 3 remaining had_presentation_forms=False with _infer_presentation_forms(text) calls uniformly across callers
2. Fix _garble_check_nodes to use _node_text_parts(node) via deferred import (matches existing pattern) so per-node detection sees table content
3. Add secondary short-text numeric-junk check below garble_digit_floor (>0.90 digits AND >=50 chars) without false positives
4. Wire _effective_script into latin_gibberish prong so script-mismatch detection fires when expected_script=Arabic but text predominantly Latin (lowered nonsense threshold)

## Code Targets

| File | What | How | Constraint |
|---|---|---|---|
| `src/pageindex_mcp/helpers/__init__.py` lines 90–103 | Export _infer_presentation_forms | Add to import block from .garble (between _garble_config and _garble_ratio) | Must not break existing imports; already defined at garble.py:30-48 |
| `src/pageindex_mcp/helpers/verdict.py` lines 254–257 | Replace had_presentation_forms=False in _try_image_enrichment | Import _infer_presentation_forms; change line 256 to had_presentation_forms=_infer_presentation_forms(_promoted_text) | Must not change function signature; _promoted_text already computed |
| `src/pageindex_mcp/client/images.py` line 134 | Replace had_presentation_forms=False in _attempt_tesseract_raster_recovery | Import _infer_presentation_forms; change ScriptContext to use _infer_presentation_forms(ocr_text) | Must remain inside try/except block |
| `src/pageindex_mcp/client/indexer.py` lines 510–511 | Replace had_presentation_forms=False in pre-garble probe | Import _infer_presentation_forms; change line 511 ScriptContext to use _infer_presentation_forms(raw_text) | Must not change try/except semantics; raw_text available |
| `src/pageindex_mcp/helpers/garble.py` lines 674–698 | Fix _garble_check_nodes per-node text extraction | Add deferred import 'from .tree_validation import _node_text_parts'. Replace line 676 with node_parts = _node_text_parts(node); text = "\n".join(p for p in node_parts if p.strip()) | Use deferred import to avoid circular; _node_text_parts already includes title |
| `src/pageindex_mcp/helpers/garble.py` lines 397–401 | Add secondary short-text numeric-junk detection | After digit_ratio block: elif len(norm) >= 50: digits = sum(1 for c in norm if c.isdigit()); if (digits / len(norm)) > 0.90: prongs.add("numeric_junk_short") | Threshold >0.90 prevents false positives on dates/currency; >=50 char floor |
| `src/pageindex_mcp/helpers/garble.py` lines 410–418 | Wire _effective_script into latin_gibberish prong | After computing ratio, add script-mismatch branch: when _effective_script == 'Arab' and ratio > latin_ratio_threshold, use lowered nonsense_threshold=0.40 (vs 0.70) | Only applies when _effective_script is Arabic |

## Wiring Checks

| Symbol | Must Be Imported By | Check Type |
|---|---|---|
| _infer_presentation_forms | `src/pageindex_mcp/helpers/__init__.py`, `src/pageindex_mcp/helpers/verdict.py`, `src/pageindex_mcp/client/images.py`, `src/pageindex_mcp/client/indexer.py` | import |
| _node_text_parts | `src/pageindex_mcp/helpers/garble.py` | import |
| _infer_presentation_forms | `src/pageindex_mcp/helpers/verdict.py`, `src/pageindex_mcp/client/images.py`, `src/pageindex_mcp/client/indexer.py` | call |

## Test Requirements

| Test File | What to Test | Assertion Type |
|---|---|---|
| `tests/test_verdict.py` | _try_image_enrichment detects garbled Arabic with presentation-form codepoints via _infer_presentation_forms fallback (presentation_forms prong fires, blocks promotion) | regression |
| `tests/test_garble.py` | _garble_check_nodes detects garbled table-block content in headers/rows/row_records (per-node count >= 1) | exhaustiveness |
| `tests/test_garble.py` | Short numeric-junk (< 500 chars, >= 50 chars, > 90% digits) triggers numeric_junk_short prong; legitimate short numeric (dates, currency) does NOT | contract |
| `tests/test_garble.py` | Script-mismatch detection: expected_script='Arab' with predominantly Latin text fires latin_gibberish at lowered 0.40 threshold | contract |
| `tests/test_garble.py` | Regression: clean Arabic text (well-formed insurance T&C, no presentation forms, no garble) returns is_garbled=False | regression |

## Corpus Validation

- **Affected documents**: Arabic insurance T&Cs (presentation-forms), table-heavy German T&Cs (table garble), scanned PDFs with numeric OCR noise, Arabic PDFs OCR'd with Latin tessdata
- **Expected direction**: improve
- **Spot check count**: 5

## Dependencies

- Verdict-Gate Threshold / Promotion / Override Cascade (Wave 1)

## Complexity

Medium
