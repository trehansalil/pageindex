---
zone_name: Verdict Computation & Promotion Cascade
severity: critical
bug_count: 6
status: improved
audit_date: 2026-09-01
audit_run: POST-RFC041
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-01_POST-RFC041.md
key_files:
  - src/pageindex_mcp/helpers/verdict.py
  - src/pageindex_mcp/helpers/types.py
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/config.py
  - src/pageindex_mcp/client/indexer.py
tags:
  - zone-spec
  - critical
  - verdict
  - promotion
scorecard_verdict: regressed
scorecard_date: 2026-09-01
scorecard_run: POST-RFC041
---
## Mechanism

The verdict subsystem is a tightly coupled three-phase pipeline (evaluate_gates → apply_promotions → finalize_gate_and_route) where threshold changes, gate reordering, and promotion eligibility interact non-linearly. Six promotion paths are evaluated first-match-wins, and changing any path eligibility or priority shifts documents across paths unpredictably.

1. **Threshold coupling:** Changing `PASS_MAX_LEAF_RATIO` (widened 3x from 0.17→0.30) shifts documents across verdict boundaries, interacting with hysteresis anchoring that widens acceptance from 0.30 to 0.40 for prior-PASS documents (Chain 19).

2. **Promotion pipeline ordering:** RFC-040 D2 reordered six `_try_*` guards from independent evaluation to precedence-locked cascade, flipping ~8 documents MARGINAL/PASS→FAIL from reorder alone; combined with three concurrent fixes, ~40 documents diverged (Chain 6).

3. **Config divergence:** PipelineConfig frozen snapshot and 24 modules with live `os.environ` reads use different truthiness parsing — `DEPTH_ADEQUACY_FLOOR` and `CHAR_FLOOR` drifted 1-2 units between call sites (Chain 7), so corpus audits misattribute config-change verdict shifts to extraction regression.

## History

- **Chain 6:** RFC-040 D2 reorder shifted ~8 docs; combined with 3 concurrent fixes, ~40 docs diverged.
- **Chain 7:** Zone-7 config consolidation revealed `DEPTH_ADEQUACY_FLOOR`/`CHAR_FLOOR` drifted 1-2 units between call sites, changing verdict outcomes for ~20 documents misattributed to extraction regression.
- **Chain 8:** RFC-036 D3 misattributed garble-gate blind spot — doc was terminal-rejected via `rtl_reversal` BEFORE reaching garble gate.
- **Chain 16:** image-enrichment bypass became entrenched leniency vector.
- **Chain 19:** RFC-025 D0 hysteresis allowed garbled doc FAIL→PASS despite 81/132 nodes garbled.
- **Chain 20:** RFC-026 `image_enrichment_promoted` bypass allowed PASS on 38-char documents.

## Code Evidence

1. **evaluate_gates** at verdict.py:126-224 resolves validate_result into GateOutcome with `hard_fail_verdict` short-circuiting Phase 2.

2. **apply_promotions** at verdict.py:405-580 has six `_try_*` promotion paths evaluated unconditionally for VG-6 telemetry, first match wins; content-volume floor (`th.min_marginal_chars`) gates all paths.

3. **finalize_gate_and_route** at types.py:399-462 is single writer barrier for state.gate_result/ok/reason/first_defect/route with 5 documented force_route/force_ok override sites.

4. **_try_image_enrichment** at verdict.py:227-269 now calls `_infer_presentation_forms` and has D1 guards.

## Key Files

| File | Role |
|------|------|
| verdict.py:126-224 | evaluate_gates Phase 1 with short-circuit logic |
| verdict.py:227-269 | _try_image_enrichment with PF recovery and D1 guards |
| verdict.py:405-580 | apply_promotions Phase 2 with six _try_* paths |
| types.py:399-462 | finalize_gate_and_route single-writer barrier |
| gates.py | Gate definitions and thresholds |
| config.py | Configuration of threshold values |
