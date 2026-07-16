<!-- Space: CITRA -->

<!-- Title: Implementation Plan: Corpus Gap Remediation — Ingestion Pipeline Hardening -->

<!-- Parent: Tasks -->

<!-- Confluence-Page-ID: 5102600195 -->

<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5102600195/Implementation+Plan+Corpus+Gap+Remediation+Ingestion+Pipeline+Hardening -->

# Implementation Plan: Corpus Gap Remediation — Ingestion Pipeline Hardening

## Traceability

| Artifact                      | Reference                                                                                                       |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Governing RFC(s)              | [RFC-010: Corpus Gap Remediation](../rfcs/010-corpus-gap-remediation.md)                                         |
| Design Document               | [Design: Corpus Gap Remediation](../designs/design-rfc010-corpus-gap-remediation.md)                             |
| PRD / Requirements            | `PRD.md`                                                                                                      |
| Hard Rules                    | [CLAUDE.md HR1 + HR3 + HR4 + HR5](../rfcs/010-corpus-gap-remediation.md#hard-rule-constraints-claudemd--binding) |
| RFC Implementation Order      | [RFC-010 Implementation Plan](../rfcs/010-corpus-gap-remediation.md#implementation-plan)                         |
| RFC Test Strategy             | [RFC-010 Test Strategy](../rfcs/010-corpus-gap-remediation.md#test-strategy)                                     |
| Design Correctness Properties | [Design Correctness Properties](../designs/design-rfc010-corpus-gap-remediation.md#correctness-properties)       |
| Design Testing Strategy       | [Design Testing Strategy](../designs/design-rfc010-corpus-gap-remediation.md#testing-strategy)                   |

## Overview

Implements six corpus gap remediations across the PageIndex ingestion pipeline, organized into five priority-ordered batches (0-4) per [RFC-010 Implementation Plan](../rfcs/010-corpus-gap-remediation.md#implementation-plan). The plan proceeds from zero-code stale artifact reprocessing ([Batch 0](../rfcs/010-corpus-gap-remediation.md#batch-0--immediate-zero-code-ops-only)) through P1 image-ratio and TOC fixes ([Batch 1](../rfcs/010-corpus-gap-remediation.md#batch-1--p1-fixes-no-dependencies)), P2 garble detection and heading normalization ([Batch 2](../rfcs/010-corpus-gap-remediation.md#batch-2--p2-fixes-independent-of-batch-1)), P3 upstream-dependent Arabic post-processing ([Batch 3](../rfcs/010-corpus-gap-remediation.md#batch-3--p3-complex-upstream-dependent)), and full corpus revalidation ([Batch 4](../rfcs/010-corpus-gap-remediation.md#batch-4--revalidation)), validating each batch with unit and integration tests tied to the design document's [7 correctness properties](../designs/design-rfc010-corpus-gap-remediation.md#correctness-properties) before advancing. Stack: Python 3.12, FastMCP, Docling (MIT), Tesseract CLI, Redis, MinIO, arq, Prometheus.

## Tasks

- [X] <a id="0-batch-0-reprocess-stale-artifacts-d0"></a>0. Batch 0 — Reprocess Stale Artifacts ([D0](../rfcs/010-corpus-gap-remediation.md#d0--reprocess-stale-corpus-artifacts-gap-3a--gap-4--immediate-zero-code))

  *[RFC-010 Batch 0](../rfcs/010-corpus-gap-remediation.md#batch-0--immediate-zero-code-ops-only): "Zero code, ops only — re-run preprocess_client.py on 3 stale doc_ids processed before Fix-1"*

  - [X] <a id="01-reprocess-stale-docids"></a>0.1 Reprocess stale doc_ids ([D0](../rfcs/010-corpus-gap-remediation.md#d0--reprocess-stale-corpus-artifacts-gap-3a--gap-4--immediate-zero-code))

    - Re-run `uv run python preprocess_client.py` on 3 stale documents:
      - `2030e34d` (Penal Code) — 236k tail-blob, 457 trapped Article markers
      - `2a7e0ebe` (Federal Decree-Law 33) — 100k tail-blob, 73 trapped markers
      - `ae02da49` (Human Rights) — 319k tail-blob, 116 markers invisible due to presentation-form Arabic (U+FExx)
    - No code changes required; the current splitter (`split_oversized_leaf_nodes` at `helpers.py:797`) and NFKC fold (`_fold_with_index_map` at `helpers.py:662`) already handle all three cases
    - _Requirements:_ [RFC-010 D0](../rfcs/010-corpus-gap-remediation.md#d0--reprocess-stale-corpus-artifacts-gap-3a--gap-4--immediate-zero-code) | [Design Property 7](../designs/design-rfc010-corpus-gap-remediation.md#property-7-stale-artifact-reprocessing)
  - [X] <a id="02-verify-splitter-output"></a>0.2 Verify splitter output ([D0](../rfcs/010-corpus-gap-remediation.md#d0--reprocess-stale-corpus-artifacts-gap-3a--gap-4--immediate-zero-code))

    - For each reprocessed document, verify:
      - Tail-blob size reduced (236k, 100k, 319k respectively should decrease significantly)
      - Article markers are no longer trapped in oversized leaf nodes
      - `validate_tree()` passes (per [HR5](../rfcs/010-corpus-gap-remediation.md#hard-rule-constraints-claudemd--binding))
    - Caveat: `ae02da49` (Human Rights) may retain a ~137k residual from genuinely long individual articles + a ToC block; 319k to ~137k is still significant
    - _Requirements:_ [RFC-010 D0](../rfcs/010-corpus-gap-remediation.md#d0--reprocess-stale-corpus-artifacts-gap-3a--gap-4--immediate-zero-code) | [Design Property 7](../designs/design-rfc010-corpus-gap-remediation.md#property-7-stale-artifact-reprocessing)
  - [X] <a id="03-checkpoint-batch-0"></a>0.3 Checkpoint — Batch 0

    - Confirm all 3 documents reprocessed successfully with reduced tail-blob sizes
    - Verify [Design Property 7](../designs/design-rfc010-corpus-gap-remediation.md#property-7-stale-artifact-reprocessing) holds
    - Ask user if questions arise before proceeding
- [X] <a id="1-batch-1-p1-fixes-d1-d4"></a>1. Batch 1 — P1 Fixes ([D1](../rfcs/010-corpus-gap-remediation.md#d1--image-ratio-pre-check-for-ocr-escalation-gap-1--35-lines), [D4](../rfcs/010-corpus-gap-remediation.md#d4--toc-as-table-filter-gap-6c--20-lines))

  *[RFC-010 Batch 1](../rfcs/010-corpus-gap-remediation.md#batch-1--p1-fixes-no-dependencies): "P1 fixes, no dependencies — image-ratio OCR pre-check and TOC dot-leader filter"*

  - [X] <a id="11-image-ratio-ocr-precheck"></a>1.1 Image-ratio OCR pre-check ([D1](../rfcs/010-corpus-gap-remediation.md#d1--image-ratio-pre-check-for-ocr-escalation-gap-1--35-lines))

    - Insert image-ratio check at `client.py:497` before FLAT-03 routing:
      - Count `<!-- image -->` lines vs total lines in `md_content`
      - If ratio > 50% and `_OCR_ESCALATION` is enabled and `settings.flat_doc_routing` is enabled and `ext == ".pdf"`:
        - Detect OCR languages via `detect_ocr_langs(filename)` + `detect_ocr_langs(md_content)`
        - Download tessdata via `ensure_tessdata(escalation_langs)`
        - Re-run `pdf_to_markdown_docling(file_path, True, langs)` with full-page OCR
        - Re-run tree build + splitter + quality gate (same pattern as existing garbling escalation at `client.py:455-490`)
        - Increment `OCR_ESCALATION_TOTAL` metric with `result="recovered"` or `result="still_image_only"`
    - Kill-switches: `_OCR_ESCALATION` (existing env var) + `settings.flat_doc_routing` (existing)
    - _Requirements:_ [RFC-010 D1](../rfcs/010-corpus-gap-remediation.md#d1--image-ratio-pre-check-for-ocr-escalation-gap-1--35-lines) | [Design Property 1](../designs/design-rfc010-corpus-gap-remediation.md#property-1-image-dominant-ocr-escalation) | [Design Service: client.py](../designs/design-rfc010-corpus-gap-remediation.md#1-clientpy) | [Design Sequence: Ingestion Flow](../designs/design-rfc010-corpus-gap-remediation.md#ingestion-flow-d0-d1-d2-d3-d5)
  - [X] <a id="12-toc-dot-leader-filter"></a>1.2 TOC dot-leader filter ([D4](../rfcs/010-corpus-gap-remediation.md#d4--toc-as-table-filter-gap-6c--20-lines))

    - Add `_looks_like_toc_page(block_text)` heuristic in `helpers.py:976`:
      - Compile `_DOT_LEADER_RE = re.compile(r"\.{4,}\s*\d+\s*$")` for dot-leader + trailing page-number detection
      - Return `False` for blocks with fewer than 3 lines
      - Count lines matching `_DOT_LEADER_RE`; return `True` if ratio > 40%
    - Wire into `route_and_extract_flat`: blocks matching this heuristic get `role: prose` instead of `role: table`
    - _Requirements:_ [RFC-010 D4](../rfcs/010-corpus-gap-remediation.md#d4--toc-as-table-filter-gap-6c--20-lines) | [Design Property 5](../designs/design-rfc010-corpus-gap-remediation.md#property-5-toc-page-classification) | [Design Service: helpers.py](../designs/design-rfc010-corpus-gap-remediation.md#2-helperspy) | [Design Sequence: Flat Doc Routing Flow](../designs/design-rfc010-corpus-gap-remediation.md#flat-doc-routing-flow-d1-d3-d4)
  - [x] <a id="13-unit-tests-d1"></a>1.3 Write image-ratio OCR escalation tests ([D1](../rfcs/010-corpus-gap-remediation.md#d1--image-ratio-pre-check-for-ocr-escalation-gap-1--35-lines))

    - **[Property 1](../designs/design-rfc010-corpus-gap-remediation.md#property-1-image-dominant-ocr-escalation) — Image-dominant OCR escalation**:
      - Test: `test_image_dominant_triggers_ocr_escalation` — mock markdown with >50% `<!-- image -->` lines, assert `pdf_to_markdown_docling` called with `force_full_page_ocr=True`
      - Test: `test_below_image_threshold_no_escalation` — markdown with <50% image lines, assert FLAT-03 routing proceeds without OCR escalation
      - Test: `test_ocr_escalation_disabled_no_escalation` — `_OCR_ESCALATION=False`, assert no escalation regardless of image ratio
      - Test: `test_ocr_escalation_metric_increments` — verify `OCR_ESCALATION_TOTAL` metric increments with `result="recovered"` or `result="still_image_only"`
    - **Validates:** [Design Property 1](../designs/design-rfc010-corpus-gap-remediation.md#property-1-image-dominant-ocr-escalation) | [RFC-010 D1](../rfcs/010-corpus-gap-remediation.md#d1--image-ratio-pre-check-for-ocr-escalation-gap-1--35-lines) | [RFC Test Strategy: D1](../rfcs/010-corpus-gap-remediation.md#gap-1-d1--image-ratio-ocr-escalation)
  - [X] <a id="14-unit-tests-d4"></a>1.4 Write TOC filter tests ([D4](../rfcs/010-corpus-gap-remediation.md#d4--toc-as-table-filter-gap-6c--20-lines))

    - **[Property 5](../designs/design-rfc010-corpus-gap-remediation.md#property-5-toc-page-classification) — TOC page classification**:
      - Test: `test_dot_leader_block_is_toc` — block with >40% dot-leader lines, assert `_looks_like_toc_page` returns `True` and block classified as `role: prose`
      - Test: `test_normal_table_not_toc` — normal table block, assert `_looks_like_toc_page` returns `False`
      - Test: `test_short_block_not_toc` — block with <3 lines containing dot leaders, assert returns `False` (too few lines to conclude TOC)
    - **Validates:** [Design Property 5](../designs/design-rfc010-corpus-gap-remediation.md#property-5-toc-page-classification) | [RFC-010 D4](../rfcs/010-corpus-gap-remediation.md#d4--toc-as-table-filter-gap-6c--20-lines) | [RFC Test Strategy: D4](../rfcs/010-corpus-gap-remediation.md#gap-6c-d4--toc-filter)
  - [x] <a id="15-checkpoint-batch-1"></a>1.5 Checkpoint — Batch 1

    - Run `uv run pytest` — all tests pass including [Batch 0](#0-batch-0-reprocess-stale-artifacts-d0) + Batch 1
    - Verify [Design Property 1](../designs/design-rfc010-corpus-gap-remediation.md#property-1-image-dominant-ocr-escalation), [Design Property 5](../designs/design-rfc010-corpus-gap-remediation.md#property-5-toc-page-classification) green
    - Confirm kill-switches (`_OCR_ESCALATION`, `settings.flat_doc_routing`) disable the new D1 path
    - Ask user if questions arise before proceeding
- [X] <a id="2-batch-2-p2-fixes-d3a-d3b-d2"></a>2. Batch 2 — P2 Fixes ([D3A](../rfcs/010-corpus-gap-remediation.md#d3--extended-garble-detection-gap-2--43-lines), [D3B](../rfcs/010-corpus-gap-remediation.md#d3--extended-garble-detection-gap-2--43-lines), [D2](../rfcs/010-corpus-gap-remediation.md#d2--heading-indent-normalization-gap-3b--30-lines))

  *[RFC-010 Batch 2](../rfcs/010-corpus-gap-remediation.md#batch-2--p2-fixes-independent-of-batch-1): "P2 fixes, independent of Batch 1 — extended garble detection and heading indent normalization"*

  - [X] <a id="21-extend-tree-is-garbled"></a>2.1 Extend `_tree_is_garbled` with PUA/digit/repetition checks ([D3A](../rfcs/010-corpus-gap-remediation.md#d3--extended-garble-detection-gap-2--43-lines))

    - Add three new heuristics to `_tree_is_garbled()` at `helpers.py:525`, after the existing control-char check:
      - PUA-char ratio > 3% returns garbled (catches font/CMap mojibake like `2c90ef0d`)
      - Digit ratio > 60% on blobs > 500 chars returns garbled (catches numeric junk like `4f37b2e3`)
      - Single-token repetition > 30% of all words returns garbled
    - False-positive safety: PUA 3% (normal docs have 0% PUA), digit 60% (world-stats-pocketbook is <30%), repetition 30% (normal docs never >5%)
    - `b1a72fb2` (2.1% Latin substitution) must remain MARGINAL, not rejected
    - _Requirements:_ [RFC-010 D3 Part A](../rfcs/010-corpus-gap-remediation.md#d3--extended-garble-detection-gap-2--43-lines) | [Design Property 3](../designs/design-rfc010-corpus-gap-remediation.md#property-3-extended-garble-detection-tree-path) | [Design Service: helpers.py](../designs/design-rfc010-corpus-gap-remediation.md#2-helperspy) | [Design Sequence: Ingestion Flow](../designs/design-rfc010-corpus-gap-remediation.md#ingestion-flow-d0-d1-d2-d3-d5)
  - [X] <a id="22-flat-text-is-garbled"></a>2.2 New `_flat_text_is_garbled` + client.py wiring ([D3B](../rfcs/010-corpus-gap-remediation.md#d3--extended-garble-detection-gap-2--43-lines))

    - Add `_flat_text_is_garbled(md)` function at `helpers.py:~975` (~20 lines):
      - Same heuristics as `_tree_is_garbled` (empty, NUL, replacement char, control-char >5%, PUA >3%, digit >60%/>500 chars, repetition >30%)
      - Applied to flat-path markdown before `route_and_extract_flat`
    - Wire at `client.py:~526`: if `_flat_text_is_garbled(md_content)` returns `True`, override `reason` to `"garbling"` so OCR escalation can fire
    - Closes the flat-path bypass — documents that were previously silently persisted with garbled text will now trigger OCR escalation or surface as `low_quality_tree` errors (per [HR5](../rfcs/010-corpus-gap-remediation.md#hard-rule-constraints-claudemd--binding))
    - _Requirements:_ [RFC-010 D3 Part B](../rfcs/010-corpus-gap-remediation.md#d3--extended-garble-detection-gap-2--43-lines) | [Design Property 4](../designs/design-rfc010-corpus-gap-remediation.md#property-4-flat-path-garble-gate) | [Design Service: helpers.py](../designs/design-rfc010-corpus-gap-remediation.md#2-helperspy) | [Design Service: client.py](../designs/design-rfc010-corpus-gap-remediation.md#1-clientpy) | [Design Sequence: Flat Doc Routing Flow](../designs/design-rfc010-corpus-gap-remediation.md#flat-doc-routing-flow-d1-d3-d4)
  - [X] <a id="23-heading-indent-normalization"></a>2.3 Heading indent normalization in `pdf_to_markdown_docling` output ([D2](../rfcs/010-corpus-gap-remediation.md#d2--heading-indent-normalization-gap-3b--30-lines))

    - Add `_normalize_indented_headings(md)` function in `converters.py:1048+`:
      - Compile `_INDENTED_HEADING_RE = re.compile(r"^[ \t]+(#{1,6}\s)", re.MULTILINE)`
      - Strip leading whitespace before `#` heading markers
    - Apply after the existing `_relevel_headings` and `_normalize_dashes` post-processing steps, before returning from `pdf_to_markdown_docling`
    - Fixes 4 post-Fix-1 documents: `144fbaaf`, `1d682268`, `4806d4bd`, `14f41037` (21-42k tail-blobs from trapped Article markers)
    - _Requirements:_ [RFC-010 D2](../rfcs/010-corpus-gap-remediation.md#d2--heading-indent-normalization-gap-3b--30-lines) | [Design Property 2](../designs/design-rfc010-corpus-gap-remediation.md#property-2-heading-indent-normalization) | [Design Service: converters.py](../designs/design-rfc010-corpus-gap-remediation.md#3-converterspy) | [Design Sequence: Ingestion Flow](../designs/design-rfc010-corpus-gap-remediation.md#ingestion-flow-d0-d1-d2-d3-d5)
  - [X] <a id="24-unit-tests-d3"></a>2.4 Write extended garble detection tests ([D3](../rfcs/010-corpus-gap-remediation.md#d3--extended-garble-detection-gap-2--43-lines))

    - **[Property 3](../designs/design-rfc010-corpus-gap-remediation.md#property-3-extended-garble-detection-tree-path) — Extended garble detection (tree path)**:
      - Test: `test_pua_heavy_string_garbled` — PUA-heavy string (>3% PUA chars), assert `_tree_is_garbled` returns `True`
      - Test: `test_digit_junk_garbled` — digit-junk string (>60% digits, >500 chars), assert garbled
      - Test: `test_single_word_repetition_garbled` — single-word repetition (>30%), assert garbled
      - Test: `test_normal_german_text_not_garbled` — normal German insurance text, assert NOT garbled (false-positive guard)
      - Test: `test_latin_substitution_not_garbled` — `b1a72fb2`-style text (2.1% Latin substitution), assert NOT garbled
    - **[Property 4](../designs/design-rfc010-corpus-gap-remediation.md#property-4-flat-path-garble-gate) — Flat-path garble gate**:
      - Test: `test_flat_text_pua_garbled` — same PUA case on `_flat_text_is_garbled`
      - Test: `test_flat_text_digit_junk_garbled` — same digit-junk case on `_flat_text_is_garbled`
      - Test: `test_flat_text_normal_not_garbled` — normal text on `_flat_text_is_garbled`, assert NOT garbled
      - Test: `test_flat_garble_triggers_ocr_escalation` — integration: verify `4f37b2e3` (digit-junk flat path) now triggers garble to OCR escalation instead of silent FLAT-03 persistence
    - **Validates:** [Design Property 3](../designs/design-rfc010-corpus-gap-remediation.md#property-3-extended-garble-detection-tree-path) | [Design Property 4](../designs/design-rfc010-corpus-gap-remediation.md#property-4-flat-path-garble-gate) | [RFC-010 D3](../rfcs/010-corpus-gap-remediation.md#d3--extended-garble-detection-gap-2--43-lines) | [RFC Test Strategy: D3](../rfcs/010-corpus-gap-remediation.md#gap-2-d3--extended-garble-detection)
  - [X] <a id="25-unit-tests-d2"></a>2.5 Write heading indent normalization tests ([D2](../rfcs/010-corpus-gap-remediation.md#d2--heading-indent-normalization-gap-3b--30-lines))

    - **[Property 2](../designs/design-rfc010-corpus-gap-remediation.md#property-2-heading-indent-normalization) — Heading indent normalization**:
      - Test: `test_indented_heading_stripped` — `    ### Article (10)` stripped to `### Article (10)`
      - Test: `test_indented_code_block_not_modified` — indented code blocks (4+ spaces, no `#`) NOT modified
      - Test: `test_three_space_heading_stripped` — `   ## Heading` (3 spaces) stripped to `## Heading`
      - Test: `test_german_corpus_no_heading_changes` — regression: run against the 27-file German insurance corpus, assert zero heading changes
    - **Validates:** [Design Property 2](../designs/design-rfc010-corpus-gap-remediation.md#property-2-heading-indent-normalization) | [RFC-010 D2](../rfcs/010-corpus-gap-remediation.md#d2--heading-indent-normalization-gap-3b--30-lines) | [RFC Test Strategy: D2](../rfcs/010-corpus-gap-remediation.md#gap-3b-d2--heading-indent-normalization)
  - [X] <a id="26-checkpoint-batch-2"></a>2.6 Checkpoint — Batch 2

    - Run `uv run pytest` — all tests pass including [Batch 0](#0-batch-0-reprocess-stale-artifacts-d0) + [Batch 1](#1-batch-1-p1-fixes-d1-d4) + Batch 2
    - Verify [Design Property 2](../designs/design-rfc010-corpus-gap-remediation.md#property-2-heading-indent-normalization), [Design Property 3](../designs/design-rfc010-corpus-gap-remediation.md#property-3-extended-garble-detection-tree-path), [Design Property 4](../designs/design-rfc010-corpus-gap-remediation.md#property-4-flat-path-garble-gate) green
    - Confirm `b1a72fb2` (2.1% Latin substitution) remains MARGINAL, not rejected
    - Ask user if questions arise before proceeding
- [X] <a id="3-batch-3-p3-fix-d5"></a>3. Batch 3 — P3 Fix ([D5](../rfcs/010-corpus-gap-remediation.md#d5--في-interim-post-process-gap-5a--upstream--interim))

  *[RFC-010 Batch 3](../rfcs/010-corpus-gap-remediation.md#batch-3--p3-complex-upstream-dependent): "P3 complex, upstream-dependent — file Docling issue and apply interim Arabic post-process"*

  - [X] <a id="31-upstream-docling-issue"></a>3.1 File upstream Docling issue ([D5](../rfcs/010-corpus-gap-remediation.md#d5--في-interim-post-process-gap-5a--upstream--interim))

    - File a Docling GitHub issue documenting the في to `#` markdown serialization bug:
      - Provide reproduction: `pdftotext` extracts 162 clean في and zero `#` from the same PDF
      - Affected document: `b87e897e` (Federal Decree-Law 33 Arabic) with 2,923 occurrences
    - _Requirements:_ [RFC-010 D5](../rfcs/010-corpus-gap-remediation.md#d5--في-interim-post-process-gap-5a--upstream--interim) | [Design Property 6](../designs/design-rfc010-corpus-gap-remediation.md#property-6-arabic-hash-substitution-fix)
    - **Filed 2026-07-14** as [docling-project/docling#3802](https://github.com/docling-project/docling/issues/3802). Maintainer `wittjeff` root-caused it to docling-parse's ToUnicode fallback (not markdown serialization) and opened fix PR [docling-parse#299](https://github.com/docling-project/docling-parse/pull/299) (open, CI green, unreviewed as of 2026-07-15).
  - [X] <a id="32-interim-fi-hash-postprocess"></a>3.2 Interim في to `#` post-process ([D5](../rfcs/010-corpus-gap-remediation.md#d5--في-interim-post-process-gap-5a--upstream--interim))

    - Add `_fix_fi_hash_substitution(md)` function in `converters.py`:
      - Compile `_INLINE_HASH_RE = re.compile(r"(?<=\S)#(?=\S)")` for inline `#` surrounded by non-whitespace
      - Detect Arabic-dominant text: >30% Arabic script chars (range U+0600 to U+06FF)
      - Replace inline `#` with في only in Arabic-dominant text
      - Line-initial `# ` heading markers must NOT be replaced
    - Apply in `pdf_to_markdown_docling` post-processing chain, after `_normalize_indented_headings` ([Task 2.3](#23-heading-indent-normalization))
    - _Requirements:_ [RFC-010 D5](../rfcs/010-corpus-gap-remediation.md#d5--في-interim-post-process-gap-5a--upstream--interim) | [Design Property 6](../designs/design-rfc010-corpus-gap-remediation.md#property-6-arabic-hash-substitution-fix) | [Design Service: converters.py](../designs/design-rfc010-corpus-gap-remediation.md#3-converterspy) | [Design Sequence: Ingestion Flow](../designs/design-rfc010-corpus-gap-remediation.md#ingestion-flow-d0-d1-d2-d3-d5)
  - [X] <a id="33-unit-tests-d5"></a>3.3 Write Arabic hash substitution tests ([D5](../rfcs/010-corpus-gap-remediation.md#d5--في-interim-post-process-gap-5a--upstream--interim))

    - **[Property 6](../designs/design-rfc010-corpus-gap-remediation.md#property-6-arabic-hash-substitution-fix) — Arabic hash substitution fix**:
      - Test: `test_arabic_inline_hash_replaced` — Arabic-dominant text with inline `#`, assert replaced with في
      - Test: `test_non_arabic_hash_not_replaced` — non-Arabic text with `#`, assert no replacement
      - Test: `test_heading_markers_not_replaced` — line-initial `# ` heading markers, assert NOT replaced
    - **Validates:** [Design Property 6](../designs/design-rfc010-corpus-gap-remediation.md#property-6-arabic-hash-substitution-fix) | [RFC-010 D5](../rfcs/010-corpus-gap-remediation.md#d5--في-interim-post-process-gap-5a--upstream--interim) | [RFC Test Strategy: D5](../rfcs/010-corpus-gap-remediation.md#gap-5a-d5--في-post-process)
  - [X] <a id="35-glyph-marker-detection-forward-compat"></a>3.5 GLYPH marker detection — forward-compat for docling-parse#299

    - _Requirements:_ [RFC-010 D5 — Upstream status](../rfcs/010-corpus-gap-remediation.md#d5--في-interim-post-process-gap-5a--upstream--interim) | [Design Property 3](../designs/design-rfc010-corpus-gap-remediation.md#property-3-extended-garble-detection-tree-path) | [Design Property 4](../designs/design-rfc010-corpus-gap-remediation.md#property-4-flat-path-garble-gate) | [Design Service: helpers.py](../designs/design-rfc010-corpus-gap-remediation.md#2-helperspy)
    - **Done 2026-07-15.** Added `if "GLYPH<" in blob: return True` to both `_tree_is_garbled` (`helpers.py:525`) and `_flat_text_is_garbled` (`helpers.py:1063`), anticipating [docling-parse#299](https://github.com/docling-project/docling-parse/pull/299) — once merged, unmapped codes in symbolic/composite-font PDFs emit `GLYPH<N>` instead of fabricated ASCII (e.g. `#`), and this check routes those documents to OCR escalation instead of silently persisting the marker.
    - Tests added to `tests/test_rfc010_helpers.py`: `test_tree_glyph_marker_garbled`, `test_flat_text_glyph_marker_garbled`, `test_tree_no_glyph_marker_clean` (negative case — "glyph" as a normal word must not false-positive)
    - Full suite green: 368 passed, 13 skipped, no regressions
    - Currently inert on our corpus — no PDF produces `GLYPH<>` output until the docling-parse dependency is bumped past #299
  - [X] <a id="34-checkpoint-batch-3"></a>3.4 Checkpoint — Batch 3

    - Run `uv run pytest` — all tests pass including [Batch 0](#0-batch-0-reprocess-stale-artifacts-d0) + [Batch 1](#1-batch-1-p1-fixes-d1-d4) + [Batch 2](#2-batch-2-p2-fixes-d3a-d3b-d2) + Batch 3
    - Verify [Design Property 6](../designs/design-rfc010-corpus-gap-remediation.md#property-6-arabic-hash-substitution-fix) green
    - Confirm upstream Docling issue filed with reproduction details
    - Ask user if questions arise before proceeding
- [X] <a id="4-batch-4-revalidation"></a>4. Batch 4 — Revalidation

  *[RFC-010 Batch 4](../rfcs/010-corpus-gap-remediation.md#batch-4--revalidation): "Full corpus revalidation — reprocess all 25 documents and regenerate corpus report"*

  - [X] <a id="41-full-corpus-reprocess"></a>4.1 Full 25-doc corpus reprocess

    - Run `uv run python preprocess_client.py` on the complete 25-document corpus
    - All documents processed through the updated pipeline with D1-D5 fixes applied
    - _Requirements:_ [RFC-010 Batch 4](../rfcs/010-corpus-gap-remediation.md#batch-4--revalidation) | [Design Property 7](../designs/design-rfc010-corpus-gap-remediation.md#property-7-stale-artifact-reprocessing)
  - [X] <a id="42-regenerate-corpus-report"></a>4.2 Regenerate corpus report

    - Regenerate `DOC_STORE_CORPUS_REPORT.md` with updated verdicts for all 25 documents
    - Compare before/after PASS/MARGINAL/FAIL counts against [RFC-010 expected outcomes](../rfcs/010-corpus-gap-remediation.md#batch-4--revalidation)
    - _Requirements:_ [RFC-010 Batch 4](../rfcs/010-corpus-gap-remediation.md#batch-4--revalidation)
  - [X] <a id="43-final-checkpoint"></a>4.3 Final Checkpoint

    - Run `uv run pytest` — full test suite passes
    - Verify all [7 correctness properties](../designs/design-rfc010-corpus-gap-remediation.md#correctness-properties) green:
      - [P1](../designs/design-rfc010-corpus-gap-remediation.md#property-1-image-dominant-ocr-escalation): Image-dominant OCR escalation ([D1](../rfcs/010-corpus-gap-remediation.md#d1--image-ratio-pre-check-for-ocr-escalation-gap-1--35-lines))
      - [P2](../designs/design-rfc010-corpus-gap-remediation.md#property-2-heading-indent-normalization): Heading indent normalization ([D2](../rfcs/010-corpus-gap-remediation.md#d2--heading-indent-normalization-gap-3b--30-lines))
      - [P3](../designs/design-rfc010-corpus-gap-remediation.md#property-3-extended-garble-detection-tree-path): Extended garble detection — tree path ([D3A](../rfcs/010-corpus-gap-remediation.md#d3--extended-garble-detection-gap-2--43-lines))
      - [P4](../designs/design-rfc010-corpus-gap-remediation.md#property-4-flat-path-garble-gate): Flat-path garble gate ([D3B](../rfcs/010-corpus-gap-remediation.md#d3--extended-garble-detection-gap-2--43-lines))
      - [P5](../designs/design-rfc010-corpus-gap-remediation.md#property-5-toc-page-classification): TOC page classification ([D4](../rfcs/010-corpus-gap-remediation.md#d4--toc-as-table-filter-gap-6c--20-lines))
      - [P6](../designs/design-rfc010-corpus-gap-remediation.md#property-6-arabic-hash-substitution-fix): Arabic hash substitution fix ([D5](../rfcs/010-corpus-gap-remediation.md#d5--في-interim-post-process-gap-5a--upstream--interim))
      - [P7](../designs/design-rfc010-corpus-gap-remediation.md#property-7-stale-artifact-reprocessing): Stale artifact reprocessing ([D0](../rfcs/010-corpus-gap-remediation.md#d0--reprocess-stale-corpus-artifacts-gap-3a--gap-4--immediate-zero-code))
    - Expected outcome: PASS rate 4% to ~60%, FAIL rate 48% to ~8%
    - Ask user for review before committing

## Notes

- [D0](../rfcs/010-corpus-gap-remediation.md#d0--reprocess-stale-corpus-artifacts-gap-3a--gap-4--immediate-zero-code) — reprocess 3 stale doc_ids processed before Fix-1 landed; zero code changes, existing splitter + NFKC fold handle all cases
- [D1](../rfcs/010-corpus-gap-remediation.md#d1--image-ratio-pre-check-for-ocr-escalation-gap-1--35-lines) — image-ratio pre-check for OCR escalation on image-only PDFs routed to FLAT-03 with no text; ~35 lines in `client.py`
- [D2](../rfcs/010-corpus-gap-remediation.md#d2--heading-indent-normalization-gap-3b--30-lines) — heading indent normalization stripping leading whitespace before `#` markers in Docling output; ~30 lines in `converters.py`
- [D3](../rfcs/010-corpus-gap-remediation.md#d3--extended-garble-detection-gap-2--43-lines) — extended garble detection adding PUA/digit/repetition checks to `_tree_is_garbled` (Part A) and new `_flat_text_is_garbled` (Part B); ~43 lines across `helpers.py` + `client.py`
- [D4](../rfcs/010-corpus-gap-remediation.md#d4--toc-as-table-filter-gap-6c--20-lines) — TOC-as-table filter for dot-leader pages misclassified as data tables; ~20 lines in `helpers.py`
- [D5](../rfcs/010-corpus-gap-remediation.md#d5--في-interim-post-process-gap-5a--upstream--interim) — interim في to `#` post-process for Arabic-dominant text; ~15 lines in `converters.py` + upstream Docling issue
- [Risk 1](../rfcs/010-corpus-gap-remediation.md#risks) — D1 image-ratio threshold (50%) may need tuning; the 6 affected documents range from 57% to 100% image lines; threshold can be raised or made configurable
- [Risk 2](../rfcs/010-corpus-gap-remediation.md#risks) — D1 OCR escalation doubles processing time; acceptable for 6 documents that currently produce zero usable output; `_OCR_ESCALATION` kill-switch provides escape hatch
- [Risk 3](../rfcs/010-corpus-gap-remediation.md#risks) — D3 garble heuristic thresholds (PUA 3%, digit 60%, repetition 30%) calibrated against current corpus; new document types may require adjustment; constants can be promoted to env vars if needed
- [Risk 4](../rfcs/010-corpus-gap-remediation.md#risks) — D3 Part B closes the flat-path bypass; documents previously accepted may now be rejected; this is intentional per [HR5](../rfcs/010-corpus-gap-remediation.md#hard-rule-constraints-claudemd--binding)
- [Risk 5](../rfcs/010-corpus-gap-remediation.md#risks) — D5 في to `#` regex is fragile; scoped to Arabic-dominant text (>30% Arabic chars) and non-heading positions only; upstream Docling fix is the proper resolution
- [Risk 6](../rfcs/010-corpus-gap-remediation.md#risks) — D0 reprocessing may not fully resolve `ae02da49` (Human Rights); ~137k residual from genuinely long articles; 319k to ~137k is still significant
- **HR1 compliance** — no fix is framed as beating vector RAG on accuracy; all changes improve ingestion recall, not retrieval ranking
- **HR3 compliance** — PII routing unchanged; OCR escalation (D1) reuses existing `pdf_to_markdown_docling` path which respects `OPENAI_BASE_URL` routing
- **HR4 compliance** — all fixes use the existing Docling (MIT) path; no new pymupdf dependency introduced
- **HR5 compliance** — `validate_tree()` continues to run before `save_doc`; D3 extends the existing gate (strictly tightening); D3B adds a gate where none existed
- **Technology** — Docling (MIT) for PDF extraction, Tesseract CLI for OCR escalation, existing env vars (`_OCR_ESCALATION`, `flat_doc_routing`, `OPENAI_BASE_URL`) for kill-switches and routing

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "name": "Batch 0 — Reprocess stale artifacts",
      "tasks": ["0.1", "0.2", "0.3"],
      "depends_on": [],
      "notes": "D0 is zero code, ops only — re-run preprocess_client.py on 3 stale doc_ids"
    },
    {
      "id": 1,
      "name": "Batch 1 + Batch 2 — Implementation (parallel)",
      "tasks": ["1.1", "1.2", "2.1", "2.2", "2.3"],
      "depends_on": [],
      "notes": "Batch 1 (D1, D4) and Batch 2 (D3A, D3B, D2) are independent per RFC — different files, no cross-dependencies"
    },
    {
      "id": 2,
      "name": "Batch 1 + Batch 2 — Tests (parallel)",
      "tasks": ["1.3", "1.4", "2.4", "2.5"],
      "depends_on": ["1.1", "1.2", "2.1", "2.2", "2.3"],
      "notes": "Tests validate implementation from wave 1"
    },
    {
      "id": 3,
      "name": "Batch 1 + Batch 2 — Checkpoints",
      "tasks": ["1.5", "2.6"],
      "depends_on": ["1.3", "1.4", "2.4", "2.5"],
      "notes": "Checkpoints verify Properties 1-5 before advancing"
    },
    {
      "id": 4,
      "name": "Batch 3 — P3 fix (upstream + interim)",
      "tasks": ["3.1", "3.2"],
      "depends_on": [],
      "notes": "D5 has no code dependencies on Batch 1/2 but is P3 priority — upstream issue + interim post-process"
    },
    {
      "id": 5,
      "name": "Batch 3 — Tests + Checkpoint",
      "tasks": ["3.3", "3.4"],
      "depends_on": ["3.2"],
      "notes": "Tests validate D5 Arabic hash substitution fix"
    },
    {
      "id": 6,
      "name": "Batch 4 — Full corpus revalidation",
      "tasks": ["4.1", "4.2", "4.3"],
      "depends_on": ["0.3", "1.5", "2.6", "3.4"],
      "notes": "Depends on all prior batches — reprocesses full 25-doc corpus with all D0-D5 fixes applied"
    }
  ]
}
```
