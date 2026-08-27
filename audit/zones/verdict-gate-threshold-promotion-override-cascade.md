---
zone_name: Verdict Gate Threshold / Promotion Override Cascade
severity: critical
bug_count: 5
status: improved
audit_date: 2026-08-27
audit_run: POST-RUN20
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-27_POST-RUN20.md
key_files:
  - src/pageindex_mcp/helpers/verdict.py
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/helpers/tree_validation.py
  - src/pageindex_mcp/config.py
tags:
  - zone-spec
  - critical
  - verdict
  - gates
scorecard_verdict: needs_another_cycle
scorecard_date: 2026-08-27
scorecard_run: POST-RUN20
---
## Mechanism

The verdict-computation pipeline (compute_verdict → evaluate_gates → apply_promotions) contains layered threshold parameters and competing promotion paths whose interactions create a ratchet: every 'softening' change (widen PASS_MAX_LEAF_RATIO, add hysteresis) reveals masked defects, and every 'hardening' change (add floor checks, tighten gates) causes verdict-label regressions across the corpus. Five consecutive RFCs (024, 025, 026, 022, 033) each introduced or revealed bugs at this boundary. The image-enrichment promotion (priority=100) explicitly overrides the structural hard-fail check, creating a two-tier override where evaluate_gates can suppress all of apply_promotions, but within apply_promotions the image-enrichment candidate can suppress what would otherwise be a structural FAIL — order-of-evaluation matters and is guarded by _has_image_rescue rather than by re-running evaluate_gates.

Fixing any threshold at this boundary shifts the verdict distribution, revealing defects that the prior setting masked:
- a. Widening PASS_MAX_LEAF_RATIO from 0.17 to 0.30 allowed documents with 81 garbled nodes to PASS (chain 10).
- b. Adding hysteresis reclassified zero-content extraction failures from FAIL to MARGINAL, violating HR5 (chain 11).
- c. The image_enrichment_promoted path bypassed content-volume floors, allowing 38-char documents to PASS (chain 12).
- d. Hardening these same gates produced 12 corpus regressions as previously-masked defects became visible (chain 14).
- e. Each threshold change invalidated test fixtures written to the prior boundary, causing test failures that looked like code bugs but were measurement-calibration drift (chain 13).

## Code Evidence

`compute_verdict` at verdict.py:516-559 dispatches evaluate_gates then apply_promotions. `apply_promotions` at verdict.py:402-513 collects candidates from `_try_image_enrichment` (priority=100), `_try_structural_pass`, `_try_ocr_promotion`, `_try_flat_promotion`, `_try_content_class_promotion`, `_try_small_doc_promotion`; `max(candidates, key=priority)` wins. The image-enrichment override is at verdict.py:462-471: `_has_image_rescue = any(c.path_name == "image_enrichment_promoted" for c in candidates); if not _has_image_rescue and sig.max_leaf_ratio > th.hard_fail_max_leaf_ratio: return FAIL`. `_try_image_enrichment` at verdict.py:220-265 checks content_class, image_enrichment_ratio >= 0.8, total_chars >= min_image_promoted_chars, and detect_garble (pass-through). `evaluate_gates` at verdict.py:119-217 resolves hard-fail via HARD_FAIL_DEFECTS and _GATE_PRIORITY tiebreak.

## Key Files

| File | Role |
|---|---|
| src/pageindex_mcp/helpers/verdict.py | Verdict computation & promotion cascade |
| src/pageindex_mcp/helpers/gates.py | Gate evaluation & priority logic |
| src/pageindex_mcp/helpers/tree_validation.py | Tree quality validation |
| src/pageindex_mcp/config.py | Threshold configuration |
