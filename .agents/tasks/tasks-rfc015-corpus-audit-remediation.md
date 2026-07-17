<!-- Space: CITRA -->
<!-- Title: Tasks — RFC-015 Corpus Audit Remediation — Verdict Engine & Extraction Gaps -->
<!-- Folder: Tasks -->

# Implementation Plan: Corpus Audit Remediation — Verdict Engine & Extraction Gaps

## Traceability

| Artifact | Reference |
|---|---|
| Governing RFC(s) | [RFC-015: Corpus Audit Remediation — Verdict Engine & Extraction Gaps](../rfcs/015-corpus-audit-remediation.md#decision) |
| Design Document | [Design: Corpus Audit Remediation — Verdict Engine & Extraction Gaps](../designs/design-rfc015-corpus-audit-remediation.md#overview) |
| PRD / Requirements | [PRD.md](../PRD.md#functional-requirements) |
| Hard Rules | [CLAUDE.md HR1 + HR3 + HR4 + HR5](../rfcs/015-corpus-audit-remediation.md#hard-rule-constraints-claudemd--binding) |
| RFC Implementation Order | [RFC-015 Implementation Plan](../rfcs/015-corpus-audit-remediation.md#implementation-plan) |
| RFC Test Strategy | [RFC-015 Test Strategy](../rfcs/015-corpus-audit-remediation.md#test-strategy) |
| Design Correctness Properties | [Design Correctness Properties](../designs/design-rfc015-corpus-audit-remediation.md#property-1-batch-supported-set-completeness) |
| Design Service Contracts | [Design Service Contracts](../designs/design-rfc015-corpus-audit-remediation.md#1-preprocess_clientpy) |

## Overview

Implements the ten decisions of [RFC-015](../rfcs/015-corpus-audit-remediation.md#decision) that remediate verdict engine correctness defects and extraction quality gaps identified by the 26-file corpus audit (2026-07-17). The audit found 2 stored PASS verdicts independently confirmed wrong — a direct violation of [CLAUDE.md HR5](../rfcs/015-corpus-audit-remediation.md#hard-rule-constraints-claudemd--binding) (never silently persist a low-quality tree). The plan proceeds in four phases matching the RFC's batch structure: [Phase 1](#1-p0-verdict-engine--tooling-correctness-d1-d2-d3) fixes P0 verdict engine and tooling correctness ([D1](../rfcs/015-corpus-audit-remediation.md#d1--batch-tooling-unify-supported-set-p0-5-lines), [D2](../rfcs/015-corpus-audit-remediation.md#d2--verdict-engine-content-ordering-check-p0-25-lines), [D3](../rfcs/015-corpus-audit-remediation.md#d3--verdict-engine-ratio-denominator-fix--english-heading-labels-p0-30-lines)); [Phase 2](#2-p1-marker--splitter-fixes-d4-d5) fixes P1 marker leakage and splitter boundary misses ([D4](../rfcs/015-corpus-audit-remediation.md#d4--marker-leakage-widen-hash-sentinel-regex-p1-15-lines), [D5](../rfcs/015-corpus-audit-remediation.md#d5--giant-tail-blob-four-additive-sub-fixes-p1-60-lines-total)); [Phase 3](#3-p1-extraction-quality-d6-d7-d8-d9-d10) fixes P1 extraction quality gaps ([D6](../rfcs/015-corpus-audit-remediation.md#d6--chartinfographic-text-recovery-via-per-picture-ocr-p1-40-lines), [D7](../rfcs/015-corpus-audit-remediation.md#d7--bidi-word-order-normalization-p1-25-lines--dependency), [D8](../rfcs/015-corpus-audit-remediation.md#d8--sparse-mixed-script-garble-detection-p1-20-lines), [D9](../rfcs/015-corpus-audit-remediation.md#d9--table-rowspan-forward-fill-p1-15-lines), [D10](../rfcs/015-corpus-audit-remediation.md#d10--preamble-node-synthesis-p1-15-lines)); [Phase 4](#4-revalidation-corpus-reprocess) reprocesses the full corpus and verifies the 2 wrong-PASS verdicts are corrected. Phases 2 and 3 are independent of each other but both depend on Phase 1's checkpoint. Every phase closes with a checkpoint gate before downstream work begins.

## Tasks

- [ ] <a id="1-p0-verdict-engine--tooling-correctness-d1-d2-d3"></a>1. P0 — Verdict Engine & Tooling Correctness ([D1](../rfcs/015-corpus-audit-remediation.md#d1--batch-tooling-unify-supported-set-p0-5-lines), [D2](../rfcs/015-corpus-audit-remediation.md#d2--verdict-engine-content-ordering-check-p0-25-lines), [D3](../rfcs/015-corpus-audit-remediation.md#d3--verdict-engine-ratio-denominator-fix--english-heading-labels-p0-30-lines))

  - [ ] <a id="11-unify-batch-supported-set-d1"></a>1.1 Unify batch SUPPORTED set ([D1](../rfcs/015-corpus-audit-remediation.md#d1--batch-tooling-unify-supported-set-p0-5-lines))

    - Replace the hardcoded `SUPPORTED = {".pdf", ".docx", ".pptx", ".md", ".txt", ".html"}` at `preprocess_client.py:111` with an import: `from pageindex_mcp.client import _SUPPORTED as SUPPORTED`
    - Remove the duplicate set definition entirely — the canonical set lives in `client.py:63-64` and already includes `.jpg`, `.xlsx`, `.png` via `_IMAGE_EXTS`/`_SUPPORTED`
    - Verify that the imported set is a superset of the old hardcoded set (no regressions for `.pdf`, `.docx`, `.pptx`, `.md`, `.txt`, `.html`)
    - _Requirements:_ [RFC-015 D1](../rfcs/015-corpus-audit-remediation.md#d1--batch-tooling-unify-supported-set-p0-5-lines) | [Design Property 1: Batch Supported Set Completeness](../designs/design-rfc015-corpus-audit-remediation.md#property-1-batch-supported-set-completeness) | [Design Service 1: preprocess_client.py](../designs/design-rfc015-corpus-audit-remediation.md#1-preprocess_clientpy)

  - [ ] <a id="12-add-tree-reordering-check-d2"></a>1.2 Add tree reordering check ([D2](../rfcs/015-corpus-audit-remediation.md#d2--verdict-engine-content-ordering-check-p0-25-lines))

    - Implement `_tree_is_reordered(tree: dict) -> bool` in `helpers.py` — walk all leaf nodes via `_walk_leaves`, track the running maximum of `start_index` (falling back to `line_num`), return `True` if any node's index regresses below the running max
    - Wire into `validate_tree` (`helpers.py:594-606`) — reject pre-`save_doc` per [CLAUDE.md HR5](../rfcs/015-corpus-audit-remediation.md#hard-rule-constraints-claudemd--binding)
    - Wire into `classify_verdict` (`helpers.py:650-694`) — force verdict below PASS and surface `"reordered"` in the reason string
    - Confirm `54e92c0a` (Federal Decree-Law 47/2021) no longer receives PASS after the fix
    - _Requirements:_ [RFC-015 D2](../rfcs/015-corpus-audit-remediation.md#d2--verdict-engine-content-ordering-check-p0-25-lines) | [Design Property 2: Content Ordering Rejection](../designs/design-rfc015-corpus-audit-remediation.md#property-2-content-ordering-rejection) | [Design Service 2: helpers.py — Verdict Engine](../designs/design-rfc015-corpus-audit-remediation.md#2-helperspy--verdict-engine) | [Design Sequence 2: Verdict Classification Flow](../designs/design-rfc015-corpus-audit-remediation.md#verdict-classification-flow--d2-d3)

  - [ ] <a id="13-fix-leaf-ratio-denominator-d3a"></a>1.3 Fix leaf ratio denominator ([D3A](../rfcs/015-corpus-audit-remediation.md#d3--verdict-engine-ratio-denominator-fix--english-heading-labels-p0-30-lines))

    - Modify `_tree_max_leaf_ratio` (`helpers.py:611-628`) to restrict the `total` accumulation to leaf nodes only — sum `len(title + text)` exclusively over leaves returned by `_walk_leaves`, not over every node (leaf and non-leaf)
    - This eliminates the denominator inflation caused by non-leaf wrapper-node titles that artificially deflate the ratio
    - Verify `a4c1b522` (Ministerial Resolution 279/2022) ratio changes from the stored `0.0971` to the independently computed `0.34-0.61` range, and the document no longer receives PASS
    - _Requirements:_ [RFC-015 D3 Part A](../rfcs/015-corpus-audit-remediation.md#d3--verdict-engine-ratio-denominator-fix--english-heading-labels-p0-30-lines) | [Design Property 3: Leaf Ratio Accuracy](../designs/design-rfc015-corpus-audit-remediation.md#property-3-leaf-ratio-accuracy) | [Design Service 2: helpers.py — Verdict Engine](../designs/design-rfc015-corpus-audit-remediation.md#2-helperspy--verdict-engine) | [Design Sequence 2: Verdict Classification Flow](../designs/design-rfc015-corpus-audit-remediation.md#verdict-classification-flow--d2-d3)

  - [ ] <a id="14-add-english-article-heading-labels-d3b"></a>1.4 Add English Article heading labels ([D3B](../rfcs/015-corpus-audit-remediation.md#d3--verdict-engine-ratio-denominator-fix--english-heading-labels-p0-30-lines))

    - Add `Art(?:icle|\.)\s+\d+` and `§\s*\d+` alternatives to `_segment_label` (`converters.py:202-270`) so English and section-symbol-prefixed headings receive an explicit depth (depth 1), preventing the "staircase" mis-nesting where Articles 3-6 nest 4-5 levels deep under an unrelated sub-bullet
    - Compile a new `_ARTICLE_RE = re.compile(r"^(?:Art(?:icle|\.)\s+\d+|§\s*\d+)", re.IGNORECASE)` and integrate into the existing label-detection cascade after German patterns
    - Verify existing German heading labels (`Abschnitt`, `Teil`) are still recognized (no regression)
    - _Requirements:_ [RFC-015 D3 Part B](../rfcs/015-corpus-audit-remediation.md#d3--verdict-engine-ratio-denominator-fix--english-heading-labels-p0-30-lines) | [Design Property 4: English Heading Depth Assignment](../designs/design-rfc015-corpus-audit-remediation.md#property-4-english-heading-depth-assignment) | [Design Service 5: converters.py — PDF Pipeline](../designs/design-rfc015-corpus-audit-remediation.md#5-converterspy--pdf-pipeline)

  - [ ] <a id="15-write-p0-unit-tests-d1-d2-d3"></a>1.5 Write P0 unit tests ([D1](../rfcs/015-corpus-audit-remediation.md#d1--batch-tooling-unify-supported-set-p0-5-lines), [D2](../rfcs/015-corpus-audit-remediation.md#d2--verdict-engine-content-ordering-check-p0-25-lines), [D3](../rfcs/015-corpus-audit-remediation.md#d3--verdict-engine-ratio-denominator-fix--english-heading-labels-p0-30-lines))

    - **D1 tests** ([RFC-015 Test: D1](../rfcs/015-corpus-audit-remediation.md#d1--batch-supported-set)): assert `preprocess_client.SUPPORTED` includes `.jpg`, `.xlsx`, `.png`; integration test confirming a `.jpg` file enqueues a job via `preprocess_client.py`
    - **D2 tests** ([RFC-015 Test: D2](../rfcs/015-corpus-audit-remediation.md#d2--content-ordering-check)): tree with monotonic `start_index` returns `_tree_is_reordered == False`; tree with regressing `start_index` returns `True`; `classify_verdict` on a reordered tree yields verdict < PASS with reason containing `"reordered"`; regression test that `54e92c0a` no longer receives PASS
    - **D3A tests** ([RFC-015 Test: D3](../rfcs/015-corpus-audit-remediation.md#d3--ratio-denominator--heading-labels)): tree with deep non-leaf wrappers produces a higher `_tree_max_leaf_ratio` (leaf-only denominator) than the old calculation; regression test that `a4c1b522` no longer receives PASS
    - **D3B tests** ([RFC-015 Test: D3](../rfcs/015-corpus-audit-remediation.md#d3--ratio-denominator--heading-labels)): `_segment_label("Article 5")` returns explicit depth (not None); `_segment_label("§ 12")` returns explicit depth; German labels (`Abschnitt`, `Teil`) still recognized
    - **Validates:** [Design Property 1](../designs/design-rfc015-corpus-audit-remediation.md#property-1-batch-supported-set-completeness) | [Design Property 2](../designs/design-rfc015-corpus-audit-remediation.md#property-2-content-ordering-rejection) | [Design Property 3](../designs/design-rfc015-corpus-audit-remediation.md#property-3-leaf-ratio-accuracy) | [Design Property 4](../designs/design-rfc015-corpus-audit-remediation.md#property-4-english-heading-depth-assignment)

  - [ ] <a id="15-checkpoint--p0-verdict-engine"></a>1.6 Checkpoint — P0 Verdict Engine

    - Run `uv run pytest` — new D1/D2/D3 unit tests pass, no existing `validate_tree()` or `classify_verdict` tests regress
    - Confirm `validate_tree()`'s hard gate is strictly tightened (D2 adds reordering rejection), never loosened, per [CLAUDE.md HR5](../rfcs/015-corpus-audit-remediation.md#hard-rule-constraints-claudemd--binding)
    - Confirm `_tree_max_leaf_ratio` leaf-only denominator is correct by spot-checking 2-3 known documents
    - Confirm `_segment_label` English patterns do not disturb existing German label recognition
    - **Validates:** [Design Property 1](../designs/design-rfc015-corpus-audit-remediation.md#property-1-batch-supported-set-completeness) | [Design Property 2](../designs/design-rfc015-corpus-audit-remediation.md#property-2-content-ordering-rejection) | [Design Property 3](../designs/design-rfc015-corpus-audit-remediation.md#property-3-leaf-ratio-accuracy) | [Design Property 4](../designs/design-rfc015-corpus-audit-remediation.md#property-4-english-heading-depth-assignment)

- [ ] <a id="2-p1-marker--splitter-fixes-d4-d5"></a>2. P1 — Marker & Splitter Fixes ([D4](../rfcs/015-corpus-audit-remediation.md#d4--marker-leakage-widen-hash-sentinel-regex-p1-15-lines), [D5](../rfcs/015-corpus-audit-remediation.md#d5--giant-tail-blob-four-additive-sub-fixes-p1-60-lines-total))

  - [ ] <a id="21-widen-hash-sentinel-regex-d4"></a>2.1 Widen hash sentinel regex ([D4](../rfcs/015-corpus-audit-remediation.md#d4--marker-leakage-widen-hash-sentinel-regex-p1-15-lines))

    - Replace `_INLINE_HASH_RE = re.compile(r"(?<=\S)#(?=\S)")` (RFC-010 D5 interim) with `_INLINE_HASH_RE = re.compile(r"#+")` to consume whole `#+` runs rather than per-character
    - Rewrite `_fix_fi_hash_substitution` to process line-by-line: preserve line-initial heading markers (`# `, `## `, etc.) while replacing all other `#+` runs with `في`
    - Move `_fix_fi_hash_substitution` **earlier** in the pipeline — before heading-depth inference — so في is restored as a single token before any heading regex sees the corrupted text
    - Verify 5 affected documents (`aebf15b4`, `a6447d73`, `cbf7e6ad`, `d8e8a357`, `fb0554bf`) no longer emit `#في#` / `#فيفي#` artifacts
    - _Requirements:_ [RFC-015 D4](../rfcs/015-corpus-audit-remediation.md#d4--marker-leakage-widen-hash-sentinel-regex-p1-15-lines) | [Design Property 5: Marker Leakage Elimination](../designs/design-rfc015-corpus-audit-remediation.md#property-5-marker-leakage-elimination) | [Design Service 5: converters.py — PDF Pipeline](../designs/design-rfc015-corpus-audit-remediation.md#5-converterspy--pdf-pipeline) | [Design Sequence 1: Ingestion Pipeline Flow](../designs/design-rfc015-corpus-audit-remediation.md#ingestion-pipeline-flow--d1-d10)

  - [ ] <a id="22-decouple-splitter-size-gate-d5a"></a>2.2 Decouple splitter size gate ([D5a](../rfcs/015-corpus-audit-remediation.md#d5--giant-tail-blob-four-additive-sub-fixes-p1-60-lines-total))

    - Modify the size gate at `helpers.py:1008` — change `if len(leaf_text) > max_chars:` to `if len(leaf_text) > max_chars or _has_heading_markers(leaf_text):` so ordinal matching runs on any leaf with detectable heading markers regardless of char count
    - Implement `_has_heading_markers(text: str) -> bool` — a lightweight check for `_OVERSIZED_ORDINAL_RE` matches in the leaf text
    - Confirm `6147c7d7` (19,959-char residual leaf, under 50k gate) now triggers ordinal matching and splits correctly
    - _Requirements:_ [RFC-015 D5a](../rfcs/015-corpus-audit-remediation.md#d5--giant-tail-blob-four-additive-sub-fixes-p1-60-lines-total) | [Design Property 6: Heading Boundary Recognition](../designs/design-rfc015-corpus-audit-remediation.md#property-6-heading-boundary-recognition) | [Design Service 4: helpers.py — Splitter](../designs/design-rfc015-corpus-audit-remediation.md#4-helperspy--splitter)

  - [ ] <a id="23-add-schedule-to-ordinal-regex-d5b"></a>2.3 Add Schedule to ordinal regex ([D5b](../rfcs/015-corpus-audit-remediation.md#d5--giant-tail-blob-four-additive-sub-fixes-p1-60-lines-total))

    - Extend `_OVERSIZED_ORDINAL_RE` (`helpers.py`) to add `Schedule\s+\(?(\d+)\)?` as an alternative alongside existing `§`, `Article`, `Section`, `مادة` patterns
    - Verify `8cfeca9a` and `bf7eb06f` now split at `Schedule (N)` boundaries
    - _Requirements:_ [RFC-015 D5b](../rfcs/015-corpus-audit-remediation.md#d5--giant-tail-blob-four-additive-sub-fixes-p1-60-lines-total) | [Design Property 6: Heading Boundary Recognition](../designs/design-rfc015-corpus-audit-remediation.md#property-6-heading-boundary-recognition) | [Design Service 4: helpers.py — Splitter](../designs/design-rfc015-corpus-audit-remediation.md#4-helperspy--splitter)

  - [ ] <a id="24-split-run-together-headings-d5c"></a>2.4 Split run-together headings ([D5c](../rfcs/015-corpus-audit-remediation.md#d5--giant-tail-blob-four-additive-sub-fixes-p1-60-lines-total))

    - Implement `_split_run_together_headings(md: str) -> str` in `converters.py` — insert newlines before `#{1,6}\s` heading markers that follow non-whitespace: `re.sub(r"(?<=[^\n])(#{1,6}\s)", r"\n\1", md)`
    - Apply as a normalization pass before heading-depth inference in `pdf_to_markdown_docling()`
    - Confirm `7dcf7cb7` (Docling multiple `#######`-prefixed headings on one physical line) now splits correctly
    - _Requirements:_ [RFC-015 D5c](../rfcs/015-corpus-audit-remediation.md#d5--giant-tail-blob-four-additive-sub-fixes-p1-60-lines-total) | [Design Property 6: Heading Boundary Recognition](../designs/design-rfc015-corpus-audit-remediation.md#property-6-heading-boundary-recognition) | [Design Service 5: converters.py — PDF Pipeline](../designs/design-rfc015-corpus-audit-remediation.md#5-converterspy--pdf-pipeline)

  - [ ] <a id="25-extend-letter-suffix-promotion-d5d"></a>2.5 Extend letter-suffix promotion ([D5d](../rfcs/015-corpus-audit-remediation.md#d5--giant-tail-blob-four-additive-sub-fixes-p1-60-lines-total))

    - Modify `_repromote_numbered_headings` (`converters.py:647-650`) — change the digit-only trailing check to accept a single trailing letter: `r"\d+[a-z]?"` so `7.10.a`, `7.10.b` pass the promotion condition
    - Confirm `acc20e08` letter-suffixed sub-clauses now promote correctly
    - _Requirements:_ [RFC-015 D5d](../rfcs/015-corpus-audit-remediation.md#d5--giant-tail-blob-four-additive-sub-fixes-p1-60-lines-total) | [Design Property 6: Heading Boundary Recognition](../designs/design-rfc015-corpus-audit-remediation.md#property-6-heading-boundary-recognition) | [Design Service 5: converters.py — PDF Pipeline](../designs/design-rfc015-corpus-audit-remediation.md#5-converterspy--pdf-pipeline)

  - [ ] <a id="26-write-marker-splitter-tests-d4-d5"></a>2.6 Write marker/splitter tests ([D4](../rfcs/015-corpus-audit-remediation.md#d4--marker-leakage-widen-hash-sentinel-regex-p1-15-lines), [D5](../rfcs/015-corpus-audit-remediation.md#d5--giant-tail-blob-four-additive-sub-fixes-p1-60-lines-total))

    - **D4 tests** ([RFC-015 Test: D4](../rfcs/015-corpus-audit-remediation.md#d4--marker-leakage-regex)): `"text #في# more text"` becomes `"text في more text"` (boundary `#` consumed); `"#فيفيفي#"` collapses to single `في`; `"## Heading"` preserved (heading markers untouched); non-Arabic text with `#` unchanged
    - **D5a test** ([RFC-015 Test: D5](../rfcs/015-corpus-audit-remediation.md#d5--tail-blob-sub-fixes)): leaf with heading markers but <50k chars triggers ordinal matching
    - **D5b test** ([RFC-015 Test: D5](../rfcs/015-corpus-audit-remediation.md#d5--tail-blob-sub-fixes)): `"Schedule (3)"` matches `_OVERSIZED_ORDINAL_RE`
    - **D5c test** ([RFC-015 Test: D5](../rfcs/015-corpus-audit-remediation.md#d5--tail-blob-sub-fixes)): `"text### Heading"` becomes `"text\n### Heading"`
    - **D5d test** ([RFC-015 Test: D5](../rfcs/015-corpus-audit-remediation.md#d5--tail-blob-sub-fixes)): `"7.10.a"` accepted by promotion condition
    - **Validates:** [Design Property 5](../designs/design-rfc015-corpus-audit-remediation.md#property-5-marker-leakage-elimination) | [Design Property 6](../designs/design-rfc015-corpus-audit-remediation.md#property-6-heading-boundary-recognition)

  - [ ] <a id="26-checkpoint--p1-marker-splitter"></a>2.7 Checkpoint — P1 Marker & Splitter

    - Run `uv run pytest` — new D4/D5 tests pass alongside Phase 1 suite, no existing splitter or heading tests regress
    - Confirm `_fix_fi_hash_substitution` pipeline ordering is correct: runs before heading-depth inference per [RFC-015 D4](../rfcs/015-corpus-audit-remediation.md#d4--marker-leakage-widen-hash-sentinel-regex-p1-15-lines)
    - Confirm `_split_run_together_headings` does not break single-heading-per-line documents (German corpus spot check)
    - Confirm `_OVERSIZED_ORDINAL_RE` still matches existing patterns (`§`, `Article`, `Section`, `مادة`) — no regression from adding `Schedule`
    - **Validates:** [Design Property 5](../designs/design-rfc015-corpus-audit-remediation.md#property-5-marker-leakage-elimination) | [Design Property 6](../designs/design-rfc015-corpus-audit-remediation.md#property-6-heading-boundary-recognition)

- [ ] <a id="3-p1-extraction-quality-d6-d7-d8-d9-d10"></a>3. P1 — Extraction Quality ([D6](../rfcs/015-corpus-audit-remediation.md#d6--chartinfographic-text-recovery-via-per-picture-ocr-p1-40-lines), [D7](../rfcs/015-corpus-audit-remediation.md#d7--bidi-word-order-normalization-p1-25-lines--dependency), [D8](../rfcs/015-corpus-audit-remediation.md#d8--sparse-mixed-script-garble-detection-p1-20-lines), [D9](../rfcs/015-corpus-audit-remediation.md#d9--table-rowspan-forward-fill-p1-15-lines), [D10](../rfcs/015-corpus-audit-remediation.md#d10--preamble-node-synthesis-p1-15-lines))

  - [ ] <a id="31-per-picture-ocr-fallback-d6"></a>3.1 Per-picture OCR fallback ([D6](../rfcs/015-corpus-audit-remediation.md#d6--chartinfographic-text-recovery-via-per-picture-ocr-p1-40-lines))

    - Implement `_recover_picture_text(doc_path, pictures, langs) -> dict[int, str]` in `converters.py` — for each `PictureItem`'s bbox, crop via PyMuPDF (`fitz`), render at 300 DPI, run the existing `_tesseract_ocr` path against the crop, return `{picture_index: recovered_text}` for entries with >20 chars stripped
    - Wire after `export_to_markdown()` returns: for each `<!-- image -->` marker with a matching recovered text, append the text as `> [Chart text]: ...`
    - Gate on the existing `_OCR_ESCALATION` kill-switch — when disabled, no per-picture OCR fires
    - Confirm `1f2a37f6` and `b644b8de` recover chart data-labels and axis text that were previously swallowed into the `Picture` cluster's bounding box
    - _Requirements:_ [RFC-015 D6](../rfcs/015-corpus-audit-remediation.md#d6--chartinfographic-text-recovery-via-per-picture-ocr-p1-40-lines) | [Design Property 7: Chart Text Recovery](../designs/design-rfc015-corpus-audit-remediation.md#property-7-chart-text-recovery) | [Design Service 5: converters.py — PDF Pipeline](../designs/design-rfc015-corpus-audit-remediation.md#5-converterspy--pdf-pipeline) | [Design Sequence 1: Ingestion Pipeline Flow](../designs/design-rfc015-corpus-audit-remediation.md#ingestion-pipeline-flow--d1-d10)

  - [ ] <a id="32-bidi-word-order-normalization-d7"></a>3.2 BiDi word-order normalization ([D7](../rfcs/015-corpus-audit-remediation.md#d7--bidi-word-order-normalization-p1-25-lines--dependency))

    - Add `python-bidi` to `pyproject.toml` dependencies (pure Python, MIT license, no C extension)
    - Implement `reconstruct_bidi_order(text: str) -> str` in `converters.py` — using `bidi.algorithm.get_display`, apply per-line, gated on Arabic-ratio threshold (>15% Arabic chars including presentation forms `U+FE70-U+FEFF`)
    - Apply in `pdf_to_markdown_docling()` output, after `_fix_fi_hash_substitution` and before heading-depth inference
    - Confirm `6e8dc6f9` and `bbd28040` (97% presentation-form Arabic) produce logical reading order after normalization
    - Confirm German/English documents are untouched (Arabic-ratio gate ensures zero false-positive risk)
    - _Requirements:_ [RFC-015 D7](../rfcs/015-corpus-audit-remediation.md#d7--bidi-word-order-normalization-p1-25-lines--dependency) | [Design Property 8: BiDi Order Restoration](../designs/design-rfc015-corpus-audit-remediation.md#property-8-bidi-order-restoration) | [Design Service 5: converters.py — PDF Pipeline](../designs/design-rfc015-corpus-audit-remediation.md#5-converterspy--pdf-pipeline) | [Design Sequence 1: Ingestion Pipeline Flow](../designs/design-rfc015-corpus-audit-remediation.md#ingestion-pipeline-flow--d1-d10)

  - [ ] <a id="33-sparse-mixed-script-garble-detection-d8"></a>3.3 Sparse mixed-script garble detection ([D8](../rfcs/015-corpus-audit-remediation.md#d8--sparse-mixed-script-garble-detection-p1-20-lines))

    - Implement `_MIXED_SCRIPT_RE` and `_has_sparse_mojibake(text, threshold=0.02) -> bool` in `helpers.py` — detect localized Latin/digit fragments glued to Arabic script using the Arabic-Latin-Arabic / Latin-Arabic-Latin patterns, requiring >100 chars and >2% of words matching
    - Wire into `_tree_is_garbled` as an additional check, and into `_flat_text_is_garbled` — when triggered, reactivate the existing OCR-escalation path (same wiring as RFC-010 D3)
    - Calibrate the 2% threshold against `92eebefa` (21.4% mixed-script ratio — must trigger) while avoiding false positives on `b1a72fb2` (legitimate transliterated names — must not trigger)
    - Confirm `92eebefa`, `c1ccd6e5`, and `6147c7d7` (subset) trigger garble detection and route to OCR escalation
    - _Requirements:_ [RFC-015 D8](../rfcs/015-corpus-audit-remediation.md#d8--sparse-mixed-script-garble-detection-p1-20-lines) | [Design Property 9: Sparse Mojibake Detection](../designs/design-rfc015-corpus-audit-remediation.md#property-9-sparse-mojibake-detection) | [Design Service 3: helpers.py — Garble Detection](../designs/design-rfc015-corpus-audit-remediation.md#3-helperspy--garble-detection) | [Design Sequence 1: Ingestion Pipeline Flow](../designs/design-rfc015-corpus-audit-remediation.md#ingestion-pipeline-flow--d1-d10)

  - [ ] <a id="34-table-rowspan-forward-fill-d9"></a>3.4 Table rowspan forward-fill ([D9](../rfcs/015-corpus-audit-remediation.md#d9--table-rowspan-forward-fill-p1-15-lines))

    - Implement `_forward_fill_leading_column(rows: list[list[str]]) -> list[list[str]]` in `helpers.py` — forward-fill empty cells in column 0 only (merged rowspan headers), scoped strictly to column 0 to avoid corrupting data columns
    - Wire into `_flat_parse_table` (`helpers.py:771-786`) after row parsing, before returning the structured table data
    - Confirm `e544d939` (GHV-TKV-Tarif) Katze table's merged `Selbstbehalt` label is forward-filled into all 22 data rows, matching the structurally identical Hund table's behavior
    - Confirm data columns (1+) with empty cells are NOT forward-filled
    - _Requirements:_ [RFC-015 D9](../rfcs/015-corpus-audit-remediation.md#d9--table-rowspan-forward-fill-p1-15-lines) | [Design Property 10: Rowspan Forward-Fill](../designs/design-rfc015-corpus-audit-remediation.md#property-10-rowspan-forward-fill) | [Design Service 4: helpers.py — Splitter](../designs/design-rfc015-corpus-audit-remediation.md#4-helperspy--splitter)

  - [ ] <a id="35-preamble-node-synthesis-d10"></a>3.5 Preamble node synthesis ([D10](../rfcs/015-corpus-audit-remediation.md#d10--preamble-node-synthesis-p1-15-lines))

    - Modify `extract_nodes_from_markdown` (`page_index_md.py:32-57`) — before the heading-split loop, find the first heading index; if content precedes it and exceeds 50 chars, synthesize a preamble node with `title="[Preamble]"`, `text=<content>`, `depth=0`, `line_num=0` and insert at position 0
    - The 50-char threshold avoids synthesizing nodes for trivial whitespace or blank lines before the first heading
    - Confirm `722eb392` (GHV Reitlehrer Haftpflicht) Section 1 ("who is covered" clause) is now present in the tree as a preamble node
    - Confirm documents that start with a heading produce no preamble node (no spurious empty nodes)
    - _Requirements:_ [RFC-015 D10](../rfcs/015-corpus-audit-remediation.md#d10--preamble-node-synthesis-p1-15-lines) | [Design Property 11: Preamble Preservation](../designs/design-rfc015-corpus-audit-remediation.md#property-11-preamble-preservation) | [Design Service 6: page_index_md.py — Tree Builder](../designs/design-rfc015-corpus-audit-remediation.md#6-page_index_mdpy--tree-builder) | [Design Sequence 1: Ingestion Pipeline Flow](../designs/design-rfc015-corpus-audit-remediation.md#ingestion-pipeline-flow--d1-d10)

  - [ ] <a id="36-write-extraction-quality-tests-d6-d10"></a>3.6 Write extraction quality tests ([D6](../rfcs/015-corpus-audit-remediation.md#d6--chartinfographic-text-recovery-via-per-picture-ocr-p1-40-lines)-[D10](../rfcs/015-corpus-audit-remediation.md#d10--preamble-node-synthesis-p1-15-lines))

    - **D6 tests** ([RFC-015 Test: D6](../rfcs/015-corpus-audit-remediation.md#d6--per-picture-ocr)): mock a PDF with a picture bbox containing text — OCR fires, text recovered and spliced after `<!-- image -->`; picture bbox with <20 chars recovered — no caption added; integration test that `1f2a37f6` reprocessed has chart text present in output
    - **D7 tests** ([RFC-015 Test: D7](../rfcs/015-corpus-audit-remediation.md#d7--bidi-normalization)): visual-order Arabic text produces logical order after `reconstruct_bidi_order`; German/English text unchanged (Arabic-ratio gate); mixed Arabic/English paragraph — Arabic reordered, English preserved
    - **D8 tests** ([RFC-015 Test: D8](../rfcs/015-corpus-audit-remediation.md#d8--sparse-mixed-script-detection)): Arabic text with glued Latin fragments — `_has_sparse_mojibake` returns True; normal Arabic text with transliterated names — returns False; German text — returns False; integration test that `92eebefa` triggers garble and OCR escalation
    - **D9 tests** ([RFC-015 Test: D9](../rfcs/015-corpus-audit-remediation.md#d9--table-forward-fill)): table rows with empty column 0 — forward-filled from prior row; table rows with non-empty column 0 — unchanged; data columns (1+) with empty cells — NOT forward-filled
    - **D10 tests** ([RFC-015 Test: D10](../rfcs/015-corpus-audit-remediation.md#d10--preamble-node)): markdown with content before first heading — preamble node created with `title="[Preamble]"`; markdown starting with heading — no preamble node; trivial whitespace before heading (<50 chars) — no preamble node
    - **Validates:** [Design Property 7](../designs/design-rfc015-corpus-audit-remediation.md#property-7-chart-text-recovery) | [Design Property 8](../designs/design-rfc015-corpus-audit-remediation.md#property-8-bidi-order-restoration) | [Design Property 9](../designs/design-rfc015-corpus-audit-remediation.md#property-9-sparse-mojibake-detection) | [Design Property 10](../designs/design-rfc015-corpus-audit-remediation.md#property-10-rowspan-forward-fill) | [Design Property 11](../designs/design-rfc015-corpus-audit-remediation.md#property-11-preamble-preservation)

  - [ ] <a id="36-checkpoint--p1-extraction-quality"></a>3.7 Checkpoint — P1 Extraction Quality

    - Run `uv run pytest` — new D6-D10 tests pass alongside Phase 1 and Phase 2 suites
    - Confirm `_recover_picture_text` only fires when pictures are detected and `_OCR_ESCALATION` is enabled — no processing-time impact on text-only documents
    - Confirm `reconstruct_bidi_order` Arabic-ratio gate is effective: German/English documents produce byte-identical output
    - Confirm `_has_sparse_mojibake` does not fire on `b1a72fb2` (legitimate transliterated names, must remain MARGINAL per [RFC-015 D8](../rfcs/015-corpus-audit-remediation.md#d8--sparse-mixed-script-garble-detection-p1-20-lines))
    - Confirm `_forward_fill_leading_column` scoping — column 0 only, data columns untouched
    - Confirm preamble synthesis 50-char threshold — no spurious nodes on clean documents
    - **Validates:** [Design Property 7](../designs/design-rfc015-corpus-audit-remediation.md#property-7-chart-text-recovery) | [Design Property 8](../designs/design-rfc015-corpus-audit-remediation.md#property-8-bidi-order-restoration) | [Design Property 9](../designs/design-rfc015-corpus-audit-remediation.md#property-9-sparse-mojibake-detection) | [Design Property 10](../designs/design-rfc015-corpus-audit-remediation.md#property-10-rowspan-forward-fill) | [Design Property 11](../designs/design-rfc015-corpus-audit-remediation.md#property-11-preamble-preservation)

- [ ] <a id="4-revalidation-corpus-reprocess"></a>4. Revalidation — Corpus Reprocess ([RFC-015 Batch 4](../rfcs/015-corpus-audit-remediation.md#batch-4--revalidation))

  - [ ] <a id="41-full-corpus-reprocess"></a>4.1 Full corpus reprocess

    - Execute `uv run python preprocess_client.py` against the full 26-file corpus, now using the unified `SUPPORTED` set from [Task 1.1](#11-unify-batch-supported-set-d1) (includes `.jpg` and `.xlsx`)
    - Monitor for any new `low_quality_tree` errors or unexpected failures introduced by the Phase 1-3 changes
    - Log the reprocessing results for comparison against the pre-remediation baseline (11 PASS / 12 MARGINAL / 2 FAIL / 1 NOT_PROCESSED)
    - _Requirements:_ [RFC-015 Batch 4](../rfcs/015-corpus-audit-remediation.md#batch-4--revalidation) | [Design Service 7: client.py — Orchestration](../designs/design-rfc015-corpus-audit-remediation.md#7-clientpy--orchestration) | [Design Service 1: preprocess_client.py](../designs/design-rfc015-corpus-audit-remediation.md#1-preprocess_clientpy)

  - [ ] <a id="42-verdict-verification"></a>4.2 Verdict verification

    - Regenerate the verdict table from persisted MinIO `processed/*.meta.json` artifacts
    - Verify the 2 wrong-PASS verdicts are corrected: `54e92c0a` (reordering — [D2](../rfcs/015-corpus-audit-remediation.md#d2--verdict-engine-content-ordering-check-p0-25-lines)) and `a4c1b522` (ratio mismatch — [D3](../rfcs/015-corpus-audit-remediation.md#d3--verdict-engine-ratio-denominator-fix--english-heading-labels-p0-30-lines)) must no longer receive PASS
    - Verify expected improvement: wrong-PASS count 2 to 0; MARGINAL count 12 to <=6 per [RFC-015 Batch 4](../rfcs/015-corpus-audit-remediation.md#batch-4--revalidation)
    - Document any documents that remain MARGINAL with their reasons for future RFC consideration
    - _Requirements:_ [RFC-015 Batch 4](../rfcs/015-corpus-audit-remediation.md#batch-4--revalidation) | [Design Sequence 2: Verdict Classification Flow](../designs/design-rfc015-corpus-audit-remediation.md#verdict-classification-flow--d2-d3)

  - [ ] <a id="43-checkpoint--final"></a>4.3 Checkpoint — Final

    - Run `uv run pytest` — full test suite passes with zero regressions
    - Verify all 11 correctness properties from [Design Correctness Properties](../designs/design-rfc015-corpus-audit-remediation.md#property-1-batch-supported-set-completeness) are green:
      - [Property 1: Batch Supported Set Completeness](../designs/design-rfc015-corpus-audit-remediation.md#property-1-batch-supported-set-completeness) ([Task 1.1](#11-unify-batch-supported-set-d1), [Task 1.5](#15-write-p0-unit-tests-d1-d2-d3))
      - [Property 2: Content Ordering Rejection](../designs/design-rfc015-corpus-audit-remediation.md#property-2-content-ordering-rejection) ([Task 1.2](#12-add-tree-reordering-check-d2), [Task 1.5](#15-write-p0-unit-tests-d1-d2-d3))
      - [Property 3: Leaf Ratio Accuracy](../designs/design-rfc015-corpus-audit-remediation.md#property-3-leaf-ratio-accuracy) ([Task 1.3](#13-fix-leaf-ratio-denominator-d3a), [Task 1.5](#15-write-p0-unit-tests-d1-d2-d3))
      - [Property 4: English Heading Depth Assignment](../designs/design-rfc015-corpus-audit-remediation.md#property-4-english-heading-depth-assignment) ([Task 1.4](#14-add-english-article-heading-labels-d3b), [Task 1.5](#15-write-p0-unit-tests-d1-d2-d3))
      - [Property 5: Marker Leakage Elimination](../designs/design-rfc015-corpus-audit-remediation.md#property-5-marker-leakage-elimination) ([Task 2.1](#21-widen-hash-sentinel-regex-d4), [Task 2.6](#26-write-marker-splitter-tests-d4-d5))
      - [Property 6: Heading Boundary Recognition](../designs/design-rfc015-corpus-audit-remediation.md#property-6-heading-boundary-recognition) ([Tasks 2.2](#22-decouple-splitter-size-gate-d5a)-[2.5](#25-extend-letter-suffix-promotion-d5d), [Task 2.6](#26-write-marker-splitter-tests-d4-d5))
      - [Property 7: Chart Text Recovery](../designs/design-rfc015-corpus-audit-remediation.md#property-7-chart-text-recovery) ([Task 3.1](#31-per-picture-ocr-fallback-d6), [Task 3.6](#36-write-extraction-quality-tests-d6-d10))
      - [Property 8: BiDi Order Restoration](../designs/design-rfc015-corpus-audit-remediation.md#property-8-bidi-order-restoration) ([Task 3.2](#32-bidi-word-order-normalization-d7), [Task 3.6](#36-write-extraction-quality-tests-d6-d10))
      - [Property 9: Sparse Mojibake Detection](../designs/design-rfc015-corpus-audit-remediation.md#property-9-sparse-mojibake-detection) ([Task 3.3](#33-sparse-mixed-script-garble-detection-d8), [Task 3.6](#36-write-extraction-quality-tests-d6-d10))
      - [Property 10: Rowspan Forward-Fill](../designs/design-rfc015-corpus-audit-remediation.md#property-10-rowspan-forward-fill) ([Task 3.4](#34-table-rowspan-forward-fill-d9), [Task 3.6](#36-write-extraction-quality-tests-d6-d10))
      - [Property 11: Preamble Preservation](../designs/design-rfc015-corpus-audit-remediation.md#property-11-preamble-preservation) ([Task 3.5](#35-preamble-node-synthesis-d10), [Task 3.6](#36-write-extraction-quality-tests-d6-d10))
    - Confirm `validate_tree()` gate is strictly tightened (D2 reordering check added), never loosened, per [CLAUDE.md HR5](../rfcs/015-corpus-audit-remediation.md#hard-rule-constraints-claudemd--binding)
    - Confirm PII routing is unchanged — OCR escalation (D6, D8) reuses the existing `pdf_to_markdown_docling` path which respects `OPENAI_BASE_URL` routing, per [CLAUDE.md HR3](../rfcs/015-corpus-audit-remediation.md#hard-rule-constraints-claudemd--binding)
    - Confirm no new pymupdf dependency introduced — D6 uses `fitz` which is already a transitive dep via `pymupdf4llm`, per [CLAUDE.md HR4](../rfcs/015-corpus-audit-remediation.md#hard-rule-constraints-claudemd--binding)
    - Verify zero flaky test failures across 3 consecutive runs

## Notes

- [D1](../rfcs/015-corpus-audit-remediation.md#d1--batch-tooling-unify-supported-set-p0-5-lines) is a pure config-drift fix — zero new code paths, the OCR route for images already exists and works via the HTTP upload path. The import replaces a duplicate set that silently excluded `.jpg` and `.xlsx`.
- [D2](../rfcs/015-corpus-audit-remediation.md#d2--verdict-engine-content-ordering-check-p0-25-lines) strictly tightens `validate_tree` per [CLAUDE.md HR5](../rfcs/015-corpus-audit-remediation.md#hard-rule-constraints-claudemd--binding) — documents that previously received a wrong PASS will now correctly receive MARGINAL or trigger re-extraction. The ordering check uses `start_index` (source-document position), not logical reference order, so appendices that physically follow the main body will not trigger false positives.
- [D3](../rfcs/015-corpus-audit-remediation.md#d3--verdict-engine-ratio-denominator-fix--english-heading-labels-p0-30-lines) Part A shifts all existing verdicts — every document's `max_leaf_ratio` will increase when computed leaf-only. The MARGINAL threshold (0.25) is already calibrated against leaf content per [RFC-015 Risk 2](../rfcs/015-corpus-audit-remediation.md#risks). A full corpus reprocess ([Task 4.1](#41-full-corpus-reprocess)) will update all stored verdicts.
- [D4](../rfcs/015-corpus-audit-remediation.md#d4--marker-leakage-widen-hash-sentinel-regex-p1-15-lines) supersedes the RFC-010 D5 interim regex (`(?<=\S)#(?=\S)`) with a wider `#+` pattern. The heading-marker preservation logic processes line-by-line to avoid consuming line-initial `#` heading markers per [RFC-015 Risk 3](../rfcs/015-corpus-audit-remediation.md#risks).
- [D5](../rfcs/015-corpus-audit-remediation.md#d5--giant-tail-blob-four-additive-sub-fixes-p1-60-lines-total) addresses the single largest defect class in the corpus (11+ of 25 docs). Each sub-cause is independent and narrowly scoped — they can be implemented and tested in any order within Phase 2.
- [D6](../rfcs/015-corpus-audit-remediation.md#d6--chartinfographic-text-recovery-via-per-picture-ocr-p1-40-lines) uses `fitz` (PyMuPDF/AGPL) for bbox cropping — this extends the existing AGPL surface already present via `pymupdf4llm`, not a new introduction, per [CLAUDE.md HR4](../rfcs/015-corpus-audit-remediation.md#hard-rule-constraints-claudemd--binding). The import is scoped to `_recover_picture_text` and only fires when pictures are detected. Per-picture OCR adds processing time per [RFC-015 Risk 4](../rfcs/015-corpus-audit-remediation.md#risks); the existing `_OCR_ESCALATION` kill-switch applies.
- [D7](../rfcs/015-corpus-audit-remediation.md#d7--bidi-word-order-normalization-p1-25-lines--dependency) introduces `python-bidi` as a new runtime dependency — pure Python, MIT license, no C extension per [RFC-015 Risk 5](../rfcs/015-corpus-audit-remediation.md#risks). Must be added to `pyproject.toml` before [Task 3.2](#32-bidi-word-order-normalization-d7) can execute. The Arabic-ratio gate (>15%) ensures zero overhead for non-Arabic documents.
- [D8](../rfcs/015-corpus-audit-remediation.md#d8--sparse-mixed-script-garble-detection-p1-20-lines) adds per-node granularity to catch corruption that bulk-ratio checks dilute away. The 2% threshold is calibrated to avoid false positives on documents with legitimate mixed-script content per [RFC-015 Risk 6](../rfcs/015-corpus-audit-remediation.md#risks).
- [D9](../rfcs/015-corpus-audit-remediation.md#d9--table-rowspan-forward-fill-p1-15-lines) is modeled on the working Hund reference table in `e544d939`. Scoped to column 0 only to avoid corrupting data columns.
- [D10](../rfcs/015-corpus-audit-remediation.md#d10--preamble-node-synthesis-p1-15-lines) preserves substantive pre-heading content (definitions, scope, effective date) that many legal and insurance documents contain. The 50-char threshold avoids synthesizing nodes for trivial whitespace.
- [CLAUDE.md HR5](../rfcs/015-corpus-audit-remediation.md#hard-rule-constraints-claudemd--binding) constraint: `validate_tree()` must run before `save_doc`. D2 and D3 add new checks (ordering, nesting sanity) to `validate_tree` and `classify_verdict` — strictly tightening the gate, never loosening.
- Tasks marked with test responsibilities (1.5, 2.6, 3.6) are the sole mechanism validating the 11 correctness properties — they may be reprioritized but not skipped.

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "name": "Phase 1 — P0 implementation (D1, D2, D3)",
      "tasks": ["1.1", "1.2", "1.3", "1.4"],
      "depends_on": [],
      "notes": "All four tasks are independent: D1 touches preprocess_client.py, D2/D3A touch helpers.py, D3B touches converters.py"
    },
    {
      "id": 1,
      "name": "Phase 1 — Tests + Checkpoint",
      "tasks": ["1.5", "1.6"],
      "depends_on": [0],
      "notes": "Gate before Phase 2 and Phase 3 begin"
    },
    {
      "id": 2,
      "name": "Phase 2 — P1 marker & splitter (D4, D5)",
      "tasks": ["2.1", "2.2", "2.3", "2.4", "2.5"],
      "depends_on": [1],
      "notes": "All five tasks are independent sub-fixes; D4 touches converters.py, D5a-D5b touch helpers.py, D5c-D5d touch converters.py"
    },
    {
      "id": 3,
      "name": "Phase 2 — Tests + Checkpoint",
      "tasks": ["2.6", "2.7"],
      "depends_on": [2],
      "notes": "Gate before Phase 4 begins (Phase 2 path)"
    },
    {
      "id": 4,
      "name": "Phase 3 — P1 extraction quality (D6, D7, D8, D9, D10)",
      "tasks": ["3.1", "3.2", "3.3", "3.4", "3.5"],
      "depends_on": [1],
      "notes": "Independent of Phase 2; all five tasks touch different files/functions. D7 requires python-bidi dep added first"
    },
    {
      "id": 5,
      "name": "Phase 3 — Tests + Checkpoint",
      "tasks": ["3.6", "3.7"],
      "depends_on": [4],
      "notes": "Gate before Phase 4 begins (Phase 3 path)"
    },
    {
      "id": 6,
      "name": "Phase 4 — Full corpus reprocess",
      "tasks": ["4.1"],
      "depends_on": [3, 5],
      "notes": "Requires both Phase 2 and Phase 3 checkpoints passed"
    },
    {
      "id": 7,
      "name": "Phase 4 — Verification + Final checkpoint",
      "tasks": ["4.2", "4.3"],
      "depends_on": [6],
      "notes": "Final gate — verifies wrong-PASS count 2→0 and all 11 correctness properties"
    }
  ]
}
```
