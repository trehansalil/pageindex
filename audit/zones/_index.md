---
tags:
  - zone-index
  - audit
  - architecture
---
# Architecture Defect Zones Index

**Audit Date:** 2026-09-01  
**Audit Run:** POST-RFC041  
**Total Zones:** 7  
**Total Bugs Attributed:** 26  
**Critical Zones:** 1 | **High Zones:** 3 | **Medium-High Zones:** 1 | **Medium Zones:** 1 | **Resolved Zones:** 1
**Validation Date:** 2026-09-01 | **RFC Split:** [[RFC-042]] (Verdict+Config: Zones 2,4,5,6) · [[RFC-043]] (OCR+Garble+Erasure: Zones 1,3,7)

---

## Zones by Severity

| Priority | Zone | Severity | Bugs | Status |
|----------|------|----------|------|--------|
| 1 | [[ocr-recovery-cascade-converter-fallback-chain\|OCR Recovery Cascade & Converter Fallback Chain]] | HIGH | 8 | validated (downgraded: AGPL fixed, ordering refuted) |
| 2 | [[verdict-computation-promotion-cascade\|Verdict Computation & Promotion Cascade]] | CRITICAL | 6 | validated |
| 3 | [[garble-detection-nfkc-signal-destruction\|Garble Detection & NFKC Signal Destruction]] | HIGH | 4 | validated |
| 4 | [[content-measurement-blind-spot-table-block\|Content Measurement Blind Spot (Table Block)]] | RESOLVED | 3 | validated (D2 complete, trap closed) |
| 5 | [[verdict-persistence-dual-writer\|Verdict Persistence Dual-Writer]] | HIGH | 2 | validated (no MinIO CAS — worse) |
| 6 | [[config-snapshot-live-read-divergence\|Config Snapshot vs Live-Read Divergence]] | MEDIUM-HIGH | 2 | validated (17 files, not 9) |
| 7 | [[hr2-erasure-cascade-ordering\|HR2 Erasure Cascade Hidden Ordering]] | MEDIUM | 1 | validated |

---

## Critical Zones (Immediate Attention Required)

### Zone 1: OCR Recovery Cascade & Converter Fallback Chain
**8 historical bugs** - The densest defect-generating zone. Three structural coupling patterns make fixes here systematically break other behaviors. See [[ocr-recovery-cascade-converter-fallback-chain]].

### Zone 2: Verdict Computation & Promotion Cascade
**6 historical bugs** - Tightly coupled three-phase pipeline where threshold changes, gate reordering, and promotion eligibility interact non-linearly. See [[verdict-computation-promotion-cascade]].

---

## High-Severity Zones (Planned Remediation)

### Zone 3: Garble Detection & NFKC Signal Destruction
**4 historical bugs** - Structural vulnerability where NFKC normalization destroys signal before garble checks run. See [[garble-detection-nfkc-signal-destruction]].

### Zone 4: Content Measurement Blind Spot (Table Block Text Extraction)
**3 historical bugs** - Table blocks intentionally omit text key, causing systematic under-measurement. See [[content-measurement-blind-spot-table-block]].

### Zone 5: Verdict Persistence Dual-Writer
**2 historical bugs** - Verdict persisted to two independent stores with different CAS guard semantics. See [[verdict-persistence-dual-writer]].

---

## Medium-Severity Zones (Follow-Up Actions)

### Zone 6: Config Snapshot vs Live-Read Divergence
**2 historical bugs** - Frozen PipelineConfig at import time vs 121 live os.environ reads in 9 files. See [[config-snapshot-live-read-divergence]].

### Zone 7: HR2 Erasure Cascade Hidden Ordering Dependencies
**1 historical bug** - Hidden data-flow dependencies between erasure steps. See [[hr2-erasure-cascade-ordering]].

---

## Cross-Cutting Patterns

See [[audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-01_POST-RFC041]] for detailed analysis of 11 recurring patterns across all zones.
