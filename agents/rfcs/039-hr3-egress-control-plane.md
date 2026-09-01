<!-- Space: CITRA -->
<!-- Title: RFC-039: Hr3 Egress Control Plane -->
<!-- Folder: RFCs -->

---
id: "RFC-039"
title: "HR3 Egress Control Plane for PII Corpus Documents"
type: rfc
status: draft
date: "2026-08-24"
plan-impact: "yes"
tags:
  - rfc
  - compliance
  - hr3
  - pii
  - egress
  - security
aliases:
  - "RFC-039"
  - "HR3-Egress-Control-Plane"
governs:
  - "[[design-rfc039-hr3-egress-control-plane]]"
  - "[[tasks-rfc039-hr3-egress-control-plane]]"
supersedes: []
---

## Context

The post-fix-11 architecture defect zones audit (2026-08-24) identified Zone 8 as a medium-severity compliance defect zone (4 bugs). While the bug count is modest, the zone is compliance-critical under CLAUDE.md Hard Rule 3 (HR3): *"Route PII-bearing documents only through a no-training + zero-retention LLM tier."*

The current HR3 enforcement architecture relies on scattered point guards rather than a unified egress control plane. The boot-time HR3 gate (`_lifespan_with_scrape` in `server.py:73-94`) runs **only** in the MCP server process; the arq worker process (`worker/lifecycle.py:51-79`) has **zero** ZDR/`pii_corpus` checks. All document ingestion — including Docling remote conversion and LLM tree-generation calls — happens exclusively in the worker process, meaning the gate that exists protects nothing that touches documents.

The audit mapped 10 egress paths across the pipeline. Six are gated, one is adjacent (Langfuse), and **three are ungated**:

1. **Primary tree-generation LLM** — `_llm_with_retry` (`client/llm.py:49-135`) calls `require_zdr_compliance` only on the fallback branch (line 118). The primary attempt has no per-call ZDR check.
2. **Docling remote PDF conversion** — `_remote_pdf_to_markdown` (`client/remote.py:67-111`) builds a presigned URL to the raw PDF and POSTs it to the Docling service with zero compliance validation.
3. **Docling remote image conversion** — the image conversion path in `remote.py` similarly has no ZDR gate.

Additionally, the VLM compliance block raised by `zdr_egress_gate` (`converters/pictures.py:175-199`) is caught by the same `except Exception` handler as genuine API failures in `client/indexer.py:779-809`. Both paths increment `VLM_FALLBACK_TOTAL.labels(result='error')`, making compliance blocks indistinguishable from service outages in metrics and alerting.

The ZDR allow-list (`_ZDR_ALLOW_PATTERNS` in `config.py:185-220`) and the per-call gate (`require_zdr_compliance(base_url, purpose)`) are established patterns that work correctly where applied — the problem is that they are not applied at every egress point.

Prior RFC: [[RFC-011]] D6 introduced the boot-time HR3 gate in `server.py`.

## Goals

- Add HR3 ZDR enforcement to the worker process startup (`lifecycle.py`), matching the server's boot gate.
- Gate the Docling remote egress paths (PDF and image) with `require_zdr_compliance` checks when `pii_corpus=True`.
- Add a per-call ZDR check to the primary LLM path in `_llm_with_retry`, not just the fallback path.
- Distinguish compliance-blocked VLM calls from genuine API failures in metrics and logs.

## Non-Goals

- Adding ZDR enforcement to Langfuse tracing egress. Langfuse is a separate telemetry system with its own data governance; it is adjacent to but not part of the document processing pipeline.
- Redesigning the `Settings` dataclass mutability concern. While `config.settings` is a rebindable module attribute that can technically bypass `@dataclass(frozen=True)`, the fix for that is an application-wide immutability pattern, not HR3-specific.
- Implementing a unified egress proxy or network-level PII filter. This RFC adds code-level gates at each egress point; a network-level solution is a future infrastructure decision.
- Retroactively auditing whether PII documents were previously sent to ungated endpoints. That is an operational incident review, not a code fix.

## Glossary

| Term | Definition |
|------|------------|
| HR3 | Hard Rule 3 from CLAUDE.md: "Route PII-bearing documents only through a no-training + zero-retention LLM tier" |
| ZDR | Zero-Data-Retention — an LLM deployment tier that does not retain request/response data for training |
| PII_Corpus | A deployment configuration (`pii_corpus=True` in Settings) indicating the document corpus contains personally identifiable information |
| ZDR_Allow_List | A set of URL patterns (`_ZDR_ALLOW_PATTERNS` in `config.py`) known to be ZDR-compliant endpoints |
| Egress_Path | Any code path that sends document content (text, PDF bytes, image bytes) to an external service |
| Boot_Gate | A startup-time check that validates configuration invariants and refuses to start if violated |
| ZDRComplianceError | A new `RuntimeError` subclass raised when an egress path is blocked by HR3 compliance checks |

## Requirements

### Requirement 1: Worker Process HR3 Boot Gate

**User Story:** As a compliance officer, I want the worker process to refuse to start when PII corpus mode is enabled and any egress endpoint is not ZDR-allowlisted, so that no document ingestion can proceed without validated compliance.

#### Acceptance Criteria

1. WHEN `pii_corpus=True` AND `openai_base_url` is not on the ZDR allow-list, THE Worker_Startup function (`lifecycle.py`) SHALL raise `RuntimeError` and refuse to start.
2. WHEN `pii_corpus=True` AND `LLM_FALLBACK_BASE_URL` is configured and not ZDR-allowlisted, THE Worker_Startup function SHALL raise `RuntimeError` and refuse to start.
3. WHEN `pii_corpus=True` AND `DOCLING_SERVICE_URL` is configured and not ZDR-allowlisted, THE Worker_Startup function SHALL raise `RuntimeError` and refuse to start.
4. THE Worker Boot_Gate checks SHALL share validation logic with the server.py boot gate via a common `validate_hr3_compliance()` function — no independent reimplementation.

### Requirement 2: Docling Remote Egress Gate

**User Story:** As a compliance officer, I want raw PDF and image data sent to external Docling services to be subject to HR3 compliance checks, so that PII documents are never sent to non-ZDR Docling endpoints.

#### Acceptance Criteria

1. WHEN `pii_corpus=True`, THE `_remote_pdf_to_markdown` function (`client/remote.py`) SHALL call `require_zdr_compliance(settings.docling_service_url, "Docling remote PDF conversion")` before sending the presigned URL.
2. WHEN `pii_corpus=True`, THE `_remote_image_to_markdown` function (`client/remote.py`) SHALL call `require_zdr_compliance(settings.docling_service_url, "Docling remote image conversion")` before sending image data.
3. IF `require_zdr_compliance` raises, THE `RuntimeError` SHALL propagate to the caller — it SHALL NOT be caught silently.

### Requirement 3: Primary LLM Per-Call Gate

**User Story:** As a compliance officer, I want every LLM call — not just fallback retries — to validate ZDR compliance, so that the primary LLM path cannot egress PII to a non-ZDR endpoint.

#### Acceptance Criteria

1. WHEN `pii_corpus=True`, THE `_llm_with_retry` function SHALL call `require_zdr_compliance` on the primary `base_url` before the first attempt — not only on the fallback path.
2. THE per-call check MAY be cached per-process (checked once on first invocation) to avoid redundant validation on every request, since the `base_url` is stable within a process lifetime.

### Requirement 4: Compliance Block Observability

**User Story:** As a pipeline operator, I want compliance-blocked VLM calls to be distinguishable from genuine API failures in metrics and logs, so that I can tell whether quality degradation is caused by policy enforcement or service outages.

#### Acceptance Criteria

1. WHEN `zdr_egress_gate` blocks a VLM call due to HR3 compliance, THE metric label SHALL be `result='compliance_blocked'` — not `result='error'`.
2. THE `except Exception` block in `client/indexer.py` that catches VLM failures SHALL distinguish `ZDRComplianceError` from other exceptions and log it as a compliance event, not a service error.
3. A new Prometheus counter `HR3_EGRESS_BLOCKED_TOTAL` with labels `(path=docling_pdf|docling_image|vlm|llm_primary|llm_fallback)` SHOULD be added to track all compliance blocks across the pipeline.

## Decision Summary

This RFC closes the HR3 PII egress gaps by adding the worker process boot gate (D1), Docling remote per-call checks (D2), primary LLM per-call check (D3), and compliance-block observability (D4). The implementation introduces a `ZDRComplianceError(RuntimeError)` exception subclass to distinguish compliance blocks from generic errors, and adds an `HR3_EGRESS_BLOCKED_TOTAL` counter for unified egress monitoring. Non-PII deployments (`pii_corpus=False`, the default) are completely unaffected — all runtime gates are behind `if settings.pii_corpus:` guards.

### D1: Shared Boot Gate — `validate_hr3_compliance()`

Extract the HR3 validation logic from `server.py:73-94` into a shared `validate_hr3_compliance()` function in `config.py`. Extend the check list to include `DOCLING_SERVICE_URL`. Call this function from both `server.py` (`_lifespan_with_scrape`) and `worker/lifecycle.py` (worker startup). The shared function validates `openai_base_url`, `LLM_FALLBACK_BASE_URL` (if set), and `docling_service_url` (if set) against `_ZDR_ALLOW_PATTERNS`.

### D2: Docling Remote Egress Gates

Add `require_zdr_compliance(settings.docling_service_url, ...)` calls at the top of `_remote_pdf_to_markdown` and `_remote_image_to_markdown` in `client/remote.py`. Guard with `if settings.pii_corpus:` to avoid unnecessary checks in non-PII deployments. Let the `RuntimeError` propagate — callers already handle conversion failures.

### D3: Primary LLM Per-Call Gate

Add a per-process-cached `require_zdr_compliance` call at the top of `_llm_with_retry` for the primary `base_url`. Cache the result in a module-level flag (`_primary_zdr_verified: bool = False`) so the check runs once per process, not per call. The fallback path's existing check at `llm.py:118` remains unchanged.

### D4: ZDRComplianceError and Observability

Create `ZDRComplianceError(RuntimeError)` in `config.py`. Update `zdr_egress_gate` in `converters/pictures.py` to raise this specific subclass instead of bare `RuntimeError`. Update the `except Exception` handler in `client/indexer.py:779-809` to catch `ZDRComplianceError` separately, logging it as a compliance event and labeling the metric with `result='compliance_blocked'`. Add an `HR3_EGRESS_BLOCKED_TOTAL` counter in `metrics/definitions.py` with a `path` label to track blocks across all egress points.

## Consequences

- **Non-PII deployments** (the default) are completely unaffected; all gates are behind `if settings.pii_corpus:`.
- **PII deployments with a non-ZDR Docling service** will fail to start the worker. The operator must either add the Docling endpoint to the ZDR allow-list or run Docling locally (no remote egress).
- **ZDRComplianceError** enables future egress points to raise a semantically clear exception that callers can handle differently from generic errors.
- **Shared `validate_hr3_compliance()`** in `config.py` becomes the single place to add new egress endpoints for HR3 validation, reducing the risk of future point-guard omissions.
- **Per-process-cached primary LLM check** (D3) means that if `openai_base_url` is changed after process startup (via the rebindable `config.settings` module attribute), the cached check would be stale. This is an accepted limitation; the `Settings` mutability concern is out of scope.
- **Monitoring improvement:** The `HR3_EGRESS_BLOCKED_TOTAL` counter and the `compliance_blocked` metric label allow operators to distinguish policy enforcement from service degradation in dashboards and alerts.

## Traceability

| Artifact | Reference |
|----------|-----------|
| Design | [[design-rfc039-hr3-egress-control-plane]] |
| Tasks | [[tasks-rfc039-hr3-egress-control-plane]] |
| Supersedes | N/A |
| Extends | [[RFC-011]] D6 (boot-time HR3 gate in server.py) |
| Audit | [ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-24_POST-FIX-11.md](../../audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-24_POST-FIX-11.md) Zone 8 |
| Hard Rule | CLAUDE.md HR3: "Route PII-bearing documents only through a no-training + zero-retention LLM tier" |
