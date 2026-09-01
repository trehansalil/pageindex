<!-- Space: CITRA -->
<!-- Title: Implementation Plan: Worker Timeout Unification and Inspector Threshold Alignment -->
<!-- Folder: Tasks -->

---
id: "tasks-rfc038-worker-timeout-unification"
title: "Tasks: Worker Timeout Unification and Inspector Threshold Alignment"
type: tasks
status: draft
date: "2026-08-24"
tags:
  - tasks
  - worker
  - timeout
  - inspector
  - reliability
aliases:
  - "tasks-rfc038-worker-timeout-unification"
governs:
  - "[[RFC-038]]"
---

# Implementation Plan: Worker Timeout Unification and Inspector Threshold Alignment

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [RFC-038](../rfcs/038-worker-timeout-unification.md) |
| Design Document | [design-rfc038-worker-timeout-unification](../designs/design-rfc038-worker-timeout-unification.md) |
| PRD / Requirements | [[PRD]] |
| Implementation Order | [RFC-038 §Decision Summary](../rfcs/038-worker-timeout-unification.md#decision-summary) |
| Test Strategy | [Design §Testing Strategy](../designs/design-rfc038-worker-timeout-unification.md#testing-strategy) |
| Correctness Properties | [Design §Correctness Properties](../designs/design-rfc038-worker-timeout-unification.md#correctness-properties) |

## Overview

This implementation plan proceeds in three phases: Phase 1 extracts timing constants to a single module and aligns the inspector confidence gate (D1+D3), Phase 2 adds early deadline persistence and the timeout multiplication cap (D2+D4), and Phase 3 validates the full integration with property-based tests. Each phase has a checkpoint. The plan validates 4 correctness properties defined in the [design document](../designs/design-rfc038-worker-timeout-unification.md#correctness-properties).

## Tasks

- [x] <a id="1-phase-1--constants-and-confidence-gate-d1-d3"></a>1. Phase 1 — Constants and Confidence Gate (D1+D3)

  *[RFC-038 D3](../rfcs/038-worker-timeout-unification.md#d3--constants-extraction) + [RFC-038 D1](../rfcs/038-worker-timeout-unification.md#d1--confidence-gate-alignment)*

  - [x] <a id="11-extract-worker-timing-constants-d3"></a>1.1 Extract worker timing constants ([D3](../rfcs/038-worker-timeout-unification.md#d3--constants-extraction))

    - Create `worker/constants.py` with `JOB_TIMEOUT`, `CHILD_GRACE_SECONDS`, `CHILD_TIMEOUT`, `REAP_GRACE`, `INSPECTOR_CONFIDENCE_THRESHOLD`, `MAX_EFFECTIVE_TIMEOUT` (env-configurable)
    - Update `worker/subprocess_mgr.py`: remove `_JOB_TIMEOUT = 3630`, `CHILD_TIMEOUT`, `CHILD_GRACE_SECONDS` definitions and the duplication-admission comment at line 38. Import from `worker/constants.py`
    - Update `worker/job.py`: remove `JOB_TIMEOUT = 3630` and `REAP_GRACE = 120` definitions. Import from `worker/constants.py`
    - Update `worker/subprocess_mgr.py` import in `job.py`: verify `CHILD_TIMEOUT` import still resolves (now re-exported or imported from constants)
    - Write `test_no_duplicate_timeout_definitions`: grep-based test asserting no module outside `constants.py` defines `JOB_TIMEOUT`, `CHILD_TIMEOUT`, `CHILD_GRACE_SECONDS`, or `REAP_GRACE` as a module-level assignment
    - _Requirements:_ [RFC-038 D3](../rfcs/038-worker-timeout-unification.md#d3--constants-extraction) | [Design Property 3](../designs/design-rfc038-worker-timeout-unification.md#property-3-constant-single-source) | [Design Service: constants.py](../designs/design-rfc038-worker-timeout-unification.md#4-workerconstantspy)

  - [x] <a id="12-confidence-gate-alignment-d1"></a>1.2 Confidence gate alignment ([D1](../rfcs/038-worker-timeout-unification.md#d1--confidence-gate-alignment))

    - In `worker/subprocess_mgr.py`, add `and pdf_class.get("confidence", 0) >= INSPECTOR_CONFIDENCE_THRESHOLD` to the 16.5× multiplier condition at line 179. Import `INSPECTOR_CONFIDENCE_THRESHOLD` from `worker/constants.py`
    - In `client/indexer.py`, replace the hardcoded `0.90` at line 372 with `INSPECTOR_CONFIDENCE_THRESHOLD` imported from `worker/constants.py`
    - Write `test_timeout_multiplier_requires_confidence_threshold`:
      - Handshake with `pdf_type="scanned"`, `confidence=0.50` → effective_timeout == CHILD_TIMEOUT (no multiplier)
      - Handshake with `pdf_type="scanned"`, `confidence=0.90` → effective_timeout == CHILD_TIMEOUT * 16.5
      - Handshake with `pdf_type="scanned"`, `confidence=0.89` → effective_timeout == CHILD_TIMEOUT (no multiplier)
    - Write `test_indexer_uses_same_threshold`: assert `indexer.py`'s forced-OCR gate uses `INSPECTOR_CONFIDENCE_THRESHOLD` (import check)
    - **Validates:** [Design Property 1](../designs/design-rfc038-worker-timeout-unification.md#property-1-confidence-gate-consistency) | [RFC-038 Requirement 1](../rfcs/038-worker-timeout-unification.md#requirement-1-threshold-consistency) | [Design §Testing Strategy](../designs/design-rfc038-worker-timeout-unification.md#testing-strategy)
    - _Requirements:_ [RFC-038 D1](../rfcs/038-worker-timeout-unification.md#d1--confidence-gate-alignment) | [Design Property 1](../designs/design-rfc038-worker-timeout-unification.md#property-1-confidence-gate-consistency) | [Design Service: subprocess_mgr.py](../designs/design-rfc038-worker-timeout-unification.md#1-workersubprocess_mgrpy) | [Design Service: indexer.py](../designs/design-rfc038-worker-timeout-unification.md#3-clientindexerpy) | [Design Sequence: Timeout Computation Flow](../designs/design-rfc038-worker-timeout-unification.md#timeout-computation-flow--d1--d4)

  - [x] <a id="13-checkpoint--phase-1"></a>1.3 Checkpoint — Phase 1

    - Run `uv run pytest` and verify all existing tests pass with the refactored imports
    - Verify [Property 1](../designs/design-rfc038-worker-timeout-unification.md#property-1-confidence-gate-consistency) and [Property 3](../designs/design-rfc038-worker-timeout-unification.md#property-3-constant-single-source) tests pass
    - Verify no circular import errors: `uv run python -c "from pageindex_mcp.worker.constants import JOB_TIMEOUT; print(JOB_TIMEOUT)"`
    - Ask the user if questions arise before proceeding

- [x] <a id="2-phase-2--deadline-and-timeout-cap-d2-d4"></a>2. Phase 2 — Deadline and Timeout Cap (D2+D4)

  *[RFC-038 D2](../rfcs/038-worker-timeout-unification.md#d2--early-deadline-persistence) + [RFC-038 D4](../rfcs/038-worker-timeout-unification.md#d4--effective-timeout-cap)*

  - [x] <a id="21-early-deadline-persistence-d2"></a>2.1 Early deadline persistence ([D2](../rfcs/038-worker-timeout-unification.md#d2--early-deadline-persistence))

    - Refactor `_run_converter_subprocess` to surface `effective_timeout` to the caller immediately after the handshake parse:
      - Option A (preferred): Return `effective_timeout` as part of the result dict (already partially done via `_effective_timeout` key at line 234). Move the computation to before `proc.communicate()` and surface the timeout early by splitting the function into handshake-parse (returns timeout) + await-completion (returns result)
      - Option B: Use an asyncio callback or event to signal the computed timeout to `job.py` before the subprocess completes
    - In `job.py`, after receiving the `effective_timeout` from the handshake parse, immediately call `await redis.hset(job_key, "effective_timeout_at", str(processing_now + int(effective_timeout) + REAP_GRACE))`
    - Remove the post-completion `effective_timeout_at` update block at `job.py:252-262` (Zone 6 Part B) — subsumed by the early-persistence mechanism
    - Write `test_early_deadline_persisted_before_subprocess_completes`:
      - Mock converter child with a 2-second sleep after handshake
      - Assert that Redis `effective_timeout_at` is updated within 1 second of handshake emission (before subprocess completes)
      - Assert `reap_stale_jobs` does not false-reap during the gap
    - Write `test_handshake_parse_failure_preserves_conservative_deadline`:
      - Handshake is garbage bytes → `effective_timeout_at` remains at the conservative initial value
    - **Validates:** [Design Property 2](../designs/design-rfc038-worker-timeout-unification.md#property-2-early-deadline-persistence) | [RFC-038 Requirement 2](../rfcs/038-worker-timeout-unification.md#requirement-2-early-deadline-persistence) | [Design §Testing Strategy](../designs/design-rfc038-worker-timeout-unification.md#testing-strategy)
    - _Requirements:_ [RFC-038 D2](../rfcs/038-worker-timeout-unification.md#d2--early-deadline-persistence) | [Design Property 2](../designs/design-rfc038-worker-timeout-unification.md#property-2-early-deadline-persistence) | [Design Service: job.py](../designs/design-rfc038-worker-timeout-unification.md#2-workerjobpy) | [Design Service: subprocess_mgr.py](../designs/design-rfc038-worker-timeout-unification.md#1-workersubprocess_mgrpy) | [Design Sequence: Early Deadline Persistence Flow](../designs/design-rfc038-worker-timeout-unification.md#early-deadline-persistence-flow--d2)

  - [x] <a id="22-timeout-multiplication-cap-d4"></a>2.2 Timeout multiplication cap ([D4](../rfcs/038-worker-timeout-unification.md#d4--effective-timeout-cap))

    - In `subprocess_mgr.py`, after all timeout multipliers have been applied (after line 193), add:
      ```python
      if effective_timeout > MAX_EFFECTIVE_TIMEOUT:
          logger.warning(
              "Effective timeout %ss exceeds MAX_EFFECTIVE_TIMEOUT %ss; capping",
              effective_timeout, MAX_EFFECTIVE_TIMEOUT,
          )
          effective_timeout = MAX_EFFECTIVE_TIMEOUT
      ```
    - Import `MAX_EFFECTIVE_TIMEOUT` from `worker/constants.py` (already done in Task 1.1)
    - Write `test_effective_timeout_capped_at_max`:
      - Handshake with `is_docling_route=True`, `chunk_count=100`, `pdf_type="scanned"`, `confidence=0.95` → computed timeout would be huge, assert effective_timeout == MAX_EFFECTIVE_TIMEOUT
      - Assert warning log emitted with the uncapped value
    - Write `test_timeout_cap_configurable_via_env`:
      - Set `MAX_EFFECTIVE_TIMEOUT=7200` via env → assert cap applied at 7200
    - **Validates:** [Design Property 4](../designs/design-rfc038-worker-timeout-unification.md#property-4-timeout-cap-enforcement) | [RFC-038 Requirement 4](../rfcs/038-worker-timeout-unification.md#requirement-4-timeout-multiplication-cap) | [Design §Testing Strategy](../designs/design-rfc038-worker-timeout-unification.md#testing-strategy)
    - _Requirements:_ [RFC-038 D4](../rfcs/038-worker-timeout-unification.md#d4--effective-timeout-cap) | [Design Property 4](../designs/design-rfc038-worker-timeout-unification.md#property-4-timeout-cap-enforcement) | [Design Service: subprocess_mgr.py](../designs/design-rfc038-worker-timeout-unification.md#1-workersubprocess_mgrpy) | [Design Sequence: Timeout Computation Flow](../designs/design-rfc038-worker-timeout-unification.md#timeout-computation-flow--d1--d4)

  - [x] <a id="23-checkpoint--phase-2"></a>2.3 Checkpoint — Phase 2

    - Run `uv run pytest` and verify all existing + new tests pass
    - Verify [Property 2](../designs/design-rfc038-worker-timeout-unification.md#property-2-early-deadline-persistence) and [Property 4](../designs/design-rfc038-worker-timeout-unification.md#property-4-timeout-cap-enforcement) tests pass
    - Verify the `late_success` recovery path still works (regression check)
    - Ask the user if questions arise before proceeding

- [x] <a id="3-phase-3--integration-and-validation"></a>3. Phase 3 — Integration and Validation

  - [x] <a id="31-integration-tests"></a>3.1 Integration tests

    - Write `test_scanned_pdf_below_threshold_no_extended_timeout`: end-to-end with mocked child process emitting a handshake with `pdf_type="scanned"`, `confidence=0.50` → assert Redis `effective_timeout_at` uses conservative deadline, assert no 16.5× multiplier
    - Write `test_scanned_pdf_above_threshold_extended_timeout`: `confidence=0.92` → assert Redis `effective_timeout_at` reflects 16.5× deadline, assert deadline persisted before subprocess completes
    - Write `test_reaper_respects_early_persisted_deadline`: start a long-running job with extended deadline, run `reap_stale_jobs` at a time that would have false-reaped under the old code, assert job is NOT reaped
    - Write PBT: `test_property_timeout_always_bounded` (Hypothesis) — generate random handshake payloads with arbitrary chunk_count, pdf_type, and confidence values, assert effective_timeout <= MAX_EFFECTIVE_TIMEOUT
    - _Requirements:_ [RFC-038 D1](../rfcs/038-worker-timeout-unification.md#d1--confidence-gate-alignment) | [RFC-038 D2](../rfcs/038-worker-timeout-unification.md#d2--early-deadline-persistence) | [RFC-038 D4](../rfcs/038-worker-timeout-unification.md#d4--effective-timeout-cap) | All [Design Properties](../designs/design-rfc038-worker-timeout-unification.md#correctness-properties)

  - [x] <a id="32-final-checkpoint"></a>3.2 Final checkpoint

    - Run `uv run pytest` — full suite, zero failures
    - Verify all 4 correctness properties ([Property 1](../designs/design-rfc038-worker-timeout-unification.md#property-1-confidence-gate-consistency), [Property 2](../designs/design-rfc038-worker-timeout-unification.md#property-2-early-deadline-persistence), [Property 3](../designs/design-rfc038-worker-timeout-unification.md#property-3-constant-single-source), [Property 4](../designs/design-rfc038-worker-timeout-unification.md#property-4-timeout-cap-enforcement)) pass
    - Verify zero flaky test failures across 3 consecutive runs
    - Ask the user if questions arise before proceeding

## Notes

- [D1](../rfcs/038-worker-timeout-unification.md#d1--confidence-gate-alignment): The confidence threshold value (0.90) is unchanged — only its source location changes. This is a wiring fix, not a recalibration.
- [D2](../rfcs/038-worker-timeout-unification.md#d2--early-deadline-persistence): The `_run_converter_subprocess` return contract change is backward-compatible since the `_effective_timeout` key already exists in the result dict (Zone 6 Part B). The refactoring makes the timing of its availability explicit.
- [D3](../rfcs/038-worker-timeout-unification.md#d3--constants-extraction): `worker/constants.py` must have ZERO internal imports to prevent circular dependency chains. Only `os` (stdlib) is imported for the env var read.
- [D4](../rfcs/038-worker-timeout-unification.md#d4--effective-timeout-cap): The 54,000s default (15 hours) is deliberately generous. The cap exists to prevent absurd values (500,000s), not to tune processing time.
- The `late_success` ERROR→DONE recovery path ([RFC-038 §Consequences](../rfcs/038-worker-timeout-unification.md#consequences)) is retained as a safety net. After D2 lands, its frequency should drop to near-zero. A `REAPER_FALSE_POSITIVE` metric is recommended for monitoring but not required in this RFC.
- The three independent page-count computations (`fitz` vs `pypdfium2` in three locations) remain unchanged per [RFC-038 §Non-Goals](../rfcs/038-worker-timeout-unification.md#non-goals).

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "tasks": ["1.1"],
      "description": "Extract constants to shared module (no behavioral change)"
    },
    {
      "id": 1,
      "tasks": ["1.2"],
      "depends_on": ["1.1"],
      "description": "Confidence gate alignment (imports INSPECTOR_CONFIDENCE_THRESHOLD from constants)"
    },
    {
      "id": 2,
      "tasks": ["1.3"],
      "depends_on": ["1.1", "1.2"],
      "description": "Phase 1 checkpoint"
    },
    {
      "id": 3,
      "tasks": ["2.1", "2.2"],
      "depends_on": ["1.3"],
      "description": "Early deadline persistence + timeout cap (can be done in parallel)"
    },
    {
      "id": 4,
      "tasks": ["2.3"],
      "depends_on": ["2.1", "2.2"],
      "description": "Phase 2 checkpoint"
    },
    {
      "id": 5,
      "tasks": ["3.1"],
      "depends_on": ["2.3"],
      "description": "Integration tests"
    },
    {
      "id": 6,
      "tasks": ["3.2"],
      "depends_on": ["3.1"],
      "description": "Final checkpoint"
    }
  ]
}
```
