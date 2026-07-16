<!-- Space: CITRA -->
<!-- Title: RFC-007: Docstore Data-Integrity & Compliance Hardening -->
<!-- Folder: RFCs -->
<!-- Confluence-Page-Id: 5093130245 -->
<!-- Confluence-Page-ID: 5093130245 -->
<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5093130245/RFC-007+Docstore+Data-Integrity+Compliance+Hardening -->

---
id: RFC-007
title: Docstore Data-Integrity & Compliance Hardening
status: landed
date: 2026-07-10
plan-impact: yes
supersedes-decisions-in: []
---

## Context

A Wave 3 docstore audit (2026-07-10) subjected every write-path in PageIndex to
end-to-end verification against actual source code. Nine issues survived independent
adversarial review. They cluster around a single systemic pattern:
**non-transactional multi-step writes** — sequential operations (stage -> set status ->
enqueue, save_raw -> save_doc -> save_meta) with no rollback on partial failure, leaving
the system in inconsistent states that are silent, latent, and — in one case — a
compliance violation.

This RFC formalizes fixes for all nine. Four issues are intentionally excluded:
ISS-22, ISS-23, ISS-24, ISS-25 were verified as style/tech-debt items that are either
non-issues or intentional design choices and are NOT addressed here.

### Severity distribution

| Severity | Count | Issues |
|---|---|---|
| FAILING (broken on fresh deploy) | 1 | ISS-01 |
| DEGRADED (incorrect behavior in production paths) | 3 | ISS-02, ISS-03, ISS-04 |
| LATENT (silent data corruption under load/failure) | 5 | ISS-09, ISS-10, ISS-11, ISS-12, ISS-20 |

## Hard Rule constraints (CLAUDE.md — binding)

- **HR2** (right-to-erasure cascade): ISS-02 is a direct violation. The fire-and-forget
  Postgres registry delete means `delete_doc` can log "full cascade succeeded" while the
  registry row persists. This RFC makes the registry delete awaited and error-tracked.
- **HR5** (never silently persist a low-quality tree): ISS-11 (orphaned raw on save_doc
  failure) and ISS-03 (empty backfill marking registry complete) both create states where
  the system believes it has valid data it does not. Fixes ensure persistence order
  matches validation order.

## Decision

### D1 — Fix default `redis_url` to localhost (ISS-01)

**File:** `config.py:81`

`redis_url` defaults to `"redis://neonatal-care-redis.neonatal-care:6379/1"` — a
hardcoded leftover from a different project. Fresh deployments without `REDIS_URL` env
var silently fail on every Redis-dependent path: job queue, cache, status polling.

**Decision:** Change default to `"redis://localhost:6379/0"`. This is the standard
convention for local/dev and matches the project's docker-compose Redis service. Production
deployments already set `REDIS_URL` explicitly.

### D2 — Await registry delete in `delete_doc` erasure cascade (ISS-02)

**File:** `storage.py:266-296`

The Postgres registry delete is scheduled via `_fire_and_forget()` — a non-awaited
background task. If the task fails (connection timeout, Postgres down), `delete_doc` logs
"full cascade succeeded" regardless. **This violates CLAUDE.md HR2**: right-to-erasure
must cascade across every derived store.

**Decision:** Replace fire-and-forget with `await asyncio.wait_for(task, timeout=5.0)`.
On timeout or exception, append the error to the `errors` list so the caller receives an
accurate cascade report. The 5s timeout prevents a hung Postgres connection from blocking
the entire delete path indefinitely.

### D3 — Guard `registry_backfill` against zero-key completion (ISS-03)

**File:** `registry_backfill.py:188-195`

When zero `.meta.json` files are found in MinIO (wrong bucket name, transient MinIO
outage, genuinely empty corpus), backfill still calls `set_registry_complete` in Redis.
Downstream, `_list_docs_with_fallback` prefers the (now-empty) Postgres registry over
MinIO listing — the entire document corpus becomes invisible to all five MCP query tools.

**Decision:** When `meta_keys` is empty, skip `set_registry_complete`. Log a WARNING
with the bucket name inspected and return early. A subsequent backfill run (or manual
invocation) will re-attempt. The registry stays in its pre-backfill state, which
correctly triggers MinIO-listing fallback.

### D4 — Validate-then-stage for multi-file uploads (ISS-04)

**File:** `upload_app.py:74-112`

In multi-file upload, if file N fails extension validation, files 1..N-1 are already
staged in MinIO with pending Redis status and arq jobs enqueued. The client receives
HTTP 400 but earlier files silently process in the background — a partial-commit the
caller cannot observe or control.

**Decision:** Split the upload handler into two passes:
1. **Validate pass:** Check all file extensions and size constraints. On any failure,
   return HTTP 400 before any MinIO write, Redis mutation, or arq enqueue.
2. **Stage pass:** Only executes if validation succeeds for all files. Stage to MinIO,
   set Redis status, enqueue arq jobs.

This is a behavioral change for callers that previously relied on partial processing of
valid files in a mixed batch. That behavior was undocumented and unintentional.

### D5 — Use full UUID for `doc_id` (ISS-09)

**File:** `client.py:539,590`

`doc_id = str(uuid.uuid4())[:8]` yields 32 bits of entropy. By the birthday paradox,
P(collision) reaches ~1% at ~6,500 documents. There is no collision check — `save_doc`
silently overwrites an existing document's tree.

**Decision:** Use `str(uuid.uuid4())` (full 128-bit UUID). The `doc_id` field is `text`
in MinIO object keys, Redis cache keys, and the Postgres registry (RFC-006) — no schema
migration required. Existing 8-char doc_ids in storage remain valid; only newly ingested
documents get full UUIDs.

### D6 — Move hash cache from MinIO JSON blob to Redis HSET (ISS-10)

**File:** `client.py:568-571,622-626`

The hash cache is a monolithic JSON blob in MinIO, guarded by `self._cache_lock =
asyncio.Lock()` — an instance-level lock. Multi-process arq workers each have their own
lock instance, so concurrent writes lose entries via last-writer-wins on the shared MinIO
object.

**Decision (immediate):** Replace the MinIO JSON blob with `Redis HSET
pageindex:hashes`. Each entry is `HSET pageindex:hashes <filename> <sha256>` — atomic
per field, no read-modify-write cycle. Redis is already a hard dependency. The
`_cache_lock` becomes unnecessary and is removed.

**Decision (long-term, deferred to RFC-006 stabilization):** Add a `sha256` column to
`doc_registry` in Postgres. Drop the Redis hash once the registry is the system of
record for content hashes. This couples to RFC-006's schema and migration path and is
NOT implemented in this RFC.

### D7 — Reorder `save_raw` after `save_doc` to prevent orphans (ISS-11)

**File:** `client.py:590-620`

`save_raw` persists the upload to MinIO before `save_doc` persists the processed tree.
If tree validation or `save_doc` fails, the raw file remains as an orphan in
`uploads/` — unreferenced by any processed artifact, invisible to `delete_doc` cascade
(which starts from the processed doc registry).

**Decision:** Move `save_raw` to execute after `save_doc` (or `save_flat_doc` for flat
content class). The tree must succeed validation and persist before the raw upload is
committed. On `save_raw` failure after successful `save_doc`, log an error — the
processed tree is still valid and queryable; the raw upload can be re-staged.

### D8 — Reorder `enqueue_job` before status set to eliminate phantom jobs (ISS-12)

**File:** `upload_app.py:98-108`

Redis hash is set to `"pending"` before `enqueue_job` is called. If enqueue fails
(Redis connection drop, arq serialization error), the phantom "pending" status persists
for 24 hours (the hash TTL). `reap_stale_jobs` only checks "processing" status, so
phantom "pending" entries are never cleaned.

**Decision:** Swap the order: call `enqueue_job` first, then set Redis status to
"pending" only on success. If `enqueue_job` raises, no phantom status is created. The
brief window where a job is enqueued but status is not yet set is acceptable — the
worst case is a status poll returning "not found" for a few milliseconds, which callers
already handle (the status endpoint returns 404 for unknown job IDs).

### D9 — Surface `delete_staging` failures instead of swallowing (ISS-20)

**File:** `storage.py:555-566`

`S3Error` in `delete_staging` is caught, logged at WARNING, and silently swallowed. The
caller proceeds as if staging files were deleted. Over time this leaks orphaned staging
objects in MinIO.

**Decision:** Change `delete_staging` to return `bool` (True on success, False on
failure). Add a `STAGING_DELETE_FAILURES` Prometheus counter (increment on False). The
caller can decide whether to retry or log — the current silent swallow is replaced with
an observable signal.

## Implementation Plan

Fixes are batched by dependency and risk. Each batch is independently deployable.

### Batch 0 — Immediate, zero-risk (no behavioral change to success paths)

| Fix | Issue | Files | Est. |
|---|---|---|---|
| D1 | ISS-01 | `config.py` | S |
| D4 | ISS-04 | `upload_app.py` | S |
| D8 | ISS-12 | `upload_app.py` | S |
| D7 | ISS-11 | `client.py` | S |

These four are pure operation-reordering or default-value fixes. No new dependencies,
no schema changes, no behavioral change on the success path.

### Batch 1 — Low risk, minor behavioral change

| Fix | Issue | Files | Est. |
|---|---|---|---|
| D5 | ISS-09 | `client.py` | S |
| D3 | ISS-03 | `registry_backfill.py` | S |

D5 changes doc_id length for new documents (existing IDs untouched). D3 adds a guard
that changes backfill behavior when MinIO returns zero keys.

### Batch 2 — Moderate risk, error-path changes

| Fix | Issue | Files | Est. |
|---|---|---|---|
| D9 | ISS-20 | `storage.py`, `metrics.py` | S |
| D2 | ISS-02 | `storage.py` | M |

D2 changes the erasure cascade from fire-and-forget to awaited — the compliance-critical
fix. Needs integration testing against a real Postgres instance to validate timeout
behavior.

### Batch 3 — Storage migration

| Fix | Issue | Files | Est. |
|---|---|---|---|
| D6 (immediate) | ISS-10 | `client.py` | M |

Replaces MinIO JSON blob with Redis HSET. Requires a migration path for existing hash
data (one-time read of MinIO blob, HSET each entry, then delete blob). The long-term
Postgres column is deferred to post-RFC-006 stabilization.

## Test Strategy

### Per-fix unit tests

| Fix | Test | Validates |
|---|---|---|
| D1 | `test_config_redis_default` | `redis_url` default is `redis://localhost:6379/0`, not neonatal-care |
| D2 | `test_delete_doc_awaits_registry` | Mock Postgres delete to raise; verify error appears in cascade result |
| D2 | `test_delete_doc_registry_timeout` | Mock Postgres delete to hang; verify 5s timeout triggers and error reported |
| D3 | `test_backfill_zero_keys_skips_complete` | Empty `meta_keys` list; verify `set_registry_complete` NOT called |
| D4 | `test_upload_mixed_invalid_no_staging` | Upload 3 files (2 valid, 1 invalid ext); verify zero MinIO writes |
| D5 | `test_doc_id_full_uuid` | Verify `doc_id` length is 36 (full UUID with hyphens) |
| D7 | `test_save_doc_failure_no_raw_orphan` | Mock `save_doc` to raise; verify `save_raw` never called |
| D8 | `test_enqueue_failure_no_phantom_status` | Mock `enqueue_job` to raise; verify no Redis status hash exists |
| D9 | `test_delete_staging_s3error_returns_false` | Mock S3Error; verify returns False and counter incremented |
| D6 | `test_hash_cache_redis_hset` | Verify hash stored via `HSET`, retrievable via `HGET` |
| D6 | `test_hash_cache_concurrent_workers` | Two async tasks write different hashes; verify both persist (no last-writer-wins) |

### Integration tests

- **Erasure cascade (D2):** End-to-end `delete_doc` against MinIO + Redis + Postgres
  (test containers). Verify all four stores are clean after successful delete. Inject
  Postgres failure; verify error reported and MinIO/Redis still cleaned.
- **Upload validation (D4):** HTTP-level test via `httpx.AsyncClient` against the
  upload endpoint. Mixed valid/invalid batch returns 400 with zero side effects.
- **Hash migration (D6):** Seed MinIO JSON blob with 100 entries. Run migration.
  Verify all 100 entries present in Redis HSET. Verify MinIO blob deleted.

### Regression gate

All existing tests must pass unchanged. The fixes are additive guards and reorderings —
no existing success-path behavior changes except D5 (longer doc_id), which has no
downstream constraint (all consumers treat doc_id as opaque `text`).

## Risks

1. **D2 timeout tuning.** The 5s timeout for Postgres registry delete is a judgment call.
   If Postgres is under load, legitimate deletes may exceed 5s and be reported as failures.
   Mitigation: make the timeout configurable via `REGISTRY_DELETE_TIMEOUT_S` env var
   (default 5.0). Monitor via the existing `delete_doc` error logging.

2. **D6 migration window.** During the transition from MinIO JSON blob to Redis HSET,
   workers started before the migration see the old blob; workers started after see Redis.
   Mitigation: the migration script writes to Redis HSET first, then deletes the MinIO
   blob. New code checks Redis first and falls back to MinIO blob if the key is missing
   (belt-and-suspenders for partial migration). Remove fallback after one full deployment
   cycle.

3. **D5 doc_id length change.** Existing 8-char doc_ids remain in storage. Any code that
   assumes a fixed doc_id length will break. Verified: no code path parses or constrains
   doc_id length — it is treated as opaque `text` everywhere (MinIO keys, Redis keys,
   Postgres `doc_id TEXT`). Risk is low but warrants a grep for `:8]` patterns in future
   code.

4. **D7 save_raw after save_doc.** If `save_raw` fails after `save_doc` succeeds, the
   processed tree exists without its source upload. This is preferable to the current
   orphan risk (raw without tree), because the tree is the queryable artifact and the raw
   upload is recoverable from the client. Add a `RAW_UPLOAD_FAILURES` counter for
   observability.

5. **D4 all-or-nothing upload.** Callers that previously relied on partial processing of
   valid files in a mixed batch will now get a clean 400 rejection. This is a breaking
   change for any such caller. Mitigation: document the all-or-nothing semantics in the
   API response body and changelog.
