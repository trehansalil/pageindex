---
zone_name: Order-coupled erasure manifest with implicit inter-step data flow
severity: medium
bug_count: 2
status: stalled
audit_date: 2026-08-29
audit_run: POST-FIX-WAVE4
scorecard_verdict: regressed
scorecard_date: 2026-08-29
scorecard_run: POST-FIX-WAVE4
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE4.md
key_files:
  - src/pageindex_mcp/storage/documents.py
  - src/pageindex_mcp/storage/hash_cache.py
  - src/pageindex_mcp/storage/reconcile_etag.py
  - src/pageindex_mcp/registry/queries.py
tags:
  - zone-spec
  - medium
  - erasure
  - compliance
  - order-dependency
---

## Mechanism

The HR2 right-to-erasure cascade was refactored into a declarative `_ERASURE_MANIFEST` of eleven `ErasureStep` entries driven by a generic loop, which reads as order-independent. **It is not.**

Two steps discover data that later steps require, so reordering or removing an entry silently converts a purge into a no-op that reports success.

### The Two Hidden Dependencies

#### Dependency 1: ctx.doc_name Discovery in Step 1

**storage/documents.py:404-411** — `_erase_uploads` (step 1):

```python
async def _erase_uploads(ctx: ErasureContext, ...) -> bool:
    """Purge original uploads. Discovered here: ctx.doc_name"""
    
    uploads = list_minio_objects('uploads/')
    for obj in uploads:
        doc_name = obj.name.split('/')[-1]  # Recover from path
        if doc_name == ctx.doc_id:
            ctx.doc_name = doc_name  # Discovered & persisted
            await delete_minio_object(obj.name)
    
    return True
```

**Consequence:** Steps 5 and 7 are unreachable without `ctx.doc_name`:

**storage/documents.py:475-481** — `_erase_hash_cache`:

```python
async def _erase_hash_cache(ctx: ErasureContext, ...) -> bool:
    if not ctx.doc_name:  # Discovered in step 1
        _log('hash_cache purge skipped; doc_name unknown', level=DEBUG)
        return False
    
    hash_key = f'hash_cache/{ctx.doc_name}'
    await delete_redis_key(hash_key)
    return True
```

**storage/documents.py:532-537** — `_erase_preloaded`:

```python
async def _erase_preloaded(ctx: ErasureContext, ...) -> bool:
    if not ctx.doc_name:  # Discovered in step 1
        _log('preloaded purge skipped; doc_name unknown', level=DEBUG)
        return False
    
    # ...
```

#### Dependency 2: ctx.sha256 Discovery in Step 2d

**storage/documents.py:405-408** — `_erase_verdicts` (step 2d) docstring:

```python
"""Must run before the sidecar is deleted (step 3) because the 
sha256 that keys the ledger lives only in processed/<doc_id>.meta.json"""
```

**storage/documents.py:425-430**

```python
async def _erase_verdicts(ctx: ErasureContext, ...) -> bool:
    """Discover ctx.sha256 from sidecar before it's deleted in step 3"""
    
    meta = read_doc_meta(doc_id)  # Step 3 deletes this
    ctx.sha256 = meta.get('sha256')  # Discovered & persisted
    
    if not ctx.sha256:
        _log('verdict ledger key unavailable', level=DEBUG)
        return False
```

**Consequence:** If step 3 runs before step 2d, the ledger is unreachable:

**storage/documents.py:455+** — Step 3 `_erase_processed`:

```python
async def _erase_processed(ctx: ErasureContext, ...) -> bool:
    """Delete the processed/<doc_id>.meta.json sidecar"""
    
    await delete_minio_object(f'processed/{doc_id}.meta.json')
    # ctx.sha256 is now unrecoverable
    return True
```

### No Declared Dependency

Both dependencies are documented **only in prose**:
- Docstring comments in the step functions
- Manifest ordering assumption

**No assertion, no dependency declaration.**

### Manifest Design Invites Reordering

**storage/documents.py:546-549** — Driver comment:

```python
# Adding a derived store is a one-line entry here plus its _erase_* 
# coroutine; the driver in delete_doc needs no change.
#
# [This assumes new stores can be added without considering ordering.]
```

The comment actively invites one-line additions, implying order-independence that doesn't exist.

### Steps Marked required=False Hide Failures

**storage/documents.py:239-244** — Manifest processing:

```python
for step in _ERASURE_MANIFEST:
    try:
        success = await step.coroutine(ctx, ...)
        if success or not step.required:
            continue  # Silently ignore failure if not required
    except Exception as e:
        if step.required:
            raise
```

Steps marked `required=False` include verdicts, preloaded, figures, flat_json — exactly those most likely to become unreachable if ordering is violated.

**storage/documents.py:475-481** — Unreachable step returns False at DEBUG level:

```python
if not ctx.doc_name:
    _log('hash_cache purge skipped; doc_name unknown', level=DEBUG)
    return False  # required=False in manifest
```

**storage/documents.py:255-258** — Final return value:

```python
return {'errors': ctx.errors}  # Empty list; appears successful
```

An unreached ledger purge is logged at DEBUG as an "expected miss" and `delete_doc` returns `{'errors': []}` — an apparently clean HR2 cascade with **residual PII-derived artifact** (verdict ledger).

## Code Evidence

### Step 1: ctx.doc_name Recovery

**storage/documents.py:404-411**

```python
async def _erase_uploads(ctx: ErasureContext, ...) -> bool:
    """Purge uploads/"""
    uploads = list_minio_objects('uploads/')
    for obj in uploads:
        if doc_id_matches(obj, ctx.doc_id):
            ctx.doc_name = obj.name.split('/')[-1]
            await delete_minio_object(obj.name)
    return True
```

### Step 2d: ctx.sha256 Recovery (Before Sidecar Delete)

**storage/documents.py:425-430**

```python
async def _erase_verdicts(ctx: ErasureContext, ...) -> bool:
    """Must run before step 3 (_erase_processed)
    because sha256 lives only in processed/<doc_id>.meta.json"""
    
    meta = read_doc_meta(doc_id)  # Read before it's deleted
    ctx.sha256 = meta.get('sha256')
    
    if not ctx.sha256:
        _log('verdict ledger unreachable', level=DEBUG)
        return False
```

### Step 3: Sidecar Deletion (Irreversible)

**storage/documents.py:455+**

```python
async def _erase_processed(ctx: ErasureContext, ...) -> bool:
    """Delete processed/<doc_id>.meta.json"""
    await delete_minio_object(f'processed/{doc_id}.meta.json')
    return True
    # ctx.sha256 is now unrecoverable if not read in step 2d
```

### Step 5 & 7: Unreachable Without ctx.doc_name

**storage/documents.py:475-481**

```python
async def _erase_hash_cache(ctx: ErasureContext, ...) -> bool:
    if not ctx.doc_name:
        _log('hash_cache skip (doc_name unknown)', level=DEBUG)
        return False  # required=False in manifest
    # ...
```

### Manifest Definition

**storage/documents.py:546-605** (approx) — `_ERASURE_MANIFEST`:

```python
_ERASURE_MANIFEST = [
    ErasureStep('uploads', _erase_uploads, required=True),
    ErasureStep('verdicts', _erase_verdicts, required=False),  # Must run before step 3
    ErasureStep('processed', _erase_processed, required=True),  # Deletes the key source
    # ...
    ErasureStep('hash_cache', _erase_hash_cache, required=False),  # Needs doc_name from step 1
    ErasureStep('preloaded', _erase_preloaded, required=False),  # Needs doc_name from step 1
    # ...
]
```

No ordering metadata; no dependency declaration.

### Driver Loop: Silently Ignores Unreachable Steps

**storage/documents.py:255-258**

```python
async def delete_doc(doc_id: str) -> dict:
    ctx = ErasureContext(doc_id=doc_id)
    
    for step in _ERASURE_MANIFEST:
        success = await step.coroutine(ctx, ...)
        if not success and step.required:
            ctx.errors.append(...)  # Log to ctx.errors
    
    return {'errors': ctx.errors}  # Empty if all required steps passed
```

Unrequired steps that fail silently don't appear in the return value.

## Historic Root Cause

**ISS-02 (delete_doc fire-and-forget registry delete):** Fix was applied to registry delete but missed `_cleanup_artifact` path.

**ISS-41:** Same gap rediscovered in parallel path.

Implication: Erasure logic was duplicated across the codebase, making it impossible to keep both paths in sync.

## Compliance Gap Pattern

New storage or new LLM paths automatically inherit the old blind spots without triggering codebase-wide audit:

- RFC-011/RFC-039: Boot-time ZDR gates added; per-call gates incomplete
- Erasure cascade: New store added → manifest entry → order dependency hidden

## Impact

- Reordering manifest entries silently converts purges to no-ops
- Verdict ledger purges fail silently if step 2d runs after step 3
- Hash cache / preloaded purges fail silently if step 1 fails
- Failed purges reported as success (`{'errors': []}`)
- Residual PII-derived artifacts (verdict ledger) survive erasure
- HR2 compliance cannot be verified end-to-end

## Files to Review

| File | Role |
|------|------|
| storage/documents.py | Manifest definition; driver loop; step implementations |
| storage/hash_cache.py | Consumer of ctx.doc_name |
| storage/reconcile_etag.py | Consistency verification |
| registry/queries.py | Verdict ledger queries; ledger lifecycle |

## Remediation Ideas

1. **Explicit dependency declarations** in ErasureStep (e.g., `depends_on=['uploads']`)
2. **Topological sort** of steps before execution
3. **Assertion on discovered fields** before steps that depend on them
4. **Integration test** verifying end-to-end: create doc → delete doc → verify all stores empty
5. **Codebase audit** for all erasure logic (look for ISS-02/ISS-41 pattern duplication)
