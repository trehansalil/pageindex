# Zone Delta Analysis — POST-FIX-11

**Current audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-24_POST-FIX-11.md
**Prior audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-19_POST-FIX-10.md
**Date:** 2026-08-12

## Summary

Total tracked bug count rose from 64 to 77 (net +13) across this cycle. Of the 5 zones carried forward from the prior audit, 3 improved (severity or bug count dropped), 2 stalled (no measurable progress despite sharper root-cause framing), and 0 regressed. 3 new zones were identified — most notably **Tree-vs-Flat Gate Asymmetry** and **Pre-Tree Text Transforms vs Table/Block Integrity**, both critical severity with proposals already drafted but not yet implemented per CodeGraph — plus a medium-severity **HR3 PII Egress Gap** tied directly to CLAUDE.md Hard Rules 3 and 4. 2 zones closed (Config Snapshot Freeze Drift, Mutable ExtractionState Recovery Path Ordering), likely absorbed into the Recovery Routing and Picture Enrichment zones following the client.py → client/indexer.py module split. The chronic **implemented-but-never-wired** anti-pattern continues to dominate: 9 of 12 historically unwired symbols persist, and four of five zones with drafted simplification proposals (RegionMetadata/_scan_regions/splice_pictures, ScriptContext threading, Postgres-authority verdict consolidation, per-block flat-gate coverage) remain `not_implemented` against the live codebase.

## Delta Table

| Zone | Status | Severity (prior→current) | Bugs (prior→current) | Proposal Status | Key Change |
|---|---|---|---|---|---|
| Recovery Routing Wiring Gaps (Detection Without Remediation) | improved | critical→high | net −1 | no_proposal | Reframed from narrow reason-code coupling to 3 structural classes (unfireable detectors, fixed-never-wired, early-exit misordering); prior GateSpec/dispatch-refactor proposal superseded |
| Picture Enrichment / OCR Filter Composition | improved | critical→high | net −1 | not_implemented | Reframed from two-subsystem collision to filter-chain composition; single-flag conflation issue absorbed; RegionMetadata/_scan_regions/splice_pictures proposal untouched (~3-4 days est.) |
| Garble Detection Prong Blindness (NFKC, Script Threading, Title Inspection) | stalled | critical→critical | 0 | not_implemented | Sharper diagnosis (4 blind-spot classes vs "8+ patchwork prongs") but zero code movement; all 10 gate fns still `expected_script: str \| None` |
| Verdict Threshold Oscillation and Dual-CAS Divergence | stalled | high→high | 0 | not_implemented | Adds dual-CAS divergence + HR2 erasure gap findings; Postgres-authority consolidation proposal (~−170 lines, 4-5 days) untouched |
| Worker/Inspector Dual-Threshold and Timeout Race | improved | critical→medium | +1 | no_proposal | Cross-process error-classification danger (child-crash misclassification) resolved; residual calibration/coordination issues remain |

## Per-Zone Details

### Recovery Routing Wiring Gaps (Detection Without Remediation)
*Prior: GATE_TABLE to Recovery Dispatch Reason-Code Coupling*

**What changed:** The prior audit framed this zone narrowly around reason-code string coupling — new gate defect reasons falling through if/elif recovery checks to a terminal `LowQualityTreeError`. The current audit broadens this into three distinct structural problem classes: (1) parameter/reason-string threading gaps that make detectors unfireable, (2) "fixed but never wired/committed" as its own failure class, and (3) `validate_tree` early-exit ordering causing wrong reason assignment. This is a diagnostic refinement of the same coupling surface, not a new defect family. **PAST DECISIONS FLAG:** this zone continues to exhibit the chronic `implemented_not_wired` anti-pattern — 9 of 12 historically unwired symbols were tracked as persisting across prior fix cycles.

**New findings:**
- RFC-027 task 4.2: `chunked_docling_timeout_s` created but never wired to `worker.py`
- RFC-028 D0: world-stats-pocketbook timed out 3 consecutive runs
- RFC-029 D0: `_check_bidi_coherence` fully implemented, never wired (dead code)
- RFC-030 D5: confirmed dead code
- RFC-034 D19: enrichment-displacement guard staged but never committed
- RFC-036 D2: finally committed already-staged code
- Cross-cutting: `validate_tree` early-exit bypasses garble detection and OCR escalation

**Resolved findings:**
- RFC-004 D1: disabled validation rejection for node_count<3/depth<2
- RFC-026 D5: moved garble check before early-exit attempting fix
- RFC-030 D2: wired the 4 reasons
- RFC-016 D4/D5: VLM fallback gated only on reason=='garbling', bypassed for shallow-tree scanned Arabic
- RFC-023 D11: garble-aware exemption causes structural failure reasons instead of garbling, OCR escalation never fires
- RFC-036 D3: 'rtl_reversal' hit terminal-raise list instead of flat-routing whitelist
- RFC-027 D2 / RFC-028 D4: OCR escalation unconditional md_content overwrite (al-qarar 230-to-123 chars)
- RFC-023 D3/Run 9: garble detected but no escalation hook

**Proposal implementation status:** `no_proposal` — the current zone has no simplification proposal. The prior zone's proposal (GateSpec recovery_fns + split `_recover_ocr_retry` + dispatch loop refactor) was superseded by the zone reorganization; no replacement proposal was generated for the current framing.

**Key files:** `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/helpers/gates.py`, `src/pageindex_mcp/helpers/types.py`, `src/pageindex_mcp/helpers/tree_validation.py`

---

### Picture Enrichment / OCR Filter Composition
*Prior: Picture/OCR Enrichment and Page-Level Escalation Conflation*

**What changed:** The prior audit framed this as a collision between two independently-evolved subsystems — page-level escalation in `client.py` vs per-picture enrichment in `converters.py` — centered on a single `_OCR_ESCALATION` boolean gating both concerns. The current audit reframes it as filter-chain composition: independently-tuned filters (coverage >60%, text-layer >20 chars clip-text, forced-OCR reclassification, synthetic `PictureResult` list multiplication) combining to silently zero out enrichment. The single-flag `OCR_IMAGE_BLOCK_CONFLATION` framing is no longer listed as distinct, suggesting it was absorbed into the broader filter-composition characterization. Key files shifted from `picture_plane.py`/`converters.py` to `converters/pictures.py` (post-module-split), with `verdict.py` added as a new coupling surface.

**New findings:**
- RFC-018 D3a: forced OCR without `ocr_lang_override` → Arabic mojibake
- RFC-020 F1/F2/F3/F4/F5: five fixes to the same filter composition
- RFC-021 QF1 → RFC-022 B3: GHV-TKV OCR splice regression
- RFC-023 D8a: standalone images create synthetic `PictureResult` with empty `ocr_text`

**Resolved findings:**
- RFC-023 D1: `splice_figure_markers` count mismatches
- RFC-023 D0 Run 8 regression: 5 docs reverted to 0 chars/ERROR
- RFC-034 D19: boilerplate displacement of real OCR digits/labels (pie chart)
- OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION: single `_OCR_ESCALATION` flag gates both concerns

**Proposal implementation status:** `not_implemented` — proposal calls for a `RegionMetadata` dataclass, `_scan_regions` extraction, unified `splice_pictures` function, and a three-phase scan→classify→execute pipeline. CodeGraph confirms none of `RegionMetadata`, `_scan_regions`, or `splice_pictures` exist anywhere in the codebase; the existing filter chain in `pictures.py` and `indexer.py` remains structurally unchanged from what the audit describes. Estimated 3-4 days implementation.

**Key files:** `src/pageindex_mcp/converters/pictures.py`, `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/helpers/verdict.py`

---

### Garble Detection Prong Blindness (NFKC, Script Threading, Title Inspection)
*Prior: Garble Detection Heuristic Patchwork*

**What changed:** The prior audit described "eight+ independently-calibrated garble prongs each calibrated to one known-bad document, routinely over-firing or under-firing." The current audit sharpens this into four specific blind-spot classes: (A) NFKC normalization destroying presentation-forms signal before detection, (B) `digit_ratio` whole-document dilution, (C) `node.title` never inspected, (D) `latin_gibberish` self-classification loop. The underlying defects are identical but the root-cause categorization is more precise. Despite the sharper understanding, there is zero implementation progress. **PAST DECISIONS FLAG:** this zone has been chronically stalling across multiple delta cycles — 9 of 12 unwired symbols persist from prior cycles, and the `expected_script` threading gap (RFC-019 D2) has appeared in every audit since it was first identified.

**New findings:**
- RFC-018 D3b: `node_garbling` reason never recognized by OCR escalation
- RFC-025 D3: recovery triggers check `'garbling'` only, missing `'node_garbling'`
- RFC-029 D1/D2: four new `validate_tree` failure reasons never wired into recovery
- RFC-030 D2: unhandled reasons caused 3 PASS→ERROR regressions (Run 13, highest-impact)
- Run 11: five independent Arabic-garble instances, none caught by PUA-only heuristic

**Resolved findings:**
- RFC-010 D3/D3B: token-repetition duplicated into `_tree_is_garbled` and `_flat_text_is_garbled` independently
- RFC-015 D8: sparse mixed-script mojibake coverage gap
- RFC-020 F2: filename-derived `expected_script` caused new forced-OCR regression
- RFC-028 D5: filename-based Arabic lang detection diluted garble ratio (warid-597 MARGINAL→PASS)
- RFC-027 D3/RFC-028 D3: RTL readability scoring only 14 common words, siyasat hawkama 100% reversed stored PASS
- obs #5627: RTL word-splitting and embedded Latin OCR fragments escape all heuristics

**Proposal implementation status:** `not_implemented` — proposal calls for threading a `ScriptContext` (replacing `str | None`) through all 10 gate function signatures, fixing `latin_gibberish` self-classification, adding per-node `digit_ratio`, and enriching `ScriptContext` with raw pre-NFKC text. CodeGraph confirms all 10 `_gate_*` functions in `gates.py` still reference `expected_script: str | None`; no signature migration has occurred. The single highest-impact fix (one line in `indexer.py` to compute `ScriptContext.from_document` with raw_text) has not landed. Estimated 6-10 hours.

**Key files:** `src/pageindex_mcp/helpers/garble.py`, `src/pageindex_mcp/helpers/tree_validation.py`, `src/pageindex_mcp/helpers/gates.py`, `src/pageindex_mcp/client/indexer.py`

---

### Verdict Threshold Oscillation and Dual-CAS Divergence
*Prior: Verdict Threshold Oscillation and Hysteresis Failure*

**What changed:** The prior audit centered on threshold widening without hysteresis producing oscillation, with the key finding being the hysteresis mechanism's structural dependency on a wiped MinIO store. The current audit adds two new instability sources: (1) dual-CAS divergence, where Python string comparison in the MinIO sidecar and SQL CASE logic in Postgres hold different verdicts indefinitely with no error surfaced, and (2) a verdict-ledger HR2 erasure gap, where erased document verdict data persists and is silently reapplied. Three concurrent registry writers are added as a new concurrency concern. The prior ToC-stripping and synthetic-structure findings resolved, replaced by write-visibility barrier and verdict-inflation findings. **PAST DECISIONS FLAG:** this zone regressed +3 bugs in a prior cycle; boolean-flag conflation was identified as a chronic regression vector. The threshold-widening pattern (0.17→0.20→0.30) persists unchanged across all audit cycles.

**New findings:**
- RFC-026 D3: GHV-TKV-Tarif flapped PASS→MARGINAL on identical tree after wipe
- Run 8: Doc 8 Reitlehrer remained degraded despite widening
- RFC-034 D18: write-visibility barrier over-provisioned (4.4s delay), ERROR propagated
- RFC-036 D1: reduced delays, caught error as warning
- Run 10-12: verdict inflation/oscillation on unchanged extraction metrics

**Resolved findings:**
- RFC-022 B1-Fix: synthetic structure promoted placeholder-only docs to PASS (doc 21 Domestic Workers)
- RFC-022 B1-Fix: guard only triggers when flat_structure completely empty (doc 20 missed)
- RFC-029: `low_content_density` 500 chars/node calibrated to marsoom-13, rejected 3 legitimate trees
- RFC-034 D11/D16: ToC-heading stripping over-stripped Penal Code (depth 3→2, 493/595 nodes flattened)
- RFC-034 D16: guarded fix incomplete, Federal Decree-Law 47 has 88% bodyless headings

**Proposal implementation status:** `not_implemented` — proposal calls for unifying verdict authority into Postgres as single source of truth: (A) SQL max-priority-wins guard in `_UPSERT_SQL`, (B) `verdicts/` prefix in HR2 erasure cascade, (C) delete verdict-ledger write path (~−90 lines), (D) remove hysteresis from indexer (~−50 lines), (E) collapse sidecar CAS to pass-through, (F) remove deprecated `upsert_verdict` wrapper. CodeGraph confirms `persist_verdict_ledger` (in_degree=4), `read_verdict_ledger`, `_LEDGER_VERDICT_PRIORITY`, and `upsert_verdict` all remain present and wired; `delete_doc` has no `verdicts/` prefix step; no max-priority-wins addition exists in `_UPSERT_SQL`. Estimated ~−170 lines net, 4-5 engineering days across 3 releases.

**Key files:** `src/pageindex_mcp/helpers/verdict.py`, `src/pageindex_mcp/storage/verdict.py`, `src/pageindex_mcp/storage/documents.py`, `src/pageindex_mcp/registry/queries.py`

---

### Worker/Inspector Dual-Threshold and Timeout Race
*Prior: Cross-Process Error Classification Boundary*

**What changed:** The prior audit focused on cross-process error classification: a child crash (SIGKILL/OOM) producing a `None` error_class fell through to a generic non-terminal reason, causing a genuinely-terminal `LowQualityTreeError` to be retried up to `MAX_TRIES`; `_TERMINAL_CHILD_REASONS` had only 2 entries against 10+ gate defects with no coverage assertion, plus a `reap_stale_jobs` vs extended-timeout race. The current audit narrows to a dual-threshold confidence gap — `indexer.py` requires `confidence>=0.90` for forced OCR while `subprocess_mgr.py` applies a 16.5x timeout on `pdf_type` alone with no confidence check — and the timeout race, where the extended deadline persists to Redis only after subprocess return, leaving a conservative default visible in the interim. The critical child-crash classification and terminal-reason coverage gap issues appear resolved. Bug count rose by 1 (5→6) but severity dropped two levels (critical→medium), indicating the most dangerous systemic issues were addressed, leaving residual calibration and coordination problems.

**New findings:**
- RFC-032 D9: recalibrated to 16.5x
- RFC-028 D0: `chunked_docling_timeout_s` never wired; world-stats-pocketbook timed out 3x
- RFC-034 D18: write-visibility barrier added 4.4s delay, document scored as false ERROR
- RFC-036 D1: reduced delays
- Run 8→9: exception patch converted Arabic CMap crash to near-empty artifact

**Resolved findings:**
- RFC-006 D3: fire-and-forget async Postgres registry delete logged 'success' over silent failure
- RFC-033 D3: read-side minio retry insufficient for write-visibility racing
- Registry dual-write non-atomic: `worker._upsert_registry_row` and `storage.save_doc` coordinate via SQL CAS with no app-level lock
- PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT: `registry:complete` write-once latch never resets for post-backfill incremental failures

**Proposal implementation status:** `no_proposal` — neither the current nor prior zone generated a simplification proposal. The zone remains at medium severity with no architectural simplification recommended.

**Key files:** `src/pageindex_mcp/worker/subprocess_mgr.py`, `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/worker/job.py`, `src/pageindex_mcp/converters/pipeline.py`

## New Zones

- **Tree-vs-Flat Gate Asymmetry** (critical, 14 bugs): Entirely new zone identifying that all 10 `GATES` entries protect only tree-routed documents; the flat path has a single ad-hoc `detect_garble` call invisible to exhaustiveness asserts. History spans RFC-004 through RFC-030, indicating a pre-existing defect pattern newly characterized as a distinct zone rather than a remediation side effect. Some findings overlap with prior Zone 3 (RFC-013 D7, RFC-019 D2), suggesting zone reorganization contributed to its emergence. Proposal exists (per-block `_garble_check_flat_blocks` + `FLAT_GATE_COVERAGE` assertion) but is `not_implemented` per CodeGraph.

- **Pre-Tree Text Transforms vs Table/Block Integrity** (critical, 11 bugs): New zone identifying that three independent pre-tree text transforms (heading injection, `split_oversized_leaf_nodes`, `_strip_toc_heading_nodes`) operate line-by-line with no table awareness, fracturing tables before `_segment_table_nodes` sees them. History spans RFC-005 through RFC-036, indicating a long-standing defect pattern. No shared table-boundary primitive exists despite three modules having built ad-hoc versions. Proposal exists (shared `compute_table_spans`/`line_in_table_span`) but is `not_implemented` per CodeGraph.

- **HR3 PII Egress Gap (Docling + VLM Silent Degradation)** (medium, 4 bugs): New zone identifying two structural HR3 bypass paths: (1) `DOCLING_SERVICE_URL` sends the full raw PDF to a remote service with zero ZDR/pii_corpus check, (2) VLM fallback compliance block is indistinguishable from an API failure in metrics. References CLAUDE.md Hard Rules 3 and 4, and project memory on the AGPL three pullers. No proposal generated.

## Closed Zones

- **Config Snapshot Freeze Drift and Incomplete Wiring Enforcement** (was high, 8 bugs prior): Tracked config snapshot freeze and wiring enforcement gaps. No longer identified as a distinct defect zone in the current audit. May have been partially absorbed into other zones or addressed by recent refactoring. Prior key files: `config.py`, `helpers.py`, `storage.py`.

- **Mutable ExtractionState Recovery Path Ordering** (was high, 7 bugs prior): Tracked mutable `ExtractionState` causing recovery path ordering issues. No longer identified in the current audit. Prior key files: `client.py`, `helpers.py`, `converters.py` (pre-module-split). The `client.py` decomposition into `client/indexer.py` may have resolved the mutable-state ordering issues, or the defects may have been redistributed into the Recovery Routing Wiring Gaps (Zone 1) and Picture Enrichment (Zone 2) zones.
