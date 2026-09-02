---
title: Architecture Defect Zones Index
zone_name: Index
tags:
  - zone-index
  - audit-summary
  - critical
audit_date: 2026-09-02
audit_run: POST-RFC043
---
# Architecture Defect Zones — Index

**Audit Date:** 2026-09-02  
**Audit Run:** POST-RFC043  
**Scope:** 7 interdependent structural defect zones  
**Total Attributed Bugs:** 50+

## Zone Priority Table

| Priority | Zone | Severity | Bug Count | Status | Note |
|---|---|---|---|---|---|
| 1 | [[ocr-pipeline-decision-recovery-cascade]] | Critical | 12 | Audited | 4 dead-code recovery methods; order-dependent re-entry guard; multiple conflicting decision sites |
| 2 | [[garble-detection-nfkc-signal-destruction]] | Critical | 8 | Audited | NFKC destroys presentation-form signal; independently rediscovered 3x; structurally present |
| 3 | [[table-unaware-pre-tree-text-transforms]] | High | 7 | Audited | Asymmetric table guard: headings.py has it, tree_split.py doesn't; cascade of breaks across 7 RFCs |
| 4 | [[verdict-promotion-hard-rule-5-bypass]] | High | 7 | Audited | Multiple bypass paths override hard-rule-5; zero-content early-return bypasses recovery; threshold widening masks defects |
| 5 | [[verdict-persistence-dual-writer-hysteresis]] | High | 6 | Audited | 3 writers with different CAS models; silent failures on transient writes; hysteresis ledger destroyed by reingestion |
| 6 | [[gate-to-recovery-dispatch-wiring-gap]] | High | 6 | Audited | Recovery declared but never invoked; 'fixed but never wired' pattern; RFC-029 failure reasons never routed |
| 7 | [[remote-local-execution-divergence-config-snapshot]] | Medium | 4 | Audited | Remote service runs stale image; no parity mechanism; config snapshot leak; timeout uncalibrated |

## By Severity

### Critical (2 zones, 20 bugs)
1. **OCR Pipeline** — Recovery methods fully implemented but zero production callers; multiple decision sites suppress each other
2. **NFKC Signal Destruction** — Architectural normalization requirement destroys gate inputs; compensation mechanisms proliferating

### High (4 zones, 30 bugs)
3. **Table-Unaware Transforms** — Guard exists but unwired in same file where needed; collateral breaks across 7 RFCs
4. **Verdict Promotion Bypass** — Hard-Rule-5 bypass paths; zero-content early-return; threshold widening
5. **Persistence Dual-Writer** — CAS asymmetry; silent failures; hysteresis fragility
6. **Gate-to-Recovery Gap** — Declarative/runtime disconnect; 'fixed but never wired' pattern

### Medium (1 zone, 4 bugs)
7. **Remote/Local Divergence** — No parity mechanism; config snapshot leak; uncalibrated timeouts

## Cross-Cutting Themes

- **Sequential remediation chains:** Most RFC fixes become next RFC's root-cause finding
- **'Fixed but never wired' pattern:** Distinct from logic bugs; correct implementations never called/committed
- **Parameter threading gaps:** Signal/reason-strings lost in transit
- **NFKC destruction (recurring):** Independently rediscovered 3x across RFCs
- **Threshold widening without anchoring:** Masks extraction defects rather than fixing them
- **Gates fighting each other:** Multiple mechanisms with conflicting objectives
- **Null/zero detectors misread as safe:** _check_bidi_coherence 0% true-positive → promoted to default-true
- **Table/structural destruction:** Multiple independent transforms with no shared primitive
- **Dual-store divergence:** MinIO vs Postgres with asymmetric CAS
- **Remote/local parity gap:** Stale deployed image predates local fixes
- **Audit staleness risk:** Claims frequently stale; validation-before-remediate needed
- **Dead code & unwired implementations:** Recovery cascade, persistence bypass callers, eligibility machinery
- **Write barrier & dual-writer gaps:** Claimed single-writer has 10+ bypass callers
- **Compliance deferred:** Zone 2 NFKC ownership explicitly unresolved

## Key Statistics

| Metric | Value |
|---|---|
| Total Zones | 7 |
| Total Bugs | 50+ |
| Critical | 20 |
| High | 30 |
| Medium | 4 |
| Key Files Affected | ~40 |
| RFC Chain Span | RFC-018 → RFC-043 |
| Audit Date | 2026-09-02 |
| Audit Source | ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-02_POST-RFC043.md |

---

## How to Use This Index

- **For remediation:** Start with Critical zones; use zone slugs to link directly to evidence
- **For architecture review:** Read cross-cutting themes to understand systemic patterns
- **For validation:** Each zone has evidence chain showing how it manifests across RFCs
- **For monitoring:** Watch for 'fixed but never wired' pattern as early warning sign

---

**Last Updated:** 2026-09-02  
**Audit Methodology:** Structural defect zones (not isolated bugs); mechanism → evidence → history → code
