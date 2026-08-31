---
title: Architecture Defect Zones Index
zone_index: true
tags:
  - zone-index
  - audit-2026-08-29
created: 2026-08-29
audit_run: POST-FIX-WAVE4
---

# Architecture Defect Zones — 2026-08-29 POST-FIX-WAVE4

**Comprehensive index of architectural defect zones** affecting the PageIndex MCP Server.

---

## Critical Zones (3)

| # | Zone | Bug Count | Status | Link |
|---|------|-----------|--------|------|
| 1 | ExtractionState route/ok multi-writer cascade | 7 | audited | [extractionstate-route-ok-multi-writer-cascade.md](extractionstate-route-ok-multi-writer-cascade.md) |
| 2 | Normalize-before-detect null-detector lattice | 6 | audited | [normalize-before-detect-null-detector-lattice.md](normalize-before-detect-null-detector-lattice.md) |
| 3 | Split verdict authority: five writers over two stores | 5 | audited | [split-verdict-authority-five-writers-over-two-stores.md](split-verdict-authority-five-writers-over-two-stores.md) |

**Summary:** Verdict/extraction-state/config-arbitration architecture has three independent single-points-of-failure where the "single writer" contract is violated across 10+ modules, leading to silent state corruption and audit-trail inconsistencies.

---

## High-Priority Zones (4)

| # | Zone | Bug Count | Status | Link |
|---|------|-----------|--------|------|
| 4 | Config-layer bifurcation | 4 | audited | [config-layer-bifurcation.md](config-layer-bifurcation.md) |
| 5 | Ordered-policy converter chain | 4 | audited | [ordered-policy-converter-chain.md](ordered-policy-converter-chain.md) |
| 6 | Recovery dispatch: tuple-keyed dedup | 4 | audited | [recovery-dispatch-tuple-keyed-dedup.md](recovery-dispatch-tuple-keyed-dedup.md) |
| 7 | Divergent parallel garble/text accessors | 4 | audited | [divergent-parallel-garble-text-accessors.md](divergent-parallel-garble-text-accessors.md) |

**Summary:** Recovery loop dedup, garble detection, and text extraction have three independent code paths each, creating chronic "fix-one-instance-miss-the-other" duplication. Config layer split between frozen snapshot and live os.environ reads causes audit trails to misrepresent actual runtime behavior. Converter chain licensing guarantee (HR4) is unenforced due to retry loop bug.

---

## Medium-Priority Zones (1)

| # | Zone | Bug Count | Status | Link |
|---|------|-----------|--------|------|
| 8 | Order-coupled erasure manifest | 2 | audited | [order-coupled-erasure-manifest.md](order-coupled-erasure-manifest.md) |

**Summary:** HR2 erasure cascade has hidden inter-step data dependencies (ctx.doc_name, ctx.sha256) that are order-sensitive but lack explicit declarations. Failed purges report success, risking PII-derived artifacts (verdict ledger) in MinIO after deletion.

---

## By Severity

### CRITICAL (3 zones, 18 bugs total)

- **Zone 1:** Multi-writer ExtractionState + recovery loop coupling
- **Zone 2:** NFKC-destroyed presentation-forms signal + null-detector pattern
- **Zone 3:** Split verdict authority across MinIO/Postgres with inconsistent CAS guards

**Common theme:** Single-point-of-failure patterns where claimed "single writer" or "sole entry point" contracts are violated across 10+ modules, resulting in silent state corruption.

### HIGH (4 zones, 16 bugs total)

- **Zone 4:** Config frozen snapshot vs live os.environ; audit trail misrepresentation
- **Zone 5:** Converter chain retry bug defeats HR4 licensing guarantee
- **Zone 6:** Recovery loop dedup on tuple, not method; OCR recovery double-runs
- **Zone 7:** Three parallel garble paths and three text accessors without sync guarantee

**Common theme:** Chronic duplication and scattered reads of the same resource create invisible asymmetries where fix-one-instance-miss-the-other is structural.

### MEDIUM (1 zone, 2 bugs total)

- **Zone 8:** Erasure cascade order dependencies hidden in prose; no topological sort

**Common theme:** New storage/LLM paths automatically inherit compliance blind spots when added to manifest-driven drivers.

---

## Cross-Cutting Themes

1. **Null-Detector Pattern** — Detectors structurally cannot fire on their real failure mode (signal destroyed pre-detection or excluded by gate order), yet zero violations is read as evidence of safety

2. **Threshold/Config Tightening as Content Regression** — Audit conclusions depend on which store/config snapshot is queried; live-store verification required to distinguish verdict change from config change

3. **Fix-One-Instance-Miss-the-Other Duplication** — Three garble paths, three text accessors, three recovery entry points; six unauthorized state writers; five verdict writers; all lack sync guarantees

4. **Partial RFC Implementation** — RFC-033/037/040/039 all carry "unresolved" sections; incompleteness markers propagate across waves without triggering follow-up

5. **Remote/External-Service Code Drift** — Docling, PDF Inspector, arq have no version/contract pinning; drift discovered only via corpus regression

6. **Compliance Cascades on New Paths** — ISS-02/ISS-41 show erasure logic duplicated; RFC-011/039 boot-time ZDR gates incomplete; new storage paths inherit blind spots automatically

7. **Verdict/Garble/OCR-Recovery Coupling** — Three-way tightly coupled triad with no common test oracle; interactions discovered only via corpus audit

8. **Compensating Heuristics Entrenchment** — Image-enrichment bypass, _has_image_rescue guard added as temporary bridges, never removed, become leniency vectors

9. **Audit/Measurement Tooling Blind Spots** — Table-role blocks omit 'text' keys; measurement tools counting keys register zero tables; fixing code doesn't fix measurement

10. **Shared Choke Points Cascade** — Bidi reconstruction in Docling feeds _check_bidi_coherence gate; external API change breaks gate silently; garble_check_nodes called from two paths with no sync guarantee

11. **Hardcoded Constants Scattered Across Files** — DEPTH_ADEQUACY_FLOOR duplicated (4 vs 5); _LEDGER_VERDICT_PRIORITY vs _LEDGER_PRIORITY in parallel modules; Unified config layer partial coverage

12. **NFKC Normalization Blind Spot** — Presentation-forms destroyed before detector sees text; RFC-040 D5 reorders check pre-NFKC but similar patterns remain in bidi/OCR recovery

13. **Silent Degradation via Unnamed Else** — Converter structural failures, low-confidence OCR, low-image-count docs fall through unnamed branches without metrics

14. **Caching Strategies with Incomplete Invalidation** — Confidence cache written once, never invalidated; cached low-confidence blocks bypass retry

15. **Test Fixture Consolidation Backward Compat Gap** — 85 test files merged to 37; fixture collision; normalization layer leaked into production paths

16. **Audit Zones Describing Completed Work** — Zone 2 describes garble fixes already in RFC-040; risk of redundant re-implementation

---

## Recommended Reading Order

### For Architects/Team Leads

1. Start with **Zone 1** (ExtractionState multi-writer) — core pattern
2. Then **Zone 3** (Split verdict authority) — data consistency
3. Then **Zone 4** (Config bifurcation) — observability
4. Cross-cutting themes (overview above)

### For Implementers

1. **Zone 6** (Recovery dispatch) — immediate impact on job reliability
2. **Zone 7** (Garble accessors) — duplication pattern recognition
3. **Zone 2** (Presentation-forms) — signal flow understanding
4. Individual zone files for code-level details

### For Compliance/Audit

1. **Zone 8** (Erasure manifest) — HR2 compliance
2. Sections on "Compliance Cascades" in cross-cutting themes
3. Zone 3 verdict authority split (audit trail integrity)

---

## Quick Reference: Files Most Frequently Cited

| File | Zones | Issue Pattern |
|------|-------|---------------|
| client/indexer.py | 1,2,4,5,6 | Multi-writer state mutations; config reads; converter logic |
| helpers/gates.py | 1,2,4,5,6 | Gate definitions; gate logic; config reads |
| storage/verdict.py | 3 | Optimistic writer with no CAS |
| helpers/garble.py | 2,7 | Three parallel detection paths; fallback bypasses |
| client/recovery.py | 1,2,6 | Recovery methods; state carrier destruction |
| config.py | 4,5 | Bifurcated config layer; CONVERTER defaults |
| helpers/flat.py | 7 | Three text accessors; role dispatch duplication |
| storage/documents.py | 8 | Erasure manifest; order-coupled steps |

---

## Audit Metadata

- **Audit Date:** 2026-08-29
- **Audit Run:** POST-FIX-WAVE4
- **Total Zones:** 8
- **Total Bug Count Attributed:** 32 defects
- **Critical Zones:** 3
- **High Zones:** 4
- **Medium Zones:** 1
- **Main Report:** [../ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE4.md](../ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE4.md)

---

## Index Metadata

```yaml
audit_date: 2026-08-29
audit_run: POST-FIX-WAVE4
project: PageIndex MCP Server
repository: https://github.com/pageindex/pageindex_deployment
version: 1.0
last_updated: 2026-08-29
maintainer: Audit System
```

---

## Previous Audit Zones

Zone completion status from prior audits:

- **Zone 1** (2026-08-29): ExtractionState multi-writer — audited
- **Zone 2** (2026-08-29): Presentation-forms null-detector — audited  
- **Zone 3** (2026-08-29): Split verdict authority — audited
- **Zone 4** (2026-08-29): Config bifurcation — audited
- **Zone 5** (2026-08-29): Converter chain — audited
- **Zone 6** (2026-08-29): Recovery dispatch — audited
- **Zone 7** (2026-08-29): Garble accessors — audited
- **Zone 8** (2026-08-29): Erasure manifest — audited

For historical context, see audit/ directory.
