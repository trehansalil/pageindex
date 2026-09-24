# Architecture Defect Zones Audit — 2026-08-17 POST-FIX-4

**Date:** 2026-08-17
**Sources:** 7 history miners, 3 code maps

## Summary Table

| # | Zone | Severity | Bug Count | Key Files |
|---|---|---|---|---|
| 1 | Garble Detection Hydra | critical | 12 | `helpers.py`, `converters.py`, `client.py` |
| 2 | God Function Routing Cascade (client.py index()) | critical | 11 | `client.py`, `helpers.py` |
| 3 | Verdict Persistence Split-Brain | high | 7 | `storage.py`, `client.py`, `promotion_sweep.py`, `preprocess_client.py`, `worker.py` |
| 4 | Threshold Calibration Feedback Loops | high | 8 | `helpers.py`, `config.py`, `client.py` |
| 5 | OCR/Enrichment Signal Conflation | high | 9 | `converters.py`, `client.py`, `config.py`, `helpers.py` |
| 6 | Conversion Pipeline Stage Coupling (pdf_to_markdown_docling) | high | 7 | `converters.py`, `helpers.py` |
| 7 | Registry/Persistence Consistency Gaps | medium | 6 | `storage.py`, `worker.py`, `registry_backfill.py` |
| 8 | Dead/Uncommitted/Stale Code Divergence | medium | 6 | `helpers.py`, `config.py`, `promotion_sweep.py` |

## Zone Details

### Zone 1: Garble Detection Hydra
**Severity:** critical | **Bug count:** 12

#### Mechanism
Five parallel garble evaluations on different text shapes means a single document can pass one check and fail another depending on which path it traverses. Fixing a threshold or heuristic in `_tree_is_garbled` does not propagate to `_flat_text_is_garbled` or the page-level `_text_layer_has_content` check. The flat-path garble gate operates on raw markdown INCLUDING formatting characters (pipes, headers, whitespace) that dilute ratios below heuristic floors, so a document flagged garbled on the tree path can pass on the flat path or vice versa. The per-node garble check (`_gate_node_garbling`) uses text-inferred expected_script that can override the filename-derived script per-node, producing inconsistent garble signals within a single tree. Meanwhile `classify_verdict` passes `expected_script=None` on the flat-doc path (when `validate_result` is None), losing script context entirely.

#### History
a. RFC-028 D2 presentation-forms detection had no treatment path, causing Human-Rights ERROR regression (Run 12).
b. RFC-029 D0 bidi-coherence detector was dead code, then wired in RFC-030 D5, then found to be a null detector in RFC-033/034 (0% TPR).
c. RFC-033 D1 `_garble_ratio` full-text tautology caused SLA false-positive garbling ratio=1.00 (Run 14-15).
d. Discovery #5331 expected_script=None for German docs let Haftpflicht (61% garbled) pass FAIL-to-PASS undetected (Run 8-9).
e. ISS-36 duplicated digit-ratio floor guards in `_tree_is_garbled` and `_flat_text_is_garbled`.
f. Cross-cutting Issue 3 shows valid-looking Latin gibberish from Arabic OCR bypasses every check for MOU, qarar-106, warid-597.

#### Code Evidence
helpers.py:1474-1480 `_tree_is_garbled` (bulk flattened text). helpers.py:1529-1545 `_gate_node_garbling` (per-node, calls `_garble_check_nodes` with `_infer_script` override). helpers.py:3228-3245 `_flat_text_is_garbled` (raw markdown, short-text garble-by-default rule). converters.py:1635-1653 `_text_layer_has_content` (page-level text, calls `_is_garbled_blob`). converters.py:1698-1762 `_document_level_text_fallback` (whole-document pdfium text, calls `_is_garbled_blob`). converters.py:2147 region-level garble check inside `_recover_picture_text`. helpers.py:2029 `classify_verdict` computes `TreeSignals.from_tree` with `expected_script=None` when `validate_result` is None (flat path).

#### Key Files
- src/pageindex_mcp/helpers.py
- src/pageindex_mcp/converters.py
- src/pageindex_mcp/client.py

#### Simplification Proposal
Replace the 5+ independent garble evaluation sites with a single `GarbleVerdict check_garble(text, expected_script, context)` function that is the sole entry point for all garble decisions. This function accepts a `GarbleContext` enum (TREE_BULK, NODE, FLAT_MARKDOWN, PAGE_TEXT_LAYER, DOCUMENT_FALLBACK, REGION, RETRY_COMPARISON) that controls the only behavioral variations that are actually needed: the short-text default rule (FLAT_MARKDOWN only) and the markdown-formatting dilution strip (FLAT_MARKDOWN only). The function always strips markdown formatting before ratio computation, always runs both `_is_garbled_blob` and `_has_sparse_mojibake` (currently some paths omit the latter), and always requires an explicit `expected_script` (no silent None fallback).

**Restructuring steps:**
1. Introduce `GarbleContext` enum and `check_garble()` in helpers.py (~+40 lines) — strips markdown for FLAT_MARKDOWN, applies short-text-garble-by-default only for FLAT_MARKDOWN, always runs `_is_garbled_blob(...) or _has_sparse_mojibake(...)`, requires explicit `expected_script`.
2. Delete `_tree_is_garbled`, `_flat_text_is_garbled`, consolidate into `check_garble()` (~-35 lines helpers.py). Update `TreeSignals.from_tree` (helpers.py:350) and `_garble_ratio` (helpers.py:1861).
3. Consolidate `_gate_node_garbling` to use `check_garble` per-node (~-10 lines) — `_garble_check_nodes` (helpers.py:1423) routes its inner call through `check_garble(text, node_script, GarbleContext.NODE)`.
4. Consolidate converters.py callsites (~-15 lines): `_text_layer_has_content` (1635), `_document_level_text_fallback` (1750), region-level check (2150) — all three currently call only `_is_garbled_blob` without `_has_sparse_mojibake`, so this fixes a silent omission.
5. Consolidate client.py callsites (~-5 lines): Tesseract fallback (443), pre-conversion probe (980), retry comparison (1402/1406/1418), image-enrichment promotion (helpers.py:2105), flat-path garble gate (1855/1883).
6. Audit `classify_verdict` callers to confirm `expected_script` is threaded through end-to-end (caller-side fix, not a `check_garble` fix).

Net line-count delta: ~+40 minus ~65 = **-25 lines net**. File targets: helpers.py (steps 1-3, 5 partially), converters.py (step 4), client.py (step 5).

**Historical bug classes prevented:** RFC-033 D1 tautology (one function can't diverge from itself); RFC-028 D2 no-treatment-path (new prong fires at all 7 callsites); RFC-033/034 null bidi detector (encoding-range mismatch caught once); Discovery #5331 expected_script=None (explicit-required-keyword design); ISS-36 duplicated floor guards; Cross-cutting Issue 3 Latin-gibberish bypass (uniform `_has_sparse_mojibake` coverage); flat-path ratio dilution (markdown-stripping in FLAT_MARKDOWN context).

**Migration risk:** Medium-low. Mechanical restructuring (all paths already call `_is_garbled_blob` at their core), but two behavioral changes (adding `_has_sparse_mojibake` to converters.py callsites, stripping markdown for FLAT_MARKDOWN) could change verdicts for edge-case documents.

**Sequencing:** Wave 1 (safe, thin wrapper dispatching to existing functions, zero verdict changes) → Wave 2 (swap callers one at a time, corpus score-diff after each, helpers.py internal callers first, then converters.py, then client.py) → Wave 3 (delete dead code: `_tree_is_garbled`, `_flat_text_is_garbled`, unused `_is_garbled_blob` imports) → Wave 4 (behavioral fixes: `_has_sparse_mojibake` addition and markdown-stripping, each behind a feature flag, corpus score-diff after each). Waves 1-3 are strictly refactoring; only Wave 4 changes verdicts.

**Estimated effort:** Wave 1: 0.5d, Wave 2: 1d, Wave 3: 0.25d, Wave 4: 1d. **Total: ~2.75 days**, with a clean checkpoint after Wave 3.

---

### Zone 2: God Function Routing Cascade (client.py index())
**Severity:** critical | **Bug count:** 11

#### Mechanism
Recovery branches are ordered sequentially within one function, so the first matching branch wins and later branches never fire. Gate check ordering in `validate_tree` (garbling > node_count_low > depth_low > node_garbling) means image-only/scanned PDFs fail on node_count<3 before garbling is evaluated, so the OCR escalation path (gated on `reason==GARBLING`) never triggers for the documents that need it most. Each recovery branch partially overwrites shared mutable state (`result`, `ok`, `reason`, `gate_result`, `md_content`, `pic_results`) but may miss some variables — RFC-030 D1 showed the revert path restoring tree state but not `md_content`/`tmp_md_path`/`pic_results`, creating a state mismatch. Adding a new recovery branch (RFC-029's 4 new `validate_tree` reasons) without wiring the corresponding client.py if/elif fell through to `LowQualityTreeError` (terminal ERROR) instead of the intended FAIL-with-artifact.

#### History
a. RFC-029 D0/D1/D2/D8 added 4 new validate_tree failure reasons mapped to FAIL in classify_verdict but none wired in client.py routing — fell through to raise LowQualityTreeError (RFC-030 D2, Run 13).
b. RFC-030 D1 revert path restored tree state but not md_content/tmp_md_path/pic_results (Run 13).
c. RFC-005 Fix-3 OCR escalation gated on reason==garbling never fired for image-only PDFs failing node_count<3 (Discovery #3093, #3082).
d. RFC-029 D1 content-density gate (500 chars/node) rejected Penal Code family until RFC-030 D3 lowered to 150 (Run 12-13-14).
e. RFC-029 D3 fence-stripping naive parity toggle caused SLA 264->0 blocks, MOU 89% loss (RFC-030 D0, Run 13).

#### Code Evidence
client.py:840-2249 `index()` method (1409 lines, noqa: C901, PLR0915). client.py:1286-1293 OCR escalation gated on `first_defect in (GARBLING, NODE_GARBLING)`. client.py:1262-1268 `first_defect` and `route` computed from `gate_result`. client.py:2092-2114 Zone-5 terminal reject / RFC-030 D2 PERSIST_FAIL fallthrough. helpers.py:1684-1695 GATE_TABLE ordering (garbling first, node_count_low second). client.py:1398-1418 D4 keep-best revert path (shared mutable locals).

#### Key Files
- src/pageindex_mcp/client.py
- src/pageindex_mcp/helpers.py

#### Simplification Proposal
Replace the monolithic `index()` function's sequential if/elif recovery cascade with a **recovery-step pipeline**: each recovery strategy (OCR escalation, RTL repair, VLM fallback, image-dominant OCR, flat-prefer override, landscape re-routing) becomes a standalone `async` method taking and returning an immutable `ExtractionState` dataclass (extending the existing `ExtractionSnapshot` pattern). The main `index()` calls these methods in a declared list; each method's guard clause determines whether it fires — eliminating the shared-mutable-locals bug class entirely. The conversion front-end (~300 lines) becomes `_convert_to_tree()`; the persistence tail (~130 lines) becomes `_persist_tree_result()`. This reduces `index()` to roughly 120 lines of orchestration.

**Restructuring steps:**
- **A** — Extract `ExtractionState` dataclass (helpers.py, +30 lines): carries `result`, `ok`, `reason`, `gate_result`, `original_gate_result`, `first_defect`, `route`, `md_content`, `tmp_md_path`, `pic_results`, `used_converter`, `total_chars`, `extraction_stages_captured`, `_flat_garble_unrecovered`. Immutable per step via `dataclasses.replace`.
- **B** — Extract `_convert_to_tree()` (client.py, net ~-10 lines): PDF converter chain, .md/.txt, .docx/.pptx, .xlsx, image, .html dispatch, plus `split_oversized_leaf_nodes`/`_segment_table_nodes` and first `validate_tree`.
- **C** — Extract each recovery branch into its own method (net ~-80 lines from deduplication): `_recover_ocr_escalation` (~200 lines, absorbs keep-best/revert), `_recover_rtl_repair` (~30 lines), `_recover_rtl_flat_compare` (~30 lines), `_recover_vlm_fallback` (~85 lines), `_recover_image_dominant_ocr` (~85 lines), `_recover_flat_prefer`/`_recover_landscape_reroute` (~50 lines). Extract the duplicated reconvert+revalidate pattern (present in 4 of 6 branches) as `_reconvert_and_revalidate()` (~25 lines, replaces ~100 lines of duplication).
- **D** — Extract `_persist_tree_result()` and `_persist_flat_result()` (net ~-20 lines).
- **E** — Rewrite `index()` as ~120-line orchestrator: convert → loop recovery pipeline → route to persist.

Net line-count delta: **-150 to -200 lines** in client.py; helpers.py grows ~50 lines.

**Historical bug classes prevented:** RFC-030 D1 partial-state revert (impossible — the whole state dataclass moves together); RFC-029 D0/D1/D2/D8 unwired defects (new defects flow through `decide_route()` policy mapping, never fall through unhandled); RFC-005 Fix-3 OCR-gated-on-garbling-only (independent guard clauses let both OCR and image-dominant recovery fire on the same state); RFC-029 D3 fence-stripping blast radius contained to its own guard; RFC-030 D2 fallthrough (orchestrator's explicit route switch has no unhandled case).

**Migration risk:** Moderate — sole ingestion entry point, 238+ tests. Sequence: Wave 0 (zero-behavior-change: add `ExtractionState` + `_reconvert_and_revalidate` alongside existing code, add corpus regression snapshot) → Wave 1 (extract `_convert_to_tree`, destructure back into locals, no behavior change) → Wave 2 (extract recovery methods one at a time, start with simplest `_recover_rtl_repair`, corpus+unit suite after each) → Wave 3 (extract persistence methods, thread ExtractionState) → Wave 4 (remove local-variable destructuring, delete `noqa: C901, PLR0915`). Key risk: the flat persistence branch's early-return semantics and the nested Tesseract/VLM sub-recovery inside the flat garble gate must stay in `_persist_flat_result` rather than the recovery pipeline, since it operates on flat_md not tree state.

**Estimated effort:** 3-4 developer-days total (Wave 0-1: 0.5d, Wave 2: 1.5d, Wave 3-4: 1d, buffer: 0.5d). Each wave independently shippable and revertible.

---

### Zone 3: Verdict Persistence Split-Brain
**Severity:** high | **Bug count:** 7

#### Mechanism
The asymmetry between tree and flat verdict persistence means flat doc artifacts lack the artifact-level verdict injection that `write_verdict` provides, creating different verdict provenance for structurally identical decisions. The two offline verdict recomputers can produce different verdicts for the same document because `promotion_sweep` uses stale stored defect strings parsed back into enums while `recompute_verdicts` runs current gate logic. The `_verdict_cas_guard` (storage.py:515) prevents verdict fields from being overwritten by a staler timestamp, but non-verdict fields (all provenance metadata) have no such guard, so concurrent writers can interleave field updates. The worker parent's `_upsert_registry_row` (worker.py:674) must re-read the just-persisted MinIO artifact across a process boundary (child subprocess wrote, parent reads) with no consistency guarantee.

#### History
a. RFC-034 D18 write-visibility barrier added up to 8.8s delay pushing job completion past scorer's polling window, causing false ERROR/MARGINAL for correctly-persisted docs: Arabic SLA (RFC-036 D1, Run 15), cabinet_resolution_no_96 (Run 16), arabicSLA (Run 19).
b. RFC-034 D19 image enrichment density guard fully implemented but staged and never committed, leaving defect active (Run 36).
c. RFC-030 D6 judge calibration rules never written to skill file, causing phantom verdict regressions (Haftpflicht PASS->MARGINAL, image pie chart MARGINAL->FAIL, Run 13).
d. Run 9 scoring harness defaulted all 24 docs to ERROR with null metrics while live re-pull refuted the data.

#### Code Evidence
client.py:2148-2165 `save_doc` with verdict fields (tree path). client.py:2170-2178 `write_verdict` call (tree path only). client.py:2060 `save_flat_doc` (flat path, no write_verdict). storage.py:515-536 `_verdict_cas_guard` soft CAS. storage.py:653-731 `write_verdict` dual-write (artifact + sidecar). worker.py:674-701 `_upsert_registry_row` (parent-side, re-reads MinIO). promotion_sweep.py:94-101 reconstruct TreeGateResult from stored string (no validate_tree). preprocess_client.py:221+ `recompute_verdicts` (re-runs validate_tree).

#### Key Files
- src/pageindex_mcp/storage.py
- src/pageindex_mcp/client.py
- promotion_sweep.py
- preprocess_client.py
- src/pageindex_mcp/worker.py

#### Simplification Proposal
Eliminate the tree path's redundant `write_verdict` call during live ingest: `save_doc` already embeds verdict in the artifact, and `save_doc_meta` can carry verdict fields in the same call that writes provenance — exactly as the flat path already does via `save_flat_doc`'s internal `save_doc_meta`. This collapses tree ingest from three sequential MinIO write operations (`save_doc`, `write_verdict`, `save_doc_meta`) to two, matching the flat path's shape. The two divergent offline recomputers merge into a single recompute function that always re-runs `validate_tree` and routes through `write_verdict` as the sole offline-mutation entry point.

**Restructuring steps:**
- **A** — Unify tree ingest persistence: delete the `write_verdict` call from tree ingest (client.py ~2167-2178, ~-15 lines); add verdict fields to the `meta` dict passed to `save_doc_meta` (~+6 lines). Delta: -9 lines.
- **B** — Atomic sidecar write during ingest: in `save_doc_meta`, when the incoming `meta` dict contains all required fields (detected via presence of `verdict_computed_at`), skip read-merge-write and PUT the complete sidecar directly. Read-merge-write survives only for partial-update callers (offline recomputers via `write_verdict`). Delta: +10 lines.
- **C** — Merge offline recomputers: extract `recompute_verdicts` from preprocess_client.py into a shared module (e.g. `verdict_recompute.py`) imported by both `promotion_sweep` and `preprocess_client`. Replace `_defect_from_reason_str` reconstruction in promotion_sweep.py:94-101 with an actual `validate_tree` call on stored structure; both callers then route through `write_verdict` for mutation. Delta: -20 lines net.
- **D** — Registry read-after-write barrier: pass already-known verdict fields directly from the job result dict in `_upsert_registry_row` (worker.py:674) rather than re-reading the MinIO artifact — requires expanding the child->parent job result to carry sha256/verdict/doc_name. Delta: +5 lines.

Total estimated delta: **-14 lines net**.

**Historical bug classes prevented:** RFC-034 D18 write-visibility barrier (eliminating write_verdict from ingest removes the re-read window entirely — Arabic SLA, cabinet_resolution_no_96, arabicSLA false verdicts would not occur); Run 9 scoring ERROR defaults (Step D passes fields directly, no cross-process re-read); RFC-030 D6 phantom regressions (merged recomputer always re-runs validate_tree, no stale-string reconstruction); RFC-034 D19 staged-but-uncommitted guard (fewer write windows reduce interleaving surface).

**Migration risk:** Medium-low, touches the persistence hot path but each step independently deployable. Sequence: Step C first (offline-only, zero live-ingest risk, validate by diffing merged recomputer against full corpus) → Step A (most impactful, verify sidecar carries correct verdict via single save_doc_meta call before removing write_verdict) → Step B (builds on A, "complete field set" detection keeps incomplete callers on read-merge-write) → Step D (isolated to worker.py, verify registry rows before/after batch ingest). Each step is a single revertible commit. Key risk: Step A removes write_verdict's re-read safety net, but `_confirm_write_visible` already runs inside `save_doc` (storage.py:220), preserving the visibility check.

**Estimated effort:** Step A: 0.5d, Step B: 0.5d, Step C: 1d, Step D: 0.5d. **Total: 2.5 days.**

---

### Zone 4: Threshold Calibration Feedback Loops
**Severity:** high | **Bug count:** 8

#### Mechanism
A threshold tuned to fix one document's failure mode rejects other documents that were previously borderline-passing. The fix for the new regressions (lowering the threshold) then re-admits the original problematic document or creates new borderline cases. Hysteresis widens the effective threshold for previously-PASS docs, so a document that was PASS can stay PASS even if its leaf concentration worsens — but a fresh evaluation (new doc_id, no prior_verdict) of byte-identical content would score MARGINAL. Table segmentation and leaf splitting both run pre-`validate_tree` and change the exact metrics the gates measure: splitting an oversized leaf increases node_count and depth (potentially clearing NODE_COUNT_LOW and DEPTH_LOW gates), while table segmentation creates children that increase node_count and decrease chars_per_node (potentially triggering the LOW_CONTENT_DENSITY gate for high-node-count documents).

#### History
a. RFC-029 D1 500 chars/node floor (tuned to marsoom-13 at ~200) rejected Penal Code (408.2), federal_decree_law_no_33 (54.3), marsoom-33 (459.4) — all PASS in Run 12, ERROR in Run 13, recovered PASS in Run 14 after RFC-030 D3 lowered to 150.
b. RFC-023 D10 PASS_MAX_LEAF_RATIO 0.17->0.20 broke 3 tests (test_qf2b_ratio_018_marginal, test_classify_verdict_marginal_on_borderline_ratio, test_classify_verdict_category_a_not_promoted_when_ratio_high).
c. RFC-025 hysteresis let Haftpflicht flip FAIL->PASS on identical 132-node/56,610-char tree (Run 8-9).
d. RFC-035 D2 landscape chart-splitting produced 71 singleton kv blocks in both orientations (Run 18-19).

#### Code Evidence
helpers.py:2115-2117 hysteresis band widening (prior_verdict==PASS). helpers.py:286-293 `VerdictThresholds.pass_max_leaf_ratio` (0.30) + `hysteresis_band` (0.10). helpers.py:1632-1652 `_gate_low_content_density` (node_count >= 200 threshold). helpers.py:2670-2794 `split_oversized_leaf_nodes` (runs pre-validate_tree, changes node_count/depth). helpers.py:2797-2999 `_segment_table_nodes` (runs pre-validate_tree, changes node_count). config.py:16 `CATEGORY_BC_PROMOTION_THRESHOLD` hardcoded 0.17 (not env-tunable). helpers.py:2660 comment explicitly warns about coupling LEAF_SPLIT_RATIO and scoring thresholds.

#### Key Files
- src/pageindex_mcp/helpers.py
- src/pageindex_mcp/config.py
- src/pageindex_mcp/client.py

#### Simplification Proposal
Freeze the tree shape before measuring it: compute TreeSignals once on the post-split/post-segment structure and make that the single immutable input to both `validate_tree` and `classify_verdict`, eliminating the feedback path where mutation thresholds determine the scoring outcome. Delete the hysteresis mechanism entirely — a document's verdict should depend only on its current tree, never on its history.

**Restructuring steps:**
1. Delete hysteresis (helpers.py `classify_verdict`): remove `prior_verdict` parameter, delete lines 2115-2117 (`_effective_max_leaf` widening), remove `hysteresis_band` from `VerdictThresholds` (276, 292), remove `find_prior_verdict` calls from client.py (2009-2010, 2131). Delta: -25 lines helpers.py, -10 lines client.py.
2. Extract tree-transform phase into a single entry point `prepare_tree(structure)` calling `split_oversized_leaf_nodes` then `_segment_table_nodes`, replacing 5 duplicated call-pairs in client.py. Delta: +8 lines helpers.py, -20 lines client.py (net -12).
3. Decouple LOW_CONTENT_DENSITY gate from post-segmentation node_count: compute chars_per_node from `total_chars / original_node_count` (pre-transform), or drop the node-count-based density gate in favor of `total_chars/page_count`. Delta: ~-15 lines or ~+5 lines.
4. Consolidate env knobs: move `CATEGORY_BC_PROMOTION_THRESHOLD` (config.py:16, hardcoded 0.17) into `VerdictThresholds.from_env` for env-tunability; delete the config.py constant. Delta: net +1 line.
5. Add `TreeTransformReport` returned from `prepare_tree` (pre/post node_count, transforms applied); log it in client.py and pass `pre_transform_node_count` into `validate_tree`. Delta: +20 lines helpers.py.

Total estimated delta: **~-15 net lines** (roughly -30 to -40 deleted, +20 new report logic).

**Historical bug classes prevented:** RFC-025 Haftpflicht FAIL->PASS flip (deleting hysteresis removes the entire class — identical content always gets identical verdicts); RFC-023 D10 threshold-widening test breakage (single fixed PASS threshold, no widening); RFC-029 D1 chars/node floor regression (pre-transform node_count reflects inherent document structure, not segmentation artifacts); RFC-035 D2 singleton kv explosion (explicit, auditable transform coupling via `prepare_tree` + `TreeTransformReport`).

**Migration risk:** Deleting hysteresis will regress some PASS docs (ratio between 0.30-0.40 relying on the widened threshold) to MARGINAL. Sequence: Step 5 first (transform-report logging only, one corpus cycle, identify hysteresis-dependent docs) → Step 2 (pure refactor, no behavior change) → Step 3 (changes LOW_CONTENT_DENSITY only for segmentation-inflated docs, the exact bug case) → Step 1 (delete hysteresis + simultaneous corpus re-score; PASS->MARGINAL regressions are genuine corrections) → Step 4 (any time, no behavior change). Each step independently deployable/revertable.

**Estimated effort:** Step 1: 2h, Step 2: 0.5h, Step 3: 1.5h, Step 4: 0.25h, Step 5: 1h — ~5h implementation + 2h corpus re-score = **~7 hours**, spread across 2-3 PRs.

---

### Zone 5: OCR/Enrichment Signal Conflation
**Severity:** high | **Bug count:** 9

#### Mechanism
When `_OCR_ESCALATION` is toggled to fix one behavior (e.g., disable runaway per-picture OCR), it inadvertently disables the other (page-level OCR escalation for garbled documents). The enrichment promotion path in `classify_verdict` checks char volume after promotion but not content validity, so barcode/watermark digit noise (warid 597: 70 blocks/3,208 chars) passes the floor check. The `image_enrichment_promoted` branch bypasses the `max_leaf_ratio > 0.75` hard-fail (helpers.py:2096-2106, intentionally before line 2111), meaning flat image-enriched documents always have max_leaf_ratio=1.0 but avoid the structural FAIL. Each successive hardening pass closes one bypass only for a new failure mode to emerge in the same documents through the same structural gap.

#### History
a. RFC-025 D1 region-aware text-layer check inflated char counts via `_flat_block_text` conflation (RFC-027 D0/D1, Run 10).
b. RFC-025/026 `image_enrichment_promoted` let marsoom-13 earn PASS on 38 chars (Run 9), RFC-026 floor then let warid-597 pass with barcode noise (Run 10).
c. RFC-020 F2 forced pre-garble OCR for Arabic-filename PDFs reclassifying PictureItems to TextItems, disabling picture enrichment for MOU MOHRE (Run 16).
d. RFC-035 D2 landscape chart-splitting unbounded loop produced 71 singleton kv blocks (Run 18-19).
e. OCR_IMAGE_BLOCK_CONFLATION investigation confirmed content_class computation only counts table/kv/prose — image blocks invisible.

#### Code Evidence
config.py:41 `OCR_ESCALATION` single flag. converters.py:1485 import of OCR_ESCALATION. helpers.py:2096-2106 image-enrichment rescue positioned before max_leaf_ratio hard-fail (line 2111). converters.py:2061-2371 `_recover_picture_text` (310 lines: coverage gate, text-layer checks, garble checks, clip-text capture, rotation, crops, parallel OCR). client.py:529-541 `image_to_markdown` branch (never calls `splice_figure_markers` or `_enrich_image_blocks`).

#### Key Files
- src/pageindex_mcp/converters.py
- src/pageindex_mcp/client.py
- src/pageindex_mcp/config.py
- src/pageindex_mcp/helpers.py

#### Simplification Proposal
Split the single `OCR_ESCALATION` boolean into two independent flags — `OCR_ESCALATION_GARBLE` (page-level OCR retry) and `OCR_ESCALATION_PER_PICTURE` (per-picture crop+OCR) — and separate `_flat_block_text` into two non-overlapping text accessors: one for verdict char-counting (primary text only, `_flat_block_primary_text`) and one for search indexing (primary + enrichment). The image-enrichment-promoted verdict branch should use `_flat_block_primary_text` so enrichment metadata cannot inflate the count. The standalone image path must call `splice_figure_markers`/`_enrich_image_blocks` via a shared reusable helper, same as the PDF path.

**Restructuring steps:**
- **A** — Split OCR_ESCALATION (config.py, client.py, converters.py): new `OCR_ESCALATION_GARBLE`/`OCR_ESCALATION_PER_PICTURE`, both default "1", backward-compat shim if old var set and new ones aren't. (+8 lines config.py, ~0 net elsewhere)
- **B** — Fix verdict char-counting to use primary text only (helpers.py): new `sig.primary_text` field from `_flat_block_primary_text`; `classify_verdict` (2096-2106) reads it instead of `sig.flat_text`. (~+15 lines)
- **C** — Unify standalone-image post-conversion path: extract `_apply_picture_enrichment(flat_md, pic_results, blocks, doc_id)` from the PDF flat-success block (~1837-1968); call it from both PDF and standalone-image (~1174-1210) paths. (~+20/-15, net +5)
- **D** — Delete dead `_flat_block_text` usage from verdict paths, redirect any transitive callers to `_flat_block_primary_text`. (~0, possibly -5)

Total: **+25 lines net** across 4 files.

**Historical bug classes prevented:** RFC-020 F2/Run-16 (OCR_ESCALATION_PER_PICTURE=0 disables crop OCR without touching garble retry); RFC-027 D0/D1/Run-10 (primary-text-only counting stops barcode-noise char inflation, warid-597 correctly MARGINAL/FAIL); RFC-025/026/Run-9 (marsoom-13's true 38-char floor surfaces); RFC-035 D2/Run-18-19 (primary-text separation keeps singleton-block inflation out of the verdict path); standalone image content loss (unified helper prevents it).

**Migration risk:** Low — internal control-flow only, no API/storage format changes. Sequence: PR 1 (flag split with backward-compat shim, both default ON, zero behavior change, corpus confirm) → PR 2 (primary-text verdict fix, highest-value/highest-risk, corpus diff to confirm warid-597 drops PASS→MARGINAL/FAIL and marsoom-13 drops below floor) → PR 3 (enrichment helper extraction/dedup, lowest risk, adds a previously-missing path).

**Estimated effort:** PR 1: 0.5d, PR 2: 1d, PR 3: 0.5d, plus 1d corpus validation. **Total: ~3 days.**

---

### Zone 6: Conversion Pipeline Stage Coupling (pdf_to_markdown_docling)
**Severity:** high | **Bug count:** 7

#### Mechanism
Stage coupling means the output of each stage becomes an implicit input to the next. The `body_for_containment` parameter exists solely to undo a prior stage's side effect (text fallback inflating md). When stage order is accidentally changed or a new stage is inserted, downstream stages see different inputs. The two-candidate source selection (post-addon vs raw Docling markdown) runs heading recovery independently on both, then selects the winner based on structural depth — but the selected candidate's heading levels are already finalized by a recovery chain that may have fired different stages on the two inputs. Arabic structural heading injection injects just enough headings to clear `validate_tree` thresholds, preventing flat-fallback routing that would have recovered more content.

#### History
a. RFC-027 D4 heading injection prevented flat fallback for marsoom-13 (RFC-029 D1, Run 12).
b. RFC-029 D3 fence-marker stripping silently dropped ALL content between stray fence markers (SLA 264->0, MOU 89% loss, Reitlehrer 32% loss, RFC-030 D0, Run 13, partial recovery Run 18).
c. RFC-034 D11 ToC heading stripping collapsed Penal Code depth 3->2 with 83% node flattening until RFC-034 D16 added over-strip guard.
d. RFC-034 D16/D17 splitter changes stripped body text from headings in Fed. Decree-Law 33 (88% body-less headings, Run 18), recovered in Run 19.

#### Code Evidence
converters.py:3139-3481 `pdf_to_markdown_docling` (342 lines, noqa: PLR0915, C901). converters.py:3424-3427 `body_for_containment` captured BEFORE text fallback stage. converters.py:3376-3377 two `_candidate_from_document` calls (post_candidate, raw_candidate). converters.py:3391-3402 source selection gate. converters.py:822-842 heading-depth recovery chain (3-stage, runs per-candidate). helpers.py:3185-3204 `_strip_toc_heading_nodes_guarded` (over-strip guard added for D11).

#### Key Files
- src/pageindex_mcp/converters.py
- src/pageindex_mcp/helpers.py

#### Simplification Proposal
No structured proposal was generated for this zone in this pass.

---

### Zone 7: Registry/Persistence Consistency Gaps
**Severity:** medium | **Bug count:** 6

#### Mechanism
The child subprocess runs `client.index()` (save_doc + save_doc_meta + write_verdict to MinIO) but cannot write to Postgres (no pool). The parent worker must re-read the just-persisted MinIO artifact via `read_registry_fields` to populate the registry, introducing a read-after-write dependency across process boundaries with no consistency guarantee. If the re-read happens before `_confirm_write_visible` completes, the parent gets stale or missing data. The `reconcile_registry_drift` cron uses etag-based change detection and a stale-row purge, but the listing-then-delete pattern is not atomic: a doc ingested between the MinIO listing and the Postgres delete has its freshly-written registry row deleted. The 50% safety threshold on `_delete_stale_rows` limits damage but doesn't prevent individual row loss.

#### History
a. RFC-034 D18 write-visibility barrier caused SLA doc to land 3-5 minutes late missing scorer window (RFC-036 D1, Run 15).
b. cabinet_resolution_no_96 scored ERROR at score-time despite artifacts existing at publish-time (Run 16), recovering to PASS by Run 18.
c. arabicSLA regressed MARGINAL->ERROR when artifact landed 06:31:28Z, minutes after cohort (Run 19).
d. ISS-02 async fire-and-forget wrapper around Postgres registry delete swallowed failures while cascade logged "full cascade succeeded".
e. ISS-03 registry_backfill completion flag set on zero keys (partially fixed).
f. RFC-009 D6 removed MinIO fallback making registry the sole read path.

#### Code Evidence
storage.py:37 `_WRITE_BARRIER_DELAYS = (0.05, 0.1, 0.3)`. storage.py:44-66 `_confirm_write_visible` barrier. worker.py:674-701 `_upsert_registry_row` (best-effort, exception swallowed). registry_backfill.py:415-423 zero-key guard (D3/Property 7 fix). registry_backfill.py:649-684 `_delete_stale_rows` (listing-then-delete race). registry_backfill.py:646 `_MAX_STALE_DELETE_FRACTION = 0.5`.

#### Key Files
- src/pageindex_mcp/storage.py
- src/pageindex_mcp/worker.py
- src/pageindex_mcp/registry_backfill.py

#### Simplification Proposal
No structured proposal was generated for this zone in this pass. (Note: Zone 3's Step D — passing registry fields directly from the job result rather than re-reading MinIO — directly addresses this zone's `_upsert_registry_row` mechanism and should be coordinated with any Zone 7 remediation.)

---

### Zone 8: Dead/Uncommitted/Stale Code Divergence
**Severity:** medium | **Bug count:** 6

#### Mechanism
The dead gate pattern is particularly insidious: `ARABIC_LOW_CONTENT_RATIO` is in `HARD_FAIL_DEFECTS` but not in `GATE_TABLE`, meaning `validate_tree` can never detect it — but `_defect_from_reason_str` can reconstruct it from a stored `verdict_reason` string, and `promotion_sweep` uses that reconstruction to build a `TreeGateResult`. If a persisted sidecar carries `verdict_reason='arabic_low_content_ratio'` from before the gate was deprecated, `promotion_sweep` creates `TreeGateResult(ok=False, defect=TreeDefect.ARABIC_LOW_CONTENT_RATIO)`, `classify_verdict` sees it in `HARD_FAIL_DEFECTS`, and returns FAIL — a permanent FAIL that no re-evaluation can lift because the gate that would clear it no longer exists. The stale-remote-image pattern means fixes deployed locally never reach the Scaleway Docling service that actually processes documents in production, so unconditional bidi heading reversal persists despite the working-tree guard.

#### History
a. RFC-029 D0 `_check_bidi_coherence` was dead code, wired in RFC-030 D5, found to be a null detector in RFC-033/034.
b. RFC-029 D6 Phase B judge rules never written, causing phantom regressions (Haftpflicht, image pie chart, Federal Decree-Law 47, Run 13).
c. RFC-034 D19 staged never committed, leaving OCR-displacement defect active (Run 36).
d. RFC-033 D2 Part A guard uncommitted, stale remote Docling image performs unconditional bidi reversal (BIDI_ROOT_CAUSE_RFC033.md).
e. PDF_INSPECTOR_PRECLASSIFY dead for months until D0-D2 wiring (PDF_INSPECTOR_PHASE2_ACTIVATION_REPORT).
f. Fix-1 splitter redesign (commit a940f14) never applied retroactively to pre-deployment docs (Discovery #3106).

#### Code Evidence
helpers.py:80 `ARABIC_LOW_CONTENT_RATIO` deprecated/dead comment. helpers.py:214 `ARABIC_LOW_CONTENT_RATIO` in HARD_FAIL_DEFECTS. helpers.py:197 `ARABIC_LOW_CONTENT_RATIO` in REASON_POLICY. helpers.py:1491-1492 GATE_TABLE comment: "ARABIC_LOW_CONTENT_RATIO is deprecated/dead and excluded". helpers.py:1944-1956 `_defect_from_reason_str` can reconstruct dead defect from stored string. config.py:21-23 `PDF_INSPECTOR_PRECLASSIFY` (default '0'). promotion_sweep.py:96 `_defect_from_reason_str` used to reconstruct TreeGateResult from stored verdict_reason.

#### Key Files
- src/pageindex_mcp/helpers.py
- src/pageindex_mcp/config.py
- promotion_sweep.py

#### Simplification Proposal
No structured proposal was generated for this zone in this pass. (Note: Zone 3's Step C — merging offline recomputers to always re-run `validate_tree` rather than reconstructing `TreeGateResult` via `_defect_from_reason_str` — directly closes this zone's "permanent FAIL from a dead gate" mechanism and should be coordinated with any Zone 8 remediation.)

## Cross-Cutting Themes

- Incomplete/dead wiring: fixes implemented but never connected to the pipeline or to recovery routing — RFC-029 D0 `_check_bidi_coherence` dead code, RFC-029 D6 Phase B judge rules never written, RFC-030 D2's four new validate_tree reasons never handled in client.py, PDF_INSPECTOR_PRECLASSIFY flag dead for months, RFC-034 D19 staged but never committed.
- Threshold calibration against a single example overgeneralizes and later requires walking back: RFC-029 D1's 500 chars/node floor (tuned to marsoom-13) rejected the entire Penal Code family (408-459 chars/node) until RFC-030 D3 lowered it to 150; RFC-023 D10's 0.17→0.20 PASS_MAX_LEAF_RATIO widening broke borderline-ratio tests and combined with RFC-025 hysteresis to let garbled documents flip FAIL→PASS on unchanged content.
- Gate-only fixes without a treatment/preprocessing path precede a working fix: RFC-028 D2 presentation-forms detection had no recovery path and caused ERROR fallthrough until RFC-029 D0 added NFKC normalization; RFC-026 D0 zero-content FAIL exposed a missing OCR-escalation trigger later closed by RFC-027 D2; RFC-005/RFC-023's OCR escalation and CMap-crash flat-route fallback both leave completed-but-content-poor documents unescalated to OCR/VLM.
- Design-intent vs implementation gap: verdict reasons are mapped to the correct outcome in classify_verdict but the routing code (client.py) never implements that intent, producing terminal ERROR where a FAIL-with-artifact was designed (RFC-029→RFC-030 D2); similarly RFC-026's char floor checks volume, not validity, so it still lets barcode/watermark junk through the promotion path it was meant to close.
- Content-preservation vs noise-filtering tradeoff: aggressive stripping/toggle logic silently drops legitimate content — RFC-029 D3's naive fence-parity toggle (SLA 264→0 blocks, MOU 89% loss, Reitlehrer 32% loss), RFC-034 D11's unbounded ToC-stripping (Penal Code 83% node collapse) — while enrichment features (RFC-025 D1 picture OCR, RFC-020 F2 Arabic-filename OCR probe) conflate or displace primary text they were meant to recover.
- Detector blind spots from vocabulary/encoding-range mismatches: RFC-027 D3's readability scoring misses specialized (governance/legal) vocabulary; the bidi-coherence detector's presentation-form signal (U+FB50-FEFF) is mutually exclusive with its own canonical-range (U+0600-06FF) line selector, making it a null detector on both canonical and NFKC-normalized text; digit-ratio and Latin-gibberish garble checks miss short blobs and space-separated Latin mojibake respectively.
- Edge-case boundary conditions silently defeat guardrails: RFC-029 D2's <20-token short-text floor makes the OCR retry keep-best comparison arithmetically impossible to win for no-text-layer PDFs; RFC-030 D1's partial-revert only restores tree state, not md_content/tmp_md_path/pic_results, creating downstream state inconsistency after a "successful" revert.
- Single kill-switches and shared code paths gate multiple independent behaviors, so a fix or toggle for one silently breaks the other: `_OCR_ESCALATION` controls both page-level and per-picture OCR; RFC-035's landscape-splitting change degraded both landscape and portrait chart extraction simultaneously via shared chart-splitting logic.
- Non-atomic or fire-and-forget writes across systems hide failures behind reported success: async registry-delete scheduling (ISS-02) and Postgres dual-write (Issue A) both swallow exceptions and log success regardless; registry_backfill sets its completion flag even on zero migrated keys, making the entire corpus invisible once RFC-009 D6 removed the MinIO fallback.
- Persistence-timing races between worker writes and scorer reads: RFC-034 D18's read-after-write consistency barrier adds up to 8.8s latency, repeatedly pushing job completion past the scorer's polling window and producing false ERROR/MARGINAL verdicts for documents that are, in fact, correctly persisted (SLA, القرار التنظيمي, cabinet_resolution_no_96, اتفاقية مستوى across Runs 15-19).
- Uncommitted or stale-deployed code diverges from what actually runs in production: RFC-033 D2 Part A's guard exists only in the working tree and can never reach the stale remote Scaleway Docling image that still performs unconditional bidi heading reversal.
- Duplicated implementations of the same check diverge over time and require double maintenance: tree-path vs flat-path garble detection (`_tree_is_garbled` / `_flat_text_is_garbled`) and duplicated digit-ratio floor guards (ISS-36) both risk a fix landing in only one copy.
- Verdict-bypass promotion paths circumvent content-volume and quality gates: `image_enrichment_promoted` and `cat_b_promoted` both let near-zero or junk content earn PASS, requiring repeated hardening passes (RFC-026, RFC-030) that close one bypass only for a new failure mode (barcode/watermark noise) to emerge in the same documents.
- Judge/gate non-determinism produces verdict flip-flops on byte-identical or near-identical content across consecutive runs, independent of any pipeline code change — RFC-025 hysteresis, RFC-023 D10 threshold widening, and unstable garble-ratio false positives (SLA ratio=1.00 appearing/disappearing) all contribute to this pattern, complicating whether a run-over-run delta reflects a real fix or pure scoring jitter.
- OCR/enrichment pipelines double-fire or conflate distinct signal channels: full-page OCR escalation re-triggers per-picture crop OCR as a competing second pass; enrichment metadata (ocr_text/description) gets conflated with primary document text in char counts, inflating totals and defeating garble detection.
- Specialized ingestion routes diverge from the primary PDF route and lose feature parity: standalone image files skip splice_figure_markers/_enrich_image_blocks entirely, losing all chart/picture content that the PDF route recovers via enrichment.
- Shared code-path changes cause simultaneous regressions across unrelated documents with an identical failure signature, making a single RFC's blast radius hard to scope in advance (RFC-035 landscape-splitting hit both portrait and landscape variants; RFC-029/030 Arabic-handling changes are repeatedly cited as the prime suspect for unrelated MOU/SLA/قرار-106/RTL regressions across multiple runs).
