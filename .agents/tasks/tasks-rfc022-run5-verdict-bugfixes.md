<!-- Space: CITRA -->
<!-- Title: Tasks: RFC-022 Run 5 Verdict Bug-Fixes -->
<!-- Folder: Tasks -->

# Implementation Plan: RFC-022 Run 5 Verdict Bug-Fixes — Flat-Doc Synthesis, Image Routing, OCR Splice Repair

## Traceability

| Artifact               | Reference                                                                                                                                                        |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Governing RFC          | [RFC-022: Run 5 Verdict Bug-Fixes](../rfcs/022-run5-verdict-bugfixes.md)                                                                                        |
| Design Document        | [Design: RFC-022 Run 5 Verdict Bug-Fixes](../designs/design-rfc022-run5-verdict-bugfixes.md)                                                                    |
| PRD / Requirements     | `PRD.md`                                                                                                                                                         |
| Hard Rules             | [CLAUDE.md HR5](../../CLAUDE.md) (no silent low-quality tree)                                                                                                    |
| Implementation Order   | [RFC-022 §Implementation Plan](../rfcs/022-run5-verdict-bugfixes.md#implementation-plan)                                                                         |
| Test Plan              | [RFC-022 §Test Plan](../rfcs/022-run5-verdict-bugfixes.md#test-plan)                                                                                             |
| Correctness Properties | [Design §Correctness Properties](../designs/design-rfc022-run5-verdict-bugfixes.md#correctness-properties)                                                       |
| Rollback Strategy      | [RFC-022 §Rollback Strategy](../rfcs/022-run5-verdict-bugfixes.md#rollback-strategy)                                                                             |
| Predecessors           | RFC-017 (P0a/P0b) -> RFC-018 (D0-D3) -> RFC-019 (D0-D4) -> RFC-020 (F0-F5) -> RFC-021 (QF1-QF4) -> this RFC-022                                                |

## Overview

This plan implements three bug-fixes ([B1](../rfcs/022-run5-verdict-bugfixes.md#b1-flat-doc-verdict-blind-spot-structure--all-gates-blocked) P0, [B2](../rfcs/022-run5-verdict-bugfixes.md#b2-image-file-routing--gate-ordering-two-part) P1, [B3](../rfcs/022-run5-verdict-bugfixes.md#b3-ghv-tkv-ocr-splice-regression) P1) identified by Run 5 corpus reaudit, proceeding through the [RFC-022 implementation phases](../rfcs/022-run5-verdict-bugfixes.md#implementation-plan) and validating against the five [correctness properties](../designs/design-rfc022-run5-verdict-bugfixes.md#correctness-properties) in the design document. Total effort: **~2.0-2.25 person-days** across 4 phases on branch `feat/run5-verdict-bugfixes`. [B1](../rfcs/022-run5-verdict-bugfixes.md#b1-fix-synthetic-structure-from-flat-doc-blocks) is the critical-path item (flat docs get `structure=[]`, all verdict metrics degenerate) and lands first; [B3](../rfcs/022-run5-verdict-bugfixes.md#b3-fix-ghv-tkv-ocr-splice-trace--repair) is diagnosis-first. Target: [19 PASS / 4 MARGINAL / 1 FAIL / 1 ERROR on Run 6](../rfcs/022-run5-verdict-bugfixes.md#implementation-plan).

## Tasks

- [ ] <a id="1-b1-flat-doc-structure-synthesis"></a>1. Phase 1 — [B1](../rfcs/022-run5-verdict-bugfixes.md#b1-fix-synthetic-structure-from-flat-doc-blocks) Flat-Doc Structure Synthesis (0.5 d)

  *[RFC-022 §Implementation Plan — Phase 1](../rfcs/022-run5-verdict-bugfixes.md#implementation-plan): `classify_verdict` receives `structure=[]` for flat docs, causing all tree metrics (depth, node_count, max_leaf_ratio) to degenerate ([B1 root cause](../rfcs/022-run5-verdict-bugfixes.md#b1-flat-doc-verdict-blind-spot-structure--all-gates-blocked)). Two-part fix: synthetic structure from blocks + `_tree_is_garbled` empty guard.*

  - [x] <a id="11-synthetic-structure-from-blocks"></a>1.1 Synthetic structure from blocks (client.py) (P0, effort: S)

    - In `src/pageindex_mcp/client.py`, after `flat_structure = result.get("structure", [])` (line ~1050): add synthetic structure synthesis when `flat_structure` is empty but `blocks` is non-empty
    - Implementation:
      ```python
      if not flat_structure and blocks:
          flat_structure = [
              {"title": "", "text": b.get("text", "")}
              for b in blocks
              if b.get("text", "").strip()
          ]
      ```
    - Ensures `classify_verdict` receives a non-empty structure with real text content, enabling tree metrics (depth, node_count, max_leaf_ratio) to compute meaningful values
    - _Requirements:_ [RFC-022 B1-Fix](../rfcs/022-run5-verdict-bugfixes.md#b1-fix-synthetic-structure-from-flat-doc-blocks) | [Design Property 1](../designs/design-rfc022-run5-verdict-bugfixes.md#property-1-synthetic-structure-for-flat-docs) | [Design Service: client.py](../designs/design-rfc022-run5-verdict-bugfixes.md#1-clientpy) | [Design Sequence: Flat-Doc Verdict Flow](../designs/design-rfc022-run5-verdict-bugfixes.md#flat-doc-verdict-flow-b1)

  - [x] <a id="12-tree-is-garbled-empty-guard"></a>1.2 `_tree_is_garbled` empty guard (helpers.py) (P0, effort: S)

    - In `src/pageindex_mcp/helpers.py`, at top of `_tree_is_garbled`: add early return `if not nodes: return False`
    - Prevents `_tree_is_garbled([])` from returning a truthy/undefined result that blocks verdict computation for flat docs with no structure
    - _Requirements:_ [RFC-022 B1-Fix](../rfcs/022-run5-verdict-bugfixes.md#b1-fix-synthetic-structure-from-flat-doc-blocks) | [Design Property 2](../designs/design-rfc022-run5-verdict-bugfixes.md#property-2-tree-is-garbled-empty-guard) | [Design Service: helpers.py](../designs/design-rfc022-run5-verdict-bugfixes.md#2-helperspy)

  - [ ] <a id="13-b1-unit-tests"></a>1.3 B1 unit tests (`tests/test_rfc022_b1.py`) (P0, effort: M)

    - (a) Empty structure + text blocks -> synthetic structure generated with nodes matching block count
    - (b) Synthetic structure -> `classify_verdict` returns `cat_b_promoted` (not degenerate MARGINAL/FAIL)
    - (c) Empty structure + empty blocks -> no synthetic structure generated, verdict MARGINAL
    - (d) Non-empty garbled structure -> garbled detection still fires (no regression)
    - (e) `_tree_is_garbled([])` -> returns `False`
    - (f) `_tree_is_garbled([{"text": "real content"}])` -> unchanged behavior (still evaluates content)
    - **Validates:** [Design Property 1](../designs/design-rfc022-run5-verdict-bugfixes.md#property-1-synthetic-structure-for-flat-docs) | [Design Property 2](../designs/design-rfc022-run5-verdict-bugfixes.md#property-2-tree-is-garbled-empty-guard) | [RFC-022 Test Plan](../rfcs/022-run5-verdict-bugfixes.md#test-plan)
    - _Requirements:_ [RFC-022 B1-Fix](../rfcs/022-run5-verdict-bugfixes.md#b1-fix-synthetic-structure-from-flat-doc-blocks) | [Design Property 1](../designs/design-rfc022-run5-verdict-bugfixes.md#property-1-synthetic-structure-for-flat-docs) | [Design Property 2](../designs/design-rfc022-run5-verdict-bugfixes.md#property-2-tree-is-garbled-empty-guard)

  - [ ] <a id="14-checkpoint--b1"></a>1.4 Checkpoint -- Phase 1

    - Run `uv run pytest` -- all tests green
    - Spot-reingest doc 24 -- verify synthetic structure generated and verdict promotes to PASS
    - Cross-ref: [RFC-022 §Validation Checkpoints](../rfcs/022-run5-verdict-bugfixes.md#validation-checkpoints)

- [ ] <a id="2-b2-image-routing-gate-reorder"></a>2. Phase 2 — [B2](../rfcs/022-run5-verdict-bugfixes.md#b2-fix-image-routing--gate-reorder-two-part) Image Routing + Gate Reorder (0.5 d)

  *[RFC-022 §Implementation Plan — Phase 2](../rfcs/022-run5-verdict-bugfixes.md#implementation-plan): two-part fix — Part A: extension-based `content_class="image_standalone"` override not firing after `route_and_extract_flat` ([B2 Part A](../rfcs/022-run5-verdict-bugfixes.md#b2-image-file-routing--gate-ordering-two-part)); Part B: QF2a image_enrichment_promoted gate ordered below `max_leaf_ratio > 0.75` hard-FAIL, preventing promotion ([B2 Part B](../rfcs/022-run5-verdict-bugfixes.md#b2-image-file-routing--gate-ordering-two-part)).*

  - [ ] <a id="21-extension-based-content-class-override"></a>2.1 Extension-based `content_class` override (client.py) (P1, effort: S)

    - In `src/pageindex_mcp/client.py`, after `route_and_extract_flat` + existing `image_standalone` detection: add extension-based override using file extension check
    - When file extension is in image set (`.jpg`, `.jpeg`, `.png`, `.tiff`, `.tif`): set `content_class = "image_standalone"` regardless of what `route_and_extract_flat` returned
    - Ensures image files are always routed through `_classify_image_verdict` instead of falling through to tree/flat verdict path
    - _Requirements:_ [RFC-022 B2-Fix Part A](../rfcs/022-run5-verdict-bugfixes.md#b2-fix-image-routing--gate-reorder-two-part) | [Design Property 3](../designs/design-rfc022-run5-verdict-bugfixes.md#property-3-image-extension-routing) | [Design Service: client.py](../designs/design-rfc022-run5-verdict-bugfixes.md#1-clientpy) | [Design Sequence: Image Standalone Routing Flow](../designs/design-rfc022-run5-verdict-bugfixes.md#image-standalone-routing-flow-b2)

  - [ ] <a id="22-qf2a-gate-hoist"></a>2.2 QF2a gate hoist (helpers.py) (P1, effort: S)

    - In `src/pageindex_mcp/helpers.py`: move the QF2a `image_enrichment_promoted` block ABOVE the `max_leaf_ratio > 0.75` hard-FAIL gate
    - Currently `max_leaf_ratio > 0.75` returns FAIL before QF2a promotion can fire for flat_prose/flat_mixed docs with high leaf ratios
    - After hoist: image-enriched flat docs with `image_enrichment_ratio >= 0.8` promote to PASS even when `max_leaf_ratio > 0.75`
    - _Requirements:_ [RFC-022 B2-Fix Part B](../rfcs/022-run5-verdict-bugfixes.md#b2-fix-image-routing--gate-reorder-two-part) | [Design Property 4](../designs/design-rfc022-run5-verdict-bugfixes.md#property-4-qf2a-gate-ordering) | [Design Service: helpers.py](../designs/design-rfc022-run5-verdict-bugfixes.md#2-helperspy)

  - [ ] <a id="23-b2-unit-tests"></a>2.3 B2 unit tests (`tests/test_rfc022_b2.py`) (P1, effort: M)

    - (a) `.jpg` file extension -> `content_class="image_standalone"` set after `route_and_extract_flat`
    - (b) `_classify_image_verdict(1.0)` -> PASS verdict
    - (c) `_classify_image_verdict(None)` -> FAIL verdict
    - (d) Hoisted QF2a: `flat_prose` + `image_enrichment_ratio=0.9` + `max_leaf_ratio=1.0` -> PASS (promotion fires before hard-FAIL)
    - (e) `flat_prose` + no `image_enrichment_ratio` + `max_leaf_ratio=1.0` -> FAIL (hard-FAIL still works for non-image-enriched)
    - (f) `IMAGE_STANDALONE_PIPELINE_ENABLED=false` -> falls back to standard path
    - **Validates:** [Design Property 3](../designs/design-rfc022-run5-verdict-bugfixes.md#property-3-image-extension-routing) | [Design Property 4](../designs/design-rfc022-run5-verdict-bugfixes.md#property-4-qf2a-gate-ordering) | [RFC-022 Test Plan](../rfcs/022-run5-verdict-bugfixes.md#test-plan)
    - _Requirements:_ [RFC-022 B2-Fix Part A](../rfcs/022-run5-verdict-bugfixes.md#b2-fix-image-routing--gate-reorder-two-part) | [RFC-022 B2-Fix Part B](../rfcs/022-run5-verdict-bugfixes.md#b2-fix-image-routing--gate-reorder-two-part) | [Design Property 3](../designs/design-rfc022-run5-verdict-bugfixes.md#property-3-image-extension-routing) | [Design Property 4](../designs/design-rfc022-run5-verdict-bugfixes.md#property-4-qf2a-gate-ordering)

  - [ ] <a id="24-checkpoint--b2"></a>2.4 Checkpoint -- Phase 2

    - Run `uv run pytest` -- all tests green
    - Spot-reingest doc 13 -- verify `image_standalone` routing and PASS verdict via `_classify_image_verdict`
    - Cross-ref: [RFC-022 §Validation Checkpoints](../rfcs/022-run5-verdict-bugfixes.md#validation-checkpoints)

- [ ] <a id="3-b3-ghv-tkv-ocr-splice"></a>3. Phase 3 — [B3](../rfcs/022-run5-verdict-bugfixes.md#b3-fix-ghv-tkv-ocr-splice-trace--repair) GHV-TKV OCR Splice (0.5-0.75 d)

  *[RFC-022 §Implementation Plan — Phase 3](../rfcs/022-run5-verdict-bugfixes.md#implementation-plan): GHV-TKV OCR splice regression ([B3 root cause](../rfcs/022-run5-verdict-bugfixes.md#b3-ghv-tkv-ocr-splice-regression)). Diagnosis-first approach in Phase 1: check existing investigation, add debug logging, verify recovery paths before implementing fix.*

  - [ ] <a id="31-b3-diagnosis"></a>3.1 B3 diagnosis (P1, effort: M)

    - Check `audit/OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION_2026-07-27.md` for applicability to B3
    - Add debug logging at `_recover_picture_text()` in `src/pageindex_mcp/client.py` to trace OCR splice behavior
    - Verify F1 coverage exemption path, P0b filter, and picture-text recovery for doc 3
    - Identify whether the regression is: P0b filter too aggressive, post-processing validation dropping valid OCR, or page-level OCR decoupling incomplete
    - Write diagnosis findings with proposed fix approach
    - _Requirements:_ [RFC-022 B3-Fix](../rfcs/022-run5-verdict-bugfixes.md#b3-fix-ghv-tkv-ocr-splice-trace--repair) | [Design Property 5](../designs/design-rfc022-run5-verdict-bugfixes.md#property-5-ocr-splice-completeness)

  - [ ] <a id="32-b3-fix"></a>3.2 B3 fix (based on diagnosis) (P1, effort: S-M)

    - Implement fix based on measured data from [Task 3.1](#31-b3-diagnosis)
    - Three hypothesized fixes (one or more may apply):
      - P0b filter relaxation: loosen criteria that reject valid OCR text from picture blocks
      - Post-processing validation: fix validation logic that drops enriched text during splice
      - Page-level OCR decoupling: ensure per-picture OCR is not conflated with page-level OCR decisions
    - Files: `src/pageindex_mcp/client.py`, `src/pageindex_mcp/helpers.py`
    - _Requirements:_ [RFC-022 B3-Fix](../rfcs/022-run5-verdict-bugfixes.md#b3-fix-ghv-tkv-ocr-splice-trace--repair) | [Design Property 5](../designs/design-rfc022-run5-verdict-bugfixes.md#property-5-ocr-splice-completeness) | [Design Service: client.py](../designs/design-rfc022-run5-verdict-bugfixes.md#1-clientpy) | [Design Service: helpers.py](../designs/design-rfc022-run5-verdict-bugfixes.md#2-helperspy)

  - [ ] <a id="33-b3-unit-tests"></a>3.3 B3 unit tests (`tests/test_rfc022_b3.py`) (P1, effort: M)

    - (a) Doc 3 code-path trace: OCR splice path fires and produces enriched blocks
    - (b) Enriched blocks count > 0 after splice (regression guard)
    - (c) Total enriched chars > 375 (minimum viable OCR content threshold)
    - Tests will be finalized after [Task 3.1](#31-b3-diagnosis) determines the actual failure mechanism
    - **Validates:** [Design Property 5](../designs/design-rfc022-run5-verdict-bugfixes.md#property-5-ocr-splice-completeness) | [RFC-022 Test Plan](../rfcs/022-run5-verdict-bugfixes.md#test-plan)
    - _Requirements:_ [RFC-022 B3-Fix](../rfcs/022-run5-verdict-bugfixes.md#b3-fix-ghv-tkv-ocr-splice-trace--repair) | [Design Property 5](../designs/design-rfc022-run5-verdict-bugfixes.md#property-5-ocr-splice-completeness)

  - [ ] <a id="34-checkpoint--b3"></a>3.4 Checkpoint -- Phase 3

    - Run `uv run pytest` -- all tests green
    - Spot-reingest doc 3 -- verify OCR splice produces enriched blocks and verdict is MARGINAL (not worse)
    - Cross-ref: [RFC-022 §Validation Checkpoints](../rfcs/022-run5-verdict-bugfixes.md#validation-checkpoints)

- [ ] <a id="4-pipeline-version-bump-reaudit"></a>4. Phase 4 — Pipeline Version Bump + Reaudit (0.5 d)

  *[RFC-022 §Implementation Plan — Phase 4](../rfcs/022-run5-verdict-bugfixes.md#implementation-plan): increment pipeline version to force reingestion, then full 25-doc reaudit to validate projected Run 6 outcomes and verify zero regressions on Run 5's 17 PASS docs.*

  - [ ] <a id="41-pipeline-version-bump"></a>4.1 Pipeline version bump (P0, effort: S)

    - Increment `CURRENT_PIPELINE_VERSION` from 3 to 4 in the relevant source file
    - Forces hash-based change detection in `preprocess_client.py` to reingest all documents
    - _Requirements:_ [RFC-022 §Pipeline Version](../rfcs/022-run5-verdict-bugfixes.md#pipeline-version)

  - [ ] <a id="42-full-25-doc-reaudit"></a>4.2 Full 25-doc reaudit (P0, effort: L)

    - Full batch reingestion via `preprocess_client.py`
    - Produce Run 6 audit scorecard against [RFC-022 projected impact](../rfcs/022-run5-verdict-bugfixes.md#implementation-plan) (target: 19 PASS / 4 MARGINAL / 1 FAIL / 1 ERROR)
    - Per-doc checks:
      - Doc 24 (flat doc): [B1](#11-synthetic-structure-from-blocks) -> PASS (synthetic structure enables verdict)
      - Doc 13 (image file): [B2](#21-extension-based-content-class-override) -> PASS (extension routing + gate hoist)
      - Doc 3 (GHV-TKV): [B3](#32-b3-fix) -> MARGINAL (OCR splice repaired)
    - Verify zero regressions on Run 5's 17 PASS docs
    - Record results in a Run 6 audit file under `audit/`; explain any variance from projection
    - Cross-ref: [RFC-022 §Validation Checkpoints](../rfcs/022-run5-verdict-bugfixes.md#validation-checkpoints)
    - _Requirements:_ [RFC-022 B1-Fix](../rfcs/022-run5-verdict-bugfixes.md#b1-fix-synthetic-structure-from-flat-doc-blocks) | [RFC-022 B2-Fix](../rfcs/022-run5-verdict-bugfixes.md#b2-fix-image-routing--gate-reorder-two-part) | [RFC-022 B3-Fix](../rfcs/022-run5-verdict-bugfixes.md#b3-fix-ghv-tkv-ocr-splice-trace--repair) | [Design Properties 1-5](../designs/design-rfc022-run5-verdict-bugfixes.md#correctness-properties)

  - [ ] <a id="43-checkpoint--final"></a>4.3 Checkpoint -- Final

    - Full Run 6 scorecard matches projections: 19 PASS / 4 MARGINAL / 1 FAIL / 1 ERROR
    - All 17 Run 5 PASS docs still PASS (zero regressions)
    - Cross-ref: [RFC-022 §Validation Checkpoints](../rfcs/022-run5-verdict-bugfixes.md#validation-checkpoints)

## Notes

- [B1](../rfcs/022-run5-verdict-bugfixes.md#b1-flat-doc-verdict-blind-spot-structure--all-gates-blocked) is the highest-priority fix (P0) -- flat docs receive `structure=[]` which causes ALL verdict metrics to degenerate, making it impossible for any gate (tree depth, node_count, max_leaf_ratio) to fire correctly. This blocks verdict computation for every flat document in the corpus
- [B2](../rfcs/022-run5-verdict-bugfixes.md#b2-image-file-routing--gate-ordering-two-part) is a two-part fix: Part A ensures image files hit `_classify_image_verdict` via extension-based routing; Part B ensures QF2a promotion fires before the `max_leaf_ratio > 0.75` hard-FAIL gate. Either part alone is insufficient for image files that go through the flat path ([RFC-022 §Risk Assessment](../rfcs/022-run5-verdict-bugfixes.md#risk-assessment))
- [B3](../rfcs/022-run5-verdict-bugfixes.md#b3-ghv-tkv-ocr-splice-regression) is diagnosis-first -- the OCR splice regression may be caused by P0b filter, post-processing validation, or page-level OCR decoupling; [Task 3.1](#31-b3-diagnosis) must identify the actual mechanism before [Task 3.2](#32-b3-fix) implements a fix
- B1 and B2 code changes are independent (different functions in client.py and helpers.py) but tests in Phase 1 and Phase 2 depend on their respective code changes landing first
- Phase 3 (B3) is independent of Phases 1-2 and could run in parallel, but the RFC prescribes sequential ordering for checkpoint clarity
- Phase 4 requires all three bug-fixes to land before the pipeline version bump forces reingestion -- any partial fix would produce misleading Run 6 numbers
- Each phase is an isolated commit with clear rollback boundaries (see [RFC-022 §Rollback Strategy](../rfcs/022-run5-verdict-bugfixes.md#rollback-strategy))
- All fixes apply to future ingestions only -- realized scorecard requires the Run 6 reaudit ([Task 4.2](#42-full-25-doc-reaudit))
- The `_tree_is_garbled` empty guard ([Task 1.2](#12-tree-is-garbled-empty-guard)) is defensive: even with synthetic structure, an edge case where blocks are all-whitespace would still yield empty structure after filtering ([RFC-022 §Risk Assessment](../rfcs/022-run5-verdict-bugfixes.md#risk-assessment))
- [RFC-022 Open Questions](../rfcs/022-run5-verdict-bugfixes.md#open-questions) may surface additional work during B3 diagnosis; any scope expansion should be deferred to a follow-up RFC

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "name": "B1 code changes (parallel)",
      "tasks": ["1.1", "1.2"],
      "depends_on": [],
      "description": "Synthetic structure from blocks + _tree_is_garbled empty guard"
    },
    {
      "id": 1,
      "name": "B1 tests",
      "tasks": ["1.3"],
      "depends_on": [0]
    },
    {
      "id": 2,
      "name": "Phase 1 checkpoint",
      "tasks": ["1.4"],
      "depends_on": [1]
    },
    {
      "id": 3,
      "name": "B2 code changes (parallel)",
      "tasks": ["2.1", "2.2"],
      "depends_on": [2],
      "description": "Extension-based content_class override + QF2a gate hoist"
    },
    {
      "id": 4,
      "name": "B2 tests",
      "tasks": ["2.3"],
      "depends_on": [3]
    },
    {
      "id": 5,
      "name": "Phase 2 checkpoint",
      "tasks": ["2.4"],
      "depends_on": [4]
    },
    {
      "id": 6,
      "name": "B3 diagnosis",
      "tasks": ["3.1"],
      "depends_on": [5],
      "description": "Trace OCR splice regression root cause"
    },
    {
      "id": 7,
      "name": "B3 fix (post-diagnosis)",
      "tasks": ["3.2"],
      "depends_on": [6]
    },
    {
      "id": 8,
      "name": "B3 tests",
      "tasks": ["3.3"],
      "depends_on": [7]
    },
    {
      "id": 9,
      "name": "Phase 3 checkpoint",
      "tasks": ["3.4"],
      "depends_on": [8]
    },
    {
      "id": 10,
      "name": "Pipeline version bump",
      "tasks": ["4.1"],
      "depends_on": [2, 5, 9],
      "description": "Requires all three bug-fixes landed"
    },
    {
      "id": 11,
      "name": "Full 25-doc reaudit",
      "tasks": ["4.2"],
      "depends_on": [10]
    },
    {
      "id": 12,
      "name": "Final checkpoint",
      "tasks": ["4.3"],
      "depends_on": [11]
    }
  ]
}
```
