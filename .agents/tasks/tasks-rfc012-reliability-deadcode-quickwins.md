<!-- Space: CITRA -->
<!-- Title: Tasks: RFC-012 Reliability & Dead-Code Quick-Wins -->
<!-- Folder: Tasks -->

# Implementation Plan: RFC-012 Reliability & Dead-Code Quick-Win Batch

## Traceability

| Artifact                      | Reference                                                                                                          |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Governing RFC(s)              | [RFC-012: Reliability & Dead-Code Quick-Win Batch](../rfcs/012-reliability-deadcode-quickwins.md)                   |
| Design Document               | [Design: RFC-012 Reliability & Dead-Code Quick-Wins](../designs/design-rfc012-reliability-deadcode-quickwins.md)    |
| PRD / Requirements            | `PRD.md`                                                                                                           |
| Hard Rules                    | [CLAUDE.md constraints](../rfcs/012-reliability-deadcode-quickwins.md#hard-rule-constraints-claudemd--binding)      |
| RFC Implementation Order      | [RFC-012 Implementation Plan](../rfcs/012-reliability-deadcode-quickwins.md#implementation-plan)                    |
| RFC Test Strategy             | [RFC-012 Test Strategy](../rfcs/012-reliability-deadcode-quickwins.md#test-strategy)                               |
| Design Correctness Properties | [Design Correctness Properties](../designs/design-rfc012-reliability-deadcode-quickwins.md#correctness-properties)  |
| Design Testing Strategy       | [Design Testing Strategy](../designs/design-rfc012-reliability-deadcode-quickwins.md#testing-strategy)              |

## Overview

Implements 7 reliability and dead-code quick-wins identified by `audit/DOCSTORE_AUDIT_REPORT.md`, organized into three risk-ordered batches plus a test suite per [RFC-012 Implementation Plan](../rfcs/012-reliability-deadcode-quickwins.md#implementation-plan). [Batch 0](../rfcs/012-reliability-deadcode-quickwins.md#implementation-plan) handles zero-risk changes: closing the already-fixed [ISS-03](../rfcs/012-reliability-deadcode-quickwins.md#d1--iss-03-no-code-change-close-as-resolved), adding a test URL env override ([D6](../rfcs/012-reliability-deadcode-quickwins.md#d6--iss-43-env-var-override-for-testpys-target-url)), and deleting confirmed-dead files ([D5](../rfcs/012-reliability-deadcode-quickwins.md#d5--iss-42--iss-45-delete-confirmed-dead-files)). [Batch 1](../rfcs/012-reliability-deadcode-quickwins.md#implementation-plan) addresses reliability: routing worker Redis through the shared singleton ([D2](../rfcs/012-reliability-deadcode-quickwins.md#d2--iss-07-route-worker-redis-access-through-the-shared-singleton)) and tuning gunicorn lifecycle ([D4](../rfcs/012-reliability-deadcode-quickwins.md#d4--iss-39-raise-gunicorn-graceful_timeout-add-request-jitter)). [Batch 2](../rfcs/012-reliability-deadcode-quickwins.md#implementation-plan) fixes concurrency: admission lock scope ([D3](../rfcs/012-reliability-deadcode-quickwins.md#d3--iss-37-hold-the-lock-through-the-full-check-then-admit-window)) and batched registry backfill upserts ([D7](../rfcs/012-reliability-deadcode-quickwins.md#d7--iss-46-batch-registry-backfill-upserts-with-bounded-concurrency)). Each batch is validated by property-based tests tied to the design document's [6 correctness properties](../designs/design-rfc012-reliability-deadcode-quickwins.md#correctness-properties). Stack: Python 3.12, FastMCP, arq, Redis, gunicorn + uvicorn.

## Tasks

- [x] <a id="1-batch-0--zero-risk-fixes-d1-d6-d5"></a>1. Batch 0 — Zero-Risk Fixes ([D1](../rfcs/012-reliability-deadcode-quickwins.md#d1--iss-03-no-code-change-close-as-resolved), [D6](../rfcs/012-reliability-deadcode-quickwins.md#d6--iss-43-env-var-override-for-testpys-target-url), [D5](../rfcs/012-reliability-deadcode-quickwins.md#d5--iss-42--iss-45-delete-confirmed-dead-files))

  *[RFC-012 Batch 0](../rfcs/012-reliability-deadcode-quickwins.md#implementation-plan): "Zero-risk fixes — close ISS-03, add test URL env override, delete dead files"*

  - [x] <a id="11-close-iss-03-as-resolved"></a>1.1 Close ISS-03 as resolved ([D1](../rfcs/012-reliability-deadcode-quickwins.md#d1--iss-03-no-code-change-close-as-resolved))

    - Mark ISS-03 as closed/resolved in the audit tracker (`audit/DOCSTORE_AUDIT_REPORT.md`)
    - No code change required: `registry_backfill.py:188-193` already guards `set_registry_complete` behind a non-empty `meta_keys` check
    - Add a note citing the existing guard as the fix
    - _Requirements:_ [RFC-012 D1](../rfcs/012-reliability-deadcode-quickwins.md#d1--iss-03-no-code-change-close-as-resolved) | [Design AD1](../designs/design-rfc012-reliability-deadcode-quickwins.md#ad1--close-iss-03-as-resolved)

  - [x] <a id="12-add-test-url-env-override"></a>1.2 Add test URL env override ([D6](../rfcs/012-reliability-deadcode-quickwins.md#d6--iss-43-env-var-override-for-testpys-target-url))

    - Replace the hardcoded production URL at `test.py:21` with an env-var override:
      ```python
      url = os.environ.get("TEST_MCP_URL", "http://localhost:8201/mcp")
      ```
    - Default to localhost, not production — prevents accidental test runs against the production server
    - Add `import os` if not already present
    - _Requirements:_ [RFC-012 D6](../rfcs/012-reliability-deadcode-quickwins.md#d6--iss-43-env-var-override-for-testpys-target-url) | [Design AD6](../designs/design-rfc012-reliability-deadcode-quickwins.md#ad6--test-url-env-override) | [Design Service: test.py](../designs/design-rfc012-reliability-deadcode-quickwins.md#5-testpy) | [Design Property 5](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-5-test-url-safety)

  - [x] <a id="13-delete-dead-files"></a>1.3 Delete dead files ([D5](../rfcs/012-reliability-deadcode-quickwins.md#d5--iss-42--iss-45-delete-confirmed-dead-files))

    - Delete `upload.py` (repo root) — calls non-existent MCP tool `process_document`; 0 references repo-wide; `ingest_via_server.py` is the active replacement
    - Delete `tools/processing.py` — 1-line tombstone comment; 0 references repo-wide
    - Both confirmed dead via codebase-graph reference search (per [RFC-012 D5](../rfcs/012-reliability-deadcode-quickwins.md#d5--iss-42--iss-45-delete-confirmed-dead-files))
    - Recoverable via git history if needed
    - **Post-hoc correction (2026-07-16):** commit ff473e5 deleted `upload.py` but left `src/pageindex_mcp/tools/processing.py` in place despite the checkbox already being marked done; caught during a re-verification pass, confirmed 0 references, and deleted
    - _Requirements:_ [RFC-012 D5](../rfcs/012-reliability-deadcode-quickwins.md#d5--iss-42--iss-45-delete-confirmed-dead-files) | [Design AD5](../designs/design-rfc012-reliability-deadcode-quickwins.md#ad5--dead-code-removal) | [Design Property 4](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-4-dead-code-absence)

  - [x] <a id="14-checkpoint--batch-0"></a>1.4 Checkpoint — Batch 0

    - Run `uv run pytest` — all existing tests continue green
    - Verify ISS-03 marked as resolved in audit tracker
    - Verify `upload.py` and `tools/processing.py` deleted; no remaining references
    - Verify `test.py` defaults to `http://localhost:8201/mcp`, not production
    - Verify [Design Property 4](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-4-dead-code-absence) (dead code absence) and [Design Property 5](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-5-test-url-safety) (test URL safety)
    - Ask user if questions arise before proceeding

- [x] <a id="2-batch-1--reliability-fixes-d2-d4"></a>2. Batch 1 — Reliability Fixes ([D2](../rfcs/012-reliability-deadcode-quickwins.md#d2--iss-07-route-worker-redis-access-through-the-shared-singleton), [D4](../rfcs/012-reliability-deadcode-quickwins.md#d4--iss-39-raise-gunicorn-graceful_timeout-add-request-jitter))

  *[RFC-012 Batch 1](../rfcs/012-reliability-deadcode-quickwins.md#implementation-plan): "Reliability fixes — Redis singleton reuse and gunicorn lifecycle tuning"*

  - [x] <a id="21-route-worker-redis-through-singleton"></a>2.1 Route worker Redis through singleton ([D2](../rfcs/012-reliability-deadcode-quickwins.md#d2--iss-07-route-worker-redis-access-through-the-shared-singleton))

    - Replace ad-hoc `aioredis.from_url` fallback at `worker.py:275` and `worker.py:446` with the shared `cache.py` singleton:
      ```python
      from .cache import get_async_redis
      redis: aioredis.Redis = ctx.get("redis") or await get_async_redis()
      ```
    - `ctx["redis"]` is always set at worker startup (`worker.py:509`), so this is dead-path insurance — but eliminates the last ad-hoc connection site in the codebase
    - Ensures consistency with `helpers.py:389` which already uses the `cache.py:39` singleton
    - _Requirements:_ [RFC-012 D2](../rfcs/012-reliability-deadcode-quickwins.md#d2--iss-07-route-worker-redis-access-through-the-shared-singleton) | [Design AD2](../designs/design-rfc012-reliability-deadcode-quickwins.md#ad2--redis-singleton-reuse) | [Design Service: worker.py](../designs/design-rfc012-reliability-deadcode-quickwins.md#1-workerpy) | [Design Service: cache.py](../designs/design-rfc012-reliability-deadcode-quickwins.md#6-cachepy) | [Design Property 1](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-1-redis-singleton-consistency) | [Design Sequence: Worker Startup Flow](../designs/design-rfc012-reliability-deadcode-quickwins.md#worker-startup-flow--d2)

  - [x] <a id="22-tune-gunicorn-lifecycle"></a>2.2 Tune gunicorn lifecycle ([D4](../rfcs/012-reliability-deadcode-quickwins.md#d4--iss-39-raise-gunicorn-graceful_timeout-add-request-jitter))

    - In `gunicorn.conf.py`:
      - Raise `graceful_timeout` from `5` to `30` (at line 13) — allows in-flight ingestion requests to complete during worker restart/deploy
      - Add `max_requests = 100` — periodic worker recycling to prevent memory leaks
      - Add `max_requests_jitter = 10` — desynchronizes worker recycling under load
    - Note in deploy runbook: longer graceful shutdown window changes deploy-time behavior
    - _Requirements:_ [RFC-012 D4](../rfcs/012-reliability-deadcode-quickwins.md#d4--iss-39-raise-gunicorn-graceful_timeout-add-request-jitter) | [Design AD4](../designs/design-rfc012-reliability-deadcode-quickwins.md#ad4--gunicorn-lifecycle-tuning) | [Design Service: gunicorn.conf.py](../designs/design-rfc012-reliability-deadcode-quickwins.md#3-gunicornconfpy) | [Design Property 3](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-3-graceful-shutdown-completeness)

  - [x] <a id="23-checkpoint--batch-1"></a>2.3 Checkpoint — Batch 1

    - Run `uv run pytest` — all tests pass including [Batch 0](#1-batch-0--zero-risk-fixes-d1-d6-d5) + Batch 1
    - Verify [Design Property 1](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-1-redis-singleton-consistency) (Redis singleton consistency): no `aioredis.from_url` calls remain outside `cache.py`
    - Verify [Design Property 3](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-3-graceful-shutdown-completeness) (graceful shutdown completeness): `gunicorn.conf.py` has `graceful_timeout >= 30`, `max_requests`, and `max_requests_jitter`
    - Ask user if questions arise before proceeding

- [x] <a id="3-batch-2--concurrency-fixes-d3-d7"></a>3. Batch 2 — Concurrency Fixes ([D3](../rfcs/012-reliability-deadcode-quickwins.md#d3--iss-37-hold-the-lock-through-the-full-check-then-admit-window), [D7](../rfcs/012-reliability-deadcode-quickwins.md#d7--iss-46-batch-registry-backfill-upserts-with-bounded-concurrency))

  *[RFC-012 Batch 2](../rfcs/012-reliability-deadcode-quickwins.md#implementation-plan): "Concurrency fixes — admission lock scope and batched registry backfill upserts"*

  - [x] <a id="31-fix-admission-lock-scope"></a>3.1 Fix admission lock scope ([D3](../rfcs/012-reliability-deadcode-quickwins.md#d3--iss-37-hold-the-lock-through-the-full-check-then-admit-window))

    - In `memory_admission.py:72-99`:
      - Currently acquires and releases the lock per loop iteration, leaving a window between check and admit where a concurrent request can slip through
      - Wrap the full decision window (check through admit) in one `asyncio.Lock()` acquisition instead of per-iteration acquire/release
      - The lock must be held continuously from the quota check through the admission decision
    - _Requirements:_ [RFC-012 D3](../rfcs/012-reliability-deadcode-quickwins.md#d3--iss-37-hold-the-lock-through-the-full-check-then-admit-window) | [Design AD3](../designs/design-rfc012-reliability-deadcode-quickwins.md#ad3--admission-lock-scope) | [Design Service: memory_admission.py](../designs/design-rfc012-reliability-deadcode-quickwins.md#2-memory_admissionpy) | [Design Property 2](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-2-admission-lock-atomicity) | [Design Sequence: Memory Admission Flow](../designs/design-rfc012-reliability-deadcode-quickwins.md#memory-admission-flow--d3)

  - [x] <a id="32-batch-registry-backfill-upserts"></a>3.2 Batch registry backfill upserts ([D7](../rfcs/012-reliability-deadcode-quickwins.md#d7--iss-46-batch-registry-backfill-upserts-with-bounded-concurrency))

    - In `registry_backfill.py:129-157`:
      - Replace the sequential `for` loop over per-doc upserts with bounded-concurrency batching:
        ```python
        sem = asyncio.Semaphore(10)
        async def _upsert(meta):
            async with sem:
                return await upsert_doc(meta)
        results = await asyncio.gather(*(_upsert(m) for m in batch), return_exceptions=True)
        ```
      - Handle per-item failures from `return_exceptions=True` — log and continue, do not abort the batch
      - Semaphore bound of 10 prevents overwhelming Redis under large backfills
    - _Requirements:_ [RFC-012 D7](../rfcs/012-reliability-deadcode-quickwins.md#d7--iss-46-batch-registry-backfill-upserts-with-bounded-concurrency) | [Design AD7](../designs/design-rfc012-reliability-deadcode-quickwins.md#ad7--batched-registry-upserts) | [Design Service: registry_backfill.py](../designs/design-rfc012-reliability-deadcode-quickwins.md#4-registry_backfillpy) | [Design Property 6](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-6-backfill-concurrency-correctness) | [Design Sequence: Registry Backfill Flow](../designs/design-rfc012-reliability-deadcode-quickwins.md#registry-backfill-flow--d7)

  - [x] <a id="33-checkpoint--batch-2"></a>3.3 Checkpoint — Batch 2

    - Run `uv run pytest` — all tests pass including [Batch 0](#1-batch-0--zero-risk-fixes-d1-d6-d5) + [Batch 1](#2-batch-1--reliability-fixes-d2-d4) + Batch 2
    - Verify [Design Property 2](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-2-admission-lock-atomicity) (admission lock atomicity): lock held continuously through check-then-admit
    - Verify [Design Property 6](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-6-backfill-concurrency-correctness) (backfill concurrency correctness): `asyncio.gather` with semaphore replaces sequential loop
    - Ask user if questions arise before proceeding

- [x] <a id="4-test-suite"></a>4. Test Suite

  *[RFC-012 Test Strategy](../rfcs/012-reliability-deadcode-quickwins.md#test-strategy): "D2 mock/spy on get_async_redis, D3 concurrency test with near-full quota, D7 gather with simulated per-item failure"*

  - [x] <a id="41-redis-singleton-spy-test"></a>4.1 Redis singleton spy test ([D2](../rfcs/012-reliability-deadcode-quickwins.md#d2--iss-07-route-worker-redis-access-through-the-shared-singleton))

    - **[Property 1](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-1-redis-singleton-consistency) — Redis singleton consistency**:
      - Test: `test_worker_redis_fallback_uses_singleton` — mock `ctx.get("redis")` to return `None`, assert the fallback calls `get_async_redis()` from `cache.py` instead of `aioredis.from_url`
      - Test: `test_no_direct_aioredis_from_url_in_worker` — static assertion: grep/AST scan `worker.py` for `aioredis.from_url` calls, assert zero matches
    - **Validates:** [Design Property 1](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-1-redis-singleton-consistency) | [RFC-012 D2](../rfcs/012-reliability-deadcode-quickwins.md#d2--iss-07-route-worker-redis-access-through-the-shared-singleton) | [RFC Test Strategy](../rfcs/012-reliability-deadcode-quickwins.md#test-strategy)

  - [x] <a id="42-admission-lock-concurrency-test"></a>4.2 Admission lock concurrency test ([D3](../rfcs/012-reliability-deadcode-quickwins.md#d3--iss-37-hold-the-lock-through-the-full-check-then-admit-window))

    - **[Property 2](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-2-admission-lock-atomicity) — Admission lock atomicity**:
      - Test: `test_concurrent_admission_only_one_admits` — two simultaneous admission checks against a near-full quota (capacity for exactly 1 more), assert only one succeeds
      - Test: `test_admission_lock_held_through_decision` — instrument the lock to verify it is held continuously from check through admit (not released and reacquired per iteration)
    - **Validates:** [Design Property 2](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-2-admission-lock-atomicity) | [RFC-012 D3](../rfcs/012-reliability-deadcode-quickwins.md#d3--iss-37-hold-the-lock-through-the-full-check-then-admit-window) | [RFC Test Strategy](../rfcs/012-reliability-deadcode-quickwins.md#test-strategy)

  - [x] <a id="43-backfill-gather-test"></a>4.3 Backfill gather test ([D7](../rfcs/012-reliability-deadcode-quickwins.md#d7--iss-46-batch-registry-backfill-upserts-with-bounded-concurrency))

    - **[Property 6](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-6-backfill-concurrency-correctness) — Backfill concurrency correctness**:
      - Test: `test_backfill_upsert_called_per_item` — assert `upsert_doc` is called once per item in a batch of N documents
      - Test: `test_backfill_gather_handles_per_item_failure` — simulate one `upsert_doc` raising an exception in a batch; assert remaining items still succeed and the exception is logged (not propagated)
      - Test: `test_backfill_semaphore_bounds_concurrency` — assert no more than 10 concurrent `upsert_doc` calls execute simultaneously
    - **Validates:** [Design Property 6](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-6-backfill-concurrency-correctness) | [RFC-012 D7](../rfcs/012-reliability-deadcode-quickwins.md#d7--iss-46-batch-registry-backfill-upserts-with-bounded-concurrency) | [RFC Test Strategy](../rfcs/012-reliability-deadcode-quickwins.md#test-strategy)

  - [x] <a id="44-final-checkpoint"></a>4.4 Final Checkpoint

    - Run `uv run pytest` — full test suite passes including all new tests from [Task 4.1](#41-redis-singleton-spy-test), [Task 4.2](#42-admission-lock-concurrency-test), [Task 4.3](#43-backfill-gather-test)
    - Verify all [6 correctness properties](../designs/design-rfc012-reliability-deadcode-quickwins.md#correctness-properties) green:
      - [P1](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-1-redis-singleton-consistency): Redis singleton consistency ([D2](../rfcs/012-reliability-deadcode-quickwins.md#d2--iss-07-route-worker-redis-access-through-the-shared-singleton))
      - [P2](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-2-admission-lock-atomicity): Admission lock atomicity ([D3](../rfcs/012-reliability-deadcode-quickwins.md#d3--iss-37-hold-the-lock-through-the-full-check-then-admit-window))
      - [P3](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-3-graceful-shutdown-completeness): Graceful shutdown completeness ([D4](../rfcs/012-reliability-deadcode-quickwins.md#d4--iss-39-raise-gunicorn-graceful_timeout-add-request-jitter))
      - [P4](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-4-dead-code-absence): Dead code absence ([D5](../rfcs/012-reliability-deadcode-quickwins.md#d5--iss-42--iss-45-delete-confirmed-dead-files))
      - [P5](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-5-test-url-safety): Test URL safety ([D6](../rfcs/012-reliability-deadcode-quickwins.md#d6--iss-43-env-var-override-for-testpys-target-url))
      - [P6](../designs/design-rfc012-reliability-deadcode-quickwins.md#property-6-backfill-concurrency-correctness): Backfill concurrency correctness ([D7](../rfcs/012-reliability-deadcode-quickwins.md#d7--iss-46-batch-registry-backfill-upserts-with-bounded-concurrency))
    - Verify [D4](../rfcs/012-reliability-deadcode-quickwins.md#d4--iss-39-raise-gunicorn-graceful_timeout-add-request-jitter), [D5](../rfcs/012-reliability-deadcode-quickwins.md#d5--iss-42--iss-45-delete-confirmed-dead-files), [D6](../rfcs/012-reliability-deadcode-quickwins.md#d6--iss-43-env-var-override-for-testpys-target-url) validated by existing test suite continuing green (no new tests needed per [RFC Test Strategy](../rfcs/012-reliability-deadcode-quickwins.md#test-strategy))
    - Ask user for review before committing

## Notes

- [D1](../rfcs/012-reliability-deadcode-quickwins.md#d1--iss-03-no-code-change-close-as-resolved) (ISS-03) — already fixed post-audit; `registry_backfill.py:188-193` guards `set_registry_complete` behind non-empty `meta_keys` check; close in audit tracker only
- [D2](../rfcs/012-reliability-deadcode-quickwins.md#d2--iss-07-route-worker-redis-access-through-the-shared-singleton) (ISS-07) — replace ad-hoc `aioredis.from_url` fallback at `worker.py:275` and `:446` with `cache.py` singleton; ~4 lines per site
- [D3](../rfcs/012-reliability-deadcode-quickwins.md#d3--iss-37-hold-the-lock-through-the-full-check-then-admit-window) (ISS-37) — hold `asyncio.Lock()` through full check-then-admit window in `memory_admission.py:72-99`; ~10 lines
- [D4](../rfcs/012-reliability-deadcode-quickwins.md#d4--iss-39-raise-gunicorn-graceful_timeout-add-request-jitter) (ISS-39) — raise `graceful_timeout` from 5 to 30+, add `max_requests=100` / `max_requests_jitter=10` in `gunicorn.conf.py`; config-only
- [D5](../rfcs/012-reliability-deadcode-quickwins.md#d5--iss-42--iss-45-delete-confirmed-dead-files) (ISS-42/45) — delete `upload.py` and `tools/processing.py`; confirmed 0 references via codebase-graph search
- [D6](../rfcs/012-reliability-deadcode-quickwins.md#d6--iss-43-env-var-override-for-testpys-target-url) (ISS-43) — env-var override for `test.py:21` target URL; default to localhost, not production; ~2 lines
- [D7](../rfcs/012-reliability-deadcode-quickwins.md#d7--iss-46-batch-registry-backfill-upserts-with-bounded-concurrency) (ISS-46) — bounded-concurrency `asyncio.gather` replacing sequential loop in `registry_backfill.py:129-157`; semaphore bound of 10; ~10 lines
- [Risk 1](../rfcs/012-reliability-deadcode-quickwins.md#risks) — [D5](../rfcs/012-reliability-deadcode-quickwins.md#d5--iss-42--iss-45-delete-confirmed-dead-files) deletions are irreversible in the working tree but recoverable via git history; confirmed zero references before removal
- [Risk 2](../rfcs/012-reliability-deadcode-quickwins.md#risks) — [D4](../rfcs/012-reliability-deadcode-quickwins.md#d4--iss-39-raise-gunicorn-graceful_timeout-add-request-jitter) gunicorn timeout increase changes deploy-time behavior (longer graceful shutdown window); note in deploy runbook
- All 7 fixes are independent with no shared code surface — no ordering dependency between decisions, batching is purely by risk level
- No compliance surface: none of these fixes touch PII routing, erasure, or tree-quality gating (per [CLAUDE.md constraints](../rfcs/012-reliability-deadcode-quickwins.md#hard-rule-constraints-claudemd--binding))

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "name": "Batch 0 — Zero-risk fixes",
      "tasks": ["1.1", "1.2", "1.3"],
      "depends_on": [],
      "notes": "D1 (close ISS-03), D6 (test URL override), D5 (delete dead files) — no code dependencies, all independent"
    },
    {
      "id": 1,
      "name": "Batch 0 — Checkpoint",
      "tasks": ["1.4"],
      "depends_on": ["1.1", "1.2", "1.3"],
      "notes": "Verify Properties 4 (dead code absence) and 5 (test URL safety)"
    },
    {
      "id": 2,
      "name": "Batch 1 — Reliability fixes (parallel)",
      "tasks": ["2.1", "2.2"],
      "depends_on": [],
      "notes": "D2 (Redis singleton) and D4 (gunicorn lifecycle) are independent — different files, no cross-dependencies"
    },
    {
      "id": 3,
      "name": "Batch 1 — Checkpoint",
      "tasks": ["2.3"],
      "depends_on": ["2.1", "2.2"],
      "notes": "Verify Properties 1 (Redis singleton consistency) and 3 (graceful shutdown completeness)"
    },
    {
      "id": 4,
      "name": "Batch 2 — Concurrency fixes (parallel)",
      "tasks": ["3.1", "3.2"],
      "depends_on": [],
      "notes": "D3 (admission lock) and D7 (batched upserts) are independent — different files, no cross-dependencies"
    },
    {
      "id": 5,
      "name": "Batch 2 — Checkpoint",
      "tasks": ["3.3"],
      "depends_on": ["3.1", "3.2"],
      "notes": "Verify Properties 2 (admission lock atomicity) and 6 (backfill concurrency correctness)"
    },
    {
      "id": 6,
      "name": "Test suite",
      "tasks": ["4.1", "4.2", "4.3"],
      "depends_on": ["2.1", "3.1", "3.2"],
      "notes": "4.1 depends on 2.1 (D2), 4.2 depends on 3.1 (D3), 4.3 depends on 3.2 (D7)"
    },
    {
      "id": 7,
      "name": "Final checkpoint",
      "tasks": ["4.4"],
      "depends_on": ["1.4", "2.3", "3.3", "4.1", "4.2", "4.3"],
      "notes": "Verify all 6 correctness properties green — depends on all prior tasks"
    }
  ]
}
```
