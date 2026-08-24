# Zone Delta Analysis — POST

**Current audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md
**Prior audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-19_POST-FIX-10.md
**Date:** 2026-08-12

## Summary

This delta compares 6 matched zone pairs plus 2 new zones and 1 closed zone, moving the total defect count from 50 to 53 (net +3). Of the 6 matched zones, 3 regressed, 2 improved, and 1 stalled — no zone was fully resolved. The most consequential shift is **Tree/Flat Verdict Split** (formerly Verdict Threshold Oscillation and Hysteresis Failure), which escalated high→critical as its mechanism broadened from narrow threshold whack-a-mole to a structural finding that any verdict fix must be implemented twice across divergent code paths with no shared validation contract — continuing a regression trajectory already flagged in the prior POST-FIX-10 delta. **Garble Detection Fragmentation** remains stalled at critical severity for a 6th+ consecutive cycle: infrastructure (a unified `GateSpec` registry, startup-time wiring validation) has landed, but the three generative causes — NFKC ordering, `expected_script` self-corruption, and per-document calibration — persist identically. **Converter-Gate-Route Ordering Chain** also regressed (critical, unchanged) as its mechanism broadened from reason-code coupling to a three-part ordering chain spanning converter selection, gate evaluation, and stale post-recovery routing. Two zones improved: **Worker-Child Process Boundary** (critical→high, exhaustiveness-assertion auto-sync closed the manual-sync risk) and **Duplicated Convergent Logic**, formerly Config Snapshot Freeze Drift, whose config-drift and wiring-enforcement concerns are now largely resolved, leaving a smaller, lower-severity residual zone. **Arabic/RTL Pipeline Blindness** regressed by 2 bugs even as its mechanism narrowed, reversing the one "improved" result (Mutable ExtractionState, critical→high) from the prior delta. One zone closed (Picture/OCR Enrichment and Page-Level Escalation Conflation); two new zones surfaced (Registry Dual-Write Consistency, ZDR/PII Egress Gap — the latter bearing directly on Hard Rule 3). Only one matched zone (Garble Detection Fragmentation) has its production entry point fully wired end-to-end with zero gaps; the Tree/Flat Verdict Split zone's unified `classify_verdict` entry point remains reachable only from batch scripts and tests, not the live MCP/worker pipeline — a partial-implementation gap consistent with Hard Rule 5's requirement that `validate_tree()` gate `save_doc`.

## Delta Table

| Zone | Status | Severity (prior→current) | Bugs (prior→current, Δ) | Proposal Status | Key Change |
|---|---|---|---|---|---|
| Tree/Flat Verdict Split *(was: Verdict Threshold Oscillation and Hysteresis Failure)* | regressed | high→critical | Δ+2 | partially_implemented | Mechanism broadened from threshold/hysteresis to a 7-gate asymmetry + dual promotion paths; unified entry point (`classify_verdict`) not on the served pipeline |
| Garble Detection Fragmentation *(was: Garble Detection Heuristic Patchwork)* | stalled | critical→critical | Δ0 | implemented_and_wired | Unified `GateSpec` registry landed and is production-wired, but NFKC ordering, `expected_script` self-corruption, per-doc calibration unchanged; `hash_pipe_ratio` orphaned |
| Converter-Gate-Route Ordering Chain *(was: GATE_TABLE to Recovery Dispatch Reason-Code Coupling)* | regressed | critical→critical | Δ+1 | implemented_and_wired | Mechanism broadened to converter-selection fragility + flat-gate narrowing + stale routing after recovery; wiring correct for old defect, gaps remain for new one |
| Worker-Child Process Boundary *(was: Cross-Process Error Classification Boundary)* | improved | critical→high | Δ0 | implemented_and_wired | `_TERMINAL_CHILD_REASONS` now derived from `_CHILD_ERROR_REGISTRY` with startup exhaustiveness assertion; 2 findings resolved |
| Arabic/RTL Pipeline Blindness *(was: Mutable ExtractionState Recovery Path Ordering)* | regressed | high→high | Δ+2 | no_proposal | Mechanism narrowed to Arabic/RTL-specific blindness (headings, OCR lang detection, table RTL, content-density) but bug count still rose; reverses prior "improved" verdict |
| Duplicated Convergent Logic *(was: Config Snapshot Freeze Drift and Incomplete Wiring Enforcement)* | improved | high→medium | Δ-3 | not_applicable | Config-drift/wiring-enforcement concerns largely resolved; residual zone narrowed to genuinely duplicated logic (flat-block text, garble, verdict hysteresis, table-text) |

**New zones:** Registry Dual-Write Consistency; ZDR/PII Egress Gap
**Closed zones:** Picture/OCR Enrichment and Page-Level Escalation Conflation

**Totals:** 50 → 53 bugs (net +3) · 2 improved · 3 regressed · 1 stalled · 2 new · 1 closed

## Per-Zone Details

### Tree/Flat Verdict Split (was: Verdict Threshold Oscillation and Hysteresis Failure)
**Status:** regressed · **Severity:** high → critical · **Bugs:** +2 · **Proposal:** partially_implemented

**What changed.** The prior zone was scoped narrowly to threshold-widening oscillation and structurally-inert hysteresis (the MinIO wipe defeating `find_prior_verdict`). The current zone broadens the mechanism to the entire tree/flat verdict split: the 7-gate asymmetry where the flat path sees only 3 gates, promotion branches that independently return PASS without re-consulting defect sets, and the original threshold oscillation findings all now fold into one mechanism — "any fix must be implemented twice across divergent code paths with no shared validation contract." Severity was escalated high→critical to match.

**Key files:** `src/pageindex_mcp/helpers/verdict.py`, `src/pageindex_mcp/helpers/gates.py`, `src/pageindex_mcp/client/indexer.py`

**New findings:**
- RFC-022 B1: synthetic structure from empty flat blocks triggered false garble (D3) and false PASS via `cat_b_promoted` (D4)
- RFC-022 B2-B: `image_enrichment_promoted` reordered above `max_leaf_ratio` hard-fail
- RFC-023 D4: added content-quality guards to `cat_b_promoted`
- RFC-023 D5: expanded synthetic-structure guard
- RFC-025 D0: added hysteresis via `find_prior_verdict`, structurally dead on reingestion
- RFC-029 D1/D2: 4 new `validate_tree` reasons with no flat-path recovery routing → 3 PASS→ERROR regressions
- RFC-030 D3: `low_content_density` threshold too aggressive for legal documents
- Run 9: `image_enrichment_promoted` let 38–123 char docs PASS (violating Hard Rule 5)
- Run 8→9: German garbled doc (Haftpflicht) FAIL→PASS via `expected_script` propagation gaps + hysteresis relaxation
- Runs 7→8: all 8 RFC-023 fix categories regressed simultaneously (17 PASS → 7 PASS)

**Resolved findings:**
- RFC-022 B1-Fix: guard only triggers when `flat_structure` is completely empty (doc 20 missed)
- RFC-029 D6: judge-calibration rules designed but never written to SKILL.md; phantom regressions persisted

**Proposal implementation status.** `compute_verdict`, `evaluate_gates`, `apply_promotions`, `detect_regression` in `helpers/verdict.py` are production-wired (in-degree 2–13, called from `client/indexer.py`). However `classify_verdict` (in-degree 54) is reached only from root-level batch scripts (`promotion_sweep.run_sweep`, `preprocess_client.recompute_verdicts`) and tests — never from the live MCP/worker pipeline (`server.py` / `worker/job.py`). The verdict-classification split exists structurally but the unified entry point is not on the served pipeline.

**History flag.** The prior zone (Verdict Threshold Oscillation) was already marked "regressed" in the POST-FIX-10 delta (Δ+3) with hysteresis wired yet structurally inert. Severity has now escalated to critical with a broadened mechanism, continuing that regression trajectory across two consecutive delta cycles.

---

### Garble Detection Fragmentation (was: Garble Detection Heuristic Patchwork)
**Status:** stalled · **Severity:** critical → critical · **Bugs:** 0 · **Proposal:** implemented_and_wired

**What changed.** The generative mechanism is unchanged: fragmented heuristics each calibrated per-document, NFKC normalization decomposing detection signals before checking (0% TPR on reversed-morphology), `expected_script` self-corruption from corrupted text, and per-call-site wiring inconsistency. The zone was renamed "Patchwork" → "Fragmentation" but the underlying defect pattern is identical. Post-refactor, code moved from `helpers.py` into `helpers/gates.py` and `helpers/tree_validation.py`, but the same structural problems persist.

**Key files:** `src/pageindex_mcp/helpers/gates.py`, `src/pageindex_mcp/helpers/tree_validation.py`, `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/helpers/verdict.py`

**New findings:**
- RFC-029 D4: `_repeating_token_density` short-text floor made OCR retry win condition arithmetically impossible
- RFC-034 B1-C2: confirms 0% TPR, 40% Latin mojibake undetected
- Run 8: `expected_script` parameter removed, garble detection regressed on Latin-gibberish CMap mojibake
- Run 9: detection partially restored but OCR escalation not wired
- Session memory (Jul 31): 5 distinct Arabic corruption mechanisms invisible to PUA-only heuristic

**Resolved findings:**
- RFC-015 D8: sparse mixed-script mojibake coverage gap
- RFC-013/RFC-015: Latin-gibberish scope gap for German filenames (Haftpflicht 81/132 garbled nodes FAIL-to-PASS via four compounding gaps)
- RFC-030 D4: garble gate ignores `node.title` (siyasat-hawkama 23/24 reversed titles undetected)
- RFC-027 D3/RFC-028 D3: RTL readability scoring only 14 common words, siyasat hawkama 100% reversed stored PASS
- obs #5627: RTL word-splitting and embedded Latin OCR fragments escape all heuristics

**Proposal implementation status.** All `_gate_*` garble/structure functions in `helpers/gates.py` are unified into a single declarative `GATES: list[GateSpec]` registry, dispatched via a `gate_fn` field into `GATE_TABLE`/`FLAT_GATE_SUBSET`, feeding `validate_tree` (in-degree 41, production-wired). `validate_feature_wirings()` is called at startup from both `server.py:_lifespan_with_scrape` and `worker/lifecycle.py:startup`, refuting the stale 08-19 audit claim of atexit-only invocation. Gap: `hash_pipe_ratio` in `helpers/tree_validation.py` has in-degree=0 and out-degree=0 — orphaned dead code.

**History flag.** Longest-stalled zone across 6+ remediation cycles (already flagged in the POST-FIX-10 delta as longest-stalled). Infrastructure has landed (`GateSpec` registry, startup wiring validation), but the three generative causes — NFKC ordering, `expected_script` self-corruption, per-doc calibration — persist identically.

---

### Converter-Gate-Route Ordering Chain (was: GATE_TABLE to Recovery Dispatch Reason-Code Coupling)
**Status:** regressed · **Severity:** critical → critical · **Bugs:** +1 · **Proposal:** implemented_and_wired

**What changed.** The prior zone focused on a two-site maintenance contract: adding a reason to `GATES` required wiring into three separate recovery paths. The current zone broadens to a three-part chain: (1) converter chain-order fragility — OCR escalation only reachable via the docling branch; (2) flat-gate narrowing — the 3-gate subset; and (3) recovery mixins that re-run `validate_tree` but do not re-trigger `decide_route`, so stale routing persists after recovery fixes a defect. The mechanism evolved from "reason-code coupling" to "ordering chain across converter selection, gate evaluation, and route decision" — a superset that subsumes the prior coupling issue plus new converter-selection and stale-route defects.

**Key files:** `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/helpers/types.py`, `src/pageindex_mcp/converters/pipeline.py`, `src/pageindex_mcp/worker/errors.py`

**New findings:**
- RFC-003 D3: Docling made primary, contingent on Phase 0 validation
- RFC-003 Amendment 3: pymupdf4llm moved to primary after Docling MPS NO-GO, fully opening the AGPL gate
- RFC-003 Amendment 4: Docling restored as default primary via `PDF_CONVERTER` config
- RFC-005 Fix-3: OCR escalation retry on garbling reason only
- RFC-016 D5: D4 tree-path VLM block skipped for structural reasons (node_count<3) instead of garbling
- RFC-020 F2/F3: Arabic OCR lang detection broke F0 tree-path splice (zero PictureResults)
- RFC-023 D0/D11: structural `validate_tree` reasons pre-empted garble check, blocking OCR escalation
- RFC-028 D4: unconditional `md_content` overwrite on OCR retry
- RFC-030 D1: `_repeating_token_density` floor made OCR retry win condition impossible
- RFC-032 D0-D2: inspector OCR pre-routing wired but required 16.5x timeout multiplier

**Resolved findings:**
- RFC-018 D3b: added 'node_garbling' reason, never matched by any of 3 recovery triggers
- RFC-025 D3: extended triggers to match ('garbling','node_garbling') but still missed node_count<3 early-exit before garble check
- RFC-026 D5: moved garble check before early-exit
- RFC-036 D3: 'rtl_reversal' hit terminal-raise list instead of flat-routing whitelist
- RFC-023 D3/Run 9: garble detected but no escalation hook

**Proposal implementation status.** `decide_route` (`helpers/types.py`, in-degree 3) is called from `client/indexer.py:_convert_to_tree` — confirmed via search_code match_lines. `pdf_to_markdown_docling`, `_run_stages`, `_build_candidate` in `converters/pipeline.py` all have in-degree ≥1 from production converter call sites. `_classify_llm_failure` (`worker/errors.py`) has in-degree 3. `REASON_POLICY` auto-derives from `GateSpec`, and `decide_route` reads only `REASON_POLICY`. However, the mechanism has shifted: the wiring is correct for the prior (reason-code coupling) defect, but the broadened mechanism (stale routing after recovery, converter chain-order fragility) represents new structural gaps not covered by this wiring.

**History flag.** Prior zone (GATE_TABLE coupling) was already "regressed" in the POST-FIX-10 delta (Δ+2), continuing to regress with a broadened mechanism.

---

### Worker-Child Process Boundary (was: Cross-Process Error Classification Boundary)
**Status:** improved · **Severity:** critical → high · **Bugs:** 0 · **Proposal:** implemented_and_wired

**What changed.** Core mechanism is unchanged: child-process exception classification collapsing to a default/generic classification, causing wasteful retries or terminal-state mismatches. Severity dropped critical→high because `_TERMINAL_CHILD_REASONS` is now derived from `_CHILD_ERROR_REGISTRY` with a module-level exhaustiveness assertion auto-syncing it, closing the previously-flagged manual-sync risk. Two prior-zone findings (RFC-006 D3 fire-and-forget, RFC-033 D3 write-visibility) are resolved. Remaining findings concern dead-code wiring across process boundaries and the dual outcome channel (arq job result vs. Redis status hash) never being reconciled.

**Key files:** `src/pageindex_mcp/worker/job.py`, `src/pageindex_mcp/worker/errors.py`, `src/pageindex_mcp/worker/subprocess_mgr.py`

**New findings:**
- RFC-034 D18: write-visibility barrier raised `PersistenceNotVisibleError` as `RuntimeError`, propagating to child process as non-terminal (arq retried)
- RFC-036 D1: wrapped in try/except
- The `_CHILD_ERROR_REGISTRY` string-match approach is documented in the code map as ordering-dependent with no shared enum

**Resolved findings:**
- RFC-006 D3: fire-and-forget async Postgres registry delete logged "full cascade succeeded" over silent failure (fixed by RFC-007 D2)
- RFC-033 D3: read-side minio retry insufficient for write-visibility racing (fixed by RFC-034 D18 with write-side head_object verification)
- RFC-032 D3: 3x timeout multiplier guess for scanned PDFs (recalibrated by D9 to 16.5x after measurement showed mean 6.16x, max 11.00x)

**Proposal implementation status.** `process_document_job` (in-degree 9), `reap_stale_jobs` (3), `_run_converter_subprocess` (8), `ConverterChildError` (4), `ConverterOOMError` (1) are all live in the worker pipeline. `_TERMINAL_CHILD_REASONS` in `worker/errors.py` is now a derived `frozenset` from `_CHILD_ERROR_REGISTRY` with a module-level assert enforcing exhaustiveness — auto-syncing, closing the previously-flagged manual-sync risk. No wiring gaps found.

---

### Arabic/RTL Pipeline Blindness (was: Mutable ExtractionState Recovery Path Ordering)
**Status:** regressed · **Severity:** high → high · **Bugs:** +2 · **Proposal:** no_proposal

**What changed.** The prior zone focused on mutable `ExtractionState` ordering: keep-best revert leaving stale fields, bidi re-normalization double-application, and Arabic heading injection cascading content loss. The current zone narrows scope to Arabic/RTL-specific pipeline blindness: Latin-centric assumptions blocking Arabic heading detection, OCR lang detection from filenames missing Arabic scans, table RTL detection flipping on borderline ratios, and content-density gates rejecting legitimate Arabic legal documents. The mechanism evolved from general mutable-state ordering to specific Arabic/RTL pipeline integration failures. Bug count rose 7→9 despite the mechanism narrowing, because new Arabic-specific findings (fence-parity toggle destroying SLA/MOU, content-density rejecting Penal Code, Run 13 collapses) surfaced.

**Key files:** `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/converters/pipeline.py`, `src/pageindex_mcp/helpers/flat.py`, `src/pageindex_mcp/helpers/tree_validation.py`

**New findings:**
- RFC-005 Fix-1: Arabic legal headings rejected by `_segment_label`'s Latin-only gates
- RFC-013 D6: missing non-Latin tessdata
- RFC-028 D1: Arabic heading injection blocked richer flat extraction
- RFC-028 D5: vastly more OCR diluted garble signals (warid-597: 1.8k→54k chars, digit-ratio ~100% to <1%, MARGINAL→PASS)
- RFC-029 D3: fence-parity toggle destroyed SLA (264→0 blocks), MOU (89% loss), qerar-106, marsoom-13
- RFC-029 D1: content-density gate rejected Penal Code (408 chars/node), federal_decree_law (54 chars/node)
- Run 13: MOU collapsed 166→20 nodes, SLA/marsoom-13 went to 0 chars, warid-597 timeout/hang

**Resolved findings:**
- RFC-029 D4: keep-best revert restores result/ok/reason but leaves md_content/tmp_md_path at post-retry data, creating tree-vs-markdown state mismatch
- RFC-021 QF1: deferred OCR changed which path F1 exemption fires under (GHV-TKV-Tarif 4,267-to-375 chars)
- RFC-021 QF1/RFC-022 B2: image-only PDFs produce only image markers as text, tripping >30% token-repetition garble check
- RFC-028 D0: `chunked_docling_timeout` function created but never called by worker.py

**Proposal implementation status.** Neither the current zone (Arabic/RTL Pipeline Blindness) nor the prior zone (Mutable ExtractionState Recovery Path Ordering) has a simplification proposal (has_proposal=false).

**History flag.** Prior zone (Mutable ExtractionState) was the only "improved" zone in the POST-FIX-10 delta (critical→high, Δ-3), but this cycle's Arabic/RTL refocusing has regressed it back up by 2 bugs.

---

### Duplicated Convergent Logic (was: Config Snapshot Freeze Drift and Incomplete Wiring Enforcement)
**Status:** improved · **Severity:** high → medium · **Bugs:** -3 · **Proposal:** not_applicable

**What changed.** The prior zone combined two concerns: (1) config values read from three competing sites diverging during process lifetime, and (2) incomplete wiring enforcement (`validate_feature_wirings` only at atexit, HR2 cascade unreachable). The current zone narrows to just duplicated convergent logic: multiple independent code paths computing the same derived value (flat-block text, garble detection, verdict hysteresis, table-text extraction) with subtly different implementations. The config-drift and wiring-enforcement concerns are largely resolved: `validate_feature_wirings` now runs at startup from both `server.py` and `worker/lifecycle.py` (confirmed by CodeGraph), and `storage/documents.py:delete_doc` IS production-wired via the MCP tool `delete_document` (a deferred local import in `server.py` lines 42–57 — a graph blind spot, not an actual gap). The remaining zone is smaller and lower severity (medium vs. high).

**Key files:** `src/pageindex_mcp/helpers/flat.py`, `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/client/recovery.py`, `src/pageindex_mcp/client/images.py`

**New findings:**
- RFC-015 D3: `_tree_max_leaf_ratio` counting non-leaf wrappers in denominator
- Session memory ISS-36: digit-ratio check duplicated at `helpers.py` lines 534-538 and 1072-1075
- The code map identifies `_flat_block_primary_text`, `_flat_block_text`, `_flat_search_text` as three near-identical reimplementations; verdict-ledger hysteresis is copy-pasted in both persistence methods

**Resolved findings:**
- RFC-027 D7: `chunked_docling_timeout` function created but never imported/called by worker.py
- RFC-029 D0/RFC-030 D5: `_check_bidi_coherence` dead code, never called
- RFC-031 shadow mode to RFC-032 activation: PDF-inspector classification computed and logged but never branched on until wired
- RFC-034 D19: enrichment fix existed as uncommitted git-staged diff through entire audit cycle
- Remote Docling service code predates locally-committed bidi-heading guard, no client-side re-normalization of remote results
- AGPL fallback chain: remote Docling failure silently walks to pymupdf4llm with no hard gate (Hard Rule 4 violation)
- `storage.delete_doc` in_degree=0: HR2 cascade has zero production entrypoints (CLAUDE.md Hard Rule 2 depends on operators knowing to invoke it)

**Proposal implementation status.** Match confidence is low between these zones. The prior zone's proposal (freeze env vars into `PipelineConfig`, wire `validate_feature_wirings` at startup, expose `delete_doc` via MCP tool) has been largely implemented: `validate_feature_wirings` runs at startup (confirmed by CodeGraph), `delete_doc` is production-wired via an MCP tool. The current zone (Duplicated Convergent Logic) is a substantially different concern (code duplication, not config drift) and has no proposal (has_proposal=false). The prior zone's config-drift and wiring-enforcement concerns have been substantially resolved, which is why the successor zone is smaller and lower severity.

## New Zones

- **Registry Dual-Write Consistency** — surfaced this cycle with no prior-zone match.
- **ZDR/PII Egress Gap** — surfaced this cycle with no prior-zone match; bears directly on Hard Rule 3 (PII-bearing documents must route only through a no-training + zero-retention LLM tier) and warrants priority triage given the compliance stakes.

## Closed Zones

- **Picture/OCR Enrichment and Page-Level Escalation Conflation** — no successor zone identified in the current audit; treated as resolved/subsumed.
