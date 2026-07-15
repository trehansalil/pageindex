<!-- Space: CITRA -->
<!-- Title: Audit: Fix Research -->
<!-- Parent: PageIndex Docstore Audit -->
<!-- Confluence-Page-ID: 5092605971 -->
<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5092605971/Audit+Fix+Research -->

# Docstore Audit — Fix Research (Wave 4)

**Last updated:** 2026-07-15 (18 resolved issues removed after codebase verification)

For each verified issue, 2-3 fix approaches were researched against the actual source code. Each approach includes complexity (S/M/L), trade-offs, and dependencies. A recommendation is highlighted per issue.

**Resolved issues (removed from this document):** ISS-01, 04, 06, 09, 10, 11, 12, 13, 14, 15, 16, 17, 20, 21, 26, 27, 28, 29 (18 issues verified fixed in codebase as of 2026-07-15).

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

### ISS-05: `list_processed_docs` O(N) serial MinIO GETs
**File:** `storage.py:392-429`

**Approach A: Store `node_count` in `.meta.json` sidecar (Recommended short-term)** (Complexity: S)
- What: Include `node_count` in `save_doc_meta`. Removes need for `recent_documents` to re-load full trees. O(N) GETs remain but each is tiny.
- Trade-off: Requires backfilling existing `.meta.json` files.
- Dependencies: None.

**Approach B: Registry-only listing (Recommended long-term)** (Complexity: M)
- What: Make registry authoritative. Remove MinIO fallback from `_list_docs_with_fallback`. Registry `list_docs` is a single SQL query.
- Trade-off: Breaks graceful degradation — listing unavailable if Postgres is down.
- Dependencies: ISS-03 fixed first. Registry must become mandatory.

**Approach C: Parallel MinIO GETs** (Complexity: M)
- What: Replace serial loop with `asyncio.gather` + semaphore (cap=10).
- Trade-off: Reduces wall-clock but doesn't eliminate O(N) work. Adds complexity to a path that should eventually be replaced.
- Dependencies: None.

---

### ISS-07: Redis connection storm (PARTIALLY FIXED)
**File:** `tools/documents.py:54-58`, `worker.py:275,446`

**Status:** `helpers.py:389` now uses `get_async_redis()` singleton. Remaining: `worker.py` still falls back to `aioredis.from_url()` when ctx lacks redis.

**Approach A: Extend singleton to worker fallback (Recommended)** (Complexity: S)
- What: Pass redis from ctx consistently, or use `get_async_redis()` as fallback instead of `aioredis.from_url()`.
- Trade-off: Minor; worker ctx should always carry redis.
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

---

## 🟡 LATENT

### ISS-18: `_prefilter_docs` broad catch degrades precision
**File:** `helpers.py:98-100`

**Approach (Recommended):** (Complexity: S)
- What: Add regex JSON extraction `re.search(r'\{.*\}', clean, re.DOTALL)` before `json.loads`. Narrow catch to `except (json.JSONDecodeError, KeyError, TypeError)`.
- Trade-off: Handles common failure (JSON in text). Code bugs surface.
- Dependencies: None.

---

### ISS-19: `_search_one_doc` broad catch loses results
**File:** `helpers.py:200-204`

**Approach (Recommended):** (Complexity: S)
- What: Same two-part fix as ISS-18: regex extraction + narrow catch. Add `RAG_PARSE_FAILURES` counter.
- Trade-off: Consistent with ISS-18. Handles common case.
- Dependencies: `metrics.py` (new counter).

---

## Corpus Quality Fixes

### ISS-30: في→# substitution (Gap 5) — INTERIM FIX

**Approach A: Post-processing workaround (Implemented via D5)** (Complexity: S)
- What: `_fix_fi_hash_substitution` in `converters.py` does regex-based replacement of في→# patterns in Arabic markdown output.
- Trade-off: Interim — the substitution is a Docling bug, not a pipeline defect. Risk of false positives on legitimate `#` characters in Arabic text (low in practice).

**Approach B: Upstream Docling fix (Recommended long-term)** (Complexity: External)
- What: Docling issue #3802 filed; maintainer confirmed the bug. Fix will land in a future Docling release.
- Trade-off: No pipeline code change needed. Requires Docling version bump.
- Dependencies: Upstream release timeline.

---

### ISS-31: Table column degradation (Gap 6) — PARTIALLY RESOLVED

**Approach A: Accept Docling limitation (Current state)** (Complexity: —)
- What: 1/3 resolved (world-stats-pocketbook -> PASS). Remaining 2 (GHV-TKV-Tarif, Unfallversicherung) are inherent Docling markdown table rendering limitations.
- Trade-off: No pipeline fix possible without a different extraction path.

**Approach B: VLM-based table extraction (Long-term, RFC-004 scope)** (Complexity: L)
- What: Use vision-language model to extract table structure from rendered PDF pages, bypassing Docling's markdown table conversion.
- Trade-off: High accuracy potential but adds LLM cost, latency, and VLM infrastructure dependency.
- Dependencies: RFC-004 Phase 1 (VLM integration). gpt-4.1-vision tested but DPI-unstable (RFC-004 Phase 0 probe). Granite-258M rejected (NO-GO: 2.9GB RSS, 38min/page).

---

## Fix Dependency Graph

```
ISS-07 ──(remaining worker.py fallback)──────→ batch 1
ISS-03 ──(prereq for ISS-05B)─────────────→ batch 1
ISS-18 ──(standalone)────────────────────→ batch 2
ISS-19 ──(standalone)────────────────────→ batch 2
ISS-05A──(enables ISS-06)───────────────→ batch 2
ISS-02 ──(standalone)────────────────────→ batch 2
ISS-08 ──(standalone)────────────────────→ batch 2
ISS-05B──(depends on ISS-03, registry)──→ batch 3 (long-term)

Corpus quality:
ISS-30 ──(D5 interim + upstream Docling #3802)─→ pending upstream
ISS-31 ──(1/3 done; VLM path = RFC-004)────────→ pending RFC-004
```

## Complexity Summary

| Complexity | Code (remaining) | Corpus (remaining) | Total |
|---|---|---|---|
| S (Small) | 5 | 1 (ISS-30 interim) | 6 |
| M (Medium) | 4 | 0 | 4 |
| L (Large) | 0 | 1 (ISS-31 long-term) | 1 |
| External | 0 | 1 (ISS-30 upstream) | 1 |
