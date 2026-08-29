---
zone_name: Erasure Cascade (Manually-Maintained Manifest)
severity: medium
bug_count: 2
status: stalled
audit_date: 2026-08-12
audit_run: POST
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md
key_files:
  - src/pageindex_mcp/storage/documents.py
  - src/pageindex_mcp/registry/queries.py
tags:
  - zone-spec
  - medium
  - erasure
  - manifest
scorecard_verdict: regressed
scorecard_date: 2026-08-12
scorecard_run: POST
---
## Mechanism

_ERASURE_MANIFEST (documents.py:551+) is a 10-step tuple that must mirror every MinIO prefix, Redis key, Postgres table, and filesystem cache that the ingestion pipeline writes to.

When the preloaded/<filename> write path was added for raw uploads, no corresponding erasure step existed — discovered only by manual audit (ISS-41), not by construction.

delete_doc (documents.py:178-265) iterates the manifest sequentially and logs gaps between required and completed steps, but there is no compile-time or test-time assertion that the manifest covers all write paths.

The fire-and-forget registry delete (ISS-02) was later wrapped in asyncio.wait_for(timeout=settings.registry_delete_timeout_s), but the underlying DELETE query in registry still has no statement/connection timeout of its own (ISS-40).

## Code Evidence

**_ERASURE_MANIFEST** (documents.py:551+): 10-step tuple of ErasureStep entries. The preloaded step (ISS-41 addition) at documents.py:615-620 marks it required=False.

**_erase_registry** (documents.py:510-529): asyncio.wait_for with timeout, TimeoutError caught and recorded but does not abort cascade.

**delete_doc** (documents.py:178-265): manifest-driven iteration with completeness check comparing ctx.completed against steps.

## Key Files

| File | Role |
|------|------|
| src/pageindex_mcp/storage/documents.py | Manifest definition & deletion orchestration |
| src/pageindex_mcp/registry/queries.py | Registry delete query |

## Related Zones

- [[dual-write-consistency-model]] (registry delete is part of cascade)
