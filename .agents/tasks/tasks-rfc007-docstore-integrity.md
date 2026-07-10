<!-- Space: CITRA -->
<!-- Title: Implementation Plan: Docstore Data-Integrity & Compliance Hardening -->
<!-- Parent: Tasks -->
<!-- Confluence-Page-ID: 5093654529 -->
<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5093654529/Implementation+Plan+Docstore+Data-Integrity+Compliance+Hardening -->

# Implementation Plan: Docstore Data-Integrity & Compliance Hardening

## Traceability

| Artifact | Reference |
|---|---|
| Governing RFC(s) | [RFC-007: Docstore Data-Integrity & Compliance Hardening](../rfcs/007-docstore-data-integrity-hardening.md) |
| Design Document | [Design: Docstore Data-Integrity & Compliance Hardening](../designs/design-rfc007-docstore-integrity.md) |
| PRD / Requirements | `PRD.md` |
| Hard Rules | [CLAUDE.md HR2 + HR5](../rfcs/007-docstore-data-integrity-hardening.md#hard-rule-constraints-claudemd--binding) |
| RFC Implementation Order | [RFC-007 Implementation Plan](../rfcs/007-docstore-data-integrity-hardening.md#implementation-plan) |
| RFC Test Strategy | [RFC-007 Test Strategy](../rfcs/007-docstore-data-integrity-hardening.md#test-strategy) |
| Design Correctness Properties | [Design Correctness Properties](../designs/design-rfc007-docstore-integrity.md#correctness-properties) |
| Design Testing Strategy | [Design Testing Strategy](../designs/design-rfc007-docstore-integrity.md#testing-strategy) |

## Overview

Implements nine verified data-integrity fixes across the PageIndex docstore write-path, organized into four risk-ordered batches per [RFC-007 Implementation Plan](../rfcs/007-docstore-data-integrity-hardening.md#implementation-plan). The plan proceeds from zero-risk operation reordering and default fixes ([Batch 0](../rfcs/007-docstore-data-integrity-hardening.md#batch-0--immediate-zero-risk-no-behavioral-change-to-success-paths)) through storage migration ([Batch 3](../rfcs/007-docstore-data-integrity-hardening.md#batch-3--storage-migration)), validating each batch with unit tests tied to the design document's [9 correctness properties](../designs/design-rfc007-docstore-integrity.md#correctness-properties) before advancing. Stack: Python 3.12, Redis, MinIO, Postgres (RFC-006), arq, Prometheus.

## Tasks

- [ ] <a id="1-batch-0--immediate-zero-risk-fixes-d1-d4-d8-d7"></a>1. Batch 0 — Immediate, zero-risk fixes ([D1](../rfcs/007-docstore-data-integrity-hardening.md#d1--fix-default-redis_url-to-localhost-iss-01), [D4](../rfcs/007-docstore-data-integrity-hardening.md#d4--validate-then-stage-for-multi-file-uploads-iss-04), [D8](../rfcs/007-docstore-data-integrity-hardening.md#d8--reorder-enqueue_job-before-status-set-to-eliminate-phantom-jobs-iss-12), [D7](../rfcs/007-docstore-data-integrity-hardening.md#d7--reorder-save_raw-after-save_doc-to-prevent-orphans-iss-11))

  *[RFC-007 Batch 0](../rfcs/007-docstore-data-integrity-hardening.md#batch-0--immediate-zero-risk-no-behavioral-change-to-success-paths): "pure operation-reordering and default-value fixes"*

  - [ ] <a id="11-fix-redis-url-default-d1"></a>1.1 Fix Redis URL default ([D1](../rfcs/007-docstore-data-integrity-hardening.md#d1--fix-default-redis_url-to-localhost-iss-01))

    - Change `redis_url` default from `"redis://neonatal-care-redis.neonatal-care:6379/1"` to `"redis://localhost:6379/0"` in `config.py:81`
    - _Requirements:_ [RFC-007 D1 (ISS-01)](../rfcs/007-docstore-data-integrity-hardening.md#d1--fix-default-redis_url-to-localhost-iss-01) | [Design Property 9](../designs/design-rfc007-docstore-integrity.md#property-9-correct-redis-default) | [Design Service: Config](../designs/design-rfc007-docstore-integrity.md#1-config-configpy)

  - [ ] <a id="12-validate-then-stage-for-multi-file-uploads-d4"></a>1.2 Validate-then-stage for multi-file uploads ([D4](../rfcs/007-docstore-data-integrity-hardening.md#d4--validate-then-stage-for-multi-file-uploads-iss-04))

    - Split upload handler into two passes in `upload_app.py`:
      - Pass 1: Validate all file extensions and size constraints
      - Pass 2: Stage to MinIO, enqueue arq jobs, set Redis status — only if validation passes
    - On any validation failure, return HTTP 400 with zero MinIO writes, zero Redis mutations, zero arq enqueues
    - _Requirements:_ [RFC-007 D4 (ISS-04)](../rfcs/007-docstore-data-integrity-hardening.md#d4--validate-then-stage-for-multi-file-uploads-iss-04) | [Design Property 2](../designs/design-rfc007-docstore-integrity.md#property-2-all-or-nothing-upload-validation) | [Design Service: Upload Handler](../designs/design-rfc007-docstore-integrity.md#2-upload-handler-upload_apppy) | [Design Sequence: Upload Flow](../designs/design-rfc007-docstore-integrity.md#upload-flow--d4--d8)

  - [ ] <a id="13-reorder-enqueue-before-status-d8"></a>1.3 Reorder enqueue before status ([D8](../rfcs/007-docstore-data-integrity-hardening.md#d8--reorder-enqueue_job-before-status-set-to-eliminate-phantom-jobs-iss-12))

    - In `upload_app.py:98-108`, swap order: call `enqueue_job()` first, then `redis.hset(status=pending)` only on success
    - If `enqueue_job` raises, no phantom pending status entry is created
    - _Requirements:_ [RFC-007 D8 (ISS-12)](../rfcs/007-docstore-data-integrity-hardening.md#d8--reorder-enqueue_job-before-status-set-to-eliminate-phantom-jobs-iss-12) | [Design Property 1](../designs/design-rfc007-docstore-integrity.md#property-1-no-phantom-pending-jobs) | [Design Service: Upload Handler](../designs/design-rfc007-docstore-integrity.md#2-upload-handler-upload_apppy) | [Design Data Model: Job Status](../designs/design-rfc007-docstore-integrity.md#job-status--d8-reorder) | [Design Sequence: Upload Flow](../designs/design-rfc007-docstore-integrity.md#upload-flow--d4--d8)

  - [ ] <a id="14-reorder-save_doc-before-save_raw-d7"></a>1.4 Reorder save_doc before save_raw ([D7](../rfcs/007-docstore-data-integrity-hardening.md#d7--reorder-save_raw-after-save_doc-to-prevent-orphans-iss-11))

    - In `client.py:590-620`, move `save_raw` to execute after `save_doc` (or `save_flat_doc`)
    - If tree validation or `save_doc` fails, `save_raw` is never called — no orphaned uploads
    - Add `RAW_UPLOAD_FAILURES` Prometheus counter in `metrics.py` for `save_raw` failures after successful `save_doc`
    - _Requirements:_ [RFC-007 D7 (ISS-11)](../rfcs/007-docstore-data-integrity-hardening.md#d7--reorder-save_raw-after-save_doc-to-prevent-orphans-iss-11) | [Design Property 3](../designs/design-rfc007-docstore-integrity.md#property-3-no-orphaned-raw-uploads) | [Design Service: Worker/Client](../designs/design-rfc007-docstore-integrity.md#3-worker--client-clientpy) | [Design Service: Metrics](../designs/design-rfc007-docstore-integrity.md#6-metrics-metricspy) | [Design Sequence: Processing Flow](../designs/design-rfc007-docstore-integrity.md#processing-flow--d5--d7) | [CLAUDE.md HR5](../rfcs/007-docstore-data-integrity-hardening.md#hard-rule-constraints-claudemd--binding)

  - [ ] <a id="15-write-unit-tests-for-batch-0"></a>1.5 Write unit tests for Batch 0

    - **[Property 9](../designs/design-rfc007-docstore-integrity.md#property-9-correct-redis-default) — Correct Redis default**: Verify config defaults to `redis://localhost:6379/0` without `REDIS_URL` env var
      - Test: `test_config_redis_default`
      - **Validates:** [Design Property 9](../designs/design-rfc007-docstore-integrity.md#property-9-correct-redis-default) | [RFC-007 D1](../rfcs/007-docstore-data-integrity-hardening.md#d1--fix-default-redis_url-to-localhost-iss-01) | [RFC Test Strategy: D1 row](../rfcs/007-docstore-data-integrity-hardening.md#per-fix-unit-tests)
    - **[Property 2](../designs/design-rfc007-docstore-integrity.md#property-2-all-or-nothing-upload-validation) — All-or-nothing upload**: Upload 3 files (2 valid, 1 invalid ext); verify zero MinIO writes, zero arq enqueues
      - Test: `test_upload_mixed_invalid_no_staging`
      - **Validates:** [Design Property 2](../designs/design-rfc007-docstore-integrity.md#property-2-all-or-nothing-upload-validation) | [RFC-007 D4](../rfcs/007-docstore-data-integrity-hardening.md#d4--validate-then-stage-for-multi-file-uploads-iss-04) | [RFC Test Strategy: D4 row](../rfcs/007-docstore-data-integrity-hardening.md#per-fix-unit-tests)
    - **[Property 1](../designs/design-rfc007-docstore-integrity.md#property-1-no-phantom-pending-jobs) — No phantom pending**: Mock `enqueue_job` to raise; verify no Redis status hash exists
      - Test: `test_enqueue_failure_no_phantom_status`
      - **Validates:** [Design Property 1](../designs/design-rfc007-docstore-integrity.md#property-1-no-phantom-pending-jobs) | [RFC-007 D8](../rfcs/007-docstore-data-integrity-hardening.md#d8--reorder-enqueue_job-before-status-set-to-eliminate-phantom-jobs-iss-12) | [RFC Test Strategy: D8 row](../rfcs/007-docstore-data-integrity-hardening.md#per-fix-unit-tests)
    - **[Property 3](../designs/design-rfc007-docstore-integrity.md#property-3-no-orphaned-raw-uploads) — No orphaned raw uploads**: Mock `save_doc` to raise; verify `save_raw` never called
      - Test: `test_save_doc_failure_no_raw_orphan`
      - **Validates:** [Design Property 3](../designs/design-rfc007-docstore-integrity.md#property-3-no-orphaned-raw-uploads) | [RFC-007 D7](../rfcs/007-docstore-data-integrity-hardening.md#d7--reorder-save_raw-after-save_doc-to-prevent-orphans-iss-11) | [RFC Test Strategy: D7 row](../rfcs/007-docstore-data-integrity-hardening.md#per-fix-unit-tests)

  - [ ] <a id="16-checkpoint--batch-0"></a>1.6 Checkpoint — Batch 0

    - Run `uv run pytest` — all existing tests + new Batch 0 tests pass
    - Verify [Property 1](../designs/design-rfc007-docstore-integrity.md#property-1-no-phantom-pending-jobs), [Property 2](../designs/design-rfc007-docstore-integrity.md#property-2-all-or-nothing-upload-validation), [Property 3](../designs/design-rfc007-docstore-integrity.md#property-3-no-orphaned-raw-uploads), [Property 9](../designs/design-rfc007-docstore-integrity.md#property-9-correct-redis-default) green
    - Confirm no behavioral change on success paths ([D1](../rfcs/007-docstore-data-integrity-hardening.md#d1--fix-default-redis_url-to-localhost-iss-01), [D8](../rfcs/007-docstore-data-integrity-hardening.md#d8--reorder-enqueue_job-before-status-set-to-eliminate-phantom-jobs-iss-12), [D7](../rfcs/007-docstore-data-integrity-hardening.md#d7--reorder-save_raw-after-save_doc-to-prevent-orphans-iss-11) are operation reordering; [D4](../rfcs/007-docstore-data-integrity-hardening.md#d4--validate-then-stage-for-multi-file-uploads-iss-04) is additive validation)
    - Ask user if questions arise before proceeding

- [ ] <a id="2-batch-1--low-risk-minor-behavioral-change-d5-d3"></a>2. Batch 1 — Low risk, minor behavioral change ([D5](../rfcs/007-docstore-data-integrity-hardening.md#d5--use-full-uuid-for-doc_id-iss-09), [D3](../rfcs/007-docstore-data-integrity-hardening.md#d3--guard-registry_backfill-against-zero-key-completion-iss-03))

  *[RFC-007 Batch 1](../rfcs/007-docstore-data-integrity-hardening.md#batch-1--low-risk-minor-behavioral-change): "[D5](../rfcs/007-docstore-data-integrity-hardening.md#d5--use-full-uuid-for-doc_id-iss-09) changes doc_id length for new documents"*

  - [ ] <a id="21-use-full-uuid-for-doc_id-d5"></a>2.1 Use full UUID for doc_id ([D5](../rfcs/007-docstore-data-integrity-hardening.md#d5--use-full-uuid-for-doc_id-iss-09))

    - In `client.py:539,590`, replace `str(uuid.uuid4())[:8]` with `str(uuid.uuid4())`
    - Existing 8-char doc_ids in storage remain valid — only new ingestions affected
    - Verify no code path parses or constrains doc_id length (grep `[:8]` patterns)
    - _Requirements:_ [RFC-007 D5 (ISS-09)](../rfcs/007-docstore-data-integrity-hardening.md#d5--use-full-uuid-for-doc_id-iss-09) | [Design Property 5](../designs/design-rfc007-docstore-integrity.md#property-5-no-collision-prone-doc_id) | [Design Service: Worker/Client](../designs/design-rfc007-docstore-integrity.md#3-worker--client-clientpy) | [Design Sequence: Processing Flow](../designs/design-rfc007-docstore-integrity.md#processing-flow--d5--d7)

  - [ ] <a id="22-guard-registry-backfill-against-zero-key-completion-d3"></a>2.2 Guard registry backfill against zero-key completion ([D3](../rfcs/007-docstore-data-integrity-hardening.md#d3--guard-registry_backfill-against-zero-key-completion-iss-03))

    - In `registry_backfill.py:188-195`, add guard: when `meta_keys` is empty, skip `set_registry_complete`, log WARNING, return early
    - _Requirements:_ [RFC-007 D3 (ISS-03)](../rfcs/007-docstore-data-integrity-hardening.md#d3--guard-registry_backfill-against-zero-key-completion-iss-03) | [Design Property 7](../designs/design-rfc007-docstore-integrity.md#property-7-zero-key-backfill-guard) | [Design Service: Registry Backfill](../designs/design-rfc007-docstore-integrity.md#5-registry-backfill-registry_backfillpy) | [CLAUDE.md HR5](../rfcs/007-docstore-data-integrity-hardening.md#hard-rule-constraints-claudemd--binding)

  - [ ] <a id="23-write-unit-tests-for-batch-1"></a>2.3 Write unit tests for Batch 1

    - **[Property 5](../designs/design-rfc007-docstore-integrity.md#property-5-no-collision-prone-doc_id) — Full UUID doc_id**: Verify `doc_id` length is 36 (full UUID with hyphens)
      - Test: `test_doc_id_full_uuid`
      - **Validates:** [Design Property 5](../designs/design-rfc007-docstore-integrity.md#property-5-no-collision-prone-doc_id) | [RFC-007 D5](../rfcs/007-docstore-data-integrity-hardening.md#d5--use-full-uuid-for-doc_id-iss-09) | [RFC Test Strategy: D5 row](../rfcs/007-docstore-data-integrity-hardening.md#per-fix-unit-tests)
    - **[Property 7](../designs/design-rfc007-docstore-integrity.md#property-7-zero-key-backfill-guard) — Zero-key backfill guard**: Empty `meta_keys` list; verify `set_registry_complete` NOT called
      - Test: `test_backfill_zero_keys_skips_complete`
      - **Validates:** [Design Property 7](../designs/design-rfc007-docstore-integrity.md#property-7-zero-key-backfill-guard) | [RFC-007 D3](../rfcs/007-docstore-data-integrity-hardening.md#d3--guard-registry_backfill-against-zero-key-completion-iss-03) | [RFC Test Strategy: D3 row](../rfcs/007-docstore-data-integrity-hardening.md#per-fix-unit-tests)

  - [ ] <a id="24-checkpoint--batch-1"></a>2.4 Checkpoint — Batch 1

    - Run `uv run pytest` — all tests pass including [Batch 0](#1-batch-0--immediate-zero-risk-fixes-d1-d4-d8-d7) + Batch 1
    - Verify [Property 5](../designs/design-rfc007-docstore-integrity.md#property-5-no-collision-prone-doc_id), [Property 7](../designs/design-rfc007-docstore-integrity.md#property-7-zero-key-backfill-guard) green
    - Ask user if questions arise before proceeding

- [ ] <a id="3-batch-2--moderate-risk-error-path-changes-d9-d2"></a>3. Batch 2 — Moderate risk, error-path changes ([D9](../rfcs/007-docstore-data-integrity-hardening.md#d9--surface-delete_staging-failures-instead-of-swallowing-iss-20), [D2](../rfcs/007-docstore-data-integrity-hardening.md#d2--await-registry-delete-in-delete_doc-erasure-cascade-iss-02))

  *[RFC-007 Batch 2](../rfcs/007-docstore-data-integrity-hardening.md#batch-2--moderate-risk-error-path-changes): "[D2](../rfcs/007-docstore-data-integrity-hardening.md#d2--await-registry-delete-in-delete_doc-erasure-cascade-iss-02) changes erasure cascade from fire-and-forget to awaited — compliance-critical fix"*

  - [ ] <a id="31-surface-delete_staging-failures-d9"></a>3.1 Surface delete_staging failures ([D9](../rfcs/007-docstore-data-integrity-hardening.md#d9--surface-delete_staging-failures-instead-of-swallowing-iss-20))

    - In `storage.py:555-566`, change `delete_staging` to return `bool` (True on success, False on S3Error)
    - Add `STAGING_DELETE_FAILURES` Prometheus counter in `metrics.py`
    - On S3Error: log at WARNING, increment counter, return False (instead of silent swallow)
    - Update callers to handle the return value
    - _Requirements:_ [RFC-007 D9 (ISS-20)](../rfcs/007-docstore-data-integrity-hardening.md#d9--surface-delete_staging-failures-instead-of-swallowing-iss-20) | [Design Property 8](../designs/design-rfc007-docstore-integrity.md#property-8-observable-staging-delete-failure) | [Design Service: Storage](../designs/design-rfc007-docstore-integrity.md#4-storage-layer-storagepy) | [Design Service: Metrics](../designs/design-rfc007-docstore-integrity.md#6-metrics-metricspy)

  - [ ] <a id="32-await-registry-delete-in-erasure-cascade-d2"></a>3.2 Await registry delete in erasure cascade ([D2](../rfcs/007-docstore-data-integrity-hardening.md#d2--await-registry-delete-in-delete_doc-erasure-cascade-iss-02))

    - In `storage.py:266-296`, replace `_fire_and_forget()` with `await asyncio.wait_for(task, timeout=timeout)`
    - Read timeout from `REGISTRY_DELETE_TIMEOUT_S` env var (default 5.0, added in [Task 1.1](#11-fix-redis-url-default-d1) config task)
    - On timeout or exception: append error to `errors` list so caller receives accurate cascade report
    - _Requirements:_ [RFC-007 D2 (ISS-02)](../rfcs/007-docstore-data-integrity-hardening.md#d2--await-registry-delete-in-delete_doc-erasure-cascade-iss-02) | [Design Property 4](../designs/design-rfc007-docstore-integrity.md#property-4-erasure-cascade-completeness) | [Design Service: Storage](../designs/design-rfc007-docstore-integrity.md#4-storage-layer-storagepy) | [Design Sequence: Erasure Cascade](../designs/design-rfc007-docstore-integrity.md#erasure-cascade--d2) | [CLAUDE.md HR2](../rfcs/007-docstore-data-integrity-hardening.md#hard-rule-constraints-claudemd--binding)

  - [ ] <a id="33-write-unit-tests-for-batch-2"></a>3.3 Write unit tests for Batch 2

    - **[Property 8](../designs/design-rfc007-docstore-integrity.md#property-8-observable-staging-delete-failure) — Observable staging delete failure**: Mock S3Error in `delete_staging`; verify returns False and counter incremented
      - Test: `test_delete_staging_s3error_returns_false`
      - **Validates:** [Design Property 8](../designs/design-rfc007-docstore-integrity.md#property-8-observable-staging-delete-failure) | [RFC-007 D9](../rfcs/007-docstore-data-integrity-hardening.md#d9--surface-delete_staging-failures-instead-of-swallowing-iss-20) | [RFC Test Strategy: D9 row](../rfcs/007-docstore-data-integrity-hardening.md#per-fix-unit-tests)
    - **[Property 4](../designs/design-rfc007-docstore-integrity.md#property-4-erasure-cascade-completeness) — Erasure cascade completeness (timeout path)**: Mock Postgres delete to hang; verify 5s timeout triggers and error reported
      - Test: `test_delete_doc_registry_timeout`
      - **Validates:** [Design Property 4](../designs/design-rfc007-docstore-integrity.md#property-4-erasure-cascade-completeness) | [RFC-007 D2](../rfcs/007-docstore-data-integrity-hardening.md#d2--await-registry-delete-in-delete_doc-erasure-cascade-iss-02) | [RFC Test Strategy: D2 timeout row](../rfcs/007-docstore-data-integrity-hardening.md#per-fix-unit-tests)
    - **[Property 4](../designs/design-rfc007-docstore-integrity.md#property-4-erasure-cascade-completeness) — Erasure cascade completeness (exception path)**: Mock Postgres delete to raise; verify error appears in cascade result
      - Test: `test_delete_doc_awaits_registry`
      - **Validates:** [Design Property 4](../designs/design-rfc007-docstore-integrity.md#property-4-erasure-cascade-completeness) | [RFC-007 D2](../rfcs/007-docstore-data-integrity-hardening.md#d2--await-registry-delete-in-delete_doc-erasure-cascade-iss-02) | [RFC Test Strategy: D2 exception row](../rfcs/007-docstore-data-integrity-hardening.md#per-fix-unit-tests)

  - [ ] <a id="34-write-integration-test-for-erasure-cascade-d2"></a>3.4 Write integration test for erasure cascade ([D2](../rfcs/007-docstore-data-integrity-hardening.md#d2--await-registry-delete-in-delete_doc-erasure-cascade-iss-02))

    - End-to-end `delete_doc` against MinIO + Redis + Postgres (test containers or mocks)
    - Scenario 1: All stores healthy — all 4 stores clean, errors list empty
    - Scenario 2: Postgres failure injected — error reported, MinIO/Redis still cleaned
    - _Requirements:_ [RFC-007 D2](../rfcs/007-docstore-data-integrity-hardening.md#d2--await-registry-delete-in-delete_doc-erasure-cascade-iss-02) | [Design Property 4](../designs/design-rfc007-docstore-integrity.md#property-4-erasure-cascade-completeness) | [RFC Integration Tests](../rfcs/007-docstore-data-integrity-hardening.md#integration-tests) | [CLAUDE.md HR2](../rfcs/007-docstore-data-integrity-hardening.md#hard-rule-constraints-claudemd--binding)

  - [ ] <a id="35-checkpoint--batch-2"></a>3.5 Checkpoint — Batch 2

    - Run `uv run pytest` — all tests pass including [Batch 0](#1-batch-0--immediate-zero-risk-fixes-d1-d4-d8-d7) + [Batch 1](#2-batch-1--low-risk-minor-behavioral-change-d5-d3) + Batch 2
    - Verify [Property 4](../designs/design-rfc007-docstore-integrity.md#property-4-erasure-cascade-completeness), [Property 8](../designs/design-rfc007-docstore-integrity.md#property-8-observable-staging-delete-failure) green
    - Confirm `delete_doc` accurately reports Postgres failures in cascade result
    - Ask user if questions arise before proceeding

- [ ] <a id="4-batch-3--storage-migration-d6"></a>4. Batch 3 — Storage migration ([D6](../rfcs/007-docstore-data-integrity-hardening.md#d6--move-hash-cache-from-minio-json-blob-to-redis-hset-iss-10))

  *[RFC-007 Batch 3](../rfcs/007-docstore-data-integrity-hardening.md#batch-3--storage-migration): "Replaces MinIO JSON blob with Redis HSET"*

  - [ ] <a id="41-implement-redis-hset-hash-cache-d6"></a>4.1 Implement Redis HSET hash cache ([D6](../rfcs/007-docstore-data-integrity-hardening.md#d6--move-hash-cache-from-minio-json-blob-to-redis-hset-iss-10))

    - In `client.py:568-571,622-626`, replace MinIO JSON blob read/write with:
      - `HSET pageindex:hashes <filename> <sha256>` for writes
      - `HGET pageindex:hashes <filename>` for reads
    - Remove `self._cache_lock = asyncio.Lock()` — no longer needed with atomic per-field HSET
    - _Requirements:_ [RFC-007 D6 (ISS-10)](../rfcs/007-docstore-data-integrity-hardening.md#d6--move-hash-cache-from-minio-json-blob-to-redis-hset-iss-10) | [Design Property 6](../designs/design-rfc007-docstore-integrity.md#property-6-hash-cache-atomicity) | [Design Service: Worker/Client](../designs/design-rfc007-docstore-integrity.md#3-worker--client-clientpy) | [Design Data Model: Hash Cache](../designs/design-rfc007-docstore-integrity.md#hash-cache--d6-migration)

  - [ ] <a id="42-write-one-time-migration-utility-for-d6"></a>4.2 Write one-time migration utility for [D6](../rfcs/007-docstore-data-integrity-hardening.md#d6--move-hash-cache-from-minio-json-blob-to-redis-hset-iss-10)

    - Script: read existing MinIO `pageindex:hashes.json` blob -> parse JSON -> `HSET` each entry to Redis -> delete MinIO blob
    - Belt-and-suspenders: new code checks Redis first, falls back to MinIO blob if key missing
    - Remove fallback after one full deployment cycle
    - _Requirements:_ [RFC-007 Risks item 2 (D6 migration window)](../rfcs/007-docstore-data-integrity-hardening.md#risks)

  - [ ] <a id="43-write-unit-tests-for-batch-3"></a>4.3 Write unit tests for Batch 3

    - **[Property 6](../designs/design-rfc007-docstore-integrity.md#property-6-hash-cache-atomicity) — Hash cache atomicity**: Verify hash stored via `HSET`, retrievable via `HGET`
      - Test: `test_hash_cache_redis_hset`
      - **Validates:** [Design Property 6](../designs/design-rfc007-docstore-integrity.md#property-6-hash-cache-atomicity) | [RFC-007 D6](../rfcs/007-docstore-data-integrity-hardening.md#d6--move-hash-cache-from-minio-json-blob-to-redis-hset-iss-10) | [RFC Test Strategy: D6 row](../rfcs/007-docstore-data-integrity-hardening.md#per-fix-unit-tests)
    - **[Property 6](../designs/design-rfc007-docstore-integrity.md#property-6-hash-cache-atomicity) — Concurrent worker safety**: Two async tasks write different filenames; both persist (no last-writer-wins)
      - Test: `test_hash_cache_concurrent_workers`
      - **Validates:** [Design Property 6](../designs/design-rfc007-docstore-integrity.md#property-6-hash-cache-atomicity) | [RFC-007 D6](../rfcs/007-docstore-data-integrity-hardening.md#d6--move-hash-cache-from-minio-json-blob-to-redis-hset-iss-10) | [RFC Test Strategy: D6 row](../rfcs/007-docstore-data-integrity-hardening.md#per-fix-unit-tests)

  - [ ] <a id="44-checkpoint--batch-3"></a>4.4 Checkpoint — Batch 3

    - Run `uv run pytest` — all tests pass including [Batch 0](#1-batch-0--immediate-zero-risk-fixes-d1-d4-d8-d7) + [Batch 1](#2-batch-1--low-risk-minor-behavioral-change-d5-d3) + [Batch 2](#3-batch-2--moderate-risk-error-path-changes-d9-d2) + Batch 3
    - Verify [Property 6](../designs/design-rfc007-docstore-integrity.md#property-6-hash-cache-atomicity) green
    - Verify migration script successfully transfers hash data from MinIO to Redis
    - Ask user if questions arise before proceeding

- [ ] <a id="5-final-integration--regression-gate"></a>5. Final integration & regression gate

  - [ ] <a id="51-write-upload-validation-integration-test-d4"></a>5.1 Write upload validation integration test ([D4](../rfcs/007-docstore-data-integrity-hardening.md#d4--validate-then-stage-for-multi-file-uploads-iss-04))

    - HTTP-level test via `httpx.AsyncClient` against upload endpoint
    - Mixed valid/invalid batch -> returns 400, zero side effects
    - _Requirements:_ [RFC-007 D4](../rfcs/007-docstore-data-integrity-hardening.md#d4--validate-then-stage-for-multi-file-uploads-iss-04) | [Design Property 2](../designs/design-rfc007-docstore-integrity.md#property-2-all-or-nothing-upload-validation) | [RFC Integration Tests](../rfcs/007-docstore-data-integrity-hardening.md#integration-tests)

  - [ ] <a id="52-run-full-regression-suite"></a>5.2 Run full regression suite

    - Run `uv run pytest` — all existing + all new tests pass
    - Verify all [9 correctness properties](../designs/design-rfc007-docstore-integrity.md#correctness-properties) green
    - Run 3 consecutive full test suite executions to confirm zero flaky failures
    - _Requirements:_ [RFC-007 Regression Gate](../rfcs/007-docstore-data-integrity-hardening.md#regression-gate) | [Design Property-to-Test Matrix](../designs/design-rfc007-docstore-integrity.md#property-to-test-matrix)

  - [ ] <a id="53-final-checkpoint"></a>5.3 Final checkpoint

    - All [9 correctness properties](../designs/design-rfc007-docstore-integrity.md#correctness-properties) verified:
      - [P1](../designs/design-rfc007-docstore-integrity.md#property-1-no-phantom-pending-jobs): No phantom pending jobs ([D8](../rfcs/007-docstore-data-integrity-hardening.md#d8--reorder-enqueue_job-before-status-set-to-eliminate-phantom-jobs-iss-12))
      - [P2](../designs/design-rfc007-docstore-integrity.md#property-2-all-or-nothing-upload-validation): All-or-nothing upload validation ([D4](../rfcs/007-docstore-data-integrity-hardening.md#d4--validate-then-stage-for-multi-file-uploads-iss-04))
      - [P3](../designs/design-rfc007-docstore-integrity.md#property-3-no-orphaned-raw-uploads): No orphaned raw uploads ([D7](../rfcs/007-docstore-data-integrity-hardening.md#d7--reorder-save_raw-after-save_doc-to-prevent-orphans-iss-11))
      - [P4](../designs/design-rfc007-docstore-integrity.md#property-4-erasure-cascade-completeness): Erasure cascade completeness ([D2](../rfcs/007-docstore-data-integrity-hardening.md#d2--await-registry-delete-in-delete_doc-erasure-cascade-iss-02))
      - [P5](../designs/design-rfc007-docstore-integrity.md#property-5-no-collision-prone-doc_id): No collision-prone doc_id ([D5](../rfcs/007-docstore-data-integrity-hardening.md#d5--use-full-uuid-for-doc_id-iss-09))
      - [P6](../designs/design-rfc007-docstore-integrity.md#property-6-hash-cache-atomicity): Hash cache atomicity ([D6](../rfcs/007-docstore-data-integrity-hardening.md#d6--move-hash-cache-from-minio-json-blob-to-redis-hset-iss-10))
      - [P7](../designs/design-rfc007-docstore-integrity.md#property-7-zero-key-backfill-guard): Zero-key backfill guard ([D3](../rfcs/007-docstore-data-integrity-hardening.md#d3--guard-registry_backfill-against-zero-key-completion-iss-03))
      - [P8](../designs/design-rfc007-docstore-integrity.md#property-8-observable-staging-delete-failure): Observable staging delete failure ([D9](../rfcs/007-docstore-data-integrity-hardening.md#d9--surface-delete_staging-failures-instead-of-swallowing-iss-20))
      - [P9](../designs/design-rfc007-docstore-integrity.md#property-9-correct-redis-default): Correct Redis default ([D1](../rfcs/007-docstore-data-integrity-hardening.md#d1--fix-default-redis_url-to-localhost-iss-01))
    - Ask user for review before committing

## Notes

- Each batch is independently deployable per [RFC-007 Implementation Plan](../rfcs/007-docstore-data-integrity-hardening.md#implementation-plan)
- [D2](../rfcs/007-docstore-data-integrity-hardening.md#d2--await-registry-delete-in-delete_doc-erasure-cascade-iss-02) timeout is configurable via `REGISTRY_DELETE_TIMEOUT_S` env var (default 5.0s) per [RFC-007 Risk 1 (D2 timeout tuning)](../rfcs/007-docstore-data-integrity-hardening.md#risks)
- [D5](../rfcs/007-docstore-data-integrity-hardening.md#d5--use-full-uuid-for-doc_id-iss-09) changes doc_id length for new documents only; existing 8-char IDs remain valid per [RFC-007 Risk 3 (D5 doc_id length change)](../rfcs/007-docstore-data-integrity-hardening.md#risks)
- [D6](../rfcs/007-docstore-data-integrity-hardening.md#d6--move-hash-cache-from-minio-json-blob-to-redis-hset-iss-10) migration has a belt-and-suspenders fallback period; remove MinIO fallback after one deployment cycle per [RFC-007 Risk 2 (D6 migration window)](../rfcs/007-docstore-data-integrity-hardening.md#risks)
- [D4](../rfcs/007-docstore-data-integrity-hardening.md#d4--validate-then-stage-for-multi-file-uploads-iss-04) is a breaking change for callers relying on partial processing; document all-or-nothing semantics per [RFC-007 Risk 5 (D4 all-or-nothing upload)](../rfcs/007-docstore-data-integrity-hardening.md#risks)
- [D7](../rfcs/007-docstore-data-integrity-hardening.md#d7--reorder-save_raw-after-save_doc-to-prevent-orphans-iss-11) inverts the current save order; if `save_raw` fails after `save_doc`, the tree is still queryable per [RFC-007 Risk 4 (D7 save_raw after save_doc)](../rfcs/007-docstore-data-integrity-hardening.md#risks)

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "name": "Batch 0 — Zero-risk fixes",
      "tasks": ["1.1", "1.2", "1.3", "1.4"],
      "rfc_batch": "Batch 0",
      "notes": "All four tasks are independent — D1 (config), D4 (upload validation), D8 (enqueue order), D7 (save order) touch different files"
    },
    {
      "id": 1,
      "name": "Batch 0 — Tests + Checkpoint",
      "tasks": ["1.5", "1.6"],
      "depends_on": [0],
      "notes": "Tests validate the fixes from wave 0"
    },
    {
      "id": 2,
      "name": "Batch 1 — Low-risk behavioral changes",
      "tasks": ["2.1", "2.2"],
      "rfc_batch": "Batch 1",
      "depends_on": [1],
      "notes": "D5 (UUID) and D3 (backfill guard) are independent of each other"
    },
    {
      "id": 3,
      "name": "Batch 1 — Tests + Checkpoint",
      "tasks": ["2.3", "2.4"],
      "depends_on": [2]
    },
    {
      "id": 4,
      "name": "Batch 2 — Error-path changes",
      "tasks": ["3.1", "3.2"],
      "rfc_batch": "Batch 2",
      "depends_on": [3],
      "notes": "D9 (staging) and D2 (erasure) are independent but both touch storage.py — sequence if merge conflicts likely"
    },
    {
      "id": 5,
      "name": "Batch 2 — Tests + Checkpoint",
      "tasks": ["3.3", "3.4", "3.5"],
      "depends_on": [4]
    },
    {
      "id": 6,
      "name": "Batch 3 — Storage migration",
      "tasks": ["4.1", "4.2"],
      "rfc_batch": "Batch 3",
      "depends_on": [5],
      "notes": "D6 hash cache migration depends on all prior fixes being stable"
    },
    {
      "id": 7,
      "name": "Batch 3 — Tests + Checkpoint",
      "tasks": ["4.3", "4.4"],
      "depends_on": [6]
    },
    {
      "id": 8,
      "name": "Final integration + regression",
      "tasks": ["5.1", "5.2", "5.3"],
      "depends_on": [7]
    }
  ]
}
```
