---
zone_name: Recovery dispatch: tuple-keyed dedup and unguarded raising normalizers
severity: high
bug_count: 4
status: improved
audit_date: 2026-08-29
audit_run: POST-FIX-WAVE4
scorecard_verdict: regressed
scorecard_date: 2026-08-29
scorecard_run: POST-FIX-WAVE4
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE4.md
key_files:
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/client/recovery.py
  - src/pageindex_mcp/converters/ocr_langs.py
  - src/pageindex_mcp/worker/errors.py
tags:
  - zone-spec
  - high
  - recovery
  - dedup
  - ocr
  - error-handling
---

## Mechanism

The GateSpec recovery loop deduplicates by the `recovery_fns` tuple rather than by individual method name, so a method listed in two different tuples runs twice. None of the OCR recovery methods check whether full-page OCR has already been applied.

Separately, `ensure_tessdata` was converted from a silent-fallback helper into a raising one, but two of its four call sites sit outside any try block, converting a language-availability problem into a terminal job error with no persisted artifact.

## Part A: Tuple-Keyed Dedup Bug

### Gate Definitions

**helpers/gates.py:368-386**

```python
GateSpec(
    'NODE_COUNT_LOW',
    recovery_fns=('_recover_low_content_ocr', '_recover_image_dominant_ocr')
),
GateSpec(
    'DEPTH_LOW',
    recovery_fns=('_recover_image_dominant_ocr',)
),
```

### Dedup Implementation

**client/indexer.py:1454-1459**

```python
_fired_recovery: set[tuple[str, ...]] = set()

for _gate in state.gate_result.failed_gates:
    if not _gate.recovery_fns or _gate.recovery_fns in _fired_recovery:
        continue
    _fired_recovery.add(_gate.recovery_fns)
```

**Problem:** Dedup is on the tuple, not the individual method name.

### Consequence

When both NODE_COUNT_LOW and DEPTH_LOW fire (common case: node_count<3 almost always means depth<2):

1. NODE_COUNT_LOW processes: `('_recover_low_content_ocr', '_recover_image_dominant_ocr')`
   - Adds tuple to `_fired_recovery`
   - Both methods execute
2. DEPTH_LOW processes: `('_recover_image_dominant_ocr',)`
   - Tuple not in `_fired_recovery` (different tuple)
   - Adds new tuple to `_fired_recovery`
   - Method executes again

**`_recover_image_dominant_ocr` runs twice** for the same document, triggering up to two full-page OCR passes.

### Correctness: NODE_COUNT_LOW + DEPTH_LOW are Coupled

Documents with node_count < 3 almost always have depth < 2:
- Minimal tree structure
- Flat or single-level
- Both gates fire together in ~95% of cases

So the double-run is systematic, not edge-case.

## Part B: Unguarded Raising Normalizer

### History

`ensure_tessdata` was converted from silent-fallback to raising. Per **converters/ocr_langs.py:123,161,174,186**, it now raises `TessdataUnavailableError` for unavailable non-Latin scripts.

### Two Call Sites, Two Error Semantics

**Call Site 1: Inside try block (recovery.py:273)**

**client/recovery.py:273**

```python
try:
    # ... OCR setup ...
    img_langs = await asyncio.to_thread(ensure_tessdata, detect_ocr_langs(filename))
    # ...
except Exception:  # Catches TessdataUnavailableError
    # Degrades to metric; continues processing
```

**Outcome:** Language unavailable → Graceful degradation to MARGINAL

**Call Site 2: Bare, no try (indexer.py:885)**

**client/indexer.py:885**

```python
# Image extension branch; no enclosing try
img_langs = await asyncio.to_thread(
    ensure_tessdata, detect_ocr_langs(filename)
)
```

If `ensure_tessdata` raises, exception propagates to index(), classified by worker/errors.py:

**worker/errors.py:30**

```python
TessdataUnavailableError: ChildErrorClassification(
    'converter_env_missing', 
    terminal=True
)
```

**Outcome:** Language unavailable → Terminal ERROR, no MinIO artifact saved

### Behavioral Divergence

Same error, two opposite outcomes:
- **PDF path:** Graceful degradation (MARGINAL)
- **.jpg path:** Terminal failure (ERROR, no artifact)

A .jpg with unavailable script languages (e.g., Assamese, Bengali) that would have received MARGINAL now receives ERROR with zero persisted artifact.

## Code Evidence

### Dedup Tuple Logic

**helpers/gates.py:368-386** — Gate definitions:

```python
_GATES = [
    GateSpec(
        'NODE_COUNT_LOW',
        severity=TreeDefect.STRUCTURAL,
        recovery_fns=('_recover_low_content_ocr', '_recover_image_dominant_ocr'),
    ),
    GateSpec(
        'DEPTH_LOW',
        severity=TreeDefect.STRUCTURAL,
        recovery_fns=('_recover_image_dominant_ocr',),
    ),
    # ... GARBLING/NODE_GARBLING share tuple (correctly deduped) ...
]
```

**client/indexer.py:1454-1470** — Recovery loop:

```python
_fired_recovery: set[tuple[str, ...]] = set()

for _gate in state.gate_result.failed_gates:
    if not _gate.recovery_fns:
        continue
    if _gate.recovery_fns in _fired_recovery:
        continue
    
    _fired_recovery.add(_gate.recovery_fns)
    
    for method_name in _gate.recovery_fns:
        method = getattr(recovery, method_name)
        await method(...)
```

### OCR Recovery No State Tracking

**client/recovery.py:458-498** — `_recover_image_dominant_ocr`:

```python
async def _recover_image_dominant_ocr(...) -> ...:
    if state.ok or ext != '.pdf':
        return  # Gate only applies to PDFs
    
    # No check for state.full_page_already_applied
    # No check of state.conversion_by_recovery
    
    # Unconditionally performs full-page OCR
    ocr_result = await _execute_ocr_retry(state, ...)
```

**client/recovery.py:232-236** — `_execute_ocr_retry` docstring:

```python
"""
...
Note: Callers should set state.full_page_already_applied = True
if they've already run full-page OCR. This method does not check it.
"""
```

**Consequence:** Documentation burden on callers; no enforced guard.

### Bare Call to ensure_tessdata

**client/indexer.py:885**

```python
async def index_image(...):
    # ... no try enclosure ...
    img_langs = await asyncio.to_thread(
        ensure_tessdata, detect_ocr_langs(filename)
    )  # Raises TessdataUnavailableError if language unavailable
    # Exception propagates to index(), classified as terminal error
```

**Guarded Call Inside recovery.py**

**client/recovery.py:273**

```python
try:
    img_langs = await asyncio.to_thread(
        ensure_tessdata, detect_ocr_langs(filename)
    )
except Exception:
    # Swallowed; continues with empty ocr_langs or fallback
    _log_metric('ocr_lang_unavailable')
```

### Error Classification

**worker/errors.py:30**

```python
TessdataUnavailableError: ChildErrorClassification(
    category='converter_env_missing',
    terminal=True  # Stops job; no save_doc_meta call
)
```

## Impact

- Up to two full-page OCR passes per document (instead of one)
- Jobs timeout before `save_doc` and leave zero MinIO artifact
- Image files with unavailable script languages → ERROR (no artifact) instead of MARGINAL (artifact saved)
- Recovery path dedup is order-dependent; adding new gates or tuples silently changes behavior

## Files to Review

| File | Role |
|------|------|
| helpers/gates.py | Gate recovery_fns tuple definitions |
| client/indexer.py | Dedup logic; bare ensure_tessdata call |
| client/recovery.py | Recovery methods; state.full_page_already_applied ignored |
| converters/ocr_langs.py | ensure_tessdata raising behavior |
| worker/errors.py | TessdataUnavailableError → terminal classification |

## Remediation Ideas

1. **Dedup by method name** instead of tuple
2. **Enforce full_page_already_applied guard** at function entry
3. **Wrap all ensure_tessdata calls** in try-except for consistent error handling
4. **Add test for coupled gates** (NODE_COUNT_LOW + DEPTH_LOW) to verify no double-run
