# Remediation Scorecard — POST-FIX-6 (2026-08-18)

**Pre-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-17_POST-FIX.md
**Post-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-18_POST-FIX-6.md
**Delta report:** audit/ZONE_DELTA_2026-08-12_POST-FIX-6.md

## Verdict: NEEDS ANOTHER CYCLE

Fix-6 closed two zones outright — the Arabic/RTL bolt-on architecture (6 bugs, high severity) and the `pdf_to_markdown_docling` god-function orchestration (4 bugs, medium severity) — and improved four of the six remaining zones (Garble Detection, Picture/OCR Dual-Path, Cross-Process Verdict Races, Duplicated Thresholds), for a net bug delta of -6 (some counts reported as -5 in the delta summary depending on new-zone attribution) with all symbols wired. However, the two CRITICAL zones — Dual Verdict Authority and Recovery Pipeline Implicit Ordering — remain STALLED for a second straight cycle, and two new zones surfaced from reclassifying prior corpus findings: Splitter Pattern Fragility (high) and Silent Fallback Chains (high, carrying Hard Rule 4 AGPL compliance exposure via pymupdf4llm). The chronic pattern of unwired implementations discovered only on the next audit did not repeat this cycle (wiring_status: all_wired), which is real progress, but the stalled critical zones mean the core verdict-authority and recovery-pipeline defects that have resisted fixes across 5 consecutive cycles are still open. Another remediation cycle is required before this can be considered production-ready.

## Zones Closed (2)

| Zone | Was Severity | Bugs Eliminated |
|---|---|---|
| Arabic/RTL Pipeline Bolt-On Architecture | high | 6 |
| God Function Orchestration (`pdf_to_markdown_docling`) | medium | 4 |

## Zones Remaining (6)

| Zone | Severity | Bug Count | Status |
|---|---|---|---|
| Garble Detection Surface Sprawl | critical | 8 | improved |
| Dual Verdict Authority (`validate_tree` vs `classify_verdict`) | critical | 6 | **stalled** |
| Recovery Pipeline Implicit Ordering and State Mutation | critical | 6 | **stalled** |
| Picture/OCR Recovery Dual-Path Conflation | high | 6 | improved |
| Cross-Process Verdict/Registry Write Races | high | 4 | improved |
| Duplicated Threshold/Logic Definitions Across Files | medium | 3 | improved |

## New Zones (2)

| Zone | Severity | Introduced By |
|---|---|---|
| Splitter Pattern Fragility and Giant Tail-Blob Recurrence | high | Reclassified from prior corpus findings (RFC-005 Fix-1, Observations #4129/#4148/#5637). Pre-existing defect newly tracked as a zone; not introduced by fix commits. 5 bugs. |
| Silent Fallback Chains Masking Compliance and Quality Failures | high | **RED FLAG (compliance):** Split from prior Zone 8 (Env-Var Flag Proliferation). Pre-existing defect surface (BIDI_ROOT_CAUSE_RFC033 C-2, ISS-34, registry dual-write swallow) newly isolated as its own zone; not introduced by fix commits. 4 bugs. Carries Hard Rule 4 (AGPL) compliance risk via unconditional pymupdf4llm seeding in the converter chain. |

## Metrics

- Net bug delta: **-6** (delta report: -5)
- Total current bugs: 8 zones tracked / prior 8 zones tracked
- Improved: 4 · Regressed: 0 · Stalled: 2 · New: 2 · Closed: 2
- Wiring status: **all_wired**
- Unwired symbols: none

## Recommended Next Steps

**Priority 1 — Address the two STALLED CRITICAL zones** that have resisted improvement across 5 consecutive cycles:

(a) **Zone 2 "Dual Verdict Authority"**: `validate_tree` and `classify_verdict` remain independent verdict engines that can disagree. Past cycles show threshold ratcheting (`PASS_MAX_LEAF_RATIO` 0.15 → 0.30 across 4 RFCs) is pure symptom management. Root fix: merge `validate_tree` gate results INTO `classify_verdict` as input signals rather than independent overrides, eliminating the dual-engine disagreement surface.

(b) **Zone 3 "Recovery Pipeline Implicit Ordering"**: the 7-step mutable-state waterfall continues producing unwired implementations each cycle (RFC-027 D7, RFC-029 D0/D6 in past; D0 now deleted but pattern recurs). Root fix: convert the implicit method-call-order pipeline to a declarative recovery-step registry (analogous to the `GateSpec` consolidation that closed the `REASON_POLICY` wiring gap in Zone 2).

**Priority 2 — Close the compliance-relevant new zone**: Zone 7 "Silent Fallback Chains" carries Hard Rule 4 (AGPL) exposure via pymupdf4llm unconditionally seeded in the converter chain. This is not a code-quality issue but a legal/compliance risk that should be gated before the next production deployment.

**Priority 3 — Harden the 4 improved zones.** Garble Detection, Picture/OCR, Verdict Races, and Duplicated Thresholds have positive trajectory but each still carries 3-8 bugs. The Garble Detection zone (critical, 8 bugs) has the worst regression history (3+ generations: RFC-020 F2 → RFC-021 QF1 → RFC-023 D3 → RFC-028 D2 → RFC-029 D3). Its next fix should include a regression gate: any garble-prong change must pass the full 25-doc corpus before merge.

**Chronic pattern to break**: 5 of 5 past cycles produced unwired implementations discovered only on the next audit. The `GateSpec` single-source-of-truth pattern (`helpers.py:1844`) that resolved `REASON_POLICY` should be replicated for recovery-step wiring (Zone 3) and verdict-authority consolidation (Zone 2) to make unwired additions structurally impossible rather than audit-detectable.
