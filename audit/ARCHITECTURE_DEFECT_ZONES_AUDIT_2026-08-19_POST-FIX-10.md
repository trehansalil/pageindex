# Architecture Defect Zones Audit — 2026-08-19 POST-FIX-10

**Date:** 2026-08-19
**Sources:** 18 history miners, 1 code maps

## Summary Table

| # | Zone | Severity | Bug Count | Key Files |
|---|---|---|---|---|
| 1 | GATE_TABLE to Recovery Dispatch Reason-Code Coupling | critical | 12 | helpers.py, client.py |
| 2 | Picture/OCR Enrichment and Page-Level Escalation Conflation | critical | 12 | picture_plane.py, converters.py, client.py |
| 3 | Garble Detection Heuristic Patchwork | critical | 12 | helpers.py, converters.py, client.py |
| 4 | Verdict Threshold Oscillation and Hysteresis Failure | high | 9 | helpers.py, storage.py |
| 5 | Config Snapshot Freeze Drift and Incomplete Wiring Enforcement | high | 8 | config.py, helpers.py, storage.py |
| 6 | Cross-Process Error Classification Boundary | critical | 7 | worker.py, client.py, job_status.py |
| 7 | Mutable ExtractionState Recovery Path Ordering | high | 7 | client.py, helpers.py, converters.py |

## Zone Details

### Zone 1: GATE_TABLE to Recovery Dispatch Reason-Code Coupling

**Severity:** critical | **Bug count:** 12

#### Mechanism
New gate defect reasons are added to GATES (helpers.py) without updating the recovery dispatch in client.py. Each new reason string falls through every if/elif recovery check to the default raise LowQualityTreeError, turning a recoverable document into a terminal failure. Fixes that add the new reason to one recovery path (e.g., OCR escalation) miss the others (VLM fallback, flat routing). Gate evaluation ordering (garble check after node_count/depth early-exit) means the same document gets different reason codes depending on which gate fires first, and recovery routing only handles specific reasons. This is a typed-contract gap: GateSpec defines recovery_tag but client.py's _recovery_dispatch consumes it via a dict of lambdas that each self-gate with early returns, so adding a gate with recovery_tag='ocr_escalation' runs the dispatch loop but the per-reason eligibility inside _recover_ocr_retry (client.py:1353-1382) silently rejects it if the first_defect enum value is not in its hardcoded set.

#### History
a. RFC-004 D1: disabled validation rejection for node_count<3/depth<2, creating the gap exploited by RFC-025/026/029/030.
b. RFC-018 D3b: added 'node_garbling' reason, never matched by any of 3 recovery triggers.
c. RFC-025 D3: extended triggers to match ('garbling','node_garbling') but still missed node_count<3 early-exit before garble check.
d. RFC-026 D5: moved garble check before early-exit.
e. RFC-029 D1-D2: added 4 new reasons (suspect_density, low_content_density, empty_node_contamination, arabic_low_content_ratio), none wired into recovery routing -- 3 PASS-to-ERROR regressions (Penal Code, federal_decree_law_no_33, marsoom-33) plus 1 FAIL-to-ERROR (warid-597).
f. RFC-030 D2: wired the 4 reasons.
g. RFC-016 D4/D5: VLM fallback gated only on reason=='garbling', bypassed for shallow-tree scanned Arabic.
h. RFC-023 D11: garble-aware exemption causes structural failure reasons instead of garbling, OCR escalation never fires.
i. RFC-036 D3: 'rtl_reversal' hit terminal-raise list instead of flat-routing whitelist.
j. RFC-027 D2 / RFC-028 D4: OCR escalation unconditional md_content overwrite (al-qarar 230-to-123 chars).
k. RFC-023 D3/Run 9: garble detected but no escalation hook.

#### Code Evidence
helpers.py:1952-1969 GATES list (verified: 10 active GateSpecs with recovery_tag on gates 0-3,5). helpers.py:2198-2264 validate_tree iterates GATE_TABLE, returns fired[0] as primary defect. client.py:1328-1570 _recover_ocr_retry dispatches on OcrRetryReason with per-reason eligibility checks at lines 1358-1382 (GARBLE and LOW_CONTENT both gated on _OCR_ESCALATION_GARBLE, confirmed). client.py:1657-1721 _recover_vlm_fallback gates on state.first_defect in (GARBLING, NODE_GARBLING) only. helpers.py:2486-2711 compute_verdict FLAT_GATE_SUBSET re-derives decide_rtl independently from validate_tree's cached rtl_decision.

#### Key Files
- src/pageindex_mcp/helpers.py
- src/pageindex_mcp/client.py

#### Simplification Proposal
Move the per-reason eligibility logic out of client.py's `_recover_ocr_retry` / `_recover_vlm_fallback` and into the GateSpec itself as a declarative `recovery_eligible(state) -> bool` predicate. The recovery dispatch loop in client.py becomes a pure iterator over GATES: for each fired defect whose GateSpec has a recovery method, call `gate.recovery_eligible(state)` then `gate.recover(self, state, ...)`. This eliminates the two-site maintenance contract -- adding a new gate with recovery semantics requires editing only the GateSpec declaration in helpers.py (the predicate + the recovery coroutine reference), never client.py's dispatch or eligibility checks.

Concrete restructuring:
- Step 1 -- Extend GateSpec with recovery contract fields (helpers.py, +30 lines): add `recovery_eligible: Callable[[ExtractionState], bool] | None = None` and `recovery_fns: tuple[str, ...] = ()` replacing `recovery_tag`; add import-time assertion that every gate with `policy in (RETRY_OCR, RETRY_RTL)` has non-empty recovery_fns and eligible predicate.
- Step 2 -- Split `_recover_ocr_retry` (250 lines, OcrRetryReason multiplexed) into `_recover_garble_ocr`, `_recover_low_content_ocr`, `_recover_image_dominant_ocr` (client.py, net -80 lines); delete OcrRetryReason enum; `_recover_vlm_fallback` loses its hardcoded defect-type guard.
- Step 3 -- Replace `_recovery_dispatch` dict with GateSpec-driven loop (client.py, net -40 lines); delete `_seen_tags` dedup; contract becomes structural rather than dict-key based.
- Step 4 -- Add exhaustiveness assertion spanning both modules (helpers.py, +5 lines): `set(g.defect for g in GATES if g.recovery_fns) == set(g.defect for g in GATES if g.policy in (RETRY_OCR, RETRY_RTL))`.

Rough delta: helpers.py +35, client.py -120 (net ~-85 lines).

Historical bug classes prevented: RFC-029 D1-D2 (new reasons without recovery wiring would crash at import time instead of regressing 3+ docs); RFC-018 D3b (node_garbling gate would require its own eligibility predicate); RFC-036 D3 (rtl_reversal eligibility becomes sole authority, not a separate list); RFC-016 D4/D5 (VLM declared per-gate rather than hardcoded to first_defect check); RFC-023 D11 (eligibility checks all_defects intersection rather than just first_defect, resilient to primary-defect masking).

Migration risk: medium-low, purely structural. Sequence: (1) additive GateSpec fields + assertion, zero behavior change; (2) shadow mode -- populate predicates alongside old dict, log divergences without acting; (3) switchover -- replace dict loop, delete OcrRetryReason, split methods, run full corpus regression; (4) cleanup -- remove recovery_tag, old dedup, shadow assertion. Primary regression risk is the shared keep-best heuristic between GARBLE and LOW_CONTENT -- extract into a shared `_keep_best_or_revert` helper (~60 lines, neutral to line count) rather than duplicating.

Estimated effort: 3.5 days total (Step 1: 0.5d, Step 2: 1d, Step 3: 1.5d, Step 4: 0.5d), with corpus regression suite run after steps 2 and 3.

---

### Zone 2: Picture/OCR Enrichment and Page-Level Escalation Conflation

**Severity:** critical | **Bug count:** 12

#### Mechanism
Filters added to one subsystem (page-coverage skip, text-layer clip-text probe) interact with the other subsystem's assumptions to produce zero-output: the coverage skip exempts full-page regions from per-picture OCR but scanned PDFs ARE full-page pictures, so zero text is recovered, causing tree-to-flat collapse. The text-layer probe skips per-picture OCR when >20 chars of clean text exist anywhere on the page (headers/footers/page numbers), making clip_text capture unreachable for full-page regions. Forced-OCR from the escalation subsystem changes Docling's PictureItem classification (reclassifies as TextItems), producing 0 PictureResults and breaking the enrichment subsystem's splice_figure_markers count guard. Each individually-reasonable filter combines with others to produce emergent zero-output states that neither subsystem detects.

#### History
a. RFC-015 D6: per-picture OCR conflated with page-level escalation gate (OCR text reclassified from prose to image blocks).
b. RFC-017 D0: >60% page-coverage skip too broad for scanned PDFs.
c. RFC-018/019 D0: coverage skip produced zero recovered text, tree-to-flat collapse for 5+ Arabic scanned PDFs.
d. RFC-019 D1: clip-text probe combined with D0 coverage filter zeroed out enrichment on docs 3,9 (1/4-to-0/3, 3/60-to-0/60).
e. RFC-019 D0: Python list multiplication shared PictureResult references, pop() stripped bytes from all siblings.
f. RFC-020 F2: forced-OCR side effect -- Docling reclassifies PictureItems as TextItems, 0 PictureResults, tree-to-flat for docs 7,17,20,21.
g. RFC-024 D1: clip_text capture never runs on full-page regions (Human-Rights 503k-to-382 chars).
h. RFC-020 F1 to RFC-023 D0: _text_layer_has_content not garble-aware.
i. RFC-023 D1: splice_figure_markers count mismatches.
j. RFC-023 D0 Run 8 regression: 5 docs reverted to 0 chars/ERROR.
k. RFC-034 D19: boilerplate displacement of real OCR digits/labels (pie chart).
l. OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION: single _OCR_ESCALATION flag gates both concerns.

#### Code Evidence
picture_plane.py:326-348 decide_ocr_mode (verified: pure function, 3 branches). client.py:1071-1075 call site 1 (has_image_markers hard-coded False, pre-markdown). converters.py:2630-2632 call site 2 (has_image_markers from real content, post-markdown). picture_plane.py:251-318 _classify_region 4-stage picture gate. client.py:1358 and 1363 _recover_ocr_retry: both GARBLE and LOW_CONTENT gated on same _OCR_ESCALATION_GARBLE flag (confirmed).

#### Key Files
- src/pageindex_mcp/picture_plane.py
- src/pageindex_mcp/converters.py
- src/pageindex_mcp/client.py

#### Simplification Proposal
Replace the two competing OCR subsystems (page-level escalation in client.py and per-picture enrichment in converters.py) with a single-writer `OcrDecision` dataclass produced once by a unified decision function that takes the complete document state (text-layer presence, garble status, coverage metrics, image-marker count) and emits a sealed instruction -- exactly one of: no-OCR, full-page-OCR, or per-picture-OCR -- along with the language list and a `full_page_already_applied` flag. Both call sites (client.py:1075 pre-markdown and converters.py:2630 post-markdown) collapse into a single decision point invoked after the primary converter returns, when all inputs are known, eliminating the temporal ordering dependency. `_recover_ocr_retry` becomes a pure re-extraction step that stamps `full_page_already_applied=True`, which `_recover_picture_results` reads to short-circuit -- closing the unwired re-entry guard (`force_full_page_ocr_applied` defaults to False at line 3634, never set by any caller after a retry).

Concrete restructuring steps:
| Step | File(s) | What | Line delta |
|---|---|---|---|
| A | picture_plane.py | Add `OcrDecision` frozen dataclass; merge decide_ocr_mode into `decide_ocr_strategy(...)` encoding all 3 flag checks in one place; delete 3-arg decide_ocr_mode | ~+25 net |
| B | client.py:1071-1082 | Delete pre-markdown call (site 1); move OcrDecision production to post-converter; thread through ExtractionState | ~-4 net |
| C | client.py:1328-1570 | Set full_page_applied=True after successful retry; remove 3 independent flag-gate checks; read from state.ocr_decision.mode | ~-20 net |
| D | converters.py:2624-2635 | Delete second decide_ocr_mode call (site 2); _recover_picture_results takes ocr_decision param; replace force_full_page_ocr_applied bool | ~-4 net |
| E | converters.py:3634 | Thread ocr_decision through _post_fallback_and_picture_recovery, closing unwired re-entry guard | ~+2 net |
| F | config.py | No flag-definition change; flags become inputs to decide_ocr_strategy | 0 |
| G | Tests | Update callers to decide_ocr_strategy; add test that full_page_applied=True prevents per-picture OCR (currently untested) | ~+30 net |

Estimated total: +30 net lines (mostly tests); ~50 deleted, ~80 added across dataclass + function + tests.

Historical bug classes prevented: RFC-017/018/019 D0 (coverage-skip zeroing scanned PDFs -- text_layer_present=False emits FULL_PAGE directly, bypassing coverage gate); RFC-019 D1 (clip-text probe and coverage gate evaluated together against same state, not two temporal phases); RFC-020 F2 (full_page_applied structurally threaded, closing unwired re-entry guard); RFC-023 D0 Run-8 regression (single decision point eliminates classification-assumption mismatch); RFC-024 D1 (coverage and clip-text gates evaluated together, cannot silently combine to zero output); temporal has_image_markers=False vs real markers (eliminated by single post-conversion decision).

Migration risk: moderate (touches hottest files). Sequence: (A) safe additive dataclass + wrapper delegation, zero behavior change; (B) client.py call site 1 replacement + ExtractionState threading, full corpus regression; (C) converters.py call site 2 replacement + force_full_page_ocr_applied deletion, full corpus regression; (D) cleanup -- delete decide_ocr_mode and scattered config-flag reads. Each phase independently deployable; rollback via `USE_LEGACY_OCR_DECISION` flag during transition.

Estimated effort: 2-3 days implementation + 1 day corpus regression validation (Phase A ~0.5d, B ~1d, C ~0.5d, D ~0.5d, regression runs ~1d total across phases).

---

### Zone 3: Garble Detection Heuristic Patchwork

**Severity:** critical | **Bug count:** 12

#### Mechanism
Each new garble heuristic is calibrated against one known-bad document and then routinely over-fires (Arabic Presentation Forms >50% rejected valid content) or under-fires (sparse mixed-script mojibake escapes all existing prongs) on the rest of the corpus. The encoding-range mismatch pattern recurs structurally: RFC-033 D2's reversed-morphology detector checks Presentation Forms codepoints (U+FB50-FEFF) that upstream NFKC normalization has already decomposed to base Arabic (U+0600-06FF), producing 0% true-positive-rate. The same pattern recurs in the Latin-gibberish expected_script threading gap: _script_from_filename returns None for German filenames, expected_script defaults to None/'Latn', so the Latin-gibberish detector's scope condition (expected_script != 'Latn') never fires. Garble detection inspects node.text but never node.title, leaving reversed/corrupted titles permanently invisible to all gates.

#### History
a. RFC-010 D3/D3B: token-repetition duplicated into _tree_is_garbled and _flat_text_is_garbled independently (fragmentation risk flagged by RFC-013 D7).
b. RFC-015 D8: sparse mixed-script mojibake coverage gap.
c. RFC-019 D2: expected_script parameter never passed by main callers, Latin-gibberish check unreachable for warid-597.
d. RFC-020 F2: filename-derived expected_script caused new forced-OCR regression.
e. RFC-028 D2: Arabic Presentation Forms >50% rejected valid content (huquq al-insan FAIL-to-ERROR).
f. RFC-028 D5: filename-based Arabic lang detection diluted garble ratio (warid-597 MARGINAL-to-PASS).
g. RFC-029 D0/RFC-030 D5: _check_bidi_coherence implemented as dead code, never called.
h. RFC-033 D2: bidi/reversed-morphology 0% TPR due to NFKC ordering.
i. RFC-013/RFC-015: Latin-gibberish scope gap for German filenames (Haftpflicht 81/132 garbled nodes FAIL-to-PASS via four compounding gaps).
j. RFC-030 D4: garble gate ignores node.title (siyasat-hawkama 23/24 reversed titles undetected).
k. RFC-027 D3/RFC-028 D3: RTL readability scoring only 14 common words, siyasat hawkama 100% reversed stored PASS.
l. obs #5627: RTL word-splitting and embedded Latin OCR fragments escape all heuristics.

#### Code Evidence
helpers.py:2198-2264 validate_tree (verified: iterates GATE_TABLE exhaustively, fires all 10 gates). helpers.py:1952-1969 GATES list (verified: GARBLING severity=0, NODE_GARBLING severity=3, both with recovery_tag='ocr_escalation'). config.py:296-357 effective_config_snapshot (verified: rereads GARBLE_LATIN_GIBBERISH_ENABLED, GARBLE_LATIN_RATIO, GARBLE_NODE_RATIO_THRESHOLD fresh from os.environ on every call). helpers.py:422-427 _get_verdict_thresholds (verified: lazy singleton, never invalidated in production).

#### Key Files
- src/pageindex_mcp/helpers.py
- src/pageindex_mcp/converters.py
- src/pageindex_mcp/client.py

#### Simplification Proposal
Replace the 8+ independently-calibrated garble prongs (each reading raw env vars, each with its own script-inference fallback, each calibrated to one document) with a single `GarbleReport` dataclass returned by one function `detect_garble(text, script_context: ScriptContext)` that runs all checks in a fixed order against a single, pre-validated `ScriptContext` value computed exactly once per document at index entry (client.py line 2153) by combining filename-derived script AND text-inferred script into one frozen object carrying `dominant_script`, `had_presentation_forms`, `source`. This eliminates the bug-generating mechanism: no prong self-infers script, no prong reads its own env vars, and the NFKC ordering problem disappears because the presentation-forms signal is captured before any normalization runs.

Concrete restructuring steps:
- Step A -- New `ScriptContext` dataclass (script_context.py, ~40 lines). Factory `from_document(filename, raw_text)` calls _script_from_filename then _infer_script fallback, scans presentation forms before NFKC. Consolidates 4 scattered inference sites (client.py:2153, check_garble:1517, TreeSignals.from_tree:472, _garble_check_nodes:1685-1697).
- Step B -- Consolidate 12 scattered os.environ reads into a frozen `GarbleConfig` (helpers.py, ~-20 net lines), invalidated by reset_verdict_thresholds(); tests monkeypatch the dataclass, not env vars.
- Step C -- Unify garble_prongs + check_garble into `detect_garble(text, ctx, config) -> GarbleReport` (helpers.py, ~-50 net lines); delete check_garble, garble_prongs, BULK_PROFILE, FLAT_MARKDOWN_PROFILE, GarbleProfile, _garble_ratio as separate public APIs; profile distinction becomes a BlobKind parameter.
- Step D -- Thread ScriptContext through validate_tree and all gate functions in place of `expected_script: str | None` (helpers.py, ~-20 net lines); _gate_node_garbling stops per-node re-inferring script.
- Step E -- Extend _garble_check_nodes to inspect node.title using the unified API (already partially implemented; net 0).
- Step F -- Delete dead code: _check_bidi_coherence, _has_sparse_mojibake, _GARBLE_SHORT_TEXT_DEFAULT, _GARBLE_FLAT_MARKDOWN_NORMALIZE reads (~-15 lines).

Total estimated delta: approximately -65 net lines across helpers.py, client.py, and new script_context.py.

Historical bug classes prevented: RFC-019 D2/RFC-020 F2 (Latin-gibberish unreachable for German filenames -- ScriptContext resolves script once correctly); RFC-028 D2 (Presentation Forms >50% false rejection -- signal captured pre-NFKC, not re-scanned post-normalization); RFC-033 D2 (reversed-morphology 0% TPR from NFKC ordering -- had_presentation_forms computed pre-NFKC); RFC-029 D0/RFC-030 D5 (_check_bidi_coherence dead code -- eliminated by single entry point); RFC-013 D7 (token-repetition duplication -- one implementation per prong); RFC-028 D5 (filename-vs-text script conflict resolved once with explicit precedence); RFC-030 D4 (garble gate ignoring node.title -- unified detect_garble called on both text and title).

Migration risk: moderate (garble detection is critical path for every ingestion). Sequence: (1) Step A, pure addition, zero risk; (2) Step B, pure refactor of env-var reads, snapshot-test verified thresholds identical; (3) Step C, highest risk -- keep old check_garble as thin wrapper for one release cycle, property-based test asserting old-vs-new equivalence across full corpus, corpus diff; (4) Step D, mechanical signature threading, enforced by _GateFn type alias at import time; (5) Steps E+F, low risk, mostly deletion.

Estimated effort: 3-4 days (Step A 0.5d, B 0.5d, C 1-1.5d including property tests and corpus validation, D 0.5d, E+F 0.5d); corpus scoring runs add ~0.5d wall-clock across the sequence.

---

### Zone 4: Verdict Threshold Oscillation and Hysteresis Failure

**Severity:** high | **Bug count:** 9

#### Mechanism
Each threshold widening is calibrated to a specific failing document. The widened threshold then admits a different document that was previously correctly rejected. The next RFC narrows or re-widens, creating an oscillation cycle. The hysteresis mechanism (prior-verdict anchoring) was added to break this cycle but has a structural flaw: the corpus reingestion pipeline wipes all processed/ objects before reingesting, so find_prior_verdict always returns 'no prior verdict', and the stabilization mechanism never fires. Separately, synthetic-structure promotion (RFC-022 B1-Fix) only triggers when flat_structure is completely empty, missing documents with non-empty but rejected structures. ToC-heading stripping has no depth guard, so it over-strips legitimate structural headings on long legal documents, collapsing depth and triggering MARGINAL verdicts.

#### History
a. RFC-023 D10: PASS_MAX_LEAF_RATIO widened 0.17-to-0.20, Haftpflicht-Besondere jittered past.
b. RFC-024 D0: widened 0.20-to-0.30, predicted own recurrence (risk table called for hysteresis).
c. RFC-025 D0: hysteresis implemented but depends on wiped MinIO store, GHV-TKV-Tarif PASS-to-MARGINAL on byte-identical tree.
d. RFC-022 B1-Fix: synthetic structure promoted placeholder-only docs to PASS (doc 21 Domestic Workers: 15 blocks of bare image markers, 210 chars, verdict PASS).
e. RFC-022 B1-Fix: guard only triggers when flat_structure completely empty (doc 20 missed).
f. RFC-029: low_content_density 500 chars/node calibrated to marsoom-13 (~200), rejected 3 legitimate trees (Penal Code 408.2, decree_33 54.3, marsoom-33 459.4).
g. RFC-029 D6: judge-calibration rules designed but never written to SKILL.md, phantom regressions persisted.
h. RFC-034 D11/D16: ToC-heading stripping over-stripped Penal Code (depth 3-to-2, 493/595 nodes flattened to top level, PASS-to-MARGINAL).
i. RFC-034 D16: guarded fix incomplete, Federal Decree-Law (47) 88% bodyless headings.

#### Code Evidence
helpers.py:2486-2711 compute_verdict (verified: complexity 28, 226 lines, Phase 1 GATE_EVALUATION + HARD_FAILs then Phase 2 PROMOTIONS with image-enrichment rescue before max_leaf_ratio). helpers.py:422-427 _get_verdict_thresholds (verified: lazy singleton from VerdictThresholds.from_env(), cached globally, reset_verdict_thresholds has zero production callers). helpers.py:3335-3354 prepare_tree (verified: runs split_oversized_leaf_nodes then _segment_table_nodes, unconditionally, fixed order).

#### Key Files
- src/pageindex_mcp/helpers.py
- src/pageindex_mcp/storage.py

#### Simplification Proposal
Replace the monolithic compute_verdict (226 lines, complexity 28) with a two-stage pipeline where gate evaluation and promotion/cap logic are separate pure functions that each return a typed intermediate result, and replace the dead hysteresis mechanism (find_prior_verdict has zero production callers) with a deterministic per-document verdict ledger persisted in a dedicated MinIO prefix (`verdicts/`) that is never wiped by reingestion, eliminating the snapshot-before-wipe race entirely.

Concrete restructuring steps:
- Step A -- Extract `evaluate_gates(structure, validate_result, flat, expected_script) -> GateOutcome` from Phase 1 (helpers.py, net -20 lines).
- Step B -- Extract `apply_promotions(gate_outcome, content_class, thresholds, ...) -> VerdictResult` from Phase 2, each promotion rule a named check with early return (helpers.py, net -60 lines).
- Step C -- Rewrite compute_verdict as a ~15-line dispatcher calling evaluate_gates then apply_promotions (net across A+B+C: ~-80 lines, complexity drops 28 -> ~4/10/12).
- Step D -- Replace hysteresis with persistent verdict ledger: delete find_prior_verdict (70 lines) and snapshot_prior_verdicts (52 lines); write `verdicts/{sha256}.json` after every save_doc_meta, excluded from wipe_processed; wire into client.py's two compute_verdict call sites (net -92 lines).
- Step E -- Decouple LEAF_SPLIT_RATIO from PASS_MAX_LEAF_RATIO with an explicit assertion `pass_max_leaf_ratio <= leaf_split_ratio` in from_env() (+5 lines).

Total estimated delta: -167 net lines.

Historical bug classes prevented: RFC-023/024 oscillation (verdict ledger anchors byte-identical content at prior PASS without widening thresholds); RFC-025 hysteresis structural flaw (verdicts/ prefix never wiped, no snapshot machinery needed); RFC-022 B1-Fix false PASS (apply_promotions makes synthetic-structure promotion individually unit-testable); RFC-029 threshold whack-a-mole (gate evaluation separated from promotion -- low_content_density enforced structurally as non-overridable hard-fail); RFC-034 D11/D16 ToC over-stripping (not directly prevented, but separated evaluate_gates surfaces depth-related firings in unit tests rather than corpus reingestion).

Migration risk: medium-low. Sequence: (1) Step E first, zero risk, prevents miscalibration during migration; (2) Steps A+B+C together as one commit -- pure refactor, exhaustive parameterized old-vs-new corpus comparison before deleting old code; (3) Step D last -- dual-write ledger alongside old snapshot for one full corpus cycle, then switch reads, then delete snapshot machinery.

Estimated effort: 1.5-2 days (A+B+C: 3-4h implementation + 2-3h corpus-coverage tests; D: 2-3h implementation + 1h dual-write verification; E: 15min), deployable across 3 commits.

---

### Zone 5: Config Snapshot Freeze Drift and Incomplete Wiring Enforcement

**Severity:** high | **Bug count:** 8

#### Mechanism
Config drift: effective_config_snapshot (config.py:296-357) rereads env vars like GARBLE_NODE_RATIO_THRESHOLD, RFC029_FLAT_PREFER_MULTIPLIER, RFC029_MIN_CHARS_PER_NODE, PASS_MAX_LEAF_RATIO fresh from os.environ on every call. But helpers.py freezes these same vars at import (module constants) or first-call (_verdict_thresholds_cache). If env changes mid-process-lifetime, the persisted audit record reports one value while the pipeline used another. Wiring enforcement: validate_feature_wirings (helpers.py:2092-2187) is registered only via atexit.register (line 2195); neither server.py nor worker.py call it explicitly. FEATURE_WIRINGS covers only 4 cross-module features and was never applied to the gate subsystem's own GATES list-order vs severity drift or the dual RtlDecision computation sites. The HR2 erasure cascade is fully implemented and tested but unreachable from any production code path.

#### History
a. RFC-027 D7: chunked_docling_timeout function created but never imported/called by worker.py (task marked complete, only wired by RFC-028 D0).
b. RFC-029 D0/RFC-030 D5: _check_bidi_coherence dead code, never called.
c. RFC-031 shadow mode to RFC-032 activation: PDF-inspector classification computed and logged but never branched on until wired.
d. RFC-034 D19: enrichment fix existed as uncommitted git-staged diff through entire audit cycle.
e. Remote Docling service code predates locally-committed bidi-heading guard, no client-side re-normalization of remote results.
f. AGPL fallback chain: remote Docling failure silently walks to pymupdf4llm with no hard gate (Hard Rule 4 violation).
g. storage.delete_doc in_degree=0: HR2 cascade has zero production entrypoints (CLAUDE.md Hard Rule 2 depends on operators knowing to invoke it).

#### Code Evidence
config.py:296-357 effective_config_snapshot (verified: rereads 25+ fields from os.environ fresh on every call, including garble_node_ratio_threshold, pass_max_leaf_ratio, rfc029_flat_prefer_multiplier, rfc029_min_chars_per_node). helpers.py:422-427 _get_verdict_thresholds (verified: lazy singleton, never invalidated in production). helpers.py:2054-2089 FEATURE_WIRINGS (verified: 4 entries only -- pdf_inspector shadow_only=True, chunked_docling_timeout, picture_ocr_enrichment, zdr_egress_gate). storage.py:291-451 delete_doc (verified: 161 lines, 7-step cascade). trace_path(delete_doc, inbound): callers=[] confirmed.

#### Key Files
- src/pageindex_mcp/config.py
- src/pageindex_mcp/helpers.py
- src/pageindex_mcp/storage.py

#### Simplification Proposal
Freeze all behavior-altering env vars exactly once at process start into a single immutable `PipelineConfig` dataclass (replaces the three competing read sites: config.py module-level constants, helpers.py's `_verdict_thresholds_cache` lazy singleton, and config.py's `effective_config_snapshot` per-call re-reads). effective_config_snapshot becomes a trivial asdict() projection instead of re-parsing os.environ. Wire validate_feature_wirings as an explicit startup call in both server.py and worker.py (removing atexit-only registration). Expose storage.delete_doc through an MCP tool so the HR2 cascade is reachable in production.

Concrete restructuring steps:
- Step A -- Unified frozen config: create `PipelineConfig` dataclass absorbing all 25+ fields from the three competing sites; instantiate once at module load as `pipeline_config = PipelineConfig.from_env()`; delete _verdict_thresholds_cache/_get_verdict_thresholds/reset_verdict_thresholds; rewrite effective_config_snapshot() as dataclasses.asdict(pipeline_config); redirect ~15 import sites (config.py, helpers.py). Net delta: ~-40 lines.
- Step B -- Startup wiring validation: add validate_feature_wirings() call in server.py's app factory/lifespan hook and worker.py's startup() coroutine; remove atexit.register; extend FEATURE_WIRINGS to cover GATES list and dual RtlDecision sites. Net delta: ~+13 lines.
- Step C -- HR2 erasure endpoint: add MCP tool `delete_document(doc_id)` calling storage.delete_doc, giving the cascade a production entrypoint per CLAUDE.md Hard Rule 2. Net delta: ~+20 lines.
- Step D -- Deprecation aliases: thin shims for removed module-level constants emitting DeprecationWarning, removed next release. Net delta: ~+15 temporary lines.

Total net: roughly -10 to +10 lines depending on deprecation-shim lifetime.

Historical bug classes prevented: config snapshot drift (audit sidecar reporting stale threshold value -- eliminated, single frozen source); chunked_docling_timeout_s unwired (would be caught at worker startup, not only atexit which long-running workers may never reach); _check_bidi_coherence dead code (flagged if wiring registry covers bidi computation sites); HR2 cascade unreachability (explicit tool endpoint makes it exercisable and testable end-to-end); AGPL fallback silent walk (a wiring entry for the AGPL gate path would surface the ungated fallback).

Migration risk: Sequence C (zero-risk additive endpoint) then B (additive startup call, feature-flaggable) then A (the breaking change) then D (cleanup). Step B risk: validation may now fail loudly at startup where it previously failed silently at exit -- mitigate with try/except + logged warning for one release before promoting to hard crash. Step A is the only breaking change: 15 import sites updated, deprecation shims protect external/test code; test fixtures using monkeypatch.setenv must switch to reset_pipeline_config(). Acceptance gate: integration test asserting effective_config_snapshot() output matches values the pipeline actually uses.

Estimated effort: ~6 hours focused implementation (A: 3-4h, B: 1h, C: 1h, D: 30min) plus 1-2 hours integration testing to confirm config parity across the three former read sites.

---

### Zone 6: Cross-Process Error Classification Boundary

**Severity:** critical | **Bug count:** 7

#### Mechanism
The child process reports its exception class name via stdout JSON. If the child crashes hard (SIGKILL, OOM, segfault) without writing the JSON line, error_class becomes None, which falls through _CHILD_ERROR_REASON.get(None, 'converter_child_failed') to a generic non-terminal reason. Since 'converter_child_failed' is NOT in _TERMINAL_CHILD_REASONS, a genuinely-terminal LowQualityTreeError gets retried up to MAX_TRIES, wasting resources on a document the gate subsystem already determined is unsalvageable. The _TERMINAL_CHILD_REASONS set has only 2 entries vs 10+ gate defects, with no coverage assertion. Separately, PDF_INSPECTOR_PRECLASSIFY grants a 16.5x effective_timeout to scanned/image PDFs in the worker child, but reap_stale_jobs uses a fixed JOB_TIMEOUT+REAP_GRACE cutoff. A legitimately-processing OCR job can be reaped to ERROR, and its later DONE transition raises an unhandled ValueError because JobStatus.ERROR has no path back to DONE in _VALID_TRANSITIONS.

#### History
a. RFC-006 D3: fire-and-forget async Postgres registry delete logged 'full cascade succeeded' over silent failure (fixed by RFC-007 D2 with awaited+timeout).
b. RFC-033 D3: read-side minio retry insufficient for write-visibility racing (fixed by RFC-034 D18 with write-side head_object verification, then tuned by RFC-036 D1 from 4.4s to 0.45s delay).
c. RFC-032 D3: 3x timeout multiplier guess for scanned PDFs (recalibrated by D9 to 16.5x after measurement showed mean 6.16x, max 11.00x).
d. Registry dual-write non-atomic: worker._upsert_registry_row (parent process) and storage save_doc (child process) coordinate via SQL CAS guard on verdict_computed_at with no application-level lock.
e. PHASE0_POSTPROCESS_REGISTRY_LATENCY_AUDIT: registry:complete write-once latch never resets for post-backfill incremental failures.

#### Code Evidence
worker.py:111-116 _CHILD_ERROR_REASON (verified: 4 entries mapping exception class names to Redis reason strings -- LowQualityTreeError, FileNotFoundError, RuntimeError, ArgparseExit). worker.py:124-129 _TERMINAL_CHILD_REASONS (verified: frozenset of 2 -- 'low_quality_tree' and 'llm_failure_terminal'). worker.py:413-627 process_document_job (verified: 215 lines, complexity 14, PLR0915 noqa, exc.error_class consumed at line ~520 via _CHILD_ERROR_REASON.get). job_status.py:36-44 _VALID_TRANSITIONS (ERROR loops to itself only, no path to DONE).

#### Key Files
- src/pageindex_mcp/worker.py
- src/pageindex_mcp/client.py
- src/pageindex_mcp/job_status.py

#### Simplification Proposal
No proposal was generated for this zone in the input data.

---

### Zone 7: Mutable ExtractionState Recovery Path Ordering

**Severity:** high | **Bug count:** 7

#### Mechanism
The recovery cascade runs up to 4 sequential recovery attempts (GARBLE, LOW_CONTENT, IMAGE_DOMINANT OCR retry, then VLM fallback), each mutating state.md_content, state.pic_results, and state.result in place. When keep-best determines the retry 'lost', RecoveryOutcome.apply() reverts result/ok/reason/gate_result/total_chars/md_content/pic_results/used_converter but leaves state.tmp_md_path pointing at the post-retry tempfile (the pre-retry tempfile was already unlinked). The bidi re-normalization (reconstruct_bidi_order) applied to remote-returned markdown before tree construction can double-apply with the mixed-script table-row guard, collapsing bilingual content structure. Arabic structural heading injection changes validate_tree's node_count/depth signals, forcing shallow docs to clear validation thresholds even when the tree path is severely content-lossy vs flat routing.

#### History
a. RFC-029 D4: keep-best revert restores result/ok/reason but leaves md_content/tmp_md_path at post-retry data, creating tree-vs-markdown state mismatch (cabinet resolution lost 69%: 48k-to-14.8k chars).
b. RFC-019 D3a: forced OCR without calling detect_ocr_langs(), defaulting to deu+eng on Arabic scans, compounding mojibake.
c. RFC-034 D3/D17: bidi re-normalization suspected double-application on mixed-script content, MOU collapsed from 134 nodes/13,422 chars to 20 nodes/12,344 chars with 11/13 images unenriched (PASS-to-MARGINAL, unresolved through Run 18).
d. RFC-021 QF1: deferred OCR changed which path F1 exemption fires under (GHV-TKV-Tarif 4,267-to-375 chars).
e. RFC-021 QF1/RFC-022 B2: image-only PDFs produce only image markers as text, tripping >30% token-repetition garble check.
f. RFC-027 D4 to RFC-028 D1 to RFC-029 D1: Arabic heading injection cascade (prev_blank guard blocks most markers, then removed, then shallow docs forced to clear validate_tree, marsoom 13: 6 nodes/1,225 chars tree vs 75 blocks/5,972 chars flat -- 80% content loss).
g. RFC-028 D0: chunked_docling_timeout function created but never called by worker.py (task marked complete but never imported).

#### Code Evidence
client.py:987-1326 _convert_to_tree (verified: 340 lines, complexity 41, 23 callees -- PDF/MD/DOCX/PPTX/XLSX/image/HTML dispatch + remote/local routing + OCR-mode decision + landscape probing + converter-chain fallback + bidi renorm + verdict/route dispatch all in one method). client.py:1482-1549 keep-best revert gate inside _recover_ocr_retry (verified: RecoveryOutcome fields include result/ok/reason/gate_result/total_chars/md_content/pic_results/used_converter). helpers.py:3335-3354 prepare_tree (verified: fixed order split_oversized_leaf_nodes then _segment_table_nodes, no table awareness in ordinal splitter).

#### Key Files
- src/pageindex_mcp/client.py
- src/pageindex_mcp/helpers.py
- src/pageindex_mcp/converters.py

#### Simplification Proposal
No proposal was generated for this zone in the input data.

## Cross-Cutting Themes

- Fix-then-regress-then-refix cycles dominate the garble-gate reason-code routing logic: node_count<3/depth<2 early-exits in validate_tree() repeatedly bypass garble-triggered recovery (OCR escalation, VLM fallback, D7 Tesseract-raster) across RFC-004 D1, RFC-018 D3b, RFC-023 D0/D11, RFC-025 D3, and RFC-026 D5 — each RFC narrows the gap without eliminating the class of bug (a new reason string or gate ordering issue).
- Threshold/verdict-gate softening (PASS_MAX_LEAF_RATIO widened 0.17→0.20→0.30, low_content_density calibrated then relaxed, image_enrichment_promoted bypass) recurringly trades one document's false-FAIL for another's false-PASS, culminating in RFC-025's hysteresis mechanism — which itself broke because the corpus-reingestion pipeline wipes the very prior-verdict store hysteresis depends on.
- Picture/image enrichment and page-level OCR escalation are two independently-evolved subsystems that keep colliding: RFC-015 D6's per-picture OCR conflated with the page-level escalation gate (RFC-017 fix), a single _OCR_ESCALATION flag gates both concerns, and RFC-018/019/020's page-coverage and clip-text filters — each individually reasonable — combine to zero out enrichment on embedded-picture PDFs and collapse trees to flat on full-page scans.
- Unicode/script-detection assumptions break under normalization ordering: RFC-033 D2's bidi/reversed-morphology detectors check Presentation-Forms codepoints that upstream NFKC normalization (converters.py:2357) has already decomposed away, producing a structurally-null (0% TPR) detector — the same encoding-range mismatch pattern recurs in the Latin-gibberish expected_script threading gap (unreachable for both Arabic-filename and non-Arabic/German-filename documents, via different specific bugs).
- Incomplete wiring is the single most common defect shape: functions/reasons/flags/interfaces are implemented but never called or threaded through — validate_tree() unimplemented per RFC-000 spec, JobEnqueuer interface unwired, chunked-Docling timeout function never invoked by worker.py, _check_bidi_coherence dead code, PDF-inspector classification computed but never branched on, judge calibration rules designed but never written to SKILL.md, and RFC-034 D19's enrichment fix staged in git but never committed through an entire audit cycle.
- State-consistency violations in recovery/revert paths: OCR retry guardrails revert some but not all mutated state (tree metadata reverted, markdown left pointing at post-retry data), Python list-multiplication for standalone-image PictureResults shares references so one enrichment pop() strips bytes from all siblings, and async fire-and-forget writes (Postgres registry delete, registry dual-write) let 'success' be logged over a silent partial failure.
- Scoring/classification layers are asymmetric with retrieval/extraction layers: content_signals and content-volume scoring only see block['text'], which is empty by design for table blocks (whose content lives in row_records) and blind to image-block OCR text, producing large apparent 'content-loss' regressions that are actually measurement blind spots, not real extraction defects.
- Heuristics calibrated against one specific failing document routinely over- or under-fire on the rest of the corpus: ToC-heading stripping (no depth/node-count guard) flattens long legal statutes; fence-marker parity toggling silently discards all content after one stray backtick; landscape re-extraction shares a code path with portrait that regresses both together; and low_content_density's 500-chars/node threshold was tuned to a single doc and rejected three legitimate ones.
- Hard Rule violations recur in the same two shapes across the whole RFC sequence: right-to-erasure cascade completeness gaps (registry rows, preloaded/ objects each discovered and closed one at a time) and 'never silently persist a low-quality tree' violations (zero-content/placeholder docs auto-promoted via image_enrichment_promoted, RTL-reversed titles undetectable by any gate, gate corrections exposing but not always blocking pre-existing defects).
- Infrastructure/deployment staleness independently corrupts pipeline correctness: the remote Docling microservice ran code predating a locally-committed bidi-heading guard (with no re-normalization of remote results locally), and a remote-call failure silently falls through to the AGPL-licensed pymupdf4llm fallback with no hard gate or confirming logs — both violate stated Hard Rules regardless of whether the primary bug is 'fixed' in the working tree.
- Production-scale parameters (timeout multipliers, write-barrier delays) are repeatedly set by guess rather than measurement, and each 'fix' overcorrects into the opposite failure mode until empirical data forces recalibration (RFC-032 D3's 3x timeout guess → D9's measured 16.5x; RFC-034 D18's 4.4s write-barrier delay → RFC-036 D1's 0.45s reduction).
</content>
