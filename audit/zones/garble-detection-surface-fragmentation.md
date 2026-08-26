---
zone_name: Garble Detection Surface Fragmentation
severity: critical
bug_count: 10
status: improved
audit_date: 2026-08-26
audit_run: POST-FIX-12
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-12.md
key_files:
  - src/pageindex_mcp/helpers/garble.py
  - src/pageindex_mcp/converters/normalize.py
  - src/pageindex_mcp/converters/pictures.py
  - src/pageindex_mcp/converters/ocr_langs.py
  - src/pageindex_mcp/script.py
tags:
  - zone-spec
  - critical
scorecard_verdict: regressed
scorecard_date: 2026-08-26
scorecard_run: POST-FIX-12
---
## Mechanism

The garble detection pipeline is structurally fragmented across multiple dimensions where signal destruction occurs before detection instruments can inspect the signals. The ordering-dependent signal destruction combined with self-referential inference creates five independently-sufficient blind spots:

1. **NFKC normalization destroys Presentation Forms:** `_pre_inference_normalize` (normalize.py:157, pictures.py:167) runs `unicodedata.normalize('NFKC', text)` BEFORE garble_prongs or bidi detectors can inspect raw codepoints, destroying Arabic Presentation Forms (U+FB50-FEFF).

2. **Self-referential script detection:** `expected_script` is derived from `_infer_script(blob)` on potentially-corrupted text, so garbled text cannot trigger the garble gate because the gate's language-detection input is itself derived from the garbled text.

3. **Latin-gibberish scope narrowing:** The Latin-gibberish check in garble_prongs only fires when `expected_script` is non-Latin, so CMap-corrupted German documents (expected_script='Latn') bypass all Latin-script garble heuristics.

4. **OCR language fallback blindness:** `ensure_tessdata` silently falls back to `['deu','eng']` when no requested languages are available, so an Arabic OCR-escalation request runs Latin-only OCR producing garbled Latin mojibake that still passes the garble gate.

5. **Digit-ratio floor gating:** The garble_prongs digit_ratio check is gated behind `if len(norm) > cfg.garble_digit_floor`, letting short numeric-junk blobs pass uninspected.

Each RFC fix targeting one prong is defeated by the NFKC ordering destroying the codepoints before the new prong runs. Fixes that target inference (RFC-033 D2's `_check_bidi_coherence`) measured 0% true-positive rate because of the same ordering problem.

## Evidence History

| RFC/Issue | Finding |
|---|---|
| RFC-010 D3B | Added `_flat_text_is_garbled` duplicating `_tree_is_garbled` (fix-one-miss-the-other drift); confirmed root cause of marsoom-13 Latin-mojibake |
| RFC-013 D7 (ISS-36) | Diagnosed duplication, unresolved through FIX-11 |
| RFC-015/018/026/027 | `expected_script` gap flip-flopped across 6+ runs and 5+ RFCs without closing |
| RFC-028 D2 | Arabic presentation-forms prong caused Human Rights PDF FAIL→ERROR regression |
| RFC-033 D2 | `_check_bidi_coherence` measured 0% TPR (two independent causes: `_reversed_morphology` fires only on U+FB50-FEFF but `get_display()`-reversed text uses canonical U+06xx; line-selector excludes presentation-form lines) |
| ISS-34/marsoom-13 | `ensure_tessdata` silent deu/eng fallback produced exact failure mode |

## Code Evidence

**garble_prongs** (garble.py:318-405)
```
digit_ratio check gated behind: if len(norm) > cfg.garble_digit_floor
latin_gibberish check fires only when: garble_latin_gibberish_enabled and ratio > threshold
No coverage for expected_script='Latn'
```

**_pre_inference_normalize** (converters/normalize.py:157, converters/pictures.py:167)
```
had_pres_forms captured BEFORE NFKC
text = unicodedata.normalize('NFKC', text)  # destroys U+FB50-FEFF codepoints
```

**detect_garble** (garble.py:494-564)
```
_effective_script = script_context.dominant_script
if _effective_script is None:
    _effective_script = _infer_script(blob)  # self-inferred from potentially-corrupted text
```

**ensure_tessdata** (converters/ocr_langs.py:91-188)
```
Final fallback: return ['deu', 'eng']  # regardless of requested script
```

## Key Files

- src/pageindex_mcp/helpers/garble.py
- src/pageindex_mcp/converters/normalize.py
- src/pageindex_mcp/converters/pictures.py
- src/pageindex_mcp/converters/ocr_langs.py
- src/pageindex_mcp/script.py
