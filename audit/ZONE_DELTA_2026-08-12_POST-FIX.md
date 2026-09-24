# Zone Delta Analysis — POST-FIX

**Current audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-17_POST-FIX.md
**Prior audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-11_RUN-2.md
**Date:** 2026-08-12

## Summary

Post-fix reconciliation of the 8 zones touched by the zone-remediation pipeline (commits `35bec73`, `0d9bda1`, `aa55d22`, `d5f816f`, `16183db`) shows across-the-board improvement: 48 total current bugs vs. 64 prior (net delta **-16**), 8/8 tracked zones marked `improved`, 0 regressed, 0 stalled, 0 new zones opened, 0 zones closed. Every zone kept a `critical`/`high` severity floor except the two lowest-priority structural-debt zones (God Function Orchestration, Env-Var Flag Proliferation), which both dropped a full severity tier to `medium`. Six of eight zones now carry an `implemented_and_wired` proposal with verified production call-graph reachability (in-degree counts, named commits, named production callers); the remaining two — **Arabic/RTL Pipeline Bolt-On Architecture** and **God Function Orchestration** — still have no drafted proposal, and **Env-Var Flag Proliferation** has never had one across either run. In every improved zone, the underlying mechanism changed shape rather than merely accumulating new fixes on the old scaffolding — string-based routing became a typed `ExtractionState`, a first-match gate cascade became a table-driven `validate_tree`, a single kill-switch became a `decide_ocr_mode` dispatcher, and 5 verdict writers collapsed to a `write_verdict` + `save_doc_meta` dual-path. That consistently means each zone's *class* of defect was replaced by a narrower successor defect, not eliminated outright — most zones list one or more genuinely new findings (e.g., PictureItem segmentation breakage from `expected_script` threading, four unhandled `validate_tree` failure reasons, page-coverage filter false positives) alongside a larger set of resolved findings.

## Delta Table

| Zone                                                                       | Status   | Severity (prior→current) | Bugs (prior→current) | Proposal Status       | Key Change                                                                                                                                                                                                                    |
| -------------------------------------------------------------------------- | -------- | ------------------------- | --------------------- | --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Garble Detection Surface Fragmentation                                     | improved | critical→critical        | +0                    | implemented_and_wired | 13 scattered call sites consolidated behind`check_garble` (in_degree=54); `expected_script` propagation gap persists but now breaks PictureItem segmentation when threaded                                                |
| Mutable ExtractionState Recovery Pipeline                                  | improved | critical→critical        | -2                    | implemented_and_wired | Bare`reason` string routing token replaced by typed `ExtractionState` + `_finalize_routing` (commit `0d9bda1`); 4 new `validate_tree` failure reasons still lack recovery handlers                                  |
| Split Verdict Authority (validate_tree / REASON_POLICY / classify_verdict) | improved | critical→critical        | -6                    | implemented_and_wired | Dead gate 11 eliminated, dual-derivation divergence resolved via`validate_tree` (commit `16183db`); now a 3-structure manual-sync coordination problem instead of a first-match masking problem                           |
| Picture Recovery / OCR Enrichment Conflation                               | improved | critical→high            | -3                    | implemented_and_wired | Single`_OCR_ESCALATION` kill-switch replaced by `decide_ocr_mode` (picture_plane.py:133) wired through full production chain; D0 dummy-PictureResult fabrication resolved, new page-coverage filter false positives found |
| Verdict Persistence Dual-Path Inconsistency                                | improved | high→high                | -3                    | implemented_and_wired | 5 independent writers consolidated to`write_verdict` (sole authoritative writer, in_degree=6) called before `save_doc_meta`; flat path via direct `save_doc_meta` remains the residual violation                        |
| Arabic/RTL Pipeline Bolt-On Architecture                                   | improved | critical→high            | -3                    | not_implemented       | Six-order-decider problem resolved; residual defect reframed as bolt-on layering across pipeline stages with double bidi reordering; no proposal drafted; heading-order guard still uncommitted/never deployed                |
| God Function Orchestration (pdf_to_markdown_docling)                       | improved | high→medium              | -5                    | no_proposal           | Dual-candidate-pipeline divergence and contradictory landscape detectors resolved (commit`d5f816f`); 333-line monolith remains, 4 lower-priority bugs left as structural debt                                               |
| Env-Var Flag Proliferation Without Interaction Registry                    | improved | high→medium              | -3                    | no_proposal           | `_OCR_ESCALATION` duplicate resolved, flag count ~35→30+; no interaction registry or retirement policy ever proposed in either run                                                                                         |

## Per-Zone Details

### 1. Garble Detection Surface Fragmentation

*(prior: "Six Arabic/RTL order deciders + 10-prong garble gate via 13 differently-shaped call sites")*

**What changed:** The prior zone merged garble detection with Arabic/RTL order decisions into a single zone spanning 13 differently-shaped call sites, 6 order deciders, and a 10-prong gate. The current run splits garble detection out as its own zone (the RTL half is now tracked separately as Zone 6) and consolidates all call sites behind a single `check_garble` function. The mechanism shifted from "scattered call sites with inconsistent shapes" to "consolidated function with context-specific gates still causing interaction bugs." The `expected_script` propagation gap persists but is now a pipeline-level threading problem rather than a per-call-site omission.

**New findings:**

- Threading `expected_script` from filename destroyed PictureItem segmentation
- Reverting the F2 forced-OCR fix
- HTML comment markers triggered false-positive `token_repetition` garble
- `_script_from_filename` returns `None` for German; `latin_gibberish` gated on non-`None`; hysteresis relaxes the threshold
- `garble_ratio` full-text tautology locked ratio to 1.0
- `_repeating_token_density` returns 0.0 for <20 tokens, so OCR keep-best never fires

**Resolved findings:**

- سياسة حوكمة 67% RTL-split leaves, stored PASS (obs #5627/#5639)
- UN Human Rights doc PASS with 97% presentation-form glyphs (obs #4114)
- Federal Decree-Law 13/2022 — fifth distinct doc in the same undetected class (obs #5627)
- `_check_bidi_coherence` 0% detection while `BIDI_COHERENCE_ENFORCE` was promoted to default true on "0 violations = safe"
- Heading-order guard verified locally but never committed or deployed to any Docling image
- ISS-36 duplicated >500-char digit-ratio floor
- Run 18: RTL-gate tightening turned وارد 597 from silent-garble MARGINAL into hard blocking ERROR
- 5+ remediation RFCs (010, 015 D6–D9, 018 D2/D3, 026, 027) never closed the class

**Proposal implementation status:** `implemented_and_wired`. `check_garble` (helpers.py:1374, in_degree=54) is called from `helpers.py` (`_garble_check_nodes`, `_garble_ratio`, `classify_verdict`), `converters.py` (`_text_layer_has_content`, `_document_level_text_fallback`, `_recover_picture_text`), and `client.py` (`_attempt_tesseract_raster_recovery`, `_convert_to_tree`, `_recover_ocr_escalation`, `_persist_flat_result`) — all confirmed production call sites, matching all 4 key files named in the zone proposal. Prior run-2 flagged 3 call sites passing no `expected_script` (converters.py:1746, 1844; helpers.py:1768); the current run still reports `expected_script` propagation issues, but the class has evolved — threading it now breaks PictureItem segmentation, indicating the fix was attempted and produced a new regression rather than being abandoned.

---

### 2. Mutable ExtractionState Recovery Pipeline

*(prior: "reason as both diagnosis and routing command inside the ~1,300-line index()")*

**What changed:** The carrier representation was refactored from a bare `reason` string doubling as diagnosis and routing token into a typed `ExtractionState` object with explicit recovery methods and a `_finalize_routing` reconciliation step. The prior zone's core defect — a single string variable simultaneously serving as diagnosis and routing command with hand-maintained literal string comparisons — has been replaced by structured dispatch. Landed via commit `0d9bda1` ("fix(zone-2): exhaustive route dispatch + _finalize_routing"). The new typed carrier still suffers from serial mutation through 7 recovery methods with order-dependent state, and new `validate_tree` failure reasons still lack corresponding recovery handlers.

**New findings:**

- Four new `validate_tree` failure reasons not handled in recovery paths
- `content_class` silently overwritten by `route_and_extract_flat`
- Synthetic-structure fallback only fires when `flat_structure` is completely empty

**Resolved findings:**

- Run 18 وارد 597 MARGINAL→blocking ERROR; Run 13 FAIL→ERROR
- Run 19 phantom `image_enrichment_promoted` verdict_reason
- Run 19 SLA doc MARGINAL→ERROR (polling window)
- RFC-030 D1 six-variable divergence (still missing the seventh)
- Defeated garble-by-default protection (RFC-025 D2 built `original_reason` for this)
- Three recovery paths rewriting `garbling`/`rtl_reversal` to `node_count<3`
- REASON_POLICY intended-vs-enacted divergence (four PERSIST_FAIL defects; RETRY_RTL hardcoded)

**Proposal implementation status:** `implemented_and_wired`. `ExtractionState` (helpers.py:177) is instantiated in `client.py:CustomPageIndexClient.index` and `converters_cli.main`. Recovery methods (`_recover_rtl_repair`, `_recover_rtl_flat_compare`, `_recover_image_dominant_ocr`, `_recover_flat_prefer`, `_recover_landscape_reroute`, `_recover_vlm_fallback`, `_recover_ocr_escalation`) plus `_finalize_routing`/`_reconvert_and_revalidate` all live on the client and are reachable from `index()`. Matches commit `0d9bda1` already on this branch. The prior run flagged RFC-030 D1 defeating RFC-025 D2 protection by omitting the `original_reason` field; this cascading-defeat pattern appears resolved by the `ExtractionState` refactoring, which replaces the bare `reason`/`original_reason` variable pattern entirely.

---

### 3. Split Verdict Authority (validate_tree / REASON_POLICY / classify_verdict)

*(prior: "Verdict engine: 11-gate first-match cascade + a second engine that re-derives the same signals")*

**What changed:** The prior zone's core mechanism — a first-match-wins cascade masking gate co-occurrence, plus dual-derivation divergence between `validate_tree` and `TreeSignals.from_tree` — has been substantially refactored. Dead gate 11 (`arabic_low_content_ratio`) is no longer reported, and the dual-derivation divergence is absent from current findings. The current zone describes a different structural defect: verdict decision authority split across three data structures (`GATE_TABLE`, `REASON_POLICY`, `HARD_FAIL_DEFECTS`) requiring manual sync at four sites with no programmatic derivation — a successor mechanism where the first-match cascade was replaced by a table-driven approach, but the table approach introduced its own coordination problem. Landed via commit `16183db` ("fix(zone-3): unify offline verdict recomputers — replace _defect_from_reason_str with validate_tree"). Bug count dropped from 12 to 6.

**New findings:**

- `image_enrichment_promoted` bypassed content-volume gates

**Resolved findings:**

- `node_count<3` masking garbling so OCR never escalates (وارد 597; obs #5330)
- RFC-026 D5 had to reorder the cascade itself (obs #5338)
- Run 13: new `low_content_density` gate simultaneously failed three previously-PASS unrelated large docs
- Run 7: D10's 0.17→0.20 widening missed Reitlehrer (0.2571)
- Run 9: RFC-025 hysteresis retune softened four zero-char Arabic docs FAIL/ERROR→MARGINAL with 0 blocks
- Run 8→9: GHV-TKV-Tarif byte-identical tree flipped PASS→MARGINAL from retune alone
- RFC-014 D4 0.15→0.17 promoted سياسة حوكمة to PASS; Run 16 found 67% of its leaves severely RTL-garbled
- RFC-026 D0/D1 shipped 5 self-inflicted test regressions
- RFC-026 char floor checks volume not content validity — وارد 597 still PASS on 3208 chars of barcode noise
- RFC-015 D2 closed half a two-bug pair; 3.5–6× stored/recomputed `max_leaf_ratio` discrepancy unaddressed
- Unreachable gate 11 (`arabic_low_content_ratio`) never once fired, hidden by cascade

**Proposal implementation status:** `implemented_and_wired`. `validate_tree` (helpers.py:1800, in_degree=86) is called from `classify_verdict`. `classify_verdict` (in_degree=144) is called by `promotion_sweep.run_sweep` and `preprocess_client.recompute_verdicts` — both non-test production scripts. `REASON_POLICY` (helpers.py:213) is referenced inside helpers.py at lines 228/229/235. Commit `16183db` replaced `_defect_from_reason_str` with `validate_tree` in offline verdict recomputers. Prior run-2 identified the `TreeDefect` StrEnum as landed-but-unwired (imported only by tests); the current wiring check confirms production callers now exist (`promotion_sweep`, `preprocess_client`), indicating the unwired-fix pattern from the prior run has been corrected.

---

### 4. Picture Recovery / OCR Enrichment Conflation

*(prior: "OCR escalation vs per-picture enrichment: mutually-exclusive subsystems joined by a fragile marker-count contract")*

**What changed:** The prior zone described page-level OCR escalation and per-picture enrichment as structurally incompatible subsystems joined by a destructive shared-mutation marker-count contract (`pop(ocr_text)` on shared dicts, an all-or-nothing ordinal count guard, a single `_OCR_ESCALATION` kill-switch). The current zone describes coupled config flags and containment checks between per-picture OCR, page-level OCR escalation, and image-enrichment verdict promotion — the mutual-exclusion mechanism evolved from a single kill-switch to a `decide_ocr_mode` function in `picture_plane.py`, but coupling between the three operations remains. Severity dropped from critical to high; the D0 dummy-PictureResult fabrication and `pop(ocr_text)` shared-dict-mutation bugs are absent from current findings.

**New findings:**

- Page-coverage filters skipped full-page regions even without a text layer
- Coverage exemption for no-text-layer pages
- `_text_layer_has_content` garble-unaware, false-positive blocked the coverage exemption
- `splice_figure_markers` count-mismatch guard bailed out entirely
- `PictureResult` list multiplication created shared dict references
- `_document_level_text_fallback` suppressed picture recovery
- Landscape rasterize-rotate-reextract caused timeout and fragmentation

**Resolved findings:**

- D0 fabricating N duplicate PictureResults purely to satisfy the all-or-nothing guard
- Per-picture OCR firing a second time during escalation (both OCR passes competing)
- RFC-017 P0a/P0b filters killing legitimate enrichment for docs 3 and 9
- Skips without `skipped_reason` leaving `<!-- image -->` verbatim in prose (Issue 4; RFC-022 B3)
- OCR text moved to `block['ocr_text']`, structurally invisible to `content_class`
- Single `_OCR_ESCALATION` kill-switch gating both mechanisms
- D1 probe: sub-60%-coverage charts re-OCR'd at 300 DPI, garbling text like 2019→20l9
- 4,267→375 char table-doc drop misdiagnosed through three hypotheses (role='table' blocks have no text key)
- RFC-035 regressed BOTH uae_numbers orientations together (RUN-19)
- `MAX_FULLPAGE` cap fires before exemption, so Docling's region enumeration order decides page OCR

**Proposal implementation status:** `implemented_and_wired`. `decide_ocr_mode` (picture_plane.py:133) is called from `converters._recover_picture_results` and `client.CustomPageIndexClient._convert_to_tree`, and transitively from `converters.pdf_to_markdown_docling` and `client.index` — the full production chain. `_recover_picture_text`/`_recover_picture_results` (converters.py, in_degree 45/17) are wired into the same production chain. `config.py` escalation flags gate the call correctly.

---

### 5. Verdict Persistence Dual-Path Inconsistency

*(prior: "Verdict persistence: five writers, lost-update sidecar merge, verdict stored apart from its artifact")*

**What changed:** The prior zone described five independent verdict writers with no shared entry point, all funneling into `save_doc_meta` read-merge-write with no ETag/CAS, plus verdict living only in the sidecar/registry (never in the processed JSON artifact). The current zone describes a designed two-step sequence: `write_verdict` (sole authoritative verdict writer, atomic artifact+sidecar update) called first, then `save_doc_meta` for remaining fields, with `save_doc_meta`'s `_verdict_cas_guard` as a safety net. The five-writer problem has consolidated to a dual-path problem where the "flat path via direct `save_doc_meta`" is the remaining violation. Commits `d5f816f` ("refactor(zone-6): decouple body_for_containment + consolidate candidate selection") and `aa55d22` ("docs(audit): mark Zones 3 + 6 as DONE") indicate this zone was considered remediated. Bug count dropped from 8 to 5.

**New findings:**

- Reduced barrier to 0.45s, added `PersistenceNotVisibleError` handling
- Density-guarded OCR preservation staged but never committed
- Verdict labels drifted independently of persisted content

**Resolved findings:**

- Run 9 harness defaulting 24/24 docs to ERROR while live metas held real verdicts (obs #5467)
- Fabricated corpus report (obs #4093)
- Run 15/16 mis-dispatched figures (harness never fixed)
- Non-fatal dual-write swallow (job reports success with no registry row)
- `run_auto_backfill` complete-flag on zero failures + removed MinIO fallback causing `backfill_incomplete`
- upload/worker/reaper three-writer status hash with no state machine (upload_app.py:167-177, worker.py:446, 640-650)

**Proposal implementation status:** `implemented_and_wired`. `storage.write_verdict` (in_degree=6) is called from `client._persist_tree_result` (line ~1992) BEFORE `save_doc_meta` (line ~2043) — the designed two-step sequence. `save_doc_meta`'s docstring explicitly defers verdict-field authority to `write_verdict` and treats its own `_verdict_cas_guard` as a safety net. `worker.process_document_job` calls `save_doc_meta` only (verdict already computed/written client-side upstream). The prior zone had no proposal at all; this zone now has one that is implemented and wired.

---

### 6. Arabic/RTL Pipeline Bolt-On Architecture

*(prior: "Six Arabic/RTL order deciders + 10-prong garble gate via 13 differently-shaped call sites")*

**What changed:** This is the RTL-focused half of the prior merged zone (the garble-focused half is now Zone 1). The prior zone described six separate Arabic orientation decisions with four sampling strategies and five thresholds, plus bidi repair removing presentation-form signatures that `garble_prongs` keys on. The current zone describes a different structural defect: Arabic/RTL support layered as bolt-on fixes at different pipeline stages operating on different text representations, with bidi reordering running twice with different results and NFKC normalization decomposing signals before detectors see them. Severity dropped from critical to high; the six-order-decider problem and presentation-form suppression issue appear partially resolved, replaced by a stage-coupling and normalization-ordering problem. This half accounts for 6 of the prior zone's 9 combined bugs.

**New findings:**

- `_reversed_morphology` tested presentation-form Unicode names but NFKC decomposed them first
- D3 re-normalization caused MOU MOHRE collapse 134→20 nodes
- Bidi early-return skipped heading repair for bilingual docs
- Heading-order guard uncommitted/never deployed to the remote service
- Arabic OCR language override only partially fixed Latin-in-Arabic mojibake

**Resolved findings:**

- `expected_script` gap flip-flopping open/closed across ≥6 runs for وارد 597 (60k chars Latin gibberish stored PASS)
- سياسة حوكمة 67% RTL-split leaves, stored PASS (obs #5627/#5639)
- UN Human Rights doc PASS with 97% presentation-form glyphs (obs #4114)
- Federal Decree-Law 13/2022 — fifth distinct doc in the same undetected class (obs #5627)
- `_check_bidi_coherence` 0% detection while `BIDI_COHERENCE_ENFORCE` was promoted to default true on "0 violations = safe"
- ISS-36 duplicated >500-char digit-ratio floor
- Run 18: RTL-gate tightening turned وارد 597 from silent-garble MARGINAL into hard blocking ERROR
- 5+ remediation RFCs (010, 015 D6–D9, 018 D2/D3, 026, 027) never closed the class

**Proposal implementation status:** `not_implemented`. Current zone has `has_proposal=false`. No specific proposal exists for the bolt-on architecture problem. The underlying RTL code paths are wired into production (bidi reordering, NFKC normalization, heading repair all execute in the pipeline), but no structural consolidation proposal has been drafted or implemented to address the bolt-on layering. Note: the heading-order guard is flagged as "uncommitted/never deployed to remote service" — a specific instance of the same unwired-fix pattern previously seen in Zone 1's `TreeDefect` StrEnum.

---

### 7. God Function Orchestration (pdf_to_markdown_docling)

*(prior: "pdf_to_markdown_docling: dual candidate pipelines, stage ordering encoded as line positions")*

**What changed:** The prior zone described dual candidate pipelines over two snapshots with a divergent copy of the real gate as selector, repair operations running twice with different heading-page maps, two landscape detectors with contradictory predicates, and a chunked route returning empty `extraction_stages`. The current zone describes a 333-line monolithic function (similar size to the prior 330 lines) but with the dual-candidate-pipeline coupling and contradictory landscape detectors absent from findings. Commit `d5f816f` ("refactor(zone-6): decouple body_for_containment + consolidate candidate selection") appears to have addressed the candidate-selection divergence. Severity dropped from high to medium; bug count dropped from 9 to 4. The prior zone had a proposal; the current zone does not, suggesting the prior proposal was executed and the remaining issues are lower-priority structural debt.

**New findings:**

- Added depth/node-count guard to fix D11

**Resolved findings:**

- Run 14: RFC-029 D3 over-stripped Reitlehrer 32% while verdict improved to PASS (masked by PASS)
- Run 18: cabinet_resolution_no_21 PASS→MARGINAL via flattened table headers (commit c62ef80)
- Run 18: Federal Decree-Law No.47 MARGINAL→FAIL, 88% body-less heading fragments
- Run 19: RFC-035 regressed landscape AND portrait uae_numbers together
- RFC-026 D2 rotation only in docling route (obs #5352, #5477)
- Two landscape detectors with contradictory predicates (`rotate%180!=0 or w>h` vs `rotate==0 and w>h`)
- Chunked route returns empty `extraction_stages` — no provenance for oversized PDFs
- Landscape probe reads the ORIGINAL pdf while char counts come from a rotation-normalized temp copy

**Proposal implementation status:** `no_proposal`. Current zone has `has_proposal=false`. Prior zone had a proposal that appears to have been partially executed (commit `d5f816f` consolidated candidate selection). The remaining 4 bugs are structural debt from the monolithic function pattern rather than specific wiring gaps. No new proposal has been drafted for the remaining issues.

---

### 8. Env-Var Flag Proliferation Without Interaction Registry

*(prior: "Flag and threshold sprawl: ~35 never-retired kill-switches with divergent binding times")*

**What changed:** The mechanism is essentially unchanged — a large uncoordinated set of env-var flags checked at multiple binding times/locations with no centralized interaction documentation. The prior zone quantified 57+30+22+14 env reads across 4 files; the current zone describes "30+ environment variables" with the same structural problem. `ALLOW_AGPL_FALLBACK` is named as a cross-cutting gate in both. The flag count may have decreased slightly (~35 to 30+) but the fundamental pattern — no interaction registry, no retirement policy, cache-staleness — persists. Severity dropped from high to medium; bug count dropped from 7 to 4. The `PDF_INSPECTOR_PRECLASSIFY` dead-end finding persists across both runs.

**New findings:**

- Missing `libgl1`+`libglib2.0-0` caused silent Docling ImportError fallback
- Remote Docling 504s silently fall through to pymupdf4llm

**Resolved findings:**

- Dead D3a probe (only source of `pdf_page_count` when `ALLOW_AGPL_FALLBACK` disabled)
- `_OCR_ESCALATION` duplicate with stale "Mirrors client.py:66" comment
- Run-13/Run-9 single-doc-calibrated corpus-wide thresholds
- Untestable 6-flag recovery-ladder combinatorics
- `effective_config_snapshot` omitting `PDF_CONVERTER`, `FLAT_DOC_ROUTING`, `TREE_PATH_PICTURE_SPLICE_ENABLED`, `RFC029_*`, `LOW_CONTENT_OCR_CHAR_FLOOR`, `BIDI_COHERENCE_ENFORCE`

**Proposal implementation status:** `no_proposal`. Neither the prior nor the current zone has a proposal. No structural remediation has been drafted or implemented for the flag-proliferation pattern. The `_OCR_ESCALATION` duplicate was resolved and some flags may have been retired, but no centralized interaction registry or retirement policy exists. Prior remediation pipeline estimated Z6/Z7 proposals as pending due to RFC-chains miner stalling.

## New Zones

None. `new_count: 0`.

## Closed Zones

None. `closed_count: 0`.
