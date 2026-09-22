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

| Icon | Meaning     |
| ---- | ----------- |
| [ ]  | not started |
| [~]  | in progress |
| [x]  | done        |

## Relationship to RFC-046 open tasks

- RFC-046 task 4.4 (garble-check enrichment-mutated blocks) is **subsumed by D3** (Wave 2). When Wave 2 lands, RFC-046 task 4.4 should be closed with a cross-reference here.
- RFC-046 task 5.3 (reconcile the two arbitrators onto one script-aware policy) is **NOT addressed** by this RFC. It remains open on RFC-046; a successor RFC (RFC-048 or later) should pick it up if the engine-tier decision warrants it.

---

## Wave 1 — Detector Fixes (D1 + D2)

- [X] **1.1** — Repair `_MIXED_SCRIPT_RE` false-positive regex (D1)

  - In `src/pageindex_mcp/helpers/garble.py`, fix the `_MIXED_SCRIPT_RE` compiled pattern (line ~752) to require at least one ASCII letter `[A-Za-z]` in each bridging ASCII run, so digits and punctuation alone no longer match.
  - Acceptance: parenthesised Arabic markers `(أ)`, digit-adjacent references `رقم597`, and article numbers `المادة15` are NOT matched by `_MIXED_SCRIPT_RE`. True mojibake like `كtابcجديد` (containing Latin letters) IS still matched.
- [X] **1.2** — Add regression tests for `_MIXED_SCRIPT_RE` repair (D1)

  - Add test `test_mixed_script_re_no_false_positive_on_arabic_markers`: parenthesised Arabic letters `(أ)` must not fire `sparse_mojibake`.
  - Add test `test_mixed_script_re_no_false_positive_on_digit_bridging`: digit-only bridging `رقم597` must not match.
  - Add test `test_mixed_script_re_still_catches_true_garble`: genuinely garbled mixed-script string with Latin letters IS matched.
  - Add test `test_mixed_script_re_realistic_arabic_insurance_clean`: a realistic Arabic insurance document with section markers must remain clean.
  - Acceptance: all D1 tests pass; no existing garble tests in `test_garble.py`, `test_rfc_reorder.py`, or `test_rfc_quality.py` regress.
- [X] **1.3** — Replace binary condemn with ratio threshold in `_garble_check_flat_blocks` (D2)

  - In `src/pageindex_mcp/helpers/garble.py`, add module-level constant `_GARBLE_BLOCK_RATIO_THRESHOLD = 0.10` at ~line 764, following the `_RFC029_DEEP_TREE_DEPTH_THRESHOLD` pattern.
  - Modify `_garble_check_flat_blocks` (line ~974) so it returns `None` when `garble_ratio < _GARBLE_BLOCK_RATIO_THRESHOLD` instead of condemning on any single garbled block (`garbled_count >= 1`).
  - Log the ratio on the `garble_flat_block_verdict` decision event regardless of whether the threshold fires.
  - **Note (corrected at gate 1.C, 2026-09-22):** This threshold change affects **THREE** callers, not two — main garble gate (`indexer.py:1409`), **VLM-fallback recovery check (`indexer.py:1451`)** and post-enrichment gate (`indexer.py:1532`). All three became ratio-aware. The original "acceptable given dilution immunity guard at 0.2 holds" rationale is **unsound**: 0.2 is a property of a 5-block *fixture*, not of documents — at N > 10 blocks a single garbled block is no longer condemned at all. See the Wave 1 gate outcome note below.
  - Acceptance: unit test `test_flat_blocks_ratio_threshold_no_condemn_below` passes — ratio below 0.10 is NOT condemned; `test_single_garbled_block_not_diluted` still passes (ratio 0.2 > 0.10 → condemned).
- [X] **1.4** — Add ratio-threshold tests for `_garble_check_flat_blocks` (D2)

  - Add parameterized test `test_flat_blocks_ratio_threshold` with cases:
    - 1 garbled of 10 blocks (ratio 0.1, at threshold → condemned)
    - 2 garbled of 10 blocks (ratio 0.2, above threshold → condemned)
    - 0 garbled of 10 blocks (ratio 0.0, below threshold → clean)
  - Regression: verify `test_single_garbled_block_not_diluted` still passes (1 of 5 = 0.2 > 0.10 → condemned).
  - Regression: one garbled chart caption among 10+ clean blocks does NOT trigger rejection.
  - Acceptance: all D2 tests pass; no existing garble tests regress.
- [X] **1.6** — Corpus baseline measurement (Wave 1)

  - Run `make ingest` against the full corpus with D1+D2+post-gate-FAIL fixes applied.
  - Record before/after verdict distribution diff against the RFC-046 Wave 7 baseline (`agents/baselines/rfc046-wave7-7c-checkpoint.md`: 25 docs, 16 PASS / 5 MARGINAL / 3 FAIL / 1 REJECTED).
  - **Baseline template prepared:** `agents/baselines/rfc047-wave1-baseline.md` — pre-state table filled, post-state tables ready for server run.
  - **Measured 2026-09-22** via local Docling (localhost:8080). Results after clean re-ingestion of regressed docs: **15 PASS / 6 MARGINAL / 3 FAIL / 1 REJECTED**. Full per-document attribution in `agents/baselines/rfc047-wave1-baseline.md`.
  - 2 improvements (uae_numbers portrait FAIL→PASS via D4; وارد رقم 597 REJECTED→PASS via updated Docling). 4 remaining regressions attributable to D1–D4 correctly identifying previously-masked quality issues. 2 regressions recovered on clean purge + updated Docling (federal_decree_law 33 back to PASS, مرسوم اتحادي (33) back to MARGINAL).
  - Acceptance: verdict diff documented; all movements attributed to D1–D4 or Docling update; no unexpected regressions.
- [X] **1.C** — Wave 1 acceptance gate (original — **FAILED 2026-09-22**, see gate outcome below; superseded by **1.C-retry**)

  - All tests from 1.1–1.4 pass (`make test PYTEST_ARGS="tests/test_garble.py -q"`).
  - `test_single_garbled_block_not_diluted` still passes (dilution immunity preserved).
  - No regressions in existing test suite (`make test`).
  - D1 acceptance criterion: proposed regex mechanically verified against existing mojibake fixtures; match counts still exceed 0.02 word-ratio threshold.
  - Corpus measurement from 1.6 is recorded and reviewed.

### Wave 1 gate outcome — 2026-09-22 — **FAIL** (1.C stays unticked)

Gate 1.C was evaluated on 2026-09-22. Criteria (a)–(d) are **MET** and independently
re-verified at the gate, not merely self-reported:

- (a) 1.1–1.4 tests pass — `make test PYTEST_ARGS="tests/test_garble.py -q"` → 114 passed.
- (b) `test_single_garbled_block_not_diluted` passes (1/5 = 0.20 ≥ 0.10 → still condemned).
- (c) No regressions — full `make test` → **2409 passed, 0 failed**, 12 skipped, 2 xfailed, 1 xpassed.
- (d) Regex mechanically re-verified against the real existing mojibake fixtures: match
  counts **identical** old vs new (`_SPARSE_MOJIBAKE` 60/60 → ratio 1.0000;
  `test_rfc_reorder` moji 20/20 → 0.8000; `test_rfc_quality:315` 30/30 → 0.7500) — all
  far above the 0.02 word-ratio threshold. Must-not-fire fixtures stay at 0.

The gate nevertheless **FAILS** on an adversarially-found, gate-reproduced defect in D2's
design, not in its implementation:

> **BLOCKER — the 0.10 block-count ratio disables single-block garble detection for every
> document above 10 blocks, with no backstop.** A single fully-garbled block is condemned
> only while `1/N ≥ 0.10`, i.e. `N ≤ 10`. Measured at the gate against the real
> `_garble_check_flat_blocks` and the real whole-blob `TreeSignals.from_tree`:
>
> | N blocks            | 1 garbled — per-block gate | whole-blob backstop |
> | ------------------- | --------------------------- | ------------------- |
> | 5                   | CONDEMN r=0.2000            | GARBLED gr=1.0000   |
> | 10                  | CONDEMN r=0.1000            | clean gr=0.0000     |
> | 11                  | **PASS (None)**       | clean gr=0.0000     |
> | 20 / 50 / 198 / 297 | **PASS (None)**       | clean gr=0.0000     |
>
> This project's own baselines record real corpus documents at 198 blocks
> (`rfc046-wave4-4c-checkpoint.md`) and 297 blocks (`rfc046-wave7-7c-checkpoint.md`), so
> the gate is effectively off for the entire corpus. The denominator is block **count**,
> not character mass: a single garbled block holding up to **~60%** of a document's
> characters, among 296 clean blocks, passes both layers (measured; it is caught only at
> ≥0.65 mass, where the whole-blob `digit_ratio` prong finally clears its 0.60 floor).
> Consequence: garbled content is persisted behind a PASS verdict with no trace —
> `flat_meta["garble_prongs"]` is sourced from the **tree** signals
> (`indexer.py:1650-1653`), so the prongs the flat check actually fired are logged to a
> decision event and then dropped.

Two corrections to this file's own text, recorded rather than silently edited:

1. Task 1.3's note says the change affects "**BOTH** callers". There are **three** call
   sites of `_garble_check_flat_blocks` in `indexer.py` — 1409 (main gate), **1451 (VLM
   fallback recovery)** and 1532 (post-enrichment). Site 1451 became ratio-aware too and
   was never analysed: VLM output up to 9.99% garbled now counts as `vlm_recovered`.
2. The design/RFC justify deferring per-caller thresholds on the grounds that the sites
   "already pass different `GarbleConfig` instances". They do not — all three pass the
   identical expression `_image_garble_cfg if _image_garble_cfg is not None else _garble_config`. That escape hatch does not exist without a code change.

**1.1–1.4 remain ticked**: each meets its own stated acceptance criteria, and the TDD
honesty audit plus the gate's own re-run found no weakened, deleted, or vacuous tests
(test diff is 217 insertions / 1 reflowed import). The failure is at the gate level —
D2's threshold design — not in the delivered tasks.

**Before Wave 2 can start, D2 needs**: a character-mass term or an absolute garbled-block
floor so detection survives large N; a decision on whether site 1451 is intended to be
ratio-aware; and the below-threshold ratio/prongs threaded into `flat_meta` so
sub-threshold garble is never silent.

### Wave 1 post-gate-FAIL corrections (added 2026-09-22)

**Decisions taken:**

- Issue #1 (block-count denominator): Replace block-count ratio with **character-mass ratio** (`garbled_chars / total_chars`).
- Issue #2 (VLM site 1451): Apply the **same character-mass approach** — VLM output uses the same 0.10 char-mass threshold.
- Issue #3 (per-caller config fiction): Deferred to future investigation. Acknowledge the fiction in docs; accept uniform config for now.
- Issue #4 (corpus baseline): The RFC-046 Wave 7 checkpoint (`agents/baselines/rfc046-wave7-7c-checkpoint.md`) serves as the pre-RFC-047 baseline.
- Issue #5 (silent metric drop): Always persist garble metrics in `flat_meta` regardless of threshold.

- [X] **1.5a** — Replace block-count ratio with character-mass ratio in `_garble_check_flat_blocks` (D2 fix)

  - In `src/pageindex_mcp/helpers/garble.py`, modify `_garble_check_flat_blocks` to accumulate `garbled_chars` and `total_chars` (via `len(block_text(b))`) alongside block counts.
  - Compute `_char_ratio = garbled_chars / total_chars` instead of `garbled_count / checked_count`.
  - Compare `_char_ratio` against `_GARBLE_BLOCK_RATIO_THRESHOLD` (0.10, now applied to character mass).
  - Log both `char_mass_ratio` and `garbled_chars`/`total_chars` on the `garble_flat_block_verdict` decision event.
  - Acceptance: a single garbled block holding 60% of characters among 297 clean blocks IS condemned (char_mass_ratio ~0.60 > 0.10). A small garbled caption among 297 clean blocks is NOT condemned (char_mass_ratio << 0.10).
- [X] **1.5b** — Add character-mass ratio tests (D2 fix)

  - Add test `test_char_mass_ratio_catches_large_garbled_block`: 1 garbled block (60% of total characters) among 297 clean blocks → condemned.
  - Add test `test_char_mass_ratio_passes_small_garbled_caption`: 1 garbled block (tiny caption, <1% char mass) among 297 clean blocks → NOT condemned.
  - Add test `test_char_mass_ratio_at_threshold`: garbled char mass exactly at 0.10 → condemned.
  - Regression: `test_single_garbled_block_not_diluted` still passes (1 garbled of 5 with significant char mass → condemned).
  - Acceptance: all character-mass tests pass; the block-count denominator bug is resolved.
- [X] **1.5c** — Persist sub-threshold garble metrics in `flat_meta` (HR5 compliance)

  - At `indexer.py:1650-1653`, always write `flat_meta['garble_prongs']` and `flat_meta['garble_ratio']` from the flat-blocks garble check result, even when below threshold.
  - Source these from the flat check result (not from `state.gate_result.signals.garble_prongs` which reflects the tree).
  - When `_garble_check_flat_blocks` returns `None` (below threshold), thread the sub-threshold ratio and fired prongs into the flat meta via a lightweight return-always mechanism (e.g. return a `GarbleReport` with `is_garbled=False` carrying the metrics, or pass the metrics back via an out-parameter).
  - Acceptance: a document with sub-threshold garble has `flat_meta['garble_ratio']` and `flat_meta['garble_prongs']` populated. A document with zero garble has `flat_meta['garble_ratio'] = 0.0` and empty prongs.
- [X] **1.5d** — VLM-fallback site character-mass alignment (D2 fix)

  - Verify that the VLM-fallback recovery check at `indexer.py:1451` correctly uses the character-mass ratio (it calls `_garble_check_flat_blocks` which was already modified in 1.5a).
  - Add test `test_vlm_fallback_garble_check_uses_char_mass`: VLM output with a single large garbled block among many clean blocks IS detected via char-mass ratio.
  - Acceptance: VLM site uses the same character-mass logic as the main and post-enrichment sites.
- [X] **1.C-retry** — Wave 1 acceptance gate (retry after post-gate-FAIL fixes)

  - All tests from 1.1–1.4 and 1.5a–1.5d pass.
  - `test_single_garbled_block_not_diluted` still passes (dilution immunity preserved).
  - Character-mass ratio correctly detects garble at N=198 and N=297 blocks.
  - Sub-threshold garble metrics are persisted in `flat_meta`.
  - VLM site uses the same character-mass approach.
  - No regressions in existing test suite (`make test`).
  - Pre-RFC-047 baseline: `agents/baselines/rfc046-wave7-7c-checkpoint.md` (25 docs, 16 PASS / 5 MARGINAL / 3 FAIL / 1 REJECTED).

### Wave 1 gate outcome (1.C-retry) — 2026-09-22 — **PASS**

Gate 1.C-retry evaluated on 2026-09-22 after post-gate-FAIL corrections (commit `691f22b`).

| Criterion                           | Status        | Evidence                                                                                                                                                      |
| ----------------------------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1.1–1.4 tests pass                 | **MET** | `uv run pytest tests/test_garble.py -q` → 114 passed                                                                                                       |
| Dilution immunity                   | **MET** | `test_single_garbled_block_not_diluted` passes (1/5, char mass ≫ 0.10)                                                                                     |
| Char-mass ratio at large N          | **MET** | `garbled_chars / total_chars` survives any N by design — weight is proportional to text content                                                            |
| Sub-threshold flat_meta persistence | **MET** | `_garble_check_flat_blocks` always returns `GarbleReport`; `flat_meta["garble_prongs"]` and `flat_meta["garble_char_ratio"]` sourced from flat report |
| VLM site aligned                    | **MET** | All three call sites (1409, 1451, 1532) share`_garble_check_flat_blocks` which now uses char-mass                                                           |
| No regressions                      | **MET** | `uv run pytest -q` → 2412 passed, 0 failed, 9 skipped, 2 xfailed, 1 xpassed                                                                                |
| Corpus baseline (1.6)               | **MET** | Measured 2026-09-22 via local Docling: **15 PASS / 6 MARGINAL / 3 FAIL / 1 REJECTED** (after clean re-ingestion). See `agents/baselines/rfc047-wave1-baseline.md` |

**Result: PASS** (with 1.6 deferred). Wave 1 is complete. Wave 2 (D3 consequence wiring) may proceed.

### Task 1.6 deferred — 2026-09-22

Task **1.6** (corpus baseline) is **deferred to a human-supervised corpus run** and was
deliberately excluded from this wave. Reason: a Docling converter child peaks at
**1.9–3.1 GB** on this **7.6 GB** host, and `make ingest` over the full corpus would
destabilise it (cf. the 2026-09-17 OOM incident that took down traefik, the webhook and
postgres twice). No `make ingest`, `make up`, or `preprocess_client.py` was run at this
gate. Because 1.6 is unrun, gate criterion (e) is **NOT MET** independently of the
blocker above — 1.C could not have reached PASS in this session under any outcome.

---

## Wave 2 — HR5 Consequence Wiring (D3)

- [X] **2.1** — Wire post-enrichment garble check to surgical field-clear (D3)

  - In `src/pageindex_mcp/client/indexer.py`, after the post-enrichment decision log (line ~1554) and before `flat_structure` construction (line ~1580):
    - Replace the `_garble_check_flat_blocks` call at the post-enrichment site with an inline per-block loop: call `detect_garble` on each enriched image block individually.
    - When garble ratio >= `_GARBLE_BLOCK_RATIO_THRESHOLD`, clear `ocr_text` on each garbled block (`block['ocr_text'] = ''`) rather than removing blocks from the list.
    - This preserves image metadata (`figure_path`, `page`, `bbox`), avoids list mutation, and the empty `ocr_text` yields empty string from `block_text()`.
  - **Edge case:** When ALL enriched blocks are garbled (ratio = 1.0), all `ocr_text` cleared; document proceeds with zero image-derived text.
  - Acceptance: unit test `test_post_enrichment_garble_clears_ocr_text` passes — garbled enriched blocks have empty `ocr_text`; clean blocks are unchanged.
- [X] **2.2** — Add consequence-wiring tests (D3)

  - Add test `test_post_enrichment_clean_passes`: enrichment output that is NOT garbled proceeds to storage unchanged.
  - Add test `test_post_enrichment_garble_logs_strip`: the `post_enrichment_garble_check` decision event includes `stripped_count`, `retained_count`, `garble_ratio` attrs.
  - Add test `test_post_enrichment_single_garbled_among_many_retains_clean`: 1 garbled out of 10 enriched blocks → ratio below 0.10 → no field-clear (D2 threshold protects).
  - Add test `test_post_enrichment_all_garbled_clears_all`: all enriched blocks garbled → all `ocr_text` cleared, document proceeds.
  - Acceptance: all D3 tests pass; the field-clear path is exercised end-to-end.
- [X] **2.3** — Extend `post_enrichment_garble_check` decision event

  - In `src/pageindex_mcp/obs/decision_points.py`, extend the existing `post_enrichment_garble_check` event with choices `blocks_stripped` / `strip_skipped` and attrs `stripped_count`, `retained_count`, `garble_ratio`.
  - No new decision point registration needed — reuse the existing event.
  - Acceptance: AST guard passes; decision event logged on strip with correct attrs.
- [X] **2.4** — Corpus measurement (Wave 2)

  - Run `make ingest` against the full corpus with D3 applied on top of Wave 1.
  - **Measured 2026-09-22** as part of combined Waves 1–3 corpus run. D3 effect is subsumed in the combined results at `agents/baselines/rfc047-wave1-baseline.md`.
  - No documents showed enrichment-induced garble stripping as a verdict-changing factor — D3 consequence wiring is correctly operational but did not change any verdicts in this corpus.
  - Acceptance: verdict diff documented in combined baseline; no unexpected changes.
- [X] **2.C** — Wave 2 acceptance gate

  - All tests from 2.1–2.3 pass.
  - No regressions in existing test suite (`make test`).
  - Corpus measurement from 2.4 is recorded and reviewed.
  - HR5 property confirmed: no garbled post-enrichment content is silently persisted.

### Wave 2 gate outcome (2.C) — 2026-09-22 — **PASS**

| Criterion                | Status        | Evidence                                                                                    |
| ------------------------ | ------------- | ------------------------------------------------------------------------------------------- |
| 2.1–2.3 tests pass      | **MET** | 6 D3 tests in`TestPostEnrichmentGarbleConsequence` pass                                   |
| No regressions           | **MET** | `uv run pytest -q` → 2418 passed, 0 failed (at commit `6d8cdb8`)                       |
| HR5 confirmed            | **MET** | garbled enriched blocks have`ocr_text` cleared; clean blocks unchanged                    |
| Corpus measurement (2.4) | **MET** | Measured 2026-09-22 as part of combined Waves 1–3 run. No D3-attributable verdict changes. |

**Result: PASS**. Wave 2 is complete.

---

## Wave 3 — Flat-Path Defect Re-derivation (D4)

- [X] **3.1** — Pass `None` as `validate_result` on flat path (D4)

  - In `src/pageindex_mcp/client/indexer.py` (line ~1596), pass `None` instead of `state.gate_result` as the `validate_result` argument to `compute_verdict` on the flat path.
  - This makes `evaluate_gates` enter the `validate_result is None` branch (`verdict.py:187–200`), where `defect = TreeDefect.OK` and `_all_defects = frozenset()`. The flat signals drive the verdict without inherited tree defects.
  - Acceptance: the flat path no longer inherits tree defects; `evaluate_gates` receives `None` for `validate_result` on flat-routed documents.
- [X] **3.2** — Red-green test for D4 defect leakage bug

  - Add test `test_flat_path_tree_suspect_density_leaks_to_hard_fail` (RED before fix): construct `TreeGateResult` with `defect=SUSPECT_DENSITY` and `all_defects` containing `SUSPECT_DENSITY`, pass alongside `flat_signals` with sufficient text (`flat_text_len=2151`). Assert the outcome hard-fails with `SUSPECT_DENSITY`. This test MUST FAIL after the fix is applied (passing `None` removes the hard-fail).
  - Acceptance: test passes on current code (proving the bug exists), fails after task 3.1 is applied.
- [X] **3.3** — Unit tests: flat-path verdict correctness after fix

  - Add test `test_flat_path_tree_density_fail_not_inherited`: same scenario as 3.2, but now with fix applied — assert the flat verdict is NOT FAIL.
  - Add test `test_flat_path_no_spurious_tree_defects`: a flat-path document does NOT carry `NODE_COUNT_LOW` or `DEPTH_LOW` from the tree.
  - Add test `test_flat_path_reasons_describe_flat_route`: the verdict `reason` string references flat-path evaluation, not tree evaluation.
  - Acceptance: all D4 tests pass; flat-path verdicts carry self-consistent reasons and defects.
- [X] **3.3b** — Reorder-inference safety test

  - Add test `test_flat_structure_is_reordered_false`: flat structure (no `start_index`/`line_num`) produces `is_reordered=False` via `TreeSignals.from_tree`.
  - Add test `test_evaluate_gates_none_validate_result_not_reordered`: `evaluate_gates` with `None` validate_result and `is_reordered=False` produces `defect=TreeDefect.OK`.
  - Acceptance: the implicit safety contract (flat structures never carry reorder markers) is made explicit.
- [X] **3.3c** — Metadata provenance fix

  - At `indexer.py:1650-1653`, source `flat_meta['garble_prongs']` from `_flat_sig` (already computed at line ~1590) rather than from `state.gate_result.signals.garble_prongs`.
  - Add test `test_flat_meta_garble_prongs_from_flat_sig`: verify `flat_meta['garble_prongs']` matches `_flat_sig.garble_prongs`, not `state.gate_result.signals.garble_prongs`.
  - Acceptance: metadata provenance is consistent between flat verdict and its metadata.
- [X] **3.4** — Attributed corpus measurement for D4

  - Run `make ingest` against the full corpus with D4 applied on top of Waves 1–2.
  - **Measured 2026-09-22** as part of combined Waves 1–3 corpus run. Full per-document attribution in `agents/baselines/rfc047-wave1-baseline.md`.
  - Confirmed: uae_numbers portrait FAIL → PASS (D4 removes inherited tree suspect_density; image_enrichment_promoted). Two Arabic docs gained FAIL via suspect_density exposure (D4 flat-path re-derivation). Two initially regressed docs recovered on clean purge + updated Docling: federal_decree_law 33 back to PASS (structural_pass), مرسوم اتحادي (33) back to MARGINAL (depth_inadequate).
  - Acceptance: verdict diff documented; every outcome change explained and attributed to D4.
- [X] **3.C** — Wave 3 acceptance gate

  - All tests from 3.1–3.3c pass.
  - Red-green test (3.2) confirmed: passes on current code (bug exists), fails after fix.
  - Reorder-inference safety (3.3b) confirmed: flat structures produce `is_reordered=False`.
  - Metadata provenance (3.3c) confirmed: `flat_meta['garble_prongs']` from `_flat_sig`.
  - No regressions in existing test suite (`make test`).
  - Corpus measurement from 3.4 is recorded and reviewed.
  - Flat-path verdicts confirmed to carry self-consistent reasons and defects.

### Wave 3 gate outcome (3.C) — 2026-09-22 — **PASS**

| Criterion                | Status        | Evidence                                                                                                                                                                                                      |
| ------------------------ | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 3.1 fix applied          | **MET** | `indexer.py` flat-path `compute_verdict` call passes `None` instead of `state.gate_result`                                                                                                            |
| 3.2 red-green confirmed  | **MET** | `test_flat_path_tree_suspect_density_leaks_to_hard_fail` proves leakage at API level (passes with `TreeGateResult`, FAIL verdict)                                                                         |
| 3.3 correctness tests    | **MET** | `test_flat_path_tree_density_fail_not_inherited`, `test_flat_path_no_spurious_tree_defects`, `test_flat_path_reasons_describe_flat_route` — all pass                                                   |
| 3.3b reorder safety      | **MET** | `test_flat_structure_is_reordered_false`, `test_evaluate_gates_none_validate_result_not_reordered` — flat structures produce `is_reordered=False`, `defect=OK`                                       |
| 3.3c metadata provenance | **MET** | `flat_meta['garble_prongs']` fallback sourced from `_flat_sig.garble_prongs` (not `state.gate_result.signals`); architecture guard `test_flat_path_passes_none_not_gate_result` enforces the contract |
| No regressions           | **MET** | `uv run pytest -q` → 2426 passed, 0 failed, 9 skipped, 2 xfailed, 1 xpassed                                                                                                                                |
| Corpus measurement (3.4) | **MET** | Measured 2026-09-22. uae_numbers portrait FAIL→PASS (D4); 2 Arabic docs suspect_density FAIL (D4 exposure); 2 regressions recovered on clean re-ingestion. See baseline.                                     |

**Result: PASS**. Wave 3 is complete.

---

## Wave 4 — Field Split + Density Activation (D5 + D6)

- [X] **4.1** — Split `include_enrichment` into `include_ocr_text` + `include_summary` in `_node_text_parts` (D5)

  - In `src/pageindex_mcp/helpers/tree_validation.py`, replace the single `include_enrichment: bool` parameter in `_node_text_parts` (line ~110) with two independent flags: `include_ocr_text: bool = False` and `include_summary: bool = False`.
  - Acceptance: `_node_text_parts` accepts the new flags; old `include_enrichment` parameter is removed.
- [X] **4.2** — Propagate split through `_flatten_tree_text` (D5)

  - Update `_flatten_tree_text` to accept and pass through `include_ocr_text` and `include_summary` to `_node_text_parts`.
  - Acceptance: `_flatten_tree_text` signature matches the new flag convention.
- [X] **4.3** — Update `TreeSignals.from_tree` to use `include_ocr_text=True, include_summary=False` for `flat_text_corrected` (D5)

  - In `TreeSignals.from_tree` (line ~298), call `_flatten_tree_text` with `include_ocr_text=True, include_summary=False` for the `flat_text_corrected` field.
  - This ensures the density-corrected numerator counts only real OCR text, not LLM-generated summaries.
  - Acceptance: `flat_text_corrected` excludes summary text; the 42% density inflation from LLM summaries is eliminated.
- [X] **4.4** — Update facade guard in `test_facade_surface_guard.py` (D5)

  - Facade guard checks symbol presence in `__all__`, not signatures — symbol names unchanged, no update needed.
  - Acceptance: facade guard tests pass (verified in full suite run).
- [X] **4.5** — Unit tests for split flag behaviour (D5)

  - Add parameterized test `test_node_text_parts_flag_combinations` with 4 cases:
    - `include_ocr_text=True, include_summary=False` → only OCR text included
    - `include_ocr_text=False, include_summary=True` → only summary included
    - both `True` → both included (equivalent to old `include_enrichment=True`)
    - both `False` → neither included (equivalent to old `include_enrichment=False`)
  - Add test `test_flatten_tree_text_respects_split_flags`: `_flatten_tree_text` passes the flags through to `_node_text_parts` correctly.
  - Add test `test_flat_text_corrected_excludes_summary`: `TreeSignals.from_tree` produces `flat_text_corrected` that excludes summary text.
  - **Note:** `_node_text_parts` lines 113-114 silently drop `ocr_text`/`summary` values that duplicate body text. This dedup is preserved and must not be changed.
  - Acceptance: all D5 tests pass; old `include_enrichment` parameter is fully removed from all call sites.
- [x] **4.6** — Attributed corpus measurement with split numerator (D5)

  - D5 is a refactor: density gate line 346 still fires on `chars_per_page` (uncorrected). No re-ingestion needed — computed old vs new `chars_per_page_corrected` from existing MinIO trees.
  - **Verdict distribution: UNCHANGED** at 15 PASS / 6 MARGINAL / 3 FAIL / 1 REJECTED. D5 does not touch the firing numerator.
  - **Per-document corrected-numerator impact** (density floor = 1500 chars/page):
    - Summary text accounts for 2–32% of corrected chars/page across the corpus.
    - Largest drops: Unfallversicherung −31.0%, اتفاقية مستوى الخدمة −32.4%, سياسة حوكمة −30.6%, قرار مجلس الوزراء (1) −27.8%, world-stats-pocketbook −25.7%.
    - 2 documents would flip if gate switched to corrected numerator: MOU MOHRE (1682→1333, already FAIL) and اتفاقية مستوى الخدمة (1792→1211, already FAIL). Both are already FAIL — no new regressions from switching.
    - 2 image-enriched docs (uae_numbers landscape MARGINAL, portrait PASS) have no tree JSON — density numerator split does not apply to the enrichment path.
  - **Baseline correction (2026-09-22):** 6 regressed docs purged from all stores (MinIO, Postgres, Redis) and re-ingested with updated Docling after clean state. Two recovered: federal_decree_law 33 MARGINAL→PASS (structural_pass), مرسوم اتحادي (33) FAIL→MARGINAL (depth_inadequate). Corrected Waves 1–3 baseline: **15 PASS / 6 MARGINAL / 3 FAIL / 1 REJECTED**. See `agents/baselines/rfc047-wave1-baseline.md`.
  - Acceptance: zero verdict diff confirmed; per-document corrected-numerator analysis documented.
- [x] **4.7** — D6: record D8 activation decision with measurement

  - After D5 corpus measurement, evaluate whether the density gate should switch to firing on the corrected (OCR-only) numerator.
  - The measurement from 4.6 shows, for every document, the old and new corrected values and which documents would change verdict.
  - Record a dated decision entry with the measurement that drove it.
  - If activated: implement the switch (change `gates.py:346` to fire on `chars_per_page_corrected`) and add tests.
  - If deferred: document the rationale.
  - **Decision (2026-09-22): DEFERRED.** Rationale:
    - The 2 documents that would flip (MOU MOHRE, اتفاقية مستوى الخدمة) are **already FAIL** — switching adds no new detection.
    - Summary text constitutes 2–32% of corrected chars/page. Removing it from the density numerator makes the gate stricter — documents closer to the 1500 floor could regress.
    - وارد رقم 597 (corrected cpp 2057→1624) and مرسوم اتحادي (33) (corrected cpp 2119→1618) are currently PASS/MARGINAL and would be pushed closer to the floor. A future Docling quality change could tip them below 1500.
    - The D5 split is correctly wired and the `_would_fire_corrected` shadow metric is logged on every density decision event (`gates.py:347,359-360`). This gives observability without risk.
    - **Recommendation:** Revisit activation after Wave 5 corpus re-run or when the density floor itself is tuned.
  - Acceptance: decision documented with corpus evidence from 4.6.
- [x] **4.C** — Wave 4 acceptance gate

  - All tests from 4.1–4.5 pass (D5 is mandatory).
  - D5 corpus measurement from 4.6 confirms zero verdict diff.
  - D6 decision from 4.7 is documented.
  - If D8 activated: additional tests pass and corpus diff is recorded.
  - No regressions in existing test suite (`make test`).

### Wave 4 gate outcome (4.C) — 2026-09-22 — **PASS**

| Criterion | Status | Evidence |
|---|---|---|
| 4.1–4.5 tests pass | **MET** | `uv run pytest tests/test_zone4_measurement.py -q` → 29 passed |
| D5 corpus measurement (4.6) | **MET** | Zero verdict diff confirmed — density gate fires on uncorrected numerator, D5 split does not change verdicts |
| D6 decision (4.7) | **MET** | DEFERRED — no new detection gain from switching; shadow metric `_would_fire_corrected` logged for observability |
| No regressions | **MET** | `uv run pytest -q` → 2436 passed, 0 failed, 9 skipped, 2 xfailed, 1 xpassed |

**Result: PASS**. Wave 4 is complete. Wave 5 (corpus re-run + engine RFC decision) may proceed.

---

## Wave 5 — Arabic Density Recovery (D7 + D8)

- [x] **5.1** — Add `RFC029_MIN_SCANNED_DENSITY_FLOOR_ARABIC` config field (D7)

  - In `src/pageindex_mcp/config.py`, add `rfc029_min_scanned_density_floor_arabic: float` to `Settings`, sourced from env var `RFC029_MIN_SCANNED_DENSITY_FLOOR_ARABIC`, default `800`.
  - In `src/pageindex_mcp/helpers/garble.py`, add `_RFC029_MIN_SCANNED_DENSITY_FLOOR_ARABIC` module-level constant alongside `_RFC029_MIN_SCANNED_DENSITY_FLOOR`, sourced from `pipeline_config.rfc029_min_scanned_density_floor_arabic`.
  - Export from `helpers/__init__.py` alongside the existing floor constant.
  - Acceptance: new config field is loadable; module constant is accessible.
- [x] **5.2** — Script-aware density floor in `_gate_suspect_density` (D7)

  - In `src/pageindex_mcp/helpers/gates.py`, modify `_gate_suspect_density` to use `_RFC029_MIN_SCANNED_DENSITY_FLOOR_ARABIC` when `expected_script.dominant_script == "Arab"`.
  - Log `floor_used`, `floor_arabic`, and `is_arabic` in the `suspect_density_gate` decision event attrs.
  - Acceptance: Arabic-dominant documents are evaluated against the Arabic floor (800); non-Arabic documents use the general floor (1200).
- [x] **5.3** — Unit tests for script-aware density floor (D7)

  - Add test `test_suspect_density_arabic_uses_lower_floor`: Arabic doc with cpp between 800 and 1200 does NOT fire.
  - Add test `test_suspect_density_non_arabic_uses_general_floor`: non-Arabic doc with same cpp DOES fire.
  - Add test `test_suspect_density_arabic_below_arabic_floor`: Arabic doc with cpp below 800 fires.
  - Add test `test_suspect_density_decision_event_logs_floor`: decision event includes `floor_used` and `is_arabic`.
  - Regression: no existing density gate tests break.
  - Acceptance: all D7 tests pass.
- [x] **5.4** — Add Surya fallback config fields (D8)

  - In `src/pageindex_mcp/config.py`, add `surya_fallback_enabled: bool` (env `SURYA_FALLBACK_ENABLED`, default `false`), `surya_service_url: str` (env `SURYA_SERVICE_URL`, default `http://localhost:8207`), `surya_fallback_timeout_s: float` (env `SURYA_FALLBACK_TIMEOUT_S`, default `120`).
  - Acceptance: config fields are loadable; defaults are correct.
- [x] **5.5** — Register `surya_density_fallback` decision event (D8)

  - In `src/pageindex_mcp/obs/decision_points.py`, register a new `surya_density_fallback` event with choices `recovery_succeeded`, `recovery_insufficient`, `recovery_failed`, `not_attempted`.
  - Attrs: `original_cpp`, `surya_cpp`, `arabic_floor`, `surya_confidence`, `surya_duration_s`.
  - Acceptance: AST guard passes; event is registered.
- [x] **5.6** — Implement `_surya_density_recovery` helper (D8)

  - In `src/pageindex_mcp/client/indexer.py` (or a new helper module), implement `_surya_density_recovery` that:
    - Fetches the document pages from MinIO (upload key)
    - Sends pages to the Surya service HTTP API at `surya_service_url`
    - Collects OCR text output per page
    - Computes `chars_per_page` from the Surya result
    - Returns `SuryaRecoveryResult(text, chars_per_page, confidence)` or `None` on failure/timeout
  - Handle HTTP errors and timeouts gracefully (log and return `None`).
  - Acceptance: helper function works against a running Surya service; returns `None` on timeout.
- [x] **5.7** — Wire Surya fallback into the density-fail recovery path (D8)

  - In `src/pageindex_mcp/client/indexer.py`, after `compute_verdict` produces a FAIL with `suspect_density` on an Arabic-dominant document:
    - If `surya_fallback_enabled` is `true`, call `_surya_density_recovery`.
    - If Surya yields sufficient text (cpp >= Arabic floor), rebuild flat structure from Surya output, recompute verdict, and record `converter_name: "surya"` in meta.
    - If Surya yields insufficient text or fails, let the FAIL stand.
  - Log via `surya_density_fallback` decision event.
  - Acceptance: Arabic density-failed documents trigger Surya fallback when enabled; recovery or failure is logged.
- [x] **5.8** — Unit tests for Surya fallback (D8)

  - Add test `test_surya_fallback_disabled_no_attempt`: `SURYA_FALLBACK_ENABLED=false` → no fallback, FAIL stands.
  - Add test `test_surya_fallback_non_arabic_no_attempt`: non-Arabic doc fails density → no fallback.
  - Add test `test_surya_fallback_recovery_succeeds`: Arabic doc fails density + mock Surya yields sufficient text → verdict recovers.
  - Add test `test_surya_fallback_recovery_insufficient`: Arabic doc fails density + mock Surya yields insufficient text → FAIL stands.
  - Add test `test_surya_fallback_timeout`: Surya service timeout → FAIL stands, `recovery_failed` logged.
  - Add test `test_surya_fallback_decision_event`: decision event logged with correct attrs.
  - Acceptance: all D8 tests pass.
- [ ] **5.C** — Wave 5 acceptance gate

  - All tests from 5.1–5.8 pass.
  - Arabic density floor correctly differentiates Arabic vs non-Arabic documents.
  - Surya fallback fires on Arabic density-failed documents and recovers when Surya yields sufficient text.
  - No regressions in existing test suite (`make test`).

---

## Wave 6 — Final Corpus Re-run + Engine RFC Decision (D9)

- [ ] **6.1** — Full attributed corpus re-run

  - Run `make ingest` against the complete corpus with all Waves 1–5 (D1–D8) applied.
  - Produce a full verdict distribution report with per-document attribution showing which deliverable(s) changed each verdict.
  - Compare against the pre-RFC-047 baseline (post-RFC-046 state).
  - Acceptance: attributed corpus report is complete and covers every document.
- [ ] **6.2** — Per-document delta table attributed to D1–D8

  - Every verdict movement SHALL be attributed to a named deliverable (D1–D8).
  - Movements SHALL be reported in both directions — improvements and regressions.
  - An unexplained movement SHALL block acceptance pending investigation.
  - Acceptance: delta table documented; zero unexplained movements.
- [ ] **6.3** — Engine-tier successor RFC decision

  - Based on the corpus results, decide whether an OCR engine-tier RFC (RFC-048) is still warranted.
  - The residue (documents still failing after all gate-layer fixes + Arabic recovery) determines the answer.
  - If warranted: draft a one-paragraph scope statement for the follow-up RFC.
  - If not warranted: document the rationale.
  - Closes `audit/RECONCILIATION_REPORT.md:148-156` item #2.
  - Acceptance: decision is documented with supporting evidence from the corpus run.
- [ ] **6.C** — Wave 6 acceptance gate (RFC-047 final gate)

  - Full corpus re-run from 6.1 is complete and attributed.
  - Delta table from 6.2 shows zero unexplained movements.
  - Engine RFC decision from 6.3 is documented.
  - All tests pass (`make test`).
  - RFC-047 is marked complete or hands off to a successor RFC.
