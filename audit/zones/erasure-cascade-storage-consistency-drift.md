---
zone_name: Erasure Cascade and Storage Consistency Drift
severity: medium
bug_count: 2
status: improved
audit_date: 2026-08-28
audit_run: POST-FIX-WAVE3
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md
key_files:
  - src/pageindex_mcp/storage/documents.py
  - src/pageindex_mcp/registry/queries.py
  - src/pageindex_mcp/storage/verdict.py
tags:
  - zone-spec
  - medium
  - erasure
  - dsr
  - compliance
scorecard_verdict: regressed
scorecard_date: 2026-08-28
scorecard_run: POST-FIX-WAVE3
---
## Mechanism

Right-to-erasure cascade (CLAUDE.md Hard Rule #2) is driven by manually-maintained `_ERASURE_MANIFEST` tuple enumerating every storage prefix. When new ingestion routes add locations, manifest **drifts out of sync**. Discovered missing only by audit (ISS-41):

- preloaded/<filename> prefix was missing
- Erasure cascades across MinIO, Redis, Postgres, hash-cache stores with **ordered steps**
- Registry-delete step historically was **fire-and-forget** (logging success on silent failure)
- Still lacks statement timeout on underlying DELETE query (ISS-40)
- Asymmetric consistency model (save_doc with barrier vs. save_doc_meta without) means erasure can race with concurrent write

## Code Evidence

**documents.py:551–618** — `_ERASURE_MANIFEST`:
- 11 ErasureStep entries: uploads → processed_json → processed_flat_json → figures → verdicts → meta_json → redis_cache → reconcile_etag → hash_cache → registry → preloaded
- preloaded step (lines 539–544) was ISS-41 addition
- Each step has name, step number, description, execute coroutine, optional required=False flag
- Manifest is module-level tuple constant with **no mechanical derivation** from storage-write functions (save_doc, save_flat_doc, save_doc_meta, _stage_to_minio)

## Key Files

| File | Role |
|------|------|
| src/pageindex_mcp/storage/documents.py | _ERASURE_MANIFEST definition and delete_doc driver |
| src/pageindex_mcp/registry/queries.py | Registry-delete step (fire-and-forget → timeout) |
| src/pageindex_mcp/storage/verdict.py | save_doc_meta barrier asymmetry |

## Related Issues

- **ISS-41:** preloaded/ prefix missing from manifest (discovered by audit)
- **ISS-40:** registry-delete lacks statement timeout (asyncio.wait_for timeout added, but DELETE query itself not protected)

## Hard Rule Compliance

**CLAUDE.md Hard Rule #2:** "Right-to-erasure must cascade across every derived store. Deleting the raw upload does NOT auto-remove derivatives. Purge MinIO uploads/, processed/*.json, processed/*.meta.json, Redis cache, and any documented backup explicitly."

Current implementation: 11-step cascade with manual manifest enumeration prone to drift.

## Critical Properties

1. Manifest enumeration must be exhaustive — any missing prefix leaves data behind
2. Step ordering matters (Redis verdict retry queue drained before MinIO etag diff per Zone 5)
3. Asymmetric consistency (save_doc barrier vs. save_doc_meta no barrier) creates race window

