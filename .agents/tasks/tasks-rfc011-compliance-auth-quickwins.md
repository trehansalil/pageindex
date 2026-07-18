<!-- Space: CITRA -->
<!-- Title: Implementation Plan: Compliance & Auth Quick-Win Batch -->
<!-- Folder: Tasks -->

# Implementation Plan: Compliance & Auth Quick-Win Batch

## Traceability

| Artifact                      | Reference                                                                                                                |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Governing RFC                 | [RFC-011: Compliance & Auth Quick-Win Batch](../rfcs/011-compliance-auth-quickwins.md)                                    |
| Design Document               | [Design: Compliance & Auth Quick-Win Batch](../designs/design-rfc011-compliance-auth-quickwins.md)                        |
| PRD / Requirements            | `PRD.md`                                                                                                                 |
| Hard Rules                    | [CLAUDE.md HR2 + HR3 + HR4](../rfcs/011-compliance-auth-quickwins.md#hard-rule-constraints-claudemd--binding)             |
| RFC Implementation Order      | [RFC-011 Implementation Plan](../rfcs/011-compliance-auth-quickwins.md#implementation-plan)                               |
| RFC Test Strategy             | [RFC-011 Test Strategy](../rfcs/011-compliance-auth-quickwins.md#test-strategy)                                           |
| Design Correctness Properties | [Design Correctness Properties](../designs/design-rfc011-compliance-auth-quickwins.md#correctness-properties)             |
| Design Testing Strategy       | [Design Testing Strategy](../designs/design-rfc011-compliance-auth-quickwins.md#testing-strategy)                         |

## Overview

Implements five compliance and auth hardening fixes ([D1](../rfcs/011-compliance-auth-quickwins.md#d1--iss-02-no-code-change-close-as-resolved) through [D6](../rfcs/011-compliance-auth-quickwins.md#d6--iss-33-startup-zdr-routing-assertion-for-pii-flagged-corpora)) from [RFC-011](../rfcs/011-compliance-auth-quickwins.md), organized into four batches (0-3) per the [RFC Implementation Plan](../rfcs/011-compliance-auth-quickwins.md#implementation-plan). [D1](../rfcs/011-compliance-auth-quickwins.md#d1--iss-02-no-code-change-close-as-resolved) is a close-only action (ISS-02 already fixed); the remaining four are small (4-15 line), standalone, config-driven fixes covering auth fail-closed default ([D4](../rfcs/011-compliance-auth-quickwins.md#d4--iss-32-bearer-auth-fails-closed-by-default)), erasure cascade gap ([D2](../rfcs/011-compliance-auth-quickwins.md#d2--iss-41-purge-preloadedfilename-in-the-erasure-cascade)), registry statement timeout ([D3](../rfcs/011-compliance-auth-quickwins.md#d3--iss-40-statement-level-timeout-on-registry-delete)), AGPL fallback observability ([D5](../rfcs/011-compliance-auth-quickwins.md#d5--iss-35-agpl-fallback-observability-metric-only)), and ZDR startup assertion ([D6](../rfcs/011-compliance-auth-quickwins.md#d6--iss-33-startup-zdr-routing-assertion-for-pii-flagged-corpora)). Each batch validates against the design document's [5 correctness properties](../designs/design-rfc011-compliance-auth-quickwins.md#correctness-properties) before advancing.

## Tasks

- [x] <a id="0-batch-0-close-resolved-issues-d1"></a>0. Batch 0 — Close Resolved Issues ([D1](../rfcs/011-compliance-auth-quickwins.md#d1--iss-02-no-code-change-close-as-resolved))

  *[RFC-011 D1](../rfcs/011-compliance-auth-quickwins.md#d1--iss-02-no-code-change-close-as-resolved): "No code change — registry delete already bounded. Close in audit tracker only."*

  - [x] <a id="01-close-iss-02-audit-tracker"></a>0.1 Close ISS-02 in audit tracker ([D1](../rfcs/011-compliance-auth-quickwins.md#d1--iss-02-no-code-change-close-as-resolved))

    - Mark ISS-02 as resolved in `audit/DOCSTORE_AUDIT_REPORT.md`
    - Reference existing regression coverage: `tests/test_storage_contract.py:388` (`test_delete_doc_awaits_registry`), `:404` (`test_delete_doc_registry_timeout`), `:480` (Postgres-failure scenario)
    - No code changes required; `storage.py:255-274` cascade step 6 already awaits `_registry_delete_doc` with `asyncio.wait_for` and appends both `TimeoutError` and generic `Exception` to the cascade's `errors` list
    - _Requirements:_ [RFC-011 D1](../rfcs/011-compliance-auth-quickwins.md#d1--iss-02-no-code-change-close-as-resolved)

  - [x] <a id="02-checkpoint-batch-0"></a>0.2 Checkpoint — Batch 0

    - Confirm ISS-02 marked as resolved in audit tracker
    - No code or test verification needed

- [x] <a id="1-batch-1-fail-closed-auth-and-erasure-d4-d2"></a>1. Batch 1 — Fail-Closed Auth & Erasure ([D4](../rfcs/011-compliance-auth-quickwins.md#d4--iss-32-bearer-auth-fails-closed-by-default), [D2](../rfcs/011-compliance-auth-quickwins.md#d2--iss-41-purge-preloadedfilename-in-the-erasure-cascade))

  *Highest-signal fixes first per [RFC Implementation Plan](../rfcs/011-compliance-auth-quickwins.md#implementation-plan): fail-closed auth default + erasure cascade gap (HR2 violation)*

  - [x] <a id="11-auth-fail-closed-default"></a>1.1 Auth fail-closed default ([D4](../rfcs/011-compliance-auth-quickwins.md#d4--iss-32-bearer-auth-fails-closed-by-default), ~12 lines in `auth.py` + `config.py`)

    - Add `MCP_ALLOW_UNAUTHENTICATED` to `config.py` (bool, default `false`), alongside existing `mcp_bearer_token`
    - Modify auth middleware in `auth.py:39-47`: when `settings.mcp_bearer_token` is unset and `settings.mcp_allow_unauthenticated` is not set, return `JSONResponse({"error": "auth not configured"}, status_code=503)`
    - Keep existing `MCP_AUTH_DISABLED` gauge and `_warn_once_auth_disabled()` warning for the explicit opt-in pass-through path
    - _Requirements:_ [RFC-011 D4](../rfcs/011-compliance-auth-quickwins.md#d4--iss-32-bearer-auth-fails-closed-by-default) | [Design Property 3](../designs/design-rfc011-compliance-auth-quickwins.md#property-3-auth-fail-closed-default) | [Design Service: auth.py](../designs/design-rfc011-compliance-auth-quickwins.md#3-authpy) | [Design Sequence: Auth Middleware](../designs/design-rfc011-compliance-auth-quickwins.md#auth-middleware-flow--d4)

  - [x] <a id="12-preloaded-purge-erasure-cascade"></a>1.2 Preloaded purge in erasure cascade ([D2](../rfcs/011-compliance-auth-quickwins.md#d2--iss-41-purge-preloadedfilename-in-the-erasure-cascade), ~10 lines in `storage.py`)

    - Add step 7 after registry delete in the erasure cascade (`storage.py:160-281`)
    - Use `doc_name` already resolved at cascade start (`storage.py:177-181`, flat-doc basename fallback at `:196-200`)
    - Build key as `preloaded/{doc_name}` and call `mc.remove_object`
    - Suppress `NoSuchKey` (not all documents have a preloaded object); append other `S3Error` to `errors` list
    - Log warning if `doc_name is None` — cannot determine preloaded key without it
    - Update cascade docstring (`storage.py:161-166`) to enumerate step 7
    - _Requirements:_ [RFC-011 D2](../rfcs/011-compliance-auth-quickwins.md#d2--iss-41-purge-preloadedfilename-in-the-erasure-cascade) | [HR2](../rfcs/011-compliance-auth-quickwins.md#hard-rule-constraints-claudemd--binding) | [Design Property 1](../designs/design-rfc011-compliance-auth-quickwins.md#property-1-preloaded-object-erasure) | [Design Service: storage.py](../designs/design-rfc011-compliance-auth-quickwins.md#1-storagepy) | [Design Sequence: Erasure Cascade](../designs/design-rfc011-compliance-auth-quickwins.md#erasure-cascade-flow--d2)

  - [x] <a id="13-unit-tests-d4"></a>1.3 Write auth middleware tests ([D4](../rfcs/011-compliance-auth-quickwins.md#d4--iss-32-bearer-auth-fails-closed-by-default))

    - **Validates:** [Design Property 3](../designs/design-rfc011-compliance-auth-quickwins.md#property-3-auth-fail-closed-default) | [RFC-011 D4](../rfcs/011-compliance-auth-quickwins.md#d4--iss-32-bearer-auth-fails-closed-by-default) | [RFC Test Strategy](../rfcs/011-compliance-auth-quickwins.md#test-strategy)
    - Test: token unset + `MCP_ALLOW_UNAUTHENTICATED` unset (default) -> 503 response with `{"error": "auth not configured"}`
    - Test: token unset + `MCP_ALLOW_UNAUTHENTICATED=true` -> pass-through with warning (`MCP_AUTH_DISABLED` gauge set, `_warn_once_auth_disabled` fires)
    - Test: token set -> normal auth flow (regression guard — existing behavior unchanged)

  - [x] <a id="14-unit-tests-d2"></a>1.4 Write preloaded purge tests ([D2](../rfcs/011-compliance-auth-quickwins.md#d2--iss-41-purge-preloadedfilename-in-the-erasure-cascade))

    - **Validates:** [Design Property 1](../designs/design-rfc011-compliance-auth-quickwins.md#property-1-preloaded-object-erasure) | [RFC-011 D2](../rfcs/011-compliance-auth-quickwins.md#d2--iss-41-purge-preloadedfilename-in-the-erasure-cascade) | [RFC Test Strategy](../rfcs/011-compliance-auth-quickwins.md#test-strategy)
    - Extend `tests/test_storage_contract.py` — assert `remove_object` called on `preloaded/<name>` during the cascade
    - Test: `doc_name is None` -> warning logged, no `remove_object` call for preloaded path

  - [x] <a id="15-checkpoint-batch-1"></a>1.5 Checkpoint — Batch 1

    - Run `uv run pytest` — all tests pass including new [Task 1.3](#13-unit-tests-d4) and [Task 1.4](#14-unit-tests-d2) tests
    - Verify [Design Property 1](../designs/design-rfc011-compliance-auth-quickwins.md#property-1-preloaded-object-erasure) and [Design Property 3](../designs/design-rfc011-compliance-auth-quickwins.md#property-3-auth-fail-closed-default) hold
    - Note: [D4](../rfcs/011-compliance-auth-quickwins.md#d4--iss-32-bearer-auth-fails-closed-by-default) is behavior-changing — flag in deploy runbook per [RFC Risks](../rfcs/011-compliance-auth-quickwins.md#risks)

- [x] <a id="2-batch-2-registry-timeout-and-agpl-metric-d3-d5"></a>2. Batch 2 — Registry Timeout & AGPL Metric ([D3](../rfcs/011-compliance-auth-quickwins.md#d3--iss-40-statement-level-timeout-on-registry-delete), [D5](../rfcs/011-compliance-auth-quickwins.md#d5--iss-35-agpl-fallback-observability-metric-only))

  *Independent of Batch 1 — no shared code surface*

  - [x] <a id="21-registry-statement-timeout"></a>2.1 Registry statement timeout ([D3](../rfcs/011-compliance-auth-quickwins.md#d3--iss-40-statement-level-timeout-on-registry-delete), ~4 lines in `registry.py`)

    - Add `timeout=settings.registry_delete_timeout_s` kwarg to `pool.execute(_DELETE_SQL, doc_id)` in `registry.py:208-216`
    - Reuses existing `registry_delete_timeout_s` config value (`config.py:34,87`) — no new config needed
    - _Requirements:_ [RFC-011 D3](../rfcs/011-compliance-auth-quickwins.md#d3--iss-40-statement-level-timeout-on-registry-delete) | [Design Property 2](../designs/design-rfc011-compliance-auth-quickwins.md#property-2-registry-delete-statement-timeout) | [Design Service: registry.py](../designs/design-rfc011-compliance-auth-quickwins.md#2-registrypy)

  - [x] <a id="22-agpl-fallback-metric"></a>2.2 AGPL fallback metric ([D5](../rfcs/011-compliance-auth-quickwins.md#d5--iss-35-agpl-fallback-observability-metric-only), ~10 lines in `metrics.py` + `converters.py`)

    - Define `AGPL_FALLBACK_TOTAL` Counter in `metrics.py` following existing `pageindex_<domain>_<noun>_total` naming:
      ```python
      AGPL_FALLBACK_TOTAL = Counter(
          "pageindex_agpl_fallback_total",
          "PDF conversions that used the AGPL pymupdf4llm path",
          ["reason"],
      )
      ```
    - Increment with `reason="operator_configured"` when `PDF_CONVERTER=pymupdf4llm` is explicit
    - Increment with `reason="docling_missing"` when Docling is unavailable and pymupdf4llm is used as fallback
    - Alert on `docling_missing > 0` — unintentional-fallback signal
    - Hard gate (`PDF_CONVERTER_STRICT`) explicitly out of scope per [RFC-011](../rfcs/011-compliance-auth-quickwins.md#what-this-rfc-does-not-cover)
    - _Requirements:_ [RFC-011 D5](../rfcs/011-compliance-auth-quickwins.md#d5--iss-35-agpl-fallback-observability-metric-only) | [HR4](../rfcs/011-compliance-auth-quickwins.md#hard-rule-constraints-claudemd--binding) | [Design Property 4](../designs/design-rfc011-compliance-auth-quickwins.md#property-4-agpl-fallback-observability) | [Design Service: converters.py](../designs/design-rfc011-compliance-auth-quickwins.md#4-converterspy) | [Design Service: metrics.py](../designs/design-rfc011-compliance-auth-quickwins.md#7-metricspy)

  - [x] <a id="23-unit-tests-d3"></a>2.3 Write registry timeout tests ([D3](../rfcs/011-compliance-auth-quickwins.md#d3--iss-40-statement-level-timeout-on-registry-delete))

    - **Validates:** [Design Property 2](../designs/design-rfc011-compliance-auth-quickwins.md#property-2-registry-delete-statement-timeout) | [RFC-011 D3](../rfcs/011-compliance-auth-quickwins.md#d3--iss-40-statement-level-timeout-on-registry-delete) | [RFC Test Strategy](../rfcs/011-compliance-auth-quickwins.md#test-strategy)
    - Extend `tests/test_registry_contract.py` — assert `pool.execute` receives `timeout` kwarg matching `settings.registry_delete_timeout_s`

  - [x] <a id="24-unit-tests-d5"></a>2.4 Write AGPL metric tests ([D5](../rfcs/011-compliance-auth-quickwins.md#d5--iss-35-agpl-fallback-observability-metric-only))

    - **Validates:** [Design Property 4](../designs/design-rfc011-compliance-auth-quickwins.md#property-4-agpl-fallback-observability) | [RFC-011 D5](../rfcs/011-compliance-auth-quickwins.md#d5--iss-35-agpl-fallback-observability-metric-only) | [RFC Test Strategy](../rfcs/011-compliance-auth-quickwins.md#test-strategy)
    - Assert counter increments with `reason="operator_configured"` when `PDF_CONVERTER=pymupdf4llm` is explicit
    - Assert counter increments with `reason="docling_missing"` when Docling is unavailable

  - [x] <a id="25-checkpoint-batch-2"></a>2.5 Checkpoint — Batch 2

    - Run `uv run pytest` — all tests pass including new [Task 2.3](#23-unit-tests-d3) and [Task 2.4](#24-unit-tests-d5) tests
    - Verify [Design Property 2](../designs/design-rfc011-compliance-auth-quickwins.md#property-2-registry-delete-statement-timeout) and [Design Property 4](../designs/design-rfc011-compliance-auth-quickwins.md#property-4-agpl-fallback-observability) hold

- [x] <a id="3-batch-3-zdr-startup-assertion-d6"></a>3. Batch 3 — ZDR Startup Assertion ([D6](../rfcs/011-compliance-auth-quickwins.md#d6--iss-33-startup-zdr-routing-assertion-for-pii-flagged-corpora))

  *Depends on reviewed ZDR allow-list per [RFC Risks](../rfcs/011-compliance-auth-quickwins.md#risks)*

  - [x] <a id="31-zdr-startup-assertion"></a>3.1 ZDR startup assertion ([D6](../rfcs/011-compliance-auth-quickwins.md#d6--iss-33-startup-zdr-routing-assertion-for-pii-flagged-corpora), ~15 lines in `config.py` + `server.py`)

    - Define ZDR allow-list constant in `config.py` next to other routing config, documented inline with the source ladder (Azure modified-abuse-monitoring host / Bedrock / OpenAI EU-ZDR endpoints)
    - Add `PII_CORPUS` config flag (bool, default `false`) in `config.py`
    - In `server.py` lifespan (`_lifespan_with_scrape`, lines 49-92): if `settings.pii_corpus` is `true` and `settings.openai_base_url` is not in the ZDR allow-list, raise `RuntimeError` with message referencing [HR3](../rfcs/011-compliance-auth-quickwins.md#hard-rule-constraints-claudemd--binding)
    - _Requirements:_ [RFC-011 D6](../rfcs/011-compliance-auth-quickwins.md#d6--iss-33-startup-zdr-routing-assertion-for-pii-flagged-corpora) | [HR3](../rfcs/011-compliance-auth-quickwins.md#hard-rule-constraints-claudemd--binding) | [Design Property 5](../designs/design-rfc011-compliance-auth-quickwins.md#property-5-zdr-routing-enforcement) | [Design Service: config.py](../designs/design-rfc011-compliance-auth-quickwins.md#5-configpy) | [Design Service: server.py](../designs/design-rfc011-compliance-auth-quickwins.md#6-serverpy) | [Design Sequence: Startup Validation](../designs/design-rfc011-compliance-auth-quickwins.md#startup-validation-flow--d6)

  - [x] <a id="32-unit-tests-d6"></a>3.2 Write ZDR assertion tests ([D6](../rfcs/011-compliance-auth-quickwins.md#d6--iss-33-startup-zdr-routing-assertion-for-pii-flagged-corpora))

    - **Validates:** [Design Property 5](../designs/design-rfc011-compliance-auth-quickwins.md#property-5-zdr-routing-enforcement) | [RFC-011 D6](../rfcs/011-compliance-auth-quickwins.md#d6--iss-33-startup-zdr-routing-assertion-for-pii-flagged-corpora) | [RFC Test Strategy](../rfcs/011-compliance-auth-quickwins.md#test-strategy)
    - Test: `PII_CORPUS=true` + allowlisted `openai_base_url` -> startup succeeds (no exception)
    - Test: `PII_CORPUS=true` + arbitrary `openai_base_url` -> `RuntimeError` raised
    - Test: `PII_CORPUS=false` -> no check performed regardless of `openai_base_url` value

  - [x] <a id="33-checkpoint-batch-3"></a>3.3 Checkpoint — Batch 3

    - Run `uv run pytest` — all tests pass including new [Task 3.2](#32-unit-tests-d6) tests
    - Verify [Design Property 5](../designs/design-rfc011-compliance-auth-quickwins.md#property-5-zdr-routing-enforcement) holds
    - Confirm ZDR allow-list has been reviewed and signed off per [RFC Risks](../rfcs/011-compliance-auth-quickwins.md#risks)

- [x] <a id="4-final-checkpoint"></a>4. Final Checkpoint

  - Run full test suite: `uv run pytest` — zero failures
  - Verify all 5 design correctness properties hold:
    - [Property 1: Preloaded Object Erasure](../designs/design-rfc011-compliance-auth-quickwins.md#property-1-preloaded-object-erasure) (via [Task 1.2](#12-preloaded-purge-erasure-cascade) + [Task 1.4](#14-unit-tests-d2))
    - [Property 2: Registry Delete Statement Timeout](../designs/design-rfc011-compliance-auth-quickwins.md#property-2-registry-delete-statement-timeout) (via [Task 2.1](#21-registry-statement-timeout) + [Task 2.3](#23-unit-tests-d3))
    - [Property 3: Auth Fail-Closed Default](../designs/design-rfc011-compliance-auth-quickwins.md#property-3-auth-fail-closed-default) (via [Task 1.1](#11-auth-fail-closed-default) + [Task 1.3](#13-unit-tests-d4))
    - [Property 4: AGPL Fallback Observability](../designs/design-rfc011-compliance-auth-quickwins.md#property-4-agpl-fallback-observability) (via [Task 2.2](#22-agpl-fallback-metric) + [Task 2.4](#24-unit-tests-d5))
    - [Property 5: ZDR Routing Enforcement](../designs/design-rfc011-compliance-auth-quickwins.md#property-5-zdr-routing-enforcement) (via [Task 3.1](#31-zdr-startup-assertion) + [Task 3.2](#32-unit-tests-d6))
  - Zero flaky failures across 3 consecutive runs

## Notes

- [D1](../rfcs/011-compliance-auth-quickwins.md#d1--iss-02-no-code-change-close-as-resolved) (ISS-02) is close-only — no implementation, no tests. Existing regression coverage confirmed in [RFC-011 D1](../rfcs/011-compliance-auth-quickwins.md#d1--iss-02-no-code-change-close-as-resolved).
- [D2](../rfcs/011-compliance-auth-quickwins.md#d2--iss-41-purge-preloadedfilename-in-the-erasure-cascade) (ISS-41) is a direct [HR2](../rfcs/011-compliance-auth-quickwins.md#hard-rule-constraints-claudemd--binding) violation — `preloaded/<filename>` raw objects written on ingest but never purged by the erasure cascade.
- [D3](../rfcs/011-compliance-auth-quickwins.md#d3--iss-40-statement-level-timeout-on-registry-delete) (ISS-40) reuses existing `registry_delete_timeout_s` config — no new configuration surface.
- [D4](../rfcs/011-compliance-auth-quickwins.md#d4--iss-32-bearer-auth-fails-closed-by-default) (ISS-32) is **behavior-changing**: deployments running with an unset bearer token in a trusted-network context will start rejecting requests until `MCP_ALLOW_UNAUTHENTICATED=true` is set or a token is configured. **Deploy-runbook callout required** per [RFC Risks](../rfcs/011-compliance-auth-quickwins.md#risks).
- [D5](../rfcs/011-compliance-auth-quickwins.md#d5--iss-35-agpl-fallback-observability-metric-only) (ISS-35) is metric-only scope — the hard gate (`PDF_CONVERTER_STRICT`) is explicitly deferred pending legal sign-off per [HR4](../rfcs/011-compliance-auth-quickwins.md#hard-rule-constraints-claudemd--binding).
- [D6](../rfcs/011-compliance-auth-quickwins.md#d6--iss-33-startup-zdr-routing-assertion-for-pii-flagged-corpora) (ISS-33) depends on a **reviewed ZDR allow-list** — get sign-off on the host list before merging, not after, per [RFC Risks](../rfcs/011-compliance-auth-quickwins.md#risks).

## Task Dependency Graph

```json
{
  "wave_0": { "tasks": ["0.1"], "description": "Close ISS-02 in audit tracker" },
  "wave_1": { "tasks": ["1.1", "1.2"], "description": "Auth fail-closed + preloaded purge (parallel, no dependency)" },
  "wave_2": { "tasks": ["1.3", "1.4", "2.1", "2.2"], "description": "Batch 1 tests + Batch 2 impl (parallel)" },
  "wave_3": { "tasks": ["2.3", "2.4", "3.1"], "description": "Batch 2 tests + ZDR startup assertion impl (parallel)" },
  "wave_4": { "tasks": ["3.2"], "description": "ZDR assertion tests" },
  "wave_5": { "tasks": ["4.0"], "description": "Final checkpoint — full suite, 3 consecutive green runs" }
}
```
