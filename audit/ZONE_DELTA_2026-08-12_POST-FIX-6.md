# Zone Delta Analysis — POST-FIX-6

**Current audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-18_POST-FIX-6.md
**Prior audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-17_POST-FIX.md
**Date:** 2026-08-12

## Summary

Across the 5 fix commits landed since the prior audit (`0d9bda1` through `8dcdf50`), 8 zones were tracked (8 prior, 8 current): 4 improved, 2 stalled, 0 regressed. 2 zones closed (Arabic/RTL Pipeline Bolt-On Architecture; God Function Orchestration in `pdf_to_markdown_docling`) and 2 new zones surfaced (Splitter Pattern Fragility and Giant Tail-Blob Recurrence; Silent Fallback Chains Masking Compliance and Quality Failures). Net bug delta is **-5**. All 6 tracked zones with an implemented proposal show `implemented_and_wired` status — no unwired proposals detected in this run — though the two `stalled` zones (Dual Verdict Authority, Recovery Pipeline Implicit Ordering) confirm the underlying structural defect persists even where wiring is sound. The dominant recurring pattern this run is partially-completed remediation: new findings in 3 of 6 zones trace to RFC-028/029 work that was implemented but left gaps (unwired functions, duplicated thresholds, regressions in specific documents) rather than genuinely new defect classes.

## Delta Table

| Zone                                                       | Status   | Severity (prior→current) | Bugs (prior→current) | Proposal Status       | Key Change                                                                                                                                                                                                               |
| ---------------------------------------------------------- | -------- | ------------------------- | --------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Garble Detection Surface Sprawl                            | improved | critical→critical        | Δ-1                  | implemented_and_wired | 8 call sites → 3 layers of indirection; garble_ratio tautology and short-text floor resolved; RFC-028/029 regressions + digit-ratio duplication now open                                                                |
| Dual Verdict Authority (validate_tree vs classify_verdict) | stalled  | critical→critical        | Δ0                   | implemented_and_wired | REASON_POLICY/HARD_FAIL_DEFECTS consolidated away, but dual-engine disagreement and flat-path gate bypass persist; classify_verdict double-call-site risk newly identified                                               |
| Recovery Pipeline Implicit Ordering and State Mutation     | stalled  | critical→critical        | Δ0                   | implemented_and_wired | `_finalize_routing` added, ordering now explicit/documented; 20-field mutable ExtractionState and fragile positional-tuple ExtractionSnapshot persist; new unwired implementations (D0, D6) replace old ones (D7, D11) |
| Picture/OCR Recovery Dual-Path Conflation                  | improved | high→high                | Δ-2                  | implemented_and_wired | `picture_plane.py` / `decide_ocr_mode` unifies both former divergent paths; PictureResult shared-reference bug and landscape timeout resolved; standalone-image chart-content loss newly identified                  |
| Cross-Process Verdict/Registry Write Races                 | improved | high→high                | Δ-1                  | implemented_and_wired | Triple-write consolidated via`_verdict_cas_guard`; write-visibility barrier cut 4.4s/8.8s→0.45s; flat-doc path still triple-writes; converters_cli subprocess boundary newly identified as race surface               |
| Duplicated Threshold/Logic Definitions Across Files        | improved | medium→medium            | Δ-1                  | no_proposal           | Split off from prior env-var-proliferation zone (silent-fallback half moved to new Zone 7); scope narrowed to duplicated constants/functions (digit-ratio floor, pipe-table detectors, heading-injection functions)      |

## Per-Zone Details

### Garble Detection Surface Sprawl (prior: Garble Detection Surface Fragmentation)

**Status:** improved | **Severity:** critical → critical | **Bugs:** -1

The prior audit described 8 call sites with unpropagated `expected_script` and a binary short-text bypass. The current audit narrows this to 3 layers of indirection — short-circuit, blob-kind normalization, and per-prong thresholds — with the `expected_script` self-inference problem persisting but now explicitly identified rather than diffuse.

**New findings:**

- RFC-028 D2 (Arabic presentation-forms prong) caused the Human Rights PDF to regress from FAIL to ERROR
- RFC-029 D3 (fence/HR stripping) caused 89–100% content loss in 5 docs and a PASS-to-MARGINAL flip on Reitlehrer
- ISS-36: digit-ratio garble floor (500 chars) duplicated in two functions with no shared helper
- Observation #5500: wārd-597 numeric-junk persisted as PASS due to zero PUA codepoints
- Observation #5627: two stored PASS verdicts reclassified as FAIL/MARGINAL due to garble patterns with zero PUA codepoints

**Resolved findings:**

- RFC-023 D0: `_text_layer_has_content` garble-unaware, garbled text passed the 20-char check
- RFC-025: `_script_from_filename` returned None for German, `latin_gibberish` gated on non-None, hysteresis threshold issue
- RFC-013 D7 / RFC-015: PUA-only garble detection missed RTL word-splitting and Latin-in-Arabic mojibake
- RFC-033 D1: `garble_ratio` full-text tautology locked ratio to 1.0
- RFC-029 D4: `_repeating_token_density` returned 0.0 for <20 tokens, OCR keep-best never fired

**Key files:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/script.py`

**Proposal status:** implemented_and_wired. `check_garble` (helpers.py) has in-degree 8, called from `_garble_check_nodes`, `_garble_ratio`, `classify_verdict`, and multiple `client.py` recovery methods. `normalize_for_garble` (script.py) feeds into the same surface; `_gate_node_garbling` sits downstream as a hop-2 caller. Confirms a single consolidated garble-detection surface reachable from `index()`.

**History:** This zone has shown 3+ generations of regression (RFC-020 F2 regressed by RFC-021 QF1, RFC-021 QF4 regressed by RFC-023 D3). Past decisions note 8 `GarbleContext` values should collapse to 2-3 named `GarbleProfile` constants — still outstanding.

---

### Dual Verdict Authority (validate_tree vs classify_verdict) (prior: Split Verdict Authority)

**Status:** stalled | **Severity:** critical → critical | **Bugs:** 0

The prior audit described three independent data structures (`GATE_TABLE`, `REASON_POLICY`, `HARD_FAIL_DEFECTS`) requiring manual synchronization at four sites. Current shows `REASON_POLICY` and `HARD_FAIL_DEFECTS` are no longer mentioned, indicating consolidation — but the core dual-engine problem persists: `validate_tree` (10-gate table) and `classify_verdict` (195-line grouped-rule engine with 7+ promotion branches) still independently compute and can disagree. The flat-path bypass of all 10 gates remains.

**New findings:**

- RFC-029 D1 added 4 new `validate_tree` failure reasons never wired into `client.py` recovery — 3 PASS-to-ERROR + 1 FAIL-to-ERROR
- Observation #4127: `classify_verdict` confirmed wrong on 2 documents where stored PASS verdicts were structurally corrupt

**Resolved findings:**

- RFC-023 D4: `cat_b_promoted` had no min text-length check, bare markers promoted to PASS
- RFC-025: hysteresis relaxation let 61%-garbled Haftpflicht flip FAIL→PASS

**Key files:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/client.py`, `src/pageindex_mcp/converters.py`

**Proposal status:** implemented_and_wired. `classify_verdict` (helpers.py:2199-2393) is called only from `_persist_flat_result` and `_persist_tree_result` in client.py, both reachable from `index()`. `validate_tree` has exactly one call site (`_convert_to_tree`). Tree validity is computed once and fed forward into the sole verdict authority rather than each path re-deriving its own verdict — no duplicate/parallel verdict path found.

**History:** Past decisions identify threshold ratcheting (`PASS_MAX_LEAF_RATIO` 0.15→0.30 across 4 RFCs) as symptom management; root cause is Zone 2/3 structural ambiguity. RFC-026 gate hardening: 12 regressions, 0 improvements in one run.

---

### Recovery Pipeline Implicit Ordering and State Mutation (prior: Mutable ExtractionState Recovery Pipeline)

**Status:** stalled | **Severity:** critical → critical | **Bugs:** 0

Prior described a serial-mutation chain with no finalization. Current shows `_finalize_routing` was added (commit `0d9bda1`) and recovery methods now explicitly invalidate `state.rtl_decision` with tagged comments (Zone-6), making ordering explicit/documented rather than implicit. However, the core 7-step waterfall with ~20 mutable `ExtractionState` fields persists. The `ExtractionSnapshot` fragile positional-tuple destructuring is a new structural concern. New unwired implementations (RFC-029 D0, D6) replace prior unwired ones (RFC-023 D7, D11) — same class of defect recurring.

**New findings:**

- RFC-029 D0: `_check_bidi_coherence` implemented with duplicate definitions but never called from any pipeline path
- RFC-029 D4: OCR retry keep-best logic had a short-text floor making the win condition mathematically impossible for no-text-layer PDFs
- RFC-029 D6: judge calibration rules marked complete but never written to file
- `ExtractionSnapshot` uses fragile positional tuple destructuring where `gate_result` appears twice

**Resolved findings:**

- RFC-023 D7: VLM crash `reason='garbling'` not in flat-routing reason check
- RFC-023 D11: OCR escalation only fired on `reason=='garbling'`, missed structural reasons
- RFC-022 B2-A: `content_class` assignment silently overwritten by `route_and_extract_flat`
- RFC-023 D5: synthetic-structure only fired when `flat_structure` completely empty

**Key files:** `src/pageindex_mcp/client.py`, `src/pageindex_mcp/helpers.py`

**Proposal status:** implemented_and_wired. `_finalize_routing` (client.py:966-991) is called from `index()` (in-degree 1) and centralizes route recomputation. Explicit state mutation is visible via `state.rtl_decision=None` reset comments tagged Zone-6 inside `_recover_rtl_repair`, `_recover_vlm_fallback`, and `_recover_image_dominant_ocr`. `index()` has explicit case-matched comments referencing `_finalize_routing` skip on `ok=True`.

**History:** Past decisions note incomplete implementations (RFC-027 D7, RFC-029 D0/D6) discovered only on next audit — a `GateSpec` assertion was proposed but the same class of defect (unwired implementations) recurs in this run.

---

### Picture/OCR Recovery Dual-Path Conflation (prior: Picture Recovery / OCR Enrichment Conflation)

**Status:** improved | **Severity:** high → high | **Bugs:** -2

Prior described three operations conflating into coupled paths with shared config flags and `PictureResult` list multiplication creating shared references. Current shows the god function `_recover_picture_text` (258 lines, complexity 32) persists, but `picture_plane.py` was added with `decide_ocr_mode` (in-degree 2) routing both former divergent paths through one shared decision function. The `PictureResult` shared-reference mutation bug (RFC-020 F4) is resolved. Standalone image path bypassing the enrichment pipeline is a new finding. The landscape rasterize-rotate-reextract timeout (RFC-035 D2) from prior is resolved.

**New findings:**

- OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION: page-level OCR ran twice producing competing results; a shared kill-switch gated both paths; standalone images lose all chart content as literal `<!-- image -->` strings

**Resolved findings:**

- RFC-017 P0a / RFC-020 F0: per-picture OCR splice removed, 5 Arabic scanned PDFs collapsed 60% content loss
- RFC-018 D0 / RFC-017 P0b: page-coverage filters skipped full-page regions even with no text layer
- RFC-019 D0 / RFC-020 F4: `PictureResult` list multiplication created shared dict references
- RFC-024 D1: `_document_level_text_fallback` suppressed picture recovery via false-positive containment
- RFC-035 D2: landscape rasterize-rotate-reextract caused timeout and chart fragmentation

**Key files:** `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/config.py`, `src/pageindex_mcp/client.py`

**Proposal status:** implemented_and_wired. `decide_ocr_mode` (picture_plane.py) has in-degree 2, called from both `converters._recover_picture_results` and `client.CustomPageIndexClient._convert_to_tree` — the two former divergent paths now route through one shared decision function. `config.effective_config_snapshot` (in-degree 13) remains the single config-snapshot surface both paths read from.

**History:** Past decisions document 3 generations of picture/OCR regressions (RFC-019 D0 → RFC-020 F4 → RFC-021 QF1 → RFC-023 D0). Each generation evolved from all-or-nothing guards to graceful degradation to per-region isolation.

---

### Cross-Process Verdict/Registry Write Races (prior: Verdict Persistence Dual-Path Inconsistency)

**Status:** improved | **Severity:** high → high | **Bugs:** -1

Prior described the tree path triple-write (`save_doc`, `write_verdict` re-read+re-write, `save_doc_meta`) and the flat path bypassing `write_verdict` entirely. Current shows the triple-write is consolidated but the flat-doc path still triggers triple-write (`save_flat_doc` + explicit `save_doc_meta` + `_upsert_registry_row`). The write-visibility barrier problem (RFC-034 D18, 4.4s/8.8s worst-case) is resolved (RFC-036 D1 reduced it to 0.45s). New finding: verdict threading through the `converters_cli` subprocess boundary (commit `35bec73`) is now identified as a cross-process race surface. `_verdict_cas_guard` (storage.py) was added as a structural fix.

**New findings:**

- Observation #5669: score-before-write race hit `cabinet_resolution` in Run 16, confirming the same pattern from Run 15
- `promotion_sweep` double-calls `save_doc_meta` (once via `write_verdict` for verdict, once directly for provenance)

**Resolved findings:**

- RFC-034 D18: write-visibility barrier (4.4s/8.8s worst-case) caused Arabic SLA doc 3-5 min latency
- RFC-036 D1: reduced barrier to 0.45s, added `PersistenceNotVisibleError` handling
- RFC-034 D19: density-guarded OCR preservation staged in git, never committed, inactive
- Run 16-19: verdict labels drifted independently of persisted content on identical tree

**Key files:** `src/pageindex_mcp/storage.py`, `src/pageindex_mcp/worker.py`, `src/pageindex_mcp/converters_cli.py`, `src/pageindex_mcp/client.py`

**Proposal status:** implemented_and_wired. `_verdict_cas_guard` (storage.py, in-degree 7) is called from `save_doc_meta`, `save_flat_doc`, and `write_verdict` — production persistence paths. `_upsert_registry_row` (worker.py) is called from `process_document_job` (arq task) and `preprocess_client._process_one`. `_delete_stale_rows` (registry_backfill.py) is called by `reconcile_registry_drift`, itself called by `worker._reconcile_registry_drift_cron`. `converters_cli.main` emits verdict fields to stdout (lines 162-168), consumed by the `worker.py` subprocess-reading path.

**History:** Score-before-write race confirmed hitting the same pattern in Run 15 and Run 16 (two different documents). Past decisions require a single atomic write-point per document.

---

### Duplicated Threshold/Logic Definitions Across Files (prior: Env-Var Flag Proliferation Without Interaction Registry)

**Status:** improved | **Severity:** medium → medium | **Bugs:** -1

Prior focused on 30+ environment variables with cascading effects and no centralized interaction registry, including `VerdictThresholds` never refreshed on env change and `ALLOW_AGPL_FALLBACK` disabling 5 features. Current narrows the zone to duplicated definitions of the same semantic concept across files (`_RFC029_MIN_CHARS_PER_NODE` 150 vs 500, two pipe-table detection functions, three heading-injection functions, `_has_structural_depth` proxy). The env-var proliferation and silent-fallback-masking aspects split into a separate zone (Zone 7, "Silent Fallback Chains Masking Compliance and Quality Failures," is new this run). This zone is the surviving half after the split.

**New findings:**

- ISS-36 root-cause finding: digit-ratio garble check floor (500 chars) duplicated in two functions with no shared helper
- OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION: shared `_OCR_ESCALATION` kill-switch simultaneously gated two independent OCR paths
- `_has_structural_depth` proxy false-negatives meant better source candidates were never selected for validation

**Resolved findings:**

- RFC-032: `PDF_INSPECTOR_PRECLASSIFY` flag defined but dead-ends, classification computed/logged never used
- Dockerfile May 2026: missing `libgl1`+`libglib2.0-0` caused Docling `ImportError`, silent fallback to AGPL pymupdf4llm
- RFC-033 F1 C-2: remote Docling 504s silently fall through to pymupdf4llm with only `logger.warning`
- RFC-024 D0/D10: `PASS_MAX_LEAF_RATIO` widened repeatedly (0.17→0.20→0.30) chasing non-determinism

**Key files:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/client.py`, `src/pageindex_mcp/converters.py`

**Proposal status:** no_proposal. Past decisions classify this as a mechanical, lowest-risk follow-up: promote constants and functions to `helpers.py`, import by `client.py` and `converters.py`. No wiring verification needed since no fix was attempted in this run.

## New Zones

- **Splitter Pattern Fragility and Giant Tail-Blob Recurrence**
- **Silent Fallback Chains Masking Compliance and Quality Failures** (split off from the prior env-var-proliferation zone)

## Closed Zones

- **Arabic/RTL Pipeline Bolt-On Architecture**
- **God Function Orchestration (`pdf_to_markdown_docling`)**
