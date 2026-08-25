---
zone_name: Verdict Promotion / Quality Gate Stack
severity: critical
wave: 2
priority: 4
status: implemented
audit_date: 2026-08-25
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md
tags:
  - zone-spec
  - critical
  - wave-2
---
## Mechanism to Eliminate

Sequential promotion cascade in apply_promotions() evaluates rescue paths in fixed order where an early match bypasses all subsequent paths, combined with a Postgres verdict-priority CAS (PASS=3>MARGINAL=2>FAIL=1>ERROR=0) that can only upgrade or tie, never downgrade. This creates three interlocking failure modes: (1) a promotion path that fires early (e.g. image_enrichment_promoted) without a sufficient content-volume floor lets zero-content or garbled documents reach PASS, which the SQL CAS then permanently locks in -- a later improved garble check correctly classifying the doc as FAIL cannot self-heal the stored verdict; (2) threshold changes calibrated against one problematic document (e.g. low_content_density from 500 to 150 chars/node) cause corpus-wide oscillation because the sequential cascade gives no visibility into near-miss candidates across the corpus; (3) the verdict-priority CASE expression is copy-pasted verbatim 4 times in _UPSERT_SQL (lines 67, 73, 79, 85 of queries.py) diverging from the canonical VERDICT_PRIORITY dict in helpers/types.py:37, meaning a priority-map change must be replicated in 5 locations.

## Strategy

Three-PR restructuring: PR1 extracts the 4x duplicated verdict-priority CASE into a Postgres function referencing the canonical VERDICT_PRIORITY dict and adds a force_verdict_override boolean to upsert_doc (default False, no behavioral change). PR2 refactors apply_promotions() from sequential first-match cascade to score-all-then-pick-best pattern using a PromotionCandidate list with uniform content-floor filtering, extracting each promotion path into a named _try_* function. PR3 wires force_verdict_override=True into re-ingestion calls when pipeline_version is strictly newer, gated behind VERDICT_DOWNGRADE_ENABLED config flag (default False).

## Code Targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| src/pageindex_mcp/registry/queries.py | 64-90 | Extract 4x duplicated verdict-priority CASE expression into a SQL helper function verdict_priority(text) and a Python-side _VERDICT_PRIORITY_SQL constant generated from VERDICT_PRIORITY dict | Replace the 4 identical CASE WHEN expressions (verified at lines 67, 73, 79, 85) with calls to a single verdict_priority() SQL function generated from helpers/types.py:37's VERDICT_PRIORITY dict at module load time. | The SQL function must be created via a migration or CREATE OR REPLACE in a startup hook; the priority mapping must be generated from VERDICT_PRIORITY dict, never hardcoded separately |
| src/pageindex_mcp/registry/queries.py | 94-134 | Add force_verdict_override parameter to upsert_doc() that bypasses the verdict-priority CAS guard | Add optional bool parameter force_verdict_override=False to upsert_doc(); when True use an alternate SQL template where verdict columns always take EXCLUDED values. | Default must be False; force_verdict_override=True must still respect processed_at CAS guard |
| src/pageindex_mcp/helpers/verdict.py | 219-347 | Refactor apply_promotions() (verified starts exactly at line 219, ends at 347) from sequential first-match cascade to score-all-then-pick-best using PromotionCandidate list | Extract each promotion path into named _try_* functions returning Optional[PromotionCandidate]; collect, filter below floor, pick highest priority. | image_enrichment_promoted must retain RFC-022 B2 priority weight |
| src/pageindex_mcp/helpers/types.py | 37 | Add PromotionCandidate type; VERDICT_PRIORITY confirmed canonical (verified at types.py:37) | Add frozen dataclass PromotionCandidate; import-time assertion VERDICT_PRIORITY values unique/ordered. | priority: higher is better |
| src/pageindex_mcp/client/indexer.py | 853-860, 973-980 | Wire force_verdict_override into re-ingestion path (compute_verdict call sites verified exact at lines 853 and 973) | Pass force_verdict_override=True into verdict_fields dict when VERDICT_DOWNGRADE_ENABLED and pipeline_version strictly newer. | Gated behind VERDICT_DOWNGRADE_ENABLED, default False |
| src/pageindex_mcp/worker/registry_mirror.py | 98-104 | Thread force_verdict_override through to upsert_doc call (fields.update verified at line 101, upsert_doc call verified at line 104) | Pop force_verdict_override from fields dict before calling upsert_doc so it's a kwarg not a column value. | Must not be persisted to MinIO sidecar |
| src/pageindex_mcp/config.py |  | Add VERDICT_DOWNGRADE_ENABLED config flag | Add to PipelineConfig dataclass (class verified present at config.py:368), sourced from env var, default False. | Must be a PipelineConfig field, not module-level constant |

## Wiring Checks

| Symbol | Must Be Imported By | Check Type |
|---|---|---|
| PromotionCandidate | ['src/pageindex_mcp/helpers/verdict.py', 'src/pageindex_mcp/helpers/__init__.py'] | import |
| verdict_priority | ['src/pageindex_mcp/registry/queries.py'] | call |
| force_verdict_override | ['src/pageindex_mcp/registry/queries.py', 'src/pageindex_mcp/worker/registry_mirror.py', 'src/pageindex_mcp/client/indexer.py'] | call |
| VERDICT_DOWNGRADE_ENABLED | ['src/pageindex_mcp/client/indexer.py'] | import |
| _try_image_enrichment | ['src/pageindex_mcp/helpers/verdict.py'] | call |
| _try_flat_promotion | ['src/pageindex_mcp/helpers/verdict.py'] | call |
| _try_structural_pass | ['src/pageindex_mcp/helpers/verdict.py'] | call |
| _try_ocr_promotion | ['src/pageindex_mcp/helpers/verdict.py'] | call |
| _try_content_class_promotion | ['src/pageindex_mcp/helpers/verdict.py'] | call |
| _try_small_doc_promotion | ['src/pageindex_mcp/helpers/verdict.py'] | call |

## Test Requirements

| Test File | What to Test | Assertion Type |
|---|---|---|
| tests/test_verdict_promotion_candidates.py | Each _try_* extractor boundary cases; score-all-then-pick-best matches old cascade for existing fixtures. | exhaustiveness |
| tests/test_verdict_promotion_candidates.py | PromotionCandidate priority ordering, image_enrichment_promoted wins. | contract |
| tests/test_rfc037_verdict_cas.py | force_verdict_override bypass behavior and default preservation; SQL verdict_priority() matches Python dict. | contract |
| tests/test_rfc037_verdict_cas.py | SQL verdict_priority function mapping matches VERDICT_PRIORITY dict exactly. | regression |
| tests/test_verdict_promotion_candidates.py | RFC-025/023/036 regression fixtures. | regression |
| tests/test_registry.py | force_verdict_override threads through _upsert_registry_row -> upsert_doc without persisting to MinIO sidecar. | wiring |
| tests/test_compute_verdict.py | compute_verdict with VERDICT_DOWNGRADE_ENABLED True/False and version comparisons. | integration |

## Corpus Validation

- **Affected documents:** ['federal_decree_law_no_33', 'marsoom_33', 'penal_code', 'cabinet_resolution_no_96', 'sla_document', 'reitlehrer', 'mou_document']
- **Expected verdict direction:** stable
- **Spot check count:** 7

## Dependencies

Depends on: Garble Detection Fragmentation, OCR Strategy Bifurcation

## Complexity

medium
