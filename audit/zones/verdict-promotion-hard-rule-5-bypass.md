---
zone_name: Verdict Promotion & Hard-Rule-5 Bypass Cascade
severity: high
bug_count: 7
status: improved
audit_date: 2026-09-02
audit_run: POST-RFC043
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-02_POST-RFC043.md
key_files:
  - src/pageindex_mcp/helpers/verdict.py
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/config.py
tags:
  - zone-spec
  - high
  - verdict
  - hard-rule-5
  - gate-bypass
scorecard_verdict: regressed
scorecard_date: 2026-09-02
scorecard_run: POST-RFC043
---
## Mechanism

The verdict classification pipeline (evaluate_gates → apply_promotions → classify_verdict) has multiple interacting bypass paths that override Hard-Rule-5 ('never silently persist a low-quality tree'). Four structural causes:

1. **First-match-wins if/elif chain**:
   - `apply_promotions` evaluates six _try_* helpers in source-code order
   - VG-6 fix now evaluates ALL paths for telemetry
   - Winner remains first match, making ordering load-bearing

2. **D1 structural hard-fail exception**:
   - `max_leaf_ratio > threshold` is unconditional hard-fail
   - Except when image enrichment exists: returns PASS via `_apply_clamp`
   - Bypasses what would be unconditional FAIL
   - VG-7 ensures `_ie` computed once and shared between D1 and D2

3. **Zero-content early-return bypass**:
   - `evaluate_gates` (lines 174-183) emits hard_fail before any gate evaluation
   - Bypasses recovery dispatch entirely
   - Zero-content documents classified FAIL without gate consultation

4. **Threshold widening without empirical anchoring**:
   - PASS_MAX_LEAF_RATIO widened three times (0.17→0.20→0.30)
   - Chased jitter on different documents
   - Masks extraction defects rather than fixing them
   - Violates Hard-Rule-5 by design

## Code Evidence

```python
# evaluate_gates (verdict.py:126-224, lines 174-183)
# Zero-content early-return BEFORE recovery dispatch
if sig.node_count == 0 or len(sig.flat_text.strip()) == 0:
    return GateOutcome(
        ...,
        hard_fail_verdict=VerdictResult("FAIL","zero_content",...)
    )
    # Exits before any HARD_FAIL_DEFECTS or recovery check

# apply_promotions (verdict.py:405-580)
# First-match-wins with D1 image-enrichment exception
if sig.max_leaf_ratio > th.hard_fail_max_leaf_ratio:
    if _ie is not None:
        return _apply_clamp(_ie, _is_image_enrichment=True)
    # Otherwise implicit FAIL from hard_fail_verdict

# D2 ordered pipeline (lines 541-576)
_matches = []  # Built in source-code order
for _try in [_try_image_enrichment, _try_structural_pass, ...]:
    if _try(...):
        _matches.append(...)
# Return _matches[0]  # First match wins

# HARD_FAIL_DEFECTS (gates.py:492)
frozenset(g.defect for g in GATES if g.hard_fail)
# GARBLING, REORDERED, EMPTY_NODE_CONTAMINATION, LOW_CONTENT_DENSITY, SUSPECT_DENSITY
```

## Key Files

| File | Role |
|---|---|
| src/pageindex_mcp/helpers/verdict.py | Verdict classification with bypass paths |
| src/pageindex_mcp/helpers/gates.py | Hard-fail defect declarations |
| src/pageindex_mcp/config.py | Threshold configuration |

## Evidence Chain

- **Chain 7** (RFC-023 D10→RFC-024 D0→RFC-025 D0): PASS_MAX_LEAF_RATIO widened; Haftpflicht with 81/132 garbled nodes passed; hysteresis defeated by reingestion wipe
- **Chain 8** (RFC-023→RFC-024→RFC-025): Image-enrichment bypass evolved from implicit drift to explicit priority=100 escape hatch
- **Chain 17** (RFC-022 B3): Table blocks carry no 'text' key; measurement artifact mistaken for regression
- **Chain 20** (RFC-041→RFC-042): Zero-content early-return bypass discovered as NEW gap
- **Chain 23** (RFC-025/026): Threshold changes downgrade verdicts without fixing extraction failures
