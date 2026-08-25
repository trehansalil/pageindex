---
tags:
  - zone-index
audit_date: 2026-08-25
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md
---
# Zone Specifications Index

All zone specifications from the audit date 2026-08-25, organized by priority and wave assignment.

## Zone Table (Priority Order)

| Priority | Zone Name | Severity | Wave | Status | Link |
|---|---|---|---|---|---|
| 1 | Multi-Store Dual-Write Consistency | high | 3 | triaged | [[multi-store-dual-write-consistency]] |
| 2 | Garble Detection Fragmentation | critical | 1 | triaged | [[garble-detection-fragmentation]] |
| 3 | OCR Strategy Bifurcation | critical | 1 | triaged | [[ocr-strategy-bifurcation]] |
| 4 | Verdict Promotion / Quality Gate Stack | critical | 2 | triaged | [[verdict-promotion-quality-gate-stack]] |
| 5 | Config Layering Split and Dead-Code Accumulation | medium | 3 | triaged | [[config-layering-split-and-dead-code-accumulation]] |

## Wave Assignment

### Wave 1
- [[garble-detection-fragmentation]] (critical, priority 2)
- [[ocr-strategy-bifurcation]] (critical, priority 3)

**Rationale:** Both critical-severity zones with no shared key_files and no cross-dependency. Each is an upstream signal producer (garble ratios vs OCR/image classification) that the Verdict Promotion gate stack later consumes. Both must land before Verdict Promotion is fixed. Can run in parallel since they touch entirely disjoint files.

### Wave 2
- [[verdict-promotion-quality-gate-stack]] (critical, priority 4)

**Rationale:** Shares PRIMARY files with both Wave 1 zones: `helpers/verdict.py` and `helpers/gates.py` (with Garble Detection), `client/indexer.py` (with OCR Strategy). Must run AFTER both Wave 1 zones complete. Computes the verdict outcome that Wave 3 will persist.

### Wave 3
- [[multi-store-dual-write-consistency]] (high, priority 1)
- [[config-layering-split-and-dead-code-accumulation]] (medium, priority 5)

**Rationale:** Multi-Store persists the verdict computed in Wave 2 via upsert_verdict/upsert_doc in registry/queries.py. Config Layering touches helpers/garble.py (shared with Wave 1's Garble Detection). Both must follow their upstream dependencies. Multi-Store and Config Layering share zero key_files with each other, so they safely co-run in this wave.

## Notes by Severity

### Critical
- [[garble-detection-fragmentation]]
- [[ocr-strategy-bifurcation]]
- [[verdict-promotion-quality-gate-stack]]

### High
- [[multi-store-dual-write-consistency]]

### Medium
- [[config-layering-split-and-dead-code-accumulation]]

## Cross-References

- **Audit Source:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md
- **Remediation Plan:** audit/REMEDIATION_PLAN_2026-08-25.md
- **Remediation Status:** See individual zone notes for code targets, test requirements, and dependencies.
