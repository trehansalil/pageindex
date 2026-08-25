---
zone_name: Multi-Store Dual-Write Consistency
severity: high
wave: 3
priority: 1
status: triaged
audit_date: 2026-08-25
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md
tags:
  - zone-spec
  - high
  - wave-3
---
## Mechanism to Eliminate

Fan-out dual-write where converter child writes MinIO artifacts then parent re-reads MinIO via read_registry_fields() to extract registry columns for Postgres upsert. If the MinIO write is not yet read-visible, the parent gets partial/empty data, producing a Postgres row with missing fields. This partial row then gets deleted by _delete_stale_rows which treats empty processed_at as 'old enough to delete'. The pattern is compounded by three secondary HR2 violations: (1) hash_cache_delete only issues Redis HDEL, never purging the legacy MinIO blob hashes/processed_hashes.json, leaving filename-to-hash PII correlation surviving erasure; (2) staging objects keyed as uploads/staging/<job_id>/<filename> have no stored job_id-to-doc_id mapping, placing them outside delete_doc's uploads/<doc_id>/ scan; (3) delete_doc's 203-line monolithic inline cascade makes store omissions likely when adding new derived stores.

## Strategy

Eliminate MinIO re-read by construction: expand the child process JSON return (converters_cli.py) to carry all _REGISTRY_FIELDS alongside existing verdict_fields, so _upsert_registry_row receives the full payload directly from the child's stdout. This closes the persistence-timing race window for ALL registry columns, not just verdict columns. Secondarily: (A) add legacy-blob purge to hash_cache_delete for HR2 compliance, (B) add a job_id-to-doc_id Redis mapping written at job completion so staging objects become reachable by delete_doc, (C) extract delete_doc's inline cascade into a declarative erasure manifest (list of (store, key_pattern, required) tuples iterated by a compact driver loop), and (D) invert _delete_stale_rows' empty-processed_at default so partial-write rows get the age-guard grace period instead of being treated as immediately stale.

## Code Targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| src/pageindex_mcp/client/indexer.py | 1063-1072 | Add last_registry_fields stash alongside existing last_verdict_fields in _persist_tree_result | After line 1071 (last_verdict_fields assignment), add self.last_registry_fields = { 'doc_name': filename, 'source_url': source_url, 'processed_at': processed_at, 'sha256': sha256, 'doc_description': state.result.get('doc_description', ''), 'product': '', 'tier': '', 'doc_family': '', 'effective_date': '', 'node_count': len(structure) } containing all _REGISTRY_FIELDS values computed in-memory during persist. This dict is what the parent would otherwise re-read from MinIO via read_registry_fields. | Must include every key in _REGISTRY_FIELDS (verdict.py:225-236) plus node_count; values must match exactly what save_doc writes to MinIO so the parent receives identical data without a re-read |
| src/pageindex_mcp/client/indexer.py | 938-947 | Add last_registry_fields stash in _persist_flat_result (flat doc path) | After line 946 (last_verdict_fields assignment for flat docs), add self.last_registry_fields = { 'doc_name': filename, 'source_url': source_url, 'processed_at': processed_at, 'sha256': sha256, 'content_class': content_class, 'doc_description': flat_desc, 'product': '', 'tier': '', 'doc_family': '', 'effective_date': '', 'node_count': 0 } mirroring the tree-path stash for flat documents (node_count=0 matches read_registry_fields behavior for flat docs). | content_class must be included for flat docs; node_count must be 0 to match read_registry_fields flat-doc convention |
| src/pageindex_mcp/converters_cli.py | 162-169 | Surface last_registry_fields in the child's stdout JSON payload alongside verdict_fields | After the verdict_fields block (line 168), add: registry_fields = getattr(client, 'last_registry_fields', None); if registry_fields: payload['registry_fields'] = registry_fields. Pattern identical to existing verdict_fields surfacing. Backward-compatible: old workers that do not read registry_fields simply ignore the extra key. | Must not emit registry_fields when None (backward compat with older workers); key name must be 'registry_fields' to match the new _upsert_registry_row parameter |
| src/pageindex_mcp/worker/job.py | 355-362 | Extract registry_fields from child result and pass to _upsert_registry_row | After verdict_fields = result.get('verdict_fields') (approx line 357), add: registry_fields = result.get('registry_fields'). Change the _upsert_registry_row call to pass registry_fields=registry_fields as an additional kwarg. Falls back gracefully when registry_fields is absent (older child binaries). | Must be backward-compatible: when registry_fields is None, _upsert_registry_row falls back to existing read_registry_fields MinIO-read path |
| src/pageindex_mcp/worker/registry_mirror.py | 55-135 | Accept optional registry_fields dict; when present, skip read_registry_fields MinIO re-read | Add parameter registry_fields: dict[str, Any] | None = None to _upsert_registry_row signature. Inside the try block, replace the unconditional `fields = await asyncio.to_thread(read_registry_fields, doc_id, content_class)` with: if registry_fields is not None, use fields = dict(registry_fields) (copy to avoid mutation); fields['doc_id'] = doc_id; else fall back to existing MinIO read. Then merge verdict_fields on top as before. This eliminates the persistence-timing race by construction when registry_fields is available. | When registry_fields is None, behavior must be identical to current code (backward compat for preprocess_client.py batch CLI and reconcile callers). The verdict_fields overlay must still apply on top of registry_fields. |
| src/pageindex_mcp/storage/hash_cache.py | 68-72 | Purge legacy MinIO blob entry alongside Redis HDEL (HR2 compliance) | After the existing HDEL call, add a best-effort legacy-blob purge: try to load _load_legacy_minio_hash_cache(), check if filename is a key, if so pop it and re-PUT the shrunk blob via minio_ops.get_minio().put_object(). Wrap in try/except (best-effort, log warning on failure). If the blob does not exist (NoSuchKey) or filename is absent, no-op. This closes the HR2 gap where filename+hash correlation survived erasure in the legacy store. | Must be best-effort (never fail the erasure cascade). Last-writer-wins is acceptable since the legacy blob is append-shrink only. Must import _load_legacy_minio_hash_cache lazily to avoid circular imports. |
| src/pageindex_mcp/storage/documents.py | 141-343 | Extract monolithic 7-step inline cascade into declarative erasure manifest | Define an ErasureStep dataclass/NamedTuple at module level: (name: str, execute: Callable[[str, str|None, MinioClient], Awaitable[None]|None], required: bool). Build _ERASURE_MANIFEST as a list of ErasureStep tuples, one per current inline step (1-7), each wrapping its existing logic in a small async callable. Replace the 203-line inline body with a 15-line driver loop that iterates _ERASURE_MANIFEST, calls each step, catches exceptions, appends to errors[] if the step failed but is required. Adding a new derived store becomes a one-line manifest entry. | Manifest ordering must match CLAUDE.md HR2 cascade order: uploads -> processed -> meta -> Redis -> hash-cache -> registry -> preloaded. Each step's required flag must match current error-reporting behavior (some steps tolerate NoSuchKey, others report). |
| src/pageindex_mcp/registry_backfill/cleanup.py | 56-75 | Invert empty-processed_at stale candidate default: protect instead of delete | In _delete_stale_rows, change the empty-processed_at handling from 'treat as old enough (continue)' to 'age_protected.add(doc_id)' so rows with empty/missing processed_at get the grace period instead of being immediately deletable. This prevents partial-write rows (whose processed_at was not yet written due to the dual-write race) from being deleted by the next reconcile tick. | Config-gated: add a setting (e.g. cleanup_protect_empty_processed_at, default True) so truly stale legacy rows can still be swept via a manual override. Rows with parseable but old processed_at are unaffected. |

## Wiring Checks

| Symbol | Must Be Imported By | Check Type |
|---|---|---|
| last_registry_fields | ['src/pageindex_mcp/converters_cli.py'] | dispatch |
| registry_fields | ['src/pageindex_mcp/worker/job.py', 'src/pageindex_mcp/worker/registry_mirror.py'] | call |
| _ERASURE_MANIFEST | ['src/pageindex_mcp/storage/documents.py'] | dispatch |
| ErasureStep | ['src/pageindex_mcp/storage/documents.py'] | isinstance |
| _purge_legacy_hash_entry | ['src/pageindex_mcp/storage/hash_cache.py'] | call |
| cleanup_protect_empty_processed_at | ['src/pageindex_mcp/registry_backfill/cleanup.py'] | import |

## Test Requirements

| Test File | What to Test | Assertion Type |
|---|---|---|
| tests/test_registry_mirror.py | When registry_fields kwarg is provided, _upsert_registry_row must NOT call read_registry_fields (no MinIO re-read). Verify upsert_doc receives the registry_fields values directly. When registry_fields is None, verify read_registry_fields IS called (backward compat). | contract |
| tests/test_registry_mirror.py | When both registry_fields and verdict_fields are provided, verdict_fields values must override any overlapping keys in registry_fields (overlay semantics preserved). | contract |
| tests/test_converters_cli.py | Successful child stdout JSON must include registry_fields dict with all _REGISTRY_FIELDS keys when client.last_registry_fields is set. Must NOT include registry_fields key when last_registry_fields is None (backward compat). | exhaustiveness |
| tests/test_worker.py | process_document_job extracts registry_fields from child result and passes to _upsert_registry_row. When child result lacks registry_fields (old binary), _upsert_registry_row is called with registry_fields=None. | wiring |
| tests/test_storage.py | hash_cache_delete must issue both Redis HDEL AND attempt legacy MinIO blob purge. When legacy blob contains the filename, it must be removed. When legacy blob does not exist, no error. When legacy blob purge fails, Redis HDEL must still have succeeded (best-effort). | contract |
| tests/test_storage.py | ErasureManifest ordering test: _ERASURE_MANIFEST step names must appear in HR2 cascade order (uploads, processed, meta, redis-cache, reconcile-etag, hash-cache, registry, preloaded). Each step's required flag must match the current behavior. | exhaustiveness |
| tests/test_storage.py | delete_doc with declarative manifest produces identical errors[] output as current inline cascade for: full success, partial MinIO failure, registry timeout, unknown doc_name scenarios. | regression |
| tests/test_registry_backfill.py | _delete_stale_rows must protect rows with empty/missing processed_at via age guard (not treat them as stale candidates) when cleanup_protect_empty_processed_at is True (default). When the setting is False, old behavior (treat as stale) must be preserved. | contract |
| tests/test_converters_cli.py | last_registry_fields stashed by _persist_tree_result must contain all keys matching _REGISTRY_FIELDS plus node_count. last_registry_fields from _persist_flat_result must include content_class and have node_count=0. | exhaustiveness |

## Corpus Validation

- **Affected documents:** ['cabinet_resolution_no_96', 'world-stats-pocketbook']
- **Expected verdict direction:** stable
- **Spot check count:** 3

## Dependencies

Depends on: Verdict Promotion / Quality Gate Stack

## Complexity

medium
