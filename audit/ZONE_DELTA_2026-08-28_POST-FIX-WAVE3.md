# Zone Delta Analysis — POST-FIX-WAVE3

**Current audit:** `audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md`  
**Prior audit:** `audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-27_POST-RUN20.md`  
**Date:** 2026-08-28

## Summary

Post-Fix-Wave3 remediation cycle closed with significant regression across the core verdict, garble, and OCR recovery zones. Of 7 prior zones, 4 regressed (verdict-gate, garble detection, OCR recovery, converter chain fallback), 1 stalled (Bidi/RTL), and 2 improved (measurement tooling, erasure cascade). A new zone—Dual-Writer Verdict Persistence—was discovered, bringing the total to 8 active defect zones. Cumulative bug count increased 14 points (24 → 38), driven by partial implementations that left prior bypasses intact while adding new paths that circumvent quality gates.

## Delta Table

| Zone | Status | Severity (prior→current) | Bugs (prior→current) | Proposal Status | Key Change |
|---|---|---|---|---|---|
| Verdict-Gate Threshold / Promotion / Override Cascade | regressed | critical → critical | 5 → 8 | partially_implemented | Reordered to source-code-order first-match-wins (RFC-040 D2), but _has_image_rescue bypass not deleted; new source_selection=True path now bypasses _apply_clamp entirely |
| Garble Detection Cross-Cutting Kernel | regressed | critical → critical | 5 → 7 | partially_implemented | Consolidation into shared detect_garble kernel done, but NFKC still runs before bidi signal for some callers; new config.garble_short_text_default hard override added |
| OCR Recovery Cascade and Kill-Switch Conflation | regressed | high → high | 4 → 6 | not_implemented | Kill-switch split and marker-removal-after-coverage-skip fixes not done; _OCR_ESCALATION still conflates page-level and per-picture; <!-- image --> markers leak into output; string-matching fragility in indexer.py gate-reason routing found |
| Converter Chain Fallback and AGPL Gating | regressed | medium → high | 2 → 4 | no_proposal | Severity escalated; structural (not transient) failures still silently walk chain to AGPL converters; remote Docling service has no version/contract enforcement |
| Bidi/RTL Processing Split (Local vs. Remote) | stalled | high → high | 3 → 3 | not_implemented | NFKC-reorder ahead of bidi coherence check and remote-image version-check assertion not implemented; only compensating heuristic (assume presentation forms when none survive) added as workaround |
| Measurement/Audit Tooling Shared Blind Spots | improved | high → medium | 3 → 4 | not_implemented | Severity dropped; core fixes (table-aware shared char-count signal, fail-loud scoring harness) not implemented; block.get('text','') and ERROR-default harness bug persist; self-reinforcing cycle characterized more precisely |
| Erasure Cascade and Storage Consistency Drift | improved | high → medium | 2 → 2 | no_proposal | Severity dropped; manually-maintained erasure-manifest largely unchanged; dual-write/consistency complexity split into new zone, narrowing scope |
| Dual-Writer Verdict Persistence and Consistency Model Split (NEW) | — | — | — | — | Extracted from Erasure zone; split logic for verdict dual-writes and cross-store consistency model |

## Per-Zone Details

### Verdict-Gate Threshold / Promotion / Override Cascade
**Status:** REGRESSED (5 → 8 bugs)

The promotion logic reorder to source-code-order first-match-wins (RFC-040 D2 style) was partially implemented. However, the proposed deletion of the `_has_image_rescue` bypass was not executed. A new code path (`source_selection=True`) was added that now entirely bypasses `_apply_clamp`, introducing 3 additional bugs. The gate logic tree is now harder to reason about and contains implicit contradictions between the reordered source-matching and the still-intact legacy bypass.

**Recommendation:** Complete the deletion of `_has_image_rescue` and audit the new `source_selection=True` path to ensure it feeds into `_apply_clamp` or equivalent quality check.

### Garble Detection Cross-Cutting Kernel
**Status:** REGRESSED (5 → 7 bugs)

Consolidation of duplicate tree/flat implementations into one shared `detect_garble` kernel appears complete. However, NFKC normalization still runs before the bidi presentation-form signal for some callers, creating a race condition. Additionally, a new hardcoded override (`config.garble_short_text_default`) was added that short-circuits detection logic for texts below a configured length threshold, introducing 2 additional bugs.

**Recommendation:** Enforce signal ordering: bidi detection → NFKC → garble detection. Review `config.garble_short_text_default` for correctness; consider deriving thresholds from corpus statistics rather than hardcoding.

### OCR Recovery Cascade and Kill-Switch Conflation
**Status:** REGRESSED (4 → 6 bugs)

The proposed kill-switch split (separating page-level OCR escalation from per-picture rescue) and marker-removal-after-coverage-skip fixes were not implemented. `_OCR_ESCALATION` remains a single switch that conflates both modes, and `<!-- image -->` marker artifacts still leak into final output. A new fragility was discovered in `indexer.py`'s gate-reason routing that relies on string matching, making error classification brittle across deployments.

**Recommendation:** Implement kill-switch split as proposed. Add post-processing step to remove residual HTML comments. Harden gate-reason routing via enum or structured constants rather than string literals.

### Converter Chain Fallback and AGPL Gating
**Status:** REGRESSED (2 → 4 bugs, severity medium → high)

No prior simplification proposal existed for this zone. Severity escalated to high because structural (not just transient) converter failures still silently walk the chain to AGPL-licensed converters (pymupdf, pymupdf4llm). The remote Docling service has no version-checking or contract enforcement, making silent degradation to different extraction semantics possible.

**Recommendation:** Add explicit version/contract check on remote Docling service before fallback. Trap structural converter failures and surface as non-recoverable errors (fail-loud) rather than silent chain walk.

### Bidi/RTL Processing Split (Local vs. Remote)
**Status:** STALLED (3 bugs, no change)

The proposed fixes (reorder NFKC ahead of bidi coherence check, remote-image version-check assertion) were not implemented. A compensating heuristic was added that assumes presentation forms survive when none do, functioning as a partial workaround but leaving the underlying inconsistency intact.

**Recommendation:** Implement the proposed NFKC reorder. Add version assertion for remote image processing that fails loudly if contract mismatches.

### Measurement/Audit Tooling Shared Blind Spots
**Status:** IMPROVED (3 → 4 bugs, severity high → medium)

Severity dropped from high to medium due to better characterization of the self-reinforcing blind-spot cycle. However, the two core proposed fixes—table-aware shared char-count signal and fail-loud scoring harness—were not implemented. The `block.get('text','')` fallback and ERROR-default harness bug both persist, and the bug count rose by 1 as the feedback loop was modeled more precisely.

**Recommendation:** Implement table-block-aware char-count signal. Replace ERROR default with UNSCORED state that forces explicit audit decision.

### Erasure Cascade and Storage Consistency Drift
**Status:** IMPROVED (2 bugs, severity high → medium)

Severity dropped from high to medium. The manually-maintained erasure-manifest mechanism remains largely unchanged. The complexity described in this zone's dual-write/consistency logic was factored into a new separate zone (Dual-Writer Verdict Persistence), narrowing this zone's remaining scope to pure cascade semantics.

**Recommendation:** Continue with current erasure-manifest approach; monitor the new Dual-Writer Verdict Persistence zone for consistency improvements that may reduce erasure complexity.

## New Zones

### Dual-Writer Verdict Persistence and Consistency Model Split
**Status:** NEW

Extracted from the Erasure Cascade zone during this audit cycle. This zone isolates the architectural complexity of maintaining verdict state across dual write paths (primary + audit log, or similar) and ensuring consistency during concurrent operations. Discovery of this zone signals that prior erasure-zone complexity analysis was conflating cascade semantics with persistence-layer concerns.

**Severity:** Not yet assigned.  
**Priority:** Medium (blocking further Erasure zone improvements).

## Closed Zones

None.
