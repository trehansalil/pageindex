<!-- Space: CITRA -->
<!-- Title: Implementation Plan: HR3 Egress Control Plane for PII Corpus Documents -->
<!-- Folder: Tasks -->

---
id: "tasks-rfc039-hr3-egress-control-plane"
title: "Tasks: HR3 Egress Control Plane for PII Corpus Documents"
type: tasks
status: draft
date: "2026-08-24"
tags:
  - tasks
  - compliance
  - hr3
  - pii
  - egress
  - security
aliases:
  - "tasks-rfc039-hr3-egress-control-plane"
governs:
  - "[[RFC-039]]"
---

# Implementation Plan: HR3 Egress Control Plane for PII Corpus Documents

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [RFC-039](../rfcs/039-hr3-egress-control-plane.md) |
| Design Document | [design-rfc039-hr3-egress-control-plane](../designs/design-rfc039-hr3-egress-control-plane.md) |
| PRD / Requirements | [[PRD]] |
| Hard Rules | [CLAUDE.md HR3](../rfcs/039-hr3-egress-control-plane.md#glossary) — "Route PII-bearing documents only through a no-training + zero-retention LLM tier" |
| Implementation Order | [RFC-039 §Decision Summary](../rfcs/039-hr3-egress-control-plane.md#decision-summary) |
| Test Strategy | [Design §Testing Strategy](../designs/design-rfc039-hr3-egress-control-plane.md#testing-strategy) |
| Correctness Properties | [Design §Correctness Properties](../designs/design-rfc039-hr3-egress-control-plane.md#correctness-properties) |

## Overview

This plan implements the HR3 egress control plane across four decisions: a shared boot gate extracted to `config.py` and called from both server and worker (D1), Docling remote egress gates (D2), a per-process-cached primary LLM gate (D3), and compliance-block observability via `ZDRComplianceError` and `HR3_EGRESS_BLOCKED_TOTAL` (D4). The implementation proceeds in three phases — shared infrastructure first, then per-call egress gates, then observability wiring — validating [4 correctness properties](../designs/design-rfc039-hr3-egress-control-plane.md#correctness-properties) at each checkpoint.

## Tasks

- [ ] <a id="1-phase-1--shared-gate-and-exception-d1-d4"></a>1. Phase 1 — Shared Gate and Exception ([D1](../rfcs/039-hr3-egress-control-plane.md#d1-shared-boot-gate--validate_hr3_compliance), [D4](../rfcs/039-hr3-egress-control-plane.md#d4-zdrcomplianceerror-and-observability))

  *[RFC-039 §D1](../rfcs/039-hr3-egress-control-plane.md#d1-shared-boot-gate--validate_hr3_compliance) + [§D4 exception subclass](../rfcs/039-hr3-egress-control-plane.md#d4-zdrcomplianceerror-and-observability)*

  - [ ] <a id="11-zdrcomplianceerror-and-validate-hr3-compliance-d1-d4"></a>1.1 Create `ZDRComplianceError` and `validate_hr3_compliance()` ([D1](../rfcs/039-hr3-egress-control-plane.md#d1-shared-boot-gate--validate_hr3_compliance), [D4](../rfcs/039-hr3-egress-control-plane.md#d4-zdrcomplianceerror-and-observability))

    - Define `ZDRComplianceError(RuntimeError)` in `config.py` for semantic distinction from generic errors
    - Extract HR3 validation logic from `server.py:73-94` into a shared `validate_hr3_compliance()` function in `config.py`
    - Extend the validation to check `openai_base_url`, `LLM_FALLBACK_BASE_URL` (if set), and `docling_service_url` (if set) against `_ZDR_ALLOW_PATTERNS`
    - Update `server.py` `_lifespan_with_scrape` to call the shared function instead of inline validation
    - _Requirements:_ [RFC-039 D1](../rfcs/039-hr3-egress-control-plane.md#d1-shared-boot-gate--validate_hr3_compliance) | [RFC-039 D4](../rfcs/039-hr3-egress-control-plane.md#d4-zdrcomplianceerror-and-observability) | [Design Property 1](../designs/design-rfc039-hr3-egress-control-plane.md#property-1-worker-boot-gate-hr3) | [Design Property 4](../designs/design-rfc039-hr3-egress-control-plane.md#property-4-compliance-observability) | [Design Service: config.py](../designs/design-rfc039-hr3-egress-control-plane.md#1-configpy)

  - [ ] <a id="12-worker-boot-gate-d1"></a>1.2 Wire worker boot gate ([D1](../rfcs/039-hr3-egress-control-plane.md#d1-shared-boot-gate--validate_hr3_compliance))

    - Call `validate_hr3_compliance()` from `worker/lifecycle.py` startup path
    - Ensure worker refuses to start when `pii_corpus=True` and any egress endpoint is not ZDR-allowlisted
    - Verify the shared function validates all three endpoints: `openai_base_url`, `LLM_FALLBACK_BASE_URL`, `docling_service_url`
    - _Requirements:_ [RFC-039 D1](../rfcs/039-hr3-egress-control-plane.md#d1-shared-boot-gate--validate_hr3_compliance) | [Design Property 1](../designs/design-rfc039-hr3-egress-control-plane.md#property-1-worker-boot-gate-hr3) | [Design Service: worker/lifecycle.py](../designs/design-rfc039-hr3-egress-control-plane.md#2-workerlifecyclepy) | [Design Sequence: Worker Boot Validation](../designs/design-rfc039-hr3-egress-control-plane.md#worker-boot-validation-flow--d1)

  - [ ]* <a id="13-test-boot-gate-d1"></a>1.3 Test boot gate ([D1](../rfcs/039-hr3-egress-control-plane.md#d1-shared-boot-gate--validate_hr3_compliance))

    - **Property 1: Worker Boot Gate HR3** — `test_worker_boot_rejects_non_zdr_endpoint`
    - Test `validate_hr3_compliance()` raises `RuntimeError` for non-ZDR `openai_base_url` when `pii_corpus=True`
    - Test `validate_hr3_compliance()` raises `RuntimeError` for non-ZDR `LLM_FALLBACK_BASE_URL` when `pii_corpus=True`
    - Test `validate_hr3_compliance()` raises `RuntimeError` for non-ZDR `docling_service_url` when `pii_corpus=True`
    - Test `validate_hr3_compliance()` passes when all endpoints are ZDR-allowlisted
    - Test `validate_hr3_compliance()` passes when `pii_corpus=False` regardless of endpoints
    - Test server.py and lifecycle.py both call the shared function (no independent reimplementation)
    - **Validates:** [Design Property 1](../designs/design-rfc039-hr3-egress-control-plane.md#property-1-worker-boot-gate-hr3) | [RFC-039 D1](../rfcs/039-hr3-egress-control-plane.md#d1-shared-boot-gate--validate_hr3_compliance) | [Design §Testing Strategy](../designs/design-rfc039-hr3-egress-control-plane.md#testing-strategy)

  - [ ] <a id="14-checkpoint--phase-1"></a>1.4 Checkpoint — Phase 1

    - Run `uv run pytest` and verify [Property 1](../designs/design-rfc039-hr3-egress-control-plane.md#property-1-worker-boot-gate-hr3) tests pass
    - Verify server.py no longer has inline HR3 validation (shared function only)
    - Verify `ZDRComplianceError` is importable from `config.py`
    - Ask the user if questions arise before proceeding

- [ ] <a id="2-phase-2--egress-gates-d2-d3"></a>2. Phase 2 — Egress Gates ([D2](../rfcs/039-hr3-egress-control-plane.md#d2-docling-remote-egress-gates), [D3](../rfcs/039-hr3-egress-control-plane.md#d3-primary-llm-per-call-gate))

  *[RFC-039 §D2](../rfcs/039-hr3-egress-control-plane.md#d2-docling-remote-egress-gates) + [§D3](../rfcs/039-hr3-egress-control-plane.md#d3-primary-llm-per-call-gate)*

  - [ ] <a id="21-docling-remote-egress-gates-d2"></a>2.1 Add Docling remote egress gates ([D2](../rfcs/039-hr3-egress-control-plane.md#d2-docling-remote-egress-gates))

    - Add `if settings.pii_corpus: require_zdr_compliance(settings.docling_service_url, "Docling remote PDF conversion")` at the top of `_remote_pdf_to_markdown` in `client/remote.py`
    - Add `if settings.pii_corpus: require_zdr_compliance(settings.docling_service_url, "Docling remote image conversion")` at the top of `_remote_image_to_markdown` in `client/remote.py`
    - Let `RuntimeError` / `ZDRComplianceError` propagate to the caller — do NOT catch silently
    - _Requirements:_ [RFC-039 D2](../rfcs/039-hr3-egress-control-plane.md#d2-docling-remote-egress-gates) | [Design Property 2](../designs/design-rfc039-hr3-egress-control-plane.md#property-2-docling-egress-gate) | [Design Service: client/remote.py](../designs/design-rfc039-hr3-egress-control-plane.md#3-clientremotepy) | [Design Sequence: Document Egress Flow](../designs/design-rfc039-hr3-egress-control-plane.md#document-egress-flow--d2--d3--d4)

  - [ ] <a id="22-primary-llm-per-call-gate-d3"></a>2.2 Add primary LLM per-call gate ([D3](../rfcs/039-hr3-egress-control-plane.md#d3-primary-llm-per-call-gate))

    - Add a per-process-cached `require_zdr_compliance` call at the top of `_llm_with_retry` in `client/llm.py` for the primary `base_url`
    - Cache result in a module-level flag (`_primary_zdr_verified: bool = False`) so the check runs once per process
    - Guard with `if settings.pii_corpus and not _primary_zdr_verified:` to avoid unnecessary checks in non-PII deployments
    - The fallback path's existing check at `llm.py:118` remains unchanged
    - _Requirements:_ [RFC-039 D3](../rfcs/039-hr3-egress-control-plane.md#d3-primary-llm-per-call-gate) | [Design Property 3](../designs/design-rfc039-hr3-egress-control-plane.md#property-3-primary-llm-gate) | [Design Service: client/llm.py](../designs/design-rfc039-hr3-egress-control-plane.md#4-clientllmpy) | [Design Sequence: Document Egress Flow](../designs/design-rfc039-hr3-egress-control-plane.md#document-egress-flow--d2--d3--d4)

  - [ ]* <a id="23-test-egress-gates-d2-d3"></a>2.3 Test egress gates ([D2](../rfcs/039-hr3-egress-control-plane.md#d2-docling-remote-egress-gates), [D3](../rfcs/039-hr3-egress-control-plane.md#d3-primary-llm-per-call-gate))

    - **Property 2: Docling Egress Gate** — `test_docling_pdf_blocked_when_pii_and_non_zdr`
    - Test `_remote_pdf_to_markdown` raises when `pii_corpus=True` and `docling_service_url` is not ZDR-allowlisted
    - Test `_remote_image_to_markdown` raises when `pii_corpus=True` and `docling_service_url` is not ZDR-allowlisted
    - Test both functions proceed normally when `pii_corpus=False`
    - Test both functions proceed normally when `pii_corpus=True` and `docling_service_url` is ZDR-allowlisted
    - **Property 3: Primary LLM Gate** — `test_primary_llm_blocked_when_pii_and_non_zdr`
    - Test `_llm_with_retry` raises on first call when `pii_corpus=True` and primary `base_url` is not ZDR-allowlisted
    - Test the per-process cache flag prevents redundant checks on subsequent calls
    - Test `_llm_with_retry` proceeds normally when `pii_corpus=False`
    - **Validates:** [Design Property 2](../designs/design-rfc039-hr3-egress-control-plane.md#property-2-docling-egress-gate) | [Design Property 3](../designs/design-rfc039-hr3-egress-control-plane.md#property-3-primary-llm-gate) | [RFC-039 D2](../rfcs/039-hr3-egress-control-plane.md#d2-docling-remote-egress-gates) | [RFC-039 D3](../rfcs/039-hr3-egress-control-plane.md#d3-primary-llm-per-call-gate) | [Design §Testing Strategy](../designs/design-rfc039-hr3-egress-control-plane.md#testing-strategy)

  - [ ] <a id="24-checkpoint--phase-2"></a>2.4 Checkpoint — Phase 2

    - Run `uv run pytest` and verify [Property 2](../designs/design-rfc039-hr3-egress-control-plane.md#property-2-docling-egress-gate) and [Property 3](../designs/design-rfc039-hr3-egress-control-plane.md#property-3-primary-llm-gate) tests pass
    - Verify all three ungated egress paths from the audit are now gated: primary LLM, Docling PDF, Docling image
    - Verify non-PII deployments (`pii_corpus=False`) are completely unaffected
    - Ask the user if questions arise before proceeding

- [ ] <a id="3-phase-3--observability-and-validation-d4"></a>3. Phase 3 — Observability and Validation ([D4](../rfcs/039-hr3-egress-control-plane.md#d4-zdrcomplianceerror-and-observability))

  *[RFC-039 §D4](../rfcs/039-hr3-egress-control-plane.md#d4-zdrcomplianceerror-and-observability)*

  - [ ] <a id="31-compliance-block-observability-d4"></a>3.1 Wire compliance block observability ([D4](../rfcs/039-hr3-egress-control-plane.md#d4-zdrcomplianceerror-and-observability))

    - Update `zdr_egress_gate` in `converters/pictures.py` to raise `ZDRComplianceError` instead of bare `RuntimeError`
    - Update the `except Exception` handler in `client/indexer.py:779-809` to catch `ZDRComplianceError` separately from other exceptions
    - Log compliance blocks as compliance events (not service errors)
    - Label the metric with `result='compliance_blocked'` instead of `result='error'` for compliance blocks
    - Add `HR3_EGRESS_BLOCKED_TOTAL` counter in `metrics/definitions.py` with labels `(path=docling_pdf|docling_image|vlm|llm_primary|llm_fallback)`
    - Increment `HR3_EGRESS_BLOCKED_TOTAL` at each egress gate block point: Docling PDF, Docling image, VLM, primary LLM, fallback LLM
    - _Requirements:_ [RFC-039 D4](../rfcs/039-hr3-egress-control-plane.md#d4-zdrcomplianceerror-and-observability) | [Design Property 4](../designs/design-rfc039-hr3-egress-control-plane.md#property-4-compliance-observability) | [Design Service: converters/pictures.py](../designs/design-rfc039-hr3-egress-control-plane.md#5-converterspicturespy) | [Design Service: client/indexer.py](../designs/design-rfc039-hr3-egress-control-plane.md#6-clientindexerpy) | [Design Service: metrics/definitions.py](../designs/design-rfc039-hr3-egress-control-plane.md#7-metricsdefinitionspy)

  - [ ]* <a id="32-test-observability-d4"></a>3.2 Test compliance observability ([D4](../rfcs/039-hr3-egress-control-plane.md#d4-zdrcomplianceerror-and-observability))

    - **Property 4: Compliance Observability** — `test_compliance_block_distinguished_from_api_error`
    - Test `zdr_egress_gate` raises `ZDRComplianceError` (not bare `RuntimeError`)
    - Test the indexer's `except` handler catches `ZDRComplianceError` separately and logs it as a compliance event
    - Test the VLM fallback metric uses `result='compliance_blocked'` for compliance blocks
    - Test the VLM fallback metric uses `result='error'` for genuine API failures
    - Test `HR3_EGRESS_BLOCKED_TOTAL` counter increments with correct `path` labels for each egress point
    - **Validates:** [Design Property 4](../designs/design-rfc039-hr3-egress-control-plane.md#property-4-compliance-observability) | [RFC-039 D4](../rfcs/039-hr3-egress-control-plane.md#d4-zdrcomplianceerror-and-observability) | [Design §Testing Strategy](../designs/design-rfc039-hr3-egress-control-plane.md#testing-strategy)

  - [ ] <a id="33-integration-tests"></a>3.3 Integration tests

    - End-to-end test: worker startup with `pii_corpus=True` and non-ZDR endpoints fails before any job is accepted
    - End-to-end test: full ingestion pipeline with `pii_corpus=True` and all ZDR endpoints succeeds
    - End-to-end test: full ingestion pipeline with `pii_corpus=False` and non-ZDR endpoints succeeds (no gates fire)
    - Verify `HR3_EGRESS_BLOCKED_TOTAL` is exposed on the Prometheus `/metrics` endpoint
    - **Validates:** [Design Property 1](../designs/design-rfc039-hr3-egress-control-plane.md#property-1-worker-boot-gate-hr3) | [Design Property 2](../designs/design-rfc039-hr3-egress-control-plane.md#property-2-docling-egress-gate) | [Design Property 3](../designs/design-rfc039-hr3-egress-control-plane.md#property-3-primary-llm-gate) | [Design Property 4](../designs/design-rfc039-hr3-egress-control-plane.md#property-4-compliance-observability) | [Design §Testing Strategy](../designs/design-rfc039-hr3-egress-control-plane.md#testing-strategy)

  - [ ] <a id="34-final-checkpoint"></a>3.4 Final Checkpoint

    - Run `uv run pytest` and verify all 4 properties pass
    - Verify zero `{{placeholder}}` tokens remain in any modified file
    - Verify non-PII deployments are completely unaffected (all gates behind `if settings.pii_corpus:`)
    - Confirm all 3 previously ungated egress paths (primary LLM, Docling PDF, Docling image) are now gated
    - Confirm VLM compliance blocks are distinguishable from API errors in metrics
    - Ask the user if questions arise before proceeding

## Notes

- [D1](../rfcs/039-hr3-egress-control-plane.md#d1-shared-boot-gate--validate_hr3_compliance): The shared `validate_hr3_compliance()` function replaces the inline validation in `server.py:73-94`. Both server and worker call the same function — no independent reimplementation.
- [D2](../rfcs/039-hr3-egress-control-plane.md#d2-docling-remote-egress-gates): The Docling remote gates are guarded by `if settings.pii_corpus:` so non-PII deployments are unaffected. The `RuntimeError` / `ZDRComplianceError` propagates to the caller — callers already handle conversion failures.
- [D3](../rfcs/039-hr3-egress-control-plane.md#d3-primary-llm-per-call-gate): The per-process-cached flag (`_primary_zdr_verified`) means that if `openai_base_url` is changed after process startup, the cached check is stale. This is an [accepted limitation](../rfcs/039-hr3-egress-control-plane.md#consequences) — the `Settings` mutability concern is out of scope.
- [D4](../rfcs/039-hr3-egress-control-plane.md#d4-zdrcomplianceerror-and-observability): `ZDRComplianceError` is defined in Phase 1 (shared infrastructure) but fully wired for observability in Phase 3. This sequencing allows Phase 2 egress gates to raise the exception immediately while deferring the metric/logging wiring.
- PII deployments with a non-ZDR Docling service will fail to start the worker after [D1](../rfcs/039-hr3-egress-control-plane.md#d1-shared-boot-gate--validate_hr3_compliance). Operators must either add the Docling endpoint to the ZDR allow-list or run Docling locally.
- Tasks marked with `*` are property-based tests — optional for faster MVP but recommended for compliance-critical code.
- Extends [RFC-011 D6](../rfcs/039-hr3-egress-control-plane.md#traceability) (boot-time HR3 gate in server.py).

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "label": "Shared infrastructure",
      "tasks": ["1.1"],
      "depends_on": []
    },
    {
      "id": 1,
      "label": "Worker boot gate",
      "tasks": ["1.2"],
      "depends_on": ["1.1"]
    },
    {
      "id": 2,
      "label": "Boot gate tests + checkpoint",
      "tasks": ["1.3", "1.4"],
      "depends_on": ["1.2"]
    },
    {
      "id": 3,
      "label": "Egress gates (parallel)",
      "tasks": ["2.1", "2.2"],
      "depends_on": ["1.4"]
    },
    {
      "id": 4,
      "label": "Egress gate tests + checkpoint",
      "tasks": ["2.3", "2.4"],
      "depends_on": ["2.1", "2.2"]
    },
    {
      "id": 5,
      "label": "Observability wiring",
      "tasks": ["3.1"],
      "depends_on": ["2.4"]
    },
    {
      "id": 6,
      "label": "Observability tests + integration",
      "tasks": ["3.2", "3.3"],
      "depends_on": ["3.1"]
    },
    {
      "id": 7,
      "label": "Final checkpoint",
      "tasks": ["3.4"],
      "depends_on": ["3.2", "3.3"]
    }
  ]
}
```
