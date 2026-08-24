# Remediation Scorecard — POST-FIX-5 (2026-08-17)

**Pre-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-17_POST-FIX-4.md
**Post-fix delta:** audit/ZONE_DELTA_2026-08-17_POST-FIX-5.md
**Remediation plan:** audit/REMEDIATION_PLAN_2026-08-17.md

## Verdict: SUBSTANTIAL PROGRESS — 2 ZONES CLOSED

Fix-5 nets -22 bugs (66 → 44) and closes 2 of 8 zones. All 3 planned waves landed:
- Wave 1 (Zone 1): `check_garble()` consolidation with `GarbleContext` enum (commit `7b345c4`)
- Wave 2 (Zone 5): OCR flag split, `primary_text`, enrichment unification (commit `f37584e`)
- Wave 3 (Zone 2): `index()` decomposition into recovery pipeline + orchestrator (commit `646cdc0`)

Zone 2 (God Function) drops from critical/11 to low/2 — the 1365-line `index()` is now a 153-line orchestrator. Zone 5 (OCR/Enrichment) drops from high/9 to low/3 — the single-boolean conflation that drove repeated regressions is eliminated. Zone 1 (Garble Hydra) drops from critical/12 to high/6 — sole entry point eliminates fix-in-one-regress-in-another, but cleanup items remain. 3 of 12 unwired symbols resolved, 9 remain across zones 1-4.

## Zones Closed (2)

| Zone | Was Severity | Bugs Eliminated |
|---|---|---|
| God Function Routing Cascade (Zone 2) | critical | 9 of 11 (11→2) |
| OCR/Enrichment Signal Conflation (Zone 5) | high | 6 of 9 (9→3) |

## Zones Remaining (6)

| Zone | Severity | Bug Count | Status |
|---|---|---|---|
| Garble Detection Hydra (Zone 1) | high | 6 | improved (was critical/12) |
| Verdict Persistence Split-Brain (Zone 3) | high | 7 | stalled |
| Threshold Calibration Feedback Loops (Zone 4) | high | 8 | stalled |
| Conversion Pipeline Stage Coupling (Zone 6) | high | 7 | stalled |
| Registry/Persistence Consistency Gaps (Zone 7) | medium | 6 | stalled |
| Dead/Uncommitted/Stale Code Divergence (Zone 8) | medium | 5 | improved (was 6) |

## Metrics

- **Net bug delta:** -22 (66 → 44)
- **Zones closed:** 2
- **Zones improved:** 2 (Zone 1, Zone 8)
- **Zones stalled:** 4 (Zones 3, 4, 6, 7)
- **Regressed:** 0
- **Unwired symbols:** 9 of 12 remaining

### Unwired Symbols (9)

| Symbol | Location | Zone | Gap |
|---|---|---|---|
| `presentation_forms` prong | helpers.py:1289-1296 | Zone 1 | Proposal says delete; still active |
| `_tree_is_garbled` | helpers.py:1577 | Zone 1 | Should fold into `TreeSignals.from_tree`; still standalone |
| `_gate_bidi_degraded` | helpers.py:1793 | Zone 2 | Dead gate still in GATE_TABLE |
| `low_content_ocr_eligible` | client.py:1266-1304 | Zone 2 | Workaround still active |
| `_hard_gate()` | — | Zone 4 | Never implemented |
| `hysteresis_band` | helpers.py:303/2220 | Zone 4 | Proposal says delete; still active |
| `write_verdict` | storage.py:653 | Zone 3 | Kept (opposite of proposal) |
| `_confirm_write_visible` | storage.py:44 | Zone 3 | Barrier causing timing regressions |
| `recompute_verdicts` | preprocess_client.py:221 | Zone 3 | Second offline recomputer alongside `promotion_sweep` |

## Next Cycle Priorities

**Priority 1 — Zone 1 (Garble Detection Hydra):** improved but not closed. Remaining work is cleanup: delete `presentation_forms` prong, fold `_tree_is_garbled` into `TreeSignals.from_tree`, thread `expected_script` on flat path, delete `_flat_text_is_garbled`. Low risk, mostly deletions.

**Priority 2 — Zone 4 (Threshold Calibration Feedback Loops):** stalled at high/8. Needs `_hard_gate()` implementation, hysteresis deletion, and `classify_verdict` two-phase pipeline. High impact on verdict stability.

**Priority 3 — Zone 3 (Verdict Persistence Split-Brain):** stalled at high/7. Architectural direction decision needed: finish `write_verdict` consolidation or eliminate-and-merge-into-`save_doc_meta`. Unify `promotion_sweep` vs `recompute_verdicts`.

**Priority 4 — Zone 6 (Conversion Pipeline Stage Coupling):** stalled at high/7. Needs simplification proposal. Fence-marker stripping and heading injection issues.

**Priority 5 — Wire remaining unwired symbols** (9 items across 4 zones) before adding new features.
