<!-- Space: CITRA -->
<!-- Title: Implementation Plan: Observability & Error-Handling Overhaul -->
<!-- Parent: Tasks -->
<!-- Confluence-Page-ID: 5093687297 -->
<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5093687297/Implementation+Plan+Observability+Error-Handling+Overhaul -->

# Implementation Plan: Observability & Error-Handling Overhaul

## Traceability

| Artifact | Reference |
|---|---|
| Governing RFC(s) | [RFC-008: Observability & Error-Handling Overhaul](../rfcs/008-observability-error-handling-overhaul.md) |
| Design Document | [Design: Observability & Error-Handling Overhaul](../designs/design-rfc008-observability-error-handling.md) |
| PRD / Requirements | `PRD.md` |
| Hard Rules | [CLAUDE.md HR2 + HR3 + HR5](../rfcs/008-observability-error-handling-overhaul.md#hard-rule-constraints) |
| RFC Implementation Order | [RFC-008 Implementation Plan](../rfcs/008-observability-error-handling-overhaul.md#implementation-plan) |
| RFC Test Strategy | [RFC-008 Test Strategy](../rfcs/008-observability-error-handling-overhaul.md#test-strategy) |
| Design Correctness Properties | [Design Correctness Properties](../designs/design-rfc008-observability-error-handling.md#correctness-properties) |
| Design Testing Strategy | [Design Testing Strategy](../designs/design-rfc008-observability-error-handling.md#testing-strategy) |

## Overview

Implements 8 decisions across the PageIndex MCP Server's observability and error-handling surface, organized into three dependency-ordered batches per [RFC-008 Implementation Plan](../rfcs/008-observability-error-handling-overhaul.md#implementation-plan). The plan proceeds from the prerequisite `_llm()` None-content guard ([Batch 0](../rfcs/008-observability-error-handling-overhaul.md#batch-0--prerequisite-immediate)) through standalone Redis/auth/cache fixes ([Batch 1](../rfcs/008-observability-error-handling-overhaul.md#batch-1--standalone-fixes-no-cross-dependencies)) to the dependent OpenAI vision and RAG query-path hardening ([Batch 2](../rfcs/008-observability-error-handling-overhaul.md#batch-2--depends-on-batch-0)), validating each batch with unit tests tied to the design document's [8 correctness properties](../designs/design-rfc008-observability-error-handling.md#correctness-properties) and closing with a 62-document corpus regression gate. Stack: Python 3.12, Redis, OpenAI SDK, Prometheus, FastMCP.

## Tasks

- [x] <a id="1-batch-0--prerequisite-d5"></a>1. Batch 0 -- Prerequisite ([D5](../rfcs/008-observability-error-handling-overhaul.md#d5--guard-_llm-against-none-content-iss-17))

  *[RFC-008 Batch 0](../rfcs/008-observability-error-handling-overhaul.md#batch-0--prerequisite-immediate): "D5 is a prerequisite for D6 and D7 -- without the None guard, narrowing their catches would expose AttributeError on content.strip()"*

  - [x] <a id="11-guard-llm-none-content-d5"></a>1.1 Guard `_llm()` None content ([D5](../rfcs/008-observability-error-handling-overhaul.md#d5--guard-_llm-against-none-content-iss-17))

    - In `helpers.py`, modify the `_llm()` function to check if `content` is `None` before calling `.strip()`
    - When `content is None`: log at WARNING (`"LLM returned None content for prompt %s", prompt[:80]`), return `""`
    - When `content` is valid: return `content.strip()` as before
    - _Requirements:_ [RFC-008 D5 (ISS-17)](../rfcs/008-observability-error-handling-overhaul.md#d5--guard-_llm-against-none-content-iss-17) | [Design Property 5](../designs/design-rfc008-observability-error-handling.md#property-5-llm-none-content-safety) | [Design Service: LLM Helper](../designs/design-rfc008-observability-error-handling.md#4-llm-helper-helperspy) | [Design Sequence: RAG Query Flow](../designs/design-rfc008-observability-error-handling.md#rag-query-flow--d5-d6-d7)

  - [x] <a id="12-test-llm-none-guard-d5"></a>1.2 Test `_llm()` None guard ([D5](../rfcs/008-observability-error-handling-overhaul.md#d5--guard-_llm-against-none-content-iss-17))

    - **[Property 5](../designs/design-rfc008-observability-error-handling.md#property-5-llm-none-content-safety) -- LLM None-content safety**: Verify None content returns `""` with WARNING log
      - Test: `test_llm_none_content_returns_empty_string` -- mock OpenAI to return `content=None`; assert return value is `""`; assert WARNING logged
      - Test: `test_llm_valid_content_strips` -- mock OpenAI to return `content="  result  "`; assert return value is `"result"`
      - **Validates:** [Design Property 5](../designs/design-rfc008-observability-error-handling.md#property-5-llm-none-content-safety) | [RFC-008 D5](../rfcs/008-observability-error-handling-overhaul.md#d5--guard-_llm-against-none-content-iss-17) | [RFC Test Strategy: D5](../rfcs/008-observability-error-handling-overhaul.md#d5-iss-17--_llm-none-guard)

  - [x] <a id="13-checkpoint--batch-0"></a>1.3 Checkpoint -- Batch 0

    - Run `uv run pytest` -- all existing tests + new Batch 0 tests pass
    - Verify [Property 5](../designs/design-rfc008-observability-error-handling.md#property-5-llm-none-content-safety) green
    - Confirm `_llm()` callers in `_prefilter_docs` and `_search_one_doc` now receive `""` instead of raising `AttributeError` on None content
    - Ask user if questions arise before proceeding

- [x] <a id="2-batch-1--standalone-fixes-d1-d3-d4"></a>2. Batch 1 -- Standalone fixes ([D1](../rfcs/008-observability-error-handling-overhaul.md#d1--replace-all-ad-hoc-redis-connections-with-cachepy-singletons-iss-07), [D3](../rfcs/008-observability-error-handling-overhaul.md#d3--warn-when-mcp-bearer-token-auth-is-disabled-iss-13), [D4](../rfcs/008-observability-error-handling-overhaul.md#d4--raise-cache-error-logging--narrow-exception-scope-iss-16))

  *[RFC-008 Batch 1](../rfcs/008-observability-error-handling-overhaul.md#batch-1--standalone-fixes-no-cross-dependencies): "D1, D3, D4 are standalone fixes with no cross-dependencies -- can be implemented in parallel"*

  - [x] <a id="21-replace-ad-hoc-redis-connections-d1"></a>2.1 Replace ad-hoc Redis connections ([D1](../rfcs/008-observability-error-handling-overhaul.md#d1--replace-all-ad-hoc-redis-connections-with-cachepy-singletons-iss-07))

    - In `tools/documents.py:54-58`, replace `aioredis.from_url()` + manual `aclose()` with `await get_async_redis()` (singleton, no close)
    - In `helpers.py:368-372`, apply the same replacement
    - Verify no remaining `aioredis.from_url` calls outside `cache.py` (grep for stale patterns)
    - _Requirements:_ [RFC-008 D1 (ISS-07)](../rfcs/008-observability-error-handling-overhaul.md#d1--replace-all-ad-hoc-redis-connections-with-cachepy-singletons-iss-07) | [Design Property 1](../designs/design-rfc008-observability-error-handling.md#property-1-redis-singleton-exclusivity) | [Design Service: Redis Connection Management](../designs/design-rfc008-observability-error-handling.md#1-redis-connection-management-cachepy) | [Design Service: Document Listing](../designs/design-rfc008-observability-error-handling.md#7-document-listing-toolsdocumentspy)

  - [x] <a id="22-cache-registry-complete-flag-d1"></a>2.2 Cache `registry_complete` flag ([D1](../rfcs/008-observability-error-handling-overhaul.md#d1--replace-all-ad-hoc-redis-connections-with-cachepy-singletons-iss-07))

    - Add module-level `_registry_complete_cache: bool | None = None` and `_registry_complete_ts: float = 0.0` variables
    - Before calling `is_registry_complete(r)`, check if cache is valid (`_registry_complete_cache is True` or `time.monotonic() - _registry_complete_ts < 60`)
    - On cache miss: call `is_registry_complete(r)`, store result and timestamp
    - The flag is monotonic (False -> True exactly once), so a 60s TTL is safe
    - _Requirements:_ [RFC-008 D1 (ISS-07)](../rfcs/008-observability-error-handling-overhaul.md#d1--replace-all-ad-hoc-redis-connections-with-cachepy-singletons-iss-07) | [Design Property 1](../designs/design-rfc008-observability-error-handling.md#property-1-redis-singleton-exclusivity) | [Design Service: Redis Connection Management](../designs/design-rfc008-observability-error-handling.md#1-redis-connection-management-cachepy)

  - [x] <a id="23-test-redis-singleton-reuse-d1"></a>2.3 Test Redis singleton reuse ([D1](../rfcs/008-observability-error-handling-overhaul.md#d1--replace-all-ad-hoc-redis-connections-with-cachepy-singletons-iss-07))

    - **[Property 1](../designs/design-rfc008-observability-error-handling.md#property-1-redis-singleton-exclusivity) -- Redis singleton exclusivity**: Verify no ad-hoc `aioredis.from_url` calls
      - Test: `test_no_adhoc_redis_connections` -- mock `get_async_redis`; call document listing and helper functions; assert `aioredis.from_url` never called
      - Test: `test_registry_complete_cache_ttl` -- call registry check twice within 60s; assert `is_registry_complete` called only once (cached)
      - **Validates:** [Design Property 1](../designs/design-rfc008-observability-error-handling.md#property-1-redis-singleton-exclusivity) | [RFC-008 D1](../rfcs/008-observability-error-handling-overhaul.md#d1--replace-all-ad-hoc-redis-connections-with-cachepy-singletons-iss-07) | [RFC Test Strategy: D1](../rfcs/008-observability-error-handling-overhaul.md#d1-iss-07--redis-singleton-reuse)

  - [x] <a id="24-auth-disabled-warning-gauge-d3"></a>2.4 Auth-disabled warning + gauge ([D3](../rfcs/008-observability-error-handling-overhaul.md#d3--warn-when-mcp-bearer-token-auth-is-disabled-iss-13))

    - In `auth.py`, add module-level `_auth_warned: bool = False` flag
    - When `MCP_BEARER_TOKEN` is empty and `_auth_warned is False`: log `logger.warning("MCP bearer-token auth is DISABLED -- MCP_BEARER_TOKEN is empty")`, set `_auth_warned = True`
    - Add Prometheus gauge `MCP_AUTH_DISABLED` (name: `pageindex_mcp_auth_disabled`) set to `1` at middleware init when token is empty, `0` when token is set
    - _Requirements:_ [RFC-008 D3 (ISS-13)](../rfcs/008-observability-error-handling-overhaul.md#d3--warn-when-mcp-bearer-token-auth-is-disabled-iss-13) | [Design Property 3](../designs/design-rfc008-observability-error-handling.md#property-3-auth-disabled-visibility) | [Design Service: Auth Middleware](../designs/design-rfc008-observability-error-handling.md#2-auth-middleware-authpy) | [Design Metrics Schema](../designs/design-rfc008-observability-error-handling.md#prometheus-metrics-schema)

  - [x] <a id="25-test-auth-disabled-d3"></a>2.5 Test auth-disabled warning ([D3](../rfcs/008-observability-error-handling-overhaul.md#d3--warn-when-mcp-bearer-token-auth-is-disabled-iss-13))

    - **[Property 3](../designs/design-rfc008-observability-error-handling.md#property-3-auth-disabled-visibility) -- Auth-disabled visibility**: Verify warning fires once and gauge reflects state
      - Test: `test_auth_disabled_warning_once` -- set `MCP_BEARER_TOKEN=""`, trigger auth path twice; assert WARNING logged exactly once
      - Test: `test_auth_disabled_gauge_set` -- set `MCP_BEARER_TOKEN=""`; assert `pageindex_mcp_auth_disabled` gauge == 1
      - Test: `test_auth_enabled_gauge_zero` -- set `MCP_BEARER_TOKEN="valid-token"`; assert `pageindex_mcp_auth_disabled` gauge == 0, no WARNING logged
      - **Validates:** [Design Property 3](../designs/design-rfc008-observability-error-handling.md#property-3-auth-disabled-visibility) | [RFC-008 D3](../rfcs/008-observability-error-handling-overhaul.md#d3--warn-when-mcp-bearer-token-auth-is-disabled-iss-13) | [RFC Test Strategy: D3](../rfcs/008-observability-error-handling-overhaul.md#d3-iss-13--auth-disabled-warning)

  - [x] <a id="26-cache-error-narrowing-d4"></a>2.6 Cache error narrowing ([D4](../rfcs/008-observability-error-handling-overhaul.md#d4--raise-cache-error-logging--narrow-exception-scope-iss-16))

    - In `cache.py:79, 93, 102`, replace `except Exception` with `except (redis.RedisError, ConnectionError)`
    - Raise log level from `logger.debug` to `logger.warning` for all caught cache errors
    - Add Prometheus counter `CACHE_ERRORS` (name: `pageindex_cache_errors`) incremented on each caught cache error
    - Let `TypeError`, `KeyError`, and other non-Redis exceptions propagate with full traceback
    - _Requirements:_ [RFC-008 D4 (ISS-16)](../rfcs/008-observability-error-handling-overhaul.md#d4--raise-cache-error-logging--narrow-exception-scope-iss-16) | [Design Property 4](../designs/design-rfc008-observability-error-handling.md#property-4-cache-error-visibility) | [Design Service: Redis Connection Management](../designs/design-rfc008-observability-error-handling.md#1-redis-connection-management-cachepy) | [Design Sequence: Cache Operation Flow](../designs/design-rfc008-observability-error-handling.md#cache-operation-flow--d4) | [Design Metrics Schema](../designs/design-rfc008-observability-error-handling.md#prometheus-metrics-schema)

  - [x] <a id="27-test-cache-error-narrowing-d4"></a>2.7 Test cache error narrowing ([D4](../rfcs/008-observability-error-handling-overhaul.md#d4--raise-cache-error-logging--narrow-exception-scope-iss-16))

    - **[Property 4](../designs/design-rfc008-observability-error-handling.md#property-4-cache-error-visibility) -- Cache error visibility**: Verify narrowed catch and counter
      - Test: `test_cache_redis_error_warning_and_counter` -- mock Redis raising `redis.ConnectionError`; assert WARNING logged, `pageindex_cache_errors` counter incremented, return value is `None` (fail-open)
      - Test: `test_cache_type_error_propagates` -- mock Redis raising `TypeError`; assert exception propagates (not caught)
      - **Validates:** [Design Property 4](../designs/design-rfc008-observability-error-handling.md#property-4-cache-error-visibility) | [RFC-008 D4](../rfcs/008-observability-error-handling-overhaul.md#d4--raise-cache-error-logging--narrow-exception-scope-iss-16) | [RFC Test Strategy: D4](../rfcs/008-observability-error-handling-overhaul.md#d4-iss-16--cache-error-narrowing)

  - [x] <a id="28-checkpoint--batch-1"></a>2.8 Checkpoint -- Batch 1

    - Run `uv run pytest` -- all tests pass including [Batch 0](#1-batch-0--prerequisite-d5) + Batch 1
    - Verify [Property 1](../designs/design-rfc008-observability-error-handling.md#property-1-redis-singleton-exclusivity), [Property 3](../designs/design-rfc008-observability-error-handling.md#property-3-auth-disabled-visibility), [Property 4](../designs/design-rfc008-observability-error-handling.md#property-4-cache-error-visibility) green
    - Confirm no `aioredis.from_url` calls remain outside `cache.py` ([D1](../rfcs/008-observability-error-handling-overhaul.md#d1--replace-all-ad-hoc-redis-connections-with-cachepy-singletons-iss-07))
    - Confirm auth gauge is queryable at `/metrics` ([D3](../rfcs/008-observability-error-handling-overhaul.md#d3--warn-when-mcp-bearer-token-auth-is-disabled-iss-13))
    - Ask user if questions arise before proceeding

- [x] <a id="3-batch-2--depends-on-batch-0-d2-d6-d7"></a>3. Batch 2 -- Depends on Batch 0 ([D2](../rfcs/008-observability-error-handling-overhaul.md#d2--add-retry--logging--metric-to-openai-vision-image-describe-iss-08), [D6](../rfcs/008-observability-error-handling-overhaul.md#d6--harden-_prefilter_docs-json-extraction--narrow-catch-iss-18), [D7](../rfcs/008-observability-error-handling-overhaul.md#d7--harden-_search_one_doc-json-extraction--narrow-catch--metric-iss-19))

  *[RFC-008 Batch 2](../rfcs/008-observability-error-handling-overhaul.md#batch-2--depends-on-batch-0): "D6 and D7 depend on D5 (Batch 0) -- without the None guard, narrowing catches would expose AttributeError. D2 is independent but grouped for sequencing."*

  - [x] <a id="31-openai-vision-retry-d2"></a>3.1 OpenAI vision retry + logging + metric ([D2](../rfcs/008-observability-error-handling-overhaul.md#d2--add-retry--logging--metric-to-openai-vision-image-describe-iss-08))

    - In `converters.py:1289-1290`, replace `except Exception: return "image"` with structured error handling:
      - Catch `openai.RateLimitError` and `openai.APIConnectionError`: retry once with 2s backoff before falling back to `"image"`
      - Catch remaining `openai.APIError` subclasses: log at ERROR with exception type and truncated message (no request/response body -- [HR3 compliance](../rfcs/008-observability-error-handling-overhaul.md#hard-rule-constraints)), return `"image"`
      - Let non-OpenAI exceptions (`TypeError`, `ValueError`) propagate -- these indicate code bugs
    - Add Prometheus counter `IMAGE_DESCRIBE_FAILURES` (name: `pageindex_image_describe_failures`, label: `error_type`) incremented on every fallback
    - _Requirements:_ [RFC-008 D2 (ISS-08)](../rfcs/008-observability-error-handling-overhaul.md#d2--add-retry--logging--metric-to-openai-vision-image-describe-iss-08) | [Design Property 2](../designs/design-rfc008-observability-error-handling.md#property-2-image-describe-retry-and-fallback) | [Design Service: OpenAI Vision](../designs/design-rfc008-observability-error-handling.md#3-openai-vision-image-describe-converterspy) | [Design Sequence: Ingestion Image-Describe Flow](../designs/design-rfc008-observability-error-handling.md#ingestion-image-describe-flow--d2) | [Design Metrics Schema](../designs/design-rfc008-observability-error-handling.md#prometheus-metrics-schema)

  - [x] <a id="32-test-image-describe-resilience-d2"></a>3.2 Test image-describe resilience ([D2](../rfcs/008-observability-error-handling-overhaul.md#d2--add-retry--logging--metric-to-openai-vision-image-describe-iss-08))

    - **[Property 2](../designs/design-rfc008-observability-error-handling.md#property-2-image-describe-retry-and-fallback) -- Image-describe retry and fallback**: Verify retry, fallback, and counter behavior
      - Test: `test_image_describe_rate_limit_retry` -- mock OpenAI raising `RateLimitError` then succeeding on retry; assert description returned (not `"image"`)
      - Test: `test_image_describe_rate_limit_exhaust` -- mock OpenAI raising `RateLimitError` twice; assert fallback to `"image"`, counter incremented with `error_type="RateLimitError"`
      - Test: `test_image_describe_connection_error_retry` -- mock OpenAI raising `APIConnectionError` then succeeding; assert description returned
      - Test: `test_image_describe_auth_error_no_retry` -- mock OpenAI raising `AuthenticationError`; assert ERROR logged, fallback to `"image"`, counter incremented, no retry
      - Test: `test_image_describe_type_error_propagates` -- mock raising `TypeError`; assert exception propagates (not caught)
      - **Validates:** [Design Property 2](../designs/design-rfc008-observability-error-handling.md#property-2-image-describe-retry-and-fallback) | [RFC-008 D2](../rfcs/008-observability-error-handling-overhaul.md#d2--add-retry--logging--metric-to-openai-vision-image-describe-iss-08) | [RFC Test Strategy: D2](../rfcs/008-observability-error-handling-overhaul.md#d2-iss-08--image-describe-resilience)

  - [x] <a id="33-prefilter-json-extraction-d6"></a>3.3 Harden `_prefilter_docs` JSON extraction ([D6](../rfcs/008-observability-error-handling-overhaul.md#d6--harden-_prefilter_docs-json-extraction--narrow-catch-iss-18))

    - In `helpers.py`, add regex-based JSON extraction to strip markdown fences before `json.loads()`:
      - Pattern: `r'```(?:json)?\s*([\s\S]*?)```'` -- extract content between fences
      - If no fences found, attempt `json.loads()` on raw content
    - Narrow `except Exception` to `except (json.JSONDecodeError, KeyError, TypeError)`
    - On caught exception: log at WARNING, return full `doc_summaries` list as fallback (existing fail-open behavior)
    - Let `AttributeError` and other unexpected exceptions propagate (code bugs, not data issues; safe because [D5](#11-guard-llm-none-content-d5) guards None content)
    - _Requirements:_ [RFC-008 D6 (ISS-18)](../rfcs/008-observability-error-handling-overhaul.md#d6--harden-_prefilter_docs-json-extraction--narrow-catch-iss-18) | [Design Property 6](../designs/design-rfc008-observability-error-handling.md#property-6-prefilter-json-extraction-resilience) | [Design Service: Prefilter](../designs/design-rfc008-observability-error-handling.md#5-prefilter-helperspy) | [Design Sequence: RAG Query Flow](../designs/design-rfc008-observability-error-handling.md#rag-query-flow--d5-d6-d7)

  - [x] <a id="34-test-prefilter-json-extraction-d6"></a>3.4 Test prefilter JSON extraction ([D6](../rfcs/008-observability-error-handling-overhaul.md#d6--harden-_prefilter_docs-json-extraction--narrow-catch-iss-18))

    - **[Property 6](../designs/design-rfc008-observability-error-handling.md#property-6-prefilter-json-extraction-resilience) -- Prefilter JSON extraction resilience**: Verify fence stripping, narrow catch, and fallback
      - Test: `test_prefilter_json_fences_extracted` -- mock `_llm()` returning `` ```json\n["id1","id2"]\n``` ``; assert extracted IDs match
      - Test: `test_prefilter_malformed_json_fallback` -- mock `_llm()` returning invalid JSON; assert WARNING logged, full `doc_summaries` returned as fallback
      - Test: `test_prefilter_key_error_caught` -- mock `_llm()` returning valid JSON with missing keys; assert caught, fallback returned
      - Test: `test_prefilter_attribute_error_propagates` -- force `AttributeError` in parsing path; assert exception propagates (not caught)
      - **Validates:** [Design Property 6](../designs/design-rfc008-observability-error-handling.md#property-6-prefilter-json-extraction-resilience) | [RFC-008 D6](../rfcs/008-observability-error-handling-overhaul.md#d6--harden-_prefilter_docs-json-extraction--narrow-catch-iss-18) | [RFC Test Strategy: D6](../rfcs/008-observability-error-handling-overhaul.md#d6-iss-18--prefilter-json-extraction)

  - [x] <a id="35-search-one-doc-json-extraction-d7"></a>3.5 Harden `_search_one_doc` JSON extraction ([D7](../rfcs/008-observability-error-handling-overhaul.md#d7--harden-_search_one_doc-json-extraction--narrow-catch--metric-iss-19))

    - In `helpers.py`, apply same regex JSON extraction pattern as [D6](#33-prefilter-json-extraction-d6) to `_search_one_doc`
    - Narrow `except Exception` to `except (json.JSONDecodeError, KeyError, TypeError)`
    - On caught exception: log at WARNING, return `ids=[]` as fallback, increment `RAG_PARSE_FAILURES` counter
    - Add Prometheus counter `RAG_PARSE_FAILURES` (name: `pageindex_rag_parse_failures`) incremented on each parse failure
    - Let `AttributeError` and other unexpected exceptions propagate (safe because [D5](#11-guard-llm-none-content-d5) guards None content)
    - _Requirements:_ [RFC-008 D7 (ISS-19)](../rfcs/008-observability-error-handling-overhaul.md#d7--harden-_search_one_doc-json-extraction--narrow-catch--metric-iss-19) | [Design Property 7](../designs/design-rfc008-observability-error-handling.md#property-7-search-one-doc-json-extraction-resilience) | [Design Service: Search-One-Doc](../designs/design-rfc008-observability-error-handling.md#6-search-one-doc-helperspy) | [Design Sequence: RAG Query Flow](../designs/design-rfc008-observability-error-handling.md#rag-query-flow--d5-d6-d7) | [Design Metrics Schema](../designs/design-rfc008-observability-error-handling.md#prometheus-metrics-schema)

  - [x] <a id="36-test-search-one-doc-d7"></a>3.6 Test `_search_one_doc` JSON extraction ([D7](../rfcs/008-observability-error-handling-overhaul.md#d7--harden-_search_one_doc-json-extraction--narrow-catch--metric-iss-19))

    - **[Property 7](../designs/design-rfc008-observability-error-handling.md#property-7-search-one-doc-json-extraction-resilience) -- Search-one-doc JSON extraction resilience**: Verify fence stripping, narrow catch, counter, and fallback
      - Test: `test_search_one_doc_preamble_json_extracted` -- mock `_llm()` returning preamble text followed by JSON; assert JSON portion extracted and parsed
      - Test: `test_search_one_doc_unparseable_fallback` -- mock `_llm()` returning non-JSON text; assert WARNING logged, `ids=[]` returned, `pageindex_rag_parse_failures` counter incremented
      - Test: `test_search_one_doc_attribute_error_propagates` -- force `AttributeError`; assert exception propagates (not caught)
      - **Validates:** [Design Property 7](../designs/design-rfc008-observability-error-handling.md#property-7-search-one-doc-json-extraction-resilience) | [RFC-008 D7](../rfcs/008-observability-error-handling-overhaul.md#d7--harden-_search_one_doc-json-extraction--narrow-catch--metric-iss-19) | [RFC Test Strategy: D7](../rfcs/008-observability-error-handling-overhaul.md#d7-iss-19--search-one-doc-json-extraction)

  - [x] <a id="37-checkpoint--batch-2"></a>3.7 Checkpoint -- Batch 2

    - Run `uv run pytest` -- all tests pass including [Batch 0](#1-batch-0--prerequisite-d5) + [Batch 1](#2-batch-1--standalone-fixes-d1-d3-d4) + Batch 2
    - Verify [Property 2](../designs/design-rfc008-observability-error-handling.md#property-2-image-describe-retry-and-fallback), [Property 6](../designs/design-rfc008-observability-error-handling.md#property-6-prefilter-json-extraction-resilience), [Property 7](../designs/design-rfc008-observability-error-handling.md#property-7-search-one-doc-json-extraction-resilience) green
    - Confirm all 4 new Prometheus metrics are registered and queryable ([D8](../rfcs/008-observability-error-handling-overhaul.md#d8--prometheus-metric-registry-conventions)): `pageindex_image_describe_failures`, `pageindex_cache_errors`, `pageindex_mcp_auth_disabled`, `pageindex_rag_parse_failures`
    - Verify [Property 8](../designs/design-rfc008-observability-error-handling.md#property-8-prometheus-metric-conventions) -- all metrics follow `pageindex_` namespace
    - Ask user if questions arise before proceeding

- [x] <a id="4-integration-validation"></a>4. Integration validation

  - [x] <a id="41-corpus-regression"></a>4.1 Corpus regression ([Integration](../rfcs/008-observability-error-handling-overhaul.md#integration))

    - Run `uv run python preprocess_client.py` against the 62-document corpus
    - Verify zero regressions: all 62 documents process to the same quality tier as before the overhaul
    - Verify new WARNING logs appear for any documents that trigger cache errors, None LLM content, or JSON parse failures (these are now visible, not silent)
    - Verify Prometheus metrics at `/metrics` endpoint reflect actual error counts from the corpus run
    - _Requirements:_ [RFC-008 Integration](../rfcs/008-observability-error-handling-overhaul.md#integration) | [Design Testing Strategy](../designs/design-rfc008-observability-error-handling.md#testing-strategy)

  - [x] <a id="42-final-checkpoint"></a>4.2 Final checkpoint

    - All [8 correctness properties](../designs/design-rfc008-observability-error-handling.md#correctness-properties) verified:
      - [P1](../designs/design-rfc008-observability-error-handling.md#property-1-redis-singleton-exclusivity): Redis singleton exclusivity ([D1](../rfcs/008-observability-error-handling-overhaul.md#d1--replace-all-ad-hoc-redis-connections-with-cachepy-singletons-iss-07))
      - [P2](../designs/design-rfc008-observability-error-handling.md#property-2-image-describe-retry-and-fallback): Image-describe retry and fallback ([D2](../rfcs/008-observability-error-handling-overhaul.md#d2--add-retry--logging--metric-to-openai-vision-image-describe-iss-08))
      - [P3](../designs/design-rfc008-observability-error-handling.md#property-3-auth-disabled-visibility): Auth-disabled visibility ([D3](../rfcs/008-observability-error-handling-overhaul.md#d3--warn-when-mcp-bearer-token-auth-is-disabled-iss-13))
      - [P4](../designs/design-rfc008-observability-error-handling.md#property-4-cache-error-visibility): Cache error visibility ([D4](../rfcs/008-observability-error-handling-overhaul.md#d4--raise-cache-error-logging--narrow-exception-scope-iss-16))
      - [P5](../designs/design-rfc008-observability-error-handling.md#property-5-llm-none-content-safety): LLM None-content safety ([D5](../rfcs/008-observability-error-handling-overhaul.md#d5--guard-_llm-against-none-content-iss-17))
      - [P6](../designs/design-rfc008-observability-error-handling.md#property-6-prefilter-json-extraction-resilience): Prefilter JSON extraction resilience ([D6](../rfcs/008-observability-error-handling-overhaul.md#d6--harden-_prefilter_docs-json-extraction--narrow-catch-iss-18))
      - [P7](../designs/design-rfc008-observability-error-handling.md#property-7-search-one-doc-json-extraction-resilience): Search-one-doc JSON extraction resilience ([D7](../rfcs/008-observability-error-handling-overhaul.md#d7--harden-_search_one_doc-json-extraction--narrow-catch--metric-iss-19))
      - [P8](../designs/design-rfc008-observability-error-handling.md#property-8-prometheus-metric-conventions): Prometheus metric conventions ([D8](../rfcs/008-observability-error-handling-overhaul.md#d8--prometheus-metric-registry-conventions))
    - Run `uv run pytest` 3 consecutive times to confirm zero flaky failures
    - Ask user for review before committing

## Notes

- [D5](../rfcs/008-observability-error-handling-overhaul.md#d5--guard-_llm-against-none-content-iss-17) is a strict prerequisite for [D6](../rfcs/008-observability-error-handling-overhaul.md#d6--harden-_prefilter_docs-json-extraction--narrow-catch-iss-18) and [D7](../rfcs/008-observability-error-handling-overhaul.md#d7--harden-_search_one_doc-json-extraction--narrow-catch--metric-iss-19) -- without the None guard, narrowing their catches would expose `AttributeError` on `content.strip()` per [RFC-008 Batch 0](../rfcs/008-observability-error-handling-overhaul.md#batch-0--prerequisite-immediate)
- [D1](../rfcs/008-observability-error-handling-overhaul.md#d1--replace-all-ad-hoc-redis-connections-with-cachepy-singletons-iss-07) singleton lifecycle must use the same event loop as callers per [Risk 1](../rfcs/008-observability-error-handling-overhaul.md#risks); test fixtures must use event-loop-scoped fixture
- [D2](../rfcs/008-observability-error-handling-overhaul.md#d2--add-retry--logging--metric-to-openai-vision-image-describe-iss-08) retry adds worst-case 2s per failed image; if `IMAGE_DESCRIBE_FAILURES` spikes, disable via `DESCRIBE_IMAGES=false` per [Risk 2](../rfcs/008-observability-error-handling-overhaul.md#risks)
- [D2](../rfcs/008-observability-error-handling-overhaul.md#d2--add-retry--logging--metric-to-openai-vision-image-describe-iss-08) logging must not include image content or PII -- only exception type and truncated message per [HR3](../rfcs/008-observability-error-handling-overhaul.md#hard-rule-constraints)
- [D4](../rfcs/008-observability-error-handling-overhaul.md#d4--raise-cache-error-logging--narrow-exception-scope-iss-16) narrowed catch may miss future `redis` exception subclasses not under `redis.RedisError` per [Risk 3](../rfcs/008-observability-error-handling-overhaul.md#risks); `redis.RedisError` is the documented base class
- [D6](../rfcs/008-observability-error-handling-overhaul.md#d6--harden-_prefilter_docs-json-extraction--narrow-catch-iss-18) and [D7](../rfcs/008-observability-error-handling-overhaul.md#d7--harden-_search_one_doc-json-extraction--narrow-catch--metric-iss-19) regex extraction should use non-greedy matching to avoid spanning multiple fenced blocks per [Risk 4](../rfcs/008-observability-error-handling-overhaul.md#risks)
- [D8](../rfcs/008-observability-error-handling-overhaul.md#d8--prometheus-metric-registry-conventions) metrics must avoid `doc_id` labels to prevent cardinality explosion per [Risk 5](../rfcs/008-observability-error-handling-overhaul.md#risks); use `error_type` labels only where specified

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "name": "Batch 0 -- Prerequisite (D5)",
      "tasks": ["1.1"],
      "rfc_batch": "Batch 0",
      "notes": "D5 _llm() None guard is prerequisite for D6 and D7 in Batch 2"
    },
    {
      "id": 1,
      "name": "Batch 0 -- Tests + Checkpoint",
      "tasks": ["1.2", "1.3"],
      "depends_on": [0],
      "notes": "Tests validate the D5 guard before proceeding"
    },
    {
      "id": 2,
      "name": "Batch 1 -- Standalone fixes (D1, D3, D4)",
      "tasks": ["2.1", "2.2", "2.4", "2.6"],
      "rfc_batch": "Batch 1",
      "depends_on": [1],
      "notes": "D1 (Redis singletons + cache flag), D3 (auth warning), D4 (cache narrowing) are independent -- can parallelize across files"
    },
    {
      "id": 3,
      "name": "Batch 1 -- Tests + Checkpoint",
      "tasks": ["2.3", "2.5", "2.7", "2.8"],
      "depends_on": [2],
      "notes": "Tests validate D1, D3, D4 before proceeding to Batch 2"
    },
    {
      "id": 4,
      "name": "Batch 2 -- Dependent fixes (D2, D6, D7)",
      "tasks": ["3.1", "3.3", "3.5"],
      "rfc_batch": "Batch 2",
      "depends_on": [3],
      "notes": "D6 and D7 depend on D5 (Batch 0). D2 is independent but grouped. All three touch different files -- can parallelize"
    },
    {
      "id": 5,
      "name": "Batch 2 -- Tests + Checkpoint",
      "tasks": ["3.2", "3.4", "3.6", "3.7"],
      "depends_on": [4],
      "notes": "Tests validate D2, D6, D7; checkpoint verifies all 4 new Prometheus metrics"
    },
    {
      "id": 6,
      "name": "Integration validation",
      "tasks": ["4.1", "4.2"],
      "depends_on": [5],
      "notes": "62-document corpus regression + final 8-property verification"
    }
  ]
}
```
