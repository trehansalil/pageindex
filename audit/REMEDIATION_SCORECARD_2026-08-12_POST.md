# Remediation Scorecard — POST (2026-08-12)

**Pre-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-19_POST-FIX-10.md  
**Post-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md  
**Delta report:** audit/ZONE_DELTA_2026-08-12_POST.md

## Verdict: REGRESSED

Despite closing one critical 12-bug zone (Picture/OCR Enrichment and Page-Level Escalation Conflation), the remediation cycle has regressed overall. Three previously stable or improved zones have degraded to regressed status (Tree/Flat Verdict Split, Converter-Gate-Route Ordering Chain, Arabic/RTL Pipeline Blindness), and two new high-severity zones surfaced after monolith decomposition exposed infrastructure boundaries and compliance gaps. The net bug count increased by 3 (50 → 53 bugs total). The pattern across 16 consecutive remediation cycles shows gate-hardening regressions and verdict fabrication outpacing genuine convergence; the next cycle must pause broadening mechanisms and focus on unifying fragmented logic paths and resolving Hard Rule compliance violations.

## Zones Closed (1)

| Name | Was Severity | Bugs Eliminated |
|---|---|---|
| Picture/OCR Enrichment and Page-Level Escalation Conflation | Critical | 12 |

## Zones Remaining (6)

| Name | Severity | Bug Count | Status |
|---|---|---|---|
| Tree/Flat Verdict Split | Critical | 11 | Regressed |
| Garble Detection Fragmentation | Critical | 12 | Stalled |
| Converter-Gate-Route Ordering Chain | Critical | 12 | Regressed |
| Worker-Child Process Boundary | High | 5 | Improved |
| Arabic/RTL Pipeline Blindness | High | 9 | Regressed |
| Duplicated Convergent Logic | Medium | 4 | Improved |

## New Zones (2)

| Name | Severity | Introduced By |
|---|---|---|
| Registry Dual-Write Consistency | High | Extracted from prior Cross-Process Error Classification Boundary zone; dual-write topology between MinIO sidecar and Postgres registry surfaced as independent defect zone with 7 bugs after monolith decomposition clarified the boundary |
| ZDR/PII Egress Gap | High | Newly identified: Hard Rule 3 ZDR enforcement is per-call-site opt-in; the two highest-volume LLM egress paths (_run_md_to_tree, _run_page_index_retrying) and LLM_FALLBACK_BASE_URL bypass zdr_egress_gate entirely. Conflict between RFC-004 VLM lock and RFC-016 VLM enablement unresolved. |

## Metrics

- **Net bug delta:** +3 (50 → 53 bugs)
- **Improved zones:** 2
- **Regressed zones:** 3
- **Stalled zones:** 1
- **New zones:** 2
- **Closed zones:** 1
- **Wiring status:** some_unwired
- **Unwired symbols:**
  - `scripts/gates/test-index-guard.sh` (commit 9e85650, not registered in scripts/eval.sh GATE_SCRIPTS table, not called by any .github/workflows/*.yml, not in any Makefile target)
  - `tests/TEST_INDEX.yaml` (only consumer is the unwired test-index-guard.sh script, so inert for enforcement)

## Recommended Next Steps

**STOP broadening gate/verdict logic and focus on convergence.** Three critical zones regressed, two new high-severity zones appeared, and the net bug count rose by 3 despite closing one 12-bug zone. Specific next steps:

1. **GARBLE DETECTION (stalled 6+ cycles, longest-stalled zone in project history):** Stop adding per-document heuristics; fix the three generative causes — move NFKC normalization AFTER morphology detection, eliminate expected_script self-corruption from corrupted text, replace per-call-site wiring with single choke-point dispatch.

2. **TREE/FLAT VERDICT SPLIT (regressed +2, escalated high→critical):** Unify the tree and flat gate evaluation into a single code path with a shared validation contract; the 7-gate vs 3-gate asymmetry is the root cause of oscillation.

3. **CONVERTER-GATE-ROUTE ORDERING CHAIN (regressed +1):** Wire decide_route re-evaluation after recovery mixins fix a defect; stale routing after recovery is the new dominant failure mode.

4. **Wire test-index-guard.sh into scripts/eval.sh GATE_SCRIPTS and CI before it rots further.**

5. **ZDR/PII EGRESS GAP is a Hard Rule 3 compliance violation** — add zdr_egress_gate to _run_md_to_tree and _run_page_index_retrying before any further LLM-calling changes.

6. **PAST CYCLE WARNING:** 16 remediation cycles have shown a pattern of gate-hardening regressions, verdict fabrication, and chronic document failures (cabinet_resolution_no_96: 8+ failed attempts, world-stats-pocketbook: unexplained 67% char drops). Each cycle broadens mechanisms faster than it closes them. The next cycle must be convergent, not expansive.
