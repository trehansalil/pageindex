---
title: Zone Spec Index
tags:
  - zone-index
  - audit
audit_date: 2026-08-28
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md
---
# Zone Spec Index

This index lists all architecture defect zones triaged in the 2026-08-28 audit, ordered by priority and severity.

## Overview

- **Total zones**: 6
- **Critical severity**: 2
- **High severity**: 4
- **Waves**: 3 (parallel execution tracking)
- **Audit source**: `audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md`

## Zones by Priority

| Priority | Zone | Severity | Wave | Status | Key Files |
|---|---|---|---|---|---|
| 1 | [[verdict-gate-threshold-promotion-override-cascade]] | critical | 1 | triaged | helpers/types.py, helpers/verdict.py, config.py |
| 2 | [[garble-detection-cross-cutting-kernel]] | critical | 2 | triaged | helpers/garble.py, helpers/verdict.py, client/images.py, client/indexer.py |
| 3 | [[ocr-recovery-cascade-kill-switch-conflation]] | high | 3 | triaged | picture_plane.py, converters/pictures.py, client/recovery.py, client/indexer.py |
| 4 | [[converter-chain-fallback-agpl-gating]] | high | 1 | triaged | converters/pipeline.py, client/indexer.py, client/remote.py, config.py |
| 5 | [[dual-writer-verdict-persistence-consistency-model-split]] | high | 3 | triaged | worker/registry_mirror.py, registry/queries.py, storage/verdict.py, metrics/definitions.py |

## Zones by Wave (Execution Order)

### Wave 1 (Parallel)
- [[verdict-gate-threshold-promotion-override-cascade]] (Priority 1)
- [[converter-chain-fallback-agpl-gating]] (Priority 4)
- **Rationale**: Verdict-Gate defines foundational contracts consumed by Wave-2 Garble Detection. Converter Chain shares no files, runs in parallel; pre-empts file-overlap conflicts with OCR Recovery (Wave 3).

### Wave 2 (Dependent on Wave 1)
- [[garble-detection-cross-cutting-kernel]] (Priority 2)
- **Rationale**: Consumes verdict-gate contract (validate_tree → TreeGateResult). Isolated alone: key files (converters/pictures.py, client/recovery.py) are PRIMARY for OCR Recovery (Wave 3); prevents same-file merge collisions.

### Wave 3 (Dependent on Wave 1 & 2)
- [[ocr-recovery-cascade-kill-switch-conflation]] (Priority 3)
- [[dual-writer-verdict-persistence-consistency-model-split]] (Priority 5)
- **Rationale**: OCR Recovery shares files with both Wave-1 and Wave-2, forced to Wave 3. Dual-Writer Verdict has zero overlap (disjoint call chain via preprocess_client.py/promotion_sweep.py) but structurally consumes both earlier waves' outputs. Co-runs with OCR Recovery; no file conflicts.

## Zones by Severity

### Critical (2)
1. **Verdict-Gate Threshold / Promotion / Override Cascade** (Wave 1, Priority 1)
   - Order-dependent promotion cascade with ambient thresholds, implicit bypass flags
   - Affects verdict classification across all documents
   - Requires type-safe PromotionSpec contract

2. **Garble Detection Cross-Cutting Kernel** (Wave 2, Priority 2)
   - 13 callers across 9+ subsystems with blind spots
   - Presentation-forms, table-block, numeric-junk, script-mismatch gaps
   - Blocks accurate garble detection for non-Latin documents

### High (4)
3. **OCR Recovery Cascade and Kill-Switch Conflation** (Wave 3, Priority 3)
   - Three OCR concerns share conflated kill-switches
   - Marker orphaning, hidden re-entry guard, narrow eligibility
   - Silent disabling of recovery paths

4. **Converter Chain Fallback and AGPL Gating** (Wave 1, Priority 4)
   - Structural failures silently walk to AGPL converters
   - Violates HR4 conscious operator decision requirement
   - Remote version checks warn-only, script forwarding missing

5. **Dual-Writer Verdict Persistence and Consistency Model Split** (Wave 3, Priority 5)
   - Three independent verdict writers share no consistency contract
   - Silent verdict loss, orphan server queries, consistency degradation
   - No observable metric when registry disabled

## Key Thresholds & Defaults

Extracted from zone specs for quick reference:

| Parameter | Current | Zone | Notes |
|---|---|---|---|
| hard_fail_max_leaf_ratio | 0.75 (hardcoded) | Wave 1: Verdict-Gate | Must become PipelineConfig field with env var HARD_FAIL_MAX_LEAF_RATIO |
| min_marginal_chars | th.min_marginal_chars | Wave 1: Verdict-Gate | All PromotionSpec.min_chars must be >= this value |
| cat_a_max_leaf_ratio | 0.15 (hardcoded) | Wave 1: Verdict-Gate | Must become VerdictThresholds field |
| cat_a_max_noise_ratio | 0.005 (hardcoded) | Wave 1: Verdict-Gate | Must become VerdictThresholds field |
| garble_digit_floor | 500 | Wave 2: Garble Detection | Secondary numeric-junk check fires at >=50 chars, >90% digits |
| presentation_forms inference | had_presentation_forms=False | Wave 2: Garble Detection | Replace with _infer_presentation_forms() in 3 call sites |
| ocr_escalation_garble | True (config) | Wave 3: OCR Recovery | Must be decoupled from ocr_escalation_low_content |
| agpl_structural_fallback_enabled | True (new) | Wave 1: Converter Chain | Default backward-compat; env var AGPL_STRUCTURAL_FALLBACK_ENABLED |
| remote_version_enforce | False (new) | Wave 1: Converter Chain | Opt-in hard-blocking of stale remote Docling versions |
| registry_enabled | True (config) | Wave 3: Dual-Writer | New REGISTRY_CONSISTENCY_DEGRADED gauge fires when False |

## Dependency Graph

```
Wave 1: Verdict-Gate → Wave 2: Garble Detection → Wave 3: OCR Recovery
Wave 1: Converter Chain → (parallel) → Wave 3: Dual-Writer Verdict
```

No horizontal dependencies within waves; all inter-wave dependencies flow forward.

## Corpus Validation Strategy

Each zone includes a corpus validation section specifying:
- **Affected documents**: German insurance T&Cs, Arabic PDFs, table-heavy contracts, scanned OCR noise
- **Expected direction**: "stable" (no verdict shifts), "improve" (better detection), or "mixed"
- **Spot check count**: 5–10 documents per zone post-fix

Run validation sequentially after each wave completes.

## Test Plan Summary

### Coverage by Category

| Category | Count | Key Files |
|---|---|---|
| Contract assertions | 22 | tests/test_verdict.py, tests/test_garble.py, tests/test_recovery.py, tests/test_config.py |
| Regression guards | 15 | tests/test_converters.py, tests/test_storage.py, tests/test_hr3_zdr_egress.py |
| Exhaustiveness checks | 8 | tests/test_architecture_guards.py, tests/test_gates.py |
| Wiring verification | 12 | tests/test_registry.py, tests/test_gates.py |
| Integration tests | 1 | tests/test_recovery.py (full loop with defect sequences) |

### Import-Time Assertions

Verify at module-load time (add to conftest.py or zone-load tests):
- PromotionSpec.min_chars >= VerdictThresholds.min_marginal_chars (Zone 1)
- PASS_MAX_LEAF_RATIO < HARD_FAIL_MAX_LEAF_RATIO (Zone 1 config)
- PROMOTION_REGISTRY priorities unique and sorted (Zone 1)
- ConverterFailurePolicy has GATE_AGPL_STRUCTURAL (Zone 4)
- REGISTRY_CONSISTENCY_DEGRADED exported from metrics (Zone 5)

## Next Steps

1. **Pre-Wave 1**: Review and approve verdict-gate and converter-chain specs
2. **Execute Wave 1**: Implement both zones in parallel; run contract tests
3. **Pre-Wave 2**: Verify Wave-1 verdicts stable; approve garble-detection spec
4. **Execute Wave 2**: Implement garble kernel; run regression suite on Arabic/table PDFs
5. **Pre-Wave 3**: Corpus spot-checks for Waves 1–2; approve OCR-recovery and dual-writer specs
6. **Execute Wave 3**: Implement both zones in parallel; full integration test suite
7. **Post-Wave 3**: Corpus validation across all 6 zones; update ARCHITECTURE.md with new contracts

## Audit Metadata

- **Audit date**: 2026-08-28
- **Audit source**: `audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md`
- **Generated for**: PageIndex MCP Server
- **Coordinator model**: Haiku 4.5
- **Zone notes written**: 6 / 6 ✓
- **Index note written**: 1 / 1 ✓
