<!-- Space: CITRA -->
<!-- Title: RFC-008: Observability & Error-Handling Overhaul -->
<!-- Parent: RFCs -->
<!-- Confluence-Page-Id: 5092179983 -->
<!-- Confluence-Page-ID: 5092179983 -->
<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5092179983/RFC-008+Observability+Error-Handling+Overhaul -->

---
id: RFC-008
title: Observability & Error-Handling Overhaul
status: landed
date: 2026-07-10
plan-impact: yes
supersedes-decisions-in: []
---

## Context

The 62-document corpus audit (Wave 3, 2026-07-10) surfaced 7 issues (ISS-07, ISS-08,
ISS-13, ISS-16, ISS-17, ISS-18, ISS-19) that share two systemic anti-patterns:

**Pattern A — Ad-hoc Redis connection management.** Redis connections are created and
destroyed per-call in 4+ locations (`documents.py:54`, `helpers.py:368`, `worker.py:275`,
`worker.py:444`) instead of reusing the existing `get_async_redis()` / `get_cache_redis()`
singletons in `cache.py`. This is the single most impactful systemic issue: under load,
every MCP tool call and every RAG query creates and tears down a TCP connection to Redis.

**Pattern B — Broad `except Exception` with low-level logging.** 8+ locations catch all
exceptions with `logger.debug()` only, creating invisible degradation. The fail-open
intent is correct (cache miss should not block a query); the visibility is not (a
misconfigured Redis URL is indistinguishable from a healthy cache miss).

This RFC addresses both patterns across all 7 issues, adds 4 new Prometheus metrics for
operational visibility, and establishes the narrowing conventions that future code should
follow.

## Hard Rule constraints

- **HR2 (right-to-erasure cascade):** Not directly implicated, but ISS-16's cache-error
  visibility fix ensures that a cache-purge failure during erasure is logged at WARNING
  (not swallowed at debug), making cascade verification auditable.
- **HR3 (PII routing):** ISS-08's OpenAI vision retry/logging touches the image-describe
  path. The fix must not log image content or PII; only the exception type and a truncated
  error message are logged.
- **HR5 (never silently persist a low-quality tree):** ISS-18 and ISS-19 fix silent
  degradation in the RAG query path, not the ingestion path. `validate_tree()` is
  unaffected.

## Decision

### D1 — Replace all ad-hoc Redis connections with `cache.py` singletons (ISS-07)

**Files:** `tools/documents.py:54-58`, `helpers.py:368-372`

Replace every instance of:
```python
r = aioredis.from_url(settings.redis_url, decode_responses=False)
complete = await is_registry_complete(r)
await r.aclose()
```
with:
```python
r = await get_async_redis()
complete = await is_registry_complete(r)
# no aclose — singleton lifecycle
```

**Rationale:** The project already maintains connection singletons in `cache.py`
(`get_async_redis()` for general use, `get_cache_redis()` for the read-through cache).
Creating per-call connections defeats connection pooling and adds ~1-3ms of TCP setup
per call. Under concurrent MCP tool calls this compounds into a connection storm.

**Additional optimization:** Cache the `registry_complete` boolean in a module-level
variable with a 60s TTL. The flag is monotonic (transitions `False` -> `True` exactly once,
when initial ingestion finishes) so caching is safe and eliminates the Redis round-trip
entirely for the common steady-state case.

**Cross-reference:** ISS-07 is a prerequisite for ISS-06 (Redis pub/sub leak, covered in
RFC-009). ISS-06's fix assumes connections are managed via singletons; ad-hoc connections
would re-introduce the leak.

### D2 — Add retry + logging + metric to OpenAI vision image-describe (ISS-08)

**File:** `converters.py:1289-1290`

Current code:
```python
except Exception:
    return "image"
```

Replace with:
1. Catch `openai.RateLimitError` and `openai.APIConnectionError` separately: retry once
   with 2s exponential backoff before falling back to `"image"`.
2. Catch remaining `openai.APIError` subclasses: log at ERROR with exception type and
   truncated message (no request/response body — HR3 compliance), return `"image"`.
3. Let non-OpenAI exceptions (`TypeError`, `ValueError`, etc.) propagate — these indicate
   code bugs, not transient API failures.
4. Add Prometheus counter `IMAGE_DESCRIBE_FAILURES` (labels: `error_type`), incremented on
   every fallback.

**Rationale:** An invalid API key or unreachable endpoint currently makes every image in
every document silently degrade to `[Image: image]` with zero diagnostic signal. The
retry handles the most common transient failure (rate limit); the counter and log make
persistent failures visible in Grafana/alerting.

### D3 — Warn when MCP bearer-token auth is disabled (ISS-13)

**File:** `auth.py:24-27`

Current code silently passes all requests through when `MCP_BEARER_TOKEN` is empty:
```python
token = settings.mcp_bearer_token
if not token:
    return await call_next(request)
```

Add:
1. A once-only `logger.warning("MCP bearer-token auth is DISABLED — MCP_BEARER_TOKEN is
   empty")` on the first request that hits the disabled path. Use a module-level
   `_auth_warned: bool = False` flag to avoid log spam.
2. A Prometheus gauge `MCP_AUTH_DISABLED` set to `1` at middleware init when the token is
   empty, `0` otherwise. This enables a Grafana alert rule
   (`mcp_auth_disabled == 1 AND environment == "production"`).

**Rationale:** If the env var is accidentally unset during a deploy, the entire MCP API
becomes unauthenticated. The gauge provides a machine-checkable signal; the warning
provides a human-readable one in logs.

### D4 — Raise cache-error logging + narrow exception scope (ISS-16)

**File:** `cache.py:79, 93, 102`

Current code (3 locations):
```python
except Exception:
    logger.debug("cache get failed for %s", doc_id)
```

Replace with:
```python
except (redis.RedisError, ConnectionError) as exc:
    logger.warning("cache %s failed for %s: %s", op, doc_id, exc)
    CACHE_ERRORS.inc()
```

Changes:
1. Narrow catch from `except Exception` to `except (redis.RedisError, ConnectionError)`.
   This preserves fail-open behavior for all Redis/network failures while letting code bugs
   (`TypeError`, `KeyError`, etc.) propagate with a full traceback.
2. Raise log level from `debug` to `warning`. A cache failure is operationally significant
   — it means every read falls through to MinIO, doubling latency.
3. Add Prometheus counter `CACHE_ERRORS` (labels: `operation` = get|set|delete).

**Rationale:** With `debug`-level logging, a misconfigured `REDIS_URL` in production is
completely invisible unless someone enables debug logs. WARNING is the correct level for
"degraded but functional."

### D5 — Guard `_llm()` against `None` content (ISS-17)

**File:** `helpers.py:51`

Current code:
```python
return r.choices[0].message.content.strip()
```

If OpenAI returns `content=None` (refusal, content-filter, or malformed response),
`.strip()` raises `AttributeError`. This is currently masked by broad `except Exception`
in callers (ISS-18, ISS-19), but produces an unhelpful error type that confounds
debugging.

Replace with:
```python
content = r.choices[0].message.content
if content is None:
    logger.warning("LLM returned content=None for model=%s", model)
    return ""
return content.strip()
```

Downstream callers already handle empty strings via their existing fallback paths
(`_prefilter_docs` falls back to all doc_ids; `_search_one_doc` falls back to `ids = []`).

**Rationale:** This is a **prerequisite** for D6 and D7. Narrowing the exception scope in
those callers requires that `_llm()` no longer raises `AttributeError` for a known,
handleable condition.

### D6 — Harden `_prefilter_docs` JSON extraction + narrow catch (ISS-18)

**File:** `helpers.py:98-100`

Current code:
```python
except Exception as e:
    logger.error("prefilter failed: %s", e)
    return [d["doc_id"] for d in doc_summaries]
```

Replace with:
1. Before `json.loads(raw)`, attempt regex extraction:
   `match = re.search(r'\{.*\}', raw, re.DOTALL)` to strip markdown fences, preamble
   text, or trailing commentary that LLMs commonly wrap around JSON.
2. Narrow catch to `except (json.JSONDecodeError, KeyError, TypeError)`. These are the
   only expected failure modes from JSON parsing + dict access. Other exceptions
   (network errors from `_llm()`, `AttributeError` — now impossible after D5) should
   propagate.
3. Log at WARNING (not ERROR — the fallback is by design, not a crash).

**Rationale:** LLMs frequently return valid JSON wrapped in ```json fences or preceded by
"Here is the result:". The regex extraction recovers these cases instead of falling back
to the expensive all-docs path. The narrowed catch ensures code bugs surface.

### D7 — Harden `_search_one_doc` JSON extraction + narrow catch + metric (ISS-19)

**File:** `helpers.py:200-204`

Current code:
```python
except Exception as e:
    ids = []
    logger.error("search parse failed for %s: %s — raw: %.200s", doc_id, e, raw_resp)
```

Apply the same two-part fix as D6:
1. Regex JSON extraction before `json.loads`.
2. Narrow catch to `except (json.JSONDecodeError, KeyError, TypeError)`.
3. Add Prometheus counter `RAG_PARSE_FAILURES` (labels: `doc_id`), incremented on every
   fallback to `ids = []`.

**Rationale:** When this fallback fires, a document that was already narrowed as relevant
by the prefilter contributes zero context to the RAG answer. The counter makes this
visible; the regex extraction reduces how often it fires.

### D8 — Prometheus metric registry conventions

All 4 new metrics follow the existing project convention (module-level
`prometheus_client` objects):

| Metric | Type | Labels | Module |
|---|---|---|---|
| `pageindex_image_describe_failures_total` | Counter | `error_type` | `converters.py` |
| `pageindex_mcp_auth_disabled` | Gauge | — | `auth.py` |
| `pageindex_cache_errors_total` | Counter | `operation` | `cache.py` |
| `pageindex_rag_parse_failures_total` | Counter | `doc_id` | `helpers.py` |

Naming follows Prometheus conventions: `pageindex_` namespace prefix, `_total` suffix for
counters, snake_case. The `doc_id` label on `rag_parse_failures` is bounded by the
narrowed candidate set (default top-K = 200 per RFC-006 D2), not the full corpus, so
cardinality is controlled.

## Implementation Plan

### Batch 0 — Prerequisite (immediate)

| Issue | Fix | Size | Files |
|---|---|---|---|
| ISS-17 | D5: Guard `_llm()` against `None` content | S | `helpers.py` |

Must land first: D6 and D7 narrow their `except` clauses, which requires that `_llm()`
no longer raises `AttributeError` on `None` content.

### Batch 1 — Standalone fixes (no cross-dependencies)

| Issue | Fix | Size | Files |
|---|---|---|---|
| ISS-07 | D1: Replace ad-hoc Redis connections + cache monotonic flag | S | `documents.py`, `helpers.py`, `cache.py` |
| ISS-13 | D3: Auth-disabled warning + gauge | S | `auth.py` |
| ISS-16 | D4: Cache error logging + narrow catch + counter | S | `cache.py` |

All three are independent of each other and of Batch 0 (D5 changes a different code
path). They can be implemented in parallel.

**Cross-reference:** ISS-07 (D1) is a prerequisite for ISS-06 (Redis pub/sub leak,
RFC-009). RFC-009 Batch 1 must not start until D1 is merged.

### Batch 2 — Depends on Batch 0

| Issue | Fix | Size | Files |
|---|---|---|---|
| ISS-18 | D6: Harden `_prefilter_docs` + narrow catch | S | `helpers.py` |
| ISS-19 | D7: Harden `_search_one_doc` + narrow catch + counter | S | `helpers.py` |
| ISS-08 | D2: OpenAI vision retry + logging + counter | M | `converters.py` |

ISS-18 and ISS-19 depend on ISS-17 (D5). ISS-08 is standalone but grouped here for
review efficiency — its retry logic is the most complex change in this RFC.

## Test Strategy

### D1 (ISS-07) — Redis singleton reuse
- Unit test: mock `get_async_redis()`, call `_list_docs_with_fallback` and
  `_registry_narrow`, assert `aioredis.from_url` is never called.
- Unit test: verify `registry_complete` module-level cache — call twice within TTL,
  assert Redis is queried only once.

### D2 (ISS-08) — Image-describe resilience
- Unit test: mock OpenAI client to raise `RateLimitError`, verify retry fires once then
  falls back to `"image"`.
- Unit test: mock OpenAI client to raise `APIConnectionError`, verify same retry behavior.
- Unit test: mock OpenAI client to raise `AuthenticationError`, verify ERROR log emitted
  and `IMAGE_DESCRIBE_FAILURES` counter incremented.
- Unit test: mock OpenAI client to raise `TypeError`, verify exception propagates (not
  caught).

### D3 (ISS-13) — Auth-disabled warning
- Unit test: instantiate middleware with empty token, send a request, assert WARNING
  logged exactly once and `MCP_AUTH_DISABLED` gauge == 1.
- Unit test: instantiate middleware with valid token, assert gauge == 0 and no warning.

### D4 (ISS-16) — Cache error narrowing
- Unit test: mock Redis to raise `redis.ConnectionError`, assert WARNING logged,
  `CACHE_ERRORS` counter incremented, and function returns `None` (cache miss).
- Unit test: mock Redis to raise `TypeError`, assert exception propagates (not caught).

### D5 (ISS-17) — `_llm()` None guard
- Unit test: mock OpenAI response with `content=None`, assert `_llm()` returns `""` and
  WARNING logged.
- Unit test: mock OpenAI response with valid content, assert `.strip()` applied.

### D6 (ISS-18) — Prefilter JSON extraction
- Unit test: pass LLM response wrapped in ```json fences, verify JSON extracted
  successfully.
- Unit test: pass malformed response, verify fallback to all doc_ids and WARNING logged.
- Unit test: verify `KeyError` caught, `AttributeError` propagates.

### D7 (ISS-19) — Search-one-doc JSON extraction
- Unit test: pass LLM response with preamble text + valid JSON, verify node IDs extracted.
- Unit test: pass unparseable response, verify `ids = []`, WARNING logged,
  `RAG_PARSE_FAILURES` counter incremented.

### Integration
- Existing 62-document corpus regression (`issue/verify_corpus.py`) must pass with no
  regressions after all batches land.

## Risks

1. **D1 singleton lifecycle mismatch.** If `get_async_redis()` returns a connection bound
   to a different event loop (e.g., in test fixtures or multi-loop deployments), callers
   will get `RuntimeError`. **Mitigation:** The existing `cache.py` singleton is already
   used by the cache read/write path in production without issue; the risk is low.
   Test fixtures must use the same event-loop-scoped fixture.

2. **D2 retry adds latency to image-heavy documents.** A single 2s retry per failed image
   in a 50-image PDF adds up to 100s worst-case. **Mitigation:** The retry is bounded to
   1 attempt with a short backoff. If `IMAGE_DESCRIBE_FAILURES` counter spikes, operators
   can disable image description entirely via the existing `DESCRIBE_IMAGES=false` env var.

3. **D4 narrowed catch may miss an unexpected Redis error subclass.** If a future
   `redis` library version introduces a new exception class not under `redis.RedisError`,
   it would propagate instead of being caught. **Mitigation:** `redis.RedisError` is the
   documented base class for all Redis client errors. The `ConnectionError` addition
   catches OS-level socket failures. This covers all known failure modes.

4. **D6/D7 regex extraction is greedy.** `re.search(r'\{.*\}', raw, re.DOTALL)` matches
   the first `{` to the last `}`, which is correct for a single JSON object but would
   produce invalid JSON if the LLM returns multiple objects. **Mitigation:** Both callers
   expect exactly one JSON object. If the regex produces invalid JSON, `json.loads` raises
   `JSONDecodeError` and the existing fallback fires — no worse than today.

5. **D8 `doc_id` label cardinality.** The `RAG_PARSE_FAILURES` counter uses `doc_id` as a
   label. If parse failures are widespread across many documents, this could create high
   cardinality in Prometheus. **Mitigation:** The counter only fires on parse failures in
   the narrowed candidate set (bounded by `PAGEINDEX_CATALOG_TOPK`, default 200). If
   cardinality becomes a concern, the label can be dropped in a follow-up without changing
   the fix semantics.
