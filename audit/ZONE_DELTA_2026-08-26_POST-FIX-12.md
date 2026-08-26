# Zone Delta Analysis — POST-FIX-12

**Current audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-12.md  
**Prior audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-24_POST-FIX-11.md  
**Date:** 2026-08-26

## Summary

The re-audit post-FIX-12 shows a net reduction of 34 bugs (83→49 total defects) with 5 zones improved, 2 regressed to critical, 1 new zone identified, and 3 zones closed. The improvements were driven primarily by remediation commits landing verdict-promotion and multi-store dual-write consistency fixes (git d4c5ef2, d063b37, 512c36c), but two high-impact zones escalated to critical due to newly instrumented bypass mechanics: the verdict-promotion image_enrichment_promoted escape hatch (explicitly hard-coded priority=100 override) and the OCR pipeline re-entry hazard (UNIFIED_OCR_PLAN_ENABLED branch circumventing full_page_already_applied guard). Meanwhile, garble detection's NFKC ordering and circular self-inference persist despite narrowing the surface. The closed zones reflect audit scope changes rather than confirmed fixes (gate machinery and worker timeouts are simply outside this re-audit's focus).

## Delta Table

| Zone | Status | Severity (prior→current) | Bugs (prior→current) | Proposal Status | Key Change |
|------|--------|--------------------------|----------------------|-----------------|-----------|
| Garble Detection Surface Fragmentation | improved | critical→critical | 13→10 | partially_implemented | NFKC-before-detector ordering + circular expected_script persist; new null-detector-promoted-to-production regression (BIDI_COHERENCE_ENFORCE 0% TPR misread as safe) |
| Verdict Gate Promotion Bypass Cascade | regressed | high→critical | 10→8 | implemented_and_wired | image_enrichment_promoted (priority=100) explicitly locks to outrank structural hard-fail; bypass is deliberate hard-coded escape hatch |
| OCR Pipeline Flag Conflation and Re-entry Hazards | regressed | high→critical | 15→7 | implemented_not_wired | state.full_page_already_applied guard is circumvented by UNIFIED_OCR_PLAN_ENABLED branch; confirmed exploitable re-entry path |
| Content-Destructive Heuristic Chains | improved | critical→high | 11→6 | not_implemented | Table-fracturing mechanism narrowed to broader unconstrained markdown heuristics; table-span primitive not implemented |
| Verdict Persistence Competing Writers | improved | high→high | 10→5 | implemented_and_wired | MinIO-vs-Postgres divergence remediated (512c36c); Postgres upsert CAS designated true arbiter; MinIO sidecar still lacks CAS guard |
| Image Block Conflation and Marker Survival | improved | high→medium | 15→4 | not_implemented | RFC-018 marker-count-duplication workaround still causes unconditional OCR; image-ingestion path never wired to enrichment pipeline |
| Verified-Locally-Never-Deployed Fix Drift | improved | high→medium | 10→4 | no_proposal | 0%-TPR-detector-promoted-as-safe variant narrowed from staged-uncommitted code class; most staged code eventually landed |
| Tree-vs-Flat Gate Asymmetry | closed | critical→n/a | 14→0 | — | gates.py/types.py/GATES machinery not referenced in current audit scope |
| Worker/Inspector Dual-Threshold and Timeout Race | closed | medium→n/a | 6→0 | — | subprocess_mgr.py and job.py outside current audit scope |
| HR3 PII Egress Gap (Docling + VLM Silent Degradation) | closed | medium→n/a | 4→0 | — | server.py HR3 startup gate outside current audit scope; should be independently re-verified |

## Per-Zone Details

### Garble Detection Surface Fragmentation
- **Status:** improved (13→10 bugs)
- **Severity:** critical (unchanged)
- **Proposal:** partially_implemented
- **Change:** The zone remains matched to pre-fix 'Garble Detection Prong Blindness (NFKC, Script Threading, Title Inspection)'. The core architectural problem—NFKC normalization before detector evaluation, circular expected_script self-inference—persists structurally, but the addressable bug surface narrowed via constraints. A new regression emerged: BIDI_COHERENCE_ENFORCE was promoted to default-true after measuring 0% true-positive rate, which was misinterpreted as 'zero violations = safe' rather than 'detector unreliable / needs retooling'. The severity remains critical because the NFKC ordering and circular inference continue to blind detection on mixed-script and Unicode-variant tables.

### Verdict Gate Promotion Bypass Cascade
- **Status:** regressed (10→8 bugs)
- **Severity:** high→critical (escalated)
- **Proposal:** implemented_and_wired
- **Change:** Matched to the promotion-cascade portion of pre-fix 'Verdict Threshold Oscillation and Dual-CAS Divergence'. The threshold-widening / hysteresis jitter evolved into a precisely identified, reproducible bypass: `image_enrichment_promoted` (priority=100) is explicitly hard-coded to outrank structural hard-fail verdicts, deliberately allowing 2-3-block / 38-123-char documents to PASS despite failing content gates. The bug count fell (10→8) but severity escalated to critical because this is now a named, hard-coded escape hatch rather than emergent threshold drift—the bypass is intentional and actively maintained. Git commits d4c5ef2 (verdict promotion quality gate refactor) and d063b37 (contract tests) landed against this zone; Obsidian wave-2 proposal 'Verdict Promotion / Quality Gate Stack' is marked implemented.

### OCR Pipeline Flag Conflation and Re-entry Hazards
- **Status:** regressed (15→7 bugs)
- **Severity:** high→critical (escalated)
- **Proposal:** implemented_not_wired
- **Change:** Matched to the OCR-flag portion of pre-fix 'Picture Enrichment / OCR Filter Composition'. The broader filter-composition churn (coverage/clip-text/forced-OCR interactions) resolved partially, narrowing into a precisely characterized cross-module re-entry bug: `state.full_page_already_applied` is set in client/recovery.py and read as a guard in picture_plane.py, but the UNIFIED_OCR_PLAN_ENABLED branch explicitly short-circuits before the guard is ever checked. Severity escalated to critical because the hazard is now confirmed and exploitable—a deliberate re-entry path rather than diffuse filter interaction. Test commit ba9a62b references OCR remediation, but the guard-bypass itself remains unfixed.

### Content-Destructive Heuristic Chains
- **Status:** improved (11→6 bugs)
- **Severity:** critical→high (improved)
- **Proposal:** not_implemented
- **Change:** Matched to pre-fix 'Pre-Tree Text Transforms vs Table/Block Integrity'. The specific table-fracturing mechanism (heading injection / ordinal splitter destroying pipe-tables before _segment_table_nodes) resolved, but defects migrated to a broader class of unconstrained markdown heuristics (fence-marker parity toggle causing total content loss, ToC-heading depth guard itself over-stripping). The shared table-span primitive proposed in prior audit (compute_table_spans / is_inside_table) was not implemented. Both bug count and severity improved, indicating the defect class narrowed rather than closed; residual risk remains at the heuristic-composition level.

### Verdict Persistence Competing Writers
- **Status:** improved (10→5 bugs)
- **Severity:** high (unchanged)
- **Proposal:** implemented_and_wired
- **Change:** Matched to the dual-CAS/multi-store portion of pre-fix 'Verdict Threshold Oscillation and Dual-CAS Divergence'. The MinIO-sidecar-vs-Postgres divergence was remediated via multi-store dual-write consistency fix (git 512c36c): Postgres `upsert_doc/_verdict_priority_expr` CAS is now designated the true arbiter, with coordinated writes to both stores. Residual gap: the MinIO sidecar still has no CAS guard equivalent to `force_verdict_override`, so a failed backfill can leave the sidecar disagreeing with Postgres. Obsidian wave-3 proposal 'Multi-Store Dual-Write Consistency' is committed, though its frontmatter status was left at 'triaged'.

### Image Block Conflation and Marker Survival
- **Status:** improved (15→4 bugs)
- **Severity:** high→medium (improved)
- **Proposal:** not_implemented
- **Change:** Matched to the image-block/marker portion of pre-fix 'Picture Enrichment / OCR Filter Composition'. The RFC-018 D0 marker-count-duplication workaround still causes per-picture OCR to fire unconditionally and relocate text invisibly into the image namespace, and the standalone image-ingestion path (client.py image branch) is never wired to the enrichment pipeline. However, the overall filter chain stabilized enough that both bug count and severity improved substantially (15→4 bugs, high→medium), indicating the defect class narrowed even without the proposed fix.

### Verified-Locally-Never-Deployed Fix Drift
- **Status:** improved (10→4 bugs)
- **Severity:** high→medium (improved)
- **Proposal:** no_proposal
- **Change:** Matched to pre-fix 'Recovery Routing Wiring Gaps (Detection Without Remediation)', specifically the 'fixed but never wired/committed' failure class (chunked_docling_timeout_s, _check_bidi_coherence, RFC-034 D19 enrichment guard staged-never-committed). Most flagged staged-but-uncommitted code was eventually landed (RFC-036 D2 finally committed the already-staged enrichment guard), substantially narrowing this class. The residual pattern is now specifically the 0%-TPR-detector-promoted-as-safe variant, which links to the garble-detection regression (BIDI_COHERENCE_ENFORCE).

## New Zones

### Landscape/Rotation and Remote Route Divergence
- **Severity:** (unspecified; flagged for investigation)
- **Status:** newly identified
- **Description:** A new zone emerged in the re-audit. This zone concerns mismatch between local document orientation/rotation handling and remote route execution divergence (behavior differs between local processing and remote MinIO/Docling service paths). Requires root-cause analysis to determine severity and impact scope.

## Closed Zones

### Tree-vs-Flat Gate Asymmetry
- **Prior severity:** critical (14 bugs)
- **Closure reason:** scope exclusion
- **Note:** gates.py, types.py, and the GATES/decide_route machinery that drove this zone's flat-routing bypass mechanism do not appear in the current re-audit's key_files. This reflects the audit's current scope focus on high-velocity extraction and verdict pipeline zones, not confirmed remediation. This zone should be independently re-verified in a future gates-focused audit.

### Worker/Inspector Dual-Threshold and Timeout Race
- **Prior severity:** medium (6 bugs)
- **Closure reason:** scope exclusion
- **Note:** subprocess_mgr.py and job.py—the sites of the inspector-confidence / 16.5x-timeout-multiplier mismatch—do not appear in any current zone's key_files or mechanism. This reflects audit scope narrowing, not confirmed fixes. Worker timeout behavior should be independently audited.

### HR3 PII Egress Gap (Docling + VLM Silent Degradation)
- **Prior severity:** medium (4 bugs)
- **Closure reason:** scope exclusion
- **Note:** server.py's HR3 startup gate and the Docling-remote-service / VLM-fallback bypass paths are absent from every current zone's key_files. CLAUDE.md Hard Rule 3 still mandates per-call ZDR routing for PII documents; this enforcement should be independently re-verified rather than assumed resolved. It is outside this audit's current extraction/verdict focus.

## Key Takeaways

1. **Two zones escalated to critical severity:** Verdict-promotion bypass (image_enrichment_promoted hard-coded override) and OCR re-entry hazard (UNIFIED_OCR_PLAN_ENABLED circumventing guard) now represent the highest-risk defects post-FIX-12.

2. **Net progress masked by deliberate architectural choices:** The 34-bug reduction (83→49) reflects genuine narrowing of addressable defects, but two zones improved in count while worsening in severity, indicating that prior fixes encoded deliberate (if risky) escape hatches rather than fully addressing root causes.

3. **Three zones closed via scope exclusion, not remediation:** Tree-vs-Flat gate, worker timeouts, and PII egress gaps are outside this audit's focus but warrant independent re-verification.

4. **One new zone flagged:** Landscape/rotation and remote route divergence requires investigation.

5. **Structural barriers remain:** NFKC ordering before detection, circular self-inference, unconstrained markdown heuristics, and marker-count workarounds persist because their fixes require broader refactoring not yet attempted.
