---
zone_name: HR2 Erasure Cascade Hidden Ordering Dependencies
severity: medium
bug_count: 1
status: improved
audit_date: 2026-09-01
audit_run: POST-RFC041
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-01_POST-RFC041.md
key_files:
  - src/pageindex_mcp/storage/documents.py
tags:
  - zone-spec
  - medium
  - compliance
  - erasure
  - ordering
scorecard_verdict: regressed
scorecard_date: 2026-09-01
scorecard_run: POST-RFC041
---
## Mechanism

The `_ERASURE_MANIFEST` presents as an order-independent declarative list of ErasureStep entries, but has hidden data-flow dependencies between steps: `ctx.doc_name` is only discovered inside step 1 (`_erase_uploads`), and `ctx.sha256` is only readable inside step 2d (from processed/<id>.meta.json before step 3 deletes that sidecar). Steps that fail to discover these values are marked `required=False`, so reordering or partial failure silently degrades a purge into a no-op that reports clean success with residual PII-derived artifacts.

1. **Implicit data dependencies:** `ctx.doc_name` needed by step 5 (hash-cache) and step 7 (preloaded raw object) is populated only by step 1 load_doc() call. If step 1 fails or is reordered after 5/7, those steps skip silently (required=False, not reported as errors).

2. **Sidecar read-then-delete order:** `ctx.sha256` needed by step 2d (verdict sidecar) is read from processed/<id>.meta.json — the same sidecar step 3 deletes. Ordering dependency undocumented outside prose comments.

3. **Compile-time validation gap:** validate_erasure_manifest at documents.py:644-678 checks PREFIX-to-step completeness at import time but does not validate data-flow ordering between steps, so adding a step that depends on data from a later step remains unguarded.

## History

- **Chain 17:** ISS-02 delete_doc fire-and-forget registry delete fixed for happy path; `_ERASURE_MANIFEST` refactored into 11 ErasureStep entries, but `ctx.doc_name` only discovered inside step1 and `ctx.sha256` only inside step2d (read from sidecar before step3 deletes it); reordering silently degrades purge to no-op reporting errors=[].

## Code Evidence

1. **ErasureStep** at documents.py:301-317 is frozen dataclass with name/step/description/execute/required fields.

2. **delete_doc** at documents.py:178-265 iterates `_ERASURE_MANIFEST`, catches exceptions per-step, logs missed_required vs missed_optional. Pre-loop doc_name recovery at line 205-211 handles happy path, falls through silently on ValueError.

3. **Compile-time validation:** validate_erasure_manifest at documents.py:644-678 asserts every `_KNOWN_STORAGE_PREFIXES` entry has matching ErasureStep — compile-time completeness but not ordering validation.

## Key Files

| File | Role |
|------|------|
| documents.py:301-317 | ErasureStep dataclass definition |
| documents.py:178-265 | delete_doc with per-step exception handling |
| documents.py:205-211 | Pre-loop doc_name recovery for happy path |
| documents.py:644-678 | validate_erasure_manifest compile-time check (incomplete) |
