# Zone Delta Analysis — POST-FIX-WAVE4

**Current audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE4.md  
**Prior audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md  
**Date:** 2026-08-29

---

## Summary

Post-FIX-WAVE4 audit reveals net improvement in bug count (−2 aggregate) across 8 active zones, but with concerning severity inversions: the Dual-Writer verdict persistence zone regressed from High to Critical (5 independent writers now discovered vs. 2 prior), and Measurement/Audit tooling blind spots elevated from Medium to High despite holding bug count steady. Three zones showed genuine improvement (threshold cascade, garble detection, OCR recovery), but two critical-severity zones remain stalled (converter chain fallback, erasure cascade) with no movement toward resolution. One zone (Bidi/RTL local-vs-remote) was closed through merger into normalize-before-detect, but surfaced a new config-layer bifurcation zone that was previously invisible. Overall posture: architectural drift is slowing, but verdict persistence and audit tooling remain high-risk.

---

## Delta Table

| Zone | Status | Severity | Bugs | Proposal | Key Change |
|------|--------|----------|------|----------|------------|
| Verdict-Gate Threshold/Promotion/Override Cascade → ExtractionState route/ok multi-writer cascade | improved | crit→crit | 8→7 | not_implemented | Reframed as ExtractionState.ok/route/gate_result, but five separate recovery methods still assign state without re-deriving; order-dependent multi-writer problem persists under new name |
| Garble Detection Cross-Cutting Kernel → Normalize-before-detect null-detector lattice (presentation forms / NFKC) | improved | crit→crit | 7→6 | no_proposal | Absorbed Bidi/RTL zone's NFKC/presentation-forms mechanism; _renormalize_bidi_guarded now sole producer, but recovery paths still actively null it out |
| OCR Recovery Cascade and Kill-Switch Conflation → Recovery dispatch: tuple-keyed dedup and unguarded raising normalizers | improved | high→high | 6→4 | no_proposal | decide_ocr_mode now forwards document_type/ocr_langs; tuple-keyed dedup still double-fires on NODE_COUNT_LOW+DEPTH_LOW co-occurrence; TessdataUnavailableError handling inconsistent across call sites |
| Converter Chain Fallback and AGPL Gating → Ordered-policy converter chain with load-bearing branch order | stalled | high→high | 4→4 | no_proposal | RETRY branch's bare `continue` advances to NEXT chain entry instead of re-entering same one; first transient failure can walk past BLOCK_AGPL into AGPL converter — same HR4 exposure, new code path |
| Dual-Writer Verdict Persistence and Consistency Model Split → Split verdict authority: five writers over two stores | regressed | high→crit | 4→5 | no_proposal | Writer count grew (2→5): save_doc_meta, _upsert_registry_row, registry_backfill/reconcile.py, registry_backfill/backfill.py, promotion_sweep.py; backfill.py's comment 'CAS guard protects against clobbering' contradicted by save_doc_meta's actual implementation (no priority comparison) |
| Erasure Cascade and Storage Consistency Drift → Order-coupled erasure manifest with implicit inter-step data flow | stalled | med→med | 2→2 | no_proposal | No structural change; hand-maintained manifest with implicit inter-step dependencies; missed purges log at DEBUG instead of surfacing errors |
| Measurement/Audit Tooling Shared Blind Spots → Divergent parallel garble/text accessors | regressed | med→high | 4→4 | partially_implemented | Header-only-table fix landed in _flat_block_primary_text (98b5038), but audit confirms multiple near-identical accessors (_flat_search_text, flat.py table branch, detect_garble vs. whole-tree fallback) remain unfixed; severity raised High despite constant bug count |
| Config-layer bifurcation: frozen snapshot vs live os.environ | new | — | — | — | Newly discovered: frozen pipeline_config snapshot vs live os.environ re-reads (BIDI_COHERENCE_ENFORCE truthiness mismatch, LEAF_SPLIT_RATIO re-read at call time defeating import-time coupling assertion, PRE_GARBLE_FORCE_OCR_ENABLED double-source) |

---

## Per-Zone Details

### 1. Verdict-Gate Threshold/Promotion/Override Cascade → ExtractionState route/ok multi-writer cascade
**Status:** improved (bug count 8→7)  
**Severity:** critical (unchanged)

The original threshold/promotion cascade zone file was deleted in commit 083aa6e as "resolved," but the underlying order-dependent, multi-writer verdict problem resurfaced under a new conceptual name centered on ExtractionState.ok/route/gate_result instead of apply_promotions. The zone no longer focuses on promotion logic per se, but on the fact that five separate code paths assign state.route or state.ok without re-deriving them from the authoritative gate_result value. Bug count dropped marginally (8→7) but severity remains critical. **Proposal Status:** not_implemented — no authoritative fix committed.

### 2. Garble Detection Cross-Cutting Kernel → Normalize-before-detect null-detector lattice (presentation forms / NFKC)
**Status:** improved (bug count 7→6)  
**Severity:** critical (unchanged)

The garble-kernel zone absorbed the Bidi/RTL zone's NFKC/presentation-forms null-detector mechanism (previously a separate High-severity zone). The flag had_presentation_forms now has exactly one live producer (_renormalize_bidi_guarded) versus a dead 'Arabic' string-comparison fallback in detect_garble. However, recovery paths (_execute_ocr_retry, _recover_vlm_fallback) still actively null out the one carrier that exists, perpetuating the structural unproducibility problem on most paths. Net bug count nudged down (7→6) but the core mechanism remains fragile. **Proposal Status:** no_proposal.

### 3. OCR Recovery Cascade and Kill-Switch Conflation → Recovery dispatch: tuple-keyed dedup and unguarded raising normalizers
**Status:** improved (bug count 6→4)  
**Severity:** high (unchanged)

The decide_ocr_mode function now forwards document_type and ocr_langs, closing one prior bug. The single _OCR_ESCALATION kill-switch conflation is narrower. Remaining defects shifted to a tuple-keyed dedup set that still double-fires _recover_image_dominant_ocr when both NODE_COUNT_LOW and DEPTH_LOW gates are true. Additionally, ensure_tessdata's new TessdataUnavailableError is caught in one call site but raised bare in another (image-extension branch), producing inconsistent ERROR-vs-MARGINAL outcomes. **Proposal Status:** no_proposal.

### 4. Converter Chain Fallback and AGPL Gating → Ordered-policy converter chain with load-bearing branch order
**Status:** stalled (bug count 4→4)  
**Severity:** high (unchanged)

Commit 0625ecb ("gate the AGPL structural fallback and close remote converter drift") closed the unconditional structural-failure-walks-to-AGPL bug, but a new bug of equal severity took its place: the RETRY branch's bare `continue` advances to the NEXT chain entry instead of re-entering the same one. With the default retry count of 1, the very first transient failure of the primary converter can walk straight past BLOCK_AGPL into an AGPL converter anyway — same net HR4 exposure, different code path. **Proposal Status:** no_proposal.

### 5. Dual-Writer Verdict Persistence and Consistency Model Split → Split verdict authority: five writers over two stores
**Status:** regressed (bug count 4→5, severity high→critical)  
**Severity:** critical (elevated)

The zone file was deleted in 083aa6e as "resolved," but the writer count grew from two (save_doc_meta, _upsert_registry_row) to five: adding registry_backfill/reconcile.py, registry_backfill/backfill.py, and promotion_sweep.py. The audit now identifies a false belief baked into backfill.py's own comment: "the CAS guard in save_doc_meta protects against clobbering a newer verdict" — contradicted by save_doc_meta's actual implementation, which has no priority comparison at all. Severity raised from High to Critical to reflect that corpus audits reading sidecars can now disagree with the registry through more independent paths than before. **Proposal Status:** no_proposal.

### 6. Erasure Cascade and Storage Consistency Drift → Order-coupled erasure manifest with implicit inter-step data flow
**Status:** stalled (bug count 2→2)  
**Severity:** medium (unchanged)

No structural change: the erasure manifest remains a hand-maintained ordered list with implicit inter-step data dependencies (doc_name recovered mid-cascade, sha256 read from a sidecar file that a later required step deletes). Steps marked required=False allow missed purges to log at DEBUG as "expected miss" rather than surfacing as an error. **Proposal Status:** no_proposal.

### 7. Measurement/Audit Tooling Shared Blind Spots → Divergent parallel garble/text accessors
**Status:** regressed (severity medium→high, bug count 4→4)  
**Severity:** high (elevated)

The block.get('text','') blind spot for table/image blocks was partially closed (Zone-9 header-only-table fix landed in _flat_block_primary_text per commit 98b5038), but the audit now documents that this is only one of several near-identical text accessors: _flat_search_text, the flat.py table branch, and detect_garble vs. the _garble_check_nodes whole-tree fallback (which bypasses detect_garble's presentation-forms recovery heuristic entirely). The fix closed one instance while confirming the zone has no single choke point. Severity raised from Medium to High even though bug count held at 4. **Proposal Status:** partially_implemented.

---

## New Zones

### Config-layer bifurcation: frozen snapshot vs live os.environ

**Severity:** undetermined (high risk)  
**Discovered:** 2026-08-29

A new zone newly surfaced by audit; no prior zone described the frozen pipeline_config snapshot vs live os.environ re-reads pattern. Three specific mismatches identified:

- BIDI_COHERENCE_ENFORCE: frozen at import time; live os.environ truthiness mismatch can occur across processing stages
- LEAF_SPLIT_RATIO: re-read at call time, defeating the import-time coupling assertion  
- PRE_GARBLE_FORCE_OCR_ENABLED: double-sourced (both frozen and live)

This configuration drift can cause different behavior between the initial pipeline_config snapshot and runtime re-reads, introducing subtle state inconsistencies. **Proposal Status:** none yet.

---

## Closed Zones

### Bidi/RTL Processing Split (Local vs. Remote) — absorbed into Normalize-before-detect null-detector lattice (presentation forms / NFKC)

**Closed:** 2026-08-29  
**Reason:** Merger into dominant mechanism

The Bidi/RTL local-vs-remote deployment-drift zone was closed through conceptual merger into Zone-2 (Normalize-before-detect null-detector lattice). The core claim of the original zone—that NFKC destroys the Arabic presentation-form signal, creating a null-detector fallacy—is now the dominant mechanism of the merged zone rather than a standalone local-vs-remote story. 

Note: The heading-guard-never-committed sub-issue is not mentioned in the post-fix zone and was not separately verified as fixed; this represents a potential documentation gap in the closure.

