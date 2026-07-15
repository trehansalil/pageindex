<!-- Space: CITRA -->
<!-- Title: Audit: Fix Research -->
<!-- Parent: PageIndex Docstore Audit -->
<!-- Confluence-Page-ID: 5092605971 -->
<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5092605971/Audit+Fix+Research -->

# Docstore Audit — Fix Research (Wave 4)

**Last updated:** 2026-07-15 (re-run: fix research added for ISS-32–ISS-46, discovered in this session's parallel exploration pass)

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

### ISS-41: `delete_doc` never removes `preloaded/<filename>`
**File:** `storage.py` (`delete_doc` cascade)

**Approach A: Add a 7th cascade step (Recommended)** (Complexity: S)
- What: Add an explicit `mc.remove_object(bucket, f"preloaded/{filename}")` step, wrapped in the same per-step error-capture pattern as the other 6 steps, run in the documented order (uploads → processed → meta → cache → registry → preloaded, or wherever DESIGN.md places it).
- Trade-off: Minimal, mirrors existing pattern exactly. Needs the filename resolved before other steps delete the metadata that names it.
- Dependencies: Confirm which step currently holds the filename needed to construct the `preloaded/` key — must capture it before the metadata-bearing object is deleted.

**Approach B: Verification script for existing orphans** (Complexity: S)
- What: One-off script (mirroring `hash_cache_migrate.py`'s pattern) that lists `preloaded/` and cross-references against currently-known doc_ids, flagging/removing orphans left by the pre-fix cascade.
- Trade-off: Needed regardless of Approach A, to clean up any erasure requests that already "succeeded" while leaking this object.
- Dependencies: None; complements Approach A rather than replacing it.

---

### ISS-34: `ensure_tessdata` silent non-Latin script substitution
**File:** `converters.py:719-752`

**Approach A: Hard-fail on non-Latin script mismatch (Recommended)** (Complexity: M)
- What: If a non-Latin script (e.g. `ara`) is requested but unavailable, raise `TessdataUnavailableError` instead of silently falling back to `deu`/`eng`. Caller in `client.py` already has an except branch (~line 492-496) that marks OCR escalation as `result="error"`, preserving the doc's pre-escalation garbled state so `low_quality_tree` surfaces correctly.
- Trade-off: A doc that previously "degraded" into false-clean Latin mojibake now correctly fails loud instead — strictly safer, since the whole point of this OCR path is satisfying the garble gate.
- Dependencies: None hard. Companion action item (infra, not code): pre-bake `ara.traineddata` in the image so the drop path is rarely hit.

**Approach B: Metric-only, keep degrading** (Complexity: S)
- What: Add `TESSDATA_LANG_UNAVAILABLE_TOTAL.labels(lang=...)` incremented on every drop/fallback; alert on rate > 0.
- Trade-off: Keeps current best-effort behavior but doesn't stop the false-clean-mojibake failure mode — only makes it observable after the fact.

**Recommended:** A, with pre-baked `ara.traineddata` tracked as a separate infra action item.

---

### ISS-36: Garble-gate 500-char digit-ratio floor
**File:** `helpers.py:534-538`, `helpers.py:1072-1075`

**Approach (Recommended):** (Complexity: S)
- What: For blobs ≤ 500 chars, don't invent a new ratio threshold (prone to false positives on short legitimate content) — instead extend the existing token-repetition check (already floor-gated at `len(tokens) > 20`, not chars) to cover short blobs, since repeated-numeric-junk trips it once token count is sufficient. Deduplicate `_tree_is_garbled`/`_flat_text_is_garbled` into one shared `_is_garbled_blob()` while touching this, to prevent "fixed in one, not the other" drift.
- Trade-off: Reuses an already-validated heuristic instead of a fresh threshold; needs re-validation against the existing garble-gate regression corpus (this codebase has been burned by garble-gate false positives before — e.g. GHV-TKV-Tarif wide-table false-positive history).
- Dependencies: Corpus re-validation pass before landing.

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

### ISS-32: `auth.py` fails OPEN when `MCP_BEARER_TOKEN` unset
**File:** `auth.py:40-47`

**Approach A: Fail closed to match `upload_app.py` (Recommended)** (Complexity: S)
- What: When `MCP_BEARER_TOKEN` is unset, reject all requests by default (matching `require_api_key`'s fail-closed behavior), gated behind an explicit opt-in flag (e.g. `MCP_ALLOW_UNAUTHENTICATED=1`) for local/dev use.
- Trade-off: Closes the inconsistency; requires any dev/test setup relying on the current open-by-default behavior to set the new flag explicitly.
- Dependencies: None.

**Approach B: Startup assertion** (Complexity: S)
- What: At server startup, if `MCP_BEARER_TOKEN` is unset AND environment is not explicitly `dev`/`local`, refuse to start (fail fast rather than fail open at request time).
- Trade-off: Prevents the misconfiguration from ever reaching request-serving; less flexible than a runtime flag.

**Recommended:** A — opt-in insecure-dev flag, default-closed.

---

### ISS-33: No PII/ZDR routing gate at query time
**File:** `tools/documents.py`, `config.py`

**Approach (Recommended):** (Complexity: S)
- What: Add a startup assertion that when a PII-bearing corpus flag is set (or unconditionally, if all corpora are treated as potentially PII-bearing per Hard Rule #3), `settings.openai_base_url`/`llm_provider` must resolve to a known ZDR-tier endpoint — refuse to start otherwise. This converts today's "satisfied by operational convention" into an enforced assertion.
- Trade-off: Doesn't add per-document routing (the global-setting model is a legitimate simplification for a single-tenant deployment) but closes the gap where a misconfigured `OPENAI_BASE_URL` could silently violate HR3.
- Dependencies: A documented list of accepted ZDR endpoints/patterns to assert against.

---

### ISS-35: AGPL fallback reachable with no hard gate
**File:** `converters.py:1218-1247`

**Approach A: Ship a metric now (Recommended immediate step)** (Complexity: S)
- What: Add `AGPL_FALLBACK_TOTAL` counter with `reason="docling_missing"` vs `reason="operator_configured"` labels, incremented whenever `pdf_to_markdown` (pymupdf4llm) actually runs. Alert on `reason="docling_missing" > 0`.
- Trade-off: Non-invasive, ships today regardless of the legal question below.

**Approach B: `PDF_CONVERTER_STRICT` deny-by-default flag (Follow-up, gated on legal sign-off)** (Complexity: M)
- What: When set, omit `pymupdf4llm` from the chain entirely or treat an AGPL-route conversion as a hard error requiring explicit unblock.
- Trade-off: Turns "degraded but working" into "hard down" during a docling outage — a behavior change needing product/legal buy-in per Hard Rule #4's "legal decision to clear, not a settled safe-harbor" framing.
- Dependencies: Explicit decision from whoever owns legal/compliance sign-off on whether AGPL-serving should be opt-in or opt-out.

**Recommended:** Ship A immediately; treat B as a follow-up pending the legal decision.

---

### ISS-37: `wait_for_memory` double-admit race
**File:** `memory_admission.py:60-97`

**Approach (Recommended):** (Complexity: S)
- What: Wrap the check-then-admit sequence in an `asyncio.Lock` (or a counting semaphore representing total admittable memory budget) so two concurrent callers can't both pass the check in the same window.
- Trade-off: Small serialization cost at admission time only (not per-job runtime); closes the race directly.
- Dependencies: None.

---

### ISS-39: gunicorn/Langfuse flush timeout mismatch
**File:** `gunicorn.conf.py`, `tracing.py`

**Approach (Recommended):** (Complexity: S)
- What: Raise `graceful_timeout` (and the ASGI `timeout_graceful_shutdown`) to comfortably exceed the Langfuse flush's worst-case network timeout, and add `max_requests`/`max_requests_jitter` so workers recycle proactively rather than only at deploy time.
- Trade-off: Slightly slower graceful shutdowns; acceptable trade for not silently dropping trace batches.
- Dependencies: None.

---

### ISS-40: `registry.py` `delete_doc` has no own timeout
**File:** `registry.py:~208-216`

**Approach (Recommended):** (Complexity: S)
- What: Add an explicit `asyncpg` statement timeout (or `asyncio.wait_for`) around the delete query itself, independent of whatever fix lands for ISS-02's fire-and-forget wrapper — this is the backstop for the backstop.
- Dependencies: None; pairs naturally with the ISS-02 fix.

---

### ISS-43: `stress_test.py`/`test.py` production URL defaults
**File:** `stress_test.py:~40`, `test.py:~21`

**Approach (Recommended):** (Complexity: S)
- What: `stress_test.py` already has env-var plumbing — flip its fallback default to `http://localhost:8201`. `test.py` has zero override capability — add the env var and drop the hardcoded prod URL entirely (require explicit opt-in, no default).
- Trade-off: Makes accidental prod runs against real LLM spend impossible rather than just harder.
- Dependencies: None; no CI job invokes either script.

---

## Dead-Code / Cleanup Fixes (ISS-38, 42, 44, 45, 46)

### ISS-42: `upload.py` — delete outright (Recommended, Complexity: S)
Zero importers confirmed repo-wide; already disclaimed in CLAUDE.md; `ingest_via_server.py` is the documented, active replacement. Confirm no onboarding doc tells new users to run it before deleting.

### ISS-45: `tools/processing.py` — delete outright (Recommended, Complexity: S)
Zero importers; optionally fold its one-line historical note into `tools/__init__.py`'s module docstring, which is a more discoverable home than an orphan file.

### ISS-44: Duplicated page-range parsing — extract shared helper (Recommended, Complexity: S)
Extract the whole page→hits extraction (parse loop + `hits` filtering, not just the range-parse) into a single `helpers.py` function (e.g. `_extract_page_hits(structure, pages)`); both call sites wrap it with their own logging/metrics. Closes the duplication fully rather than partially.

### ISS-38: `RAG_PARSE_FAILURES` cardinality — no code fix needed now (Complexity: —)
Track as a watch-item; only becomes worth fixing (e.g. bucket by doc-age instead of raw doc_id, or drop the label) if corpus scale makes Prometheus storage cost material.

### ISS-46: `registry_backfill.py` sequential upserts — bounded-concurrency gather (Recommended, Complexity: S)
Wrap `upsert_doc` calls in an `asyncio.Semaphore`-guarded coroutine (10-20 concurrent), `asyncio.gather` with per-item try/except preserved so one failure doesn't cancel the batch. Confirm `upsert_doc`'s underlying connection is pooled (not a single shared connection) before parallelizing — a chunked-sequential fallback (batches of 20-50) is the safer choice if pool sizing is uncertain.

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
ISS-41 ──(HR2 compliance, standalone)───→ batch 1 (new)
ISS-34 ──(pairs with tessdata pre-bake)──→ batch 2 (new)
ISS-36 ──(needs corpus re-validation)────→ batch 2 (new)
ISS-32 ──(standalone)────────────────────→ batch 1 (new)
ISS-35 ──(metric now; strict-flag gated on legal)→ batch 1 metric / follow-up flag (new)
ISS-37 ──(standalone)────────────────────→ batch 1 (new)
ISS-39 ──(standalone)────────────────────→ batch 1 (new)
ISS-40 ──(pairs with ISS-02)─────────────→ batch 2 (new)
ISS-42,44,45,46 ──(cleanup, standalone)──→ batch 1 (new)

Corpus quality:
ISS-30 ──(D5 interim + upstream Docling #3802)─→ pending upstream
ISS-31 ──(1/3 done; VLM path = RFC-004)────────→ pending RFC-004
```

## Complexity Summary

| Complexity | Code (remaining) | Corpus (remaining) | Total |
|---|---|---|---|
| S (Small) | 15 | 1 (ISS-30 interim) | 16 |
| M (Medium) | 6 | 0 | 6 |
| L (Large) | 0 | 1 (ISS-31 long-term) | 1 |
| External | 0 | 1 (ISS-30 upstream) | 1 |
| Legal sign-off gated | 1 (ISS-35 follow-up) | 0 | 1 |
