# Remediation Scorecard — POST-FIX-11 (2026-08-24)

**Pre-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-19_POST-FIX-10.md
**Post-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-24_POST-FIX-11.md
**Delta report:** audit/ZONE_DELTA_2026-08-12_POST-FIX-11.md

## Verdict: REGRESSED

Three new zones (two critical) surfaced during this audit cycle, net bug count rose from 64 to 77 (+13), and zero architectural proposals were implemented despite four detailed simplification proposals being specified across prior cycles. The codebase is accumulating diagnosis faster than it ships fixes, with the longest-stalling critical zone (Garble Detection) having appeared in every audit since first identification yet never landing.

## Zones Closed (2)

| Zone | Was Severity | Bugs Eliminated |
|------|-------------|-----------------|
| Config Snapshot Freeze Drift and Incomplete Wiring Enforcement | high | 8 |
| Mutable ExtractionState Recovery Path Ordering | high | 7 |

## Zones Remaining (5)

| Zone | Severity | Bug Count | Status |
|------|----------|-----------|--------|
| Garble Detection Prong Blindness (NFKC, Script Threading, Title Inspection) | critical | 12 | stalled |
| Picture Enrichment / OCR Filter Composition | high | 11 | improved |
| Recovery Routing Wiring Gaps (Detection Without Remediation) | high | 10 | improved |
| Verdict Threshold Oscillation and Dual-CAS Divergence | high | 9 | stalled |
| Worker/Inspector Dual-Threshold and Timeout Race | medium | 6 | improved |

## New Zones (3)

| Zone | Severity | Introduced By |
|------|----------|---------------|
| Tree-vs-Flat Gate Asymmetry | critical | Pre-existing defect pattern newly characterized as distinct zone. Flat pipeline has ad-hoc single-blob garble check with no structural decomposition while tree pipeline has formal gate table with exhaustiveness assertions. History spans RFC-004 through RFC-030; some findings overlap with prior Garble Detection zone (RFC-013 D7, RFC-019 D2), suggesting zone reorganization contributed to emergence. 11 bugs in current audit. |
| Pre-Tree Text Transforms vs Table/Block Integrity | critical | Pre-existing defect pattern newly characterized as distinct zone. Three independent pre-tree text transforms (heading injection, split_oversized_leaf_nodes, _strip_toc_heading_nodes) operate line-by-line with no table awareness, fracturing tables before _segment_table_nodes sees them. History spans RFC-005 through RFC-036. No shared table-boundary primitive exists despite three modules building ad-hoc versions. 9 bugs in current audit. |
| HR3 PII Egress Gap (Docling + VLM Silent Degradation) | medium | Pre-existing compliance gap newly characterized as distinct zone. DOCLING_SERVICE_URL sends full raw PDF to remote service with zero ZDR/pii_corpus check; VLM fallback compliance block indistinguishable from API failure in metrics. References CLAUDE.md Hard Rules 3 and 4. 5 bugs in current audit. |

## Metrics

- **Net bug delta:** +13 (64 → 77)
- **Total current:** 77
- **Total prior:** 64
- **Improved zones:** 3
- **Regressed zones:** 0
- **Stalled zones:** 2
- **New zones:** 3
- **Closed zones:** 2
- **Wiring status:** all_wired
- **Unwired symbols:** none

## Recommended Next Steps

Three new zones (two critical) surfaced, net bug count rose 64 to 77 (+13), and zero architectural proposals were implemented despite four being specified across prior cycles. The codebase is accumulating diagnosis faster than it ships fixes.

**PRIORITY 1 — Break chronic Garble Detection stall** (critical, 12 bugs, 6+ cycles stalled):
Implement ScriptContext threading across all 10 gate function signatures in gates.py. The single highest-impact change (one-line ScriptContext.from_document call in indexer.py with raw pre-NFKC text) has appeared in every audit since first identified and has never landed. Estimated 6–10 hours per the zone proposal. This is the longest-stalling critical zone and should be treated as a blocking prerequisite for Waves 2–3.

**PRIORITY 2 — Address new critical zones before they calcify into chronic stalls:**
- **(A) Tree-vs-Flat Gate Asymmetry** (critical, 11 bugs): implement per-block _garble_check_flat_blocks and FLAT_GATE_COVERAGE exhaustiveness assertion per the existing unimplemented proposal.
- **(B) Pre-Tree Text Transforms vs Table/Block Integrity** (critical, 9 bugs): implement shared compute_table_spans/line_in_table_span primitives so heading injection, leaf splitting, and ToC stripping become table-aware. Both proposals exist but neither has any code landed.

**PRIORITY 3 — Verdict zone** (high, 9 bugs, stalled 2+ cycles):
Unify verdict authority into Postgres as single source of truth, add verdicts/ prefix to HR2 erasure cascade, remove verdict ledger write path (~−170 lines net). The dual-CAS divergence between MinIO sidecar and Postgres is a silent-corruption vector.

**SEQUENCING** (aligned with prior Wave Plan):
- **Wave 1:** Garble Detection (P1) + Tree-vs-Flat Gate Asymmetry (P2A) — shared gates.py surface, no cross-dependencies.
- **Wave 2:** Pre-Tree Transforms (P2B) + Verdict unification (P3) — depend on gate stabilization from Wave 1.
- **Wave 3:** Picture Enrichment proposal (RegionMetadata pipeline) + HR3 PII egress audit.
- Full corpus revalidation required after each wave.

## Anti-Pattern Flag

Past cycle outcomes show an overall REGRESSED trend with net critical bugs rising 50 → 53 → 77 across recent cycles. Three zones previously closed (Tree/Flat Split, Garble Detection, Converter-Gate-Route) have reopened or regressed in new forms. The implemented_not_wired anti-pattern has been partially addressed (chunked_docling_timeout_s now wired, _check_bidi_coherence deleted, enrichment guard committed) but the proposal_not_implemented pattern is now the dominant blocker — four detailed simplification proposals exist with no code landed for any of them.
