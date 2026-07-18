<!-- Space: CITRA -->
<!-- Title: Implementation Plan: Structural Hardening Batch — Performance, Error Handling, Corpus Quality -->
<!-- Folder: Tasks -->

# Implementation Plan: Structural Hardening Batch — Performance, Error Handling, Corpus Quality

## Traceability

| Artifact                      | Reference                                                                                                                 |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Governing RFC(s)              | [RFC-013: Structural Hardening Batch](../rfcs/013-structural-hardening.md)                                                 |
| Design Document               | [Design: Structural Hardening Batch](../designs/design-rfc013-structural-hardening.md)                                     |
| PRD / Requirements            | `PRD.md`                                                                                                                    |
| Hard Rules                    | [CLAUDE.md HR4 + HR5](../rfcs/013-structural-hardening.md#hard-rule-constraints-claudemd--binding)                         |
| RFC Implementation Order      | [RFC-013 Implementation Plan](../rfcs/013-structural-hardening.md#implementation-plan)                                     |
| RFC Test Strategy             | [RFC-013 Test Strategy](../rfcs/013-structural-hardening.md#test-strategy)                                                 |
| Design Correctness Properties | [Design Correctness Properties](../designs/design-rfc013-structural-hardening.md#correctness-properties)                  |
| Design Testing Strategy       | [Design Testing Strategy](../designs/design-rfc013-structural-hardening.md#testing-strategy)                              |

## Overview

Implements the four open items from `audit/DOCSTORE_AUDIT_REPORT.md` Batch 2 (structural hardening), plus closes three items already fixed on re-verification, organized into three batches per [RFC-013 Implementation Plan](../rfcs/013-structural-hardening.md#implementation-plan). The plan proceeds from closing already-resolved issues ([Batch 0](#1-batch-0--close-resolved-issues-d1-d3)), through two independent code fixes for performance and duplication ([Batch 1](#2-batch-1--independent-code-fixes-d4-d5)), to two corpus-quality fixes on the garbling-detection path that feeds `validate_tree()` ([Batch 2](#3-batch-2--corpus-quality-fixes-d6-d7)), validating each batch against the design document's [4 correctness properties](../designs/design-rfc013-structural-hardening.md#correctness-properties) before advancing. All fixes are small (~15 lines each per [RFC-013 Implementation Plan](../rfcs/013-structural-hardening.md#implementation-plan)) and none touch the AGPL/pymupdf surface ([HR4](../rfcs/013-structural-hardening.md#hard-rule-constraints-claudemd--binding)); the two corpus-quality fixes tighten rather than loosen the garbling gate ([HR5](../rfcs/013-structural-hardening.md#hard-rule-constraints-claudemd--binding)). Stack: Python 3.12, FastMCP, arq, MinIO, Redis, Prometheus.

## Tasks

- [x] <a id="1-batch-0--close-resolved-issues-d1-d3"></a>1. Batch 0 — Close Resolved Issues ([D1-D3 (ISS-08, ISS-18, ISS-19)](../rfcs/013-structural-hardening.md#d1-d3--iss-08-iss-18-iss-19-no-code-change-close-as-resolved))

  *[RFC-013 Implementation Plan](../rfcs/013-structural-hardening.md#implementation-plan): "D1-D3 (ISS-08/18/19) require no implementation — mark resolved in audit tracker"*

  - [x] <a id="11-mark-iss-08-iss-18-iss-19-resolved"></a>1.1 Mark ISS-08, ISS-18, ISS-19 resolved in audit tracker ([D1-D3](../rfcs/013-structural-hardening.md#d1-d3--iss-08-iss-18-iss-19-no-code-change-close-as-resolved))

    - Verify ISS-08: `_describe` (`converters.py:1316-1369`) already retries transient OpenAI errors with backoff and increments `IMAGE_DESCRIBE_FAILURES.labels(error_type=...)` at retry-exhausted and permanent-failure branches — no code change
    - Verify ISS-18/19: `_extract_json_object` (`helpers.py:62-76`) already shared by both call sites; `_search_one_doc` narrows its except to `(json.JSONDecodeError, KeyError, TypeError)` and increments `RAG_PARSE_FAILURES.labels(doc_id=doc_id)` (`helpers.py:221-223`) — no code change
    - Note the `_prefilter_docs` narrowed-except-but-no-counter gap as an acknowledged out-of-scope minor gap (per [RFC-013 What This RFC Does NOT Cover](../rfcs/013-structural-hardening.md#what-this-rfc-does-not-cover)) — do not action it in this RFC
    - Update `audit/DOCSTORE_AUDIT_REPORT.md` tracker entries for ISS-08, ISS-18, ISS-19 to "resolved"
    - _Requirements:_ [RFC-013 D1-D3](../rfcs/013-structural-hardening.md#d1-d3--iss-08-iss-18-iss-19-no-code-change-close-as-resolved) | [RFC-013 What This RFC Covers](../rfcs/013-structural-hardening.md#what-this-rfc-covers)
  - [x] <a id="12-checkpoint--batch-0"></a>1.2 Checkpoint — Batch 0

    - Confirm no code changes were made for ISS-08, ISS-18, ISS-19 — verification-only batch
    - Confirm audit tracker reflects resolved status for all three issues
    - Ask user if questions arise before proceeding
- [x] <a id="2-batch-1--independent-code-fixes-d4-d5"></a>2. Batch 1 — Independent Code Fixes ([D4 (ISS-05)](../rfcs/013-structural-hardening.md#d4--iss-05-bounded-concurrency-minio-fetch-for-list_processed_docs), [D5 (ISS-44)](../rfcs/013-structural-hardening.md#d5--iss-44-extract-shared-page-hit-helper))

  *[RFC-013 Implementation Plan](../rfcs/013-structural-hardening.md#implementation-plan): "D4 (ISS-05, ~15 lines) — independent, ship anytime; D5 (ISS-44, ~15 lines) — independent, ship anytime"*

  - [x] <a id="21-bounded-concurrency-minio-fetch-d4"></a>2.1 Bounded-concurrency MinIO fetch for `list_processed_docs` ([D4 (ISS-05)](../rfcs/013-structural-hardening.md#d4--iss-05-bounded-concurrency-minio-fetch-for-list_processed_docs))

    - Replace the serial per-doc `mc.get_object` loop in `storage.py:420-423` with a bounded-concurrency fetch: `asyncio.Semaphore(10)` guarding an `async def _fetch(doc_id, obj_name)` that wraps `mc.get_object` in `asyncio.to_thread`
    - Gather all fetches with `asyncio.gather(*(_fetch(d, o) for d, o in meta_keys.items()), return_exceptions=True)`
    - Since `list_processed_docs` is currently sync, either make it `async` or wrap the call site (`client.py:286`) in `asyncio.to_thread`
    - Do NOT implement the registry-only long-term fix (Approach B) — that is explicitly out of scope per [RFC-013 What This RFC Does NOT Cover](../rfcs/013-structural-hardening.md#what-this-rfc-does-not-cover); this task ships only the bounded-concurrency interim (Approach C)
    - _Requirements:_ [RFC-013 D4 (ISS-05)](../rfcs/013-structural-hardening.md#d4--iss-05-bounded-concurrency-minio-fetch-for-list_processed_docs) | [Design Property 1](../designs/design-rfc013-structural-hardening.md#property-1-bounded-concurrency-minio-fetch) | [Design Service: storage.py](../designs/design-rfc013-structural-hardening.md#1-storagepy) | [Design Sequence: Listing Flow](../designs/design-rfc013-structural-hardening.md#listing-flow--d4)
  - [x] <a id="22-extract-shared-page-hit-helper-d5"></a>2.2 Extract shared page-hit helper ([D5 (ISS-44)](../rfcs/013-structural-hardening.md#d5--iss-44-extract-shared-page-hit-helper))

    - Add `_extract_page_hits(structure: list, pages: str) -> list[dict]` to `helpers.py`, combining `_parse_page_spec(pages)` and the existing shared `_build_node_map` plus a page-set intersection filter (`_node_pages(n) & wanted`)
    - Replace the independently-implemented page-spec parse and node-filter logic in `tools/documents.py` (~352-360) and `client.py` (~769-776) with a call to `_extract_page_hits(structure, pages)`
    - Preserve each call site's own logging/metrics wrapper around the call — same pattern the codebase already uses for `_build_node_map`
    - _Requirements:_ [RFC-013 D5 (ISS-44)](../rfcs/013-structural-hardening.md#d5--iss-44-extract-shared-page-hit-helper) | [Design Property 2](../designs/design-rfc013-structural-hardening.md#property-2-shared-page-hit-extraction) | [Design Service: helpers.py](../designs/design-rfc013-structural-hardening.md#2-helperspy) | [Design Service: tools/documents.py](../designs/design-rfc013-structural-hardening.md#4-toolsdocumentspy) | [Design Service: client.py](../designs/design-rfc013-structural-hardening.md#5-clientpy) | [Design Sequence: Query Flow](../designs/design-rfc013-structural-hardening.md#query-flow--d5)
  - [x] <a id="23-unit-tests-d4-d5"></a>2.3 Write unit tests for D4 and D5 ([D4](../rfcs/013-structural-hardening.md#d4--iss-05-bounded-concurrency-minio-fetch-for-list_processed_docs), [D5](../rfcs/013-structural-hardening.md#d5--iss-44-extract-shared-page-hit-helper))

    - **[Design Property 1](../designs/design-rfc013-structural-hardening.md#property-1-bounded-concurrency-minio-fetch) — Bounded-concurrency MinIO fetch**: mock `mc.get_object` and assert `list_processed_docs` issues fetches under the semaphore bound (max 10 concurrent in-flight calls), per [RFC Test Strategy: D4](../rfcs/013-structural-hardening.md#test-strategy)
    - **[Design Property 2](../designs/design-rfc013-structural-hardening.md#property-2-shared-page-hit-extraction) — Shared page-hit extraction**: parametrized test asserting `tools/documents.py` and `client.py` produce identical page-hit results for the same `(structure, pages)` input post-extraction, per [RFC Test Strategy: D5](../rfcs/013-structural-hardening.md#test-strategy)
    - **Validates:** [Design Property 1](../designs/design-rfc013-structural-hardening.md#property-1-bounded-concurrency-minio-fetch) | [Design Property 2](../designs/design-rfc013-structural-hardening.md#property-2-shared-page-hit-extraction) | [RFC-013 D4](../rfcs/013-structural-hardening.md#d4--iss-05-bounded-concurrency-minio-fetch-for-list_processed_docs) | [RFC-013 D5](../rfcs/013-structural-hardening.md#d5--iss-44-extract-shared-page-hit-helper) | [RFC Test Strategy](../rfcs/013-structural-hardening.md#test-strategy)
  - [x] <a id="24-checkpoint--batch-1"></a>2.4 Checkpoint — Batch 1

    - Run `uv run pytest` — all existing tests + new Batch 1 tests pass
    - Verify [Batch 0](#1-batch-0--close-resolved-issues-d1-d3) status unaffected
    - Verify [Design Property 1](../designs/design-rfc013-structural-hardening.md#property-1-bounded-concurrency-minio-fetch), [Design Property 2](../designs/design-rfc013-structural-hardening.md#property-2-shared-page-hit-extraction) green
    - Confirm no AGPL/pymupdf surface touched ([HR4](../rfcs/013-structural-hardening.md#hard-rule-constraints-claudemd--binding))
    - Ask user if questions arise before proceeding
- [x] <a id="3-batch-2--corpus-quality-fixes-d6-d7"></a>3. Batch 2 — Corpus-Quality Fixes ([D6 (ISS-34)](../rfcs/013-structural-hardening.md#d6--iss-34-raise-on-missing-non-latin-tessdata-instead-of-silent-drop), [D7 (ISS-36)](../rfcs/013-structural-hardening.md#d7--iss-36-deduplicate-garble-detection-into-one-shared-function))

  *[RFC-013 Implementation Plan](../rfcs/013-structural-hardening.md#implementation-plan): "D6 (ISS-34, ~15 lines) — pairs with `ara.traineddata` pre-bake infra item for full effect, but raise itself independently correct, ship first; D7 (ISS-36, ~15 lines) — ship, run corpus re-validation follow-up before declaring done"*

  - [x] <a id="31-tessdata-unavailable-error-d6"></a>3.1 Raise `TessdataUnavailableError` on missing non-Latin tessdata ([D6 (ISS-34)](../rfcs/013-structural-hardening.md#d6--iss-34-raise-on-missing-non-latin-tessdata-instead-of-silent-drop))

    - Define `class TessdataUnavailableError(RuntimeError): pass` in `converters.py`
    - In the language-resolution loop inside `ensure_tessdata` (`converters.py:719-752`), replace the silent `logger.warning` + empty-`available` + fallback-to-`["deu", "eng"]` path with: if `lang not in _LATIN_LANGS` and `lang not in available`, raise `TessdataUnavailableError(f"non-Latin tessdata missing: {lang}")`
    - Confirm this is the fix for the root cause of the مرسوم-13 Latin-mojibake-passes-garble-gate failure mode (memory `fix3-ocr-escalation-mojibake-escape`)
    - Verify `client.py:472` (which feeds `ensure_tessdata`'s resolved langs into `pdf_to_markdown_docling`) and `client.py:492-496` (`except Exception → OCR_ESCALATION_TOTAL.labels(result="error")`) correctly surface this new raise as `low_quality_tree` instead of persisting false-clean mojibake — no changes needed there, verify only
    - Do NOT change Latin-language (deu/eng) fallback behavior — only non-Latin requests raise
    - Flag the companion infra item — pre-baking `ara.traineddata` — as a tracked-separately, non-code dependency for this raise to not become the common case for Arabic corpora (see [RFC-013 Risk 1](../rfcs/013-structural-hardening.md#risks))
    - _Requirements:_ [RFC-013 D6 (ISS-34)](../rfcs/013-structural-hardening.md#d6--iss-34-raise-on-missing-non-latin-tessdata-instead-of-silent-drop) | [Design Property 3](../designs/design-rfc013-structural-hardening.md#property-3-non-latin-tessdata-raise) | [Design Service: converters.py](../designs/design-rfc013-structural-hardening.md#3-converterspy) | [Design Sequence: Ingestion Flow](../designs/design-rfc013-structural-hardening.md#ingestion-flow--d6--d7) | [CLAUDE.md HR5](../rfcs/013-structural-hardening.md#hard-rule-constraints-claudemd--binding)
  - [x] <a id="32-deduplicate-garble-detection-d7"></a>3.2 Deduplicate garble detection into one shared function ([D7 (ISS-36)](../rfcs/013-structural-hardening.md#d7--iss-36-deduplicate-garble-detection-into-one-shared-function))

    - Extract the existing digit-ratio (>0.60, gated on `len(blob) > 500`) and token-repetition (>0.30, gated on `len(tokens) > 20`) checks from `_tree_is_garbled` (`helpers.py:535`) and `_flat_text_is_garbled` (`helpers.py:1072`) into a single shared `_is_garbled_blob(text: str) -> bool`
    - Redefine `_tree_is_garbled(structure) -> bool` as `return _is_garbled_blob(_flatten_tree_text(structure))`
    - Redefine `_flat_text_is_garbled(text: str) -> bool` as `return _is_garbled_blob(text)`
    - Preserve the 500-char and 20-token floors exactly as-is — this is pure deduplication, not a heuristic change, per [RFC-013 What This RFC Does NOT Cover](../rfcs/013-structural-hardening.md#what-this-rfc-does-not-cover)
    - Do NOT introduce a new sub-500-char ratio threshold — explicitly out of scope due to false-positive risk (GHV-TKV-Tarif wide-table false positive on pipe/€ symbols)
    - _Requirements:_ [RFC-013 D7 (ISS-36)](../rfcs/013-structural-hardening.md#d7--iss-36-deduplicate-garble-detection-into-one-shared-function) | [Design Property 4](../designs/design-rfc013-structural-hardening.md#property-4-unified-garble-detection) | [Design Service: helpers.py](../designs/design-rfc013-structural-hardening.md#2-helperspy) | [Design Sequence: Ingestion Flow](../designs/design-rfc013-structural-hardening.md#ingestion-flow--d6--d7) | [CLAUDE.md HR5](../rfcs/013-structural-hardening.md#hard-rule-constraints-claudemd--binding)
  - [x] <a id="33-unit-tests-d6-d7"></a>3.3 Write unit tests for D6 and D7 ([D6](../rfcs/013-structural-hardening.md#d6--iss-34-raise-on-missing-non-latin-tessdata-instead-of-silent-drop), [D7](../rfcs/013-structural-hardening.md#d7--iss-36-deduplicate-garble-detection-into-one-shared-function))

    - **[Design Property 3](../designs/design-rfc013-structural-hardening.md#property-3-non-latin-tessdata-raise) — Non-Latin tessdata raise**: in `test_converters_contract.py`, assert `TessdataUnavailableError` raises on `ensure_tessdata(["ara"])` when the `ara` prefix set/file is absent and download is off (currently only the silent-drop path is tested), per [RFC Test Strategy: D6](../rfcs/013-structural-hardening.md#test-strategy)
    - **[Design Property 4](../designs/design-rfc013-structural-hardening.md#property-4-unified-garble-detection) — Unified garble detection**: in `test_rfc010_helpers.py`, add a short-numeric-junk parametrized case (≤500 char, >60% digit) run through both `_tree_is_garbled` and `_flat_text_is_garbled`, asserting the two agree, per [RFC Test Strategy: D7](../rfcs/013-structural-hardening.md#test-strategy)
    - **Validates:** [Design Property 3](../designs/design-rfc013-structural-hardening.md#property-3-non-latin-tessdata-raise) | [Design Property 4](../designs/design-rfc013-structural-hardening.md#property-4-unified-garble-detection) | [RFC-013 D6](../rfcs/013-structural-hardening.md#d6--iss-34-raise-on-missing-non-latin-tessdata-instead-of-silent-drop) | [RFC-013 D7](../rfcs/013-structural-hardening.md#d7--iss-36-deduplicate-garble-detection-into-one-shared-function) | [RFC Test Strategy](../rfcs/013-structural-hardening.md#test-strategy)
  - [x] <a id="34-checkpoint--batch-2"></a>3.4 Checkpoint — Batch 2

    - Run `uv run pytest` — all tests pass including [Batch 0](#1-batch-0--close-resolved-issues-d1-d3) + [Batch 1](#2-batch-1--independent-code-fixes-d4-d5) + Batch 2
    - Verify [Design Property 3](../designs/design-rfc013-structural-hardening.md#property-3-non-latin-tessdata-raise), [Design Property 4](../designs/design-rfc013-structural-hardening.md#property-4-unified-garble-detection) green
    - Confirm `validate_tree()` gate is tightened, not loosened, per [HR5](../rfcs/013-structural-hardening.md#hard-rule-constraints-claudemd--binding)
    - Confirm `ara.traineddata` pre-bake infra item is scheduled/tracked before [Task 3.1](#31-tessdata-unavailable-error-d6) reaches production, per [RFC-013 Risk 1](../rfcs/013-structural-hardening.md#risks)
    - Schedule the full corpus re-validation follow-up task (operational, gated on this RFC merging) per [RFC-013 Risk 2](../rfcs/013-structural-hardening.md#risks) — do NOT declare D7/ISS-36 fully done until it completes
    - Ask user if questions arise before proceeding
- [x] <a id="4-final-checkpoint"></a>4. Final Checkpoint

  - Run `uv run pytest` — full test suite passes
  - Verify all [4 correctness properties](../designs/design-rfc013-structural-hardening.md#correctness-properties) green:
    - [Property 1](../designs/design-rfc013-structural-hardening.md#property-1-bounded-concurrency-minio-fetch): Bounded-concurrency MinIO fetch ([D4](../rfcs/013-structural-hardening.md#d4--iss-05-bounded-concurrency-minio-fetch-for-list_processed_docs))
    - [Property 2](../designs/design-rfc013-structural-hardening.md#property-2-shared-page-hit-extraction): Shared page-hit extraction ([D5](../rfcs/013-structural-hardening.md#d5--iss-44-extract-shared-page-hit-helper))
    - [Property 3](../designs/design-rfc013-structural-hardening.md#property-3-non-latin-tessdata-raise): Non-Latin tessdata raise ([D6](../rfcs/013-structural-hardening.md#d6--iss-34-raise-on-missing-non-latin-tessdata-instead-of-silent-drop))
    - [Property 4](../designs/design-rfc013-structural-hardening.md#property-4-unified-garble-detection): Unified garble detection ([D7](../rfcs/013-structural-hardening.md#d7--iss-36-deduplicate-garble-detection-into-one-shared-function))
  - Confirm ISS-08, ISS-18, ISS-19 marked resolved in audit tracker ([Batch 0](#1-batch-0--close-resolved-issues-d1-d3))
  - Confirm no AGPL/pymupdf surface touched anywhere in the batch ([HR4](../rfcs/013-structural-hardening.md#hard-rule-constraints-claudemd--binding))
  - Confirm `validate_tree()` gate behavior is unchanged-or-tightened, never loosened ([HR5](../rfcs/013-structural-hardening.md#hard-rule-constraints-claudemd--binding))
  - Confirm the `ara.traineddata` pre-bake infra item and the full corpus re-validation follow-up are both tracked as open operational tasks per [RFC-013 Risks](../rfcs/013-structural-hardening.md#risks)
  - Ask user for review before committing

## Notes

- [D6 (ISS-34)](../rfcs/013-structural-hardening.md#d6--iss-34-raise-on-missing-non-latin-tessdata-instead-of-silent-drop) changes a silent-degrade path into a raise — any deployment currently relying (even accidentally) on eng/deu fallback for non-Latin OCR will start seeing `low_quality_tree` errors until `ara.traineddata` (or the relevant script's tessdata) is pre-baked. Per [RFC-013 Risk 1](../rfcs/013-structural-hardening.md#risks), sequence the tessdata pre-bake infra item alongside [Task 3.1](#31-tessdata-unavailable-error-d6), not after.
- [D7 (ISS-36)](../rfcs/013-structural-hardening.md#d7--iss-36-deduplicate-garble-detection-into-one-shared-function) requires a full corpus re-validation pass before close-out — per [RFC-013 Risk 2](../rfcs/013-structural-hardening.md#risks), budget this as a distinct operational step; do not assume "tests pass" is sufficient sign-off given the prior GHV-TKV-Tarif false-positive history.
- [Batch 0](#1-batch-0--close-resolved-issues-d1-d3) (D1-D3) is verification-only — no code changes, per [RFC-013 D1-D3](../rfcs/013-structural-hardening.md#d1-d3--iss-08-iss-18-iss-19-no-code-change-close-as-resolved).
- [D4 (ISS-05)](../rfcs/013-structural-hardening.md#d4--iss-05-bounded-concurrency-minio-fetch-for-list_processed_docs) ships only the bounded-concurrency interim (Approach C); the registry-only long-term fix (Approach B) is explicitly out of scope for this RFC per [RFC-013 What This RFC Does NOT Cover](../rfcs/013-structural-hardening.md#what-this-rfc-does-not-cover).
- [D7 (ISS-36)](../rfcs/013-structural-hardening.md#d7--iss-36-deduplicate-garble-detection-into-one-shared-function) does NOT introduce a new sub-500-char ratio threshold — that heuristic change is explicitly out of scope due to false-positive risk, per [RFC-013 What This RFC Does NOT Cover](../rfcs/013-structural-hardening.md#what-this-rfc-does-not-cover).
- [HR5](../rfcs/013-structural-hardening.md#hard-rule-constraints-claudemd--binding) applies to both [D6](#31-tessdata-unavailable-error-d6) and [D7](#32-deduplicate-garble-detection-d7): neither may loosen the `validate_tree()` gate.
- [HR4](../rfcs/013-structural-hardening.md#hard-rule-constraints-claudemd--binding) applies across the whole batch: none of D4-D7 touch the AGPL/pymupdf surface.

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "name": "Batch 0 — Close resolved issues",
      "tasks": ["1.1"],
      "depends_on": [],
      "notes": "D1-D3 are verification-only, no code dependencies"
    },
    {
      "id": 1,
      "name": "Batch 0 — Checkpoint",
      "tasks": ["1.2"],
      "depends_on": ["1.1"],
      "notes": "Confirms audit tracker updated correctly"
    },
    {
      "id": 2,
      "name": "Batch 1 — Independent code fixes (parallel)",
      "tasks": ["2.1", "2.2"],
      "depends_on": [],
      "notes": "D4 (storage.py) and D5 (helpers.py/tools/documents.py/client.py) touch disjoint code paths — independent per RFC-013 Implementation Plan"
    },
    {
      "id": 3,
      "name": "Batch 1 — Tests + Checkpoint",
      "tasks": ["2.3", "2.4"],
      "depends_on": ["2.1", "2.2"],
      "notes": "Tests validate both D4 and D5 fixes"
    },
    {
      "id": 4,
      "name": "Batch 2 — Corpus-quality fixes (parallel)",
      "tasks": ["3.1", "3.2"],
      "depends_on": [],
      "notes": "D6 (converters.py) and D7 (helpers.py) touch disjoint functions on the garbling-detection path — independent, but both gated by HR5"
    },
    {
      "id": 5,
      "name": "Batch 2 — Tests + Checkpoint",
      "tasks": ["3.3", "3.4"],
      "depends_on": ["3.1", "3.2"],
      "notes": "Tests validate D6 and D7; checkpoint schedules the ara.traineddata pre-bake and corpus re-validation follow-ups"
    },
    {
      "id": 6,
      "name": "Final Checkpoint",
      "tasks": ["4"],
      "depends_on": ["1.2", "2.4", "3.4"],
      "notes": "Full suite + all 4 correctness properties + HR4/HR5 compliance confirmed before commit"
    }
  ]
}
```
