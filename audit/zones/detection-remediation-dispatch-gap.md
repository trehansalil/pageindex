---
zone_name: Detection-Remediation Dispatch Gap
severity: critical
bug_count: 4
status: improved
audit_date: 2026-08-29
audit_run: POST-FIX-WAVE3-VERIFY
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE3-VERIFY.md
key_files:
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/helpers/tree_validation.py
  - src/pageindex_mcp/helpers/garble.py
  - src/pageindex_mcp/client/recovery.py
tags:
  - zone-spec
  - critical
  - detection-remediation
  - dispatch-gap
scorecard_verdict: regressed
scorecard_date: 2026-08-29
scorecard_run: POST-FIX-WAVE3-VERIFY
---

## Mechanism

Garble and structural defect detection fires correctly at the `validate_tree` gate stage, but the OCR/VLM recovery dispatch fails to connect to the right remediation because:

### Four Root Causes

**(a) Severity Ordering Hides Co-Firing Defects**
- The GATE_TABLE severity ordering lets non-garble defects suppress co-firing garble defects as the primary reason
- Recovery dispatch branches on the primary reason
- Example: NODE_COUNT_LOW (severity=1) suppresses NODE_GARBLING (severity=3)

**(b) Early-Exit Before Garble Checks Complete**
- `validate_tree`'s early-exit on `node_count<3` or `depth<2` runs before garble checks complete
- Numeric-junk PDFs receive `reason='node_count<3'` and never trigger OCR recovery
- Gate is dimensionally correct but terminally wrong

**(c) Recovery Dispatch Consumes Narrower Reason Set**
- Detection can emit many reason types
- Recovery only looks for a subset
- Newly-added defect types can shadow garble

**(d) Terminal-Raise Routing Pre-empts Remediation**
- Terminal-raise and flat-routing whitelist ordering can terminate documents before reaching gates that would detect and remediate their defect
- Example: Ward 597 with rtl_reversal routing

## Code Evidence

### GATE_TABLE Severity Ordering (gates.py:359-446)

| Gate | Severity |
|---|---|
| GARBLING | 0 |
| NODE_COUNT_LOW | 1 |
| DEPTH_LOW | 2 |
| NODE_GARBLING | 3 |

Problem: Lower severity values win as "primary_defect" in early ordering, so NODE_COUNT_LOW (1) pre-empts NODE_GARBLING (3).

### D4 Garble-Priority Override (tree_validation.py:~416)

```python
if primary_defect not in _garble_defects:
  for d, detail in fired:
    if d in _garble_defects:
      primary_defect = d
      break
```

**Limitation:** Only covers `GARBLING`/`NODE_GARBLING` pair. Any NEW defect type added to GATE_TABLE with lower severity could shadow garble before D4 fires.

### Zone-1 Fix: `_eligible_image_dominant` (gates.py:314-327)

Checks `all_defects` not just `first_defect`, so `DEPTH_LOW` as secondary still triggers recovery.

This pattern should be applied uniformly across all recovery gates.

### Node Garbling Detection (gates.py:72-96)

Fires when per-node garble ratio exceeds `garble_node_ratio_threshold`.

### `detect_garble` Trace Path

11 direct callers across:
- garble.py
- pictures.py
- tree_validation.py
- verdict.py
- recovery.py
- images.py
- indexer.py

Each passing different text derivation and ScriptContext — creates divergent detection behavior.

## Evidence History

| Artifact | Finding |
|---|---|
| Chains 3, 12, 18, 19 | Theme recurrence across runs |
| GATE_TABLE tiebreak | NODE_COUNT_LOW (severity=1) suppresses NODE_GARBLING (severity=3) |
| Ward 597 | Persisted with garbling(ratio=1.00) and 81 garbled nodes but PASS |
| Ward 597 prevention | Numeric-junk early-exit prevented garble detection and OCR escalation |
| Ward 597 recovery | Only reached MARGINAL by Run 16 despite VLM availability |
| RFC-036 D3 | rtl_reversal routing: document terminates in terminal-raise BEFORE reaching flat-path garble gate |

## Root Cause

The system has two phases:
1. **Detection:** Exhaustive GATE_TABLE evaluation in `validate_tree`
2. **Recovery:** `GateSpec.recovery_eligible + recovery_fns` dispatch

The gap arises because:
- D4 garble-priority override was added as a patch AFTER severity ordering was established
- Only covers GARBLING/NODE_GARBLING pair
- Recovery dispatch consumes narrower reason set than detection can emit
- Early-exit gates run before garble checks complete

Recovery dispatch on `_eligible_garble` / `_eligible_low_content` predicates checks `state.first_defect` or `state.ok`, which depend on the primary defect chosen — so a detection-correct but dispatch-wrong primary selection silently disables the correct recovery path.

## Related Chains

- Chain 3: Initial detection-dispatch mismatch
- Chain 12: Early-exit gate problem
- Chain 18: Recovery escalation failure
- Chain 19: Terminal routing interception
