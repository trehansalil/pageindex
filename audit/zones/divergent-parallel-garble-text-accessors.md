---
zone_name: Divergent parallel garble/text accessors
severity: high
bug_count: 4
status: regressed
audit_date: 2026-08-29
audit_run: POST-FIX-WAVE4
scorecard_verdict: regressed
scorecard_date: 2026-08-29
scorecard_run: POST-FIX-WAVE4
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE4.md
key_files:
  - src/pageindex_mcp/helpers/garble.py
  - src/pageindex_mcp/helpers/flat.py
  - src/pageindex_mcp/helpers/tree_validation.py
  - src/pageindex_mcp/helpers/gates.py
tags:
  - zone-spec
  - high
  - garble-detection
  - text-accessor
  - duplication
---

## Mechanism

Garble is decided by three procedures meant to be consolidated but were not:

1. `detect_garble` — declared "sole public entry point"
2. Raw `garble_prongs` call inside `_garble_check_nodes`' whole-tree fallback
3. `_garble_check_flat_blocks`

The same shape recurs in flat-block text extraction, where role-typed blocks omit the 'text' key and three separate accessors each re-implement the role dispatch.

### Garble Decision Paths

#### Path 1: detect_garble (Full Policy)

**helpers/garble.py:529-540**

```python
def detect_garble(text, script_context, ...) -> GarbleResult:
    """Unified garble evaluation entry point (Zone-3).
    Single-surface API: all garble heuristics are implemented here."""
    
    # 1. Re-infer dominant script when None
    script = script_context.script or infer_script(text)
    
    # 2. Apply RFC-025 D2 short-text rule
    if len(text.split()) < MIN_WORDS:
        return GarbleResult.NOT_GARBLED
    
    # 3. Select normalization kind
    norm_blob = _select_normalization_blob(script)
    
    # 4. Run presentation-forms RECOVERY (critical!)
    if not script_context.had_presentation_forms:
        _had_pf = _infer_presentation_forms(norm_blob)
        script_context = script_context.with_pf(_had_pf)  # Can flip False→True
    
    # 5. Call shared garble_prongs with enriched context
    return garble_prongs(norm_blob, script_context, ...)
```

#### Path 2: _garble_check_nodes Fallback (Minimal Policy)

**helpers/garble.py:747-757**

```python
def _garble_check_nodes(...):
    # ... tree traversal ...
    if not _is_garbled_per_node:
        # Whole-tree fallback: bypass detect_garble entirely
        text = normalize_for_garble(tree.text, tree.script)
        result = garble_prongs(text, script_context, ...)
        # ✓ Passes script_context verbatim (no PF recovery)
        # ✗ Skips short-text rule
        # ✗ No script re-inference
```

**Consequence:** The whole-tree fallback is strictly **less sensitive** to exactly the signal RFC-040 D5 was about (presentation-forms).

#### Path 3: _garble_check_flat_blocks (Duplicate)

**helpers/garble.py:766+**

```python
def _garble_check_flat_blocks(...):
    # Similar to path 2; bypasses detect_garble
    for block in flat_blocks:
        # Re-implements garble logic locally
        ...
```

#### Sync Problem

The two procedures **cannot be kept in sync by construction** — only by a reviewer noticing. Any fix to detect_garble must be manually propagated to the fallback paths.

### Text Accessor Duplication

Flat-block text extraction has the same problem. Blocks where `role=='table'` and `role=='image'` intentionally carry no 'text' key (RFC-022 B3).

Three separate accessors each re-implement role dispatch:

#### Accessor 1: _flat_block_primary_text

**helpers/flat.py:184-197**

```python
def _flat_block_primary_text(block) -> str:
    """Primary text extraction; carries Zone-9 header-only-table fix"""
    if block.get('text'):
        return block['text']
    
    if block['role'] == 'table':
        return _extract_table_headers(block)  # Zone-9 fix here
    elif block['role'] == 'image':
        return _extract_image_alt_text(block)
    
    return ''
```

#### Accessor 2: _flat_search_text

**helpers/flat.py:205-216**

```python
def _flat_search_text(block) -> str:
    """Search-optimized extraction"""
    if block.get('text'):
        return block['text']
    
    if block['role'] == 'table':
        # **Zone-9 fix NOT applied here**
        return block.get('table_text', '')
    elif block['role'] == 'image':
        return block.get('image_description', '')
    
    return ''
```

**Zone-9 header-only-table fix was applied only to accessor 1**, not here.

#### Accessor 3: Direct table-role dispatch

**helpers/flat.py:242**

```python
# Another site where role dispatch is reimplemented
if block['role'] == 'table':
    text = _extract_table_headers(block)
elif block['role'] == 'image':
    text = block.get('image_description', '')
```

### Measurement Impact

Any audit or measurement path that reads `block['text']` directly still registers zero table content — the accessors are invisible to generic dictionary iteration.

## Code Evidence

### detect_garble: Intended Single Entry Point

**helpers/garble.py:529-540** — Docstring:

```python
"""
Unified garble evaluation entry point (Zone-3).
Single-surface API: all garble heuristics - bidi drift, presentation-forms
recovery, short-text rule - are implemented here and must run inside
garble_prongs.
"""
```

### Path 2: Fallback Bypasses All Heuristics

**helpers/garble.py:745-757**

```python
if not _is_toplevel:
    # Fallback: recompute as generic tree
    text = normalize_for_garble(tree.text, tree.script)
    # Direct call; bypasses:
    # - script re-inference (line 541 in detect_garble)
    # - short-text rule (line 565 in detect_garble)
    # - PF recovery (line 577 in detect_garble)
    return garble_prongs(text, script_context, ...)
```

### Path 3: Another Fallback

**helpers/garble.py:766+** — `_garble_check_flat_blocks` re-implements the logic.

### Text Accessor 1 with Zone-9 Fix

**helpers/flat.py:184-197**

```python
def _flat_block_primary_text(block) -> str:
    if block.get('text'):
        return block['text']
    
    if block['role'] == 'table':
        # Zone-9: header-only table fix (commit 98b5038)
        return _extract_table_headers(block)
    elif block['role'] == 'image':
        return _extract_image_alt_text(block)
```

### Text Accessor 2 Missing Zone-9 Fix

**helpers/flat.py:205-216**

```python
def _flat_search_text(block) -> str:
    if block.get('text'):
        return block['text']
    
    if block['role'] == 'table':
        # ✗ Zone-9 fix NOT applied here
        return block.get('table_text', '')
```

### Block Construction Omits 'text' Key

**helpers/flat.py:87-95, :116-126**

```python
# Table blocks constructed without 'text' key
table_block = {
    'role': 'table',
    'table_structure': ...,
    'table_text': ...,
    # NO 'text' key
}

# Image blocks similarly
image_block = {
    'role': 'image',
    'image_description': ...,
    # NO 'text' key
}
```

## Sync Losses

1. **RFC-040 D3/D6 consolidation** — marked "partial" because fallback paths not consolidated
2. **Zone-9 header-only-table fix** — applied to 1 accessor only
3. **Any future garble heuristic** — cannot be kept in sync across three paths

## Generative Pattern: No Single Choke Point

The codebase has no single choke point for "text of a thing", only a family of near-identical accessors. This makes the classic "fix one instance, miss the other" defect structural.

## Impact

- Garble detection has three independent code paths with no sync guarantee
- Text extraction has three accessors re-implementing role dispatch
- Zone-9 fix applied to 1/3 text paths; other paths register zero table content
- Any future enhancement to detect_garble must be manually propagated

## Files to Review

| File | Role |
|------|------|
| helpers/garble.py | Three garble paths; fallback bypasses heuristics |
| helpers/flat.py | Three text accessors; role dispatch duplicated |
| helpers/tree_validation.py | Calls garble_prongs directly in some paths |
| helpers/gates.py | Consumes garble output from multiple paths |

## Remediation Ideas

1. **Consolidate garble paths** into single `detect_garble` call
2. **Create unified text accessor** with guaranteed role handling
3. **Test for sync** — verify all paths produce same garble result for same input
4. **Audit all measurement paths** that read `block['text']` directly
