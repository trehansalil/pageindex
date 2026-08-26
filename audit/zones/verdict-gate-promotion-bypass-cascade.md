---
zone_name: Verdict Gate Promotion Bypass Cascade
severity: critical
bug_count: 8
status: regressed
audit_date: 2026-08-26
audit_run: POST-FIX-12
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-12.md
key_files:
  - src/pageindex_mcp/helpers/verdict.py
  - src/pageindex_mcp/config.py
  - src/pageindex_mcp/storage/verdict.py
tags:
  - zone-spec
  - critical
  - hard-rule-5
scorecard_verdict: regressed
scorecard_date: 2026-08-26
scorecard_run: POST-FIX-12
---
## Mechanism

The verdict engine implements a two-stage cascade (evaluate_gates → apply_promotions) where multiple promotion branches bypass structural quality gates, violating Hard Rule 5 ('never silently persist a low-quality tree'). 

The generative mechanism is a priority-based candidate system where each RFC adds a new promotion path to rescue one category of false-positive FAILs, but each new path also opens a bypass for genuinely-bad documents. The `image_enrichment_promoted` candidate carries locked priority=100 that explicitly outranks the structural max_leaf_ratio hard-fail. Small_doc_promotion, flat_promotion, and content_class_promotion candidates independently bypass content-volume quality checks.

Threshold ratcheting (PASS_MAX_LEAF_RATIO widened 0.17→0.20→0.30) progressively weakened structural gates. RFC-025 hysteresis mechanism (prior-verdict anchoring) was defeated entirely by corpus reingestion wiping processed/*.meta.json sidecars, AND independently softened four zero-char Arabic docs from FAIL/ERROR to MARGINAL.

## Evidence History

| RFC/Run | Finding |
|---|---|
| RFC-023 D10 | Widened PASS_MAX_LEAF_RATIO 0.17→0.20; missed Reitlehrer at 0.2571 |
| RFC-024 D0 | Widened 0.20→0.30; own risk table predicted failure |
| RFC-025 D0 | Hysteresis defeated by corpus reingestion wiping meta.json sidecars; softened four zero-char Arabic docs FAIL/ERROR→MARGINAL |
| RFC-025 D1 | Page-level `_text_layer_has_content` from header/footer disabled picture OCR (503k→382 chars) |
| Run 9 audit | Flagged `image_enrichment_promoted` bypass — documents with only 38-123 chars received PASS verdicts (marsoom-13, al-qarar) |

## Code Evidence

**compute_verdict** (verdict.py:521-564) — Hard-fail short-circuit
```python
outcome = evaluate_gates(...)
if outcome.hard_fail_verdict is not None:
    return outcome.hard_fail_verdict
```

**apply_promotions** (verdict.py:407-518) — Image enrichment bypass
```python
_has_image_rescue = any(c.path_name == 'image_enrichment_promoted' for c in candidates)
if not _has_image_rescue and sig.max_leaf_ratio > th.hard_fail_max_leaf_ratio:
    return VerdictResult('FAIL', ...)
# image enrichment explicitly bypasses structural hard-fail
best = max(candidates, key=lambda c: c.priority)  # image_enrichment at priority=100
```

**evaluate_gates** (verdict.py:119-217) — Zero-content hard-fail
```python
if sig.node_count == 0 or len(sig.flat_text.strip()) == 0:
    return GateOutcome(...hard_fail_verdict=VerdictResult('FAIL', 'zero_content'...))
# But promotion candidates can fire on near-zero content
```

## Key Files

- src/pageindex_mcp/helpers/verdict.py
- src/pageindex_mcp/config.py
- src/pageindex_mcp/storage/verdict.py
