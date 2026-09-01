<!-- Space: CITRA -->
<!-- Title: RFC-038: Worker Timeout Unification -->
<!-- Folder: RFCs -->

---
id: "RFC-038"
title: "Worker Timeout Unification and Inspector Threshold Alignment"
type: rfc
status: draft
date: "2026-08-24"
plan-impact: "yes"
tags:
  - rfc
  - worker
  - timeout
  - inspector
  - reliability
aliases:
  - "RFC-038"
  - "Worker-Timeout-Unification"
governs:
  - "[[design-rfc038-worker-timeout-unification]]"
  - "[[tasks-rfc038-worker-timeout-unification]]"
supersedes: []
---

## Context

The post-fix-11 architecture defect zones audit (2026-08-24) identified Zone 7 as a medium-severity defect zone with 6 bugs affecting worker timeout management and inspector threshold alignment.

The `PDF_INSPECTOR_PRECLASSIFY` feature, introduced in [[RFC-031]]/[[RFC-032]], has a split-brain threshold problem. The client-side OCR-forcing gate in `indexer.py:354-382` requires `confidence >= 0.90` before triggering full-page OCR on scanned/image-based PDFs. However, the worker-side timeout multiplier in `subprocess_mgr.py:179-193` applies a 16.5× timeout extension based on `pdf_type` alone with **no confidence check**. A document classified as scanned at confidence 0.50 receives an enormous timeout budget but never runs the forced OCR that the budget was sized for.

`JOB_TIMEOUT = 3630` is independently defined in both `job.py:55` and `subprocess_mgr.py:39` — the comment at `subprocess_mgr.py:38` explicitly admits this duplication. `CHILD_TIMEOUT` and `CHILD_GRACE_SECONDS` are derived from the duplicated value, compounding the risk of silent divergence.

The effective timeout multiplication chain is unbounded: the chunked Docling timeout and the 16.5× inspector multiplier are **not mutually exclusive** (`subprocess_mgr.py:155-195`). When both apply, the computed effective timeout can reach ~500,000 seconds while `JOB_TIMEOUT` remains 3,630 seconds.

The extended deadline computed after a converter subprocess emits its handshake is only persisted to Redis **after the subprocess completes** (`job.py:246-260`), not when the handshake is received. This creates a race window: the initial Redis deadline is set conservatively at job start (`job.py:153-162`, `now + JOB_TIMEOUT + REAP_GRACE = 3750s`), and `reap_stale_jobs` (`job.py:399-460`, running every 60 seconds) sees only the conservative deadline. A legitimately running long job can be falsely reaped during the gap between handshake and subprocess completion. The `late_success` ERROR→DONE recovery path (`job.py:282-316`, allowed by the state machine in `job_status.py:36-46`) masks this race rather than closing it.

Additionally, page-count is computed independently in three locations — `pipeline.py:180-202` (fitz, gated by `ALLOW_AGPL_FALLBACK`, reports 0 when false), `docling_conv.py:370-411` (pypdfium2, unconditional), and `indexer.py:386-401` (fitz, gated) — though unifying these is a separate concern.

Prior RFCs: [[RFC-028]] raised `JOB_TIMEOUT` to 3630; [[RFC-032]] calibrated the 16.5× multiplier.

## Goals

- Align the inspector confidence threshold between `indexer.py` and `subprocess_mgr.py` so that timeout budget and forced-OCR decisions are always consistent.
- Persist the extended deadline to Redis immediately after the handshake so that `reap_stale_jobs` sees the true deadline and cannot falsely reap legitimately running jobs.
- Eliminate the `JOB_TIMEOUT` duplication by extracting the canonical value to a shared constants module.
- Cap the effective timeout multiplication chain so that chunked Docling timeout and the 16.5× inspector multiplier cannot compound unboundedly.

## Non-Goals

- Changing the 16.5× multiplier value itself. Recalibration requires more corpus data and belongs in a future RFC.
- Removing the `late_success` ERROR→DONE recovery path. It remains as a safety net but should fire near-zero after the early-deadline fix.
- Replacing the subprocess isolation architecture. The child-process model is correct; the issue is deadline signaling.
- Unifying the three independent page-count computations (`fitz` vs `pypdfium2`). The discrepancy when `ALLOW_AGPL_FALLBACK=false` is an [[RFC-034]] D4 follow-on concern.

## Glossary

| Term | Definition |
|------|------------|
| Handshake | The JSON object written to stdout by the converter child process after it determines the conversion route, chunk count, and PDF classification. |
| Effective_Timeout | The actual timeout applied to a child process, potentially multiplied by the inspector 16.5× factor and/or extended by chunked Docling timeout. |
| Reap_Stale_Jobs | A cron task (`job.py:399-460`) that runs every 60 seconds to detect and mark as ERROR any job whose `effective_timeout_at` has passed. |
| Late_Success | The ERROR→DONE recovery transition (`job.py:282-316`) that fires when a subprocess completes after the reaper has already marked the job as ERROR. |
| Inspector_Force_OCR | The flag set when `PDF_INSPECTOR_PRECLASSIFY` classifies a document as scanned/image-based with sufficient confidence, triggering full-page OCR in `indexer.py`. |
| CHILD_TIMEOUT | The subprocess-level timeout (`_JOB_TIMEOUT - CHILD_GRACE_SECONDS`), currently 3600 seconds, before the 16.5× or chunked multipliers are applied. |

## Requirements

### Requirement 1: Threshold Consistency

**User Story:** As a pipeline operator, I want the inspector confidence threshold applied consistently in both client-side OCR decisions and worker-side timeout budgeting, so that timeout budgets always match the work that will actually be performed.

#### Acceptance Criteria

1. WHEN `PDF_INSPECTOR_PRECLASSIFY` is enabled and the pdf_classification indicates scanned/image_based, THE `subprocess_mgr.py` timeout multiplier SHALL only apply if `confidence >= INSPECTOR_CONFIDENCE_THRESHOLD` (the same threshold used in `indexer.py:365-380`).
2. IF confidence is below the threshold, THEN the effective_timeout SHALL remain at the base `CHILD_TIMEOUT` (no multiplier applied).
3. THE confidence threshold value SHALL be defined as a single named constant (`INSPECTOR_CONFIDENCE_THRESHOLD`) shared between `indexer.py` and `subprocess_mgr.py`.

### Requirement 2: Early Deadline Persistence

**User Story:** As a pipeline operator, I want the extended deadline persisted to Redis immediately after the handshake, so that `reap_stale_jobs` never falsely reaps a legitimately running job.

#### Acceptance Criteria

1. WHEN the converter child process emits a handshake that extends the effective_timeout (via chunked Docling timeout or 16.5× multiplier), THE worker SHALL update `effective_timeout_at` in Redis within 1 second of computing the new deadline.
2. WHILE a converter child process is running, THE `effective_timeout_at` in Redis SHALL reflect the actual deadline the worker will enforce, not the conservative default.
3. IF the handshake fails to parse, THEN the conservative default deadline SHALL remain in Redis (no regression from current behavior).

### Requirement 3: Timeout Constant Deduplication

**User Story:** As a developer, I want `JOB_TIMEOUT` defined in exactly one place, so that future changes cannot create silent divergence between `job.py` and `subprocess_mgr.py`.

#### Acceptance Criteria

1. WHEN `JOB_TIMEOUT` is needed, ALL modules SHALL import it from a single canonical location (`worker/constants.py`).
2. THE comment at `subprocess_mgr.py:38` admitting duplication SHALL be removed after the deduplication.
3. `CHILD_TIMEOUT`, `CHILD_GRACE_SECONDS`, and `REAP_GRACE` SHOULD also be consolidated into `worker/constants.py` alongside `JOB_TIMEOUT`.

### Requirement 4: Timeout Multiplication Cap

**User Story:** As a pipeline operator, I want a hard cap on effective timeout so that no combination of timeout multipliers can produce an absurdly long deadline that outlives the job's useful lifetime.

#### Acceptance Criteria

1. WHEN the chunked Docling timeout and the 16.5× multiplier both apply, THE effective_timeout SHALL be capped at a configurable maximum (`MAX_EFFECTIVE_TIMEOUT`, default 54,000 seconds / 15 hours).
2. IF the computed effective_timeout exceeds the cap, THE worker SHALL log a warning with the uncapped value and apply the cap.
3. `MAX_EFFECTIVE_TIMEOUT` MAY be overridden via environment variable for deployments with exceptionally large documents.

## Decision Summary

This RFC closes the inspector split-brain by adding a confidence gate to `subprocess_mgr.py`'s 16.5× multiplier, persists the extended deadline to Redis immediately after the handshake, extracts all worker timing constants to a shared module, and adds a configurable effective-timeout cap. Core decisions:

### D1 — Confidence Gate Alignment

Add `confidence >= INSPECTOR_CONFIDENCE_THRESHOLD` check to the 16.5× timeout multiplier in `subprocess_mgr.py:179`. The threshold constant is imported from `worker/constants.py`, shared with `indexer.py`. When confidence is below the threshold, the multiplier is not applied regardless of `pdf_type`.

### D2 — Early Deadline Persistence

After the handshake is parsed in `subprocess_mgr.py` (~line 193), the computed `effective_timeout` is returned to the caller in `job.py`, which immediately calls `await redis.hset(job_key, "effective_timeout_at", str(int(time.time()) + effective_timeout + REAP_GRACE))`. This requires either threading the Redis handle and job_id into `_run_converter_subprocess` or returning the `effective_timeout` for the caller in `job.py` to persist. The latter approach (returning `effective_timeout`) is preferred to keep subprocess management decoupled from Redis.

### D3 — Constants Extraction

Create `worker/constants.py` containing `JOB_TIMEOUT`, `CHILD_TIMEOUT`, `CHILD_GRACE_SECONDS`, `REAP_GRACE`, `INSPECTOR_CONFIDENCE_THRESHOLD`, and `MAX_EFFECTIVE_TIMEOUT`. All consumers (`job.py`, `subprocess_mgr.py`, `indexer.py`) import from this single source. Remove the duplicated definitions and the admission comment.

### D4 — Effective Timeout Cap

Add `MAX_EFFECTIVE_TIMEOUT` (env-configurable, default 54,000 seconds) to `worker/constants.py`. Apply `min(effective_timeout, MAX_EFFECTIVE_TIMEOUT)` after all multiplication chains in `subprocess_mgr.py`. Log a warning when the cap is hit, including the uncapped computed value for diagnostic purposes.

## Consequences

- The `late_success` ERROR→DONE recovery path frequency SHOULD drop to near-zero after D2 lands. A new `REAPER_FALSE_POSITIVE` metric SHOULD be added to track how often `late_success` fires post-fix; if it drops to zero over a full corpus cycle, consider removing the ERROR→DONE transition in a future RFC.
- The `_run_converter_subprocess` function's return contract changes: it now returns `(result, effective_timeout)` instead of just `result`, so the caller in `job.py` can persist the deadline. This is a minor interface change affecting one call site.
- The new `worker/constants.py` module becomes the canonical source for all worker timing constants. Any future timeout-related changes go there.
- Documents that genuinely require >15 hours of processing (if any exist) will need `MAX_EFFECTIVE_TIMEOUT` raised via environment variable. The warning log emitted at cap-hit provides the necessary diagnostic signal.
- The three independent page-count computations remain unchanged. Unifying them is deferred to a future RFC, as the fix requires resolving the AGPL gating question for `fitz`.

## Traceability

| Artifact | Reference |
|----------|-----------|
| Design   | [[design-rfc038-worker-timeout-unification]] |
| Tasks    | [[tasks-rfc038-worker-timeout-unification]] |
| Supersedes | N/A (refines [[RFC-028]] D0, [[RFC-032]] D9) |
| Audit    | [ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-24_POST-FIX-11.md](../../audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-24_POST-FIX-11.md) Zone 7 |
