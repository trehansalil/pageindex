<!-- Space: CITRA -->
<!-- Title: Implementation Plan: RFC-029 Run 12 Arabic Garble-Gate Fixes, Thin-Tree Density Gate, and Extraction Quality Improvements -->
<!-- Folder: Tasks -->

# Implementation Plan: RFC-029 Run 12 Arabic Garble-Gate Fixes, Thin-Tree Density Gate, and Extraction Quality Improvements

## Traceability

| Artifact | Reference |
|---|---|
| Governing RFC(s) | [RFC-029: Run 12 Arabic garble-gate fixes, thin-tree density gate, and extraction quality improvements](../rfcs/029-run12-arabic-garble-gates-and-extraction-quality.md) |
| Design Document | [design-rfc029-run12-arabic-garble-gates-and-extraction-quality.md](../designs/design-rfc029-run12-arabic-garble-gates-and-extraction-quality.md) |
| Audit | [CORPUS_REINGESTION_AUDIT_RUN-12.md](../../audit/CORPUS_REINGESTION_AUDIT_RUN-12.md) |
| Hard Rules (binding) | [CLAUDE.md § Hard Rules](../../CLAUDE.md#hard-rules) |

## Overview

Run 12 audited all 25 corpus documents (10 PASS / 10 MARGINAL / 4 FAIL / 1 ERROR) and surfaced nine decisions (D0-D8) spanning the Arabic garble gate, the tree-vs-flat routing decision, table export/segmentation, picture-enrichment context retention, judge calibration, and a new cross-document contamination gate. Implementation proceeds in five batches ordered by cross-dependency risk: Batch 1 lands small, isolated fixes (D0 NFKC+bidi normalization, D3 fence/HR stripping, D6 judge calibration, D8 contamination gate); Batch 2 lands the two routing-path decisions together (D1 content-density gate, D2 scanned-density floor) since both touch `validate_tree`/`classify_verdict` and must be threshold-calibrated jointly; Batch 3 lands the two table-handling fixes (D4 Docling dedup, D7 tree-builder table segmentation); Batch 4 lands the low-risk picture-enrichment plumbing fixes (D5a, D5b); Batch 5 lands the higher-risk post-Docling chart-page-detection heuristic (D5c), which is independently deferrable. All work is code + unit/property tests only — corpus re-ingestion and re-scoring are handled by the separate corpus-cycle workflow, not by these tasks.

## Tasks

- [ ] <a id="1-batch-1--isolated-fixes-d0-d3-d6-d8"></a>1. Batch 1 — Isolated Fixes ([RFC-029 D0](../rfcs/029-run12-arabic-garble-gates-and-extraction-quality.md#d0-nfkc-normalize-arabic-presentation-forms-before-garble-check), [D3](../rfcs/029-run12-arabic-garble-gates-and-extraction-quality.md#d3-strip-fence-markers-and-hr-separators-in-flat-extraction-path), [D6](../rfcs/029-run12-arabic-garble-gates-and-extraction-quality.md#d6-llm-judge-calibration-stability-and-severity-anchoring-rules), [D8](../rfcs/029-run12-arabic-garble-gates-and-extraction-quality.md#d8-cross-document-contamination-gate-for-zero-body-text-node-clusters))

  - [ ] 1.1 <a id="11-nfkc-normalize-arabic-presentation-forms"></a>Add NFKC normalization for Arabic Presentation Forms in `_pre_inference_normalize`

    - In `src/pageindex_mcp/converters.py::_pre_inference_normalize`, add an early `unicodedata.normalize('NFKC', text)` pass scoped to Arabic Presentation Forms ranges (U+FB50-FDFF, U+FE70-FEFF), run before `md_to_tree` and `validate_tree` so all downstream consumers see canonically-encoded Arabic
    - Ensure the normalization is idempotent and does not alter non-Arabic text
    - _Requirements: [Design Property 1: NFKC canonicalization idempotence](../designs/design-rfc029-run12-arabic-garble-gates-and-extraction-quality.md#property-1-nfkc-canonicalization-idempotence)_
  - [ ] 1.2 <a id="12-bidi-coherence-check-for-visual-order-garble"></a>Add post-NFKC bidi-coherence check and `visual_order_garble` reason

    - In `src/pageindex_mcp/helpers.py`, add a function that samples 3-5 multi-word Arabic runs from NFKC-normalized text and verifies RTL logical word order (not LTR visual order)
    - When >50% of sampled runs fail the coherence check, return `reason=visual_order_garble` instead of passing the blob through
    - Wire `visual_order_garble` into the OCR-retry recovery path in `src/pageindex_mcp/client.py` (same retry path used for `reason=garbling`, lines ~991-1082) — do NOT add `visual_order_garble` to the flat-routing exclusion list at line ~1286; it must behave identically to `garbling` for routing purposes
    - _Requirements: [Design Property 2: Bidi-coherence detection](../designs/design-rfc029-run12-arabic-garble-gates-and-extraction-quality.md#property-2-bidi-coherence-detection), [Design Flow: NFKC then bidi check](../designs/design-rfc029-run12-arabic-garble-gates-and-extraction-quality.md#flow-nfkc-then-bidi-check)_
  - [ ]* 1.3 <a id="13-unit-tests-d0-nfkc-and-bidi"></a>Unit tests for D0 NFKC normalization and bidi-coherence check

    - **Property 1: NFKC canonicalization idempotence** — craft a string with >50% Arabic PF characters, verify `_pre_inference_normalize` maps them to canonical Arabic (U+0600-06FF) forms and `_is_garbled_blob` no longer flags the result on a second pass
    - **Property 2: Bidi-coherence detection** — craft a visual-order (character-reversed) Arabic string after NFKC normalization, verify the bidi-coherence check flags `visual_order_garble`
    - Regression case: verify genuinely garbled text (repeating digit sequences, random byte sequences) still triggers the garble check after normalization
    - **Validates: Requirements Design Property 1, Design Property 2**
  - [ ] 1.4 <a id="14-strip-fence-and-hr-markers-in-flat-extraction"></a>Strip fence markers and HR separators in `route_and_extract_flat`

    - In `src/pageindex_mcp/helpers.py::route_and_extract_flat` (lines ~2281-2359), add fence-marker detection at the top of the while loop: match ```` ```.*``` ```` lines and toggle an `in_fence` state to skip all content inside code fences
    - Add HR-separator detection: match `^-{3,}$`, `^={3,}$`, `^\*{3,}$` lines and skip them without emitting a prose block
    - _Requirements: [Design Property 5: Fence/HR stripping](../designs/design-rfc029-run12-arabic-garble-gates-and-extraction-quality.md#property-5-fence-hr-stripping)_
  - [ ]* 1.5 <a id="15-unit-test-d3-fence-hr-stripping"></a>Unit test for D3 fence/HR stripping

    - **Property 5: Fence/HR stripping** — feed `route_and_extract_flat` a markdown string containing ``` ``` ``` fences, `---` HRs, and `===` separators interspersed with real content; verify the output contains only real content blocks with zero fence/HR artifacts
    - **Validates: Requirements Design Property 5**
  - [ ] 1.6 <a id="16-d6-phase-a-json-diff-verification"></a>D6 Phase A — verify byte-identity of cabinet_resolution_no_21 stored artifacts across runs

    - Diff the stored table blocks in the Run 11 and Run 12 JSON artifacts for `cabinet_resolution_no_21` to confirm the extraction is byte-identical (per [RFC-029 D6 verification prerequisite](../rfcs/029-run12-arabic-garble-gates-and-extraction-quality.md#d6-llm-judge-calibration-stability-and-severity-anchoring-rules))
    - Document the diff result inline in this task's commit message or a code comment; if the diff reveals real table-header malformation, STOP and do not proceed to 1.7 — file a follow-on code decision instead and skip the stability rule for this document
    - _Requirements: [RFC-029 D6](../rfcs/029-run12-arabic-garble-gates-and-extraction-quality.md#d6-llm-judge-calibration-stability-and-severity-anchoring-rules)_
  - [ ] 1.7 <a id="17-d6-phase-b-judge-calibration-rules"></a>D6 Phase B — add judge stability and severity-anchoring calibration rules (contingent on 1.6)

    - In `.claude/skills/corpus-ingest-score/SKILL.md`, add the stability rule to the Judge Verdict guidance section: when stored gate verdict is PASS and metrics are byte-identical to the prior run (per the 1.6 diff, not assumed), the judge MUST NOT downgrade unless it can cite a specific content-quality defect not present in the prior run's finding
    - Add the severity-anchoring rule: for flat/chart docs with <1000 chars and zero enrichments, anchor severity to MARGINAL (not FAIL) when the extraction layer has not regressed
    - In `.claude/skills/corpus-score-diff/SKILL.md`, add a consistency check note for byte-identical artifacts across runs
    - _Requirements: [Design Property 8: Verdict stability anchoring](../designs/design-rfc029-run12-arabic-garble-gates-and-extraction-quality.md#property-8-verdict-stability-anchoring)_
  - [ ] 1.8 <a id="18-zero-body-text-contamination-gate"></a>Add zero-body-text node cluster contamination gate

    - In `src/pageindex_mcp/helpers.py::validate_tree`, after tree construction, count non-root nodes where body text (stripped whitespace) is empty
    - When the zero-body fraction exceeds 30% of non-root nodes, flag with `reason=empty_node_contamination`
    - In `classify_verdict`, treat `empty_node_contamination` as a gate failure (FAIL verdict), not PASS
    - Count zero-body leaf nodes separately from zero-body non-leaf nodes in the internal metric (leaf nodes with empty bodies are the stronger contamination signal per the RFC's calibration note)
    - _Requirements: [Design Property 10: Zero-body contamination gate](../designs/design-rfc029-run12-arabic-garble-gates-and-extraction-quality.md#property-10-zero-body-contamination-gate)_
  - [ ]* 1.9 <a id="19-unit-tests-d8-contamination-gate"></a>Unit tests for D8 contamination gate

    - **Property 10: Zero-body contamination gate** — construct a tree with 91 nodes where 48 have empty body text; verify `validate_tree` flags `empty_node_contamination`
    - Construct a healthy tree with <10% empty-body nodes; verify it passes
    - Regression case: verify all 10 current PASS-doc metric shapes (title-only section headings with content in child nodes) still pass the zero-body fraction check
    - **Validates: Requirements Design Property 10**

- [ ] <a id="2-checkpoint--batch-1"></a>2. Checkpoint — Batch 1

  - Run `uv run pytest tests/test_rfc029_d0.py tests/test_rfc029_d3.py tests/test_rfc029_d8.py -v` and verify all property tests (Properties 1, 2, 5, 10) pass
  - Verify `uv run python -c "import pageindex_mcp.converters, pageindex_mcp.helpers, pageindex_mcp.client"` succeeds with no import errors
  - Confirm Task 1.6's diff result before Task 1.7 was executed (not skipped)
  - Ask the user if questions arise before proceeding.

- [ ] <a id="3-batch-2--routing-decision-hardening-d1-d2"></a>3. Batch 2 — Routing Decision Hardening ([RFC-029 D1](../rfcs/029-run12-arabic-garble-gates-and-extraction-quality.md#d1-content-density-gate-prefer-flat-extraction-when-tree-is-thin), [D2](../rfcs/029-run12-arabic-garble-gates-and-extraction-quality.md#d2-post-ocr-garble-dilution-density-floor-for-scanned-arabic-pdfs))

  - [ ] 3.1 <a id="31-content-density-gate-tree-vs-flat"></a>Add content-density comparison gate to tree-vs-flat decision

    - In `src/pageindex_mcp/client.py::index()`, after a tree passes `validate_tree`, also run `route_and_extract_flat` and compare char counts
    - When `flat_char_count > 3 * tree_char_count`, prefer the flat result over the tree result
    - Additionally, add a minimum chars-per-node floor (~500 chars/node) to `validate_tree` in `src/pageindex_mcp/helpers.py` so a thin tree (e.g. 6 nodes / 1225 chars) is flagged even without a flat comparison being run
    - Gate the 3x threshold behind an env var so it can be tuned without a code change
    - _Requirements: [Design Property 3: Content-density routing](../designs/design-rfc029-run12-arabic-garble-gates-and-extraction-quality.md#property-3-content-density-routing)_
  - [ ]* 3.2 <a id="32-unit-and-parameterized-tests-d1"></a>Unit and parameterized tests for D1 density gate

    - **Property 3: Content-density routing** — construct a tree with 6 nodes / 1200 chars and a flat extraction with 75 blocks / 6000 chars; verify the density gate prefers flat
    - Parameterized test across corpus-doc metric fixtures: verify no fixture matching a current PASS doc's node/char-count shape flips to flat under the new gate
    - **Validates: Requirements Design Property 3**
  - [ ] 3.3 <a id="33-scanned-density-floor-and-arabic-content-ratio-check"></a>Add chars-per-page density floor and Arabic-content-ratio check for scanned PDFs

    - Thread `page_count` through to `validate_tree` (verify current availability in the converter layer; if missing, propagate it from `src/pageindex_mcp/converters.py`)
    - In `validate_tree` or `classify_verdict` (`src/pageindex_mcp/helpers.py`), when `page_count` is available and `chars_per_page < MIN_SCANNED_DENSITY_FLOOR` (default 1500), flag as suspect density
    - Add an Arabic-content validation heuristic: for Arabic-filename docs, if the OCR output's meaningful-Arabic-char ratio is low (dominated by numeric/OCR-noise junk), flag as garbled regardless of total char volume
    - In the D4 keep-best logic (`src/pageindex_mcp/client.py`, lines ~1062-1081), do not let char-count growth alone override a garble-detection result when the pre-retry was garbled and the post-retry shows similar repeating-token patterns
    - _Requirements: [Design Property 4: Scanned-density floor](../designs/design-rfc029-run12-arabic-garble-gates-and-extraction-quality.md#property-4-scanned-density-floor)_
  - [ ]* 3.4 <a id="34-unit-tests-d2-density-floor"></a>Unit tests for D2 scanned-density floor

    - **Property 4: Scanned-density floor** — construct a 42-page tree with 54k chars of repeating numeric content; verify the density floor flags it
    - Construct a 42-page tree with 54k chars of real Arabic content; verify it does NOT trigger the floor
    - Regression case: verify all 10 current PASS-doc metric fixtures still pass the density floor
    - **Validates: Requirements Design Property 4**

- [ ] <a id="4-checkpoint--batch-2"></a>4. Checkpoint — Batch 2

  - Run `uv run pytest tests/test_rfc029_d1.py tests/test_rfc029_d2.py -v` and verify all property tests (Properties 3, 4) pass
  - Run the full parameterized corpus-metric-fixture sweep from Tasks 3.2 and 3.4 together and confirm no fixture-level conflict between the D1 and D2 thresholds
  - Ask the user if questions arise before proceeding.

- [ ] <a id="5-batch-3--table-handling-d4-d7"></a>5. Batch 3 — Table Handling ([RFC-029 D4](../rfcs/029-run12-arabic-garble-gates-and-extraction-quality.md#d4-post-export-table-deduplication-for-docling-char-inflation), [D7](../rfcs/029-run12-arabic-garble-gates-and-extraction-quality.md#d7-tree-builder-table-aware-node-segmentation))

  - [ ] 5.1 <a id="51-post-export-table-repair-pass"></a>Add post-export table-repair pass for Docling output

    - In `src/pageindex_mcp/converters.py`, add a repair pass that runs after every Docling `export_to_markdown()` call (call sites at lines ~2460, ~2525, ~2822, and in `_pdf_to_markdown_docling_chunked`, lines ~2289-2375)
    - Detect degenerate pipe-table rows where ALL column cells are byte-identical (require all columns identical, not just any two, per the RFC's risk mitigation) and collapse them to a single cell
    - Re-emit tables with minimal single-space column padding, stripping Docling's GFM-aligned whitespace padding
    - Add a minimum column-count threshold (only collapse when >3 columns are identical) to avoid false-positive collapse of legitimately-identical short tables
    - _Requirements: [Design Property 6: Table-dedup collapse](../designs/design-rfc029-run12-arabic-garble-gates-and-extraction-quality.md#property-6-table-dedup-collapse)_
  - [ ]* 5.2 <a id="52-unit-tests-d4-table-dedup"></a>Unit tests for D4 table-repair pass

    - **Property 6: Table-dedup collapse** — construct a pipe-table with 5 byte-identical columns and 1000-char padding per cell; verify the repair pass collapses to 1 column with minimal padding
    - Construct a pipe-table with legitimately different columns; verify the repair pass leaves it unchanged
    - **Validates: Requirements Design Property 6**
  - [ ] 5.3 <a id="53-table-aware-node-segmentation"></a>Add table-aware node segmentation to tree-builder

    - In `src/pageindex_mcp/helpers.py`, add a function (or extend `split_oversized_leaf_nodes`, line ~1927) that detects pipe-table boundaries (`|---|---|` separator lines) within a node body
    - When a node has >2000 chars AND contains a pipe-table with >5 rows, split it: the prose portion retains the original heading, the table portion becomes a child node with a synthesized heading (table's first header row, or `Table: {parent heading}`)
    - Run this segmentation after heading-based node construction but before `validate_tree`
    - Handle edge cases: table at start of node, multiple tables in one node, table with no header row
    - _Requirements: [Design Property 9: Table-node segmentation](../designs/design-rfc029-run12-arabic-garble-gates-and-extraction-quality.md#property-9-table-node-segmentation)_
  - [ ]* 5.4 <a id="54-unit-tests-d7-table-segmentation"></a>Unit tests for D7 table-node segmentation

    - **Property 9: Table-node segmentation** — construct a node with 3000 chars of prose followed by a 20-row pipe table; verify it splits into two nodes (prose + table)
    - Construct a node with only prose and no table; verify it is not split
    - Regression case: construct a node representative of Haftpflicht-Allgemeine-Bedingungen's table-in-node shape; verify no content loss across the split (concatenated child text equals original node text)
    - **Validates: Requirements Design Property 9**

- [ ] <a id="6-checkpoint--batch-3"></a>6. Checkpoint — Batch 3

  - Run `uv run pytest tests/test_rfc029_d4.py tests/test_rfc029_d7.py -v` and verify all property tests (Properties 6, 9) pass
  - Ask the user if questions arise before proceeding.

- [ ] <a id="7-batch-4--picture-enrichment-context-retention-d5ab"></a>7. Batch 4 — Picture Enrichment Context Retention ([RFC-029 D5](../rfcs/029-run12-arabic-garble-gates-and-extraction-quality.md#d5-retain-chart-image-context-when-picture-skip-gates-fire) parts a, b)

  - [ ] 7.1 <a id="71-retain-png-and-clip-text-on-skip-gate"></a>Retain `png_bytes`/`clip_text` when `_recover_picture_text` skip gates fire

    - In `src/pageindex_mcp/converters.py::_recover_picture_text` (lines ~1849-1898), when either skip gate fires (`clip_text_already_exported` at line ~1881, or `page_coverage` at lines ~1849-1874), still populate the returned `PictureResult` with the cropped `png_bytes` instead of leaving it empty
    - For the `clip_text_already_exported` case specifically, propagate `clip_text` into `PictureResult.ocr_text` so `splice_figure_markers` can emit a `[Chart text]` block
    - In `splice_figure_markers` (lines ~2071-2075), do not strip the `<!-- image -->` marker when the `PictureResult` carries retained `png_bytes` or non-empty `ocr_text`, even if `STRIP_SKIPPED_IMAGE_MARKERS` is true
    - _Requirements: [Design Property 7: Picture-context retention](../designs/design-rfc029-run12-arabic-garble-gates-and-extraction-quality.md#property-7-picture-context-retention)_
  - [ ] 7.2 <a id="72-standalone-jpg-docling-text-passthrough"></a>Copy Docling-extracted text into `PictureResult.ocr_text` for standalone images

    - In `src/pageindex_mcp/client.py`, standalone image route (lines ~928-946, the D8a gate that skips Tesseract when Docling `md_content > 100` chars), copy the Docling-extracted `md_content` into the synthetic `PictureResult.ocr_text` field rather than leaving it empty
    - _Requirements: [Design Property 7: Picture-context retention](../designs/design-rfc029-run12-arabic-garble-gates-and-extraction-quality.md#property-7-picture-context-retention)_
  - [ ]* 7.3 <a id="73-unit-tests-d5ab"></a>Unit tests for D5(a,b) picture-context retention

    - **Property 7: Picture-context retention** — construct a `PictureResult` with `clip_text_already_exported` skip reason; verify `png_bytes` and `ocr_text` are retained, not empty, after the fix
    - Construct a standalone-JPG scenario where Docling `md_content` > 100 chars; verify the synthetic `PictureResult.ocr_text` contains the Docling text
    - Regression case: verify `splice_figure_markers` still strips markers for `PictureResult`s with genuinely no retained content (both `png_bytes` and `ocr_text` empty)
    - **Validates: Requirements Design Property 7**

- [ ] <a id="8-checkpoint--batch-4"></a>8. Checkpoint — Batch 4

  - Run `uv run pytest tests/test_rfc029_d5ab.py -v` and verify all property tests (Property 7) pass
  - Ask the user if questions arise before proceeding.

- [ ] <a id="9-batch-5--post-docling-chart-page-heuristic-d5c"></a>9. Batch 5 — Post-Docling Chart-Page Heuristic ([RFC-029 D5](../rfcs/029-run12-arabic-garble-gates-and-extraction-quality.md#d5-retain-chart-image-context-when-picture-skip-gates-fire) part c — independently deferrable)

  - [ ] 9.1 <a id="91-chart-page-density-heuristic"></a>Add post-Docling low-text-density chart-page detection heuristic

    - In `src/pageindex_mcp/converters.py`, add a new post-Docling heuristic (gated behind an env var, opt-in initially per the RFC's risk mitigation) that detects pages with very low text content relative to page area (<200 chars/page) combined with visual-content indicators (embedded images or vector paths)
    - When detected, synthesize a `PictureItem` region for the page so it enters the existing picture-enrichment pipeline (OCR/VLM enrichment) rather than being silently skipped
    - Conservative threshold only; do not fire on legitimately sparse text-only pages lacking visual indicators
    - _Requirements: [Design Property 7: Picture-context retention](../designs/design-rfc029-run12-arabic-garble-gates-and-extraction-quality.md#property-7-picture-context-retention)_
  - [ ]* 9.2 <a id="92-unit-test-d5c-chart-page-heuristic"></a>Unit test for D5(c) chart-page heuristic

    - Construct a 2-page PDF fixture where each page has <200 chars of text-layer content plus a vector-path indicator; verify the post-Docling heuristic synthesizes `PictureItem` regions
    - Construct a 2-page PDF fixture with <200 chars/page and NO visual-content indicators; verify the heuristic does NOT fire (false-positive guard)
    - **Validates: Requirements Design Property 7 (extended: chart-page synthesis sub-case)**

- [ ] <a id="10-final-checkpoint"></a>10. Final Checkpoint

  - Run `uv run pytest` (full suite) and verify zero failures
  - Verify `uv run python -c "import pageindex_mcp.converters, pageindex_mcp.helpers, pageindex_mcp.client"` succeeds with no circular-import errors after all batches
  - Confirm no task in this file performed corpus re-ingestion, re-scoring, or artifact re-generation — that is out of scope for this plan and belongs to the corpus-cycle workflow
  - Ask the user if questions arise before proceeding.

## Notes

- Tasks marked with `*` are optional property-based tests and can be skipped for a faster MVP landing, but D0/D1/D2/D8 property tests are strongly recommended given they gate document quality classification.
- This plan is code + unit/property tests only. No task performs document ingestion, corpus re-scoring, or "re-ingest and verify" steps — those live in the separate corpus-cycle / corpus-ingest-score workflow and run after this plan lands.
- [Task 1.2](#12-bidi-coherence-check-for-visual-order-garble)'s `visual_order_garble` reason MUST be routed through the same OCR-retry recovery path as `garbling`, and MUST NOT be added to the flat-routing exclusion list — this mirrors the existing `garbling` routing behavior and preserves [CLAUDE.md Hard Rule 5](../../CLAUDE.md#hard-rules) (no silent low-quality persistence).
- [Task 1.6](#16-d6-phase-a-json-diff-verification) is a **hard prerequisite gate** for [Task 1.7](#17-d6-phase-b-judge-calibration-rules) — do not implement the Phase B calibration rules until the Phase A diff confirms byte-identity. If the diff reveals a real table-header regression, Task 1.7 must be skipped and a separate code-fix decision filed instead.
- [Task 3.1](#31-content-density-gate-tree-vs-flat) (D1) and [Task 3.3](#33-scanned-density-floor-and-arabic-content-ratio-check) (D2) both modify `validate_tree`/`classify_verdict` threshold logic and must be checkpointed together ([Checkpoint 4](#4-checkpoint--batch-2)) to catch threshold conflicts before landing either independently.
- [Task 3.1](#31-content-density-gate-tree-vs-flat) depends on [Task 1.1](#11-nfkc-normalize-arabic-presentation-forms)/[1.2](#12-bidi-coherence-check-for-visual-order-garble) (D0) landing first, per the RFC's stated ordering: NFKC normalization changes what passes the garble check, which changes which trees are "thin" versus genuinely garbled.
- [Task 5.3](#53-table-aware-node-segmentation) (D7) must only apply to nodes above the 2000-char threshold — this avoids fragmenting the already-thin trees that [Task 3.1](#31-content-density-gate-tree-vs-flat) (D1) may route to flat instead, per the RFC's noted interaction risk between D7 and D1/Arabic-heading injection.
- [Task 9.1](#91-chart-page-density-heuristic) (D5c) is independently deferrable from [Task 7.1](#71-retain-png-and-clip-text-on-skip-gate)/[7.2](#72-standalone-jpg-docling-text-passthrough) (D5a/b) — Batch 4 can land and be considered complete on its own if Batch 5 is deferred or rejected after review.
- Batch ordering (1 → 2 → 3 → 4 → 5) follows the RFC's stated cross-decision interaction risk: land D0/D8 first (isolated gate checks), then D3 (no routing impact), then D1+D2 together (both affect routing), then D4/D7 (table handling), then D5a/b, then D5c last (highest risk, independently deferrable).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.4", "1.6", "1.8"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.5", "1.7", "1.9"] },
    { "id": 2, "tasks": ["2"] },
    { "id": 3, "tasks": ["3.1", "3.3"] },
    { "id": 4, "tasks": ["3.2", "3.4"] },
    { "id": 5, "tasks": ["4"] },
    { "id": 6, "tasks": ["5.1", "5.3"] },
    { "id": 7, "tasks": ["5.2", "5.4"] },
    { "id": 8, "tasks": ["6"] },
    { "id": 9, "tasks": ["7.1", "7.2"] },
    { "id": 10, "tasks": ["7.3"] },
    { "id": 11, "tasks": ["8"] },
    { "id": 12, "tasks": ["9.1"] },
    { "id": 13, "tasks": ["9.2"] },
    { "id": 14, "tasks": ["10"] }
  ]
}
```
