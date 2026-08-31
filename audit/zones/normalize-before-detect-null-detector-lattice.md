---
zone_name: Normalize-before-detect null-detector lattice (presentation forms / NFKC)
severity: critical
bug_count: 6
status: improved
audit_date: 2026-08-29
audit_run: POST-FIX-WAVE4
scorecard_verdict: regressed
scorecard_date: 2026-08-29
scorecard_run: POST-FIX-WAVE4
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE4.md
key_files:
  - src/pageindex_mcp/helpers/garble.py
  - src/pageindex_mcp/script.py
  - src/pageindex_mcp/helpers/tree_validation.py
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/client/recovery.py
  - src/pageindex_mcp/helpers/gates.py
tags:
  - zone-spec
  - critical
  - garble-detection
  - nfkc-normalization
  - null-detector
  - presentation-forms
---

## Mechanism

The Arabic presentation-forms signal (U+FB50-FDFF, U+FE70-FEFF) is destroyed by NFKC normalization early in the pipeline, but four independent detectors consume it downstream:

1. Gate 7 (`_gate_bidi_degraded`)
2. `detect_garble`'s PF recovery branch
3. `_garble_check_nodes`' per-node contexts
4. `_try_image_enrichment`'s garble re-check

Every fallback path that supplies the flag operates on post-NFKC text and therefore returns False structurally, not empirically. The detectors report zero violations, historically read as evidence of safety.

### Single True Producer

`had_presentation_forms` has exactly one true producer: `_renormalize_bidi_guarded` (indexer.py:180-183), which captures the flag pre-NFKC on the remote Docling path only. It returns `had_presentation_forms=False` when the bilingual Latin guard trips — exactly on mixed Arabic/English documents.

### Broken Fallbacks

Every other producer is a fallback that cannot see the signal:

- `_infer_presentation_forms` own docstring states: "Post-NFKC this ratio is always 0 (the codepoints decompose into logical Arabic), so callers on post-normalization text correctly get False" — yet it supplies the flag at 8 call sites.
- `decide_rtl` never assigns `had_presentation_forms`, leaving the RtlDecision dataclass default (False).
- Recovery actively destroys the one live carrier: `_execute_ocr_retry` sets `state.rtl_decision = None`, forcing recompute.

### Dead Code Recovery Heuristic

The NFKC-recovery heuristic inside `detect_garble` checks `_effective_script == 'Arabic'` (garble.py:583), but `_infer_script` returns only `'Arab'`, `'Latn'` or None. The literal `'Arabic'` appears exactly once in the codebase at that comparison — unreachable dead code.

## Code Evidence

### Pre-NFKC Capture (Docling Path Only)

**client/indexer.py:180-183** — Single true producer:
```python
if state.converter_source != 'docling':
    state.rtl_decision = None
else:
    state.rtl_decision = await _renormalize_bidi_guarded(...)
```

**client/indexer.py:155-161** — Bilingual guard kills the signal:
```python
if _is_bilingual_latin_dominant(flat_text):
    return RtlDecision(method='bilingual_guard_skip')
```
Returns with `had_presentation_forms=False` dataclass default.

### Fallback Producers at 8 Call Sites

**helpers/garble.py:30-45** — Docstring contradiction:
> "Post-NFKC this ratio is always 0... so callers on post-normalization text correctly get False"

Yet supplies flag at:
- **helpers/tree_validation.py:392**
- **helpers/verdict.py:257**
- **helpers/garble.py:855**
- **client/indexer.py:513**
- **client/indexer.py:998**
- **client/indexer.py:1024**
- **client/images.py:135**
- **converters/pictures.py:21**

### State Carrier Destruction

**script.py:694-708** — `RtlDecision.had_presentation_forms` defaults to False; `decide_rtl` never sets it.

**helpers/tree_validation.py:396-398** — Falls back to `decide_rtl(sig.flat_text)` when rtl_decision not threaded.

**client/recovery.py:332** — Destruction in recovery:
```python
state.rtl_decision = None  # Recompute with post-OCR text
```

**client/recovery.py:634-636** — Fallback clears it:
```python
if has_recovery_scars:
    state.rtl_decision = None  # So validate_tree recomputes
```

### Dead Code: String Literal Mismatch

**helpers/garble.py:583** — Dead code check:
```python
elif _arc > 0 and _pf == 0 and _effective_script == "Arabic":
```

**script.py:174** — Actual return values:
```python
if _is_arabic_script(...):
    return 'Arab'
elif _is_latin_script(...):
    return 'Latn'
else:
    return None
```

`grep` confirms literal `"Arabic"` appears exactly once in src/pageindex_mcp/.

## Impact

- Presentation-forms detector is structurally blind on ~99% of call paths
- Arabic bidi coherence checks fail silently
- Post-NFKC garble recovery is dead code
- Zero violations measurement used to justify stricter enforcement defaults
- RFC-040 D5 reordering pre-NFKC fixes one path but three others remain broken

## Files to Review

| File | Role |
|------|------|
| helpers/garble.py | Dead-code recovery heuristic; pre-NFKC signal lost |
| script.py | RtlDecision defaults and missing producer |
| helpers/tree_validation.py | Fallback to post-NFKC decide_rtl |
| client/indexer.py | Single true producer + bilingual guard kill |
| client/recovery.py | State carrier destruction; recompute blindness |
| helpers/gates.py | Gate 7 consumes zero-returning fallback |
