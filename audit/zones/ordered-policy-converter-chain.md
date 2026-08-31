---
zone_name: Ordered-policy converter chain with load-bearing branch order
severity: high
bug_count: 4
status: stalled
audit_date: 2026-08-29
audit_run: POST-FIX-WAVE4
scorecard_verdict: regressed
scorecard_date: 2026-08-29
scorecard_run: POST-FIX-WAVE4
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE4.md
key_files:
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/config.py
  - src/pageindex_mcp/converters/pipeline.py
  - src/pageindex_mcp/helpers/gates.py
tags:
  - zone-spec
  - high
  - converter
  - fallback-chain
  - licensing
  - policy
---

## Mechanism

The PDF converter fallback chain resolves each failure through a five-way if/elif ladder producing a `ConverterFailurePolicy`:

- RETRY
- BLOCK_AGPL
- GATE_AGPL_STRUCTURAL
- REJECT
- WALK

The classification order is explicitly documented as load-bearing, and the licensing guarantee (HR4: never walk into an AGPL converter on transient outage) depends entirely on that order — which the RETRY branch defeats.

### The Bug

The ladder at **indexer.py:670-677** tests RETRY first:
```python
if _is_transient and _transient_attempts < CONVERTER_TRANSIENT_RETRY_COUNT:
    # RETRY handler's comment: "rewind idx so the for-loop re-enters this entry"
    continue  # <-- WRONG: advances to NEXT entry, not same one
```

The RETRY handler's comment claims it will "rewind idx so the for-loop re-enters this entry", but the implementation is a bare `continue` inside `for idx, entry in enumerate(chain)` — which advances to the NEXT chain entry.

With shipped default `CONVERTER_TRANSIENT_RETRY_COUNT=1` (config.py:522), the first transient failure of the primary converter **always walks one step down the chain**. If the next entry is the AGPL converter it executes it — **bypassing the BLOCK_AGPL branch that exists precisely to prevent that**.

### Licensing Guarantee Broken

BLOCK_AGPL is only reachable once `_transient_attempts` has been exhausted. A single transient error walks around it:

1. Primary converter fails transiently (e.g., timeout)
2. `_transient_attempts < 1` is true
3. RETRY branch executes `continue` → advances to next entry
4. Next entry is AGPL converter
5. HR4 guarantee (never walk into AGPL on transient) is violated

The "no retry actually happened" bug and the "HR4 licensing guarantee is unenforced" bug are the same line.

### Generative Mechanism: Ordered Predicate Ladder

Policy is derived by falling through an ordered predicate ladder over four independent inputs:
- `_is_transient`
- `_transient_attempts`
- `next_is_agpl`
- `next_idx >= len(chain)`

With two orthogonal env toggles:
- `ALLOW_AGPL_FALLBACK` — removes AGPL entries from chain AND disables fitz pre-garble probe (see Zone 4)
- `AGPL_STRUCTURAL_FALLBACK_ENABLED` — gates only the structural walk

**Result:** Inserting or reordering any branch silently redefines all others.

## Code Evidence

### Load-Bearing Order Comment

**client/indexer.py:657-661**

```python
# Ordering is load-bearing: the AGPL branches must be classified 
# BEFORE the generic end-of-chain/WALK branches, or a structural 
# failure into an AGPL converter would fall through to the bare else 
# (its former, ungated behavior).
```

### The Ladder

**client/indexer.py:670-677**

```python
for idx, entry in enumerate(chain):
    # ... attempt conversion ...
    if exc is not None:
        _is_transient = isinstance(exc, TransientError)
        _transient_attempts = state.conversion_attempts_by_entry.get(idx, 0)
        
        if _is_transient and _transient_attempts < CONVERTER_TRANSIENT_RETRY_COUNT:
            continue  # <-- BUG: idx not rewound; advances to next entry
        elif next_is_agpl and not ALLOW_AGPL_FALLBACK:
            policy = ConverterFailurePolicy.BLOCK_AGPL
        # ... more branches ...
```

### RETRY Handler Comment vs Implementation

**client/indexer.py:692-702**

```python
def _handle_retry_policy(chain, idx):
    """Rewind idx so the for-loop re-enters this entry"""
    # No loop variable mutation possible; continue only advances
    continue
```

The loop variable `idx` is bound by `enumerate()` and cannot be rewound by a bare `continue` statement.

### Default Transient Retry Count

**config.py:521-522**

```python
CONVERTER_TRANSIENT_RETRY_COUNT: int = field(default=1)
```

With default 1, first transient failure walks the chain.

### Coupled Toggles: ALLOW_AGPL_FALLBACK Effects

**client/indexer.py:479-484**

```python
# D3a pre-conversion probe skipped when ALLOW_AGPL_FALLBACK=false
if not ALLOW_AGPL_FALLBACK:
    state.fitz_probe_result = None
    state.pdf_page_count = None  # Gate 10 requires this
```

The same branch that disables AGPL fallback also disables:
- D3a pre-garble probe (fitz)
- Page count capture (required for Gate 10: SUSPECT_DENSITY)

## Impact

- First transient converter failure walks to next converter in chain
- AGPL converters can be entered even with ALLOW_AGPL_FALLBACK=false
- HR4 licensing guarantee is unenforced
- Retry count config is ineffective (always retries by walking the chain)
- Coupled toggles make it unsafe to change ALLOW_AGPL_FALLBACK

## Files to Review

| File | Role |
|------|------|
| client/indexer.py | Converter chain; policy ladder; RETRY bug |
| config.py | CONVERTER_TRANSIENT_RETRY_COUNT default |
| converters/pipeline.py | Converter definitions and ordering |
| helpers/gates.py | Gate 10 (SUSPECT_DENSITY) dependency on pdf_page_count |

## Related Issues

- ISS-35 (2026-07-15) — Partial fix regressed at POST-FIX-WAVE3
- RFC-018 P2 — AGPL converter structural failures had no gating
- RFC-028 — JOB_TIMEOUT calibration (consequence of runaway OCR)
- RFC-031/032 — PDF_INSPECTOR_PRECLASSIFY confidence gate (workaround)
