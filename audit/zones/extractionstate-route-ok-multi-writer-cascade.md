---
zone_name: ExtractionState route/ok multi-writer cascade
severity: critical
bug_count: 7
status: improved
audit_date: 2026-08-29
audit_run: POST-FIX-WAVE4
scorecard_verdict: regressed
scorecard_date: 2026-08-29
scorecard_run: POST-FIX-WAVE4
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE4.md
key_files:
  - src/pageindex_mcp/helpers/types.py
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/client/recovery.py
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/helpers/verdict.py
tags:
  - zone-spec
  - critical
  - verdict
  - extraction-state
  - multi-writer
---

## Mechanism

The ingestion pipeline threads a single mutable `ExtractionState` through a 10-gate evaluation, a GateSpec-driven recovery loop, two post-loop quality-check rerouters, and a match-based persistence dispatcher. `finalize_gate_and_route()` is documented as the "single writer" of `gate_result/ok/reason/first_defect/route`, but six other call sites write `state.route` and/or `state.ok` directly, leaving these five fields mutually inconsistent.

Every downstream consumer (reject reason, `all_defects` sidecar field, flat-vs-tree persistence, `LOW_QUALITY_TREES` metric label) reads a different subset of those fields, so any change to one stage silently reinterprets the others.

A tree that passed every gate reaches the dispatcher with `ok=False`, `route=FLAT`, `first_defect=TreeDefect.OK` (value `''`), and `gate_result.ok=True`.

The recovery loop re-enters `finalize_gate_and_route` from inside recovery methods, coupling gate ordering and recovery ordering: any reorder of `GATES` changes which recovery sees which tree.

## Code Evidence

### Single Writer Violation

**helpers/types.py:355-388** — `finalize_gate_and_route` docstring claims:
> "Single writer of gate_result/ok/reason/first_defect/route on *state*. ... Every call site that previously set a subset of these fields ... must call this instead."

**Contradicted by direct assignments:**
- **client/recovery.py:593** — `state.route = Route.FLAT`
- **client/recovery.py:649, 667, 685** — Multiple recovery methods assign `state.route` directly
- **client/recovery.py:728-729, 758-759** — Both `state.route` and `state.ok` assignments

### State Inconsistency Cascade

**client/indexer.py:1473-1475** — Comment documents the intentional bypass:
```python
# Quality checks (may override route intentionally - no re-derivation afterwards)
```

**client/indexer.py:1521** — Computes reject reason from stale state:
```python
_reject_reason = state.first_defect.value
```
Since `TreeDefect.OK = ''`, the reject reason is empty string on the flat-prefer/landscape-reroute path, corrupting the metric label.

**client/indexer.py:1279** — Writes sidecar from un-refreshed gate_result:
```python
meta['all_defects'] = state.gate_result.all_defects
```
Records clean tree for a rejected document.

### Recovery Loop Coupling

**client/indexer.py:1454-1470** — Recovery loop mutates state in place:
```python
for gate_result in state.gate_result.failed_gates:
    # Evaluate whether each gate's recovery method applies
    # Recovery methods call finalize_gate_and_route internally
```

This couples gate ordering and recovery ordering: changing `GATES` order changes which recovery paths are attempted.

## Impact

- Inconsistent verdict state across pipeline
- Silent metric corruption (empty reject reasons)
- Recovery logic coupled to gate order, making refactors risky
- No invariant checking on state.ok / state.gate_result alignment

## Files to Review

| File | Role |
|------|------|
| helpers/types.py | State definition and single-writer contract |
| client/indexer.py | Main dispatch site; quality-check rerouters |
| client/recovery.py | Six unauthorized writers |
| helpers/gates.py | Gate definition and verdict promotion |
| helpers/verdict.py | Verdict derivation logic |
