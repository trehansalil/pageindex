# Remediation Scorecard — POST-FIX-12 (2026-08-26)

**Pre-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-24_POST-FIX-11.md
**Post-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-12.md
**Delta report:** audit/ZONE_DELTA_2026-08-26_POST-FIX-12.md

## Verdict: REGRESSED

Post-fix audit reveals two severity escalations (high→critical) in critical paths and one new zone surfaced. Net bug reduction of 34 (83→49) masks structural regressions in Verdict Gate Promotion and OCR Pipeline re-entry. Three zones closed cleanly, but five critical unwired symbols remain, including `validate_hr3_compliance` (CLAUDE.md Hard Rule 3 violation). Two regressions require immediate prioritization before next pilot: (1) Verdict Gate's priority=100 hard-coded bypass outranks structural guards; (2) OCR Pipeline's `full_page_already_applied` check runs AFTER `UNIFIED_OCR_PLAN_ENABLED` short-circuit, creating re-entry hazard. Recovery dispatcher remains test-only; production flow cannot reach five recovery strategies. Status: partially wired, regressed, at-risk.

## Zones Closed (3)

| Zone | Was Severity | Bugs Eliminated |
|------|--------------|-----------------|
| Tree-vs-Flat Gate Asymmetry | critical | 14 |
| Worker/Inspector Dual-Threshold and Timeout Race | medium | 6 |
| HR3 PII Egress Gap (Docling + VLM Silent Degradation) | medium | 4 |

## Zones Remaining (7)

| Zone | Severity | Bug Count | Status |
|------|----------|-----------|--------|
| Garble Detection Surface Fragmentation | critical | 10 | improved |
| Verdict Gate Promotion Bypass Cascade | critical | 8 | regressed |
| OCR Pipeline Flag Conflation and Re-entry Hazards | critical | 7 | regressed |
| Content-Destructive Heuristic Chains | high | 6 | improved |
| Verdict Persistence Competing Writers | high | 5 | improved |
| Image Block Conflation and Marker Survival | medium | 4 | improved |
| Verified-Locally-Never-Deployed Fix Drift | medium | 4 | improved |

## New Zones (1)

| Zone | Severity | Introduced By | Flag |
|------|----------|---------------|------|
| Landscape/Rotation and Remote Route Divergence | medium | 1 | RED |

**Flag Detail:** Post-fix re-audit surfaced page rotation handling gaps (uae_numbers consistently ~750 chars across 3+ runs) and remote route divergence not previously isolated as a named zone; chronic issue now formalized.

## Metrics

- **Net bug delta:** −34 (83 prior → 49 current)
- **Zones closed:** 3 (84 bugs eliminated)
- **Zones improved:** 5 (25 bugs reduced)
- **Zones regressed:** 2 (total bug count unchanged; severity escalated)
- **New zones:** 1 (medium severity, structural)
- **Wiring status:** partially_wired
- **Critical unwired symbols:**
  - `validate_hr3_compliance` (config.py) — HR3 compliance gate defined but 0 production callers; **CRITICAL**
  - `compute_image_enrichment_ratio` (helpers/verdict.py) — RFC-036 D4 filter; 0 production callers
  - `_recover_rtl_repair` (recovery.py) — RTL repair strategy; 0 callers
  - `_recover_rtl_flat_compare` (recovery.py) — RTL flat compare strategy; 0 callers
  - `_recover_vlm_fallback` (recovery.py) — VLM fallback strategy; 0 callers
  - `_recover_garble_ocr` (recovery.py) — garble OCR recovery; test-only wiring
  - `_recover_image_dominant_ocr` (recovery.py) — image-dominant OCR; test-only wiring
  - `_recover_low_content_ocr` (recovery.py) — low content OCR recovery; test-only wiring
  - `_document_level_text_fallback` (pictures.py) — partially unwired; single indirect caller

## Recommended Next Steps

1. **CRITICAL: Wire `validate_hr3_compliance` into server and worker boot sequences** (Hard Rule 3 violation). CLAUDE.md mandate requires active compliance gating at startup; current state leaves PII-bearing documents unprotected.

2. **Fix Verdict Gate Promotion regression:** The `image_enrichment_promoted` priority=100 hard-coded bypass must not outrank structural hard-fail. Implement the ordered-decision-list simplification proposal to eliminate the priority-max candidate system entirely.

3. **Fix OCR Pipeline re-entry:** Reorder `decide_ocr_strategy()` so the `full_page_already_applied` guard runs **BEFORE** the `UNIFIED_OCR_PLAN_ENABLED` short-circuit (picture_plane.py lines 389–397 ordering bug).

4. **Wire recovery dispatcher:** `_recover_garble_ocr`, `_recover_image_dominant_ocr`, `_recover_low_content_ocr`, `_recover_rtl_repair`, `_recover_vlm_fallback` are all defined but unreachable from production flow. Add dispatch routing in `_execute_ocr_retry` or a new recovery orchestrator.

5. **Wire `compute_image_enrichment_ratio` into the promotion path** it was designed for (RFC-036 D4).

6. **Re-verify HR3 PII Egress closure independently** — the zone dropped from audit scope rather than being confirmed fixed. CLAUDE.md Hard Rule 3 still mandates active verification.

7. **Run corpus-cycle after wiring fixes** to confirm no new regressions from the two severity escalations (high→critical on Verdict Gate and OCR Pipeline).

---

**Generated:** 2026-08-26  
**Audit run:** POST-FIX-12  
**Coordinator:** audit tooling
