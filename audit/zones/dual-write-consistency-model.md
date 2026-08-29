---
zone_name: Dual-Write Consistency Model
severity: high
bug_count: 3
status: improved
audit_date: 2026-08-12
audit_run: POST
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md
key_files:
  - src/pageindex_mcp/storage/verdict.py
  - src/pageindex_mcp/storage/documents.py
  - src/pageindex_mcp/worker/registry_mirror.py
  - src/pageindex_mcp/registry/queries.py
tags:
  - zone-spec
  - high
  - dual-write
  - consistency
scorecard_verdict: regressed
scorecard_date: 2026-08-12
scorecard_run: POST
---
## Mechanism

Asymmetric write-visibility guarantees: save_doc (documents.py:106) and save_flat_doc (documents.py:165) call _confirm_write_visible for read-after-write consistency; save_doc_meta (verdict.py:78-198) explicitly omits the barrier ('eventual consistency' by design).

A reader racing right after a sidecar write has no positive visibility guarantee. _upsert_registry_row (registry_mirror.py:56-200) has three possible data sources for the same Postgres row: in-memory registry_fields, MinIO-read artifact fields, and job-context verdict_fields — reconciled via documented precedence order.

force_verdict_override is deliberately popped from the fields dict so it is never persisted. upsert_doc (queries.py:130-184) uses meta.get('verdict','') at line 175 — empty string is a valid SQL value that the ON CONFLICT CAS treats as a verdict, enabling silent overwrite of existing FAIL rows.

reconcile_registry_drift has load-bearing step ordering: drain_verdict_retry_queue MUST run BEFORE the MinIO etag diff scan.

## Code Evidence

**save_doc_meta** (verdict.py:78-198): line 193 documents 'eventual consistency' by design, no visibility barrier.

**_upsert_registry_row** (registry_mirror.py:56-200): three data sources reconciled via precedence order, force_verdict_override popped at line 168.

**upsert_doc** (queries.py:130-184): meta.get('verdict','') at line 175, ON CONFLICT CAS with empty-string default.

## Key Files

| File | Role |
|------|------|
| src/pageindex_mcp/storage/verdict.py | Sidecar writes without visibility barrier |
| src/pageindex_mcp/storage/documents.py | Write-visible barrier pattern |
| src/pageindex_mcp/worker/registry_mirror.py | Three-way data source reconciliation |
| src/pageindex_mcp/registry/queries.py | Upsert with empty-string default |

## Related Zones

- [[measurement-and-audit-self-reinforcing-blind-spot]] (consistency affects measurements)
