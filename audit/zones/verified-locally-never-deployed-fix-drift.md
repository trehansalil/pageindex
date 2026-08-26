---
zone_name: Verified-Locally-Never-Deployed Fix Drift
severity: medium
bug_count: 4
status: improved
audit_date: 2026-08-26
audit_run: POST-FIX-12
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-12.md
key_files:
  - src/pageindex_mcp/converters/normalize.py
  - src/pageindex_mcp/worker/registry_mirror.py
  - src/pageindex_mcp/converters/ocr_langs.py
tags:
  - zone-spec
  - medium
scorecard_verdict: regressed
scorecard_date: 2026-08-26
scorecard_run: POST-FIX-12
---
## Mechanism

Multiple critical fixes were implemented, tested, and marked complete in task files but never actually landed in a commit or reached the deployed artifact. The generative mechanism is a documentation/deployment gap where the task-tracking system (tasks files, RFC deliverable markers) is disconnected from the actual deployment pipeline (git commits, container image builds, service deployments).

A fix can be 'complete' in the task file while existing only in the working tree, never committed. The remote service runs a stale image with no automated parity check against the local codebase. This creates a state where the documented system and the running system diverge silently — subsequent RFCs are written against the documented (fixed) state, so they cannot anticipate the still-present defect in production.

The 0%-TPR promotion pattern is a variant: a detector's null output is treated as evidence of safety rather than evidence of detector failure.

## Evidence History

| RFC/Issue | Finding |
|---|---|
| RFC-033 D2 | `_heading_is_logical_order` heading-order guard exists in NO commit (git log -S finds nothing); worker never re-normalizes remote route markdown (23/23 headings corrupted on fresh Arabic document ingest) |
| RFC-027 task 4.2 | `chunked_docling_timeout_s` created but never wired to worker.py; marked complete in tasks file; world-stats-pocketbook timed out 3 consecutive runs (ERROR, FAIL, ERROR) before RFC-032 D3/D9 recalibrated multiplier |
| RFC-033 D2 | `_check_bidi_coherence` detector promoted to BIDI_COHERENCE_ENFORCE=true default on a 0%-TPR instrument, misreading zero detections as zero violations |
| RFC-029 D6 | Judge calibration rules documented but not verified as deployed |

## Code Evidence

**ensure_tessdata** (ocr_langs.py:91-188) — Documented but bypassed fix
```python
# Documents TessdataUnavailableError for non-Latin languages as Zone-3 fix
# But final fallback to ['deu', 'eng'] at lines 186-188 still fires
# when no languages are available
# Fix for non-Latin languages (raising error) is bypassed by catch-all fallback
```

**_upsert_registry_row** (registry_mirror.py:55-155) — Phase-3 addition
```python
# Skip when get_pool() is None queues retry via _enqueue_verdict_retry
# But this path was added in Zone-4 Phase 3
# Prior to that, pool unavailability silently dropped the write
```

**RFC-033 D2 heading guard**
```python
# _heading_is_logical_order not found in any commit
# git log -S "_heading_is_logical_order" returns nothing
# Yet documented as complete and deployed
```

**RFC-027 timeout wiring**
```python
# chunked_docling_timeout_s created in config
# But never wired to worker.py initialization
# Still marked complete in tasks file
```

**0%-TPR null-detector pattern**
```python
# _check_bidi_coherence measuring 0% true positives
# Treated as evidence of safety, not evidence of detector failure
# Promoted to BIDI_COHERENCE_ENFORCE=true default
```

## Key Files

- src/pageindex_mcp/converters/normalize.py
- src/pageindex_mcp/worker/registry_mirror.py
- src/pageindex_mcp/converters/ocr_langs.py
