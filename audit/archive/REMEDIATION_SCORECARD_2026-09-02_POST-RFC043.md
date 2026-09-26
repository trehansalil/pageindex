# Remediation Scorecard — POST-RFC043 (2026-09-02)

**Pre-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-01_POST-RFC041.md  
**Post-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-02_POST-RFC043.md  
**Delta report:** audit/ZONE_DELTA_2026-09-02_POST-RFC043.md

## Verdict: REGRESSED

RFC-043 OCR recovery and erasure hardening work closed 2 high-priority zones (HR2 Erasure Cascade + Content Measurement) and improved 1 zone (Verdict Promotion), but uncovered 3 critical new zones (Gate-to-Recovery Dispatch Wiring Gap, Garble Detection NFKC Signal Destruction, Table-Unaware Pre-Tree Text Transforms) and regressed 3 existing zones (OCR Pipeline Decision, Verdict Persistence Dual-Writer, Config Snapshot/Live-Read Divergence) for a net +28 bug delta. The dominant pattern is unfinished wiring: validate_feature_wirings and recovery dispatch methods declared in GateSpec but never invoked at runtime — a recurrence of the RFC-041 dead-dispatch defect flagged two cycles ago and still unresolved.

## Zones Closed (2)

| Zone | Was Severity | Bugs Eliminated |
|------|--------------|-----------------|
| HR2 Erasure Cascade Hidden Ordering Dependencies | high | 3 |
| Content Measurement Blind Spot (Table Block Text Extraction) | medium | 2 |

## Zones Remaining (4)

| Zone | Severity | Bug Count | Status |
|------|----------|-----------|--------|
| OCR Pipeline Decision & Recovery Cascade | critical | 12 | regressed |
| Verdict Persistence Dual-Writer & Hysteresis Fragility | high | 6 | regressed |
| Verdict Promotion & Hard-Rule-5 Bypass Cascade | high | 7 | improved |
| Config Snapshot vs Live-Read Divergence & Remote/Local Execution Divergence | medium | 4 | regressed |

## New Zones (3)

| Zone | Severity | Introduced By |
|------|----------|---------------|
| Gate-to-Recovery Dispatch Wiring Gap | critical | RFC-043 audit discovered validate_feature_wirings defined but never called from production code; recovery dispatch methods declared in GateSpec.recovery_fns have zero inbound callers at runtime |
| Garble Detection NFKC Signal Destruction | high | RFC-043 audit isolated chronic NFKC normalization destroying presentation-forms signal before garble detection; 4 independent ScriptContext construction sites replicate inference without canonical factory |
| Table-Unaware Pre-Tree Text Transforms | medium | RFC-043 audit revealed split_oversized_leaf_nodes lacks the table-span guard already proven correct in headings.py; pipe-table rows shattered by numbered-line splitting |

## Metrics

- **Net bug delta:** +28
- **Wiring status:** partially_wired
- **Unwired symbols:** validate_feature_wirings, validate_recovery_method_names
- **Overall scorecard trend:** Regressed (1 improved, 3 regressed, 2 closed)

## Recommended Next Steps

1. **CRITICAL BLOCKER:** Wire validate_feature_wirings into worker startup (worker/lifecycle.py:startup) so GateSpec.recovery_fns are validated at boot, not only in tests — this is the same class of dead-dispatch defect flagged in RFC-041 and still unresolved after two cycles.

2. **Wire the recovery dispatcher:** add ~15 lines to helpers/gates.py that does getattr(RecoveryModule, gate.recovery_fns[i]) in a loop — this alone closes the biggest bug class in the OCR cascade zone (4 dead recovery paths).

3. **Consolidate ScriptContext construction** behind build_script_context() factory to stop NFKC signal destruction recurring (chronic defect across 3+ cycles).

4. **Add table-span guard to split_oversized_leaf_nodes** (1-day effort, low risk, reuses existing compute_table_spans).

5. **Apply CAS-by-priority to save_doc_meta** using existing VERDICT_PRIORITY dict to close the dual-writer disagreement.

6. **Run corpus-diff after each fix lands** to verify no verdict distribution regression — past cycles prove code-level fixes alone do not guarantee scorecard closure without corpus verification.