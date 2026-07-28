<!-- Space: CITRA -->
<!-- Title: Tasks: RFC-020 Run 3 Regression Remediation -->
<!-- Folder: Tasks -->

# Implementation Plan: RFC-020 Run 3 Regression Remediation — Tree/Image/Garble Pipeline Fixes

## Traceability

| Artifact               | Reference                                                                                                                                                        |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Governing RFC          | [RFC-020: Run 3 Regression Remediation](../rfcs/020-run3-regression-remediation.md)                                                                              |
| Design Document        | [Design: RFC-020 Run 3 Regression Remediation](../designs/design-rfc020-run3-regression-remediation.md)                                                          |
| PRD / Requirements     | `PRD.md`                                                                                                                                                         |
| Hard Rules             | [CLAUDE.md HR2](../../CLAUDE.md) (erasure cascade), [CLAUDE.md HR3](../../CLAUDE.md) (ZDR routing), [CLAUDE.md HR5](../../CLAUDE.md) (no silent low-quality tree)   |
| Implementation Order   | [RFC-020 §Implementation Plan](../rfcs/020-run3-regression-remediation.md#implementation-plan)                                                                   |
| Test Strategy          | [RFC-020 §Test Strategy](../rfcs/020-run3-regression-remediation.md#test-strategy)                                                                               |
| Correctness Properties | [Design §Correctness Properties](../designs/design-rfc020-run3-regression-remediation.md#correctness-properties)                                                 |
| Predecessors           | RFC-017 (P0a/P0b) → RFC-018 (D0-D3) → RFC-019 (D0-D4) → this RFC-020                                                                                             |

## Overview

This plan implements six regression fixes ([F0](../rfcs/020-run3-regression-remediation.md#f0-restore-per-picture-ocr-splice-to-tree-path-p0--critical)–[F5](../rfcs/020-run3-regression-remediation.md#f5-accurate-skipped_reason-attribution-in-_recover_picture_results-p2)) for the three regression categories identified by the Run 3 corpus reingestion audit (8 PASS / 11 MARGINAL / 5 FAIL / 1 ERROR), proceeding through the [RFC-020 implementation phases](../rfcs/020-run3-regression-remediation.md#implementation-plan) and validating against the six [correctness properties](../designs/design-rfc020-run3-regression-remediation.md#correctness-properties) in the design document. Total effort: **~3.5-4 person-days** across 5 phases on branch `feat/image-block-picture-ocr`. All fixes are new (nothing pre-landed); [F0](../rfcs/020-run3-regression-remediation.md#f0-restore-per-picture-ocr-splice-to-tree-path-p0--critical) is the critical-path item and lands first. Target: [15-17 PASS on Run 4](../rfcs/020-run3-regression-remediation.md#beforeafter-corpus-impact).

## Tasks

- [ ] <a id="1-phase-1--f0-tree-path-splice-restoration"></a>1. Phase 1 — [F0](../rfcs/020-run3-regression-remediation.md#f0-restore-per-picture-ocr-splice-to-tree-path-p0--critical) tree-path splice restoration (1.0 d)

  *[RFC-020 §Implementation Plan — Phase 1](../rfcs/020-run3-regression-remediation.md#implementation-plan): the critical regression ([Regression 1, Cause 1](../rfcs/020-run3-regression-remediation.md#cause-1-primary-critical--per-picture-ocr-splice-moved-to-flat-only-path)) — per-picture OCR silently discarded for ALL tree-path documents.*

  - [x] <a id="11-implement-tree-path-splice-helper"></a>1.1 Implement `splice_picture_text_for_tree` helper (P0, effort: M)

    - In `src/pageindex_mcp/converters.py`, beside `splice_figure_markers` (L1536-1581): new `def splice_picture_text_for_tree(md: str, pics: list[PictureResult]) -> str`
    - Append `> [Chart text]: {ocr_text}` after each `<!-- image -->` marker whose ordinal `PictureResult` has non-empty `ocr_text` (matching master's `_maybe_splice_picture_ocr` output format)
    - Leave markers intact so the flat branch's `splice_figure_markers` (`client.py:940`) still resolves them — the two splices must compose
    - Apply the `marker_count == len(pics)` ordinal guard verbatim; on mismatch return `md` unchanged and log WARNING with both counts
    - _Requirements:_ [RFC-020 F0](../rfcs/020-run3-regression-remediation.md#f0-restore-per-picture-ocr-splice-to-tree-path-p0--critical) | [Design Property 1](../designs/design-rfc020-run3-regression-remediation.md#property-1-tree-path-ocr-splice-parity) | [Design Service: converters.py](../designs/design-rfc020-run3-regression-remediation.md#2-converterspy) | [Design AD1](../designs/design-rfc020-run3-regression-remediation.md#ad1-splice-markdown-before-tree-parse-f0) | [Design Sequence: Tree-Path Splice Flow](../designs/design-rfc020-run3-regression-remediation.md#tree-path-splice-flow--f0)
  - [x] <a id="12-wire-splice-into-clientindex"></a>1.2 Wire splice into `client.index()` before markdown persistence (P0, effort: S)

    - In `src/pageindex_mcp/client.py`, immediately after conversion returns `(md_content, pic_results)` and BEFORE the markdown is written to disk (because `_run_md_to_tree` at `client.py:1188` reads the on-disk file):
      - `if pic_results and TREE_PATH_PICTURE_SPLICE_ENABLED: md_content = splice_picture_text_for_tree(md_content, pic_results)`
    - Add `TREE_PATH_PICTURE_SPLICE_ENABLED` env var (default `true`) as the F0 kill switch
    - Verify the flat branch (`client.py:940`) still receives the spliced markdown and composes correctly
    - _Requirements:_ [RFC-020 F0](../rfcs/020-run3-regression-remediation.md#f0-restore-per-picture-ocr-splice-to-tree-path-p0--critical) | [Design Property 1](../designs/design-rfc020-run3-regression-remediation.md#property-1-tree-path-ocr-splice-parity) | [Design Service: client.py](../designs/design-rfc020-run3-regression-remediation.md#1-clientpy) | [Design AD1](../designs/design-rfc020-run3-regression-remediation.md#ad1-splice-markdown-before-tree-parse-f0) | [Design Sequence: Tree-Path Splice Flow](../designs/design-rfc020-run3-regression-remediation.md#tree-path-splice-flow--f0)
  - [x] <a id="13-f0-unit-and-integration-tests"></a>1.3 F0 unit and integration tests (P0, effort: M)

    - Unit: (a) `pic_results` with OCR text → output contains `> [Chart text]:` blocks in marker order; (b) `pic_results=[]` → markdown byte-identical; (c) count mismatch → unchanged + WARNING; (d) markers preserved after splice; (e) composition test: `splice_picture_text_for_tree` then `splice_figure_markers` yields chart text AND `[Figure: fig-N]` without duplication; (f) `TREE_PATH_PICTURE_SPLICE_ENABLED=false` → branch-HEAD behavior
    - Integration: Arabic scanned-page fixture (Docling classifies page as Picture) → spliced markdown → `md_to_tree` produces `depth>=2` → `validate_tree` passes → NO flat-routing at `client.py:859`
    - Regression guard: assert this test fails on branch HEAD (proves it catches [Regression 1 Cause 1](../rfcs/020-run3-regression-remediation.md#cause-1-primary-critical--per-picture-ocr-splice-moved-to-flat-only-path))
    - **Validates:** [Design Property 1](../designs/design-rfc020-run3-regression-remediation.md#property-1-tree-path-ocr-splice-parity) | [RFC-020 F0](../rfcs/020-run3-regression-remediation.md#f0-restore-per-picture-ocr-splice-to-tree-path-p0--critical) | [RFC-020 §Test Strategy F0 row](../rfcs/020-run3-regression-remediation.md#test-strategy)
    - _Requirements:_ [RFC-020 F0](../rfcs/020-run3-regression-remediation.md#f0-restore-per-picture-ocr-splice-to-tree-path-p0--critical) | [Design Property 1](../designs/design-rfc020-run3-regression-remediation.md#property-1-tree-path-ocr-splice-parity) | [Design Sequence: Tree-Path Splice Flow](../designs/design-rfc020-run3-regression-remediation.md#tree-path-splice-flow--f0)
  - [x] <a id="14-checkpoint--phase-1"></a>1.4 Checkpoint — Phase 1

    - Run `uv run pytest` — all tests green
    - Verify [Property 1](../designs/design-rfc020-run3-regression-remediation.md#property-1-tree-path-ocr-splice-parity) validated by [Task 1.3](#13-f0-unit-and-integration-tests)
    - Cross-ref: [RFC-020 §Implementation Plan checkpoint 1](../rfcs/020-run3-regression-remediation.md#implementation-plan)
    - Ask the user if questions arise before proceeding to [Phase 2](#2-phase-2--f1-coverage-exemption--f5-skip-reason)

- [ ] <a id="2-phase-2--f1-coverage-exemption--f5-skip-reason"></a>2. Phase 2 — [F1](../rfcs/020-run3-regression-remediation.md#f1-exempt-no-text-layer-full-page-scans-from-the-coverage-filter-p0) coverage exemption + [F5](../rfcs/020-run3-regression-remediation.md#f5-accurate-skipped_reason-attribution-in-_recover_picture_results-p2) skip-reason (1.0 d)

  *[RFC-020 §Implementation Plan — Phase 2](../rfcs/020-run3-regression-remediation.md#implementation-plan): both fixes live in `_recover_picture_text`; land together. Addresses [Regression 1 Cause 2](../rfcs/020-run3-regression-remediation.md#cause-2-compounding--d0-page-coverage-skip-blocks-full-page-recovery) and [Regression 2](../rfcs/020-run3-regression-remediation.md#regression-2--zero-image-enrichment-docs-3-9).*

  - [x] <a id="21-implement-f1-coverage-exemption"></a>2.1 Implement F1 text-layer-gated coverage exemption (P0, effort: M)

    - In `src/pageindex_mcp/converters.py:1471-1474` (`_recover_picture_text`): coverage skip (`coverage > _PICTURE_PAGE_COVERAGE_THRESHOLD`, threshold at `converters.py:1327-1329`) fires only when `_text_layer_has_content(page)` is true
    - No-text-layer full-page regions fall through to crop + OCR (the picture IS the page content); the clip-text filter (`converters.py:1475-1479`) naturally passes (empty `clip_text`)
    - Add `COVERAGE_EXEMPT_NO_TEXT_LAYER` env var (default `true`); `false` restores unconditional RFC-018 D0 skipping
    - Log WARNING when exemption fires (page number, coverage %)
    - _Requirements:_ [RFC-020 F1](../rfcs/020-run3-regression-remediation.md#f1-exempt-no-text-layer-full-page-scans-from-the-coverage-filter-p0) | [Design Property 2](../designs/design-rfc020-run3-regression-remediation.md#property-2-full-page-scan-text-recovery) | [Design Service: converters.py](../designs/design-rfc020-run3-regression-remediation.md#2-converterspy) | [Design AD2](../designs/design-rfc020-run3-regression-remediation.md#ad2-text-layer-gated-coverage-exemption-f1) | [Design Sequence: Full-Page Scan Recovery Flow](../designs/design-rfc020-run3-regression-remediation.md#full-page-scan-recovery-flow--f1)
  - [x] <a id="22-implement-f5-skip-reason-plumbing"></a>2.2 Implement F5 skip-reason plumbing (P2, effort: S)

    - `_recover_picture_text` (`converters.py:1427-1527`) returns `(recovered, skip_reasons: dict[int, str])` with values `"page_coverage"` / `"clip_text"`
    - `_recover_picture_results` (`converters.py:1598-1636`): replace hardcoded `skipped_reason="page_coverage"` at `converters.py:1628` with `skip_reasons.get(i, "unknown")`
    - Confirm `splice_figure_markers` strip-vs-keep branch (RFC-019 D3) treats all non-empty reasons identically — no behavior change, diagnostics only
    - _Requirements:_ [RFC-020 F5](../rfcs/020-run3-regression-remediation.md#f5-accurate-skipped_reason-attribution-in-_recover_picture_results-p2) | [Design Property 6](../designs/design-rfc020-run3-regression-remediation.md#property-6-accurate-skip-reason-attribution) | [Design Service: converters.py](../designs/design-rfc020-run3-regression-remediation.md#2-converterspy) | [Design AD6](../designs/design-rfc020-run3-regression-remediation.md#ad6-skip-reason-returned-from-source-f5)
  - [x] <a id="23-f1-and-f5-tests"></a>2.3 F1 and F5 tests (P0, effort: M)

    - F1: (a) full-page region + no text layer → OCR fires, text recovered; (b) full-page region + rich text layer → skipped, `skipped_reason="page_coverage"` (RFC-018 D0 preserved); (c) sub-coverage region → unaffected; (d) coverage exactly 0.6 → boundary unchanged (`>`); (e) `COVERAGE_EXEMPT_NO_TEXT_LAYER=false` → unconditional skip; (f) RFC-018 D0 waste-prevention suite stays green
    - F5: (a) coverage-skipped → `"page_coverage"`; (b) clip-text-skipped → `"clip_text"`; (c) marker-strip identical for both reasons
    - Integration: docs-3/9-shaped fixture (large picture regions, no alternative text under bbox) → ≥1 enrichable `PictureResult` produced ( [Regression 2](../rfcs/020-run3-regression-remediation.md#regression-2--zero-image-enrichment-docs-3-9) end-to-end invariant that RFC-019 missed)
    - **Validates:** [Design Property 2](../designs/design-rfc020-run3-regression-remediation.md#property-2-full-page-scan-text-recovery) | [Design Property 6](../designs/design-rfc020-run3-regression-remediation.md#property-6-accurate-skip-reason-attribution) | [RFC-020 §Test Strategy F1/F5 rows](../rfcs/020-run3-regression-remediation.md#test-strategy)
    - _Requirements:_ [RFC-020 F1](../rfcs/020-run3-regression-remediation.md#f1-exempt-no-text-layer-full-page-scans-from-the-coverage-filter-p0) | [RFC-020 F5](../rfcs/020-run3-regression-remediation.md#f5-accurate-skipped_reason-attribution-in-_recover_picture_results-p2) | [Design Sequence: Full-Page Scan Recovery Flow](../designs/design-rfc020-run3-regression-remediation.md#full-page-scan-recovery-flow--f1)
  - [ ] <a id="24-spot-reingestion-checkpoint-2"></a>2.4 Spot reingestion — checkpoint 2 (P0, effort: S)

    - Reingest docs 3, 9, 17 via `preprocess_client.py`
    - Assert enriched-block counts ≥ Run 2 levels (doc 3 ≥ 1, doc 9 ≥ 3); doc 17 recovers page content
    - Cross-ref: [RFC-020 §Implementation Plan checkpoint 2](../rfcs/020-run3-regression-remediation.md#implementation-plan)
    - _Requirements:_ [RFC-020 F1](../rfcs/020-run3-regression-remediation.md#f1-exempt-no-text-layer-full-page-scans-from-the-coverage-filter-p0) | [Design Property 2](../designs/design-rfc020-run3-regression-remediation.md#property-2-full-page-scan-text-recovery) | [RFC-020 §Before/After Corpus Impact](../rfcs/020-run3-regression-remediation.md#beforeafter-corpus-impact)
  - [ ] <a id="25-checkpoint--phase-2"></a>2.5 Checkpoint — Phase 2

    - Run `uv run pytest` — all tests green
    - Verify [Property 2](../designs/design-rfc020-run3-regression-remediation.md#property-2-full-page-scan-text-recovery) and [Property 6](../designs/design-rfc020-run3-regression-remediation.md#property-6-accurate-skip-reason-attribution) validated by [Task 2.3](#23-f1-and-f5-tests), confirmed operationally by [Task 2.4](#24-spot-reingestion-checkpoint-2)
    - Cross-ref: [Phase 1 checkpoint](#14-checkpoint--phase-1) passed
    - Ask the user if questions arise before proceeding to [Phase 3](#3-phase-3--f2f3-script-and-language-threading)

- [ ] <a id="3-phase-3--f2f3-script-and-language-threading"></a>3. Phase 3 — [F2](../rfcs/020-run3-regression-remediation.md#f2-filename-derived-expected_script-for-garble-gate-callers-p0)/[F3](../rfcs/020-run3-regression-remediation.md#f3-arabic-aware-ocr-language-for-the-pre-garble-probe-p1) script and language threading (1.0 d)

  *[RFC-020 §Implementation Plan — Phase 3](../rfcs/020-run3-regression-remediation.md#implementation-plan): makes the RFC-019 D2 prong reachable ([Regression 3](../rfcs/020-run3-regression-remediation.md#regression-3--garble-gate-gap-doc-24)) and fixes probe OCR language ([Regression 1 Cause 3](../rfcs/020-run3-regression-remediation.md#cause-3-secondary--pre-garble-probe-forces-ocr-without-arabic-language)). Independent of Phases 1-2.*

  - [x] <a id="31-implement-f2-expected-script-threading"></a>3.1 Implement F2 `expected_script` threading (P0, effort: M)

    - In `src/pageindex_mcp/helpers.py`:
      - New `_script_from_filename(filename) -> str | None` — `"Arab"` when `detect_ocr_langs(filename)` (`converters.py:773-800`) includes `"ara"`, else `None`
      - `_tree_is_garbled(nodes, expected_script=None)` (`helpers.py:738-742`) — forward to `_is_garbled_blob(blob, expected_script=expected_script)`
      - `_flat_text_is_garbled(md, expected_script=None)` (`helpers.py:1519-1523`) — forward identically
      - `validate_tree(..., expected_script=None)` (`helpers.py:746-769`) — forward to `_tree_is_garbled` and `_garble_check_nodes`
      - `_garble_check_nodes` (`helpers.py:724-735`) — prefer caller-supplied script; `_infer_script` (`helpers.py:701-721`) only as fallback when `None`
    - In `src/pageindex_mcp/client.py`: compute `expected_script = _script_from_filename(filename)` once in `index()`; pass to the pre-garble probe's `_flat_text_is_garbled` call (`client.py:531-548`) and to `validate_tree`
    - All parameters optional — no signature breaks at existing call sites
    - Log WARNING when filename-derived and text-inferred scripts disagree
    - _Requirements:_ [RFC-020 F2](../rfcs/020-run3-regression-remediation.md#f2-filename-derived-expected_script-for-garble-gate-callers-p0) | [Design Property 3](../designs/design-rfc020-run3-regression-remediation.md#property-3-filename-derived-script-garble-detection) | [Design Service: helpers.py](../designs/design-rfc020-run3-regression-remediation.md#3-helperspy) | [Design AD3](../designs/design-rfc020-run3-regression-remediation.md#ad3-filename-derived-expected-script-f2) | [Design Sequence: Garble Script-Threading Flow](../designs/design-rfc020-run3-regression-remediation.md#garble-script-threading-flow--f2f3)
  - [x] <a id="32-implement-f3-ocr-lang-override"></a>3.2 Implement F3 pre-garble probe OCR language override (P1, effort: S)

    - In `src/pageindex_mcp/client.py:553-556`: when `pre_garbled=True` and converter is docling, call `conv_fn(file_path, True, ocr_lang_override=detect_ocr_langs(filename))` instead of bare `conv_fn(file_path, True)`
    - Restores master's escalation-path language detection (was `client.py:724-731`); Arabic filenames now yield `ara` instead of the `DOCLING_OCR_LANG` default `"deu,eng"`
    - _Requirements:_ [RFC-020 F3](../rfcs/020-run3-regression-remediation.md#f3-arabic-aware-ocr-language-for-the-pre-garble-probe-p1) | [Design Property 4](../designs/design-rfc020-run3-regression-remediation.md#property-4-arabic-ocr-language-selection) | [Design Service: client.py](../designs/design-rfc020-run3-regression-remediation.md#1-clientpy) | [Design AD4](../designs/design-rfc020-run3-regression-remediation.md#ad4-reuse-detect-ocr-langs-for-probe-f3) | [Design Sequence: Garble Script-Threading Flow](../designs/design-rfc020-run3-regression-remediation.md#garble-script-threading-flow--f2f3)
  - [x] <a id="33-f2-and-f3-tests"></a>3.3 F2 and F3 tests (P0, effort: M)

    - **F2 positive:** doc-24 (warid 597) fixture blob — 60k-char-class Latin gibberish, 0% Arabic codepoints — with Arabic filename → `_tree_is_garbled(nodes, "Arab")` and `_flat_text_is_garbled(md, "Arab")` both `True`; `validate_tree` end-to-end fails with garble reason → OCR escalation path entered
    - **F2 negatives:** (a) same blob + Latin filename (`expected_script=None`) → RFC-019 HEAD behavior, not flagged; (b) legitimate bilingual Arabic/English contract excerpt + Arabic filename → `False`; (c) pure Arabic text → `False`; (d) `GARBLE_LATIN_GIBBERISH_ENABLED=false` → prong disabled
    - **F2 fallback:** `_garble_check_nodes` with `expected_script=None` uses `_infer_script` as before
    - **F3:** (a) `pre_garbled=True` + Arabic filename → converter mock receives `ocr_lang_override` containing `"ara"`; (b) German filename → langs unchanged from `detect_ocr_langs` German output; (c) `pre_garbled=False` → no forced re-conversion
    - **Regression:** Run 3's 8 PASS docs' baseline garble assertions hold
    - **Validates:** [Design Property 3](../designs/design-rfc020-run3-regression-remediation.md#property-3-filename-derived-script-garble-detection) | [Design Property 4](../designs/design-rfc020-run3-regression-remediation.md#property-4-arabic-ocr-language-selection) | [RFC-020 §Test Strategy F2/F3 rows](../rfcs/020-run3-regression-remediation.md#test-strategy)
    - _Requirements:_ [RFC-020 F2](../rfcs/020-run3-regression-remediation.md#f2-filename-derived-expected_script-for-garble-gate-callers-p0) | [RFC-020 F3](../rfcs/020-run3-regression-remediation.md#f3-arabic-aware-ocr-language-for-the-pre-garble-probe-p1) | [Design Sequence: Garble Script-Threading Flow](../designs/design-rfc020-run3-regression-remediation.md#garble-script-threading-flow--f2f3)
  - [ ] <a id="34-targeted-reingestion-checkpoint-3"></a>3.4 Targeted reingestion — checkpoint 3 (P0, effort: S)

    - Reingest doc 24 (warid 597) via `preprocess_client.py` — assert garble flag fires, OCR escalation runs with `ara`; outcome is recovery or an honest `low_quality_tree` FAIL per [CLAUDE.md HR5](../../CLAUDE.md) (see [RFC-020 Open Question 4](../rfcs/020-run3-regression-remediation.md#open-questions))
    - Reingest Run 3's 8 PASS docs — zero regressions
    - Cross-ref: [RFC-020 §Implementation Plan checkpoint 3](../rfcs/020-run3-regression-remediation.md#implementation-plan) | [RFC-020 §Risks](../rfcs/020-run3-regression-remediation.md#risks--mitigations) (F2 false-positive row)
    - _Requirements:_ [RFC-020 F2](../rfcs/020-run3-regression-remediation.md#f2-filename-derived-expected_script-for-garble-gate-callers-p0) | [RFC-020 F3](../rfcs/020-run3-regression-remediation.md#f3-arabic-aware-ocr-language-for-the-pre-garble-probe-p1) | [Design Property 3](../designs/design-rfc020-run3-regression-remediation.md#property-3-filename-derived-script-garble-detection)
  - [x] <a id="35-checkpoint--phase-3"></a>3.5 Checkpoint — Phase 3

    - Run `uv run pytest` — all tests green
    - Verify [Property 3](../designs/design-rfc020-run3-regression-remediation.md#property-3-filename-derived-script-garble-detection) and [Property 4](../designs/design-rfc020-run3-regression-remediation.md#property-4-arabic-ocr-language-selection) validated by [Task 3.3](#33-f2-and-f3-tests), confirmed operationally by [Task 3.4](#34-targeted-reingestion-checkpoint-3)
    - Cross-ref: [Phase 2 checkpoint](#25-checkpoint--phase-2) passed
    - Ask the user if questions arise before proceeding to [Phase 4](#4-phase-4--f4-shared-reference-fix)

- [ ] <a id="4-phase-4--f4-shared-reference-fix"></a>4. Phase 4 — [F4](../rfcs/020-run3-regression-remediation.md#f4-independent-pictureresult-copies-in-the-standalone-image-path-p1) shared-reference fix (0.25 d)

  *[RFC-020 §Implementation Plan — Phase 4](../rfcs/020-run3-regression-remediation.md#implementation-plan): bonus bug from the [Regression 2 investigation](../rfcs/020-run3-regression-remediation.md#regression-2--zero-image-enrichment-docs-3-9); fully independent, trivial.*

  - [x] <a id="41-implement-f4-independent-copies"></a>4.1 Implement F4 independent `PictureResult` copies + tests (P1, effort: S)

    - In `src/pageindex_mcp/client.py:679-686` (standalone-image branch, `client.py:667-692`): replace `[PictureResult(...)] * max(1, marker_count)` with `[PictureResult(ocr_text="", page=1, bbox={"l": 0, "t": 0, "r": 0, "b": 0}, png_bytes=img_bytes) for _ in range(max(1, marker_count))]`
    - `img_bytes` (immutable `bytes`) may alias; dict containers must not
    - Tests: (a) `test_pic_results_not_shared_references` — build 3-marker list, `pop("png_bytes")` on entry 0 (mirroring `_enrich_image_blocks` at `client.py:428`), assert entries 1-2 retain bytes; (b) multi-marker end-to-end: all N figures enriched; (c) single-marker parity; (d) assert (a) fails on branch HEAD
    - **Validates:** [Design Property 5](../designs/design-rfc020-run3-regression-remediation.md#property-5-independent-pictureresult-copies) | [RFC-020 F4](../rfcs/020-run3-regression-remediation.md#f4-independent-pictureresult-copies-in-the-standalone-image-path-p1) | [RFC-020 §Test Strategy F4 row](../rfcs/020-run3-regression-remediation.md#test-strategy)
    - _Requirements:_ [RFC-020 F4](../rfcs/020-run3-regression-remediation.md#f4-independent-pictureresult-copies-in-the-standalone-image-path-p1) | [Design Property 5](../designs/design-rfc020-run3-regression-remediation.md#property-5-independent-pictureresult-copies) | [Design Service: client.py](../designs/design-rfc020-run3-regression-remediation.md#1-clientpy) | [Design AD5](../designs/design-rfc020-run3-regression-remediation.md#ad5-independent-pictureresult-copies-f4)
  - [x] <a id="42-checkpoint--phase-4"></a>4.2 Checkpoint — Phase 4

    - Run `uv run pytest` — all tests green
    - Verify [Property 5](../designs/design-rfc020-run3-regression-remediation.md#property-5-independent-pictureresult-copies) validated by [Task 4.1](#41-implement-f4-independent-copies)
    - Cross-ref: [Phase 3 checkpoint](#35-checkpoint--phase-3) passed
    - Ask the user if questions arise before proceeding to [Phase 5](#5-phase-5--final-validation)

- [ ] <a id="5-phase-5--final-validation"></a>5. Phase 5 — Final validation (0.5 d)

  *[RFC-020 §Implementation Plan — Phase 5](../rfcs/020-run3-regression-remediation.md#implementation-plan): full 25-doc batch reingestion; produce Run 4 scorecard.*

  - [ ] <a id="51-full-corpus-reaudit-run-4"></a>5.1 Full 25-doc corpus reaudit — Run 4 (P0, effort: M)

    - Full batch reingestion via `preprocess_client.py`
    - Produce Run 4 audit scorecard against [RFC-020 projected impact](../rfcs/020-run3-regression-remediation.md#beforeafter-corpus-impact) (target: 15-17 PASS, 6-7 MARGINAL, 1-2 FAIL, 1 ERROR)
    - Per-doc checks: docs 17, 20-23 tree-routed with recovered Arabic content; docs 3/9 enriched ≥ Run 2; doc 24 garble-flagged and escalated
    - Record results in a Run 4 audit file under `audit/`; explain any variance from projection
    - Verify all 6 correctness properties operationally: [Property 1](../designs/design-rfc020-run3-regression-remediation.md#property-1-tree-path-ocr-splice-parity), [Property 2](../designs/design-rfc020-run3-regression-remediation.md#property-2-full-page-scan-text-recovery), [Property 3](../designs/design-rfc020-run3-regression-remediation.md#property-3-filename-derived-script-garble-detection), [Property 4](../designs/design-rfc020-run3-regression-remediation.md#property-4-arabic-ocr-language-selection), [Property 5](../designs/design-rfc020-run3-regression-remediation.md#property-5-independent-pictureresult-copies), [Property 6](../designs/design-rfc020-run3-regression-remediation.md#property-6-accurate-skip-reason-attribution)
    - Cross-ref: [RFC-020 §Implementation Plan checkpoint 4](../rfcs/020-run3-regression-remediation.md#implementation-plan)
    - _Requirements:_ [RFC-020 F0](../rfcs/020-run3-regression-remediation.md#f0-restore-per-picture-ocr-splice-to-tree-path-p0--critical) | [RFC-020 F1](../rfcs/020-run3-regression-remediation.md#f1-exempt-no-text-layer-full-page-scans-from-the-coverage-filter-p0) | [RFC-020 F2](../rfcs/020-run3-regression-remediation.md#f2-filename-derived-expected_script-for-garble-gate-callers-p0) | [RFC-020 F3](../rfcs/020-run3-regression-remediation.md#f3-arabic-aware-ocr-language-for-the-pre-garble-probe-p1) | [RFC-020 F4](../rfcs/020-run3-regression-remediation.md#f4-independent-pictureresult-copies-in-the-standalone-image-path-p1) | [RFC-020 F5](../rfcs/020-run3-regression-remediation.md#f5-accurate-skipped_reason-attribution-in-_recover_picture_results-p2) | [Design Properties 1-6](../designs/design-rfc020-run3-regression-remediation.md#correctness-properties)

## Notes

- [F0](../rfcs/020-run3-regression-remediation.md#f0-restore-per-picture-ocr-splice-to-tree-path-p0--critical) is the CRITICAL fix — [Regression 1 Cause 1](../rfcs/020-run3-regression-remediation.md#cause-1-primary-critical--per-picture-ocr-splice-moved-to-flat-only-path) silently discards per-picture OCR for ALL tree-path documents. It must land before [F1](../rfcs/020-run3-regression-remediation.md#f1-exempt-no-text-layer-full-page-scans-from-the-coverage-filter-p0)'s recovered text has anywhere to go on the tree path
- Docs 17, 20-23 need [F0](../rfcs/020-run3-regression-remediation.md#f0-restore-per-picture-ocr-splice-to-tree-path-p0--critical) + [F1](../rfcs/020-run3-regression-remediation.md#f1-exempt-no-text-layer-full-page-scans-from-the-coverage-filter-p0) + [F3](../rfcs/020-run3-regression-remediation.md#f3-arabic-aware-ocr-language-for-the-pre-garble-probe-p1) together — all three causes compound; fixing any one alone leaves them flat-routed or garbled ([RFC-020 §Regression 1](../rfcs/020-run3-regression-remediation.md#regression-1--tree-to-flat-collapse-of-arabic-scanned-pdfs-docs-17-20-23))
- [F2](../rfcs/020-run3-regression-remediation.md#f2-filename-derived-expected_script-for-garble-gate-callers-p0) does not change RFC-019 D2 thresholds — it only makes the prong *reachable*; false-positive exposure is bounded by the existing 40%/5-token/70% gates and the `GARBLE_LATIN_GIBBERISH_ENABLED` kill switch ([RFC-020 §Risks](../rfcs/020-run3-regression-remediation.md#risks--mitigations))
- [F2](../rfcs/020-run3-regression-remediation.md#f2-filename-derived-expected_script-for-garble-gate-callers-p0) enforces [CLAUDE.md HR5](../../CLAUDE.md): doc 24's silent MARGINAL persistence becomes detect → escalate → recover-or-honest-FAIL
- [F1](../rfcs/020-run3-regression-remediation.md#f1-exempt-no-text-layer-full-page-scans-from-the-coverage-filter-p0) intentionally re-adds OCR cost on genuine scans — that is the product working, not the RFC-018 D0 waste problem returning ([RFC-020 §Risks](../rfcs/020-run3-regression-remediation.md#risks--mitigations))
- [RFC-020 Open Question 1](../rfcs/020-run3-regression-remediation.md#open-questions): splice format stays `> [Chart text]:` (master parity); output-contract change deferred
- [RFC-020 Open Question 2](../rfcs/020-run3-regression-remediation.md#open-questions): garbled-thin-text-layer exemption — Phase-2 stretch only if trivial
- Run 3's 1 ERROR doc is out of scope (RFC-019 D4 retry territory)
- Phases 1-2 are sequential (splice must exist before recovered full-page text reaches the tree path); Phase 3 and Phase 4 are independent of each other and of Phases 1-2
- All fixes apply to future ingestions only — realized scorecard requires the Run 4 reaudit ([Task 5.1](#51-full-corpus-reaudit-run-4))
- Each phase is an isolated commit with env-var rollback levers (see [Design §Migration and Rollback](../designs/design-rfc020-run3-regression-remediation.md#migration-and-rollback))

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "name": "Critical splice + independent parallel starts",
      "tasks": ["1.1", "3.1", "4.1"],
      "depends_on": []
    },
    {
      "id": 1,
      "name": "Splice wiring + F3 lang override + F4 checkpoint",
      "tasks": ["1.2", "3.2", "4.2"],
      "depends_on": [0]
    },
    {
      "id": 2,
      "name": "F0 tests + F2/F3 tests",
      "tasks": ["1.3", "3.3"],
      "depends_on": [1]
    },
    {
      "id": 3,
      "name": "Phase 1 checkpoint + Phase 2 implementation + doc-24 reingest",
      "tasks": ["1.4", "2.1", "2.2", "3.4"],
      "depends_on": [2]
    },
    {
      "id": 4,
      "name": "F1/F5 tests + Phase 3 checkpoint",
      "tasks": ["2.3", "3.5"],
      "depends_on": [3]
    },
    {
      "id": 5,
      "name": "Spot reingestion + Phase 2 checkpoint",
      "tasks": ["2.4", "2.5"],
      "depends_on": [4]
    },
    {
      "id": 6,
      "name": "Final Run 4 corpus reaudit",
      "tasks": ["5.1"],
      "depends_on": [5]
    }
  ]
}
```
