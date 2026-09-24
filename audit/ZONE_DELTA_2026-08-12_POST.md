# Zone Delta Analysis — POST

**Current audit:** `audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md`  
**Prior audit:** `audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md`  
**Date:** 2026-08-12

---

## Summary

The post-fix audit of 7 zones (vs. 8 in the prior audit) shows mixed results: 3 zones improved with a net -3 bugs, but 2 zones regressed or escalated in severity, notably the Verdict-Gate Cascade (which remains critical with +3 bugs due to unimplemented refactoring) and Measurement/Audit Tooling (escalated from medium to high severity despite holding 4 bugs). The Bidi/RTL Processing Split zone was closed, absorbed into or resolved relative to higher-level zone mechanisms. Overall bug count dropped modestly from 35 to 32; the critical path remains Verdict-Gate Cascade and Garble Detection Kernel.

---

## Delta Table

| Zone | Status | Severity (prior→current) | Bugs (prior→current) | Proposal Status | Key Change |
|---|---|---|---|---|---|
| Verdict-Gate Cascade (Threshold / Promotion / Override) | **Regressed** | Critical→Critical | 8→11 | Not Implemented | Proposed declarative PROMOTION_TABLE refactor never applied; if/elif chain unchanged; source_selection bypass and hysteresis interactions surfaced 3 additional bugs. |
| Garble Detection Kernel / Cross-Cutting Kernel | **Stalled** | Critical→Critical | 7→7 | No Proposal | Bug count and severity unchanged; 13-caller shared kernel structure remains unmodified. |
| OCR Recovery Cascade (and Kill-Switch Conflation) | **Improved** | High→High | 6→5 | No Proposal | Detection-to-remediation gap narrowed; severity held at high; no explicit simplification on record. |
| Converter Pipeline / Chain Fallback and AGPL Gating | **Improved** | High→High | 4→3 | No Proposal | AGPL structural-failure walk-through and remote Docling expected_script gap persist; 1 bug resolved. |
| Dual-Writer Verdict Persistence / Dual-Write Consistency Model | **Improved** | High→High | 4→3 | No Proposal | save_doc_meta still omits _confirm_write_visible barrier (eventual consistency by design); 1 defect resolved. |
| Erasure Cascade (Manually-Maintained Manifest / Storage Consistency Drift) | **Stalled** | Medium→Medium | 2→2 | No Proposal | Manifest lacks compile-time/test-time coverage assertion; registry DELETE has no statement timeout (ISS-40 unresolved). |
| Measurement/Audit Tooling Shared Blind Spots | **Regressed** | Medium→**High** | 4→4 | No Proposal | Bug count held at 4 but severity escalated from medium to high; confirmed self-reinforcing table-block cycle (block.get('text','') blind spot in pipeline and audit tooling) deemed more critical on re-audit. |

---

## Per-Zone Details

### Verdict-Gate Cascade (Threshold / Promotion / Override)
**Status:** Regressed | **Bugs:** 8→11 | **Severity:** Critical→Critical

The proposed declarative PROMOTION_TABLE refactor (intended for `verdict.py`) was never implemented. A `grep` confirms no PROMOTION_TABLE symbol exists and the ordered if/elif chain (`_try_image_enrichment`, `apply_promotions`) remains unchanged from prior audit. Meanwhile, source_selection clamp-bypass and hysteresis-band interactions (which interact through the existing ordered chain) surfaced 3 additional bugs. This zone remains the critical path and is now more defective than expected.

### Garble Detection Kernel / Cross-Cutting Kernel
**Status:** Stalled | **Bugs:** 7→7 | **Severity:** Critical→Critical

No simplification proposal existed for this zone in the prior audit. The shared 13-caller `detect_garble` kernel structure is unchanged. Bug count and severity held constant; this zone is neither improving nor worsening.

### OCR Recovery Cascade (and Kill-Switch Conflation)
**Status:** Improved | **Bugs:** 6→5 | **Severity:** High→High

The detection-to-remediation gap narrowed slightly with 1 bug resolved. Severity held at high; no explicit simplification proposal was on record for this zone prior to the fix.

### Converter Pipeline / Chain Fallback and AGPL Gating
**Status:** Improved | **Bugs:** 4→3 | **Severity:** High→High

Bug count dropped from 4 to 3. AGPL structural-failure walk-through and remote Docling expected_script gap persist as residual defects. One prior bug was resolved, but the high-level architectural tension remains.

### Dual-Writer Verdict Persistence / Dual-Write Consistency Model
**Status:** Improved | **Bugs:** 4→3 | **Severity:** High→High

Bug count dropped from 4 to 3. The `save_doc_meta` function still deliberately omits the `_confirm_write_visible` barrier, maintaining eventual consistency by design. One prior defect was resolved; the architectural choice to avoid synchronous barriers stands.

### Erasure Cascade (Manually-Maintained Manifest / Storage Consistency Drift)
**Status:** Stalled | **Bugs:** 2→2 | **Severity:** Medium→Medium

No progress. The manifest still lacks compile-time/test-time coverage assertion. Registry DELETE still has no statement timeout (ISS-40 remains unresolved). This zone shows no movement.

### Measurement/Audit Tooling Shared Blind Spots
**Status:** Regressed | **Bugs:** 4→4 | **Severity:** Medium→**High**

The bug count held at 4, but severity was escalated from medium to high on re-audit, reflecting the confirmed self-reinforcing measurement cycle: the `block.get('text','')` blind spot is shared by both the extraction pipeline and the audit tooling itself, creating a closed feedback loop that masks defects. This was judged more serious on re-examination.

---

## New Zones

None.

---

## Closed Zones

**Bidi/RTL Processing Split (Local vs. Remote)**  
Previously: High severity, 3 bugs  
Status: Not present as a distinct zone in the current post-fix zone list. Its symptoms and mechanisms appear to have been absorbed into or resolved relative to the Verdict-Gate Cascade and Garble Detection Kernel zones.

---

## Audit Metadata

- **Total zones (current):** 7
- **Total zones (prior):** 8
- **Improved zones:** 3
- **Regressed zones:** 2
- **Stalled zones:** 2
- **New zones:** 0
- **Closed zones:** 1
- **Net bug delta:** -3 (35 bugs prior → 32 bugs current)
