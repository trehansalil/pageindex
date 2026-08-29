---
title: Architecture Defect Zones Index
audit_date: 2026-08-12
audit_run: POST
tags:
  - zone-index
total_zones: 7
total_bugs: 35
---
# Architecture Defect Zones Index

**Audit Date:** 2026-08-12 | **Audit Run:** POST  
**Source:** [[audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md]]

## Zone Summary (Priority-Ordered by Severity & Impact)

| Priority | Zone | Severity | Bugs | Status | Key Issue |
|----------|------|----------|------|--------|-----------|
| 1 | [[verdict-gate-cascade]] | CRITICAL | 11 | **fix-spec approved (rev 2, source-verified)** | First-match-wins promotion pipeline — VG-1..VG-7 + partition characterization test; rev 1 retracted for unverified symbols |
| 2 | [[garble-detection-kernel]] | CRITICAL | 7 | audited | 15+ caller shared choke point with NFKC destruction problem |
| 3 | [[ocr-recovery-cascade]] | HIGH | 5 | audited | Detection without wired remediation; single kill-switch |
| 4 | [[measurement-and-audit-self-reinforcing-blind-spot]] | HIGH | 4 | **fix-spec approved** | Audit tooling inherits pipeline's blind spots — export helpers + SKILL.md measurement rule spec'd 2026-08-29 |
| 5 | [[dual-write-consistency-model]] | HIGH | 3 | audited | Asymmetric write-visibility across dual writers |
| 6 | [[converter-pipeline-and-deployment-gap]] | HIGH | 3 | audited | AGPL fallback on structural failures; remote service drift |
| 7 | [[erasure-cascade-manually-maintained-manifest]] | MEDIUM | 2 | audited | Manual manifest with no mechanical derivation |

**Total Attributed Bugs: 35**

---

## Zone Stability Policy

**Pinned as of 2026-08-29.** The 7 canonical zones listed above are
stable identifiers. Audit workflows MUST report findings INTO these
zones rather than re-discovering zone boundaries from scratch.

Rules:
1. **No merging or closing** a pinned zone without explicit human approval.
2. **New zones** may be added if the audit discovers a defect cluster that
   does not fit any existing zone. New zones are marked `(new)` and require
   human review before becoming pinned.
3. **Zone names** are the kebab-case filenames (e.g. `verdict-gate-cascade`).
   Audit agents must use these exact names, not synonyms or rewordings.
4. **Bug counts** may change freely — zones improve or regress based on
   code changes. Only the zone *identity* is pinned, not the bug count.

This policy exists because prior audit cycles re-drew zone boundaries
each run, creating the illusion of zones "closing" (bugs reclassified,
not fixed) and "coming back" (same bugs, new zone name).

---

## Key Cross-Cutting Themes

1. **Silent degradation defeats the gate** - Fallback mechanisms produce false-clean output that slips past quality gates
2. **Coupled kill-switches and shared kernels** - One flag/function serves multiple subsystems; fixes ripple unpredictably
3. **Fixes land locally but never reach production** - RFC-033's bidi guard never committed; remote services run different code
4. **Diagnostic blind spots** - Audit tooling replicates pipeline defects; bugs agree with each other
5. **Duplicated implementations drift** - _tree_is_garbled vs _flat_text_is_garbled repeat identical bugs independently
6. **Threshold-tuning ratchet** - Five consecutive RFCs fixed and re-broke the same verdict boundary
7. **Detection without wired remediation** - Garble correctly detected but recovery never fires
8. **Process safeguards substitute for fixes** - Pre-publish verification prevents publishing wrong numbers but leaves root bugs
9. **Manually-maintained enumerations** - Erasure manifest and gate-raise-set have no mechanical derivation
10. **Compliance by convention** - Hard Rules #2 & #4 satisfied by code paths, not enforced invariants

---

## Zone Relationships

```
verdict-gate-cascade
  ├─ depends on garble-detection-kernel (GATE_TABLE severity)
  ├─ blocks ocr-recovery-cascade (severity ordering)
  └─ measured by measurement-and-audit-self-reinforcing-blind-spot

garble-detection-kernel
  ├─ shared by verdict-gate-cascade
  ├─ shared by ocr-recovery-cascade
  └─ affected by converter-pipeline-and-deployment-gap (AGPL converters)

ocr-recovery-cascade
  ├─ depends on verdict-gate-cascade (gate reasons)
  ├─ depends on garble-detection-kernel (detection)
  └─ controlled by converter-pipeline-and-deployment-gap (remote routing)

dual-write-consistency-model
  └─ underpins erasure-cascade-manually-maintained-manifest (registry state)

converter-pipeline-and-deployment-gap
  ├─ affects garble-detection-kernel (different converters)
  └─ underpins dual-write-consistency-model (remote writes)
```

---

## Remediation Priority

### Phase 1: Root-Cause Fixes (Zones 1 & 2)
- Decouple verdict-gate cascade ordering from GATE_TABLE severity
- Mechanically derive gate-raise-set from gate definitions
- Consolidate garble-detection shared kernel; eliminate duplicate implementations
- Fix NFKC destruction; ensure presentation-form detection works

### Phase 2: Detection-to-Remediation (Zone 3)
- Split _OCR_ESCALATION kill-switch into page-level and per-picture controls
- Widen OCR-recovery eligibility to match full GATE_TABLE evaluation
- Implement marker-removal step to complement coverage filter

### Phase 3: Consistency & Compliance (Zones 4, 5, 6)
- Mechanically derive erasure-manifest from storage-write code
- Version-gate remote Docling; enforce contract parameters
- Unify local vs remote bidi normalization

### Phase 4: Audit Infrastructure (Zone 4)
- Fix measurement blind spot: block.get('text','') for all block types
- Regenerate ground-truth corpus validation before publishing metrics

---

## Audit Evidence

All zones verified via:
- Direct code inspection (verified with get_code_snippet)
- Commit history analysis (git log -S)
- Execution trace analysis (chain_XY evidence identifiers)
- MinIO meta.json spot checks

**Audit Time:** 2026-08-12  
**Audit Method:** Structured zone analysis with per-bug evidence chains  
**Next Audit:** Post-remediation verification recommended
