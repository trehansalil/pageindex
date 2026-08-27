---
zone_name: Erasure Cascade / Storage Consistency
severity: high
bug_count: 2
status: improved
audit_date: 2026-08-27
audit_run: POST-RUN20
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-27_POST-RUN20.md
key_files:
  - src/pageindex_mcp/storage/documents.py
  - src/pageindex_mcp/storage/verdict.py
  - src/pageindex_mcp/worker/registry_mirror.py
  - src/pageindex_mcp/registry/queries.py
tags:
  - zone-spec
  - high
  - erasure
  - dsr
  - consistency
scorecard_verdict: needs_another_cycle
scorecard_date: 2026-08-27
scorecard_run: POST-RUN20
wave: 3
---
## Mechanism

The HR2 right-to-erasure cascade (delete_doc) is driven by _ERASURE_MANIFEST, a tuple of 11 ErasureStep entries. Each new storage prefix or derived store requires adding a corresponding step to the manifest, but the manifest is not mechanically derived from the storage-write code paths — it is a manually maintained list that drifts when new ingestion routes add storage locations. The verdict-write architecture has three named entry points (write_verdict → save_doc_meta; save_doc_meta; _upsert_registry_row → upsert_doc) into overlapping state with asymmetric durability: save_doc/save_flat_doc retain write-visibility barriers, save_doc_meta deliberately removed them (Zone-4 Phase 3). When registry_enabled is false or pool unavailable, the sidecar becomes the sole source of truth, changing the effective consistency model.

The generative mechanism operates through decoupled storage-write paths with a manually-maintained erasure manifest:
- a. The preloaded/<filename> prefix was added by a later ingestion path but the erasure cascade was designed against the original prefix set — the step was added only after audit discovery (chain 9).
- b. The registry-delete step was historically fire-and-forget, so delete_doc logged 'full cascade succeeded' even when the Postgres row delete silently failed (chain 9, now fixed: _erase_registry uses asyncio.wait_for).
- c. The asymmetric write-visibility barrier between save_doc (retained) and save_doc_meta (removed) means reading the sidecar immediately after a verdict write has a weaker consistency guarantee than reading a tree artifact.
- d. New storage locations (figures/<doc_id>/*, verdicts/<sha256>.json) had to be added retroactively, each time discovered by audit rather than by construction.

## Code Evidence

`_ERASURE_MANIFEST` at storage/documents.py:510-581 lists 11 ErasureSteps. `delete_doc` at documents.py:145-224 iterates: `for entry in _ERASURE_MANIFEST: reached = await entry.execute(ctx)` with completeness check at lines 200-215 logging missed_required and missed_optional. `_erase_registry` at documents.py:452-488 uses `await asyncio.wait_for(_registry_delete_doc(ctx.doc_id), timeout=settings.registry_delete_timeout_s)`. `save_doc_meta` at verdict.py:176-177 documents: 'Zone-4 Phase 3: write-visibility barrier removed'. `_upsert_registry_row` at registry_mirror.py:55-155 performs CAS upsert with verdict_fields overlay and best-effort sidecar backfill.

## Key Files

| File | Role |
|---|---|
| src/pageindex_mcp/storage/documents.py | Erasure cascade manifest & execution |
| src/pageindex_mcp/storage/verdict.py | Verdict sidecar persistence |
| src/pageindex_mcp/worker/registry_mirror.py | Registry consistency & upsert |
| src/pageindex_mcp/registry/queries.py | Registry delete operations |
