---
zone_name: Gate-to-Recovery Dispatch Wiring Gap
severity: high
bug_count: 6
status: new
audit_date: 2026-09-02
audit_run: POST-RFC043
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-02_POST-RFC043.md
key_files:
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/client/recovery.py
  - src/pageindex_mcp/client/indexer.py
tags:
  - zone-spec
  - high
  - dead-code
  - dispatcher
  - fixed-but-unwired
scorecard_verdict: regressed
scorecard_date: 2026-09-02
scorecard_run: POST-RFC043
---
## Mechanism

The GATES table declares recovery functions and eligibility predicates, import-time assertions verify completeness, but the runtime dispatcher never calls them. Two structural causes, plus a recurring pattern:

1. **Declarative specification vs runtime execution disconnect**:
   - GATES list (gates.py:354-441) has import-time exhaustiveness assertions (lines 456-489)
   - Every gate with RETRY_OCR/RETRY_RTL policy must have recovery_fns and recovery_eligible
   - These assertions pass, creating false sense of completeness
   - `_convert_to_tree` (indexer.py:443-963) calls `finalize_gate_and_route`, evaluates gates
   - Never iterates GateSpec.recovery_fns to invoke the declared recovery methods

2. **'Fixed but never wired' pattern**:
   - Correct implementations exist in working tree but are inert in production
   - `chunked_docling_timeout_s` (RFC-027 task 4.2) marked complete, never wired to worker.py
   - `_check_bidi_coherence` improvements staged but inactive
   - RFC-030 D6 judge-calibration rules committed but never called
   - RFC-034 D19 enrichment-displacement guard staged but inactive

3. **New failure reasons never routed to recovery**:
   - RFC-029 added four new validate_tree failure reasons to GATE_TABLE
   - Never wired into client.py recovery routing
   - Caused 3 documents PASS→ERROR in Run 13 — "single highest-impact systemic bug of that run"
   - Also: validate_tree returns FIRST firing gate by severity; NODE_COUNT_LOW (severity=1) masks NODE_GARBLING (severity=3)

## Code Evidence

```python
# GATES table (gates.py:354-441)
# Declares recovery_fns but runtime never calls them
GateSpec(
    defect=Defect.GARBLING,
    recovery_fns=('_recover_garble_ocr','_recover_vlm_fallback'),
    recovery_eligible=_eligible_garble,
    ...
)
GateSpec(
    defect=Defect.NODE_COUNT_LOW,
    recovery_fns=('_recover_low_content_ocr','_recover_image_dominant_ocr'),
    ...
)
# Import-time assertions (lines 464-489) verify these are non-empty

# _recover_garble_ocr (recovery.py:400-432)
# Fully implemented, callers=0 per trace_path
def _recover_garble_ocr(...):
    ...
    _execute_ocr_retry(...)

# _convert_to_tree (indexer.py:443-963)
# Evaluates gates but never reads recovery_fns
def _convert_to_tree(...):
    gate_result = finalize_gate_and_route(...)
    # Never:
    # for fn_name in gate_result.recovery_fns:
    #     fn = getattr(recovery_module, fn_name)
    #     fn(...)
```

## Key Files

| File | Role |
|---|---|
| src/pageindex_mcp/helpers/gates.py | Gate declaration with recovery wiring (unused) |
| src/pageindex_mcp/client/recovery.py | Recovery implementations (never called) |
| src/pageindex_mcp/client/indexer.py | Dispatcher (never invokes recovery) |

## Evidence Chain

- **Chain 9** (RFC-029→Run 13): Four new validate_tree failure reasons added but never routed; caused 3 documents PASS→ERROR; also validate_tree returns first gate, masking high-severity with low
- **Chain 10** (RFC-027→RFC-030→RFC-034): Four instances of 'fixed but never wired' — chunked_docling_timeout_s, _check_bidi_coherence improvements, RFC-030 D6 judge-calibration, RFC-034 D19 enrichment-displacement guard
- **Chain 21** (RFC-041→RFC-043): Four recovery methods implemented but dispatcher never updated
- **Chain 27** (RFC-042 Zone 1): Gate coupling claims stale but zero-content early-return discovered as new gap
