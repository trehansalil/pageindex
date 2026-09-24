# Architecture Defect Zones Audit — 2026-08-18 POST-FIX-6

**Date:** 2026-08-18
**Sources:** 8 history miners, 3 code maps

## Summary Table

| # | Zone | Severity | Bug Count | Key Files |
|---|------|----------|-----------|-----------|
| 1 | Garble Detection Surface Sprawl | critical | 11 | `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/script.py` |
| 2 | Dual Verdict Authority (validate_tree vs classify_verdict) | critical | 10 | `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/client.py`, `src/pageindex_mcp/converters.py` |
| 3 | Recovery Pipeline Implicit Ordering and State Mutation | critical | 9 | `src/pageindex_mcp/client.py`, `src/pageindex_mcp/helpers.py` |
| 4 | Picture/OCR Recovery Dual-Path Conflation | high | 8 | `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/config.py`, `src/pageindex_mcp/client.py` |
| 5 | Cross-Process Verdict/Registry Write Races | high | 5 | `src/pageindex_mcp/storage.py`, `src/pageindex_mcp/worker.py`, `src/pageindex_mcp/converters_cli.py`, `src/pageindex_mcp/client.py` |
| 6 | Splitter Pattern Fragility and Giant Tail-Blob Recurrence | high | 5 | `src/pageindex_mcp/helpers.py` |
| 7 | Silent Fallback Chains Masking Compliance and Quality Failures | high | 5 | `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/config.py`, `src/pageindex_mcp/worker.py` |
| 8 | Duplicated Threshold/Logic Definitions Across Files | medium | 4 | `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/client.py`, `src/pageindex_mcp/converters.py` |

## Zone Details

### Zone 1: Garble Detection Surface Sprawl

**Severity:** critical | **Bug count:** 11

Garble detection is split across 8+ calling contexts (`GarbleContext` enum), two granularity levels (bulk vs per-node), context-specific short-circuits, blob-kind normalization strategies, and 6+ independently-evolved prongs. Each new prong or context-specific rule closes one blind spot while leaving others — RTL word-splitting, embedded Latin mojibake in Arabic, canonical-order BiDi reversal, image-marker false-flags — undetected for months. Fixes to one prong or context routinely expose new false-positive or false-negative failure modes in another context because the shared `garble_prongs` function is evaluated with different normalization and short-circuit rules depending on who calls it.

#### Mechanism
The single `garble_prongs` function (helpers.py:1251) is the shared engine, but its behavior is context-dependent via three layers of indirection: (1) `_garble_context_short_circuit` (helpers.py:1386) can return True/False before prongs even run, (2) `_garble_context_blob_kind` (helpers.py:1411) selects different normalization (BlobKind.TREE_TEXT vs RAW_MARKDOWN), (3) each prong has its own threshold and env-var gate. Adding a prong to catch one failure mode (e.g. presentation_forms for Arabic, RFC-028 D2) triggers false positives in another context (e.g. image-marker repetition in FLAT_MARKDOWN, RFC-023 D3). Stripping HTML comments to fix that false positive then fails to detect legitimate garble where comments carry the signal. The `expected_script` parameter self-infers when None (helpers.py:1328), so callers cannot reliably disable the `latin_gibberish` prong — they get inference-based behavior instead of no-check behavior. Gate 1 (bulk, hard_fail) and Gate 4 (per-node, not hard_fail) both call `check_garble` but at different granularity and with different policy consequences, so a fix to the shared engine affects both gates asymmetrically.

#### History
a. RFC-020 F2 (expected_script threading) regressed by RFC-021 QF1 (pre-garble forced-OCR).
b. RFC-021 QF4 (windowed garble detection) regressed by RFC-023 D3 (image-marker false-flags).
c. RFC-023 D3 (HTML comment stripping) was itself a partial fix.
d. RFC-028 D2 (Arabic presentation-forms prong) caused Human Rights PDF to regress from FAIL to ERROR (RFC-029 D0, RFC-030 D0).
e. RFC-029 D3 (fence/HR stripping) caused 89-100% content loss in 5 docs and a PASS-to-MARGINAL flip on Reitlehrer via reduced flat_char_count.
f. ISS-36: digit-ratio garble floor (500 chars) duplicated in `_tree_is_garbled` and `_flat_text_is_garbled` with no shared helper.
g. Observation #5500: ward-597 numeric-junk persisted as PASS because zero PUA codepoints made it invisible to standard garble heuristics.
h. Observation #5627: two stored PASS verdicts reclassified as FAIL/MARGINAL due to garble patterns using zero PUA codepoints.

#### Code Evidence
helpers.py:1251 `garble_prongs` (shared engine, 12 prongs). helpers.py:1360 `GarbleContext` (8 contexts). helpers.py:1386 `_garble_context_short_circuit` (FLAT_MARKDOWN short-circuit). helpers.py:1411 `_garble_context_blob_kind` (normalization dispatch). helpers.py:1328 expected_script self-inference fallback. helpers.py:1644 `_gate_garbling` (Gate 1, hard_fail=True). helpers.py:1677 `_gate_node_garbling` (Gate 4, hard_fail=False). helpers.py:1316 HTML comment stripping in token_repetition prong. helpers.py:1375 `GARBLE_SHORT_TEXT_DEFAULT` env var gate.

#### Key Files
- `src/pageindex_mcp/helpers.py`
- `src/pageindex_mcp/script.py`

#### Simplification Proposal
Replace the "one shared engine with context-dependent behavior" pattern with two explicit, separate garble evaluators — a `GarbleProfile` dataclass that freezes all evaluation parameters (blob_kind, short_circuit rule, expected_script handling) at construction time, and a single `evaluate(blob) -> GarbleResult` method that runs prongs with those frozen parameters. Each call site constructs a profile once (or uses a module-level constant profile), eliminating `GarbleContext` dispatch, `_garble_context_short_circuit`, `_garble_context_blob_kind`, and the `expected_script` self-inference fallback. `garble_prongs` becomes a pure function of `(normalized_blob, expected_script)` with no BlobKind parameter — normalization happens before it is called, inside the profile's `evaluate` method.

**Restructuring steps:**
1. Introduce `GarbleProfile` (helpers.py, +45 lines net) — frozen dataclass with `blob_kind`, `short_circuit`, `script_inference` fields; two constants `BULK_PROFILE` and `FLAT_MARKDOWN_PROFILE`; an `evaluate(blob, expected_script, original_defect=None) -> GarbleResult` method.
2. Purify `garble_prongs` (-15 lines) — remove `blob_kind` param and internal normalization call; remove `expected_script` self-inference (callers must call `infer_script` explicitly if they want inference).
3. Delete indirection layer (-40 lines) — delete `GarbleContext`, `_garble_context_short_circuit`, `_garble_context_blob_kind`, `_is_garbled_blob`; simplify `check_garble` to a thin shim over `profile.evaluate`.
4. Update call sites (~21 sites, -5 lines avg) — replace `context=GarbleContext.X` with `profile=BULK_PROFILE`/`FLAT_MARKDOWN_PROFILE`; make all `expected_script=None` calls explicit via `infer_script(text)`.
5. Unify `_garble_check_nodes` script inference (-10 lines) — route through `BULK_PROFILE.evaluate(text, node_script)`.
6. Delete env-var gates from `garble_prongs` (-5 lines) — move `GARBLE_SHORT_TEXT_DEFAULT`/`GARBLE_FLAT_MARKDOWN_NORMALIZE` into profile construction.

Net delta: ~-30 lines, with the primary win being conceptual (8 context values + 2 dispatch functions + 1 self-inference fallback → 2-3 named profile constants).

This would have prevented: RFC-028 D2/RFC-029 D0 (prong-normalization coupling), RFC-023 D3 (image-marker false-flags), RFC-029 D3 (fence/HR content loss), and RFC-020 F2/RFC-021 QF1 (expected_script silent-inference regression), because normalization, short-circuit, and script-inference become profile-level concerns frozen at construction rather than shared-engine side effects. ISS-36's digit-ratio duplication is also closed since bulk and per-node evaluation both route through the same `garble_prongs` call with the same thresholds.

Migration: staged in 5 independently-shippable steps (introduce types → remove self-inference → migrate call sites file-by-file → delete old dispatch → delete backward-compat shim), each verifiable with `uv run pytest` and a corpus diff. Estimated effort: 2-3 days.

---

### Zone 2: Dual Verdict Authority (validate_tree vs classify_verdict)

**Severity:** critical | **Bug count:** 10

Quality gating is split across two independent verdict engines that can disagree: `validate_tree` (10-gate table producing `TreeGateResult` with defects and signals) and `classify_verdict` (a 195-line grouped-rule engine with 7+ independent promotion branches and its own structural checks). `validate_tree` gates tree validity; `classify_verdict` determines the persisted PASS/MARGINAL/FAIL verdict. But `classify_verdict` also applies independent checks (max_leaf_ratio hard-fail, content-class promotions, image-enrichment rescue, depth-adequacy clamp) that can override or mask what `validate_tree` decided. For flat-path documents, `validate_tree` is bypassed entirely (`validate_result=None`), so all 10 quality gates are skipped. Additionally, `classify_verdict` is called twice: once during source selection in `_candidate_from_document` (converters.py:948) on a lightweight structure, and again in client.py on the final post-`prepare_tree` structure — the two calls can produce different verdicts.

#### Mechanism
The structural split creates three classes of bugs: (1) New `validate_tree` gates shipped without corresponding recovery wiring in client.py cause cascading PASS-to-ERROR regressions (RFC-029 D1: 4 new failure reasons with no recovery path, 3 PASS-to-ERROR + 1 FAIL-to-ERROR). (2) `classify_verdict`'s promotion paths can bypass `validate_tree`'s structural hard-fails: the image-enrichment rescue (helpers.py:2306-2319) is intentionally placed before the max_leaf_ratio hard-fail, meaning flat image-enriched docs with max_leaf_ratio > 0.75 bypass the structural gate entirely. (3) The `PASS_MAX_LEAF_RATIO` threshold was ratcheted 0.15 → 0.17 → 0.20 → 0.30 across four RFCs because `classify_verdict`'s base PASS check uses it as a hard cutoff while the tree exhibits Docling non-determinism jitter — each widening acknowledged as needing hysteresis, and 0.20→0.30 broke 5 previously-passing unit tests. (4) Flat docs bypass all gates: `_persist_flat_result` (client.py:1898) passes `validate_result=None`, so `classify_verdict` (helpers.py:2261) builds its own `TreeSignals` but never evaluates the `GATE_TABLE`. (5) The `_clamp_pass` function (helpers.py:2177), applied to every PASS branch, can downgrade a verdict that both `validate_tree` and the promotion logic approved.

#### History
a. RFC-021 QF2a (image-enrichment promotion) became dead code when max_leaf_ratio > 0.75 hard-FAIL fires first (RFC-022 B2-B).
b. RFC-023 D10 widened `PASS_MAX_LEAF_RATIO` 0.17→0.20.
c. RFC-024 D0 widened 0.20→0.30, breaking 5 unit tests (observation #5364).
d. RFC-026 gate hardening surfaced 12 pre-existing masked defects in one run (0 improvements, 12 regressions, observation #5483).
e. RFC-029 D1 added 4 new `validate_tree` failure reasons never wired into client.py recovery — 3 PASS-to-ERROR + 1 FAIL-to-ERROR (RFC-030 D2/D3).
f. Observation #4127: `classify_verdict` confirmed wrong on 2 documents where stored PASS verdicts were structurally corrupt.

#### Code Evidence
helpers.py:1888 `validate_tree` (10 gates via GATE_TABLE). helpers.py:2199 `classify_verdict` (195-line grouped-rule engine). helpers.py:2306-2319 image-enrichment rescue before max_leaf_ratio hard-fail (helpers.py:2324). helpers.py:2177 `_clamp_pass` (caps PASS to MARGINAL). helpers.py:317 `PASS_MAX_LEAF_RATIO` default 0.30. client.py:1898-1901 flat path passes `validate_result=None`. converters.py:948 early classify_verdict call in `_candidate_from_document`. client.py:2006 final classify_verdict call with `state.original_gate_result`.

#### Key Files
- `src/pageindex_mcp/helpers.py`
- `src/pageindex_mcp/client.py`
- `src/pageindex_mcp/converters.py`

#### Simplification Proposal
Merge `validate_tree` and `classify_verdict` into a single function `compute_verdict(structure, content_class, ...) -> VerdictResult` that runs the GATE_TABLE, then applies content-class promotions and caps in one pass, returning a single (verdict, reason, signals, all_defects) result. The early call in converters.py:948 should call the same unified function with a `source_selection=True` flag that skips persistence-only caps like `_clamp_pass`, rather than calling a separate `classify_verdict` with `validate_result=None`. The flat-doc path should also run through the unified function with an explicit `flat=True` mode that evaluates a defined subset of gates (zero_content, reordered, garble) instead of silently skipping all 10 gates via `validate_result=None`.

**Restructuring steps:**
- A. Define `VerdictResult` dataclass (helpers.py, +15) replacing `TreeGateResult` + classify_verdict's `(str, str)` return.
- B. Inline gate evaluation into unified `compute_verdict` (-40 net) — move GATE_TABLE evaluation into compute_verdict's first phase; `validate_tree` becomes a thin backward-compat wrapper, later deleted.
- C. Eliminate the `validate_result=None` silent-skip path (+10) — flat docs run a defined gate subset instead of skipping all gates.
- D. Remove early `classify_verdict` call in converters.py:948 (~0) — replace with `compute_verdict(..., source_selection=True)`.
- E. client.py: single call-site (-25) — both `_persist_flat_result` and `_persist_tree_result` call `compute_verdict`; remove `state.original_gate_result`.
- F. Delete `_compute_verdict_band`, fold into compute_verdict (-20).
- G. GATE_TABLE exhaustive-coverage assertion (+8) — module-level assertion that every non-hard-fail `TreeDefect` has a matching recovery guard or route mapping, turning missing recovery wiring into an import-time crash.

Net delta: ~-50 lines.

This would have prevented: RFC-029 D1 (Step G's assertion catches missing recovery wiring at import time), the `PASS_MAX_LEAF_RATIO` ratcheting churn (single evaluation order for hard-fail vs PASS check), RFC-026's 12 masked regressions (signals computed once, consumed once instead of two independently-derived signal sets), the converters.py:948 vs client.py:2006 dual-call divergence (one function, two explicit modes), and flat docs bypassing all gates.

Migration: 5-step sequence, each corpus-diffable — (1) add VerdictResult as pure addition, (2) wrap old functions inside compute_verdict and prove zero verdict diff, (3) add flat-gate subset behind `FLAT_GATE_SUBSET_ENABLED` and manually review any flips, (4) delete old functions/call-sites, (5) add exhaustive-coverage assertion. Estimated effort: 3-4 days.

---

### Zone 3: Recovery Pipeline Implicit Ordering and State Mutation

**Severity:** critical | **Bug count:** 9

The 7-step recovery waterfall in client.py (OCR escalation → RTL repair → RTL flat compare → VLM fallback → image-dominant OCR → flat-prefer → landscape reroute) is implicitly ordered by method call sequence, not by a declarative pipeline. Each recovery reads `state.first_defect` to decide whether to fire, creating hidden dependencies on prior recovery methods' side effects on the mutable `ExtractionState` dataclass (~20 mutable fields). The snapshot/restore pattern (`ExtractionSnapshot`) uses fragile positional tuple destructuring where `gate_result` appears twice (helpers.py:164-172). Recovery methods mutate `state.ok`, `state.reason`, `state.gate_result`, `state.route`, `state.md_content`, `state.pic_results`, and `state.rtl_decision` in-place, and `_finalize_routing` (client.py:967) attempts to reconcile these after all recoveries complete. New validation gates shipped without corresponding recovery wiring repeatedly caused PASS-to-ERROR regressions.

#### Mechanism
Each recovery method checks `state.first_defect` to decide whether to fire, but prior recovery methods may have changed the tree text, re-run `validate_tree`, and updated `state.first_defect` as a side effect. This means: (1) Adding a new recovery method requires understanding every prior method's state mutations. (2) A fix in one recovery that changes `state.ok` or `state.first_defect` can prevent a downstream recovery from firing. (3) The RTL repair (recovery 2, client.py:1510) clears `state.rtl_decision` to None, forcing `validate_tree` to recompute `decide_rtl` on potentially different text — if the repair only partially succeeded, the recomputation may produce a different `RtlDecision` than the original. (4) `_finalize_routing` (client.py:979) skips when `state.ok` or `state.route_overridden`, so stale routes from pre-recovery state can reach the exhaustive match/case dispatch (the comments at client.py:2236-2249 document this explicitly). (5) Implementations marked complete but never wired into the pipeline (dynamic timeout RFC-027 D7, bidi coherence check RFC-029 D0, judge calibration rules RFC-029 D6) recur because there is no structural contract requiring new gates to have recovery handlers.

#### History
a. RFC-027 D7: dynamic timeout function created but never called from worker.py (fixed by RFC-028 D0).
b. RFC-029 D0: `_check_bidi_coherence` implemented with duplicate definitions but never called from any pipeline path (dead code until RFC-030 D5 wired it).
c. RFC-029 D1: 4 new `validate_tree` failure reasons never wired into client.py recovery paths (RFC-030 D2: "single highest-impact systemic bug").
d. RFC-029 D4: OCR retry keep-best logic had short-text floor making win condition mathematically impossible for no-text-layer PDFs (RFC-030 D1).
e. RFC-029 D6: judge calibration rules marked complete but never written to file (RFC-030 D6).
f. RFC-020 F0 regressed by RFC-021 QF1: OCR splice to tree path fix was undone by forced-OCR destroying PictureItem segmentation.

#### Code Evidence
client.py:2197-2210 recovery pipeline call sequence (7 sequential await calls). client.py:967-989 `_finalize_routing` (skips when ok=True or route_overridden). helpers.py:177-207 `ExtractionState` (20 mutable fields). helpers.py:151-172 `ExtractionSnapshot.restore` (8-element positional tuple, gate_result appears twice at positions 3 and 4). client.py:1510 `state.rtl_decision = None` (stale-decision clearing). client.py:2232-2297 exhaustive match/case dispatch with explicit comments about stale routes.

#### Key Files
- `src/pageindex_mcp/client.py`
- `src/pageindex_mcp/helpers.py`

#### Simplification Proposal
Extend the existing `GateSpec` declarative table in helpers.py to include a `recovery` field (an async callable or None) for each `TreeDefect`, replacing the implicit 7-method call sequence in client.py with a single loop: `for spec in GATES: if spec.recovery and spec.should_fire(state): await spec.recovery(state, ctx)`. Each recovery receives an immutable `RecoveryContext` and returns a `RecoveryOutcome` dataclass declaring which fields it changed, rather than mutating `ExtractionState` fields directly — the loop applies the outcome atomically and re-derives `first_defect`/`route` from the current gate_result. A GateSpec without a recovery field is a compile-time signal that the gate has no handler.

**Restructuring steps:**
1. Define `RecoveryOutcome` and `RecoveryContext` (helpers.py, +35) — replaces the positional-tuple `ExtractionSnapshot.restore()` pattern (delete, -22).
2. Add `recovery: RecoveryFn | None = None` field to `GateSpec` (+1).
3. Convert each `_recover_*` method in client.py to a standalone async function returning `RecoveryOutcome` instead of mutating state directly (~-35 across 7 functions) — guard conditions move into the loop driver.
4. Wire recovery functions into the GATES table (+7) — plus a module-level assertion that every RETRY_OCR/RETRY_RTL/RAISE-policy gate has a recovery callable (+3).
5. Replace the 7 sequential await calls (client.py:2197-2210) with the recovery loop (~+1 net) — single `_finalize_routing` call at the end.
6. Delete `ExtractionSnapshot` class and `.restore()` (-32) — rollback uses "return None = no-op" instead.
7. Reduce `ExtractionState` mutable fields (-10) — remove `route_overridden`, `original_gate_result`, `flat_garble_unrecovered`.

Net delta: ~-40 to -60 lines.

This would have prevented: RFC-027 D7 and RFC-029 D0/D1/D6 (the GATES assertion fails at import time if a RETRY_*-policy gate lacks a recovery), RFC-029 D4 (explicit RecoveryOutcome contract replaces the fragile snapshot/restore positional destructuring where gate_result appeared twice), and RFC-020 F0/RFC-021 QF1 (each recovery sees state after prior outcomes are applied atomically, not mid-mutation, so pic_results changes are explicit rather than silently overwritten).

Migration: 4 waves — (1) additive types + recovery field with recovery=None everywhere, (2) convert 2 simplest recoveries behind a feature flag with dual-path assertion, (3) convert remaining 5, delete ExtractionSnapshot, (4) replace the 7-call sequence with the GATES loop and remove the flag. Estimated effort: 3-4 days.

---

### Zone 4: Picture/OCR Recovery Dual-Path Conflation

**Severity:** high | **Bug count:** 8

Picture recovery and OCR escalation are two conceptually independent operations (per-picture enrichment vs page-level garble retry) that share code paths, configuration flags, and data structures, causing fixes to one to break the other. The legacy `OCR_ESCALATION` env var silently controls both via inheritance (config.py:50-62). The `_recover_picture_text` god function (converters.py:2215, 258 lines, complexity 32) combines serial fitz cropping, region classification, containment checking, clip-text capture, retained-skip bookkeeping, parallel Tesseract OCR, and VLM PNG retention. Coverage-filtered skips and genuine empty recoveries both produce empty `PictureResult` with no distinguishing signal, leaving literal `<!-- image -->` markers in output. The standalone image path (client.py, jpg/png) bypasses the entire enrichment pipeline that PDF images receive.

#### Mechanism
Three structural problems generate recurring bugs: (1) The `OCR_ESCALATION` flag inheritance (config.py:50-62) means setting `OCR_ESCALATION=0` silently disables BOTH garble-retry OCR escalation AND per-picture OCR enrichment — two independent features gated by one legacy toggle. (2) `_recover_picture_results` (converters.py:2603) uses `body_for_containment` to avoid false containment matches after `_document_level_text_fallback` appends raw text. This fragile ordering dependency (the snapshot must be taken BEFORE the fallback runs) is not structurally enforced and would silently break on stage reordering. (3) Coverage-filtered regions and genuinely-empty OCR results both produce empty `PictureResult` objects (converters.py:2418/2459), so downstream `splice_figure_markers` cannot distinguish "deliberately skipped" from "tried, found nothing." (4) The `_text_layer_has_content` probe (converters.py:1765) is a single function whose boolean result gates two different downstream decisions: whether to skip a region as decorative, and whether to enable coverage exemption for OCR. A garble sub-check (`_TEXT_LAYER_GARBLE_CHECK_ENABLED`) can flip the answer, cascading into unexpected OCR behavior.

#### History
a. RFC-019 D0 (marker-count mismatch fix) regressed by RFC-020 F4 (shared-reference mutation bug).
b. RFC-020 F0 (per-picture OCR splice) regressed by RFC-021 QF1 (forced-OCR destroys PictureItem segmentation).
c. RFC-020 F1 (coverage-filter exemption) regressed by RFC-023 D0 (exemption ineffective when text layer garbled, not absent).
d. RFC-024 D1 (clip_text capture for misclassified PictureItems).
e. RFC-024 D2 (per-region try/except isolation).
f. OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION: page-level OCR ran twice producing competing results; shared kill-switch gated both paths; standalone images lose all chart content as literal `<!-- image -->` strings; table blocks invisible to char-count diagnostics.

#### Code Evidence
config.py:41-62 `OCR_ESCALATION` inheritance shim with three-way flag split. converters.py:2215-2473 `_recover_picture_text` (258-line god function). converters.py:2603-2665 `_recover_picture_results` (body_for_containment parameter for ordering). converters.py:1765 `_text_layer_has_content` (single probe gating two different decisions). converters.py:2418 retained-skip PictureResult (empty, indistinguishable from genuine-empty). converters.py:2501 `splice_figure_markers` (cannot distinguish skip reasons).

#### Key Files
- `src/pageindex_mcp/converters.py`
- `src/pageindex_mcp/config.py`
- `src/pageindex_mcp/client.py`

#### Simplification Proposal
Replace the three-tier config inheritance shim (`OCR_ESCALATION` → `OCR_ESCALATION_GARBLE` + `OCR_ESCALATION_PER_PICTURE`) with two flat, independent env vars that default to "1" and have no inheritance relationship — delete the legacy `OCR_ESCALATION` flag entirely. Add a `skipped_reason` field (using the existing `SkipReason` enum) as a REQUIRED key on `PictureResult` (always populated, `None` when not skipped) so downstream consumers can distinguish "deliberately skipped," "tried and got nothing," and "succeeded" without inspecting the absence of fields. Structurally enforce the `body_for_containment` ordering by moving the snapshot + picture recovery call into a single function that takes pre-fallback markdown and returns (post-fallback-markdown, pic_results), making it impossible to call recovery against post-fallback text.

**Restructuring steps:**
1. Config flag decoupling (config.py, -15) — delete the legacy `OCR_ESCALATION` flag and both inheritance shims; two independent flat reads with no inheritance.
2. `PictureResult` skip-reason normalization (converters.py, +5 net) — `skipped_reason: str | None`, always populated; delete the `decorative` field entirely; `splice_figure_markers` checks `skipped_reason is not None`.
3. Enforce `body_for_containment` ordering (converters.py, ~-5 net) — extract into `_fallback_and_recover_pictures(...)` where the snapshot is a local variable, not caller-visible.
4. Collapse `_text_layer_has_content` into `_region_has_own_text_layer` (-15) — unify page-level and region-scoped checks behind one function with an optional `region_rect` param; delete the `_REGION_AWARE_TEXT_CHECK_ENABLED` rollback toggle.

Net delta: ~-25 to -30 lines.

This would have prevented: RFC-020 F1/RFC-023 D0 (merged text-layer function with garble-checking built in, removing the dual-path where one had the garble check and one didn't), RFC-024 D1 (structurally impossible to use the containment snapshot at the wrong time), `OCR_ESCALATION=0` silently disabling per-picture enrichment (independent flags, no inheritance), and `splice_figure_markers` mishandling skipped-vs-empty results (root of RFC-019 D0/RFC-020 F0 regressions — all three states now have distinct `skipped_reason` values).

Migration: 4 independently-shippable PRs — config decoupling (lowest risk) → PictureResult normalization (moderate, with a corpus spot-check on picture-heavy docs) → body_for_containment structural refactor (low risk, pure extraction) → text-layer consolidation (moderate, verify no deployment relies on the rollback toggle). Estimated effort: 2-3 days.

---

### Zone 5: Cross-Process Verdict/Registry Write Races

**Severity:** high | **Bug count:** 5

The extraction pipeline splits across a process boundary (converters_cli child subprocess vs worker parent), with verdict data crossing via stdout JSON and MinIO sidecars via read-merge-write cycles with no locking. The flat-doc path triggers triple-write: (1) `save_flat_doc` → MinIO `.flat.json` + `save_doc_meta` with non-verdict fields, (2) explicit `save_doc_meta` with verdict-only fields, (3) `_upsert_registry_row` in worker parent with verdict_fields overlay on MinIO-read base. The registry dual-write swallows all exceptions (worker.py:728-730), so Postgres genuinely under-populates relative to MinIO. The score-before-write race pattern recurred across two consecutive corpus runs (Run 15 and Run 16) on different documents.

#### Mechanism
Four structural problems: (1) `save_doc_meta` (storage.py:545) is a read-merge-write on MinIO sidecars with no locking — concurrent calls from live jobs, `reconcile_registry_drift`, and `promotion_sweep` can produce last-writer-wins for non-verdict fields. The `_verdict_cas_guard` (storage.py:515) only protects verdict fields via timestamp comparison. (2) The worker parent's `_upsert_registry_row` (worker.py:707) reads from MinIO after the child wrote, but the read may see stale data if MinIO consistency has not caught up. verdict_fields from the child's stdout JSON are overlaid on this potentially-stale base (worker.py:716-720). (3) The registry write is explicitly best-effort: "never fails the job" (worker.py:695), so a Postgres failure is swallowed and the registry diverges from MinIO. (4) The corpus-ingest-score pipeline attempts to score (read verdict) before the MinIO write from a concurrent job has completed, hitting a timing race that manifested on different documents in consecutive runs.

#### History
a. Observation #5669: score-before-write race hit cabinet_resolution in Run 16, confirming the same pattern from Run 15 (al-qarar al-tanzimi).
b. PHASE0/PHASE2_POSTPROCESS_REGISTRY_LATENCY_AUDIT: registry dual-write is non-atomic and double-gated, "job still reports success" on dual-write failure.
c. Promotion_sweep double-calls save_doc_meta (once via write_verdict for verdict, once directly for provenance).
d. RFC-009 D6's removal of MinIO fallback blocked reads when backfill_incomplete, traced to registry under-population from swallowed exceptions.

#### Code Evidence
storage.py:545-640 `save_doc_meta` (read-merge-write with no locking). storage.py:515-542 `_verdict_cas_guard` (temporal ordering for verdict fields only). worker.py:707-731 `_upsert_registry_row` (best-effort, swallows all exceptions). worker.py:716-720 verdict_fields overlay on MinIO-read base. client.py:1936-1950 flat-path double save_doc_meta (non-verdict then verdict). converters_cli.py:166-168 verdict_fields surfaced via stdout JSON for process-boundary crossing.

#### Key Files
- `src/pageindex_mcp/storage.py`
- `src/pageindex_mcp/worker.py`
- `src/pageindex_mcp/converters_cli.py`
- `src/pageindex_mcp/client.py`

#### Simplification Proposal
Collapse the three-writer pipeline (save_flat_doc's internal save_doc_meta, client.py's explicit verdict save_doc_meta, and worker.py's _upsert_registry_row with its own MinIO re-read) into a single atomic write-point per document. The child process (converters_cli) should return a complete meta dict via stdout JSON — all verdict fields, all non-verdict fields — and the worker parent performs exactly one save_doc_meta call followed by exactly one upsert_doc call, both using the same in-memory dict. This eliminates the read-merge-write race in save_doc_meta, eliminates the MinIO re-read in _upsert_registry_row, and makes the registry write fail-loud rather than best-effort.

**Restructuring steps:**
- A. client.py: merge flat-path double save_doc_meta into one call (~-20) — build one combined meta dict passed via the stdout payload; remove the two separate calls for both flat and tree paths.
- B. storage.py: remove internal save_doc_meta call from save_flat_doc (-1) — save_flat_doc becomes a pure MinIO `.flat.json` writer.
- C. converters_cli.py: expand stdout JSON payload to carry the full meta dict, not just verdict_fields (~+2 net).
- D. worker.py: replace `_upsert_registry_row`'s MinIO re-read with direct use of the meta dict from the child's stdout (~-10 net) — one save_doc_meta call, one upsert_doc call, no MinIO round-trip.
- E. worker.py: make registry write failure a surfaced job warning instead of a silent swallow (+3).
- F. storage.py: delete `write_verdict`, inline its two remaining callers (~-13 net).
- G. storage.py: add `overwrite: bool = False` param to save_doc_meta — ingestion path skips read-existing entirely, eliminating the race window (+5).

Net delta: ~-40 to -50 lines.

This would have prevented: the score-before-write race (Run 15/Run 16) — MinIO and registry now write from the same in-memory dict in sequence with no window where the sidecar is absent while the registry expects to read it; registry under-population from swallowed exceptions (now surfaced in job status); the flat-path double save_doc_meta last-writer-wins; and `_upsert_registry_row`'s stale MinIO read (no re-read occurs at all).

Migration: 3 phases — (1) delete write_verdict + surface registry failures (lowest risk), (2) merge flat-path double save_doc_meta (medium risk, single-doc validation), (3) expand stdout contract + move save_doc_meta into worker parent + add overwrite mode (highest risk, needs backward-compat guard for old-binary children and a 5+ doc corpus spot-check). Estimated effort: 2-2.5 days.

---

### Zone 6: Splitter Pattern Fragility and Giant Tail-Blob Recurrence

**Severity:** high | **Bug count:** 5

The oversized-leaf splitter (helpers.py:2895-3019) relies on a single regex (`_OVERSIZED_ORDINAL_RE`, helpers.py:2534) to recognize heading patterns for splitting giant tail-blob leaves, with three fallback tiers (ordinal → paragraph-marker → blank-line). The regex must be extended pattern-by-pattern for each new document format (Arabic articles, MOU clauses, decree parts, Roman numerals, Schedules, Annexes), and each extension risks prose false-positives. Minor formatting variants — a caption-format change, run-together markers on one line, letter-suffixed sub-clauses, or documents under the size gate with high leaf concentration — cause the splitter to silently stop producing child nodes, leaving the entire tail as a single oversized leaf.

#### Mechanism
The splitter architecture requires explicit regex patterns for every heading format across every language and document type. When a pattern is missing, the entire tail of the document (potentially hundreds of thousands of characters) becomes a single leaf node, which then fails the max_leaf_ratio check in classify_verdict. This caused the recurring "giant tail-blob" defect that affected 11+ of 25 corpus docs. Four distinct sub-causes were identified (observation #4129/#4148): (1) size-gate threshold bypass at 19,959 chars under the 50,000-char gate (fixed by RFC-015 D5a's marker detection), (2) missing "Schedule (N)" caption alternative (fixed by RFC-024 D3), (3) run-together "#######" headings on one line (unmatched), (4) letter-suffixed sub-clauses "7.10.a/b" failing digit-only check (unmatched). Each fix extends `_OVERSIZED_ORDINAL_RE` with new alternatives but cannot address the fundamental brittleness: any new heading format in any new document corpus will re-trigger the defect.

#### History
a. RFC-024 D3: extended ordinal splitter for MOU/decree Clause/Part/Arabic markers, acknowledging risk of prose false-positives.
b. Observation #4129: identified as "most common failure class in the corpus (11+ of 25 docs)."
c. Observation #4148: listed 4 distinct sub-causes per document.
d. Observation #5637: confirmed residual defect post-fix (Human Rights doc residual TOC blob).
e. RFC-005 (Fix-1): initial redesign with inline match + NFKC fold + longest-increasing-run guard split 4/5 tail-blobs fully, but Human-Rights 320k→137k remained partial. The pattern of discovering a new heading variant, extending the regex, then discovering the next variant has repeated across RFC-005, RFC-024 D3, RFC-028 D7 (Roman numeral guard).

#### Code Evidence
helpers.py:2534-2549 `_OVERSIZED_ORDINAL_RE` (12 alternatives across 3 scripts). helpers.py:2895-3019 `split_oversized_leaf_nodes` (124 lines, 3 fallback tiers). helpers.py:2971 `all_matches = list(_OVERSIZED_ORDINAL_RE.finditer(folded))`. helpers.py:2985-2993 fallback to paragraph markers when < min_segments ordinal matches. helpers.py:2955 size-gate bypass: `_has_heading_markers(text)` check. helpers.py:2964-2968 leaf_share-based blank-line fallback gate.

#### Key Files
- `src/pageindex_mcp/helpers.py`

#### Simplification Proposal
No dedicated simplification proposal was generated for this zone in this audit pass. Given the fundamental brittleness (any new heading format re-triggers the defect regardless of regex extension), a durable fix would need to move away from pattern-matching-per-format toward a structure-agnostic segmentation signal (e.g. whitespace/indentation rhythm, font-size deltas from the source PDF, or an LLM-assisted boundary detector) rather than continuing to extend `_OVERSIZED_ORDINAL_RE`. This should be scoped as a follow-up analysis.

---

### Zone 7: Silent Fallback Chains Masking Compliance and Quality Failures

**Severity:** high | **Bug count:** 5

Multiple code paths fail soft without surfacing degradation: the AGPL pymupdf4llm converter is always seeded into the fallback chain with no hard gate or logging when it fires (converters.py:3710-3711), tessdata falls back silently to Latin OCR when Arabic data is unavailable (ISS-34), the registry dual-write swallows exceptions (worker.py:728-730), and the remote Docling service can run a stale copy without the local BiDi guard. These silent fallbacks mean job success masks compliance violations (AGPL exposure under Hard Rule 4) and quality failures (false-clean Latin mojibake from tessdata fallback passing validate_tree's garble gate).

#### Mechanism
The converter chain (converters.py:3669-3729) always includes pymupdf4llm when `ALLOW_AGPL_FALLBACK=true` (default). When remote Docling 504s (e.g. 161-page PDF cc4533aa), the chain silently falls through to pymupdf4llm with no counter/alert or persisted routing decision. `ALLOW_AGPL_FALLBACK` gates 6+ code paths (picture recovery, landscape probe, landscape reextract, page rotation, chunked Docling, pymupdf4llm converter), but its default is true and it has no runtime alerting. The tessdata fallback (ISS-34: `ensure_tessdata`) silently falls back to [deu,eng] Latin OCR when Arabic tessdata is unavailable, producing false-clean Latin mojibake that passes the garble gate (the marsoom-13 failure mode). The remote Docling route drift (BIDI_ROOT_CAUSE_RFC033) showed a fix existing in the repo's working tree but unreachable in production because the remote service ran a stale copy and worker never re-normalized markdown from the remote route.

#### History
a. BIDI_ROOT_CAUSE_RFC033 section 1.3 C-2: pymupdf4llm always seeded in chain, "Under Hard Rule 4 this must be closed regardless of whether it fired."
b. ISS-34: tessdata silent Latin fallback caused marsoom-13 false-clean failure.
c. BIDI_ROOT_CAUSE_RFC033 section 1.1-1.3: D2 Part A guard unreachable on remote route ("uncommitted and the worker never re-normalizes markdown returned over the remote route").
d. Registry dual-write failure masked by worker.py:728 swallow pattern, confirmed by PHASE0/PHASE2_POSTPROCESS_REGISTRY_LATENCY_AUDIT.

#### Code Evidence
converters.py:3710-3711 `chain.append(('pymupdf4llm', _pdf_to_markdown_no_pics))` unconditionally when ALLOW_AGPL_FALLBACK. config.py:35-37 `ALLOW_AGPL_FALLBACK` default true. converters.py:2260-2268 picture recovery gated on ALLOW_AGPL_FALLBACK. worker.py:728-730 `except Exception` swallow in `_upsert_registry_row`. config.py:27-29 `REMOTE_MD_RENORMALIZE` (safety net for remote stale-copy drift).

#### Key Files
- `src/pageindex_mcp/converters.py`
- `src/pageindex_mcp/config.py`
- `src/pageindex_mcp/worker.py`

#### Simplification Proposal
No dedicated simplification proposal was generated for this zone in this audit pass. A follow-up should scope: (1) a hard gate + alert/counter on pymupdf4llm chain-fallback firing (closing Hard Rule 4 exposure regardless of whether it fires), (2) surfacing tessdata Latin-fallback as a flagged low-confidence result rather than a silent pass, and (3) making the worker.py:728 registry-write swallow fail loud (consistent with Zone 5's Step E proposal, which already addresses this).

---

### Zone 8: Duplicated Threshold/Logic Definitions Across Files

**Severity:** medium | **Bug count:** 4

The same semantic concepts are defined independently in multiple files with different values and different logic, causing fixes in one location to not apply in the other. `_RFC029_MIN_CHARS_PER_NODE` is defined as 150 in helpers.py (Gate 9) and 500 in client.py (flat-prefer logic), reading the same env var name but with different defaults. Two separate pipe-table detection functions (`_flat_is_pipe_row` at helpers.py:2446 checks `'|' in line`, while `_is_pipe_row` at helpers.py:3057 checks startswith/endswith `'|'`) use different logic for the same concept. Three nearly identical heading-injection functions (converters.py:123-249) share the same split-check-match-prepend pattern with different regexes. The `_has_structural_depth` proxy (converters.py:828) duplicates validate_tree's Gate 2+3 node_count/depth checks using a markdown-level approximation that can disagree with the real tree structure.

#### Mechanism
When the same concept is defined in two places, a fix to one definition does not propagate to the other. The `_RFC029_MIN_CHARS_PER_NODE` divergence means Gate 9 in validate_tree fires at 150 chars/node while the flat-prefer logic in client.py uses 500 chars/node — a doc with 200 chars/node passes Gate 9 but triggers flat-prefer. The pipe-table detection divergence means `_flat_is_pipe_row` matches any line containing `'|'` (including prose with pipes), while `_is_pipe_row` requires the line to start and end with `'|'`. A table row like `'value | other'` would be matched by one but not the other, producing inconsistent parsing in flat extraction vs table segmentation. The heading-injection duplication (3 functions, ~130 lines) means a bug fix or improvement to the injection pattern must be applied three times or the fix drifts.

#### History
a. ISS-36 root-cause finding: digit-ratio garble check floor (500 chars) duplicated in two functions with no shared helper, meaning a fix landed in one function was not guaranteed in the other.
b. OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION: shared `_OCR_ESCALATION` kill-switch simultaneously gated two independent OCR paths, "toggling the flag to control one behavior inadvertently disabled the other."
c. The `_has_structural_depth` proxy false-negatives meant better source candidates were never selected for validation (code map observation confirmed).

#### Code Evidence
helpers.py:1519 `_RFC029_MIN_CHARS_PER_NODE = float(os.environ.get('RFC029_MIN_CHARS_PER_NODE', '150'))`. client.py:433 `_RFC029_MIN_CHARS_PER_NODE = float(os.getenv('RFC029_MIN_CHARS_PER_NODE', '500'))`. helpers.py:2446 `_flat_is_pipe_row: return '|' in line and line.strip() != ''`. helpers.py:3057-3059 `_is_pipe_row: return s.startswith('|') and s.endswith('|') and len(s) > 1`. converters.py:123-249 three heading-injection functions (Arabic/German/English). converters.py:828-833 `_has_structural_depth` proxy vs helpers.py:1655-1674 Gate 2+3.

#### Key Files
- `src/pageindex_mcp/helpers.py`
- `src/pageindex_mcp/client.py`
- `src/pageindex_mcp/converters.py`

#### Simplification Proposal
No dedicated simplification proposal was generated for this zone in this audit pass. The general remedy pattern is single-sourcing: promote `_RFC029_MIN_CHARS_PER_NODE`, the pipe-row detector, and the heading-injection pattern into one shared constant/function each (in helpers.py, imported by client.py and converters.py), eliminating independent redefinition. This is a low-risk, mechanical follow-up.

## Cross-Cutting Themes

- Threshold widening as symptom management: `PASS_MAX_LEAF_RATIO` ratcheted 0.15→0.17→0.20→0.30 across RFC-021/023/024 to absorb Docling non-determinism jitter, each widening explicitly acknowledged as a recurrence needing hysteresis (not more widening) and eventually also broke the pinned unit-test suite (test_verdict_d1.py) that had itself been corrected once before (2026-07-16).
- Incomplete implementation marked complete leaves dead code: dynamic-timeout calculation (RFC-027 D7), bidi-coherence check (RFC-029 D0, duplicated and uncalled), and judge calibration rules (RFC-029 D6) were all built but never wired into the execution path, only discovered on the next audit.
- Picture/OCR recovery evolved from all-or-nothing guards to graceful degradation across three RFC generations: RFC-019/020 binary count-check → RFC-023 D1 ordinal-matched splicing → RFC-024 D2 per-region try/except isolation — each generation fixing the prior's catastrophic-failure mode.
- Garble/script detection is a whack-a-mole surface: Latin gibberish → expected_script threading → image-marker stripping → PUA/consonant-run checks, with each fix closing one blind spot while leaving RTL word-splitting, embedded Latin mojibake inside Arabic sentences, and canonical-order (vs presentation-form) BiDi reversal undetected for months, only surfaced by manual pattern scans.
- New validation gates shipped without corresponding recovery/routing wiring cause cascading PASS→ERROR/FAIL regressions: RFC-029's four new validate_tree failure reasons had no client.py recovery path (3 PASS→ERROR, 1 FAIL→ERROR); RFC-026 gate hardening surfaced 12 pre-existing masked defects in one run (0 improvements, 12 regressions).
- Silent fallbacks and swallowed exceptions hide real degradation behind reported success: OCR-escalation double-firing, tessdata's silent Latin fallback, registry dual-write's non-fatal swallow-and-log, and the ungated AGPL (pymupdf4llm) fallback chain all fail soft, so job success masks compliance or quality failures.
- Duplicated logic without single-sourcing causes fix drift: the 500-char digit-ratio garble floor exists identically in two functions with no shared helper (ISS-36); the `_OCR_ESCALATION` kill-switch simultaneously (and unintentionally) gates two independent OCR paths; PDF images get per-picture enrichment while standalone image files bypass it entirely.
- Downstream metric blind spots cause spurious verdict flips independent of actual content quality: table blocks are invisible to `block['text']`-based char counts (4,267 vs apparent 375 chars), and RFC-029 D3's fence/HR stripping silently lowered flat_char_count, both feeding a judge/gate that then mis-scores unchanged or improved content (e.g. Reitlehrer PASS→MARGINAL in Run 13).
- Splitter heading-pattern recognition is fragile to minor formatting variants: it correctly emits per-article nodes for the first N headings then silently stops on a caption-format change, run-together markers, or letter-suffixed clauses, producing giant tail-blob leaves across 11+ of 25 corpus docs and requiring repeated RFC-024 D3-style pattern-specific extensions.
- Async pipeline race conditions recur across independently-diagnosed subsystems: registry dual-write happens strictly after MinIO success with no coordination, and the same score-before-write race pattern hit two different documents across consecutive corpus runs (Run 15, Run 16), indicating a systemic timing bug rather than isolated incidents.
- OCR escalation pathway design bifurcated and then had its rasterization backend SPOF decoupled from the VLM backend across RFC-023/024, while remote-vs-local code drift (stale Docling service copy missing an uncommitted local guard) shows fixes can be entirely unreachable in production despite existing in the repo.
