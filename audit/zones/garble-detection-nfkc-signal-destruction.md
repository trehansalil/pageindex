---
zone_name: Garble Detection NFKC Signal Destruction
severity: critical
bug_count: 8
status: new
audit_date: 2026-09-02
audit_run: POST-RFC043
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-02_POST-RFC043.md
key_files:
  - src/pageindex_mcp/helpers/garble.py
  - src/pageindex_mcp/script.py
  - src/pageindex_mcp/converters/normalize.py
  - src/pageindex_mcp/helpers/gates.py
tags:
  - zone-spec
  - critical
  - arabic
  - unicode
  - signal-loss
scorecard_verdict: regressed
scorecard_date: 2026-09-02
scorecard_run: POST-RFC043
---
## Mechanism

NFKC Unicode normalization destroys the presentation-form signal that downstream garble and bidi-coherence gates depend on. Four structural causes:

1. **Signal destruction via normalization**:
   - `_pre_inference_normalize` (normalize.py) captures `had_presentation_forms` BEFORE NFKC
   - Attaches flag to RtlDecision
   - Only reaches gates that consume RtlDecision
   - Multiple independent ScriptContext construction sites (4+) must independently replicate the signal inference
   - Each new site is a potential signal-loss point

2. **Compensating mechanism proliferation**:
   - `detect_garble` has fallback (lines 584-592) that infers `had_presentation_forms=True` when dominant_script is Arabic but zero forms survive
   - Fallback only covers callers that pass script_context
   - Multiple independent call sites construct ScriptContext with `had_presentation_forms=False`, bypassing the safety net

3. **Digit-ratio blind spot for short garbled text**:
   - `digit_ratio` prong only fires above `garble_digit_floor` (default 500 chars)
   - Secondary `numeric_junk_short` prong (>= 50 chars, > 90% digits) partially closes gap
   - Was added reactively, not part of original design

4. **Latin-gibberish unreachable for project's validation vertical**:
   - Requires `expected_script` non-None
   - `_script_from_filename` returns None for German filenames
   - German T&Cs are primary validation vertical

## Code Evidence

```python
# _pre_inference_normalize (normalize.py:138-170)
# Captures signal BEFORE NFKC
had_pres_forms = any('fb50' <= ch <= 'fdff' or 'fe70' <= ch <= 'feff' 
                     for ch in text)
text = unicodedata.normalize('NFKC', text)
# Attaches to RtlDecision, only consumed by gates that use it

# detect_garble (garble.py:529-614, lines 584-592)
# Compensating fallback only when script_context passed
if script_context.had_presentation_forms:
    # Use it
else:
    # Compensating fallback: infer from script + content
    if dominant_script == 'Arab' and arc_count > 0 and pres_count == 0:
        had_presentation_forms = True

# _garble_prongs (garble.py:339-440)
# Digit-ratio blind spot for short text
if len(norm) > cfg.garble_digit_floor:  # default 500
    # digit_ratio prong fires
# numeric_junk_short (lines 401-408) partially closes for >= 50 chars at > 90%

# latin_gibberish (lines 418-434)
# Unreachable for German (expected_script is None)
if expected_script and ratio > threshold:
    # fires only for non-German
```

## Key Files

| File | Role |
|---|---|
| src/pageindex_mcp/helpers/garble.py | Garble detection with signal-loss fallback |
| src/pageindex_mcp/script.py | Script detection |
| src/pageindex_mcp/converters/normalize.py | NFKC normalization with signal capture |
| src/pageindex_mcp/helpers/gates.py | Gate declarations |

## Evidence Chain

- **Chain 4** (RFC-028 D2, RFC-033 D2, RFC-034 D7): NFKC destruction independently rediscovered three times; structurally present as of 2026-08-26
- **Chain 5** (RFC-024→025): _check_bidi_coherence has 0% true-positive rate; promoted to default-true on wrong reasoning
- **Chain 18** (RFC-018→019): Digit-ratio blind spot for short text (ISS-36); latin_gibberish unreachable for German
- **Chain 19** (RFC-020): short_text_prior_garble made detect_garble non-idempotent
- **Chain 28** (RFC-041/042/043): NFKC ownership unresolved across RFC cycle
