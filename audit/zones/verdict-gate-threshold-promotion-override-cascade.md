---
zone_name: Verdict-Gate Threshold / Promotion / Override Cascade
severity: critical
bug_count: 8
status: regressed
wave: 1
audit_date: 2026-08-28
audit_run: POST-FIX-WAVE3
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md
key_files:
  - src/pageindex_mcp/helpers/verdict.py
  - src/pageindex_mcp/helpers/types.py
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/config.py
tags:
  - zone-spec
  - critical
  - verdict
  - threshold
  - promotion
scorecard_verdict: regressed
scorecard_date: 2026-08-28
scorecard_run: POST-FIX-WAVE3
---
## Mechanism

The verdict computation pipeline (evaluate_gates → apply_promotions → compute_verdict) is an order-dependent, first-match-wins cascade with threshold boundaries, promotion overrides, and bypass flags (source_selection, image_enrichment_promoted) that systematically generate regressions:

- **Threshold widening** masks defects at the new edge; **tightening** regresses previously-passing docs
- Each **promotion path** can bypass content-volume floors that other paths enforce
- Five consecutive RFCs (022, 024, 025, 026, 033) each fixed and re-broke this same boundary
- The **hysteresis band** added to stabilize borderline documents reclassified zero-content failures from FAIL to MARGINAL, **violating CLAUDE.md Hard Rule #5** (never silently persist low-quality trees)

## Code Evidence

**verdict.py:379–466** — `apply_promotions`:
- D1 structural hard-fail gate runs BEFORE promotions
- _try_image_enrichment returns `_apply_clamp(_ie)` which, when `source_selection=True`, **bypasses the inner clamp entirely**
- D2 ordered chain is **pure source-order specification** (_try_image_enrichment → _try_structural_pass → ... → MARGINAL fallback)

**evaluate_gates:124–222**:
- Uses `_GATE_PRIORITY` tiebreak to suppress co-firing defects
- Determines whether promotions run at all — adding/removing a defect from HARD_FAIL_DEFECTS changes which documents reach apply_promotions

**config.py**:
- Threshold values (PASS_MAX_LEAF_RATIO, MARGINAL boundaries) are module-level constants with no versioning or audit trail

## Key Files

| File | Role |
|------|------|
| src/pageindex_mcp/helpers/verdict.py | Core promotion pipeline, threshold application |
| src/pageindex_mcp/helpers/types.py | Verdict/defect type definitions |
| src/pageindex_mcp/helpers/gates.py | Gate priority, defect evaluation |
| src/pageindex_mcp/config.py | Threshold constants (no versioning) |

## Related Issues

- Chain 12: PASS_MAX_LEAF_RATIO widened 0.17→0.30 masked 81-garbled-node docs
- Chain 13: Hysteresis violated HR#5 by reclassifying zero-content as MARGINAL
- Chain 15: Hardening produced 12 corpus regressions
- Chains 26–27: Five RFCs repeatedly hit this same boundary

