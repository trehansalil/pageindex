---
zone_name: Garble Detection Surface Fragmentation
severity: critical
bug_count: 12
status: audited
audit_date: 2026-08-26
audit_run: POST-FIX-13
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-13.md
key_files:
  - src/pageindex_mcp/helpers/garble.py
  - src/pageindex_mcp/script.py
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/helpers/tree_validation.py
tags:
  - zone-spec
  - critical
  - garble
  - arabic
  - unicode
---
## Mechanism

The generative mechanism is **HEURISTIC INTERACTION WITH NORMALIZATION DESTRUCTION**. `detect_garble` (garble.py:494-564) normalizes text via `normalize_for_garble` before passing to `garble_prongs`, but NFKC normalization decomposes Arabic Presentation-Form codepoints (U+FB50-FEFF) into logical Arabic, destroying the very signal the presentation_forms prong keys on.

`ScriptContext.from_document` (script.py:896-968) computes `had_presentation_forms` from raw text pre-NFKC, and `detect_garble` at line 540 reads `script_context.had_presentation_forms`, with a fallback computation at lines 541-543 scanning the blob directly — but if the blob has already been NFKC-normalized before reaching `detect_garble`, the fallback always returns False.

`garble_prongs` (garble.py:318-405) has a second structural problem: multiple prongs have independent blind spots that interact. The digit_ratio prong (line 383) only fires when len(norm) > cfg.garble_digit_floor (default 500), so short garbled text passes uninspected. The latin_gibberish prong (line 392) requires garble_latin_gibberish_enabled AND expected_script must be available — but _script_from_filename returns None for German filenames, making the prong permanently unfireable for German docs.

The short_text_prior_garble short-circuit (lines 524-534) makes detect_garble non-idempotent: when blob_kind==RAW_MARKDOWN, original_defect was GARBLING/NODE_GARBLING, and text<200 chars, it forces is_garbled=True without running any heuristic, so a prior garbling verdict permanently poisons short post-retry text.

## Code Evidence

- `detect_garble` (garble.py:494-564): short-circuit at lines 524-534 checks `blob_kind==RAW_MARKDOWN`, `config.garble_short_text_default`, `len(blob)<200`, `original_defect in (GARBLING, NODE_GARBLING)` and returns `GarbleReport(is_garbled=True, fired_prongs={'short_text_prior_garble'})` without calling `garble_prongs`.

- `ScriptContext` (script.py:869-968): docstring at line 881 states 'Post-NFKC the ratio is always 0 because presentation-form codepoints are decomposed into logical Arabic.' from_document at line 907 computes had_pf by scanning raw_text for PRESENTATION_RANGES codepoints, confirming pre-NFKC capture is intentional.

- `garble_prongs` (garble.py:318-405): digit_ratio at line 383 gated behind 'len(norm) > cfg.garble_digit_floor'; latin_gibberish at line 392 gated behind 'cfg.garble_latin_gibberish_enabled'; had_presentation_forms at line 368 simply adds the prong if True.

- `normalize_for_garble` (script.py:677-690): RAW_MARKDOWN path strips heading markers and table pipes but does NOT strip NFKC-decomposed presentation forms.

## Related RFCs

NFKC destruction independently rediscovered in RFC-028 D2, RFC-033 D2, RFC-034 D7.

expected_script never threaded to garble callers (RFC-019 D2).

_check_bidi_coherence had 0% TPR due to range exclusion (RFC-033 D2, BIDI_ROOT_CAUSE_RFC033).

D8 mixed-script regex included space in character class (Chain 21).

Markdown formatting dilutes digit-ratio below threshold (Chain 22).
