---
zone_name: Verdict-Gate Threshold / Promotion / Override Cascade
severity: critical
wave: 1
priority: 1
status: triaged
audit_date: 2026-08-28
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md
tags:
  - zone-spec
  - critical
  - wave-1
---
## Mechanism to Eliminate

Order-dependent first-match-wins promotion cascade with ambient threshold constants, implicit bypass flags (source_selection skips _clamp_pass for image enrichment only by closure capture, not by typed contract), and promotion helpers that each enforce different content-volume floors (or none at all). 

- Six _try_* promotion paths lack a shared eligibility contract
- _try_image_enrichment enforces min_image_promoted_chars + garble re-check
- _try_cat_b enforces min_flat_promotion_chars + placeholder ratio
- _try_cat_a has only hardcoded 0.15/0.005 pair
- _try_cat_c has no char floor at all
- _try_small_doc has hardcoded 100-char floor disconnected from th.min_marginal_chars
- hard_fail_max_leaf_ratio hardcoded to 0.75 inside VerdictThresholds.from_config rather than sourced from PipelineConfig/env (invisible to config snapshot, unauditable)

## Strategy

Type-safe contract:
1. Extract PromotionSpec dataclass that each promotion path must declare, carrying typed eligibility predicates (min_chars, max_leaf_ratio_bound, garble_check_required, content_class_filter)
2. Move hard_fail_max_leaf_ratio from hardcoded literal into PipelineConfig with env var HARD_FAIL_MAX_LEAF_RATIO (appears in config snapshot)
3. Replace _apply_clamp closure (captures source_selection) with explicit ClampPolicy enum parameter
4. Add compile-time assertion: every PromotionSpec.min_chars >= th.min_marginal_chars
5. Add PROMOTION_REGISTRY list with import-time completeness assertion

## Code Targets

| File | What | How | Constraint |
|---|---|---|---|
| `src/pageindex_mcp/helpers/types.py` | Add PromotionSpec dataclass and ClampPolicy enum | Define ClampPolicy = Enum('ClampPolicy', ['CLAMP', 'BYPASS_IMAGE_ENRICHMENT']) and PromotionSpec dataclass with name, priority, min_chars, content_class_filter, garble_recheck, clamp_policy. Add PROMOTION_REGISTRY after VerdictThresholds. | PromotionSpec.min_chars must be >= VerdictThresholds.min_marginal_chars at registration time; ClampPolicy.BYPASS_IMAGE_ENRICHMENT only skips _clamp_pass |
| `src/pageindex_mcp/config.py` | Add HARD_FAIL_MAX_LEAF_RATIO to PipelineConfig | Add hard_fail_max_leaf_ratio: float field. Wire to env var with default '0.75'. Add assertion: pass_max_leaf_ratio < hard_fail_max_leaf_ratio | PASS_MAX_LEAF_RATIO < HARD_FAIL_MAX_LEAF_RATIO must hold |
| `src/pageindex_mcp/helpers/types.py` line 428 | Source hard_fail_max_leaf_ratio from PipelineConfig | Change line 428 from hardcoded 0.75 to cfg.hard_fail_max_leaf_ratio | Must come from PipelineConfig, appears in dataclasses.asdict() |
| `src/pageindex_mcp/helpers/verdict.py` lines 464–489 | Refactor apply_promotions to use PROMOTION_REGISTRY | Replace six _try_* calls with loop over PROMOTION_REGISTRY sorted by priority. Apply shared pre-check (content_class_filter, min_chars, garble_recheck). Replace _apply_clamp closure with standalone function taking ClampPolicy. | Preserve exact evaluation order via priority: image_enrichment > structural_pass > cat_a > cat_b > cat_c > small_doc |
| `src/pageindex_mcp/helpers/verdict.py` line 328 | Add content-volume floor to _try_cat_c | Add guard: if len(sig.flat_text.strip()) < th.min_flat_promotion_chars: return None | Use th.min_flat_promotion_chars for consistency |
| `src/pageindex_mcp/helpers/verdict.py` lines 283–292 | Align _try_cat_a hardcoded thresholds to VerdictThresholds fields | Add cat_a_max_leaf_ratio: float = 0.15 and cat_a_max_noise_ratio: float = 0.005 to VerdictThresholds. Replace hardcoded values. | Defaults must exactly match current values (0.15, 0.005) for unchanged behavior |

## Wiring Checks

| Symbol | Must Be Imported By | Check Type |
|---|---|---|
| PromotionSpec | `src/pageindex_mcp/helpers/verdict.py`, `src/pageindex_mcp/helpers/__init__.py` | import |
| ClampPolicy | `src/pageindex_mcp/helpers/verdict.py`, `src/pageindex_mcp/helpers/__init__.py` | import |
| PROMOTION_REGISTRY | `src/pageindex_mcp/helpers/verdict.py` | import |
| hard_fail_max_leaf_ratio | `src/pageindex_mcp/helpers/types.py` | call |
| VerdictThresholds.from_config | `src/pageindex_mcp/helpers/verdict.py` | call |

## Test Requirements

| Test File | What to Test | Assertion Type |
|---|---|---|
| `tests/test_verdict.py` | PromotionSpec registry completeness: unique priority, min_chars >= min_marginal_chars, valid content_class_filter | exhaustiveness |
| `tests/test_verdict.py` | Content-volume floor uniformity: document with stripped-text < min_marginal_chars must FAIL across all 6 paths | contract |
| `tests/test_verdict.py` | ClampPolicy contract: source_selection=True only bypasses _clamp_pass for image-enrichment, not others | contract |
| `tests/test_verdict.py` | _try_cat_c now enforces min_flat_promotion_chars: 10-char hierarchical doc must NOT promote to PASS via cat_c | regression |
| `tests/test_verdict.py` | hard_fail_max_leaf_ratio from PipelineConfig: HARD_FAIL_MAX_LEAF_RATIO=0.60 with max_leaf_ratio=0.65 must FAIL | contract |
| `tests/test_architecture_guards.py` | Import-time assertion: PASS_MAX_LEAF_RATIO < HARD_FAIL_MAX_LEAF_RATIO in live pipeline_config | wiring |
| `tests/test_verdict.py` | Promotion order stability: PROMOTION_REGISTRY priorities match documented order | exhaustiveness |
| `tests/test_gates.py` | HARD_FAIL_MAX_LEAF_RATIO appears in pipeline_config asdict (auditable) | wiring |

## Corpus Validation

- **Affected documents**: AVB_Muster_GDV_2008.pdf, Verbraucherinformation_Muster_GDV.pdf, Versicherungsbedingungen_Muster_GDV_2008.pdf
- **Expected direction**: stable
- **Spot check count**: 5

## Dependencies

None

## Complexity

Medium
