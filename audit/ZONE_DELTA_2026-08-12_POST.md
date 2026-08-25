---
title: Zone Delta Analysis — POST (2026-08-12)
date: 2026-08-12
type: audit/zone-delta
tags:
  - audit
  - zone-delta
  - architecture
  - post-fix
aliases:
  - POST zone delta
  - 2026-08-12 zone delta
current_audit: "[[ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST]]"
prior_audit: "[[ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-24_POST-FIX-11]]"
scorecard: "[[REMEDIATION_SCORECARD_2026-08-12_POST]]"
net_bug_delta: +4
zones_regressed: 4
zones_improved: 1
zones_new: 2
zones_closed: 3
---

# Zone Delta Analysis — POST

**Current audit:** [[ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST]]
**Prior audit:** [[ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-24_POST-FIX-11]]
**Date:** 2026-08-12

## Summary

The current audit tracks 52 zone-level bugs against 48 in the prior audit, a net delta of +4. Four zones regressed (all escalating in severity), one zone improved, and none stalled. Two zones are new — **Multi-Store Dual-Write Consistency** and **Config Layering Split and Dead-Code Accumulation** — and three prior zones closed out: **Tree-vs-Flat Gate Asymmetry**, **Pre-Tree Text Transforms vs Table/Block Integrity**, and **HR3 PII Egress Gap (Docling + VLM Silent Degradation)**. Every tracked zone reports `implemented_and_wired` proposal status per CodeGraph confirmation, but wiring presence continues to mask correctness gaps — most sharply in **Garble Detection Fragmentation**, which despite resolving five prior recovery-wiring gaps, picked up new calibration instabilities (PASS_MAX_LEAF_RATIO relaxation, RTL vocabulary insufficiency) and a legacy-API usage finding (`had_presentation_forms=False` across 10 production call sites), holding at critical severity. The broader pattern across regressed zones is scope widening rather than root-cause elimination: **OCR Strategy Bifurcation** and **Verdict Promotion / Quality Gate Stack** both escalated from high to critical as their prior narrow framings (filter composition, threshold oscillation) gave way to deeper structural findings (unconditional content replacement, promotion-gate bypass ordering), and **God-Function Orchestration** absorbed the previously narrow Worker/Inspector timeout-race zone wholesale while adding a documented Hard Rule 5 violation (Run 13: empty body-extraction persisted despite the no-silent-persist mandate). The lone improvement, **validate_tree Reason-String Dispatch** (formerly Recovery Routing Wiring Gaps), closed five dead-code/unwired findings via RFC-036/RFC-038 work but still carries two new reason-routing gaps, keeping it at high severity with a net -3 bug delta.

## Delta Table

| Zone | Status | Severity (prior→current) | Bugs (prior→current) | Proposal Status | Key Change |
|---|---|---|---|---|---|
| Garble Detection Fragmentation | regressed | critical→critical | Δ+2 | implemented_and_wired | Mechanism broadened to include oscillation/false-positive cycling (Runs 10-15); recovery-wiring gaps resolved but new calibration + legacy-API findings surfaced |
| OCR Strategy Bifurcation | regressed | high→critical | Δ+1 | implemented_and_wired | Filter-composition framing gave way to document-class-specific destruction; forced-OCR now destroys PictureItems outright, not just filters them |
| Verdict Promotion / Quality Gate Stack | regressed | high→critical | Δ+2 | implemented_and_wired (relocated mechanism) | Threshold-oscillation framing gave way to promotion-stack sequential bypass + CAS upgrade-only lock-in; dual-CAS divergence resolved via RFC-037 SQL-level guards |
| God-Function Orchestration with Duplicated Divergent Logic | regressed | medium→high | Δ+2 | implemented_and_wired | Absorbed prior narrow Worker/Inspector timeout-race zone; timeout mechanism resolved via RFC-038 but broader duplication/content-destruction findings surfaced, incl. Hard Rule 5 violation |
| validate_tree Reason-String Dispatch | improved | high→high | Δ-3 | implemented_and_wired | Five dead-code/unwired recovery gaps closed (RFC-036/RFC-038); reason-string dispatch mechanism persists with two new routing gaps |

## Per-Zone Details

### Garble Detection Fragmentation
*(prior: Garble Detection Prong Blindness — NFKC, Script Threading, Title Inspection)*

**Status:** regressed · **Severity:** critical → critical · **Bug delta:** +2

**Key files:** `src/pageindex_mcp/helpers/garble.py`, `src/pageindex_mcp/helpers/gates.py`, `src/pageindex_mcp/helpers/tree_validation.py`, `src/pageindex_mcp/helpers/verdict.py`, `src/pageindex_mcp/client/indexer.py`

**What changed:** The mechanism broadened from "structurally independent blind spots" (NFKC normalization, script threading, title inspection, signal-never-reaches-action) to a more general "independently-gated prong fragmentation" framing. The current audit newly treats oscillation and false-positive cycling (Runs 10-15) as a first-class mechanism component alongside the prior blind-spot findings. Several prior recovery-wiring gaps are resolved, but new calibration instabilities and a legacy-API usage pattern surfaced, holding the zone at critical severity.

**New findings:**
- RFC-010 D3/D3B: token-repetition guard duplicated in two functions, fixed RFC-013 D7
- RFC-023 D3: image-marker token repetition false positive ('image' at 100% ratio)
- RFC-024: `PASS_MAX_LEAF_RATIO` relaxation let 81/132 garbled nodes PASS with empty `verdict_reason`
- RFC-028 D3: RTL reversal detection vocabulary too small (14 words), zero true-positive on governance docs
- RFC-029 D2: improved OCR language detection paradoxically removed garble-gate safety net on junk text
- Runs 10-13: garble gate oscillated on MOU and ward 597 despite visible garbling
- Run 15: exact garble false-positive from Run 13 reappeared after Run 14 correction
- Observation #5330: 10 production `check_garble` calls use legacy `had_presentation_forms=False`

**Resolved findings:**
- RFC-018 D3b: `node_garbling` reason never recognized by OCR-escalation conditional
- RFC-025 D3: recovery triggers only checked `garbling`, missing `node_garbling`
- RFC-028 D2: Arabic PF garble detection tripped on legitimate text
- RFC-029 D1/D2: four new `validate_tree` failure reasons never wired into recovery
- RFC-030 D2: unhandled reasons caused 3 PASS→ERROR regressions
- Run 11: five independent Arabic-garble instances catalogued, none caught

**Proposal implementation status:** `implemented_and_wired`. CodeGraph confirms `detect_garble` (`src/pageindex_mcp/helpers/garble.py`) is called from `_garble_check_nodes`/`_garble_check_flat_blocks`/`_garble_ratio`, `apply_promotions` (`src/pageindex_mcp/helpers/verdict.py`), `src/pageindex_mcp/converters/pictures.py`, `src/pageindex_mcp/client/indexer.py`, and all `RecoveryMixin` OCR-retry methods. `validate_tree` is called by `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/client/recovery.py`, `mcp_server.py`, `promotion_sweep.py`, `preprocess_client.py`, and `issue/verify_corpus.py`. Gate helpers feed `validate_feature_wirings`, called from `src/pageindex_mcp/server.py` lifespan and `src/pageindex_mcp/worker/lifecycle.py` startup. All production and offline paths confirmed wired. **History note:** this zone has shown oscillation across multiple prior delta reports; `implemented_and_wired` reflects infrastructure presence, not correctness of the detection logic itself.

---

### OCR Strategy Bifurcation
*(prior: Picture Enrichment / OCR Filter Composition)*

**Status:** regressed · **Severity:** high → critical · **Bug delta:** +1

**Key files:** `src/pageindex_mcp/picture_plane.py`, `src/pageindex_mcp/converters/pictures.py`, `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/client/images.py`, `src/pageindex_mcp/helpers/verdict.py`

**What changed:** The mechanism evolved from "independently-tuned filter chain that combines to zero out enrichment" to "document-class-specific filter applied uniformly destroys content for document types where the filtered condition IS the content." Severity escalated from high to critical, reflecting forced-OCR now destroying `PictureItem`s outright (not merely filtering them), the tree path never invoking `splice_figure_markers`, OCR retry unconditionally replacing content without a quality comparison, and language detection derived from near-empty markdown. The prior zone's list-multiplication and five-fix filter-composition findings are resolved, but deeper structural issues surfaced.

**New findings:**
- RFC-017 D1: standalone images never call `splice_figure_markers`, `pic_results` stays empty
- RFC-020: tree path never calls `splice_figure_markers` before `md_to_tree`, markdown becomes nearly empty
- RFC-023 D0: F1 coverage exemption only checks character count without garble detection
- RFC-027 D2/RFC-028 D4: OCR retry unconditionally replaces `md_content`, causing content regression when retry produces fewer chars
- RFC-028 D5: language detection derived from near-empty Docling markdown returned `['eng']` for scanned Arabic PDFs
- RFC-030 D1: `_repeating_token_density` hardcoded 0.0 for text <20 tokens, making OCR retry win condition arithmetically impossible
- Cross-cutting 2026-07-27: standalone image branch bypasses enrichment entirely, confirmed data loss on pie chart numeric labels

**Resolved findings:**
- RFC-019 D0: list multiplication shared references, mutated siblings
- RFC-020 F1/F2/F3/F4/F5: five fixes to the same filter composition
- RFC-021 QF1→RFC-022 B3: GHV-TKV OCR splice regression
- RFC-024 D1→RFC-025 D1: `clip_text` never executed, Human Rights doc 503k→382 chars
- RFC-023 D8a: standalone images create synthetic `PictureResult` with empty `ocr_text`

**Proposal implementation status:** `implemented_and_wired`. CodeGraph confirms `src/pageindex_mcp/picture_plane.py`'s `decide_ocr_strategy` is called by `decide_ocr_mode`, `src/pageindex_mcp/client/indexer.py`'s `_convert_to_tree` and `index`, and `src/pageindex_mcp/converters/pictures.py`'s `_recover_picture_results`. All `src/pageindex_mcp/converters/pictures.py` and `src/pageindex_mcp/client/images.py` functions show production call sites in `src/pageindex_mcp/client/indexer.py`. `TestOcrGatingWiring` test class exists specifically to assert wiring correctness. Fully wired across both live pipeline and offline paths.

---

### Verdict Promotion / Quality Gate Stack
*(prior: Verdict Threshold Oscillation and Dual-CAS Divergence)*

**Status:** regressed · **Severity:** high → critical · **Bug delta:** +2

**Key files:** `src/pageindex_mcp/helpers/verdict.py`, `src/pageindex_mcp/helpers/gates.py`, `src/pageindex_mcp/registry/queries.py`, `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/storage/verdict.py`, `src/pageindex_mcp/storage/documents.py`

**What changed:** The mechanism evolved from "threshold oscillation + dual-CAS divergence" to "promotion-stack sequential bypass + CAS upgrade-only lock-in." The prior zone focused on `PASS_MAX_LEAF_RATIO` widening instability and dual MinIO/Postgres CAS divergence. The current zone identifies a deeper structural issue: `apply_promotions()` rescue paths bypass subsequent gates, and the CAS priority comparison (PASS=3>MARGINAL=2>FAIL=1>ERROR=0) permanently locks in a bad verdict once granted. Dual-CAS divergence findings are resolved — RFC-037's verdict CAS landed as SQL-level guards in `src/pageindex_mcp/registry/queries.py`'s `upsert_doc` with unconditional sidecar merge in `src/pageindex_mcp/storage/verdict.py`, superseding the dual-CAS design. New promotion-gate ordering bugs surfaced in its place. Severity escalated high→critical.

**New findings:**
- RFC-022 B2: QF2a promotion unreachable for `max_leaf_ratio>0.75` — hard-FAIL gate fires before QF2a check
- RFC-022 B1: `structure=[]` produces degenerate metrics (`node_count=0`), blocking all promotion gates
- RFC-023 D4: synthetic structure from 15 flat blocks (210 total chars) passed `node_count>=3`, producing factually wrong PASS
- RFC-025 Run 9: `image_enrichment_promoted` assigned PASS with 38 chars (barcode watermark), less than prior run's 60-char FAIL
- RFC-025: garble detection correctly flagged garbling but no escalation hook — persisted fully-garbled text as MARGINAL
- RFC-029 D1: heading injection gave shallow Arabic trees just enough depth to clear `validate_tree`, blocking richer flat fallback
- RFC-030 D3: `low_content_density` threshold of 500 chars/node calibrated against one doc, over-rejected legitimate legal trees
- RFC-036 D4: `landscape_fallback_picture` `PictureResult`s with `skipped_reason` triggered false `image_enrichment_promoted` verdicts
- Run 14: `low_content_density` gate removal caused federal_decree_law oscillation PASS→MARGINAL→PASS

**Resolved findings:**
- RFC-023 D10: widened `PASS_MAX_LEAF_RATIO` 0.17→0.20
- RFC-024 D0: widened 0.20→0.30, own risk table predicted failure
- RFC-025 D0: hysteresis via prior-verdict anchoring, defeated by corpus reingestion
- RFC-026 D3: GHV-TKV-Tarif flapped PASS→MARGINAL on identical tree after wipe
- Run 8: Doc 8 Reitlehrer remained degraded despite widening
- RFC-034 D18: write-visibility barrier over-provisioned (4.4s blocking delay)
- RFC-036 D1: reduced delays, caught error as warning

**Proposal implementation status:** `implemented_and_wired` (relocated mechanism). CodeGraph confirms `compute_verdict`/`classify_verdict`/`evaluate_gates`/`apply_promotions` (`src/pageindex_mcp/helpers/verdict.py`) and `finalize_gate_and_route` (`src/pageindex_mcp/helpers/types.py`) are called throughout `src/pageindex_mcp/client/indexer.py` and `src/pageindex_mcp/client/recovery.py`, plus offline tools. Several proposals landed via different mechanisms than originally proposed: the verdict CAS guard is SQL-level guards in `src/pageindex_mcp/registry/queries.py`'s `upsert_doc` (not a standalone module), hysteresis was deliberately removed from `src/pageindex_mcp/helpers/verdict`, and `_decomposed_verdict` is confirmed dead code. Tests explicitly assert these relocations (`TestSidecarPassivity`, `TestHysteresisRemoval`, `TestDecomposedVerdictDeadCode`). This is `implemented_and_wired` via relocated mechanism, not a gap.

---

### God-Function Orchestration with Duplicated Divergent Logic
*(prior: Worker/Inspector Dual-Threshold and Timeout Race)*

**Status:** regressed · **Severity:** medium → high · **Bug delta:** +2

**Key files:** `src/pageindex_mcp/worker/subprocess_mgr.py`, `src/pageindex_mcp/worker/job.py`, `src/pageindex_mcp/storage/documents.py`, `src/pageindex_mcp/helpers/flat.py`, `src/pageindex_mcp/helpers/tree_split.py`, `src/pageindex_mcp/helpers/tables.py`, `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/converters/pipeline.py`

**What changed:** The mechanism broadened significantly from "dual-threshold timeout race between worker and inspector" to "god-function orchestration with duplicated divergent logic across multiple subsystems." The prior zone was narrowly scoped to `subprocess_mgr.py` vs `job.py` timeout constants and confidence-threshold divergence. The current zone absorbs that timeout mechanism (partially remediated by RFC-038 via a shared timing-constants module and unified confidence gate) but adds table-separator regex duplication between `src/pageindex_mcp/helpers/tables.py` and `src/pageindex_mcp/helpers/tree_split.py` (causing ~7-row DATA truncation), fence-parity content destruction, a segmentation refactor that broke both orientations at once, and a Hard Rule 5 violation. Severity escalated medium→high reflecting the broader scope.

**New findings:**
- RFC-002 Amendment 1: five `dag.yaml` module-boundary edges didn't match actual imports
- RFC-029 D3: naive fence-parity toggle + unconditional fence/HR-marker stripping destroyed content across corpus
- RFC-035: table-meta/chart-block segmentation refactor broke BOTH landscape AND portrait orientations together
- RFC-036 D0: uncapped landscape rasterize caused serial 300-DPI OCR re-runs; chart axis labels shattered into 71+ singleton kv blocks
- RFC-036 D2: RFC-034 D19 enrichment displacement fix fully implemented and staged in git but never committed
- Run 13: body-extraction silently returned empty and persisted `flat.json` anyway, **violating Hard Rule 5**

**Resolved findings:**
- RFC-032 D3: 3x worker timeout multiplier empirically shown insufficient (range 2.32x-11.00x)
- RFC-032 D9: recalibrated to 16.5x
- Run 8→Run 9: exception-handling patch converted Arabic CMap crash to near-empty artifact

**Proposal implementation status:** `implemented_and_wired`. CodeGraph confirms `src/pageindex_mcp/worker/subprocess_mgr.py`'s `_run_converter_subprocess` is called by `src/pageindex_mcp/worker/job.py`'s `process_document_job` (single call site, not duplicated). RFC-038 commits (visible in git log) specifically targeted timeout/confidence-gate unification — tests `test_early_deadline_persisted_before_subprocess_completes` and `TestReapDynamicTimeout` pass against production code. `src/pageindex_mcp/helpers/flat.py`'s `route_and_extract_flat` is called from `src/pageindex_mcp/client/indexer.py` and `src/pageindex_mcp/client/recovery.py`. `src/pageindex_mcp/helpers/tree_split.py`'s `prepare_tree`/`split_oversized_leaf_nodes`/`_segment_table_nodes`/`table_is_rtl` are called from `src/pageindex_mcp/client/indexer.py` and `src/pageindex_mcp/client/recovery.py`. `src/pageindex_mcp/helpers/tables.py`'s `_flat_parse_table`/`_flat_verbalize_rows` have an explicit wiring test (`TestForwardFillColumnZero.test_wired_into_flat_parse_table`). No evidence of duplicated divergent copies across `src/pageindex_mcp/worker/job.py` vs `src/pageindex_mcp/client/indexer.py` found in this pass, suggesting partial remediation of the "duplicated divergent logic" framing via consolidation into helper modules. Prior zone had no proposal; current zone has one.

---

### validate_tree Reason-String Dispatch
*(prior: Recovery Routing Wiring Gaps — Detection Without Remediation)*

**Status:** improved · **Severity:** high → high · **Bug delta:** -3

**Key files:** `src/pageindex_mcp/helpers/tree_validation.py`, `src/pageindex_mcp/helpers/gates.py`, `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/client/recovery.py`, `src/pageindex_mcp/helpers/types.py`

**What changed:** The core mechanism is unchanged — string/reason-name dispatch from gate detection to recovery routing, where an unhandled or unwired reason falls through to a terminal error. The prior zone's framing as "Detection Without Remediation" (fully implemented detectors inert due to missing wiring) narrowed to the current zone's "Reason-String Dispatch" focus. Several wiring gaps closed: `chunked_docling_timeout_s` wired (RFC-038), `_check_bidi_coherence` dead code resolved (RFC-036 D2 committed staged code), enrichment displacement committed. The fundamental dispatch mechanism (if/elif chain on reason strings) persists, and two new reason-routing gaps appeared. Five resolved findings vs two new findings yields the net -3 bug-count improvement.

**New findings:**
- RFC-036 D3: `rtl_reversal` routed to terminal rejection with no flat-fallback attempt
- Observation #5330: early-exit in `validate_tree` before garble check makes OCR escalation unreachable for docs classified as `node_count<3`

**Resolved findings:**
- RFC-027 task 4.2: `chunked_docling_timeout_s` created but never wired to `worker.py`
- RFC-028 D0: world-stats-pocketbook timed out 3 consecutive runs
- RFC-029 D0: `_check_bidi_coherence` fully implemented, duplicated, never wired (dead code)
- RFC-030 D5: confirmed dead code
- RFC-034 D19: enrichment displacement guard staged, never committed, inactive

**Proposal implementation status:** `implemented_and_wired`. CodeGraph confirms `validate_tree` (`src/pageindex_mcp/helpers/tree_validation.py`) and gate helpers (`src/pageindex_mcp/helpers/gates.py`) feed into `validate_feature_wirings`, called from `src/pageindex_mcp/server.py` lifespan and `src/pageindex_mcp/worker/lifecycle.py` startup. `src/pageindex_mcp/client/recovery.py`'s `RecoveryMixin` methods (`_execute_ocr_retry`, `_recover_vlm_fallback`, `_recover_garble_ocr`, `_recover_low_content_ocr`, `_recover_image_dominant_ocr`) are all called from `src/pageindex_mcp/client/indexer.py`. Neither the prior nor current zone has a formal proposal document; wiring status here reflects infrastructure presence and connectedness, though the dispatch mechanism itself (reason-string matching) remains the root cause of gaps. **History note:** the prior delta (POST-FIX-6) saw Zone 5 remediation replace string-based routing with a Route StrEnum (TREE/FLAT/REJECT/PERSIST_FAIL). The current audit's persistence of string-dispatch issues suggests the StrEnum may not cover all reason-string paths, or new reasons were added after the StrEnum landed.

## New Zones

- **Multi-Store Dual-Write Consistency**
- **Config Layering Split and Dead-Code Accumulation**

No prior-audit lineage exists for these zones in this delta pass; they were not present in the prior audit's zone set and require a first-pass characterization before their next delta comparison.

## Closed Zones

- **Tree-vs-Flat Gate Asymmetry**
- **Pre-Tree Text Transforms vs Table/Block Integrity**
- **HR3 PII Egress Gap (Docling + VLM Silent Degradation)**

These three zones from the prior audit (POST-FIX-11) do not appear in the current audit's zone set, indicating they were either fully remediated and dropped from tracking, or merged into surviving/new zones. Given the presence of new zones **Multi-Store Dual-Write Consistency** and **Config Layering Split and Dead-Code Accumulation**, and the mechanism-broadening pattern observed across every regressed zone in this delta, a merge into a broader-scoped successor zone is plausible for at least one of the three and should be confirmed against the current audit document rather than assumed closed outright.
