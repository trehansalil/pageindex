---
zone_name: Garble Detection Cross-Cutting Kernel
severity: critical
bug_count: 7
status: regressed
wave: 1
audit_date: 2026-08-28
audit_run: POST-FIX-WAVE3
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md
key_files:
  - src/pageindex_mcp/helpers/garble.py
  - src/pageindex_mcp/converters/pictures.py
  - src/pageindex_mcp/helpers/tree_validation.py
  - src/pageindex_mcp/client/recovery.py
tags:
  - zone-spec
  - critical
  - garble
  - cross-cutting
  - kernel
scorecard_verdict: regressed
scorecard_date: 2026-08-28
scorecard_run: POST-FIX-WAVE3
---
## Mechanism

`detect_garble` is a single shared kernel with **13 callers across 9+ distinct subsystems** (text-layer probing, tree validation, flat-block checks, Tesseract recovery, image-enrichment verdict promotion, keep-best OCR comparison, converter pre-garble probe, tree conversion). A narrow fix to one prong silently changes behavior for all other callers. Blind spots (numeric-junk, Latin mojibake from Arabic OCR) propagate simultaneously to every downstream gate:

- **Config flag override:** `config.garble_short_text_default` forces `is_garbled=True` for text<200 chars unconditionally (RFC-025 D2), creating a hidden mode switch
- **NFKC normalization destroys signal:** Arabic presentation-form codepoints (U+FB50-FEFF) are normalized away BEFORE text reaches detect_garble
- **ScriptContext threading gap:** Not all callers supply ScriptContext correctly — `_text_layer_has_content` constructs ScriptContext with `had_presentation_forms=False` when `script_context is None`

## Code Evidence

**garble.py:494–572** — `detect_garble`:
- Lines 525–530: short_text_prior_garble override forces is_garbled=True for short text
- Lines 543–554: presentation-forms fallback compensating for NFKC destruction

**garble.py:318–405** — `garble_prongs`:
- 11 independent prongs (digit_ratio, token_repetition, latin_gibberish, etc.)
- Each prong can independently fire; ordering determines priority

**pictures.py:240–272** — `_text_layer_has_content`:
- Constructs ScriptContext with `had_presentation_forms=False` when `script_context is None`
- Breaks the garble-detection contract

## Key Files

| File | Role |
|------|------|
| src/pageindex_mcp/helpers/garble.py | detect_garble kernel, prongs, config overrides |
| src/pageindex_mcp/converters/pictures.py | Image layer detection, ScriptContext construction |
| src/pageindex_mcp/helpers/tree_validation.py | Tree-level garble checks |
| src/pageindex_mcp/client/recovery.py | OCR recovery decision logic |

## Related Issues

- Chain 2: NFKC destruction of bidi signal
- Chain 5: Latin tessdata mojibake passes all prongs
- Chain 6: token_repetition fix left wider blast radius
- Chain 7: GATE_TABLE ordering masks garbling reason
- Chain 29: Zone 4 ScriptContext bug

