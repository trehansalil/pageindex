---
zone_name: Config-layer bifurcation: frozen snapshot vs live os.environ
severity: high
bug_count: 4
status: new
audit_date: 2026-08-29
audit_run: POST-FIX-WAVE4
scorecard_verdict: regressed
scorecard_date: 2026-08-29
scorecard_run: POST-FIX-WAVE4
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE4.md
key_files:
  - src/pageindex_mcp/config.py
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/helpers/tree_split.py
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/client/recovery.py
tags:
  - zone-spec
  - high
  - config
  - environment
  - threshold
  - consistency
---

## Mechanism

`config.py` builds a frozen `PipelineConfig` snapshot from 88 env reads at import time, asserts cross-flag coupling invariants against it, and serializes a subset into every sidecar as the `effective_config` audit trail.

**But:** 55 further `os.environ`/`os.getenv` reads live in 24 other modules, several re-reading the same variables at call time with different parsing rules.

The recorded audit trail describes a configuration that did not govern the run.

## Three Verified Divergences

### 1. Bidi Coherence Enforce: Parsing Mismatch

**Definition:** `config.py:397, 506`
```python
bidi_coherence_enforce = _envbool('BIDI_COHERENCE_ENFORCE', default=False)
```

**Sidecar Inclusion:** `config.py:705` — listed in effective_config fields

**Consumer:** `helpers/gates.py:162`
```python
if os.environ.get("BIDI_COHERENCE_ENFORCE", "true").lower() != "true":
    return (False, "")  # Gate 7 disabled
```

**Parsing Mismatch:**
- `_envbool` (config.py:353) accepts `{'1','true','yes'}`
- gates.py requires exactly the string `'true'` (after lower())

**Consequence:** Setting `BIDI_COHERENCE_ENFORCE=1` records `enforce=True` in sidecar while disabling Gate 7 at runtime.

### 2. Leaf Split Ratio: Assertion vs Live Read

**Snapshotted:** `config.py:511`
```python
leaf_split_ratio = float(os.environ.get('LEAF_SPLIT_RATIO', '0.30'))
```

**Import-Time Assertion:** `config.py:597-600`
```python
assert PASS_MAX_LEAF_RATIO <= LEAF_SPLIT_RATIO <= HARD_FAIL_MAX_LEAF_RATIO
```

**Live Read at Call Time:** `helpers/tree_split.py:385`
```python
ratio = float(os.environ.get('LEAF_SPLIT_RATIO', '0.30'))
```

**Consequence:** The assertion guards a value the splitter does not use. The invariant chain `PASS_MAX_LEAF_RATIO <= LEAF_SPLIT_RATIO <= HARD_FAIL_MAX_LEAF_RATIO` is unenforceable in the code path that matters.

### 3. Pre-Garble Force OCR: Process Boundary Bifurcation

**Snapshot at Import:** `config.py:488`
```python
pre_garble_force_ocr_enabled = _envbool('PRE_GARBLE_FORCE_OCR_ENABLED', ...)
```

**Live Read at Call Time:** `client/indexer.py:530`
```python
if os.environ.get('PRE_GARBLE_FORCE_OCR_ENABLED', '').lower() in _TRUE_SET:
```

**Consequence:** A process that mutates `os.environ` (tests, converters_cli child inheriting modified environment) gets two different answers from the same variable.

## Scale of Bifurcation

- **88 env reads** in config.py (frozen at import)
- **55 env reads** across 24 other modules (live at call time)
- **Total:** 143 environment reads; 88 frozen, 55 live

## Code Evidence

### Bidi Coherence Gate

**config.py:353** — `_envbool`:
```python
def _envbool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, str(default)).lower()
    return val in ('1', 'true', 'yes')
```

**config.py:397** — Snapshot:
```python
bidi_coherence_enforce: bool = _envbool('BIDI_COHERENCE_ENFORCE', False)
```

**config.py:705** — Sidecar field:
```python
effective_config = {
    'bidi_coherence_enforce': pipeline_config.bidi_coherence_enforce,
    ...
}
```

**helpers/gates.py:162** — Consumer reads directly:
```python
if os.environ.get("BIDI_COHERENCE_ENFORCE", "true").lower() != "true":
    return (False, "")  # Gate 7 short-circuits
```

### Leaf Split Ratio

**config.py:511** — Snapshot:
```python
leaf_split_ratio: float = float(os.environ.get('LEAF_SPLIT_RATIO', '0.30'))
```

**config.py:597-600** — Assertion over frozen value:
```python
assert (config.PASS_MAX_LEAF_RATIO <= 
        config.LEAF_SPLIT_RATIO <= 
        config.HARD_FAIL_MAX_LEAF_RATIO)
```

**helpers/tree_split.py:385** — Live read at call time:
```python
split_config = {
    'leaf_split_ratio': float(os.environ.get('LEAF_SPLIT_RATIO', '0.30')),
    'leaf_concentration_paragraph_split_enabled': (
        os.environ.get('LEAF_CONCENTRATION_PARAGRAPH_SPLIT_ENABLED', 'false').lower() 
        in ('false', '0', 'no', 'off')
    ),
}
```

### Pre-Garble Force OCR

**config.py:488** — Frozen:
```python
pre_garble_force_ocr_enabled: bool = _envbool('PRE_GARBLE_FORCE_OCR_ENABLED', ...)
```

**client/indexer.py:530** — Live:
```python
if os.environ.get('PRE_GARBLE_FORCE_OCR_ENABLED', '').lower() in _TRUE_SET:
    state.force_ocr = True
```

## Historic Root Cause

**commit 610d078** (Zone 7 config-layering refactor):
> "Refactored frozen threshold constants from local scopes into unified _CONFIG scope, revealing that DEPTH_ADEQUACY_FLOOR and CHAR_FLOOR had drifted by 1-2 units across duplicated calls ... changed verdict outcomes for ~20 documents"

The refactor partially addressed this but coverage remains incomplete.

## Impact

- Audit trail (`effective_config`) misrepresents actual runtime configuration
- Threshold changes are masked as content regressions in corpus audits
- Process boundary mutations (tests, child processes) cause bifurcation
- Cross-flag coupling assertions are unenforceable in live paths

## Files to Review

| File | Role |
|------|------|
| config.py | Frozen snapshot layer; 88 reads |
| helpers/gates.py | Bidi coherence: direct os.environ read |
| helpers/tree_split.py | Leaf split: re-reads ratio + different parsing for CONCENTRATION_PARAGRAPH |
| client/indexer.py | Pre-garble force: live os.environ read |
| client/recovery.py | Likely further direct env reads |

## Remediation Pattern

Consolidate all env reads into config.py at import time; pass PipelineConfig explicitly to all consumers; assert invariants once at import; serialize config to sidecar as source of truth for audits.
