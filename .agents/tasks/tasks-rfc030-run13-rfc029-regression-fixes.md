<!-- Space: CITRA -->
<!-- Title: Implementation Plan: RFC-030 Run 13 RFC-029 Regression Fixes -->
<!-- Folder: Tasks -->

# Implementation Plan: RFC-030 Run 13 RFC-029 Regression Fixes

## Traceability

| Artifact | Reference |
|---|---|
| Governing RFC(s) | `../rfcs/030-run13-rfc029-regression-fixes.md` |
| Design Document | `../designs/design-rfc030-run13-rfc029-regression-fixes.md` |
| Related | `../rfcs/030-run13-rfc029-regression-fixes.md` (superseded by RFC-030 after Fable verification) |

## Overview

RFC-030 corrects three systemic root-cause clusters introduced by RFC-029 that drove Run 13's net regression (7P/9M/5F/4E vs Run 12's 10P/10M/4F/1E): unwired `validate_tree` failure reasons that raise unrecoverable `LowQualityTreeError` instead of persisting a FAIL verdict, a naive fence-toggle in `route_and_extract_flat` that silently destroys content, and a `_repeating_token_density` short-text floor that makes the OCR retry guardrail arithmetically impossible to win. Batch 1 lands the density-threshold and persist-with-FAIL routing fix together since D2 depends on D3's threshold to determine which documents fall through to persistence. Batch 2 lands the two independent content-loss fixes (fence-toggle rewrite, OCR retry floor fix). Batch 3 lands the lower-severity garble/bidi detection enhancements and the judge calibration documentation update. Implementation proceeds strictly in `helpers.py` / `client.py` code changes plus unit tests — corpus re-ingestion and live verification are handled by the separate corpus-cycle skill, not by tasks in this plan.

## Tasks

- [x] <a id="1-batch-1--density-threshold-and-persist-with-fail-routing-d3-d2"></a>1. Batch 1 — Density Threshold and Persist-with-FAIL Routing ([RFC-030 D3](../rfcs/030-run13-rfc029-regression-fixes.md#d3-lower-low_content_density-threshold-to-stop-rejecting-well-structured-legal-trees), [D2](../rfcs/030-run13-rfc029-regression-fixes.md#d2-persist-trees-with-unhandled-validate_tree-failure-reasons-as-fail-instead-of-raising-error))

  - [x] <a id="11-lower-low_content_density-threshold"></a>1.1 Lower `_RFC029_MIN_CHARS_PER_NODE` default from 500 to 150

    - Change the default value of `_RFC029_MIN_CHARS_PER_NODE` at `helpers.py:1102` from 500 to 150, keeping it env-configurable via `RFC029_MIN_CHARS_PER_NODE`
    - Fix the `validate_tree()` docstring at `helpers.py:1289` to state the actual gate condition (`total_nodes >= 200`), correcting the stale `total_nodes >= 3` reference
    - _Requirements: [Design §Property 7 — low_content_density threshold lowered](../designs/design-rfc030-run13-rfc029-regression-fixes.md#property-7-low_content_density-threshold-lowered-to-150-charsnode) | [RFC-030 D3](../rfcs/030-run13-rfc029-regression-fixes.md#d3-lower-low_content_density-threshold-to-stop-rejecting-well-structured-legal-trees)_
  - [x]* <a id="12-threshold-boundary-property-tests"></a>1.2 Property tests for the density threshold boundary

    - **Property 7: low_content_density threshold lowered to 150 chars/node**
    - Unit test: tree with 300 nodes at 300 chars/node passes the gate (previously rejected at the 500 threshold)
    - Unit test: tree with 300 nodes at 50 chars/node still fails the gate
    - Unit test: tree with 200 nodes at 160 chars/node passes (above the new 150 threshold)
    - **Validates: [RFC-030 D3](../rfcs/030-run13-rfc029-regression-fixes.md#d3-lower-low_content_density-threshold-to-stop-rejecting-well-structured-legal-trees)**
  - [x] <a id="13-persist-unhandled-reasons-as-fail"></a>1.3 Add persist-with-FAIL branch for unhandled `validate_tree` reasons

    - In `client.py::index()`, add a branch before the terminal `raise LowQualityTreeError` (~line 1639) that catches `low_content_density`, `suspect_density`, `empty_node_contamination`, and `arabic_low_content_ratio`
    - Set `ok = False` and fall through to `save_doc` / `classify_verdict` instead of raising, so the tree persists with the FAIL verdict `classify_verdict` already assigns for these reasons (`helpers.py:1541-1553`)
    - Do not route these reasons through flat extraction or OCR retry — the tree structure is preserved as-is per CLAUDE.md Hard Rule 5 (no silent persistence of an unvalidated tree; here the tree IS validated and explicitly marked FAIL, not silently persisted)
    - _Requirements: [Design §Property 6 — unhandled reasons persist as FAIL](../designs/design-rfc030-run13-rfc029-regression-fixes.md#property-6-unhandled-validate_tree-reasons-persist-as-fail-not-error) | [RFC-030 D2](../rfcs/030-run13-rfc029-regression-fixes.md#d2-persist-trees-with-unhandled-validate_tree-failure-reasons-as-fail-instead-of-raising-error)_
  - [x]* <a id="14-persist-with-fail-property-tests"></a>1.4 Property tests for persist-with-FAIL routing

    - **Property 6: Unhandled validate_tree reasons persist as FAIL, not ERROR**
    - Unit test: tree triggering `low_content_density` is persisted (`save_doc` called) with `classify_verdict` returning FAIL, not `LowQualityTreeError` raised
    - Unit test: same assertion for `suspect_density`, `empty_node_contamination`, `arabic_low_content_ratio`
    - Unit test: persisted tree structure is unchanged (no flat extraction, no OCR retry triggered) for any of the four reasons
    - Unit test: existing PASS-path trees (no unhandled reason present) still route through the normal tree path unaffected
    - **Validates: [RFC-030 D2](../rfcs/030-run13-rfc029-regression-fixes.md#d2-persist-trees-with-unhandled-validate_tree-failure-reasons-as-fail-instead-of-raising-error)**

- [x] <a id="2-checkpoint--batch-1"></a>2. Checkpoint — Batch 1

  - Run `uv run pytest tests/test_rfc030_d2_d3.py -v` (or equivalent test module) and verify all property tests (Properties 6, 7) pass
  - Confirm `_RFC029_MIN_CHARS_PER_NODE` docstring and env-var default are consistent
  - Ask the user if questions arise before proceeding.

- [x] <a id="3-batch-2--content-loss-fixes-d0-d1"></a>3. Batch 2 — Content-Loss Fixes ([RFC-030 D0](../rfcs/030-run13-rfc029-regression-fixes.md#d0-fix-fence-toggle-content-destruction-in-route_and_extract_flat), [D1](../rfcs/030-run13-rfc029-regression-fixes.md#d1-fix-_repeating_token_density-short-text-floor-breaking-ocr-retry-guardrail))

  - [x] <a id="31-rewrite-fence-toggle-as-delimiter-only-strip"></a>3.1 Replace the fence-parity toggle with delimiter-only stripping

    - In `route_and_extract_flat()` (`helpers.py:2711-2726`), replace the `in_fence` boolean parity toggle with logic that strips only the triple-backtick delimiter lines themselves, letting enclosed content fall through to the normal prose/table parsers
    - Alternatively: pre-scan the input for fence-marker count; if the count is odd, treat only paired fences as real fences and pass unpaired trailing markers through as noise text (do not toggle a persistent skip state)
    - Ensure no line is skipped as "content" solely because it falls between fence markers — only the marker lines themselves are dropped
    - _Requirements: [Design §Property 1 — fence-delimiter stripping preserves content](../designs/design-rfc030-run13-rfc029-regression-fixes.md#property-1-fence-delimiter-only-stripping-preserves-enclosed-content) | [RFC-030 D0](../rfcs/030-run13-rfc029-regression-fixes.md#d0-fix-fence-toggle-content-destruction-in-route_and_extract_flat)_
  - [x] <a id="32-review-hr-separator-stripping"></a>3.2 Review HR-separator stripping for over-aggressiveness

    - Audit the HR-separator stripping logic at `helpers.py:2733-2737` implicated in the Reitlehrer 32% char-count reduction
    - Tighten the match condition so genuine horizontal-rule markdown (`---`, `***`) is stripped but prose lines that merely contain repeated dash/asterisk runs are not misclassified and dropped
    - _Requirements: [Design §Property 1 — fence-delimiter stripping preserves content](../designs/design-rfc030-run13-rfc029-regression-fixes.md#property-1-fence-delimiter-only-stripping-preserves-enclosed-content) | [RFC-030 D0](../rfcs/030-run13-rfc029-regression-fixes.md#d0-fix-fence-toggle-content-destruction-in-route_and_extract_flat)_
  - [x] <a id="33-zero-block-guard-in-client"></a>3.3 Add post-extraction zero-block guard in client.py

    - In the flat-routing caller in `client.py::index()`, detect when `route_and_extract_flat` returns `(content_class, [])` from non-empty input markdown
    - Treat this as an extraction failure requiring escalation (re-run without the fence heuristic, or raise the same error path already used for tree-routed docs) instead of persisting a 0-block flat.json
    - _Requirements: [Design §Property 2 — zero-block output triggers escalation](../designs/design-rfc030-run13-rfc029-regression-fixes.md#property-2-zero-block-flat-extraction-triggers-escalation-not-persistence) | [RFC-030 D0](../rfcs/030-run13-rfc029-regression-fixes.md#d0-fix-fence-toggle-content-destruction-in-route_and_extract_flat)_
  - [x] <a id="34-update-fence-edge-case-test"></a>3.4 Update the fence edge-case test to assert content preservation

    - Update `tests/test_rfc029_d3.py::TestEdgeCases::test_unclosed_fence_content_is_skipped` (rename to reflect new expected behavior) to assert content after an unclosed opening fence is preserved as prose, not silently dropped
    - _Requirements: [Design §Property 1 — fence-delimiter stripping preserves content](../designs/design-rfc030-run13-rfc029-regression-fixes.md#property-1-fence-delimiter-only-stripping-preserves-enclosed-content) | [RFC-030 D0](../rfcs/030-run13-rfc029-regression-fixes.md#d0-fix-fence-toggle-content-destruction-in-route_and_extract_flat)_
  - [x]* <a id="35-fence-fix-property-tests"></a>3.5 Property tests for fence-delimiter stripping and zero-block guard

    - **Property 1: Fence-delimiter-only stripping preserves enclosed content**
    - **Property 2: Zero-block flat extraction triggers escalation, not persistence**
    - Unit test: markdown with paired fence blocks preserves enclosed content as prose blocks
    - Unit test: markdown with an odd number of fence markers (unclosed fence) preserves all content after the stray marker
    - Unit test: zero-block output from non-empty markdown triggers the escalation path, not silent persistence of an empty flat.json
    - **Validates: [RFC-030 D0](../rfcs/030-run13-rfc029-regression-fixes.md#d0-fix-fence-toggle-content-destruction-in-route_and_extract_flat)**
  - [x] <a id="36-repeating-token-density-none-floor"></a>3.6 Change `_repeating_token_density` short-text floor from 0.0 to None

    - In `_repeating_token_density()` (`client.py:1083-1096`), change the short-text return value from `0.0` to `None` when token count is below 20 alnum tokens
    - _Requirements: [Design §Property 3 — density floor returns None](../designs/design-rfc030-run13-rfc029-regression-fixes.md#property-3-ocr-retry-short-text-density-floor-returns-none-not-zero) | [RFC-030 D1](../rfcs/030-run13-rfc029-regression-fixes.md#d1-fix-_repeating_token_density-short-text-floor-breaking-ocr-retry-guardrail)_
  - [x] <a id="37-retry-wins-short-circuit"></a>3.7 Short-circuit retry_wins=True when pre-density is None

    - In the D4 keep-best comparator block (`client.py:1111-1148`), when `_pre_density` is `None`, set `retry_wins = True` unconditionally — any real OCR recovery beats a near-empty/garbled pre-retry snapshot
    - Add a minimum absolute quality floor on the post-retry output (e.g., require at least N chars or N nodes) before accepting the retry, per the RFC's stated mitigation for the "retry always wins" risk
    - _Requirements: [Design §Property 4 — retry-wins short-circuit](../designs/design-rfc030-run13-rfc029-regression-fixes.md#property-4-retry-wins-short-circuit-when-pre-density-is-none) | [RFC-030 D1](../rfcs/030-run13-rfc029-regression-fixes.md#d1-fix-_repeating_token_density-short-text-floor-breaking-ocr-retry-guardrail)_
  - [x] <a id="38-atomic-revert-of-retry-state"></a>3.8 Make the OCR retry revert path atomic across all retry-derived state

    - Snapshot `md_content`, `tmp_md_path`, and `pic_results` alongside `result`/`ok`/`reason` before the OCR retry attempt
    - When the retry loses (`retry_wins = False`), restore all six variables together at the revert site (`client.py:1144-1147`) so the tree path and the downstream flat-routing markdown path cannot diverge on which extraction was actually used
    - _Requirements: [Design §Property 5 — atomic revert](../designs/design-rfc030-run13-rfc029-regression-fixes.md#property-5-atomic-revert-of-md_content-tmp_md_path-pic_results) | [RFC-030 D1](../rfcs/030-run13-rfc029-regression-fixes.md#d1-fix-_repeating_token_density-short-text-floor-breaking-ocr-retry-guardrail)_
  - [x]* <a id="39-ocr-retry-property-tests"></a>3.9 Property tests for the OCR retry guardrail fix

    - **Property 3: OCR retry short-text density floor returns None, not zero**
    - **Property 4: retry-wins short-circuit when pre-density is None**
    - **Property 5: atomic revert of md_content/tmp_md_path/pic_results**
    - Unit test: `_repeating_token_density` returns `None` for text with <20 alnum tokens
    - Unit test: when `_pre_density` is `None`, `retry_wins` is `True` regardless of `_post_density` value (subject to the absolute quality floor)
    - Unit test: when retry loses, `md_content`/`tmp_md_path`/`pic_results` are reverted alongside `result`/`ok`/`reason` — all six variables consistent post-revert
    - **Validates: [RFC-030 D1](../rfcs/030-run13-rfc029-regression-fixes.md#d1-fix-_repeating_token_density-short-text-floor-breaking-ocr-retry-guardrail)**

- [x] <a id="4-checkpoint--batch-2"></a>4. Checkpoint — Batch 2

  - Run `uv run pytest tests/test_rfc030_d0_d1.py -v` (or equivalent test module) and verify all property tests (Properties 1-5) pass
  - Run the full existing `tests/test_rfc029_d3.py` module and confirm the updated fence edge-case assertion passes with no other regressions
  - Ask the user if questions arise before proceeding.

- [x] <a id="5-batch-3--garble-bidi-and-judge-calibration-d4-d5-d6"></a>5. Batch 3 — Garble/Bidi Detection and Judge Calibration ([RFC-030 D4](../rfcs/030-run13-rfc029-regression-fixes.md#d4-extend-garble-gate-to-inspect-node-title-field), [D5](../rfcs/030-run13-rfc029-regression-fixes.md#d5-wire-_check_bidi_coherence-into-validate_tree-pipeline), [D6](../rfcs/030-run13-rfc029-regression-fixes.md#d6-write-judge-calibration-rules-to-corpus-ingest-score-skill-file))

  - [x] <a id="51-title-field-garble-inspection"></a>5.1 Add title-field inspection to `_garble_check_nodes`

    - In `_garble_check_nodes()` (`helpers.py:1153-1186`), after inspecting `node.get('text')`, also inspect `node.get('title')` with the same garble/RTL-reversal checks, including `_word_has_reversed_morphology` for Arabic titles
    - Mark a node as garbled if either the text or the title field fails the check
    - _Requirements: [Design §Property 8 — garble gate inspects titles](../designs/design-rfc030-run13-rfc029-regression-fixes.md#property-8-garble-gate-inspects-node-title-field) | [RFC-030 D4](../rfcs/030-run13-rfc029-regression-fixes.md#d4-extend-garble-gate-to-inspect-node-title-field)_
  - [x] <a id="52-flatten-tree-text-includes-titles"></a>5.2 Include title text in `_flatten_tree_text` concatenation

    - Update `_flatten_tree_text()` to prepend each node's title to its text with a separator before concatenation, so `_tree_is_garbled` and `classify_verdict` inherit title-level corruption detection
    - _Requirements: [Design §Property 9 — flatten_tree_text includes titles](../designs/design-rfc030-run13-rfc029-regression-fixes.md#property-9-_flatten_tree_text-includes-title-text) | [RFC-030 D4](../rfcs/030-run13-rfc029-regression-fixes.md#d4-extend-garble-gate-to-inspect-node-title-field)_
  - [x]* <a id="53-title-garble-property-tests"></a>5.3 Property tests for title-field garble detection

    - **Property 8: Garble gate inspects node title field**
    - **Property 9: `_flatten_tree_text` includes title text**
    - Unit test: node with garbled title but clean text is detected by `_garble_check_nodes`
    - Unit test: node with RTL-reversed Arabic title is detected via `_word_has_reversed_morphology`
    - Unit test: `_flatten_tree_text` output includes title text for every node
    - **Validates: [RFC-030 D4](../rfcs/030-run13-rfc029-regression-fixes.md#d4-extend-garble-gate-to-inspect-node-title-field)**
  - [x] <a id="54-deduplicate-check_bidi_coherence"></a>5.4 Deduplicate `_check_bidi_coherence` definition

    - Remove the duplicate `_check_bidi_coherence` definition in `helpers.py` (two copies exist at lines 936 and 1028); keep the more complete one (line 1028)
    - _Requirements: [Design §Property 10 — bidi coherence wired and deduplicated](../designs/design-rfc030-run13-rfc029-regression-fixes.md#property-10-_check_bidi_coherence-wired-into-validate_tree-deduplicated) | [RFC-030 D5](../rfcs/030-run13-rfc029-regression-fixes.md#d5-wire-_check_bidi_coherence-into-validate_tree-pipeline)_
  - [x] <a id="55-wire-bidi-coherence-into-validate_tree"></a>5.5 Wire `_check_bidi_coherence` into `validate_tree`

    - Add a call to `_check_bidi_coherence` inside `validate_tree()`, immediately after the existing `_tree_is_rtl_reversed` check, returning `(False, 'visual_order_garble')` when the check fails
    - Confirm `'visual_order_garble'` is already present in the `client.py` OCR-escalation whitelist (~line 1011); add it if missing, mirroring existing `garbling`/`node_garbling` routing
    - _Requirements: [Design §Property 10 — bidi coherence wired and deduplicated](../designs/design-rfc030-run13-rfc029-regression-fixes.md#property-10-_check_bidi_coherence-wired-into-validate_tree-deduplicated) | [RFC-030 D5](../rfcs/030-run13-rfc029-regression-fixes.md#d5-wire-_check_bidi_coherence-into-validate_tree-pipeline)_
  - [x]* <a id="56-bidi-coherence-property-tests"></a>5.6 Property tests for bidi coherence wiring

    - **Property 10: `_check_bidi_coherence` wired into validate_tree, deduplicated**
    - Unit test: tree with visual-order Arabic text (reversed morphology) triggers `_check_bidi_coherence` failure
    - Unit test: `validate_tree` returns `(False, 'visual_order_garble')` for a bidi-incoherent tree
    - Unit test: only one definition of `_check_bidi_coherence` exists in `helpers.py` after the fix (e.g., via `ast`-based symbol count or grep-count assertion)
    - **Validates: [RFC-030 D5](../rfcs/030-run13-rfc029-regression-fixes.md#d5-wire-_check_bidi_coherence-into-validate_tree-pipeline)**
  - [x] <a id="57-judge-calibration-rules-in-skill-file"></a>5.7 Write judge calibration rules to `corpus-ingest-score` skill file

    - Add the stability rule to `.claude/skills/corpus-ingest-score/SKILL.md` (after the existing RFC-028 D6 paragraph, lines 34-42): stored PASS with extraction metrics within 10% of the prior run must not be downgraded without citing a specific new content-quality defect
    - Add the severity-anchoring rule: flat/chart docs (`content_class` starts with `flat_`) with <1000 total chars and zero picture enrichments anchor to MARGINAL, not FAIL, when the extraction layer has not regressed
    - Add a consistency-check note to `.claude/skills/corpus-score-diff/SKILL.md` for byte-identical artifacts across runs
    - _Requirements: [Design §Property 11 — judge calibration rules prevent verdict instability](../designs/design-rfc030-run13-rfc029-regression-fixes.md#property-11-judge-calibration-rules-prevent-verdict-instability-on-unchanged-content) | [RFC-030 D6](../rfcs/030-run13-rfc029-regression-fixes.md#d6-write-judge-calibration-rules-to-corpus-ingest-score-skill-file)_

- [x] <a id="6-checkpoint--batch-3"></a>6. Checkpoint — Batch 3

  - Run `uv run pytest tests/test_rfc030_d4_d5.py -v` (or equivalent test module) and verify all property tests (Properties 8, 9, 10) pass
  - Review the `.claude/skills/corpus-ingest-score/SKILL.md` and `.claude/skills/corpus-score-diff/SKILL.md` diffs to confirm both calibration rules are correctly placed and worded
  - Ask the user if questions arise before proceeding.

- [x] <a id="7-final-checkpoint"></a>7. Final Checkpoint

  - Run `uv run pytest` (full suite) and verify zero failures across the RFC-030 test modules plus all pre-existing RFC-025 through RFC-029 test modules (no regressions introduced)
  - Confirm no task in this plan performed corpus ingestion, scoring, or re-ingestion — those steps belong to the separate corpus-cycle skill, not this implementation plan
  - Ask the user if questions arise before proceeding.

## Notes

- [Task 1.1](#11-lower-low_content_density-threshold)'s threshold reduction is a hard prerequisite for [Task 1.3](#13-persist-unhandled-reasons-as-fail)'s persist-with-FAIL wiring — per [RFC-030's Implementation Plan](../rfcs/030-run13-rfc029-regression-fixes.md#implementation-plan), D3 determines which documents still trigger `low_content_density` before D2 decides how those documents are routed. Land D3 first within Batch 1.
- [Task 3.1](#31-rewrite-fence-toggle-as-delimiter-only-strip) through [3.9](#39-ocr-retry-property-tests) (D0, D1) have no dependency on Batch 1 and could theoretically run in parallel with it, but are sequenced into Batch 2 per the RFC's own batch plan to keep validate_tree-routing changes and content-loss changes independently testable at their own checkpoints.
- [Task 3.3](#33-zero-block-guard-in-client)'s zero-block guard must re-run or raise, never silently persist an empty `flat.json` — this directly enforces CLAUDE.md Hard Rule 5 (never silently persist a low-quality tree) applied to the flat-routing output path.
- [Task 3.7](#37-retry-wins-short-circuit)'s unconditional `retry_wins=True` when `_pre_density` is `None` carries an accepted risk (per [RFC-030 Risks](../rfcs/030-run13-rfc029-regression-fixes.md#risks)): a retry that produces worse output than the pre-retry snapshot could still win. The absolute quality floor required in the same task is the mitigation — do not land 3.7 without it.
- [Task 5.4](#54-deduplicate-check_bidi_coherence)/[5.5](#55-wire-bidi-coherence-into-validate_tree)'s `_check_bidi_coherence` has never run against the full corpus in a live pipeline (dead code since RFC-029). Per the RFC's stated mitigation, consider an audit-only logging mode for one corpus cycle before this wiring gates routing decisions in production — this is a deployment/rollout decision outside this tasks file's code-change scope, to be handled by the corpus-cycle skill.
- [Task 1.3](#13-persist-unhandled-reasons-as-fail)'s persist-with-FAIL fix does **not** address the وارد رقم 597 (warid-597) timeout, which the RFC's live audit verification shows is a pre-`validate_tree` failure (document never persisted, `NoSuchKey` on meta.json lookup). Per [RFC-030 D2's warid-597 reconciliation](../rfcs/030-run13-rfc029-regression-fixes.md#d2-persist-trees-with-unhandled-validate_tree-failure-reasons-as-fail-instead-of-raising-error), this requires separate investigation (job-level timeout guard or OCR path performance fix) and is explicitly out of scope for this tasks file.
- [Task 1.1](#11-lower-low_content_density-threshold)'s threshold change does not guarantee `federal_decree_law_no_33_of_2021` recovers to PASS (54.3 chars/node remains below even the lowered 150 threshold) — per RFC-030, full recovery requires a separate investigation into the 502→2042 node-explosion mechanism, which is explicitly deferred.
- `_segment_table_nodes` primary-path wiring (GHV-TKV-Tarif table-segmentation stall) is explicitly deferred per RFC-030 D3 and has no task in this plan.
- Tasks marked with `*` are property-based tests validating the 10 correctness properties defined in the design doc; they may be implemented alongside their corresponding implementation task rather than strictly after it, but must land in the same batch checkpoint.
- No task in this plan performs corpus ingestion, live re-ingestion, or verification against MinIO — those are handled exclusively by the `corpus-cycle` / `corpus-ingest` / `corpus-ingest-score` skills in a separate workflow, per this task's explicit scope constraint.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "1.4"] },
    { "id": 2, "tasks": ["2"] },
    { "id": 3, "tasks": ["3.1", "3.2", "3.6"] },
    { "id": 4, "tasks": ["3.3", "3.4", "3.7"] },
    { "id": 5, "tasks": ["3.8", "3.5", "3.9"] },
    { "id": 6, "tasks": ["4"] },
    { "id": 7, "tasks": ["5.1", "5.4", "5.7"] },
    { "id": 8, "tasks": ["5.2", "5.5", "5.3"] },
    { "id": 9, "tasks": ["5.6", "6"] },
    { "id": 10, "tasks": ["7"] }
  ]
}
```
