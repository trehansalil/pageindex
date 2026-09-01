---
zone_name: Garble Detection & NFKC Signal Destruction
severity: high
bug_count: 4
status: improved
audit_date: 2026-09-01
audit_run: POST-RFC041
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-01_POST-RFC041.md
key_files:
  - src/pageindex_mcp/helpers/garble.py
  - src/pageindex_mcp/helpers/tree_validation.py
  - src/pageindex_mcp/script.py
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/helpers/verdict.py
tags:
  - zone-spec
  - high
  - garble
  - nfkc
  - signal-destruction
scorecard_verdict: regressed
scorecard_date: 2026-09-01
scorecard_run: POST-RFC041
---
## Mechanism

The garble-detection subsystem has a structural vulnerability where NFKC Unicode normalization destroys Arabic presentation-form codepoints (U+FB50-FEFF) before garble checks run, creating a null-detector pattern where quality gates structurally cannot fire on their real failure mode.

1. **NFKC normalization signal destruction:** Normalization decomposes presentation-form codepoints into logical Arabic characters. `_infer_presentation_forms` called on post-NFKC text always returns False because the signal is already destroyed. The fix works at patched call sites, but `ScriptContext` permits construction with `had_presentation_forms=False` with no compile-time enforcement.

2. **Bidi coherence null-detector:** Its only failure signal (presentation-form morphology) cannot exist in canonical-reversed text because NFKC destroys it before detection, so 0 `bidi_coherence_violations` was read as proof of safety and used to justify defaulting `BIDI_COHERENCE_ENFORCE=true` (Chain 4). This pattern recurs wherever a zero-violation count is interpreted as correctness rather than detector blindness.

## History

- **Chain 3:** RFC-033 D2 heading guard never committed; Scaleway remote ran stale pre-guard image for weeks.
- **Chain 4:** RFC-033 D2 bidi coherence was null-detector fallacy — 0 violations because NFKC destroys signal before check.
- **Chain 13:** `detect_garble` declared sole entry point but `_garble_check_nodes` whole-tree fallback previously bypassed it (now fixed at garble.py:758).
- **Chain 21:** RFC-040 D6 fixed NFKC ordering in normalize.py but left 9 other call sites unpatched (many now fixed via extraction).

## Code Evidence

1. **_infer_presentation_forms** at garble.py:30-48 docstring confirms post-NFKC ratio is always 0.

2. **detect_garble** at garble.py:529-614 has internal PF recovery at lines 579-593.

3. **_garble_check_nodes** at garble.py:669-772 now calls `detect_garble` in whole-tree fallback (line 760-768 D1 comment).

4. **validate_tree** at tree_validation.py:407-419 now calls `_infer_presentation_forms(sig.flat_text)`.

5. **ScriptContext references:** Only 3 matches for `had_presentation_forms=False` remain in src/ — down from 10+ previously.

## Key Files

| File | Role |
|------|------|
| garble.py:30-48 | _infer_presentation_forms with null-detector documentation |
| garble.py:529-614 | detect_garble with internal PF recovery (579-593) |
| garble.py:669-772 | _garble_check_nodes delegating to detect_garble |
| tree_validation.py:407-419 | validate_tree calling _infer_presentation_forms |
| script.py | Script processing with presentation-form handling |
| indexer.py | Integration of garble detection in indexing |
| verdict.py | Verdict promotion with garble-aware gates |
