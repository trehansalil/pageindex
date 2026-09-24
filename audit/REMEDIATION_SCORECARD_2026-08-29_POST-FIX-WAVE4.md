# Remediation Scorecard — POST-FIX-WAVE4 (2026-08-29)

**Pre-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md
**Post-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE4.md
**Delta report:** audit/ZONE_DELTA_2026-08-29_POST-FIX-WAVE4.md

## Verdict: REGRESSED

This cycle regressed across 2 zones (one escalated from High to Critical), stalled 2 others, and introduced 1 new zone through configuration-layer bifurcation introduced by prior fixes. Despite closing 1 zone, the net bug delta of -2 across 8 active zones masks a deeper architectural stall: wiring is fully clean (5/5 new symbols confirmed wired, no orphans), but the bottleneck is structural, not integrative. Two chronic Criticals (ExtractionState multi-writer verdict cascade at 7+ fix cycles; NFKC presentation-forms null-detector at 4+ cycles) continue showing falling bug counts amid static severity—the signature of symptom removal without root-cause elimination. No new fix waves should open against the regressed or chronic zones until their structural simplification proposals (already drafted in prior cycles) are implemented.

## Zones Closed (1)

| Zone Name | Was Severity | Bugs Eliminated |
|-----------|--------------|-----------------|
| Bidi/RTL Processing Split (Local vs. Remote) | high | 0 |

## Zones Remaining (7)

| Zone Name | Severity | Bug Count | Status |
|-----------|----------|-----------|--------|
| ExtractionState route/ok multi-writer cascade (was: Verdict-Gate Threshold/Promotion/Override Cascade) | critical | 7 | improved |
| Normalize-before-detect null-detector lattice (presentation forms / NFKC) (was: Garble Detection Cross-Cutting Kernel) | critical | 6 | improved |
| Split verdict authority: five writers over two stores (was: Dual-Writer Verdict Persistence and Consistency Model Split) | critical | 5 | regressed |
| Divergent parallel garble/text accessors (was: Measurement/Audit Tooling Shared Blind Spots) | high | 4 | regressed |
| Recovery dispatch: tuple-keyed dedup and unguarded raising normalizers (was: OCR Recovery Cascade and Kill-Switch Conflation) | high | 4 | improved |
| Ordered-policy converter chain with load-bearing branch order (was: Converter Chain Fallback and AGPL Gating) | high | 4 | stalled |
| Order-coupled erasure manifest with implicit inter-step data flow (was: Erasure Cascade and Storage Consistency Drift) | medium | 2 | stalled |

## New Zones (1)

| Zone Name | Severity | Introduced By |
|-----------|----------|---------------|
| Config-layer bifurcation: frozen snapshot vs live os.environ | high | Config/gating fixes in the 083aa6e..HEAD wave: pipeline_config is frozen at import time while gates.py (BIDI_COHERENCE_ENFORCE), tree_split.py (LEAF_SPLIT_RATIO) and indexer.py (PRE_GARBLE_FORCE_OCR_ENABLED) keep live os.environ re-reads with divergent truthiness parsing, so snapshot and runtime values can disagree and the import-time PASS_MAX_LEAF_RATIO <= LEAF_SPLIT_RATIO invariant guards a value nothing uses. |

## Metrics

| Metric | Value |
|--------|-------|
| Net bug delta | -2 |
| Total zones (active + closed) | 9 |
| Zones improved | 3 |
| Zones regressed | 2 |
| Zones stalled | 2 |
| Zones closed | 1 |
| Zones new | 1 |
| Wiring status | fully_wired (5/5 new symbols wired, 0 orphans) |
| Overall verdict | regressed |

## Recommended Next Steps

Treat this as a regressed cycle: 2 zones regressed (one High→Critical escalation), 2 stalled, 1 new zone introduced by the fix wave, only -2 net bugs across 8 zones. Wiring is clean (5/5 new symbols wired, 0 orphans), so the bottleneck is architectural, not integration.

### Prioritized Order of Work

1. **Stop the fix-one-miss-other pattern** by implementing the already-written simplification proposals instead of more point patches. Proposals have now gone unimplemented across multiple cycles and will continue blocking progress until adopted.

2. **'Split verdict authority: five writers over two stores'** (2 eng-days, low-medium risk, escalated to Critical; the false comment at registry_backfill/backfill.py:145 means engineers reason from a wrong model of save_doc_meta):
   - Strip 'verdict' from save_doc_meta's mergeable set
   - Make registry_mirror.py the sole required mirror
   - Delete sidecar self-heal writes in backfill.py:161/323 and promotion_sweep.py:124/141

3. **'Config-layer bifurcation'** (1 eng-day, low risk, brand-new):
   - Route all three call sites through pipeline_config
   - Export a single envbool parser
   - Land the CI grep guard

4. **'Ordered-policy converter chain'** is an HR4 licensing exposure stalled a full cycle:
   - The RETRY branch's bare `continue` advances idx instead of retrying, so the first transient primary-converter failure walks past BLOCK_AGPL into an AGPL converter
   - Isolated commit series, characterization tests over the full boolean matrix, dedicated AGPL sign-off
   - Do NOT batch with other zone fixes

5. **The two chronic Criticals** (ExtractionState multi-writer, 7+ cycles of verdict-gate churn; NFKC null-detector lattice, chronic since 2026-07-27) need their structural proposals (single finalize_gate_and_route writer; single pre-NFKC presentation_forms_signal on ExtractionState), not more incremental bug shaving. Both show bug_count falling while severity stays Critical—the signature of symptom removal without mechanism removal.

6. **Re-verify compliance surfaces** this cycle's HEAD~15..HEAD-scoped wiring audit did not cover:
   - The 11 _erase_* erasure-cascade functions (HR2/GDPR)
   - validate_hr3_compliance (HR3/PII egress)
   - These were unwired in prior cycles and are not confirmed wired here
   - Run a full-repo (not diff-scoped) wiring check before any cycle is declared complete

7. **Re-audit after proposals 2 and 3 land**. Open no new fix waves against zones 1, 2, 4, or 7 until their proposals land.
