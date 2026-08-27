---
zone_name: Verdict Gate Promotion Bypass Cascade
severity: critical
bug_count: 11
status: audited
audit_date: 2026-08-26
audit_run: POST-FIX-13
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-13.md
key_files:
  - src/pageindex_mcp/helpers/verdict.py
  - src/pageindex_mcp/helpers/tree_validation.py
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/config.py
tags:
  - zone-spec
  - critical
  - verdict
  - gate
  - promotion
---
## Mechanism

The generative mechanism is **MULTIPLE INDEPENDENT BYPASS PATHS WITH CIRCULAR THRESHOLD COUPLING**. `apply_promotions` (verdict.py:407-518) runs only when no HARD_FAIL fired, but the hard-fail check itself is conditionally gated: the max_leaf_ratio structural hard-fail at line 476 is evaluated 'if not _has_image_rescue' — so a fired image_enrichment_promoted candidate (priority=100, _try_image_enrichment at verdict.py:220-270) bypasses what would otherwise be an unconditional FAIL.

`_try_image_enrichment` checks image_enrichment_ratio >= 0.8 but has no minimum char floor for the PASS verdict path when total_chars >= th.min_image_promoted_chars — and below that floor, it returns a MARGINAL verdict still at priority=100, still outranking structural passes.

The threshold coupling is circular: widening PASS_MAX_LEAF_RATIO to reduce false FAILs (RFC-023 D10: 0.17→0.20, RFC-024 D0: 0.20→0.30) let garbled documents through; adding hysteresis (RFC-025 D0) to stabilize verdicts was defeated by reingestion wiping processed/*.meta.json (RFC-026 D3); and the hysteresis itself interacts badly with garble detection because it relaxes max_leaf_ratio when prior_verdict=='PASS', letting identical garbling metrics pass on re-score.

## Code Evidence

- `apply_promotions` (verdict.py:407-518): hard-fail check at line 476 'if not _has_image_rescue and sig.max_leaf_ratio > th.hard_fail_max_leaf_ratio' — the _has_image_rescue guard is computed at line 473 'any(c.path_name == "image_enrichment_promoted" for c in candidates)'.

- `_try_image_enrichment` (verdict.py:220-270): returns `PromotionCandidate(priority=100, path_name='image_enrichment_promoted', verdict='PASS')` at line 267 when ratio>=0.8 and total_chars >= min_image_promoted_chars and not garbled. Returns MARGINAL at priority=100 when below char floor (line 244).

- `GATE_TABLE` (gates.py:321-408): 10 active gates evaluated exhaustively, severity-ordered (GARBLING=0 highest, SUSPECT_DENSITY=9 lowest).

- `validate_tree` (tree_validation.py:262-354): returns only the first firing gate as primary defect even though all_defects carries every co-firing gate — callers reading the 2-tuple form lose co-firing information.

## Related RFCs

PASS_MAX_LEAF_RATIO widened three times (0.17→0.20→0.30), hysteresis added then defeated by ledger wipe (RFC-023→024→025→026).

image_enrichment_promoted evolved from implicit drift to explicitly hard-coded priority=100 escape hatch.

RFC-024 threshold widening caused 'Haftpflicht' with 81/132 garbled nodes to flip FAIL→PASS.

Near-zero-content documents earn PASS via promotion flag with no content-validity check.

Hysteresis relaxes max_leaf_ratio for garbled trees when prior PASS exists (Chain 27).
