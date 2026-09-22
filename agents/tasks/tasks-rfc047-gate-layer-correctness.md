---
id: tasks-rfc047
title: "Tasks — RFC-047 Gate-Layer Correctness"
type: tasks
status: draft
date: "2026-09-22"
tags:
  - tasks
  - gate-layer
  - garble-detection
  - verdict
  - flat-path
  - enrichment
governs:
  - "[[047-gate-layer-correctness]]"
---

# Tasks — RFC-047 Gate-Layer Correctness

## Status legend

| Icon | Meaning |
|------|---------|
| [ ]  | not started |
| [~]  | in progress |
| [x]  | done |

## Relationship to RFC-046 open tasks

- RFC-046 task 4.4 (garble-check enrichment-mutated blocks) is **subsumed by D3** (Wave 2). When Wave 2 lands, RFC-046 task 4.4 should be closed with a cross-reference here.
- RFC-046 task 5.3 (reconcile the two arbitrators onto one script-aware policy) is **NOT addressed** by this RFC. It remains open on RFC-046; a successor RFC (RFC-048 or later) should pick it up if the engine-tier decision warrants it.

---

## Wave 1 — Detector Fixes (D1 + D2)

- [x] **1.1** — Repair `_MIXED_SCRIPT_RE` false-positive regex (D1)
  - In `src/pageindex_mcp/helpers/garble.py`, fix the `_MIXED_SCRIPT_RE` compiled pattern (line ~752) to require at least one ASCII letter `[A-Za-z]` in each bridging ASCII run, so digits and punctuation alone no longer match.
  - Acceptance: parenthesised Arabic markers `(أ)`, digit-adjacent references `رقم597`, and article numbers `المادة15` are NOT matched by `_MIXED_SCRIPT_RE`. True mojibake like `كtابcجديد` (containing Latin letters) IS still matched.

- [x] **1.2** — Add regression tests for `_MIXED_SCRIPT_RE` repair (D1)
  - Add test `test_mixed_script_re_no_false_positive_on_arabic_markers`: parenthesised Arabic letters `(أ)` must not fire `sparse_mojibake`.
  - Add test `test_mixed_script_re_no_false_positive_on_digit_bridging`: digit-only bridging `رقم597` must not match.
  - Add test `test_mixed_script_re_still_catches_true_garble`: genuinely garbled mixed-script string with Latin letters IS matched.
  - Add test `test_mixed_script_re_realistic_arabic_insurance_clean`: a realistic Arabic insurance document with section markers must remain clean.
  - Acceptance: all D1 tests pass; no existing garble tests in `test_garble.py`, `test_rfc_reorder.py`, or `test_rfc_quality.py` regress.

- [x] **1.3** — Replace binary condemn with ratio threshold in `_garble_check_flat_blocks` (D2)
  - In `src/pageindex_mcp/helpers/garble.py`, add module-level constant `_GARBLE_BLOCK_RATIO_THRESHOLD = 0.10` at ~line 764, following the `_RFC029_DEEP_TREE_DEPTH_THRESHOLD` pattern.
  - Modify `_garble_check_flat_blocks` (line ~974) so it returns `None` when `garble_ratio < _GARBLE_BLOCK_RATIO_THRESHOLD` instead of condemning on any single garbled block (`garbled_count >= 1`).
  - Log the ratio on the `garble_flat_block_verdict` decision event regardless of whether the threshold fires.
  - **Note:** This threshold change affects BOTH callers — main garble gate (`indexer.py:1409`) and post-enrichment gate (`indexer.py:1532`). Both become ratio-aware. Acceptable given dilution immunity guard at 0.2 holds.
  - Acceptance: unit test `test_flat_blocks_ratio_threshold_no_condemn_below` passes — ratio below 0.10 is NOT condemned; `test_single_garbled_block_not_diluted` still passes (ratio 0.2 > 0.10 → condemned).

- [x] **1.4** — Add ratio-threshold tests for `_garble_check_flat_blocks` (D2)
  - Add parameterized test `test_flat_blocks_ratio_threshold` with cases:
    - 1 garbled of 10 blocks (ratio 0.1, at threshold → condemned)
    - 2 garbled of 10 blocks (ratio 0.2, above threshold → condemned)
    - 0 garbled of 10 blocks (ratio 0.0, below threshold → clean)
  - Regression: verify `test_single_garbled_block_not_diluted` still passes (1 of 5 = 0.2 > 0.10 → condemned).
  - Regression: one garbled chart caption among 10+ clean blocks does NOT trigger rejection.
  - Acceptance: all D2 tests pass; no existing garble tests regress.

- [ ] **1.6** — Corpus baseline measurement (Wave 1)
  - Run `make ingest` against the full corpus with D1+D2 applied.
  - Record before/after verdict distribution diff against the most recent baseline run.
  - Expected: fewer false-positive garble verdicts on clean Arabic/mixed-script documents; no new false negatives on genuinely garbled documents.
  - Acceptance: verdict diff is documented in a commit message or audit note; no unexpected verdict regressions.

- [ ] **1.C** — Wave 1 acceptance gate
  - All tests from 1.1–1.4 pass (`make test PYTEST_ARGS="tests/test_garble.py -q"`).
  - `test_single_garbled_block_not_diluted` still passes (dilution immunity preserved).
  - No regressions in existing test suite (`make test`).
  - D1 acceptance criterion: proposed regex mechanically verified against existing mojibake fixtures; match counts still exceed 0.02 word-ratio threshold.
  - Corpus measurement from 1.6 is recorded and reviewed.

---

## Wave 2 — HR5 Consequence Wiring (D3)

- [ ] **2.1** — Wire post-enrichment garble check to surgical field-clear (D3)
  - In `src/pageindex_mcp/client/indexer.py`, after the post-enrichment decision log (line ~1554) and before `flat_structure` construction (line ~1580):
    - Replace the `_garble_check_flat_blocks` call at the post-enrichment site with an inline per-block loop: call `detect_garble` on each enriched image block individually.
    - When garble ratio >= `_GARBLE_BLOCK_RATIO_THRESHOLD`, clear `ocr_text` on each garbled block (`block['ocr_text'] = ''`) rather than removing blocks from the list.
    - This preserves image metadata (`figure_path`, `page`, `bbox`), avoids list mutation, and the empty `ocr_text` yields empty string from `block_text()`.
  - **Edge case:** When ALL enriched blocks are garbled (ratio = 1.0), all `ocr_text` cleared; document proceeds with zero image-derived text.
  - Acceptance: unit test `test_post_enrichment_garble_clears_ocr_text` passes — garbled enriched blocks have empty `ocr_text`; clean blocks are unchanged.

- [ ] **2.2** — Add consequence-wiring tests (D3)
  - Add test `test_post_enrichment_clean_passes`: enrichment output that is NOT garbled proceeds to storage unchanged.
  - Add test `test_post_enrichment_garble_logs_strip`: the `post_enrichment_garble_check` decision event includes `stripped_count`, `retained_count`, `garble_ratio` attrs.
  - Add test `test_post_enrichment_single_garbled_among_many_retains_clean`: 1 garbled out of 10 enriched blocks → ratio below 0.10 → no field-clear (D2 threshold protects).
  - Add test `test_post_enrichment_all_garbled_clears_all`: all enriched blocks garbled → all `ocr_text` cleared, document proceeds.
  - Acceptance: all D3 tests pass; the field-clear path is exercised end-to-end.

- [ ] **2.3** — Extend `post_enrichment_garble_check` decision event
  - In `src/pageindex_mcp/obs/decision_points.py`, extend the existing `post_enrichment_garble_check` event with choices `blocks_stripped` / `strip_skipped` and attrs `stripped_count`, `retained_count`, `garble_ratio`.
  - No new decision point registration needed — reuse the existing event.
  - Acceptance: AST guard passes; decision event logged on strip with correct attrs.

- [ ] **2.4** — Corpus measurement (Wave 2)
  - Run `make ingest` against the full corpus with D3 applied on top of Wave 1.
  - Record before/after verdict distribution diff.
  - Expected: documents whose enrichment introduces garble have garbled blocks stripped; net effect is 0–2 documents with reduced enrichment content but same verdict.
  - Acceptance: verdict diff documented; no unexpected changes.

- [ ] **2.C** — Wave 2 acceptance gate
  - All tests from 2.1–2.3 pass.
  - No regressions in existing test suite (`make test`).
  - Corpus measurement from 2.4 is recorded and reviewed.
  - HR5 property confirmed: no garbled post-enrichment content is silently persisted.

---

## Wave 3 — Flat-Path Defect Re-derivation (D4)

- [ ] **3.1** — Pass `None` as `validate_result` on flat path (D4)
  - In `src/pageindex_mcp/client/indexer.py` (line ~1596), pass `None` instead of `state.gate_result` as the `validate_result` argument to `compute_verdict` on the flat path.
  - This makes `evaluate_gates` enter the `validate_result is None` branch (`verdict.py:187–200`), where `defect = TreeDefect.OK` and `_all_defects = frozenset()`. The flat signals drive the verdict without inherited tree defects.
  - Acceptance: the flat path no longer inherits tree defects; `evaluate_gates` receives `None` for `validate_result` on flat-routed documents.

- [ ] **3.2** — Red-green test for D4 defect leakage bug
  - Add test `test_flat_path_tree_suspect_density_leaks_to_hard_fail` (RED before fix): construct `TreeGateResult` with `defect=SUSPECT_DENSITY` and `all_defects` containing `SUSPECT_DENSITY`, pass alongside `flat_signals` with sufficient text (`flat_text_len=2151`). Assert the outcome hard-fails with `SUSPECT_DENSITY`. This test MUST FAIL after the fix is applied (passing `None` removes the hard-fail).
  - Acceptance: test passes on current code (proving the bug exists), fails after task 3.1 is applied.

- [ ] **3.3** — Unit tests: flat-path verdict correctness after fix
  - Add test `test_flat_path_tree_density_fail_not_inherited`: same scenario as 3.2, but now with fix applied — assert the flat verdict is NOT FAIL.
  - Add test `test_flat_path_no_spurious_tree_defects`: a flat-path document does NOT carry `NODE_COUNT_LOW` or `DEPTH_LOW` from the tree.
  - Add test `test_flat_path_reasons_describe_flat_route`: the verdict `reason` string references flat-path evaluation, not tree evaluation.
  - Acceptance: all D4 tests pass; flat-path verdicts carry self-consistent reasons and defects.

- [ ] **3.3b** — Reorder-inference safety test
  - Add test `test_flat_structure_is_reordered_false`: flat structure (no `start_index`/`line_num`) produces `is_reordered=False` via `TreeSignals.from_tree`.
  - Add test `test_evaluate_gates_none_validate_result_not_reordered`: `evaluate_gates` with `None` validate_result and `is_reordered=False` produces `defect=TreeDefect.OK`.
  - Acceptance: the implicit safety contract (flat structures never carry reorder markers) is made explicit.

- [ ] **3.3c** — Metadata provenance fix
  - At `indexer.py:1650-1653`, source `flat_meta['garble_prongs']` from `_flat_sig` (already computed at line ~1590) rather than from `state.gate_result.signals.garble_prongs`.
  - Add test `test_flat_meta_garble_prongs_from_flat_sig`: verify `flat_meta['garble_prongs']` matches `_flat_sig.garble_prongs`, not `state.gate_result.signals.garble_prongs`.
  - Acceptance: metadata provenance is consistent between flat verdict and its metadata.

- [ ] **3.4** — Attributed corpus measurement for D4
  - Run `make ingest` against the full corpus with D4 applied on top of Waves 1–2.
  - Record before/after verdict distribution diff.
  - Expected: uae_numbers portrait FAIL → clears density gate → expected PASS or MARGINAL. Other flat-routed documents may shift verdict reasons; outcome changes should be attributable to unmasked flat-path defects.
  - Acceptance: verdict diff documented; every outcome change explained and attributed to D4.

- [ ] **3.C** — Wave 3 acceptance gate
  - All tests from 3.1–3.3c pass.
  - Red-green test (3.2) confirmed: passes on current code (bug exists), fails after fix.
  - Reorder-inference safety (3.3b) confirmed: flat structures produce `is_reordered=False`.
  - Metadata provenance (3.3c) confirmed: `flat_meta['garble_prongs']` from `_flat_sig`.
  - No regressions in existing test suite (`make test`).
  - Corpus measurement from 3.4 is recorded and reviewed.
  - Flat-path verdicts confirmed to carry self-consistent reasons and defects.

---

## Wave 4 — Field Split + Density Activation (D5 + D6)

- [ ] **4.1** — Split `include_enrichment` into `include_ocr_text` + `include_summary` in `_node_text_parts` (D5)
  - In `src/pageindex_mcp/helpers/tree_validation.py`, replace the single `include_enrichment: bool` parameter in `_node_text_parts` (line ~110) with two independent flags: `include_ocr_text: bool = False` and `include_summary: bool = False`.
  - Acceptance: `_node_text_parts` accepts the new flags; old `include_enrichment` parameter is removed.

- [ ] **4.2** — Propagate split through `_flatten_tree_text` (D5)
  - Update `_flatten_tree_text` to accept and pass through `include_ocr_text` and `include_summary` to `_node_text_parts`.
  - Acceptance: `_flatten_tree_text` signature matches the new flag convention.

- [ ] **4.3** — Update `TreeSignals.from_tree` to use `include_ocr_text=True, include_summary=False` for `flat_text_corrected` (D5)
  - In `TreeSignals.from_tree` (line ~298), call `_flatten_tree_text` with `include_ocr_text=True, include_summary=False` for the `flat_text_corrected` field.
  - This ensures the density-corrected numerator counts only real OCR text, not LLM-generated summaries.
  - Acceptance: `flat_text_corrected` excludes summary text; the 42% density inflation from LLM summaries is eliminated.

- [ ] **4.4** — Update facade guard in `test_facade_surface_guard.py` (D5)
  - Update the facade guard assertions for BOTH `_node_text_parts` (line ~319) AND `_flatten_tree_text` (line ~306) to match the new parameter names (`include_ocr_text`, `include_summary` instead of `include_enrichment`).
  - Both functions' signatures change in lockstep — the guard must be updated for both, not only `_node_text_parts`.
  - Acceptance: facade guard tests pass with the new signatures for both functions.

- [ ] **4.5** — Unit tests for split flag behaviour (D5)
  - Add parameterized test `test_node_text_parts_flag_combinations` with 4 cases:
    - `include_ocr_text=True, include_summary=False` → only OCR text included
    - `include_ocr_text=False, include_summary=True` → only summary included
    - both `True` → both included (equivalent to old `include_enrichment=True`)
    - both `False` → neither included (equivalent to old `include_enrichment=False`)
  - Add test `test_flatten_tree_text_respects_split_flags`: `_flatten_tree_text` passes the flags through to `_node_text_parts` correctly.
  - Add test `test_flat_text_corrected_excludes_summary`: `TreeSignals.from_tree` produces `flat_text_corrected` that excludes summary text.
  - **Note:** `_node_text_parts` lines 113-114 silently drop `ocr_text`/`summary` values that duplicate body text. This dedup is preserved and must not be changed.
  - Acceptance: all D5 tests pass; old `include_enrichment` parameter is fully removed from all call sites.

- [ ] **4.6** — Attributed corpus measurement with split numerator (D5)
  - Run `make ingest` against the full corpus with D5 applied on top of Waves 1–3.
  - This is a refactor — record that verdict distribution is UNCHANGED (the density gate still fires on the uncorrected numerator).
  - Record per-document: old `chars_per_page_corrected` (summary + ocr_text) and new value (ocr_text only).
  - Acceptance: zero verdict diff; per-document corrected-numerator table documented.

- [ ] **4.7** — D6: record D8 activation decision with measurement
  - After D5 corpus measurement, evaluate whether the density gate should switch to firing on the corrected (OCR-only) numerator.
  - The measurement from 4.6 shows, for every document, the old and new corrected values and which documents would change verdict.
  - Record a dated decision entry with the measurement that drove it.
  - If activated: implement the switch (change `gates.py:346` to fire on `chars_per_page_corrected`) and add tests.
  - If deferred: document the rationale.
  - Acceptance: decision is documented with supporting corpus evidence.

- [ ] **4.C** — Wave 4 acceptance gate
  - All tests from 4.1–4.5 pass (D5 is mandatory).
  - D5 corpus measurement from 4.6 confirms zero verdict diff.
  - D6 decision from 4.7 is documented.
  - If D8 activated: additional tests pass and corpus diff is recorded.
  - No regressions in existing test suite (`make test`).

---

## Wave 5 — Corpus Re-run + Engine RFC Decision (D7)

- [ ] **5.1** — Full attributed corpus re-run
  - Run `make ingest` against the complete corpus with all Waves 1–4 applied.
  - Produce a full verdict distribution report with per-document attribution showing which deliverable(s) changed each verdict.
  - Compare against the pre-RFC-047 baseline (post-RFC-046 state).
  - Acceptance: attributed corpus report is complete and covers every document.

- [ ] **5.2** — Per-document delta table attributed to D1–D5
  - Every verdict movement SHALL be attributed to a named deliverable (D1, D2, D3, D4, or D5).
  - Movements SHALL be reported in both directions — improvements and regressions.
  - An unexplained movement SHALL block acceptance pending investigation.
  - Acceptance: delta table documented; zero unexplained movements.

- [ ] **5.3** — Engine-tier successor RFC decision
  - Based on the corpus results, decide whether an OCR engine-tier RFC is warranted.
  - The residue (documents still failing after all gate-layer bugs are fixed) determines the answer.
  - If warranted: draft a one-paragraph scope statement for the follow-up RFC (RFC-048).
  - If not warranted: document the rationale (gate layer is now correct; remaining failures are capability gaps, not engine quality).
  - Closes `audit/RECONCILIATION_REPORT.md:148-156` item #2.
  - Acceptance: decision is documented with supporting evidence from the corpus run.

- [ ] **5.C** — Wave 5 acceptance gate (RFC-047 final gate)
  - Full corpus re-run from 5.1 is complete and attributed.
  - Delta table from 5.2 shows zero unexplained movements.
  - Engine RFC decision from 5.3 is documented.
  - All tests pass (`make test`).
  - RFC-047 is marked complete or hands off to a successor RFC.
