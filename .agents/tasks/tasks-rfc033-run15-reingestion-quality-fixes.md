<!-- Space: CITRA -->
<!-- Title: Tasks: RFC-033 — Run-15 Corpus Re-ingestion Quality Fixes -->
<!-- Folder: Tasks -->

# Tasks: RFC-033 — Run-15 Corpus Re-ingestion Quality Fixes

## Traceability

| Artifact | Reference |
|---|---|
| Governing RFC | [`.agents/rfcs/033-run15-run15-reingestion-quality-fixes.md`](../rfcs/033-run15-run15-reingestion-quality-fixes.md) |
| Design Document | [`.agents/designs/design-rfc033-run15-reingestion-quality-fixes.md`](../designs/design-rfc033-run15-reingestion-quality-fixes.md) |
| Audit | `audit/CORPUS_REINGESTION_AUDIT_RUN-15.md` |

## Overview

RFC-033 lands nine code-level fixes surfaced by the Run-15 corpus re-ingestion audit (11 PASS / 12 MARGINAL / 1 FAIL / 1 ERROR across 25 docs): hysteresis snapshot wiring (D0), garble-ratio tautology + flatten-text separator (D1), Arabic single-letter fragment detection + bidi coherence enforcement (D2), MinIO read retry (D3), parenthesized-article regex widening (D4), German clause-heading injection (D5), primary-path table segmentation (D6), image_standalone content_class override (D7), and Arabic OCR RTL-reversal hardening (D8). Work proceeds in **five** batches. **D2 is split into Part A and Part B across non-adjacent batches, and that separation is load-bearing** (reconciliation H-1, 2026-08-06): Batch 0 lands the independent small fixes in `storage.py`, `helpers.py`, `minio_helper.py`, `converters.py` **plus D2 Part A — the `reconstruct_bidi_order()` heading-reversal guard**, because the pipeline itself is reversing already-correct Arabic headings and every downstream bidi measurement is invalid until it stops; Batch 1 lands two client.py verdict-flow changes; Batch 2 lands the Arabic fragment detector and German/English heading injection, which depend on D1's garble-ratio fix; Batch 3 lands the highest-complexity RTL-reversal hardening, which depends on D2's Arabic detection groundwork; **Batch 4 lands D2 Part B (`BIDI_COHERENCE_ENFORCE` promotion) last**, gated on a scoped re-ingest and re-measurement — promoting it any earlier would enforce a verdict cap against titles Part A exists to stop corrupting. This tasks file covers code changes and their unit tests only — corpus re-ingestion, scoring, and verification are handled separately by the corpus-quality-cycle skills, not by these tasks.

## Tasks

- [x] <a id="1-batch-0--independent-small-fixes-d0-d1-d3-d4"></a>1. Batch 0 — Independent Small Fixes + D2 Part A ([RFC-033 D0](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d0-wire-hysteresis-snapshot-into-corpus-reingestion-pipeline), [D1](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d1-fix-garble-ratio-full-text-tautology-and-flatten-text-separator), [D2 Part A](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d2-arabic-single-letter-fragment-detection-and-bidi-coherence-enforcement), [D3](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d3-add-retry-logic-to-minio-read-path-in-ingestscore-pipeline), [D4](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d4-extend-_article_re-to-match-parenthesized-article-numbering))

  - [x] <a id="11-wipe_processed-utility-and-snapshot-relocation"></a>1.1 Implement `wipe_processed()` utility with relocated snapshot prefix

    - In `src/pageindex_mcp/storage.py`, add `wipe_processed()`: (1) call `snapshot_prior_verdicts()` to write `snapshots/_prior_verdicts.json` (a new MinIO prefix, outside `processed/`), then (2) delete all `processed/*` objects.
    - Update `find_prior_verdict()` (lines 726-743) to read from `snapshots/_prior_verdicts.json` instead of `processed/_prior_verdicts.json`.
    - _Requirements: [RFC-033 D0](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d0-wire-hysteresis-snapshot-into-corpus-reingestion-pipeline) | [Design §Property 0 — hysteresis snapshot survives wipe](../designs/design-rfc033-run15-reingestion-quality-fixes.md#property-0-hysteresis-snapshot-survives-wipe)_
  - [x] <a id="12-wire-wipe_processed-into-skills-and-workflows"></a>1.2 Wire `wipe_processed()` into both skills and both workflow wipe call sites

    - Update `.claude/skills/corpus-ingest-score/SKILL.md` and `.claude/skills/corpus-ingest/SKILL.md` to instruct calling `wipe_processed()` instead of a raw MinIO delete.
    - Update `.claude/workflows/corpus-ingest.js` (~lines 44-58) and `.claude/workflows/corpus-ingest-score.js` (~lines 49-63) — the actual wipe call sites — to invoke `wipe_processed()`.
    - _Requirements: [RFC-033 D0](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d0-wire-hysteresis-snapshot-into-corpus-reingestion-pipeline) | [Design §Property 0 — hysteresis snapshot survives wipe](../designs/design-rfc033-run15-reingestion-quality-fixes.md#property-0-hysteresis-snapshot-survives-wipe)_
  - [x] <a id="13-property-snapshot-survives-wipe"></a>1.3 Property test: snapshot survives wipe

    - **Property 1: Snapshot-before-wipe ordering**
    - Call `wipe_processed()`; verify `snapshots/_prior_verdicts.json` exists in MinIO AFTER `processed/*` is deleted (snapshot survives the wipe, and `find_prior_verdict()` reads the relocated prefix and returns the correct prior verdict).
    - **Validates: [RFC-033 D0](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d0-wire-hysteresis-snapshot-into-corpus-reingestion-pipeline)**
  - [x] <a id="14-remove-garble-ratio-full-text-tautology"></a>1.4 Remove `_garble_ratio` full-text tautology

    - In `src/pageindex_mcp/helpers.py`, rewrite `_garble_ratio()` (line 1439) to compute ONLY the windowed ratio — drop the full-text `_is_garbled_blob` / `_has_sparse_mojibake` re-check that duplicates what `_tree_is_garbled` already gates in `classify_verdict()` (lines 1559, 1572).
    - _Requirements: [RFC-033 D1](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d1-fix-garble-ratio-full-text-tautology-and-flatten-text-separator) | [Design §Property 1 — garble ratio reflects windowed measurement only](../designs/design-rfc033-run15-reingestion-quality-fixes.md#property-1-garble-ratio-reflects-windowed-measurement-only)_
  - [x] <a id="15-flatten-tree-text-separator"></a>1.5 Insert newline separator in `_flatten_tree_text`

    - In `src/pageindex_mcp/helpers.py`, insert a newline separator between concatenated title/text parts in `_flatten_tree_text()` (lines 555-565) to prevent artificial glued Arabic-Latin-Arabic boundary patterns.
    - _Requirements: [RFC-033 D1](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d1-fix-garble-ratio-full-text-tautology-and-flatten-text-separator) | [Design §Property 1 — garble ratio reflects windowed measurement only](../designs/design-rfc033-run15-reingestion-quality-fixes.md#property-1-garble-ratio-reflects-windowed-measurement-only)_
  - [x] <a id="16-property-windowed-ratio-and-separator"></a>1.6 Property tests: windowed ratio + separator correctness

    - **Property 2: Windowed garble ratio reflects per-window variance**
    - Build a synthetic tree with Arabic title nodes adjacent to Latin text nodes; verify `_flatten_tree_text` produces newline-separated output. Verify `_garble_ratio` returns the windowed ratio (not a constant 1.0) when individual windows have varying garble levels.
    - **Validates: [RFC-033 D1](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d1-fix-garble-ratio-full-text-tautology-and-flatten-text-separator)**
  - [x] <a id="17-minio-read-retry"></a>1.7 Add retry-with-backoff to MinIO read commands

    - In `scripts/minio_helper.py`, wrap `cmd_meta()` (line 36) and `cmd_tree()` (line 41) with exponential-backoff retry (3 attempts, 2s/4s/8s delays) on transient S3 errors (`NoSuchKey`, `ConnectionError`, network timeouts).
    - Add a retry instruction to the Stage 2 agent prompt in `.claude/workflows/corpus-ingest-score.js` (lines 242-271): "If minio_helper.py returns NoSuchKey, wait 5 seconds and retry up to 3 times before concluding the artifacts are missing."
    - _Requirements: [RFC-033 D3](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d3-add-retry-logic-to-minio-read-path-in-ingestscore-pipeline) | [Design §Property 3 — MinIO read retries recover from transient failures](../designs/design-rfc033-run15-reingestion-quality-fixes.md#property-3-minio-read-retries-recover-from-transient-failures)_
  - [x] <a id="18-property-retry-transient-vs-permanent"></a>1.8 Property tests: retry succeeds on transient failure, fails clean on permanent failure

    - **Property 3: Retry masks transient failures without masking permanent ones**
    - Mock `get_object` to raise `NoSuchKey` on first two calls and succeed on the third — verify `cmd_meta` returns valid JSON. Mock `get_object` to raise on all 3 attempts — verify a clean error message (not a silent swallow).
    - **Validates: [RFC-033 D3](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d3-add-retry-logic-to-minio-read-path-in-ingestscore-pipeline)**
  - [x] <a id="19-widen-article-regex"></a>1.9 Widen `_ARTICLE_RE` to accept parenthesized article numbers

    - In `src/pageindex_mcp/converters.py`, change `_ARTICLE_RE` (line 226) from `r'^(?:Art(?:icle|\.)\s+\d+|§\s*\d+)'` to `r'^(?:Art(?:icle|\.)\s+\(?\s*\d+|§\s*\(?\s*\d+)'` so `_segment_label()` (line 298) extracts a label for `Article (47)` and `_containment_depths()` (line 360) / `_relevel_by_containment()` (line 384) can assign proper nested depth.
    - _Requirements: [RFC-033 D4](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d4-extend-_article_re-to-match-parenthesized-article-numbering) | [Design §Property 4 — parenthesized article numbering yields containment depth](../designs/design-rfc033-run15-reingestion-quality-fixes.md#property-4-parenthesized-article-numbering-yields-containment-depth)_
  - [x] <a id="110-property-article-label-extraction"></a>1.10 Property tests: article label extraction, parenthesized and plain

    - **Property 4: `_segment_label` extracts the numeric label regardless of parenthesization**
    - Verify `_segment_label('Article (47) - Title')` returns `['47']`. Verify `_segment_label('Article 47 - Title')` still returns `['47']` (no regression on the plain form).
    - **Validates: [RFC-033 D4](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d4-extend-_article_re-to-match-parenthesized-article-numbering)**

  - [x] <a id="111-heading-reversal-guard-d2-part-a"></a>1.11 **[D2 Part A]* Gate the heading branch of `reconstruct_bidi_order()`

    - In `src/pageindex_mcp/converters.py`, `reconstruct_bidi_order()` (lines 1301-1339) applies `get_display()` to **every** line matching `_BIDI_HEADING_PREFIX_RE` **unconditionally** (lines 1330-1333), never consulting `reorder_body` or `_text_is_logical_order` (lines 1270-1298). Since `get_display()` maps logical → visual order, **already-correct Arabic headings are reversed by us.** Empirically reproduced: `get_display('المحتويات') == 'تايوتحملا'` and `get_display('الخلاصة') == 'ةصالخلا'` — byte-for-byte the reversed titles the Run-15 audit reports for حقوق الإنسان.
    - Fix: apply `get_display` to a heading only when that heading is not already in logical order — `if not _text_is_logical_order(heading_text)` per heading, or the cheaper `any(_word_has_reversed_morphology(w) for w in heading_text.split())` (`helpers.py:1150`), which is designed for short 10–100 char titles.
    - **Also update the docstring.** It currently advertises a safeguard it does not apply here: *"Includes a logical-vs-visual order probe: if the text already reads correctly ... get_display() is skipped to prevent double-reversal."* That probe gates only the body via `reorder_body` (line 1325). The anti-double-reversal guarantee must actually hold for headings after this fix.
    - **Record the RFC-023 D9 supersede.** The unconditional heading branch is *documented intentional behavior* per RFC-023 D9 (docstring, `converters.py:1314-1318`) — this is a design defect in a prior decision, not an accidental slip. D2's text must state that it narrows RFC-023 D9's scope, and RFC-023's decision record must point back to D2. Without this, the code reads as working-as-documented and the next reader restores the unconditional branch as a "regression fix".
    - _Requirements: [RFC-033 D2](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d2-arabic-single-letter-fragment-detection-and-bidi-coherence-enforcement) (Part A) | Reconciliation C-3 / H-1 (`../../audit/RECONCILIATION_REPORT.md`)_
  - [x] <a id="112-property-heading-guard-idempotence"></a>1.12 Property tests: logical-order headings survive untouched; repair path is idempotent

    - **Property 10: `reconstruct_bidi_order` never reverses an already-logical heading**
    - (a) Logical-order Arabic headings (`# المحتويات`, `## الخلاصة`) survive `reconstruct_bidi_order` **byte-identical**. (b) Genuinely visual-order headings are still corrected (the RFC-023 D9 bilingual case must not regress). (c) The secondary repair path at `client.py:1255-1280`, which re-applies the same function to node titles when `validate_tree` returns `rtl_reversal`, is **idempotent** — a document entering that path must not be reversed twice.
    - **Validates: [RFC-033 D2](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d2-arabic-single-letter-fragment-detection-and-bidi-coherence-enforcement) (Part A)**

- [x] <a id="2-checkpoint--batch-0"></a>2. Checkpoint — Batch 0

  - Run `uv run pytest` and verify all Batch 0 unit/property tests (Tasks [1.3](#13-property-snapshot-survives-wipe), [1.6](#16-property-windowed-ratio-and-separator), [1.8](#18-property-retry-transient-vs-permanent), [1.10](#110-property-article-label-extraction)) pass.
  - Confirm no other call site still performs a raw `processed/*` MinIO delete outside `wipe_processed()`.
  - Ask the user if questions arise before proceeding.

- [x] <a id="3-batch-1--verdict-flow-client-fixes-d6-d7"></a>3. Batch 1 — Verdict-Flow Client Fixes ([RFC-033 D6](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d6-call-_segment_table_nodes-on-primary-tree-build-path), [D7](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d7-implement-rfc-022-b2-part-a-image_standalone-content_class-override))

  - [x] <a id="31-table-segmentation-on-primary-path"></a>3.1 Call `_segment_table_nodes` on the primary tree-build and image-escalation paths

    - In `src/pageindex_mcp/client.py`, add `result['structure'] = _segment_table_nodes(result.get('structure', []))` after line 1031 (primary tree-build path, after `split_oversized_leaf_nodes`) and after line 1428 (image-escalation path).
    - Ensure segmentation runs BEFORE `validate_tree` so the validated structure is the segmented one.
    - _Requirements: [RFC-033 D6](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d6-call-_segment_table_nodes-on-primary-tree-build-path) | [Design §Property 6 — table segmentation runs on all tree-build paths](../designs/design-rfc033-run15-reingestion-quality-fixes.md#property-6-table-segmentation-runs-on-all-tree-build-paths)_
  - [x] <a id="32-property-table-node-segmentation"></a>3.2 Property + regression tests: table segmentation fires on primary path without altering garble-recovery output

    - **Property 5: Table segmentation is path-agnostic**
    - Build a tree with a single large TABLE node; verify `_segment_table_nodes` splits it into per-section sub-nodes when invoked from the primary path. Verify documents already on garble-recovery paths (where `_segment_table_nodes` already ran pre-fix) produce byte-identical output after the change.
    - **Validates: [RFC-033 D6](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d6-call-_segment_table_nodes-on-primary-tree-build-path)**
  - [x] <a id="33-image-standalone-override"></a>3.3 Add extension-based `image_standalone` content_class override

    - In `src/pageindex_mcp/client.py`, after the existing all-blocks-are-image check (line 1608), add: when `ext in _IMAGE_EXTS` and `_IMAGE_STANDALONE_PIPELINE_ENABLED`, force `content_class = 'image_standalone'` regardless of what `route_and_extract_flat` returned, so `classify_verdict` routes through `_classify_image_verdict(image_enrichment_ratio)` (helpers.py:1522).
    - _Requirements: [RFC-033 D7](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d7-implement-rfc-022-b2-part-a-image_standalone-content_class-override) | [Design §Property 7 — image extension forces image_standalone content_class](../designs/design-rfc033-run15-reingestion-quality-fixes.md#property-7-image-extension-forces-image_standalone-content_class)_
  - [x] <a id="34-property-image-standalone-override-scope"></a>3.4 Property tests: override applies only to bare image extensions

    - **Property 6: image_standalone override is extension-scoped, not content-scoped**
    - Mock a `.jpg` file path with `flat_mixed` content_class; verify the override sets `content_class='image_standalone'`. Verify `.pdf` files with mixed image/text blocks are NOT overridden to `image_standalone`.
    - **Validates: [RFC-033 D7](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d7-implement-rfc-022-b2-part-a-image_standalone-content_class-override)**

- [x] <a id="4-checkpoint--batch-1"></a>4. Checkpoint — Batch 1

  - Run `uv run pytest` and verify all Batch 1 property tests (Tasks [3.2](#32-property-table-node-segmentation), [3.4](#34-property-image-standalone-override-scope)) pass, plus a full re-run of Batch 0's suite to catch any client.py interaction regressions.
  - Ask the user if questions arise before proceeding.

- [x] <a id="5-batch-2--arabic-fragment-bidi-enforcement-and-german-heading-injection-d2-d5"></a>5. Batch 2 — Arabic Fragment Detection and German Heading Injection ([RFC-033 D2](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d2-arabic-single-letter-fragment-detection-and-bidi-coherence-enforcement) fragment detection only — **bidi enforcement moved to [Batch 4](#9-batch-4--d2-part-b-bidi-enforcement-promotion)**, [D5](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d5-add-german-clause-pattern-heading-injection-zifferziff))

  - [x] <a id="51-single-letter-arabic-fragment-detection"></a>5.1 Add single-letter Arabic fragment detection to `_is_garbled_blob`

    - In `src/pageindex_mcp/helpers.py`, extend `_is_garbled_blob()` (line 863): when Arabic-script characters are present and >40% of whitespace-delimited tokens containing Arabic chars are single characters (excluding the conjunction particle "wa"), flag as garbled.
    - Wire the same heuristic into `_garble_check_nodes()` for per-node garble-ratio inspection.
    - _Requirements: [RFC-033 D2](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d2-arabic-single-letter-fragment-detection-and-bidi-coherence-enforcement) | [Design §Property 2 — Arabic single-letter fragments detected without false positives on particles](../designs/design-rfc033-run15-reingestion-quality-fixes.md#property-2-arabic-single-letter-fragments-detected-without-false-positives-on-particles)_
  - [x] <a id="52-property-fragment-detection-and-particle-exclusion"></a>5.2 Property tests: fragment detection fires correctly, particle exclusion holds

    - **Property 7: Single-letter fragment ratio ignores the "wa" conjunction**
    - Construct text with Arabic single-letter fragments (e.g. "م ا د ة" instead of "مادة"); verify `_is_garbled_blob` returns True. Verify the conjunction particle "wa" exclusion does not inflate the fragment ratio. Negative test: verify clean Arabic docs (synthetic fixtures modeled on مرسوم 13 / مرسوم 33 phrasing) do not false-trigger the fragment detector.
    - **Validates: [RFC-033 D2](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d2-arabic-single-letter-fragment-detection-and-bidi-coherence-enforcement)**
  - [x] <a id="55-german-clause-heading-injection"></a>5.5 Implement `_inject_german_clause_headings`

    - In `src/pageindex_mcp/converters.py`, add `_inject_german_clause_headings()`: regex-match "Ziffer N" / "Ziff. N" at line start, promote to `##` heading. Require line-start anchoring so mid-sentence references (e.g. "see Ziffer 1 above") are NOT promoted.
    - Call it alongside the existing Arabic injection call site (line 2759-2760) inside `pdf_to_markdown_docling()`.
    - _Requirements: [RFC-033 D5](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d5-add-german-clause-pattern-heading-injection-zifferziff) | [Design §Property 5 — German and English heading injection is line-start-anchored](../designs/design-rfc033-run15-reingestion-quality-fixes.md#property-5-german-and-english-heading-injection-is-line-start-anchored)_
  - [x] <a id="56-english-parenthesized-article-heading-injection"></a>5.6 Implement `_inject_english_article_headings`

    - In `src/pageindex_mcp/converters.py`, add `_inject_english_article_headings()` for "Article (N)" prose lines Docling missed entirely (line-start anchored, same promotion pattern as [5.5](#55-german-clause-heading-injection)).
    - Call it alongside `_inject_german_clause_headings` at the same call site (converters.py line 2759-2760).
    - _Requirements: [RFC-033 D5](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d5-add-german-clause-pattern-heading-injection-zifferziff) | [Design §Property 5 — German and English heading injection is line-start-anchored](../designs/design-rfc033-run15-reingestion-quality-fixes.md#property-5-german-and-english-heading-injection-is-line-start-anchored)_
  - [x] <a id="57-property-heading-injection-line-start-anchoring"></a>5.7 Property tests: heading injection is line-start anchored, no mid-sentence promotion

    - **Property 9: Structural heading injection never promotes mid-sentence references**
    - Pass markdown with "Ziffer 1 Haftung" as a prose line; verify output has "## Ziffer 1 Haftung". Pass markdown with "Article (3) Definitions" as a prose line; verify output has "## Article (3) Definitions". Negative test: verify lines like "see Ziffer 1 above" mid-sentence are NOT promoted.
    - **Validates: [RFC-033 D5](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d5-add-german-clause-pattern-heading-injection-zifferziff)**

- [x] <a id="6-checkpoint--batch-2"></a>6. Checkpoint — Batch 2

  - Run `uv run pytest` and verify all Batch 2 property tests (Tasks [5.2](#52-property-fragment-detection-and-particle-exclusion), [5.7](#57-property-heading-injection-line-start-anchoring)) pass.
  - Re-run the full Batch 0 + Batch 1 suites together to confirm D1's garble-ratio fix and D2's new fragment/bidi detectors do not produce conflicting threshold behavior on the same synthetic fixtures.
  - Ask the user if questions arise before proceeding.

- [x] <a id="7-batch-3--arabic-ocr-rtl-reversal-hardening-d8"></a>7. Batch 3 — Arabic OCR RTL-Reversal Hardening ([RFC-033 D8](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d8-harden-arabic-ocr-tree-building-against-tesseract-rtl-reversed-text))

  - [x] <a id="71-reversed-arabic-regex-variants"></a>7.1 Add reversed-pattern variants to Arabic stem regexes

    - In `src/pageindex_mcp/converters.py`, extend `_AR_PART_RE`, `_AR_ARTICLE_RE`, `_AR_WORD_RE` (~lines 155-214) to also match mirror-reversed variants of مادة / باب / فصل, so `numbering_depth()` and `_relevel_by_containment()` can match Tesseract RTL-reversed OCR output.
    - _Requirements: [RFC-033 D8](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d8-harden-arabic-ocr-tree-building-against-tesseract-rtl-reversed-text) | [Design §Property 8 — reversed Arabic stems match in numbering_depth](../designs/design-rfc033-run15-reingestion-quality-fixes.md#property-8-reversed-arabic-stems-match-in-numbering_depth)_
  - [x] <a id="72-property-reversed-regex-equivalence"></a>7.2 Property tests: reversed regex variants match forward-equivalent stems

    - **Property 10: Reversed Arabic stem regexes are equivalent to their forward form**
    - Verify reversed regex variants match "ةداملا" as equivalent to "المادة" (and the corresponding باب/فصل reversed forms).
    - **Validates: [RFC-033 D8](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d8-harden-arabic-ocr-tree-building-against-tesseract-rtl-reversed-text)**
  - [x] <a id="73-reversal-detection-and-repair"></a>7.3 Add per-line/per-block reversal detection with known-good word-list check

    - In `src/pageindex_mcp/converters.py`, add a reversal-detection function that checks candidate lines against a known-good Arabic word list; when reversal is detected, flip the text before feeding it into `_inject_arabic_structural_headings`.
    - _Requirements: [RFC-033 D8](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d8-harden-arabic-ocr-tree-building-against-tesseract-rtl-reversed-text) | [Design §Property 8 — reversed Arabic stems match in numbering_depth](../designs/design-rfc033-run15-reingestion-quality-fixes.md#property-8-reversed-arabic-stems-match-in-numbering_depth)_
  - [x] <a id="74-property-reversal-detection-and-repair-correctness"></a>7.4 Property tests: reversal detection identifies and correctly flips mirror-reversed text

    - **Property 11: Reversal detection is precise — no false positives on non-reversed Arabic**
    - Verify reversal detection correctly identifies mirror-reversed Arabic text and returns the corrected form. Negative test: verify non-reversed Arabic fixtures (modeled on مرسوم 13 / مرسوم 33 phrasing) are not affected by the reversal detector (no false positives).
    - **Validates: [RFC-033 D8](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d8-harden-arabic-ocr-tree-building-against-tesseract-rtl-reversed-text)**
  - [x] <a id="75-ocr-source-quality-comparison-hook"></a>7.5 (Lower priority) Add tree-path vs flat-path OCR quality comparison hook

    - In `src/pageindex_mcp/client.py`, add a comparison hook in the OCR escalation path (~lines 1370-1425) that can select the flat-path OCR source over the tree-path source when the tree path shows reversal artifacts the flip repair could not fully correct.
    - _Requirements: [RFC-033 D8](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d8-harden-arabic-ocr-tree-building-against-tesseract-rtl-reversed-text) | [Design §Property 8 — reversed Arabic stems match in numbering_depth](../designs/design-rfc033-run15-reingestion-quality-fixes.md#property-8-reversed-arabic-stems-match-in-numbering_depth)_

- [x] <a id="8-checkpoint--batch-3"></a>8. Checkpoint — Batch 3

  - Run `uv run pytest` and verify all Batch 3 property tests (Tasks [7.2](#72-property-reversed-regex-equivalence), [7.4](#74-property-reversal-detection-and-repair-correctness)) pass.
  - Confirm no regression against Batch 2's Arabic fragment/bidi detectors on shared synthetic fixtures (D8 depends on D2's groundwork per the RFC's stated batch ordering).
  - Ask the user if questions arise before proceeding.

- [ ] <a id="9-batch-4--d2-part-b-bidi-enforcement-promotion"></a>9. Batch 4 — D2 Part B: `BIDI_COHERENCE_ENFORCE` Promotion ([RFC-033 D2](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d2-arabic-single-letter-fragment-detection-and-bidi-coherence-enforcement) Part B)

  > ⚠️ **THE BATCH SEPARATION IS LOAD-BEARING — DO NOT MERGE THIS INTO BATCH 2.** D2 was folded into a single decision with two parts (reconciliation H-1), but Parts A and B **must not land together**. If an implementer reads "D2" as one unit and ships it in Batch 2, `BIDI_COHERENCE_ENFORCE` goes live against titles **our own pipeline corrupted** ([Task 1.11](#111-heading-reversal-guard-d2-part-a)) — mass-capping documents at MARGINAL for damage we inflicted. D2's own blast-radius document (حقوق الإنسان) is the first casualty. This batch is blocked until Part A has landed and the scoped re-ingest below has run.

  - [ ] <a id="91-scoped-reingest-and-remeasure"></a>9.1 **[GATE]* Scoped re-ingest and re-measurement of `bidi_coherence_violations`

    - **Blocked on [Task 1.11](#111-heading-reversal-guard-d2-part-a) landing and its property tests ([1.12](#112-property-heading-guard-idempotence)) passing.**
    - Re-ingest the Arabic documents exhibiting reversed-heading signatures (**scoped re-ingest**, per the 2026-08-06 H-1(b) decision — not the full Arabic corpus). Hand off to the `corpus-ingest-score` / `corpus-cycle` skills; this file does not perform ingestion.
    - Re-measure the `bidi_coherence_violations` counter against the post-fix corpus.
    - ⚠️ **Record the sampling frame alongside the number.** Measuring only on docs already known to show reversed headings over-samples the affected population, so the resulting rate is a **lower bound on the clean-doc false-positive rate, not an unbiased corpus-wide estimate.** Do not present it as a corpus-wide FP rate when justifying the promotion below.
    - _Requirements: Reconciliation H-1(b) (`../../audit/RECONCILIATION_REPORT.md`)_
  - [x] <a id="92-bidi-coherence-verdict-only-enforcement"></a>9.2 **[D2 Part B]* Promote `BIDI_COHERENCE_ENFORCE` to verdict-only enforcement

    - In `src/pageindex_mcp/helpers.py`, flip `BIDI_COHERENCE_ENFORCE` default to `true` (~line 1288). Change the enforcement path in `validate_tree` from raising `LowQualityTreeError` to setting a `bidi_degraded` flag.
    - Wire `classify_verdict()` to read `bidi_degraded` and cap the verdict at MARGINAL — enforcement must never gate persistence, only the returned verdict.
    - Justify the promotion with the [Task 9.1](#91-scoped-reingest-and-remeasure) measurement, stated explicitly as a lower bound.
    - _Requirements: [RFC-033 D2](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d2-arabic-single-letter-fragment-detection-and-bidi-coherence-enforcement) (Part B) | [Design §Property 2](../designs/design-rfc033-run15-reingestion-quality-fixes.md#property-2-arabic-single-letter-fragments-detected-without-false-positives-on-particles)_
  - [x] <a id="93-property-bidi-degraded-caps-verdict-not-persistence"></a>9.3 Property tests: `bidi_degraded` caps verdict without blocking persistence

    - **Property 8: Bidi enforcement is verdict-only, never persistence-gating**
    - Construct a tree with bidi-reversed node titles (e.g. modeled on تايوتحملا / ةصالخلا); verify `bidi_degraded` is set and the verdict is capped at MARGINAL, but `validate_tree` does NOT raise `LowQualityTreeError` (the tree is still persisted).
    - **Validates: [RFC-033 D2](../rfcs/033-run15-run15-reingestion-quality-fixes.md#d2-arabic-single-letter-fragment-detection-and-bidi-coherence-enforcement) (Part B)**

- [ ] <a id="10-checkpoint--batch-4"></a>10. Checkpoint — Batch 4

  - Run `uv run pytest` and verify [Task 9.3](#93-property-bidi-degraded-caps-verdict-not-persistence) passes.
  - Confirm the promotion justification cites the [Task 9.1](#91-scoped-reingest-and-remeasure) measurement **with its sampling frame stated**, not as a corpus-wide FP rate.
  - Ask the user if questions arise before proceeding.

- [ ] <a id="11-final-checkpoint"></a>11. Final Checkpoint

  - Run `uv run pytest` (full suite) and verify zero failures.
  - Verify no code path outside `wipe_processed()` performs a raw `processed/*` MinIO delete (Task [1.2](#12-wire-wipe_processed-into-skills-and-workflows)).
  - Verify `_garble_ratio` (Task [1.4](#14-remove-garble-ratio-full-text-tautology)) and the new Arabic fragment detector (Task [5.1](#51-single-letter-arabic-fragment-detection)) do not conflict on the same synthetic mixed-script fixture set.
  - Verify `bidi_degraded` enforcement (Task [9.2](#92-bidi-coherence-verdict-only-enforcement)) never raises `LowQualityTreeError` under any unit-test fixture — only caps the verdict.
  - Verify [Task 1.11](#111-heading-reversal-guard-d2-part-a) landed **before** [Task 9.2](#92-bidi-coherence-verdict-only-enforcement), and that logical-order Arabic headings survive `reconstruct_bidi_order` byte-identical ([Task 1.12](#112-property-heading-guard-idempotence)).
  - This tasks file does not include corpus re-ingestion or scoring — hand off to the corpus-ingest-score / corpus-cycle skills for that separately.
  - Ask the user if questions arise before proceeding.

## Notes

- Tasks marked with `*` are property-based tests; they may be deferred for a faster landing but should not be skipped permanently given CLAUDE.md Hard Rule 5 (never silently persist a low-quality tree) is directly implicated by [Task 9.2](#92-bidi-coherence-verdict-only-enforcement)/[9.3](#93-property-bidi-degraded-caps-verdict-not-persistence).
- **This tasks file intentionally excludes ingestion, verification, and re-ingest steps.** All "Integration test: re-ingest X and verify verdict Y" bullets from RFC-033's Test Strategy table are handled by the corpus-quality-cycle skills (`corpus-ingest`, `corpus-ingest-score`, `corpus-cycle`) after this batch of code changes lands — they are out of scope for this file by design, not by omission.
- [Task 1.1](#11-wipe_processed-utility-and-snapshot-relocation)'s snapshot-then-wipe ordering is load-bearing: the RFC is explicit that the snapshot MUST land in a prefix outside `processed/` (`snapshots/_prior_verdicts.json`) — writing it under `processed/` before the wipe would delete it in the same operation.
- [Task 1.4](#14-remove-garble-ratio-full-text-tautology)'s D1 fix is a hard prerequisite for [Task 5.1](#51-single-letter-arabic-fragment-detection)'s D2 detector per the RFC's stated Batch ordering (Batch 2 depends on Batch 0's garble-ratio fix being correct) — the fragment detector must operate on a windowed ratio that isn't already pinned to 1.0 by the tautology, or its threshold tuning will be meaningless.
- [Task 9.2](#92-bidi-coherence-verdict-only-enforcement)'s verdict-only gating is a strict requirement, not an implementation convenience: the RFC's Risks section calls out that حقوق الإنسان (347 nodes, stored PASS, known bidi-reversed titles) would become an ingestion failure if enforcement were persistence-gating instead of verdict-only. Do not "simplify" this by having `bidi_degraded` raise `LowQualityTreeError` directly.
- [Task 3.1](#31-table-segmentation-on-primary-path)'s D6 fix was explicitly deferred by a prior RFC (RFC-030) — per the RFC's Risks section, run `_segment_table_nodes` BEFORE `validate_tree` and confirm the regression test in [Task 3.2](#32-property-table-node-segmentation) passes before considering this task done, since there may be an unstated reason for the original deferral (e.g. validate_tree ordering concerns).
- [Task 7.1](#71-reversed-arabic-regex-variants) through [7.5](#75-ocr-source-quality-comparison-hook) (D8) are sequenced into Batch 3, after Batch 2 (D2), per the RFC's own batch plan — this avoids duplicate or conflicting Arabic text-detection logic between the fragment/bidi detectors and the reversal hardening.
- **[Task 1.11](#111-heading-reversal-guard-d2-part-a) (D2 Part A) and [Task 9.2](#92-bidi-coherence-verdict-only-enforcement) (D2 Part B) are one RFC decision split across two batches, and the split is load-bearing.** Per reconciliation H-1 the guard was folded into D2 rather than given its own decision number — but Part B promotes `BIDI_COHERENCE_ENFORCE` against titles Part A exists to stop corrupting. Shipping them together enforces a gate on self-inflicted damage. Part B is additionally gated on the scoped re-ingest ([Task 9.1](#91-scoped-reingest-and-remeasure)), whose measured FP rate is a **lower bound**, not a corpus-wide estimate.
- No task in this file may introduce `pymupdf4llm` as a fallback under any code path, per [CLAUDE.md Hard Rule 4](../../CLAUDE.md#hard-rules) (AGPL-3.0 awareness) — none of D0-D8 touch the extraction fallback chain, but this constraint applies to any incidental refactor touching `converters.py`.
- Batch 1 ([Tasks 3.1](#31-table-segmentation-on-primary-path)-[3.4](#34-property-image-standalone-override-scope)) has no functional dependency on Batch 0 beyond both touching `client.py`/`helpers.py` in the same modules — it is sequenced after Batch 0 purely for checkpoint hygiene per the RFC's stated rationale, not a hard code dependency.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8", "1.9", "1.10", "1.11", "1.12"] },
    { "id": 1, "tasks": ["2"] },
    { "id": 2, "tasks": ["3.1", "3.2", "3.3", "3.4"] },
    { "id": 3, "tasks": ["4"] },
    { "id": 4, "tasks": ["5.1", "5.2", "5.5", "5.6", "5.7"] },
    { "id": 5, "tasks": ["6"] },
    { "id": 6, "tasks": ["7.1", "7.2", "7.3", "7.4", "7.5"] },
    { "id": 7, "tasks": ["8"] },
    { "id": 8, "tasks": ["9.1", "9.2", "9.3"] },
    { "id": 9, "tasks": ["10"] },
    { "id": 10, "tasks": ["11"] }
  ]
}
```
