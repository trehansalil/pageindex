---
title: Architecture Defect Zones Index
audit_date: 2026-08-27
audit_run: POST-RUN20
tags:
  - zone-index
---
# Architecture Defect Zones Index

**Audit Date:** 2026-08-27  
**Audit Run:** POST-RUN20  
**Total Zones:** 7 (2 critical, 4 high, 1 medium)  
**Total Attributed Bugs:** 24  

## Zone Catalog

| Priority | Zone | Severity | Bug Count | Link |
|---|---|---|---|---|
| 1 | Verdict Gate Threshold / Promotion Override Cascade | critical | 5 | [[verdict-gate-threshold-promotion-override-cascade]] |
| 2 | Garble Detection Cross-Cutting Kernel | critical | 5 | [[garble-detection-cross-cutting-kernel]] |
| 3 | OCR Recovery Cascade | high | 4 | [[ocr-recovery-cascade]] |
| 4 | Bidi/RTL Processing Split | high | 3 | [[bidi-rtl-processing-split]] |
| 5 | Measurement / Audit Tooling Shared Blind Spots | high | 3 | [[measurement-audit-tooling-shared-blind-spots]] |
| 6 | Erasure Cascade / Storage Consistency | high | 2 | [[erasure-cascade-storage-consistency]] |
| 7 | Converter Chain Fallback / AGPL Gating | medium | 2 | [[converter-chain-fallback-agpl-gating]] |

## Cross-Cutting Themes

All zones exhibit one or more of these meta-patterns:

1. **Silent degradation defeats the very gate it feeds** — Recovery mechanisms produce 'false-clean' content that bypasses quality gates designed to catch the failure mode.

2. **Coupled kill-switches and shared code paths** — One RFC's fix can disable another RFC's mechanism through shared parameter or gate.

3. **Fixes in working tree only, not in production** — Commits never land, remote services run stale code, or remediation sits uncommitted while the old behavior lands.

4. **Diagnostic tooling inherits pipeline blind spots** — Audit harness and pipeline both miss the same class of defects (table blocks invisible to char-count).

5. **Duplicated/parallel implementations drift independently** — Same safety mechanism re-implemented in multiple places with no central enforcement.

6. **Gate threshold changes mask underlying defects** — Adjusting a threshold reveals bugs at the adjacent boundary, creating a ratchet effect.

7. **Detection without corresponding remediation** — Quality gate fires but no recovery hook is wired.

8. **Process-level harness bugs obscure visibility** — Measurement tool itself is broken, requiring workarounds rather than fixes.

## Full Report

See **[[ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-27_POST-RUN20]]** for the complete audit report with detailed evidence and mechanism analysis.
