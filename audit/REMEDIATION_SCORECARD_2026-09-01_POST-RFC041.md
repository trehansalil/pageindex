# Remediation Scorecard — POST-RFC041 (2026-09-01)

**Pre-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE4.md  
**Post-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-01_POST-RFC041.md  
**Delta report:** audit/ZONE_DELTA_2026-09-01_POST-RFC041.md

## Verdict: REGRESSED

The post-RFC041 audit surface reveals one critical regression: the OCR Recovery Cascade & Converter Fallback Chain zone escalated from high to critical severity (8 bugs, regressed status). This escalation was triggered by unmerged dead-code pathways — the four recovery orchestrators (_recover_vlm_fallback, _recover_garble_ocr, _recover_low_content_ocr, _recover_image_dominant_ocr) are never invoked from production dispatch logic, rendering them maintenance liabilities rather than fixes. Conversely, five zones improved through RFC041 scope: Verdict Computation, Garble Detection, Content Measurement, Verdict Persistence, and three supportive zones. Net bug delta is −10 (26 bugs recorded pre-RFC041 across all zones; 16 recorded post). Wiring status remains partially_wired — the dispatcher must be restored before further OCR work proceeds.

## Zones Closed (0)

No zones were closed in this cycle.

## Zones Remaining (7)

| Zone Name | Severity | Bug Count | Status |
|---|---|---|---|
| OCR Recovery Cascade & Converter Fallback Chain | Critical | 8 | Regressed |
| Verdict Computation & Promotion Cascade | Critical | 6 | Improved |
| Garble Detection & NFKC Signal Destruction | High | 4 | Improved |
| Content Measurement Blind Spot (Table Block Text Extraction) | High | 3 | Improved |
| Verdict Persistence Dual-Writer | High | 2 | Improved |
| Config Snapshot vs Live-Read Divergence | Medium | 2 | Improved |
| HR2 Erasure Cascade Hidden Ordering Dependencies | Medium | 1 | Improved |

## New Zones (0)

No new zones identified in this audit cycle.

## Metrics

- **Total zones:** 7
- **Total bugs recorded:** 16
- **Zones improved:** 6
- **Zones regressed:** 1
- **Zones stalled:** 0
- **Net bug delta:** −10 (26 pre-RFC041 → 16 post-RFC041)
- **Wiring status:** partially_wired
- **Unwired symbols:** 
  - `_recover_vlm_fallback`
  - `_recover_garble_ocr`
  - `_recover_low_content_ocr`
  - `_recover_image_dominant_ocr`

## Recommended Next Steps

1. **CRITICAL BLOCKER — Wire OCR recovery dispatch:** The 4 recovery methods (`_recover_garble_ocr`, `_recover_low_content_ocr`, `_recover_image_dominant_ocr`, `_recover_vlm_fallback`) are dead code — never called from production. The orchestration dispatcher in `_convert_to_tree` or indexer must be restored/created before any other OCR zone work proceeds. This is the root cause of the zone's regression from high to critical.

2. **Fix the converter-chain RETRY bare 'continue' bug:** This is an identical unfixed defect across 3 audit cycles — chronic issue. Replace with explicit `restart_from_primary()` per the simplification proposal.

3. **Implement the promotion-table collapse for Verdict Computation (Zone 2):** Make the six-guard cascade reviewable as data.

4. **Complete ScriptContext required-constructor-arg migration for Garble/NFKC zone:** Eliminate null-detector pattern by construction.

5. **Add architecture-guard test for table block text accessor:** Lowest effort, highest ROI among remaining zones — 0.5–1 day.

6. **Address pre-existing blockers:**
   - RFC-033 gate violation (deferred to 2026-09-15)
   - RFC-037 Release B validation for D11 verdict authority consolidation