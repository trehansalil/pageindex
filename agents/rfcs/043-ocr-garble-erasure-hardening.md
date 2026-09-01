---
id: "RFC-043"
title: "OCR Recovery, Garble Defense & Erasure Hardening"
type: rfc
status: draft
date: "2026-09-01"
plan-impact: "no"
tags:
  - rfc
  - ocr-recovery
  - garble
  - erasure
  - compliance
aliases:
  - "RFC-043"
  - "OCR Garble Erasure"
governs:
  - "[[design-rfc043-ocr-garble-erasure-hardening]]"
  - "[[tasks-rfc043-ocr-garble-erasure-hardening]]"
supersedes: []
---

## Context

The POST-RFC041 architecture defect zones audit (2026-09-01) identified three subsystems with validated structural gaps that [[RFC-042]] does not cover: OCR recovery (Zone 1, 8 historical bugs), garble detection (Zone 3, 4 historical bugs), and right-to-erasure (Zone 7, 1 historical bug). RFC-041 closed several bugs in each zone, but validation confirmed remaining gaps that create silent failure modes.

Zone 1's most critical gap is a zero-content early return in `evaluate_gates` (verdict.py:175-176) that hard-fails documents with `node_count == 0` BEFORE any gate-specific recovery can run — image-only PDFs that would otherwise trigger OCR escalation are instead terminal-rejected. A secondary coupling between `_eligible_low_content` and `_eligible_image_dominant` via the shared `image_dominant_ocr_escalation_enabled` flag creates unintended kill-switch behavior.

Zone 3 is largely mitigated by RFC-041 D1/D2, but one backward-compat factory (`ScriptContext.from_script_str` at script.py:966) hardcodes `had_presentation_forms=False` with no compile-time enforcement — the last remaining path where the null-detector pattern could recur.

Zone 7's `_ERASURE_MANIFEST` (documents.py:551-622) presents as order-independent but has hidden data-flow dependencies: `ctx.doc_name` discovered in step 1, `ctx.sha256` readable only from a sidecar that a later step deletes. Steps with `required=False` silently degrade a purge to a no-op reporting success while leaving PII-derived artifacts intact.

### Relationship to Prior RFCs

- [[RFC-041]]: Closed Zone 1 Chains 9/15, Zone 3 Chains 13/21, Zone 7 Chain 17 structurally. This RFC addresses validated remaining gaps.
- [[RFC-042]]: Covers Zones 2, 4, 5, 6 (verdict computation + config). No overlap.
- [[RFC-040]]: Zone 1 Chain 9 (AGPL fallback) and Zone 3 Chain 21 (NFKC ordering) originally from RFC-040.

## Goals

- Close the zero-content early return bypass so image-only PDFs can reach OCR recovery
- Decouple OCR escalation eligibility predicates so disabling one recovery path does not silently disable another
- Eliminate the last `had_presentation_forms=False` hardcode and add compile-time enforcement
- Make erasure manifest ordering dependencies explicit and validated at import time
- Ensure erasure failures are loud — no silent degradation to partial purge

## Non-Goals

- Rewriting the full OCR recovery cascade or converter chain (RFC-041 D3/D4 scope)
- Restructuring garble detection beyond the ScriptContext PF enforcement (RFC-041 D1 scope)
- Full compliance audit of erasure across backup stores (CLAUDE.md HR2 scope, future RFC)
- Adding new OCR escalation paths or recovery strategies
- Changing the `_ERASURE_MANIFEST` step structure (ErasureStep dataclass stays)

## Glossary

| Term | Definition |
|------|------------|
| Zero-content early return | `evaluate_gates` short-circuit at verdict.py:175-176: `node_count == 0` → `hard_fail_verdict="FAIL"/"zero_content"` before Phase 2 recovery |
| Eligibility predicate | `_eligible_garble`, `_eligible_low_content`, `_eligible_image_dominant` (gates.py:277-327) — per-gate functions that decide whether a document qualifies for recovery |
| `image_dominant_ocr_escalation_enabled` | Config flag shared by `_eligible_low_content` and `_eligible_image_dominant`, creating kill-switch coupling |
| Null-detector pattern | Quality gate that structurally cannot fire on its real failure mode because the signal is destroyed before detection (Zone 3 NFKC → PF) |
| `ScriptContext.from_script_str` | Backward-compat factory at script.py:956-968 that hardcodes `had_presentation_forms=False` — last remaining null-detector entry point |
| `_ERASURE_MANIFEST` | 11 `ErasureStep` entries at documents.py:551-622 iterated by `delete_doc` for right-to-erasure cascade |
| Data-flow dependency | Implicit ordering requirement: step N produces a value step M consumes; reordering breaks M silently when `required=False` |

## Requirements

### Requirement 1: Zero-Content Recovery Bypass

**User Story:** As the indexing pipeline, I want zero-content documents to reach gate-specific recovery before verdict computation hard-fails them, so that image-only PDFs can trigger OCR escalation instead of being terminal-rejected.

#### Acceptance Criteria

**(Amendment 2026-09-01 v2:** Redesigned — `evaluate_gates` runs post-recovery, not pre-recovery. Fix point redirected to recovery predicates.)**
**(Amendment 2026-09-01 v3:** v3 review confirmed zero-content recovery already works correctly today. `_eligible_low_content` checks flags + defect membership only (no char threshold). The `low_content_ocr_char_floor=300` check is a *skip* guard inside `_recover_low_content_ocr` (recovery.py:453): `0 >= 300` = False → recovery proceeds. D1 reframed as test-coverage lock — no production code changes needed.)**

1. THE existing zero-content recovery flow SHALL be verified end-to-end: `validate_tree` → `NODE_COUNT_LOW` gate → `_eligible_low_content` returns True → `_recover_low_content_ocr` proceeds (char floor skip guard passes for `total_chars=0`).
2. A regression test SHALL lock the flow: zero-content doc → recovery fires → recovered doc passes `evaluate_gates` post-recovery.
3. A regression test SHALL verify: unrecoverable zero-content doc → recovery fails → `evaluate_gates` returns `hard_fail_verdict="FAIL"/"zero_content"` (correct post-recovery behavior).
4. An architecture guard SHALL verify `_eligible_low_content` does not check `total_chars` (the char floor check must remain inside the recovery function, not the eligibility predicate).

### Requirement 2: OCR Escalation Flag Decoupling

**User Story:** As an operator, I want to disable image-dominant OCR escalation without silently disabling low-content recovery, so that kill-switch coupling does not create unexpected side effects.

#### Acceptance Criteria

1. WHEN `image_dominant_ocr_escalation_enabled` is set to False, THE `_eligible_low_content` predicate SHALL still allow low-content recovery via its own dedicated flag (`ocr_escalation_low_content`).
2. THE `_eligible_low_content` predicate (gates.py:294-311) SHALL NOT OR-gate `image_dominant_ocr_escalation_enabled` as a fallback path.
3. THE `_eligible_image_dominant` predicate (gates.py:314-327) SHALL remain gated solely on `image_dominant_ocr_escalation_enabled`.
4. AN architecture guard test SHALL verify the two predicates do not share a config flag.

### Requirement 3: ScriptContext Presentation-Forms Enforcement

**User Story:** As a developer, I want compile-time enforcement that `ScriptContext` cannot be constructed with a wrong `had_presentation_forms` value, so that the null-detector pattern cannot recur.

#### Acceptance Criteria

1. THE `ScriptContext.from_script_str` factory (script.py:956-968) SHALL accept `had_presentation_forms` as a required parameter instead of hardcoding `False`.
2. IF no live production callers of `from_script_str` exist, THEN the factory MAY be deprecated or removed.
3. AN architecture guard test SHALL verify no `had_presentation_forms=False` literal construction exists in src/ outside test fixtures.

### Requirement 4: Erasure Manifest Ordering Validation

**User Story:** As a compliance engineer, I want the erasure manifest to validate data-flow ordering at import time, so that reordering steps cannot silently degrade a purge.

#### Acceptance Criteria

1. THE `validate_erasure_manifest` function (documents.py:644-678) SHALL validate data-flow ordering: steps that produce values (`ctx.doc_name`, `ctx.sha256`) must precede steps that consume them.
2. IF a new ErasureStep is added that depends on a value produced by a later step, THEN import-time validation SHALL raise an error.
3. THE ordering validation SHALL be declarative — each ErasureStep declares its `produces` and `consumes` fields.

### Requirement 5: Erasure Failure Loudness

**User Story:** As a compliance engineer, I want erasure failures to be loud and explicit, so that a partial purge cannot report success while leaving PII-derived artifacts intact.

#### Acceptance Criteria

1. WHEN a `required=False` step is skipped because a discovered value (`ctx.doc_name`, `ctx.sha256`) is missing, THE `delete_doc` function SHALL log the skip at WARNING level with the specific missing dependency.
2. THE `delete_doc` return value SHALL include a `partial_purge` flag when any step was skipped due to missing dependencies.
3. IF the verdicts step (step 2d) is skipped because `ctx.sha256` is missing, THE function SHALL attempt to discover `sha256` from an alternative source (e.g., registry row) before giving up.

## Decision Summary

### D1: Zero-Content Recovery Bypass (Requirement 1)

**(Amendment 2026-09-01 v3:** v3 review confirmed zero-content recovery already works correctly. No production code changes needed — reframed as test-coverage lock.)**

Zero-content recovery already works correctly today: `_eligible_low_content` gates on flags + defect membership (no char threshold), `_recover_low_content_ocr` has a skip guard (`total_chars >= low_content_ocr_char_floor=300`) that passes for `total_chars=0` (0 >= 300 is False → recovery proceeds). D1 adds regression tests and an architecture guard to lock this behavior and prevent future regressions. ~1h (reduced from ~3h → ~2h → ~1h — test-only, no production code changes).

### D2: OCR Escalation Flag Decoupling (Requirement 2)

**(Amendment 2026-09-01:** The OR-gate in `_eligible_low_content` is documented as intentional design (docstring: "Combined OR-gate"). This is an intentional behavior change, not a bug fix. Operators who set `OCR_ESCALATION_LOW_CONTENT=0` + `IMAGE_DOMINANT_OCR_ESCALATION_ENABLED=1` currently get NODE_COUNT_LOW recovery — after D2, they won't. `ocr_escalation_low_content` defaults to True (via `OCR_ESCALATION_GARBLE` at config.py:478-482), so the default deployment is unaffected.**)**

Remove the `image_dominant_ocr_escalation_enabled` OR-gate from `_eligible_low_content` (gates.py:309-310). This is an intentional decoupling — the OR-gate was a design convenience that created kill-switch coupling. `ocr_escalation_low_content` already defaults to True, so the default behavior is preserved. Operators who explicitly set `OCR_ESCALATION_LOW_CONTENT=0` and relied on the OR-gate fallback will need to enable the flag directly. Migration note required in release notes.

### D3: ScriptContext PF Enforcement (Requirement 3)

**(Amendment 2026-09-01:** `TestPresentationFormsNotHardcoded` (test_architecture_guards.py:498-582) already guards this via AST parsing. It exempts `script.py` via `ALLOWED_FILES = {"script.py"}`. D3 fixes the hardcode and removes the exemption — no new guard needed.**)**

Deprecate `ScriptContext.from_script_str` — validation confirmed zero live production callers. Add `warnings.warn(DeprecationWarning)` (matching existing pattern in queries.py:213) and make `had_presentation_forms` a required parameter. Remove the `ALLOWED_FILES = {"script.py"}` exemption from `TestPresentationFormsNotHardcoded`. This closes the last null-detector entry point.

### D4: Erasure Manifest Ordering Validation (Requirement 4)

**(Amendment 2026-09-01:** Validation found `_erase_verdicts` (step 2d) reads `ctx.sha256` from the sidecar internally — it's self-contained, not consuming from a prior step. Current ordering is correct. DAG model rethought to track sidecar-object-level dependencies, not just `ctx.*` fields.**)**

Extend `ErasureStep` with optional `reads: frozenset[str]` and `deletes: frozenset[str]` fields tracking sidecar-object-level dependencies (e.g., `reads={"processed/{id}.meta.json"}`, `deletes={"processed/{id}.meta.json"}`). Extend `validate_erasure_manifest` to verify that any step that `reads` a sidecar precedes the step that `deletes` it. Also retain `produces`/`consumes` for `ctx.*` field-level dependencies (`ctx.doc_name`). Import-time enforcement, zero runtime cost.

### D5: Erasure Failure Loudness (Requirement 5)

Upgrade `delete_doc` skip logging from DEBUG to WARNING with structured fields (`step_name`, `missing_dep`, `doc_id`). Add `partial_purge: bool` to the return dict. For the `ctx.sha256` dependency specifically, add a fallback lookup from the Postgres registry row before skipping the verdicts step. This ensures the most critical erasure step (verdict sidecar deletion) survives step-ordering failures.

## Implementation Plan

### Sequencing

1. **Phase 1: OCR Recovery** (D1, D2) — coupled within gate/recovery subsystem
2. **Phase 2: Garble Defense** (D3) — independent, can parallel with Phase 1
3. **Phase 3: Erasure Hardening** (D4, D5) — independent subsystem
4. **Phase 4: Integration Tests** — cross-zone regression validation

### Effort Estimate

| Phase | Deliverable | Effort | Risk |
|-------|------------|--------|------|
| 1 | D1: Zero-content recovery bypass | ~1h (revised from ~3h → ~2h → ~1h — test-only, no production code changes) | Low — regression tests + architecture guard only |
| 1 | D2: Flag decoupling | ~2h | Low — removing one OR clause |
| 2 | D3: ScriptContext PF enforcement | ~2h | Low — deprecation + guard |
| 3 | D4: Erasure ordering validation | ~3h | Medium — DAG validation design |
| 3 | D5: Erasure failure loudness | ~2h | Low — logging + fallback |
| 4 | Integration tests | ~3h | Low |
| **Total** | | **~13h** (revised from ~15h → ~14h → ~13h) | |

## Test Strategy

- D1: Golden-file test with a zero-content document that should reach OCR recovery
- D2: Test that disabling `image_dominant_ocr_escalation_enabled` does not affect low-content recovery
- D3: Architecture guard test: no `had_presentation_forms=False` in src/ outside test fixtures
- D4: Test that reordering erasure steps with unsatisfied dependencies fails at import time
- D5: Test that missing `ctx.doc_name` produces WARNING log and `partial_purge=True`

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| D1 recovery changes verdict outcomes for existing documents | Medium | Medium | Golden-file test against corpus; recovery only fires on node_count==0 (currently terminal-rejected anyway) |
| D2 flag removal changes operator behavior | Low | Low | `ocr_escalation_low_content` already exists as the primary flag; OR-gate was fallback only |
| D4 DAG validation rejects current manifest ordering | Low | High | Validate current ordering passes before adding the check |

## Consequences

- Image-only PDFs that currently hard-fail will get one recovery attempt before failing — expect some documents to shift from FAIL to MARGINAL/PASS
- Operators can independently toggle low-content and image-dominant recovery without side effects
- Any future erasure step must declare its data dependencies — prevents the Chain 17 pattern
- Partial purges become visible in logs and return values — compliance monitoring can alert on them

## Traceability

| Artifact | Reference |
|----------|-----------|
| Design | [[design-rfc043-ocr-garble-erasure-hardening]] |
| Tasks | [[tasks-rfc043-ocr-garble-erasure-hardening]] |
| Supersedes | N/A |
| Zone Specs | [[ocr-recovery-cascade-converter-fallback-chain]], [[garble-detection-nfkc-signal-destruction]], [[hr2-erasure-cascade-ordering]] |
