# Zone Delta Analysis — POST-FIX-WAVE3-VERIFY

**Current audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE3-VERIFY.md  
**Prior audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md  
**Date:** 2026-08-29

## Summary

Fix-Wave-3 verification reduced the active defect zone count from 8 to 6 (2 zones absorbed into larger patterns) and cut the aggregate bug count by 16 (63→47), driven primarily by the VG-1..VG-7 verdict-gate promotion cascade fix (commit 9ccc6f1, bugs: 8→6), the garble-detection cross-cutting kernel dispatch narrowing (bugs: 7→4), and the closure of the OCR double-pass re-entry bug (bugs: 6→3). However, four zones remain at critical or high severity — verdict promotion still exhibits threshold-ratcheting false-passes, garble detection retains 11 divergent call sites, the remote Docling boundary still lacks version enforcement, and measurement tooling was re-rated from medium to high severity following root-cause tracing of the GHV-TKV 96.1% under-count to naive text-extraction sites outside the audited verdict path.

## Delta Table

| Zone | Status | Severity (prior→current) | Bugs (prior→current) | Proposal Status | Key Change |
|---|---|---|---|---|---|
| Verdict Promotion / Threshold Ratchet | improved | critical→critical | 8→6 | partially_implemented | VG-1..VG-7 fixes (commit 9ccc6f1) reduced promotion-cascade bugs by computing image-enrichment garble check once and sharing between D1/D2, but apply_promotions remains ordered if/elif priority cascade with source_selection clamp-bypass still suppressing structural caps |
| Garble Detection Cross-Cutting Kernel→Dispatch Gap | improved | critical→critical | 7→4 | no_proposal | GATE_TABLE dispatch gap narrowed via D4 garble-priority override and _eligible_image_dominant all-defects check, but D4 only covers GARBLING/NODE_GARBLING pair — any newly-added lower-severity gate can still shadow garble detection |
| Converter Chain Fallback and AGPL Gating→Remote Service Boundary Drift | stalled | high→high | 4→4 | no_proposal | GATE_AGPL_STRUCTURAL now explicit and operator-gateable; RFC-033's _heading_is_logical_order bidi guard still never committed to git, and remote Docling service still unversioned with incomplete remote_version_enforce |
| OCR Recovery Cascade and Kill-Switch Conflation→OCR Pipeline Conflation | improved | high→high | 6→3 | no_proposal | Zone-2 re-entry guard (force_full_page_ocr_applied) closed double-Tesseract duplication; decide_ocr_mode now forwards document_type/ocr_langs; root conflation of per-picture and full-page OCR strategies in pdf_to_markdown_docling remains |
| Measurement/Audit Tooling Shared Blind Spots→Content Measurement Blind Spot | regressed | medium→high | 4→3 | no_proposal | Role-aware helpers (_flat_block_primary_text/_flat_search_text) now exist in RFC-022 B3 verdict; re-rated high severity following GHV-TKV 96.1% char under-count traced to naive block.get('text','') sites outside verdict.py — audit harness itself never updated to use correct helper |
| Dual-Writer Verdict Persistence and Consistency Model Split→Verdict Persistence Asymmetry | improved | high→medium | 4→2 | no_proposal | reconcile.py load-bearing drain-verdict-retry-queue ordering fix landed; write_verdict confirmed zero live production callers; save_doc_meta still deliberately omits write-visibility barrier (archival-only by design) |

## Per-Zone Details

### Zone 1: Verdict Promotion / Threshold Ratchet

**Status:** Improved  
**Severity:** critical→critical  
**Bugs:** 8→6 (−25%)

Commit 9ccc6f1 ("fix(zone1): verdict-gate promotion cascade — VG-1..VG-7") refactored the promotion-cascade logic to compute the image-enrichment garble check once (in D1) and share the result between D1 and D2 verdicts. This consolidation eliminated 2 redundant checks and reduced the cascade-ordering hazards. However, the root architectural pattern — `apply_promotions` implemented as a strict if/elif priority cascade with source_selection clamp-bypass exemptions — remains unchanged. New insights into the threshold-ratchet pattern (widen one edge, admit false-PASS at the new edge) confirm that each successive promotion can relax a structural constraint while the ordered dispatch prevents earlier gates from firing on the now-relaxed input. Proposal for ordered-dispatch refactor is partially implemented but stalled.

### Zone 2: Garble Detection Cross-Cutting Kernel → Detection-Remediation Dispatch Gap

**Status:** Improved  
**Severity:** critical→critical  
**Bugs:** 7→4 (−43%)

The Zone-1 fix introduced the D4 garble-priority override, which elevates GARBLING and NODE_GARBLING to top priority in the GATE_TABLE dispatch, and also added the _eligible_image_dominant all-defects check to ensure image-enrichment side-effects never mask garble signals. These changes narrowed the cross-cutting garble/dispatch interaction from 7 to 4 confirmed bugs. However, detect_garble retains 11 divergent call sites across 9+ subsystems, and the D4 override only covers the GARBLING/NODE_GARBLING pair — any newly-added lower-severity gate (e.g., PRESENTATION_FORM) can still shadow garble detection before it fires. The fundamental decoupling of detection (scattered across workers) from remediation dispatch (GATE_TABLE order) remains unresolved.

### Zone 3: Converter Chain Fallback and AGPL Gating → Converter Chain / Remote Service Boundary Drift

**Status:** Stalled  
**Severity:** high→high  
**Bugs:** 4→4 (no change)

The zone absorbed the former Bidi/RTL Processing Split zone during consolidation. GATE_AGPL_STRUCTURAL is now an explicit, metricked, operator-gateable policy (previously an unnamed WALK fallthrough that only logged a warning). However, this zone's two unresolved defects persist:

1. **RFC-033 bidi-heading guard never committed:** _heading_is_logical_order bidi guard appears 0 times in src/; the proposed local fallback for heading-order detection remains unimplemented.
2. **Remote Docling unversioned:** The remote Docling microservice has no version enforcement in pageindex; remote_version_enforce is incomplete. Local fixes to tree quality gates have zero effect on remotely-routed documents.

Net severity and bug count unchanged.

### Zone 4: OCR Recovery Cascade and Kill-Switch Conflation → OCR Pipeline Conflation

**Status:** Improved  
**Severity:** high→high  
**Bugs:** 6→3 (−50%)

The Zone-2 re-entry guard (force_full_page_ocr_applied) was added to prevent double-Tesseract-pass duplication when force_full_page_ocr is re-invoked during recovery. Additionally, decide_ocr_mode now correctly forwards document_type and ocr_langs to pdf_to_markdown_docling, eliminating fallback-language and document-type mismatches. These changes reduced confirmed bugs from 6 to 3. However, the root conflation — one function (pdf_to_markdown_docling) handling both per-picture OCR enrichment and full-page OCR strategies — remains, and standalone images still bypass splice_figure_markers and _enrich_image_blocks entirely.

### Zone 5: Measurement/Audit Tooling Shared Blind Spots → Content Measurement Blind Spot

**Status:** Regressed  
**Severity:** medium→high  
**Bugs:** 4→3 (−25%)

Role-aware text helpers (_flat_block_primary_text, _flat_search_text) now exist and are used in RFC-022 B3's verdict computation, reducing raw bug count by 1. However, the zone was re-rated from medium to high severity after root-cause analysis of the GHV-TKV-Tarif corpus under-count (13,022 raw characters vs. 375 measured; 96.1% deficit) traced directly to naive block.get('text','') calls in measurement sites outside verdict.py. The audit harness itself was never updated to use the correct role-aware helpers, meaning production measurement tooling is still blind to the correct extraction pattern. This zone now supersedes the prior Measurement Blind Spot as the highest-priority correctness issue in the audit path.

### Zone 6: Dual-Writer Verdict Persistence and Consistency Model Split → Verdict Persistence Asymmetry

**Status:** Improved  
**Severity:** high→medium  
**Bugs:** 4→2 (−50%)

The zone absorbed the former Erasure Cascade and Storage Consistency Drift zone during consolidation. reconcile.py's load-bearing drain-verdict-retry-queue-before-MinIO-etag-diff ordering fix landed (commit b0a2435), and write_verdict is now confirmed to have zero live production callers, dropping bug count from 4 to 2. The severity was downgraded from high to medium. The absorbed Erasure Cascade zone's manifest-drift concern (preloaded/ prefix, ISS-41) is now tracked as one of this zone's four listed bug sources rather than a standalone zone. However, save_doc_meta still deliberately omits the write-visibility barrier (archival-only by design), meaning child/parent process-boundary desync remains structurally possible.

## New Zones

None.

## Closed Zones

1. **Bidi/RTL Processing Split (Local vs. Remote)** — Absorbed into Converter Chain / Remote Service Boundary Drift. Its unresolved defects (bidi-heading-guard-never-committed and remote-drift) are now reported as sub-issues of the larger zone rather than tracked as standalone.

2. **Erasure Cascade and Storage Consistency Drift** — Absorbed into Verdict Persistence Asymmetry as its 'Manual Erasure Manifest Drift' bug source (ISS-41 preloaded/ prefix gap). No longer tracked as a standalone zone; its concerns are subsumed into verdict-persistence asymmetry analysis.
