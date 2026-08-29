---
zone_index: true
audit_date: 2026-08-29
audit_run: POST-FIX-WAVE3-VERIFY
audit_scope: Six critical and high-severity defect zones
tags:
  - zone-index
  - audit-reference
---

# Architecture Defect Zones Index

**Audit Date:** 2026-08-29
**Audit Run:** POST-FIX-WAVE3-VERIFY
**Total Zones:** 6 (2 Critical, 3 High, 1 Medium)
**Total Bugs Attributed:** 22

## Priority-Ordered Zone Reference

| Priority | Zone | Severity | Bugs | Key Impact | Note |
|---|---|---|---|---|---|
| 1 | [Verdict Promotion / Threshold Ratchet](verdict-promotion-threshold-ratchet.md) | **CRITICAL** | 6 | Threshold widening fixes false-FAIL but unmasks false-PASS; promotion paths bypass structural checks | **Active Defect**: RFC-022/024/025/026/033 ratchet cycle still recurring |
| 2 | [Detection-Remediation Dispatch Gap](detection-remediation-dispatch-gap.md) | **CRITICAL** | 4 | Garble detected correctly but recovery escalation fails to connect; early-exit gates prevent remediation | **Active Defect**: Ward 597 pattern; recovery only by VLM fallback |
| 3 | [Converter Chain / Remote Service Boundary Drift](converter-chain-remote-service-boundary-drift.md) | **HIGH** | 4 | Local fixes never reach production; remote Docling independently versioned with no skew enforcement | **Active Defect**: RFC-033 guard never committed; remote service versionless |
| 4 | [Content Measurement Blind Spot](content-measurement-blind-spot.md) | **HIGH** | 3 | block.get('text','') returns 0 chars for tables; affects both pipeline and audit harness equally | **Active Defect**: Fabricated Run 9 report; GHV-TKV-Tarif 96% under-count |
| 5 | [OCR Pipeline Conflation](ocr-pipeline-conflation.md) | **HIGH** | 3 | Per-picture OCR duplicates work during force_full_page_ocr; standalone images bypass enrichment splice | **Active Defect**: Zone-2 patch incomplete; duplication still occurs |
| 6 | [Verdict Persistence Asymmetry](verdict-persistence-asymmetry.md) | **MEDIUM** | 2 | Three writers with asymmetric consistency; erasure manifest manually maintained and drifts out of sync | **Active Defect**: ISS-41 preloaded/ prefix coverage gap; ISS-40 registry-delete fire-and-forget |

## Cross-Zone Themes

### Architectural Root Causes

1. **Threshold Ratchet (Zone 1)**
   - Single metric (max_leaf_ratio) gates promotion
   - Widening threshold fixes one false class but unmasks another
   - Promotion paths bypass structural checks

2. **Detection-Remediation Disconnect (Zone 2)**
   - GATE_TABLE severity ordering hides co-firing defects
   - Early-exit gates prevent garble detection
   - Recovery dispatch operates on narrower reason set than detection

3. **Local-Remote Divergence (Zone 3)**
   - Remote service independently versioned
   - Fixes implemented locally but never deployed
   - No version assertion at API boundary

4. **Measurement Blindness (Zone 5)**
   - block.get('text','') pattern blindly applied
   - Table content stored in row_records, not text key
   - Affects both pipeline and audit harness identically

5. **Pipeline Overload (Zone 4)**
   - Single conversion function handles two OCR strategies
   - Per-picture and full-page compete on same region
   - Content-type boundary gaps at every new ingestion route

6. **Consistency Boundary (Zone 6)**
   - Process boundary between child/parent without transactional wrap
   - Three writers with different guarantees
   - Erasure manifest manually maintained, drifts when routes added

### Systemic Patterns

- **Metrics trump content quality** — threshold tweaks cause verdict flips without extraction changes
- **Audit tool inherits pipeline blind spots** — same block.get('text','') pattern used in both
- **Process safeguards substitute for root-cause fixes** — RFC-025 D4 re-verify prevents publishing wrong numbers but doesn't fix the scoring bug
- **Early-exit gates prevent downstream remediation** — numeric-junk early-exit prevents garble detection
- **Duplicated detection diverges independently** — _tree_is_garbled vs _flat_text_is_garbled repeat same bugs
- **Coverage gaps recur at content-type boundaries** — standalone images, preloaded/ prefix, image_based timeouts

## Related Documents

- **Main Audit Report:** [ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE3-VERIFY.md](../ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE3-VERIFY.md)
- **Project Memory:** Linked in MEMORY.md with individual decision records
- **Companion Chains:** 22 evidence chains documented across Run history

## Audit Scope

Each zone includes:
- **Mechanism:** Root cause and how it manifests
- **Code Evidence:** Specific file:line references and code patterns
- **Evidence History:** Chains and run artifacts supporting the finding
- **Key Files:** Source files involved in the defect

## Verification Status

All zones marked **audited** as of 2026-08-29 POST-FIX-WAVE3-VERIFY.

For detailed analysis of any zone, select it from the table above.
