# Remediation Scorecard — POST-FIX-7 (2026-08-18)

**Pre-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-18_POST-FIX-6.md
**Post-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-18_POST-FIX-7.md
**Delta report:** audit/ZONE_DELTA_2026-08-12_POST-FIX-7.md

## Verdict: REGRESSED

POST-FIX-7 closed two zones outright — Recovery Pipeline Implicit Ordering and State Mutation (critical, 9 bugs eliminated) and Duplicated Threshold/Logic Definitions Across Files (medium, 4 bugs eliminated) — but every one of the six remaining zones got worse in the same cycle, for a net swing of +9 bugs (46 → 55). This is the fifth consecutive cycle to fail `cycle_complete`. The fix commits wired their target symbols correctly (`RecoveryOutcome`, `GarbleProfile`, the `compute_verdict` consolidation, the RTL decision path — all confirmed wired, no dangling symbols), but the deeper post-fix audit pass surfaced pre-existing latent defects in the adjacent zones that earlier, shallower audits had not caught, more than offsetting the bugs actually fixed.

## Zones Closed (2)

| Zone | Was Severity | Bugs Eliminated |
|---|---|---|
| Recovery Pipeline Implicit Ordering and State Mutation | Critical | 9 |
| Duplicated Threshold/Logic Definitions Across Files | Medium | 4 |

## Zones Remaining (6)

| Zone | Severity | Bug Count | Status |
|---|---|---|---|
| Garble Detection Surface Fragmentation | Critical | 12 | Regressed |
| OCR Recovery Pipeline Flag Conflation and Mutable State Ordering | Critical | 10 | Regressed |
| Three-Layer Verdict Pipeline Implicit GATE_TABLE Coupling | Critical | 9 | Regressed |
| Dual-Store Verdict Consistency and Persistence Timing | High | 11 | Regressed |
| Dead Code and Incomplete Wiring Enforcement Gap | High | 7 | Regressed |
| Content-Destructive Heuristics Without Safety Bounds | Critical | 6 | Regressed |

## New Zones (0)

| Zone | Severity | Introduced By |
|---|---|---|
| — none — | — | — |

No new zones surfaced this cycle. All regression is concentrated in pre-existing zones — not a red flag for scope creep, but confirmation that the remaining defect surface is deeper than prior audits estimated.

## Metrics

- Net bug delta: **+9** (46 → 55)
- Wiring status: **all_wired** (no unwired symbols)
- Zones improved: 0
- Zones regressed: 6
- Zones stalled: 0
- Zones closed: 2
- Zones new: 0

## Recommended Next Steps

POST-FIX-7 regressed: +9 net bugs (46->55), all 6 remaining zones worsened despite 2 zones closed (13 bugs eliminated). This is the 5th consecutive cycle failing cycle_complete. The fix commits successfully wired their symbols (RecoveryOutcome, GarbleProfile, compute_verdict consolidation, RTL decision) but the post-fix audit surfaced more findings than were resolved -- the deeper audit exposed pre-existing latent defects previously invisible.

CHRONIC STALLING (all 5 cycles): Zone 1 (Garble, 12 bugs critical), Zone 2 (OCR Recovery, 10 bugs critical), Zone 3 (Verdict Pipeline, 9 bugs critical). These three zones have never improved across any cycle. Zone 4 (Dual-Store, 11 bugs high) has worsened steadily from 4 bugs in POST-FIX-4.

RECOMMENDED STRATEGY PIVOT -- Stop incremental patching. Five cycles of additive fixes have produced net regression (+9 bugs) because: (1) calibration-by-incident (Zone 6) guarantees each fix creates a new failure on a structurally different document, (2) NFKC-before-garble-check ordering (Zone 1) is a single root cause amplifying 4+ downstream detection gaps that no individual prong fix can address, (3) the OCR retry arithmetic impossibility (Zone 2) and write-barrier oscillation (Zone 4) are structural design flaws not patchable by threshold tuning.

NEXT CYCLE SHOULD: (A) Zone 1: move NFKC normalization AFTER garble detection -- single change addresses RFC-033 D1/D2 and _check_bidi_coherence signal loss simultaneously. (B) Zone 2: replace _repeating_token_density None-return with explicit 0.0 for sub-threshold inputs, making OCR retry winnable. (C) Zone 4: implement write-barrier as an actual coordination primitive (event/semaphore) between MinIO write and Postgres upsert rather than oscillating sleep delays. (D) Zone 6: FREEZE all content-destructive heuristics behind per-document-class guards (Arabic legal vs German insurance vs statistical) rather than universal thresholds. (E) Zone 3: extract GATE_TABLE position-encoding into an explicit GateSpec.priority field eliminating the implicit coupling. Each of these is a single structural change, not a threshold adjustment. If the next cycle still regresses, escalate to a deletion-first rewrite of the garble and recovery subsystems -- the deletion pattern (which closed Zones 3-prior and 8-prior with zero regressions) remains the only proven strategy in this project.
