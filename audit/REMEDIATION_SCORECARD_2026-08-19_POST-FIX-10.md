# Remediation Scorecard — POST-FIX-10 (2026-08-19)

**Pre-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-18_POST-FIX-7.md
**Post-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-19_POST-FIX-10.md
**Delta report:** audit/ZONE_DELTA_2026-08-12_POST-FIX-10.md

## Verdict: REGRESSED

POST-FIX-10 closed one zone outright — Dual-Store Verdict Consistency and Persistence Timing (high, 9 bugs eliminated via CAS consolidation and write-visibility coordination) — and genuinely improved one zone — Mutable ExtractionState Recovery Path Ordering dropped from critical to high (−3 bugs, OCR flag-conflation findings carved out into new zone). However, three carried-forward zones regressed (+2, +1, +3 respectively) and two new critical zones surfaced (Picture/OCR Enrichment Conflation at 12 bugs, Cross-Process Error Classification Boundary at 5 bugs), for a net swing of +13 bugs (53 → 66). This is the sixth consecutive cycle to fail `cycle_complete`. The structural pattern persists: each fix wave's wiring is confirmed (all carried zones reached `implemented_and_wired` or `partially_implemented` proposal status), but deeper audit passes surface pre-existing latent defects and cross-subsystem interactions that earlier audits missed, more than offsetting the bugs resolved.

## Zones Closed (1)

| Zone | Was Severity | Bugs Eliminated |
|---|---|---|
| Dual-Store Verdict Consistency and Persistence Timing | High | 9 |

Persistence-timing race, CAS divergence, and write-barrier oscillation no longer appear in the post-fix audit. Some persistence-timing aspects may have migrated into the new Cross-Process Error Classification Boundary zone.

## Zones Remaining (5 carried + 2 new = 7)

| Zone | Severity | Bug Count | Status |
|---|---|---|---|
| GATE_TABLE to Recovery Dispatch Reason-Code Coupling | Critical | 11 | Regressed (+2) |
| Picture/OCR Enrichment and Page-Level Escalation Conflation | Critical | 12 | **NEW** |
| Garble Detection Heuristic Patchwork | Critical | 12 | Stalled (0) |
| Verdict Threshold Oscillation and Hysteresis Failure | High | 9 | Regressed (+3) |
| Config Snapshot Freeze Drift and Incomplete Wiring Enforcement | High | 7 | Regressed (+1) |
| Cross-Process Error Classification Boundary | Critical | 5 | **NEW** |
| Mutable ExtractionState Recovery Path Ordering | High | 7 | Improved (−3) |

## New Zones (2)

| Zone | Severity | Bug Count | Introduced By |
|---|---|---|---|
| Picture/OCR Enrichment and Page-Level Escalation Conflation | Critical | 12 | Cross-subsystem filter interaction: page-coverage skip + text-layer probe + forced-OCR PictureItem reclassification combine to produce emergent zero-output states |
| Cross-Process Error Classification Boundary | Critical | 5 | Child-process exception class reporting via stdout JSON falls through on hard crash; `_TERMINAL_CHILD_REASONS` has 2 entries vs 10+ gate defects; PDF_INSPECTOR_PRECLASSIFY timeout vs reap_stale_jobs race |

**Red flag:** Two new critical zones emerged this cycle. Zone 2 (Picture/OCR) carries 12 bugs — the highest single-zone count in this audit. Zone 6 (Cross-Process) exposes a process-boundary classification gap that can cause terminal errors to be retried indefinitely.

## Metrics

- Net bug delta: **+13** (53 → 66)
- Zones improved: 1
- Zones regressed: 3
- Zones stalled: 1
- Zones closed: 1
- Zones new: 2
- Total zones: 7 (was 6)
- Wiring status: **mostly_wired** (5/7 zones `implemented_and_wired`, 1 `partially_implemented`, 1 `no_proposal`)

## Recommended Next Steps

POST-FIX-10 regressed: +13 net bugs (53→66), 2 new critical zones surfaced despite 1 zone closed (9 bugs eliminated) and 1 improved (−3 bugs). This is the 6th consecutive cycle failing `cycle_complete`. The pattern from POST-FIX-7 persists — each fix wave's wiring lands correctly but deeper auditing reveals more findings than were resolved.

CHRONIC STALLING (6+ cycles): Zone 3 (Garble Detection, 12 bugs critical) has never improved across any cycle. NFKC-before-garble-check ordering remains the single root cause amplifying 4+ downstream detection gaps.

KEY STRUCTURAL GAPS:
- **Zone 1 (GATE_TABLE→Recovery Dispatch):** REASON_POLICY/GATE_TABLE auto-derivation landed cleanly, but `client.py`'s `_recover_ocr_retry` and `_recover_vlm_fallback` hardcode per-reason eligibility sets — new GateSpecs with `recovery_tag` are dispatched but silently rejected. Next fix: make recovery dispatch consume `GateSpec.recovery_tag` directly rather than maintaining parallel hardcoded sets.
- **Zone 2 (Picture/OCR Conflation, NEW):** Cross-subsystem filter interaction where page-coverage skip, text-layer probe, and forced-OCR PictureItem reclassification combine to zero-output. This is the highest-count zone (12 bugs). Root cause: per-picture vs page-level OCR share a single `_OCR_ESCALATION` flag.
- **Zone 3 (Garble Detection):** Move NFKC normalization AFTER garble detection — single change addresses RFC-033 D2 (0% TPR for `_reversed_morphology`) and `_check_bidi_coherence` signal loss simultaneously.
- **Zone 4 (Verdict Threshold):** Hysteresis mechanism wired but structurally inert — MinIO wipe on reingestion defeats `find_prior_verdict`. Fix: persist prior verdict in Postgres (not MinIO) so it survives reingestion.
- **Zone 5 (Config Drift/Wiring):** `validate_feature_wirings` only fires at `atexit`, not startup. HR2 `storage.delete_doc` has zero production entrypoints (Hard Rule 2 violation).
- **Zone 6 (Cross-Process Error, NEW):** `_TERMINAL_CHILD_REASONS` coverage gap + reap_stale_jobs timeout race. Fix: assert `_TERMINAL_CHILD_REASONS` covers all gate defect enum values at import time.
- **Zone 7 (ExtractionState):** Only improved zone (−3 bugs, critical→high). OCR flag-conflation carved out. Remaining: keep-best revert state mismatch, bidi double-application, heading injection.

STRATEGY: The incremental patching pattern has now failed 6 consecutive cycles. Each fix resolves its target but the deeper audit pass reveals more latent defects than were fixed. Consider freezing all new feature work and executing a focused structural remediation phase targeting the 3 root causes (NFKC ordering, per-picture/page-level OCR split, recovery dispatch hardcoding) rather than continuing zone-by-zone threshold adjustments.
