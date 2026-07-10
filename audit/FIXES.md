<!-- Space: CITRA -->
<!-- Title: Audit: Fix Research -->
<!-- Parent: PageIndex Docstore Audit -->
<!-- Confluence-Page-ID: 5092605971 -->
<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5092605971/Audit+Fix+Research -->

# Docstore Audit — Fix Research (Wave 4)

For each verified issue, 2-3 fix approaches were researched against the actual source code. Each approach includes complexity (S/M/L), trade-offs, and dependencies. A recommendation is highlighted per issue.

---

## 🔴 FAILING

### ISS-01: `redis_url` default points to neonatal-care
**File:** `config.py:81`

**Approach A: Fix the default (Recommended)** (Complexity: S)
- What: Change default from `"redis://neonatal-care-redis.neonatal-care:6379/1"` to `"redis://localhost:6379/0"`.
- Trade-off: One-line fix. Zero risk. Matches standard dev convention.
- Dependencies: None. `.env.example` already documents `REDIS_URL` for production.

---

## 🟠 DEGRADED

### ISS-02: `delete_doc` fire-and-forget registry delete
**File:** `storage.py:266-296`

**Approach A: Await inline** (Complexity: M)
- What: Replace `_fire_and_forget` with direct `await _registry_delete_doc(doc_id)`. Requires making `delete_doc` async or splitting into sync/async variants.
- Trade-off: Strongest guarantee — delete completes before "cascade succeeded" logs. But requires all callers to handle async.
- Dependencies: All callers of `delete_doc` must be audited.

**Approach B: Await with timeout fallback (Recommended)** (Complexity: M)
- What: Capture the fire-and-forget task reference, `await asyncio.wait_for(task, timeout=5.0)` before logging success. If timeout, append to `errors` list.
- Trade-off: Minimal caller changes. Timeout prevents blocking on Postgres hang. Correctly reports partial failure.
- Dependencies: None beyond storage.py.

**Approach C: Emit metric + error callback** (Complexity: S)
- What: Add `add_done_callback` that logs ERROR + increments `REGISTRY_DELETE_FAILURES` counter on exception. Accept async gap but make it observable.
- Trade-off: Doesn't fix compliance gap, but makes it visible. Good interim measure.
- Dependencies: `metrics.py` (new counter).

---

### ISS-03: `registry_backfill` marks complete on 0 keys
**File:** `registry_backfill.py:188-195`

**Approach A: Gate on non-zero count (Recommended)** (Complexity: S)
- What: Skip `set_registry_complete` when `meta_keys` is empty. Log warning and exit.
- Trade-off: Simple, safe. Empty bucket stays on MinIO listing path.
- Dependencies: None.

**Approach B: Require `--force` for empty backfill** (Complexity: S)
- What: Only set complete flag on 0 keys when `--force` is passed. Without `--force`, log error.
- Trade-off: More flexible for intentional empty-corpus deployments.
- Dependencies: `--force` flag already exists.

---

### ISS-04: Partial upload rollback gap
**File:** `upload_app.py:74-112`

**Approach A: Validate all extensions first (Recommended)** (Complexity: S)
- What: Split into two passes: (1) validate all extensions upfront, raising HTTP 400 before any staging; (2) stage + enqueue each file.
- Trade-off: Clean fix. No partial state ever created. Zero risk.
- Dependencies: None.

**Approach B: Return partial success** (Complexity: M)
- What: Don't raise on bad extension. Skip file, append error to results, continue. Return HTTP 202 with per-file status.
- Trade-off: More client-friendly but changes API contract.
- Dependencies: Client code + API docs update.

---

### ISS-05: `list_processed_docs` O(N) serial MinIO GETs
**File:** `storage.py:392-429`

**Approach A: Store `node_count` in `.meta.json` sidecar (Recommended short-term)** (Complexity: S)
- What: Include `node_count` in `save_doc_meta`. Removes need for `recent_documents` to re-load full trees (ISS-06 enrichment loop). O(N) GETs remain but each is tiny.
- Trade-off: Requires backfilling existing `.meta.json` files.
- Dependencies: ISS-06 should consume `node_count` from listing.

**Approach B: Registry-only listing (Recommended long-term)** (Complexity: M)
- What: Make registry authoritative. Remove MinIO fallback from `_list_docs_with_fallback`. Registry `list_docs` is a single SQL query.
- Trade-off: Breaks graceful degradation — listing unavailable if Postgres is down.
- Dependencies: ISS-03 fixed first. Registry must become mandatory.

**Approach C: Parallel MinIO GETs** (Complexity: M)
- What: Replace serial loop with `asyncio.gather` + semaphore (cap=10).
- Trade-off: Reduces wall-clock but doesn't eliminate O(N) work. Adds complexity to a path that should eventually be replaced.
- Dependencies: None.

---

### ISS-06: `recent_documents` fetches all docs then slices
**File:** `tools/documents.py:74,109-122`

**Approach A: Pass pagination to data source (Recommended)** (Complexity: S)
- What: Pass `limit=page_size, offset=(page-1)*page_size` to `list_docs()`. Use `node_count` from sidecar (ISS-05) to eliminate tree deserialization loop.
- Trade-off: Registry path becomes O(1). MinIO fallback remains O(N) but is the degraded path.
- Dependencies: ISS-05 Approach A. Registry `list_docs` already supports LIMIT/OFFSET.

**Approach B: Two-tier listing with count** (Complexity: M)
- What: Registry: `count_docs()` for total + `LIMIT/OFFSET`. MinIO fallback: cap at `page * page_size`.
- Trade-off: Both paths bounded. MinIO path still iterates but stops early.
- Dependencies: `count_docs()` already exists in registry.py.

---

### ISS-07: Redis connection storm
**File:** `tools/documents.py:54-58`, `helpers.py:368-372`

**Approach A: Reuse `get_async_redis()` singleton (Recommended, combined with C)** (Complexity: S)
- What: Replace ad-hoc `aioredis.from_url()` + `aclose()` with existing `get_async_redis()` singleton from `cache.py`.
- Trade-off: May need to verify `decode_responses` compatibility. Minor.
- Dependencies: Verify `is_registry_complete` works with decoded responses.

**Approach B: Module-level Redis in registry.py** (Complexity: S)
- What: Move `is_registry_complete` check into registry module with its own Redis client.
- Trade-off: Violates separation of concerns (registry.py currently only talks to Postgres).
- Dependencies: Registry needs Redis URL at init.

**Approach C: Cache registry-complete flag in-process (Recommended, combined with A)** (Complexity: S)
- What: Cache `True` result in a module-level bool with TTL. The flag is monotonic (False→True only), so caching is safe.
- Trade-off: Up to TTL delay before tools switch to registry after backfill. Eliminates Redis check entirely for common case.
- Dependencies: None.

---

### ISS-08: `_describe` drops all OpenAI errors
**File:** `converters.py:1289-1290`

**Approach A: Log + fallback** (Complexity: S)
- What: Replace bare `except Exception` with `except Exception as exc: logger.warning(...)`. Same fallback, now visible.
- Trade-off: Minimal change, no behavior difference.
- Dependencies: None.

**Approach B: Retry transient + log permanent (Recommended)** (Complexity: M)
- What: Catch `RateLimitError`/`APIConnectionError` with 1 retry + backoff. Log all others at ERROR. Add `IMAGE_DESCRIBE_FAILURES` Prometheus counter.
- Trade-off: Better resilience + alertable metric. ~15 lines.
- Dependencies: `metrics.py` (new counter).

**Approach C: Concurrency cap + logging** (Complexity: M)
- What: Wrap `asyncio.gather` with `Semaphore(settings.llm_search_concurrency)`. Add logging per A.
- Trade-off: Fixes unbounded concurrency alongside error swallowing.
- Dependencies: `config.py` (reuse existing setting).

---

## 🟡 LATENT

### ISS-09: `doc_id` UUID truncation
**File:** `client.py:539,590`

**Approach A: Use full UUID (Recommended)** (Complexity: S)
- What: Replace `str(uuid.uuid4())[:8]` with `str(uuid.uuid4())`. 128 bits = effectively collision-free.
- Trade-off: Longer doc_ids (36 chars vs 8). May affect aesthetics of URLs/keys but no schema change needed.
- Dependencies: None — doc_id is `text` everywhere.

**Approach B: 16-char hex truncation** (Complexity: S)
- What: `str(uuid.uuid4().hex)[:16]` — 64 bits. Birthday collision at ~4B docs.
- Trade-off: Shorter than full UUID, still practically collision-free.
- Dependencies: None.

---

### ISS-10: Hash cache read-modify-write race
**File:** `client.py:568-571`

**Approach A: Redis hash (Recommended immediate)** (Complexity: S)
- What: `HSET pageindex:hashes <filename> <sha256>` in Redis. Atomic per-field. No read-modify-write.
- Trade-off: Fastest fix. Redis already a dependency. Data lost on Redis flush (re-processing is safe, just wasteful).
- Dependencies: None beyond existing Redis.

**Approach B: Postgres registry column (Recommended long-term)** (Complexity: M)
- What: Add `sha256` column to `doc_registry`. Drop MinIO JSON blob.
- Trade-off: Cleanest long-term fix. Postgres handles concurrency natively.
- Dependencies: RFC-006 registry must be stable.

**Approach C: Per-file hash keys in MinIO** (Complexity: M)
- What: One object per file: `hashes/<sha256_of_filename>.json`. Each PUT is atomic.
- Trade-off: Eliminates race without Postgres. Read path needs `list_objects` to reconstruct cache.
- Dependencies: Migration script.

---

### ISS-11: `save_raw` before `save_doc` orphans
**File:** `client.py:590-620`

**Approach A: Reverse the order (Recommended)** (Complexity: S)
- What: Move `save_raw` after `save_doc`/`save_flat_doc`. Tree succeeds before raw is written.
- Trade-off: If `save_raw` then fails, tree exists without source file. Tree is self-contained, so less harmful than an orphaned raw file.
- Dependencies: None.

**Approach B: try/except with cleanup** (Complexity: M)
- What: Wrap save sequence in try block. On failure, delete already-written objects.
- Trade-off: True rollback. More complex, cleanup can fail (double-fault).
- Dependencies: None.

---

### ISS-12: Job status set before arq enqueue
**File:** `upload_app.py:98-108`

**Approach A: Reverse the order (Recommended)** (Complexity: S)
- What: Move `enqueue_job` before `job_status_set`. If enqueue fails, no phantom status.
- Trade-off: Brief window where job is enqueued but status isn't set (client gets "not found"). Worker sets "processing" within seconds.
- Dependencies: None.

**Approach B: Atomic pipeline** (Complexity: M)
- What: Use Redis pipeline/transaction for status + enqueue. Or set status in worker's startup.
- Trade-off: Eliminates both phantom and not-found window. More complex.
- Dependencies: arq internals.

---

### ISS-13: Auth silently disabled when `MCP_BEARER_TOKEN` empty
**File:** `auth.py:24-27`

**Approach A: Startup warning** (Complexity: S)
- What: Log `WARNING("MCP_BEARER_TOKEN not set — MCP auth is DISABLED")` with once-only flag.
- Trade-off: Zero behavior change, just visibility.
- Dependencies: None.

**Approach B: Prometheus gauge + warning (Recommended)** (Complexity: S)
- What: Add `MCP_AUTH_DISABLED` gauge in `metrics.py` (set to 1 when token empty). Plus log warning.
- Trade-off: Persistent signal for monitoring/alerting.
- Dependencies: `metrics.py` (new gauge).

---

### ISS-14: Tessdata download with no integrity verification
**File:** `converters.py:755-768`

**Approach A: Add timeout + size cap (Recommended immediate)** (Complexity: S)
- What: Replace `urlretrieve` with `urlopen(url, timeout=30)` + chunked read with 100MB cap.
- Trade-off: Prevents hangs and disk-fill. No checksum yet.
- Dependencies: None.

**Approach B: SHA256 manifest** (Complexity: M)
- What: Ship `tessdata_sha256.json` manifest. Verify after download.
- Trade-off: Full integrity verification. Maintenance burden on new tessdata releases.
- Dependencies: New manifest file.

**Approach C: Pre-bake in Docker (Recommended production)** (Complexity: M)
- What: Add `RUN curl ...` to Dockerfile for all expected languages. Remove runtime download.
- Trade-off: Eliminates supply-chain risk entirely. Already partially done per memory.
- Dependencies: Dockerfile, CI.

---

### ISS-15: Upload endpoint no file size limit
**File:** `upload_app.py:89`

**Approach A: Chunked read with cap (Recommended)** (Complexity: S)
- What: Replace `file.read()` with chunked reader (1MB chunks). Abort with HTTP 413 if total exceeds `MAX_UPLOAD_SIZE_MB` (new env var, default 100MB).
- Trade-off: Works in all deployment modes. ~10 lines.
- Dependencies: `config.py` (new setting).

**Approach B: Content-Length check** (Complexity: S)
- What: Check `file.size` before read. Reject with HTTP 413 if over limit.
- Trade-off: Fast rejection. Doesn't protect against chunked-transfer where size is None.
- Dependencies: `config.py` (new setting).

---

### ISS-16: Cache swallows Redis errors at debug level
**File:** `cache.py:79,93,102`

**Approach A+C combined (Recommended):** (Complexity: S)
- What: (A) Raise logging to WARNING + add `CACHE_ERRORS` Prometheus counter. (C) Narrow catch to `except (redis.RedisError, ConnectionError)` so code bugs surface.
- Trade-off: Preserves fail-open behavior. Adds visibility + precision.
- Dependencies: `metrics.py` (new counter).

**Approach B: First-call connection probe** (Complexity: M)
- What: Add one-time `ping()` in `get_cache_redis()` after creating singleton. Log WARNING on failure.
- Trade-off: Catches misconfiguration at startup. Per-op failures stay quiet.
- Dependencies: None.

---

### ISS-17: `_llm()` no guard against `None` content
**File:** `helpers.py:51`

**Approach A: Explicit None check (Recommended)** (Complexity: S)
- What: Check `content is None`, log warning, return `""`.
- Trade-off: Simple, safe. Downstream callers handle empty string via existing fallback paths.
- Dependencies: None.

**Approach B: Raise `LLMRefusalError`** (Complexity: S)
- What: Define dedicated exception. Callers' broad `except Exception` already handles it.
- Trade-off: More precise error signaling for future code.
- Dependencies: None.

---

### ISS-18: `_prefilter_docs` broad catch degrades precision
**File:** `helpers.py:98-100`

**Approach C+A combined (Recommended):** (Complexity: S)
- What: (C) Add regex JSON extraction `re.search(r'\{.*\}', clean, re.DOTALL)` before `json.loads`. (A) Narrow catch to `except (json.JSONDecodeError, KeyError, TypeError)`.
- Trade-off: Handles common failure (JSON in text). Code bugs surface.
- Dependencies: ISS-17 should be fixed first.

---

### ISS-19: `_search_one_doc` broad catch loses results
**File:** `helpers.py:200-204`

**Approach A: Narrow catch + JSON extraction (Recommended)** (Complexity: S)
- What: Same two-part fix as ISS-18: regex extraction + narrow catch. Add `RAG_PARSE_FAILURES` counter.
- Trade-off: Consistent with ISS-18. Handles common case.
- Dependencies: ISS-17 fix. `metrics.py` (new counter).

---

### ISS-20: `delete_staging` swallows S3Error
**File:** `storage.py:555-566`

**Approach B: Return boolean + add metric (Recommended)** (Complexity: S)
- What: Return `bool` (True/False). Add `STAGING_DELETE_FAILURES` counter. Non-breaking.
- Trade-off: Makes failures visible without forcing callers to handle exceptions.
- Dependencies: `metrics.py` (new counter).

---

### ISS-21: Error paths trigger O(N) MinIO listing
**File:** `tools/documents.py:195,258,300`

**Approach A: Remove `available` list from error (Recommended)** (Complexity: S)
- What: Return `{"error": "Document not found"}` without calling `list_processed_docs()`. MCP tool description already says "Use recent_documents() to find available doc_ids."
- Trade-off: Slightly less helpful error message. But eliminates DoS vector entirely.
- Dependencies: None — pure code removal.

---

## Fix Dependency Graph

```
ISS-01 ──(standalone)──────────────────────────→ immediate
ISS-17 ──(prereq for ISS-18, ISS-19)──────────→ immediate
ISS-04 ──(standalone)──────────────────────────→ immediate
ISS-12 ──(standalone)──────────────────────────→ immediate
ISS-11 ──(standalone)──────────────────────────→ immediate
ISS-21 ──(standalone)──────────────────────────→ immediate
ISS-07 ──(prereq for ISS-06)──────────────────→ batch 1
ISS-03 ──(prereq for ISS-05B)─────────────────→ batch 1
ISS-13 ──(standalone)──────────────────────────→ batch 1
ISS-16 ──(standalone)──────────────────────────→ batch 1
ISS-09 ──(standalone)──────────────────────────→ batch 1
ISS-18 ──(depends on ISS-17)──────────────────→ batch 2
ISS-19 ──(depends on ISS-17)──────────────────→ batch 2
ISS-05A──(enables ISS-06)─────────────────────→ batch 2
ISS-06 ──(depends on ISS-05A, ISS-07)─────────→ batch 2
ISS-02 ──(standalone)──────────────────────────→ batch 2
ISS-08 ──(standalone)──────────────────────────→ batch 2
ISS-20 ──(standalone)──────────────────────────→ batch 2
ISS-10 ──(standalone)──────────────────────────→ batch 3
ISS-14 ──(standalone)──────────────────────────→ batch 3
ISS-15 ──(standalone)──────────────────────────→ batch 3
ISS-05B──(depends on ISS-03, registry stable)──→ batch 4 (long-term)
```

## Complexity Summary

| Complexity | Count |
|---|---|
| S (Small) | 16 |
| M (Medium) | 5 |
| L (Large) | 0 |
