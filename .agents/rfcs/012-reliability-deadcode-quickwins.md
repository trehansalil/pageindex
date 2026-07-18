<!-- Space: CITRA -->
<!-- Title: RFC-012: Reliability & Dead-Code Quick-Win Batch -->
<!-- Folder: RFCs -->

---
id: RFC-012
title: Reliability & Dead-Code Quick-Win Batch
status: proposed
date: 2026-07-16
plan-impact: yes
supersedes-decisions-in: []
---

## Context

`audit/DOCSTORE_AUDIT_REPORT.md` flagged 7 issues spanning worker reliability
(connection reuse, race conditions, process lifecycle) and dead code (unreferenced
files carrying real maintenance/confusion cost). Re-verified 2026-07-16 against live
`HEAD`. One (ISS-03) is already fixed post-audit; the rest are open. These are batched
together because each is small, standalone, and low-risk — no shared code surface, but
all are the kind of fix an L7 review would call "just do it" rather than defer.

### What this RFC covers

| Issue | Status | File:Line | One-liner |
|---|---|---|---|
| ISS-03 | **Already fixed** | `registry_backfill.py:188-198` | Now guards against marking backfill complete on 0 keys |
| ISS-07 | Open | `worker.py:275`, `:446` | Ad-hoc `aioredis.from_url` fallback instead of the `cache.py` singleton |
| ISS-37 | Open | `memory_admission.py:72-99` | Lock released/reacquired per iteration — check-then-admit race |
| ISS-39 | ✅ RESOLVED 2026-07-18 | `gunicorn.conf.py` | `graceful_timeout=30`, `max_requests=100`, `max_requests_jitter=10` all landed |
| ISS-42 | Open — dead code | `upload.py` (repo root) | Calls a non-existent MCP tool `process_document`; 0 references repo-wide |
| ISS-45 | Open — dead code | `tools/processing.py` | Tombstone-only file (1 line), 0 references |
| ISS-43 | Open | `test.py:21` | Hardcoded production URL, no env override |
| ISS-46 | Open | `registry_backfill.py:129-157` | Sequential upsert loop, no batching |

### What this RFC does NOT cover

- Any change to `registry_backfill.py`'s core backfill semantics beyond ISS-46's
  concurrency change — the completeness-guard fix (ISS-03) already landed separately.
- `stress_test.py` — it already has an env-var override (line 40), just defaulting to
  prod; noted but not touched since it's not in the audit's issue list.

## Hard Rule constraints (CLAUDE.md — binding)

- None of these fixes touch PII routing, erasure, or tree-quality gating directly.
  ISS-07's Redis singleton reuse and ISS-46's batched upserts are pure reliability/perf
  changes with no compliance surface.

## Decision

### D1 — ISS-03: no code change, close as resolved

`registry_backfill.py:188-193` already guards `set_registry_complete` behind a
non-empty `meta_keys` check. No task required beyond marking ISS-03 closed.

### D2 — ISS-07: route worker Redis access through the shared singleton

`worker.py:275` and `:446` fall back to `aioredis.from_url(settings.redis_url, ...)`
when `ctx.get("redis")` is falsy. `ctx["redis"]` is always set at worker startup
(`worker.py:509`), so this is dead-path insurance today, not a normal hit — but it's
the last ad-hoc connection site in the codebase (`helpers.py:389` already uses the
`cache.py:39` singleton). Replace both for consistency and defense-in-depth:

```python
from .cache import get_async_redis
redis: aioredis.Redis = ctx.get("redis") or await get_async_redis()
```

### D3 — ISS-37: hold the lock through the full check-then-admit window

`memory_admission.py:72-99` acquires and releases a lock per loop iteration, leaving a
window between the check and the admit where a concurrent request can slip through.
Wrap the full decision window — check through admit — in one `asyncio.Lock()`
acquisition instead of one per iteration.

### D4 — ISS-39: raise gunicorn graceful_timeout, add request jitter

`gunicorn.conf.py:13`'s `graceful_timeout=5` is too short for in-flight ingestion
requests to complete during a worker restart/deploy. Raise to 30s+, and add
`max_requests=100` / `max_requests_jitter=10` to avoid synchronized worker recycling
under load.

### D5 — ISS-42 / ISS-45: delete confirmed-dead files

`upload.py` (repo root) calls a non-existent MCP tool `process_document` and has 0
references repo-wide; `ingest_via_server.py` is the active replacement. `tools/processing.py`
is a 1-line tombstone comment with 0 references. Both confirmed dead via codebase-graph
reference search — delete outright, no deprecation shim (per CLAUDE.md's guidance
against backwards-compat hacks for confirmed-unused code).

### D6 — ISS-43: env-var override for `test.py`'s target URL

`test.py:21` hardcodes `https://pageindex.aiwithsalil.work/mcp`. Add:

```python
url = os.environ.get("TEST_MCP_URL", "http://localhost:8201/mcp")
```

Defaulting to localhost, not prod — prevents accidental test runs against the
production server.

### D7 — ISS-46: batch registry-backfill upserts with bounded concurrency

`registry_backfill.py:129` runs a sequential `for` loop over per-doc upserts. Replace
with bounded-concurrency batching:

```python
sem = asyncio.Semaphore(10)
async def _upsert(meta):
    async with sem:
        return await upsert_doc(meta)
results = await asyncio.gather(*(_upsert(m) for m in batch), return_exceptions=True)
```

## Implementation Plan

All 6 open fixes (D2-D7) are independent — no ordering dependency. Suggested batching
by risk, lowest first:

1. D6 (ISS-43, ~2 lines) — test URL env override
2. D5 (ISS-42/45, deletion) — dead-code removal
3. D2 (ISS-07, ~4 lines ×2 sites) — Redis singleton
4. D4 (ISS-39, config-only) — gunicorn timeouts
5. D3 (ISS-37, ~10 lines) — admission-lock scope
6. D7 (ISS-46, ~10 lines) — batched upserts

D1 (ISS-03) requires no implementation — mark resolved in the audit tracker only.

## Test Strategy

| Decision | Test |
|---|---|
| D2 | Assert worker startup path never calls `aioredis.from_url` directly (mock/spy on `get_async_redis`) |
| D3 | Concurrency test: two simultaneous admission checks against a near-full quota; assert only one admits |
| D7 | Assert `upsert_doc` is called once per item in a batch, results collected via `gather` with `return_exceptions=True` handling a simulated per-item failure |
| D4, D5, D6 | Config-only / deletion — verified by existing test suite continuing green, no new test needed |

## Risks

- D5's deletions are irreversible in the working tree (recoverable via git history) —
  confirmed zero references via codebase-graph search before removal, per the audit's
  own verification step.
- D4's gunicorn timeout increase changes deploy-time behavior (longer graceful
  shutdown window) — note in the deploy runbook, not just the RFC.
