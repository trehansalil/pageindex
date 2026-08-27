---
zone_index: true
audit_date: 2026-08-26
audit_run: POST-FIX-13
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-13.md
total_zones: 7
critical_zones: 3
high_zones: 3
medium_zones: 1
total_bugs: 64
tags:
  - zone-index
  - audit
---
# Architecture Defect Zones Index

**Audit Date:** 2026-08-26  
**Audit Run:** POST-FIX-13  
**Total Zones:** 7 | **Critical:** 3 | **High:** 3 | **Medium:** 1 | **Total Bugs:** 64

## Zone Inventory (Priority Order)

| Priority | Zone | Severity | Bug Count | Status | Notes |
|----------|------|----------|-----------|--------|-------|
| 1 | [[ocr-pipeline-filter-composition-and-re-entry-hazards]] | critical | 15 | audited | Order-dependent flag interaction; UNIFIED_OCR_PLAN_ENABLED bypasses re-entry guard |
| 2 | [[garble-detection-surface-fragmentation]] | critical | 12 | audited | NFKC normalization destroys presentation-form signal; multiple heuristic blind spots |
| 3 | [[verdict-gate-promotion-bypass-cascade]] | critical | 11 | audited | Priority=100 image_enrichment_promoted escape hatch outranks hard-fail verdicts |
| 4 | [[pre-tree-text-transform-table-fracture]] | high | 8 | audited | Line-level transforms lack table-span detection; split_oversized_leaf_nodes unguarded |
| 5 | [[verdict-persistence-competing-writers]] | high | 7 | audited | MinIO sidecar lacks CAS guard; reingestion wipes hysteresis ledger |
| 6 | [[gate-to-recovery-signal-threading-gaps]] | high | 6 | audited | Primary defect ordering masks co-firing defects; 'fixed but never committed' pattern |
| 7 | [[remote-vs-local-execution-divergence]] | medium | 5 | audited | Stale remote Docling image; chunked_docling_timeout_s never wired; AGPL detection incomplete |

## Severity Breakdown

### Critical (3 zones, 38 bugs)
- **OCR Pipeline:** 15 bugs — full_page_already_applied state coupling, marker-count workaround side effects
- **Garble Detection:** 12 bugs — NFKC destruction rediscovered 3 times independently
- **Verdict Promotion:** 11 bugs — explicit priority=100 bypass mechanism, circular threshold coupling

### High (3 zones, 21 bugs)
- **Table Fracture:** 8 bugs — 8 RFCs, same unguarded mechanism in different transform
- **Verdict Writers:** 7 bugs — dual-store eventual consistency with asymmetric guards
- **Signal Threading:** 6 bugs — reason-string coupling, RFC-029 D6 never wired, D19 uncommitted

### Medium (1 zone, 5 bugs)
- **Remote/Local Divergence:** 5 bugs — stale deployment, uncommitted guard, timeout config unused

## Cross-Zone Themes

**Sequential Remediation Chains:** Nearly every RFC's fix becomes the next RFC's root cause. Critical chains: RFC-018→019→020 (picture-OCR); RFC-021→022→023 (verdict-gate); RFC-024→025→026 (threshold saga); RFC-027→028→029→030 (Arabic-recovery); RFC-033→034→035→036 (landscape/barrier).

**'Fixed but Never Committed':** Distinct failure class from logic bugs. Examples: chunked_docling_timeout_s (RFC-027 task 4.2), _check_bidi_coherence, RFC-030 D6 judge rules, RFC-034 D19 enrichment-displacement guard. Most eventually landed by later RFCs, narrowing but not eliminating the pattern.

**Parameter Threading Gaps:** Detection fires but signal never reaches dispatch. Examples: expected_script never passed to garble callers (RFC-019 D2), node_garbling not recognized by OCR escalation (RFC-018 D3b), RFC-029's four new failure reasons never wired (caused Run 13's highest-impact bug).

**NFKC Normalization Blind Spot:** Silently destroys presentation-form signal for both garble detector and bidi-coherence detector. Independently rediscovered in RFC-028 D2, RFC-033 D2, RFC-034 D7. Still structurally present as of 2026-08-26 zone delta.

**Threshold Widening Cycles:** PASS_MAX_LEAF_RATIO widened 3x (0.17→0.20→0.30) chasing jitter on different docs. Hysteresis fix defeated by orthogonal reingestion wipe. No true anchoring.

**Competing Mechanisms:** Page-coverage OCR-skip vs. per-picture forced-OCR; full_page_already_applied guard vs. UNIFIED_OCR_PLAN_ENABLED bypass; digit-ratio floor duplicated in two non-shared functions.

**Verdict-Promotion Bypass Escalation:** Evolved from implicit threshold drift to explicitly hard-coded priority=100 escape hatch. Bug count fell 10→8 but severity escalated high→critical because bypass is now deliberate and actively maintained.

**Null-Sensitivity Misreading:** _check_bidi_coherence measured 0% TPR (signal structurally excluded from range) yet promoted default-true on reasoning that "zero violations = zero risk" rather than detector can't fire.

**Table/Structural Destruction Cascade:** Multiple independent pre-tree transforms lack table-span awareness. Arabic heading injection guards but split_oversized_leaf_nodes, _repair_docling_tables each independently fracture rows.

**Dual-Store Divergence:** MinIO sidecar vs. Postgres registry; each historically had independent CAS and priority maps. 2026-08 fix designated Postgres as arbiter but sidecar still lacks equivalent CAS guard on backfill failures.

**Deployment Synchronization Failure:** BiDi heading reversal root cause was a stale remote Docling image running code predating a guard that exists only in local working tree (never committed). Emerging pattern flagged for independent re-verification.

## Query Links

- View full audit report: [[ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-13]]
- Filter by severity: #critical, #high, #medium
- Filter by topic: #ocr, #garble, #verdict, #table, #storage, #gates, #remote-execution
