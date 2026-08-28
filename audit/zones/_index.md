---
tags:
  - zone-index
audit_date: 2026-08-28
audit_run: POST-FIX-WAVE3
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md
---
# Architecture Defect Zones Index

**Audit Date:** 2026-08-28  
**Audit Run:** POST-FIX-WAVE3  
**Total Zones:** 8 | **Critical:** 2 | **High:** 4 | **Medium:** 2  
**Total Bugs Attributed:** 38

---

## Zone Priority Table

| Priority | Zone | Severity | Bug Count | Wave | Status | Note |
|----------|------|----------|-----------|------|--------|------|
| **1** | [[verdict-gate-threshold-promotion-override-cascade\|Verdict-Gate Threshold / Promotion / Override Cascade]] | **Critical** | 8 | 1 | Audited | Order-dependent cascade with bypass flags; 5 RFCs repeatedly fixed/re-broke |
| **2** | [[garble-detection-cross-cutting-kernel\|Garble Detection Cross-Cutting Kernel]] | **Critical** | 7 | 1 | Audited | 13 callers across 9+ subsystems; narrow fix changes all consumers |
| **3** | [[ocr-recovery-cascade-kill-switch-conflation\|OCR Recovery Cascade and Kill-Switch Conflation]] | **High** | 6 | 2 | Audited | Page-level and per-picture OCR conflated; detection/remediation gap |
| **4** | [[converter-chain-fallback-agpl-gating\|Converter Chain Fallback and AGPL Gating]] | **High** | 4 | 2 | Audited | Structural failures silently advance to AGPL; violates HR#4 |
| **5** | [[dual-writer-verdict-persistence-consistency-split\|Dual-Writer Verdict Persistence and Consistency Model Split]] | **High** | 4 | 3 | Audited | save_doc vs save_doc_meta asymmetry; load-bearing reconciliation ordering |
| **6** | [[bidi-rtl-processing-split-local-vs-remote\|Bidi/RTL Processing Split (Local vs. Remote)]] | **High** | 3 | 2 | Audited | RFC-033 guard never committed; remote service outdated; NFKC destroys signal |
| **7** | [[erasure-cascade-storage-consistency-drift\|Erasure Cascade and Storage Consistency Drift]] | **Medium** | 2 | 3 | Audited | Manual manifest enumeration drifts; fire-and-forget registry-delete; violates HR#2 |
| **8** | [[measurement-audit-tooling-shared-blind-spots\|Measurement/Audit Tooling Shared Blind Spots]] | **Medium** | 4 | 3 | Audited | Audit inherits pipeline blind spots; self-reinforcing bug cycle |

---

## Cross-Cutting Themes

### Silent Degradation & False-Clean Output
Recovery/fallback mechanisms (Latin OCR substitution, blind bidi flip, AGPL converter fallback, image-enrichment promotion) produce 'false-clean' output that slips past the quality gate designed to catch that exact failure mode.

### Coupled Kill-Switches & Shared Kernels
One flag or function (_OCR_ESCALATION, detect_garble, GATE_TABLE severity ordering) simultaneously serves multiple independently-evolving subsystems — fix aimed at one consumer silently changes behavior for the others.

### Fixes Local, Not Remote
RFC-033 bidi heading guard never committed; remote Docling microservice runs separately-deployed, versionless image predating local converter fixes — local patch has zero effect on remotely-routed documents.

### Diagnostic/Audit Tooling Inheritance
Audit tooling inherits structural blind spots from pipeline it measures (char-count via block.get('text','') is 0 for tables in both); scoring harness process bug defaulted entire corpus to ERROR.

### Duplicated Implementations Drift
_tree_is_garbled vs _flat_text_is_garbled repeat identical digit-ratio floor bug; decide_ocr_mode vs decide_ocr_strategy silently diverge in parameter passing; local vs remote bidi normalization run different code versions.

### Threshold-Tuning Ratchet
Widening threshold reveals previously-masked defects at new edge; tightening reveals different set and regresses previously-passing docs — five RFCs (022, 024, 025, 026, 033) each fixed and re-broke this same boundary.

### Detection Without Wired Remediation
Garble detection fires correctly at verdict stage, but OCR-recovery escalation gated on narrower set of early-stage validation reasons — correctly-detected garbled document never reaches recovery hook.

### Process Safeguards Substitute for Root-Cause Fixes
RFC-025 D4 mandatory pre-publish MinIO re-verification prevents publishing wrong numbers but does not fix scoring-harness bug that produces them.

### Manually-Maintained Enumerations Drift
11-step erasure manifest, raise-set of gate reasons triggering LowQualityTreeError drift out of sync with actual storage-write/gate-evaluation code — discovered missing only by audit.

### Compliance by Convention, Not Invariant
HR#2 (erasure cascade) and HR#4 (AGPL) satisfied by best-effort code paths (fire-and-forget registry delete, unconditional converter chain-walk) rather than enforced invariant.

---

## Quick Reference

### By Severity Level

**Critical (2 zones):**
- Zone 1: Verdict-Gate Threshold Cascade
- Zone 2: Garble Detection Kernel

**High (4 zones):**
- Zone 3: OCR Recovery Conflation
- Zone 4: Converter Chain AGPL
- Zone 5: Dual-Writer Consistency
- Zone 6: Bidi/RTL Split

**Medium (2 zones):**
- Zone 7: Erasure Cascade
- Zone 8: Audit Tooling Blind Spots

### By Affected Component

| Component | Zones |
|-----------|-------|
| Verdict pipeline | 1, 5 |
| Garble detection | 2, 3, 6 |
| OCR escalation | 3 |
| Converter chain | 4, 6 |
| Storage/consistency | 5, 7 |
| Bidi/RTL processing | 6 |
| Audit/measurement | 8 |

### Hard Rules Violated

- **HR#2** (right-to-erasure cascade): Zone 7 — manual manifest enumeration drifts
- **HR#4** (AGPL awareness): Zone 4 — structural failures silently advance to AGPL
- **HR#5** (never silently persist low-quality trees): Zone 1 — hysteresis reclassifies zero-content as MARGINAL

---

## Audit Methodology

This audit was structured around:
1. **Chain analysis**: Evidence cross-referenced to specific RFC chains (1–30+)
2. **Code evidence**: Line-specific citations from source files
3. **Mechanism classification**: Identifying root patterns (cascade, conflation, divergence, drift, gap)
4. **Cross-cutting themes**: Mapping common failure modes across zones

See [[ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3]] for full audit report.

