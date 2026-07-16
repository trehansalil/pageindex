<!-- Space: CITRA -->
<!-- Title: Implementation Plan: Query-Path Performance & Input Hardening -->
<!-- Folder: Tasks -->
<!-- Confluence-Page-ID: 5093720065 -->
<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5093720065/Implementation+Plan+Query-Path+Performance+Input+Hardening -->

# Implementation Plan: Query-Path Performance & Input Hardening

## Traceability

| Artifact                      | Reference                                                                                                                 |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Governing RFC(s)              | [RFC-009: Query-Path Performance &amp; Input Hardening](../rfcs/009-query-path-performance-input-hardening.md)             |
| Design Document               | [Design: Query-Path Performance &amp; Input Hardening](../designs/design-rfc009-query-path-performance-input-hardening.md) |
| PRD / Requirements            | `PRD.md`                                                                                                                |
| Hard Rules                    | [CLAUDE.md HR1 + HR2 + HR5](../rfcs/009-query-path-performance-input-hardening.md#hard-rule-constraints-claudemd--binding) |
| RFC Implementation Order      | [RFC-009 Implementation Plan](../rfcs/009-query-path-performance-input-hardening.md#implementation-plan)                   |
| RFC Test Strategy             | [RFC-009 Test Strategy](../rfcs/009-query-path-performance-input-hardening.md#test-strategy)                               |
| Design Correctness Properties | [Design Correctness Properties](../designs/design-rfc009-query-path-performance-input-hardening.md#correctness-properties) |
| Design Testing Strategy       | [Design Testing Strategy](../designs/design-rfc009-query-path-performance-input-hardening.md#testing-strategy)             |

## Overview

Implements seven performance and input-hardening fixes across the PageIndex query path and upload pipeline, organized into five risk-ordered batches per [RFC-009 Implementation Plan](../rfcs/009-query-path-performance-input-hardening.md#implementation-plan). The plan proceeds from immediate O(N) error-path removal ([Batch 0](../rfcs/009-query-path-performance-input-hardening.md#batch-0--immediate-no-dependencies)) through input validation ([Batch 1](../rfcs/009-query-path-performance-input-hardening.md#batch-1--short-term-no-cross-rfc-dependencies)), pagination and sidecar enrichment ([Batch 2](../rfcs/009-query-path-performance-input-hardening.md#batch-2--pagination-fix-depends-on-batch-1--iss-07rfc-008)), Docker pre-bake ([Batch 3](../rfcs/009-query-path-performance-input-hardening.md#batch-3--docker-pre-bake-ops-change)), and MinIO fallback removal ([Batch 4](../rfcs/009-query-path-performance-input-hardening.md#batch-4--registry-only-listing-depends-on-rfc-007-iss-03--rfc-006-d3-backfill)), validating each batch with unit tests tied to the design document's [7 correctness properties](../designs/design-rfc009-query-path-performance-input-hardening.md#correctness-properties) before advancing. Stack: Python 3.12, FastMCP, Redis, MinIO, Postgres (RFC-006), arq, Prometheus.

## Tasks

- [X] <a id="1-batch-0--immediate-d1"></a>1. Batch 0 — Immediate ([D1](../rfcs/009-query-path-performance-input-hardening.md#d1--remove-on-listing-from-error-paths-iss-21--immediate))

  *[RFC-009 Batch 0](../rfcs/009-query-path-performance-input-hardening.md#batch-0--immediate-no-dependencies): "Remove O(N) listing from error paths — pure code removal, no behavioral change for well-behaved clients"*

  - [X] <a id="11-remove-on-listing-from-error-paths-d1"></a>1.1 Remove O(N) listing from error paths ([D1](../rfcs/009-query-path-performance-input-hardening.md#d1--remove-on-listing-from-error-paths-iss-21--immediate))

    - Remove `list_processed_docs()` calls from error paths in three MCP tools:
      - `get_document` (`documents.py:195`) — remove `available` array construction from not-found response
      - `get_document_structure` (`documents.py:258`) — remove `available` array construction from not-found response
      - `get_page_content` (`documents.py:300`) — remove `available` array construction from not-found response
    - Return simple error JSON: `{"error": "Document not found: {doc_id}"}` without the `available` key
    - _Requirements:_ [RFC-009 D1 (ISS-21)](../rfcs/009-query-path-performance-input-hardening.md#d1--remove-on-listing-from-error-paths-iss-21--immediate) | [Design Property 1](../designs/design-rfc009-query-path-performance-input-hardening.md#property-1-no-on-listing-on-error-paths) | [Design Service: tools/documents.py](../designs/design-rfc009-query-path-performance-input-hardening.md#1-toolsdocumentspy) | [Design Sequence: Error Path Flow](../designs/design-rfc009-query-path-performance-input-hardening.md#error-path-flow--d1)
  - [X] <a id="12-error-path-regression-tests-d1"></a>1.2 Write error-path regression tests ([D1](../rfcs/009-query-path-performance-input-hardening.md#d1--remove-on-listing-from-error-paths-iss-21--immediate))

    - **[Property 1](../designs/design-rfc009-query-path-performance-input-hardening.md#property-1-no-on-listing-on-error-paths) — No O(N) listing on error paths**: Test each of the three MCP tools with an invalid `doc_id`:
      - Test: `test_get_document_not_found_no_listing` — call `get_document("nonexistent-id")`, assert response is `{"error": "Document not found: nonexistent-id"}` with no `available` key
      - Test: `test_get_document_structure_not_found_no_listing` — same for `get_document_structure`
      - Test: `test_get_page_content_not_found_no_listing` — same for `get_page_content`
      - Mock `list_processed_docs` and assert zero calls across all three tests
    - **Validates:** [Design Property 1](../designs/design-rfc009-query-path-performance-input-hardening.md#property-1-no-on-listing-on-error-paths) | [RFC-009 D1 (ISS-21)](../rfcs/009-query-path-performance-input-hardening.md#d1--remove-on-listing-from-error-paths-iss-21--immediate) | [RFC Test Strategy: ISS-21 D1](../rfcs/009-query-path-performance-input-hardening.md#iss-21-d1--error-path-regression)
  - [X] <a id="13-checkpoint--batch-0"></a>1.3 Checkpoint — Batch 0

    - Run `uv run pytest` — all existing tests + new Batch 0 tests pass
    - Verify [Property 1](../designs/design-rfc009-query-path-performance-input-hardening.md#property-1-no-on-listing-on-error-paths) green
    - Confirm no behavioral change for well-behaved clients (error response format simplified, not changed in breaking ways)
    - Ask user if questions arise before proceeding
- [X] <a id="2-batch-1--input-hardening-d4-d5"></a>2. Batch 1 — Input Hardening ([D4](../rfcs/009-query-path-performance-input-hardening.md#d4--chunked-upload-with-size-limit-iss-15), [D5](../rfcs/009-query-path-performance-input-hardening.md#d5--tessdata-download-hardening-iss-14-immediate))

  *[RFC-009 Batch 1](../rfcs/009-query-path-performance-input-hardening.md#batch-1--short-term-no-cross-rfc-dependencies): "No cross-RFC dependencies — upload size limit and tessdata download hardening"*

  - [X] <a id="21-chunked-upload-with-size-limit-d4"></a>2.1 Implement chunked upload with size limit ([D4](../rfcs/009-query-path-performance-input-hardening.md#d4--chunked-upload-with-size-limit-iss-15))

    - Replace unbounded `file.read()` in `upload_app.py:89` with chunked read (1 MB chunks)
    - Add `MAX_UPLOAD_SIZE_MB` env var to `settings.py` (default `100`)
    - Abort with HTTP 413 if total bytes exceed `MAX_UPLOAD_SIZE_MB` limit
    - Reassemble chunks with `b"".join(chunks)` after successful size check
    - _Requirements:_ [RFC-009 D4 (ISS-15)](../rfcs/009-query-path-performance-input-hardening.md#d4--chunked-upload-with-size-limit-iss-15) | [Design Property 4](../designs/design-rfc009-query-path-performance-input-hardening.md#property-4-upload-size-bounded) | [Design Service: upload_app.py](../designs/design-rfc009-query-path-performance-input-hardening.md#3-upload_apppy) | [Design Service: settings.py](../designs/design-rfc009-query-path-performance-input-hardening.md#5-settingspy) | [Design Sequence: Upload Flow](../designs/design-rfc009-query-path-performance-input-hardening.md#upload-flow--d4)
  - [X] <a id="22-tessdata-download-hardening-d5"></a>2.2 Harden tessdata download ([D5](../rfcs/009-query-path-performance-input-hardening.md#d5--tessdata-download-hardening-iss-14-immediate))

    - Replace `urllib.request.urlretrieve` in `converters.py:755-768` with `urllib.request.urlopen(url, timeout=30)` plus chunked read (1 MB chunks)
    - Add 100 MB size cap — abort and clean up partial file (`os.unlink(dest)`) if exceeded
    - On any failure (timeout, size exceeded, network error), ensure partial file is cleaned up
    - _Requirements:_ [RFC-009 D5 (ISS-14)](../rfcs/009-query-path-performance-input-hardening.md#d5--tessdata-download-hardening-iss-14-immediate) | [Design Property 5](../designs/design-rfc009-query-path-performance-input-hardening.md#property-5-tessdata-download-bounded) | [Design Service: converters.py](../designs/design-rfc009-query-path-performance-input-hardening.md#4-converterspy) | [Design Sequence: Tessdata Download Flow](../designs/design-rfc009-query-path-performance-input-hardening.md#tessdata-download-flow--d5)
  - [X] <a id="23-input-hardening-tests-d4-d5"></a>2.3 Write input hardening tests ([D4](../rfcs/009-query-path-performance-input-hardening.md#d4--chunked-upload-with-size-limit-iss-15), [D5](../rfcs/009-query-path-performance-input-hardening.md#d5--tessdata-download-hardening-iss-14-immediate))

    - **[Property 4](../designs/design-rfc009-query-path-performance-input-hardening.md#property-4-upload-size-bounded) — Upload size bounded**:
      - Test: `test_upload_exceeds_max_size_returns_413` — POST a file exceeding `MAX_UPLOAD_SIZE_MB`, assert HTTP 413 response
      - Test: `test_upload_under_limit_succeeds` — POST a file under the limit, assert HTTP 200 (existing behavior preserved)
      - Test: `test_upload_at_boundary_succeeds` — POST a file exactly at 100 MB, assert success; 100 MB + 1 byte fails with 413
    - **Validates:** [Design Property 4](../designs/design-rfc009-query-path-performance-input-hardening.md#property-4-upload-size-bounded) | [RFC-009 D4 (ISS-15)](../rfcs/009-query-path-performance-input-hardening.md#d4--chunked-upload-with-size-limit-iss-15) | [RFC Test Strategy: ISS-15 D4](../rfcs/009-query-path-performance-input-hardening.md#iss-15-d4--upload-size-limit)
    - **[Property 5](../designs/design-rfc009-query-path-performance-input-hardening.md#property-5-tessdata-download-bounded) — Tessdata download bounded**:
      - Test: `test_tessdata_oversize_cleanup` — mock `urlopen` to return data exceeding 100 MB cap, assert `_try_download_tessdata` raises/returns False and partial file is cleaned up
      - Test: `test_tessdata_timeout` — mock `urlopen` to hang, assert timeout fires within 30s
      - Test: `test_tessdata_valid_download` — mock `urlopen` to return valid data under cap, assert file is written correctly
    - **Validates:** [Design Property 5](../designs/design-rfc009-query-path-performance-input-hardening.md#property-5-tessdata-download-bounded) | [RFC-009 D5 (ISS-14)](../rfcs/009-query-path-performance-input-hardening.md#d5--tessdata-download-hardening-iss-14-immediate) | [RFC Test Strategy: ISS-14 D5](../rfcs/009-query-path-performance-input-hardening.md#iss-14-d5--tessdata-hardening)
  - [X] <a id="24-checkpoint--batch-1"></a>2.4 Checkpoint — Batch 1

    - Run `uv run pytest` — all tests pass including [Batch 0](#1-batch-0--immediate-d1) + Batch 1
    - Verify [Property 4](../designs/design-rfc009-query-path-performance-input-hardening.md#property-4-upload-size-bounded), [Property 5](../designs/design-rfc009-query-path-performance-input-hardening.md#property-5-tessdata-download-bounded) green
    - Ask user if questions arise before proceeding
- [X] <a id="3-batch-2--pagination-and-sidecar-d2-d3"></a>3. Batch 2 — Pagination & Sidecar ([D2](../rfcs/009-query-path-performance-input-hardening.md#d2--store-node_count-in-metajson-sidecar-at-save-time-iss-05-short-term), [D3](../rfcs/009-query-path-performance-input-hardening.md#d3--server-side-pagination-for-recent_documents-iss-06))

  *[RFC-009 Batch 2](../rfcs/009-query-path-performance-input-hardening.md#batch-2--pagination-fix-depends-on-batch-1--iss-07rfc-008): "Pagination fix — depends on D2 sidecar enrichment + ISS-07/RFC-008 Redis singleton"*

  - [X] <a id="31-store-node-count-in-metajson-sidecar-d2"></a>3.1 Store node_count in .meta.json sidecar ([D2](../rfcs/009-query-path-performance-input-hardening.md#d2--store-node_count-in-metajson-sidecar-at-save-time-iss-05-short-term))

    - Compute `node_count` in `save_doc_meta()` (`storage.py`) at ingestion time by counting nodes in the tree structure
    - Persist `node_count` as a field in the `.meta.json` sidecar alongside existing metadata
    - Add `node_count INTEGER` column to the registry `documents` table
    - Populate `node_count` via dual-write in the registry write path
    - Ensure backward compatibility: existing `.meta.json` sidecars without `node_count` must not break `list_processed_docs` (field defaults to `None`/`0`)
    - _Requirements:_ [RFC-009 D2 (ISS-05)](../rfcs/009-query-path-performance-input-hardening.md#d2--store-node_count-in-metajson-sidecar-at-save-time-iss-05-short-term) | [Design Property 2](../designs/design-rfc009-query-path-performance-input-hardening.md#property-2-node-count-persisted-at-save-time) | [Design Service: storage.py](../designs/design-rfc009-query-path-performance-input-hardening.md#2-storagepy) | [Design Service: registry.py](../designs/design-rfc009-query-path-performance-input-hardening.md#6-registrypy) | [Design Data Model: Meta Sidecar](../designs/design-rfc009-query-path-performance-input-hardening.md#meta-sidecar--d2) | [Design Data Model: Registry Documents Table](../designs/design-rfc009-query-path-performance-input-hardening.md#registry-documents-table--d2) | [Design Sequence: Recent Documents Flow](../designs/design-rfc009-query-path-performance-input-hardening.md#recent-documents-flow--d2--d3) | [CLAUDE.md HR5](../rfcs/009-query-path-performance-input-hardening.md#hard-rule-constraints-claudemd--binding)
  - [X] <a id="32-server-side-pagination-d3"></a>3.2 Implement server-side pagination ([D3](../rfcs/009-query-path-performance-input-hardening.md#d3--server-side-pagination-for-recent_documents-iss-06))

    - Pass `limit=page_size, offset=(page-1)*page_size` directly to `list_docs()` on the registry path instead of `limit=100_000, offset=0`
    - Read `node_count` from listing metadata instead of deserializing full trees via `get_doc()`
    - On MinIO fallback path, retain existing fetch-all-then-slice behavior (fallback is already degraded; it goes away entirely with [D6](#51-remove-minio-fallback-d6))
    - Add a `count()` query or use the registry's existing count mechanism to preserve `DOCUMENTS_TOTAL` gauge accuracy (currently uses `len(docs)` which would become `page_size` with pagination)
    - _Requirements:_ [RFC-009 D3 (ISS-06)](../rfcs/009-query-path-performance-input-hardening.md#d3--server-side-pagination-for-recent_documents-iss-06) | [Design Property 3](../designs/design-rfc009-query-path-performance-input-hardening.md#property-3-server-side-pagination) | [Design Service: tools/documents.py](../designs/design-rfc009-query-path-performance-input-hardening.md#1-toolsdocumentspy) | [Design Sequence: Recent Documents Flow](../designs/design-rfc009-query-path-performance-input-hardening.md#recent-documents-flow--d2--d3)
  - [X] <a id="33-pagination-and-sidecar-tests-d2-d3"></a>3.3 Write pagination and sidecar tests ([D2](../rfcs/009-query-path-performance-input-hardening.md#d2--store-node_count-in-metajson-sidecar-at-save-time-iss-05-short-term), [D3](../rfcs/009-query-path-performance-input-hardening.md#d3--server-side-pagination-for-recent_documents-iss-06))

    - **[Property 2](../designs/design-rfc009-query-path-performance-input-hardening.md#property-2-node-count-persisted-at-save-time) — Node count persisted at save time**:
      - Test: `test_save_doc_meta_produces_node_count` — call `save_doc_meta()` with a tree structure, read back `.meta.json`, assert `node_count` field is present and correct
      - Test: `test_backward_compat_missing_node_count` — existing `.meta.json` without `node_count` must not break `list_processed_docs` (field defaults to `None`/`0`)
    - **Validates:** [Design Property 2](../designs/design-rfc009-query-path-performance-input-hardening.md#property-2-node-count-persisted-at-save-time) | [RFC-009 D2 (ISS-05)](../rfcs/009-query-path-performance-input-hardening.md#d2--store-node_count-in-metajson-sidecar-at-save-time-iss-05-short-term) | [RFC Test Strategy: ISS-05A D2](../rfcs/009-query-path-performance-input-hardening.md#iss-05a-d2--sidecar-enrichment)
    - **[Property 3](../designs/design-rfc009-query-path-performance-input-hardening.md#property-3-server-side-pagination) — Server-side pagination**:
      - Test: `test_list_docs_called_with_limit_offset` — mock `list_docs` and call `recent_documents(page=2, page_size=5)`, assert `list_docs` was called with `limit=5, offset=5`, NOT `limit=100_000`
      - Test: `test_get_doc_not_called_for_node_count` — assert `get_doc` is NOT called for node count enrichment (reads from metadata)
      - Test: `test_pagination_integration_20_docs` — integration test with registry: insert 20 docs, request page 2 size 5, verify exactly 5 results with correct offset
    - **Validates:** [Design Property 3](../designs/design-rfc009-query-path-performance-input-hardening.md#property-3-server-side-pagination) | [RFC-009 D3 (ISS-06)](../rfcs/009-query-path-performance-input-hardening.md#d3--server-side-pagination-for-recent_documents-iss-06) | [RFC Test Strategy: ISS-06 D3](../rfcs/009-query-path-performance-input-hardening.md#iss-06-d3--server-side-pagination)
  - [X] <a id="34-checkpoint--batch-2"></a>3.4 Checkpoint — Batch 2

    - Run `uv run pytest` — all tests pass including [Batch 0](#1-batch-0--immediate-d1) + [Batch 1](#2-batch-1--input-hardening-d4-d5) + Batch 2
    - Verify [Property 2](../designs/design-rfc009-query-path-performance-input-hardening.md#property-2-node-count-persisted-at-save-time), [Property 3](../designs/design-rfc009-query-path-performance-input-hardening.md#property-3-server-side-pagination) green
    - Confirm `DOCUMENTS_TOTAL` gauge still reports accurate corpus count with pagination
    - Ask user if questions arise before proceeding
- [X] <a id="4-batch-3--docker-pre-bake-d5b"></a>4. Batch 3 — Docker Pre-bake ([D5b](../rfcs/009-query-path-performance-input-hardening.md#d5b--pre-bake-tessdata-in-docker-image-iss-14-production))

  *[RFC-009 Batch 3](../rfcs/009-query-path-performance-input-hardening.md#batch-3--docker-pre-bake-ops-change): "Ops change only — pre-bake tessdata in Docker image to eliminate runtime downloads"*

  - [X] <a id="41-pre-bake-tessdata-in-dockerfile-d5b"></a>4.1 Pre-bake tessdata in Dockerfile ([D5b](../rfcs/009-query-path-performance-input-hardening.md#d5b--pre-bake-tessdata-in-docker-image-iss-14-production))

    - Add `RUN curl -fsSL -o ...` lines to the Dockerfile for all expected languages:
      - `deu.traineddata` — German
      - `eng.traineddata` — English
      - `ara.traineddata` — Arabic
    - Formalizes `.tessdata/` as the production-only path; runtime download ([D5](#22-tessdata-download-hardening-d5)) remains as dev/local fallback
    - No code changes required — this is purely an ops/Dockerfile change
    - _Requirements:_ [RFC-009 D5b (ISS-14)](../rfcs/009-query-path-performance-input-hardening.md#d5b--pre-bake-tessdata-in-docker-image-iss-14-production) | [Design Property 6](../designs/design-rfc009-query-path-performance-input-hardening.md#property-6-tessdata-pre-baked-in-production)
  - [X] <a id="42-checkpoint--batch-3"></a>4.2 Checkpoint — Batch 3

    - Run `docker build` — image builds successfully
    - Verify tessdata files are present in the image at expected paths (`deu.traineddata`, `eng.traineddata`, `ara.traineddata`)
    - Verify [Property 6](../designs/design-rfc009-query-path-performance-input-hardening.md#property-6-tessdata-pre-baked-in-production) green
    - Ask user if questions arise before proceeding
- [X] <a id="5-batch-4--registry-only-listing-d6"></a>5. Batch 4 — Registry-Only Listing ([D6](../rfcs/009-query-path-performance-input-hardening.md#d6--remove-minio-fallback-from-_list_docs_with_fallback-iss-05-long-term))

  *[RFC-009 Batch 4](../rfcs/009-query-path-performance-input-hardening.md#batch-4--registry-only-listing-depends-on-rfc-007-iss-03--rfc-006-d3-backfill): "Registry-only listing — depends on RFC-007 ISS-03 + RFC-006 D3 backfill completion"*

  - [X] <a id="51-remove-minio-fallback-d6"></a>5.1 Remove MinIO fallback from _list_docs_with_fallback ([D6](../rfcs/009-query-path-performance-input-hardening.md#d6--remove-minio-fallback-from-_list_docs_with_fallback-iss-05-long-term))

    - Remove all 4 MinIO fallback codepaths from `_list_docs_with_fallback()` (`documents.py:39-80`):
      - Backfill incomplete fallback
      - Postgres error fallback
      - Redis error checking registry flag fallback
      - Registry query returning `None` fallback
    - Return error on Postgres failure instead of degraded O(N) MinIO listing
    - Gate deployment on `pageindex:registry:complete` Redis flag being set (confirms RFC-006 D3 backfill is complete)
    - _Requirements:_ [RFC-009 D6 (ISS-05)](../rfcs/009-query-path-performance-input-hardening.md#d6--remove-minio-fallback-from-_list_docs_with_fallback-iss-05-long-term) | [Design Property 7](../designs/design-rfc009-query-path-performance-input-hardening.md#property-7-no-minio-fallback-on-registry-path) | [Design Service: tools/documents.py](../designs/design-rfc009-query-path-performance-input-hardening.md#1-toolsdocumentspy)
  - [X] <a id="52-registry-only-tests-d6"></a>5.2 Write registry-only listing tests ([D6](../rfcs/009-query-path-performance-input-hardening.md#d6--remove-minio-fallback-from-_list_docs_with_fallback-iss-05-long-term))

    - **[Property 7](../designs/design-rfc009-query-path-performance-input-hardening.md#property-7-no-minio-fallback-on-registry-path) — No MinIO fallback on registry path**:
      - Test: `test_postgres_down_returns_error` — Postgres unavailable returns error response, NOT an O(N) MinIO listing
      - Test: `test_registry_path_returns_correct_results` — registry path returns correct document listing via single SQL query
      - Test: `test_no_list_processed_docs_calls` — mock `list_processed_docs` and assert zero calls across all listing paths
    - **Validates:** [Design Property 7](../designs/design-rfc009-query-path-performance-input-hardening.md#property-7-no-minio-fallback-on-registry-path) | [RFC-009 D6 (ISS-05)](../rfcs/009-query-path-performance-input-hardening.md#d6--remove-minio-fallback-from-_list_docs_with_fallback-iss-05-long-term) | [RFC Test Strategy: ISS-21 + ISS-05 combined](../rfcs/009-query-path-performance-input-hardening.md#iss-21--iss-05-combined--dos-resistance)
  - [X] <a id="53-checkpoint--batch-4"></a>5.3 Checkpoint — Batch 4

    - Run `uv run pytest` — all tests pass including [Batch 0](#1-batch-0--immediate-d1) + [Batch 1](#2-batch-1--input-hardening-d4-d5) + [Batch 2](#3-batch-2--pagination-and-sidecar-d2-d3) + [Batch 3](#4-batch-3--docker-pre-bake-d5b) + Batch 4
    - Verify [Property 7](../designs/design-rfc009-query-path-performance-input-hardening.md#property-7-no-minio-fallback-on-registry-path) green
    - Confirm no `list_processed_docs` calls remain in any listing or error path
    - Ask user if questions arise before proceeding
- [X] <a id="6-final-checkpoint"></a>6. Final Checkpoint

  - Run `uv run pytest` — full test suite passes
  - Verify all [7 correctness properties](../designs/design-rfc009-query-path-performance-input-hardening.md#correctness-properties) green:
    - [P1](../designs/design-rfc009-query-path-performance-input-hardening.md#property-1-no-on-listing-on-error-paths): No O(N) listing on error paths ([D1](../rfcs/009-query-path-performance-input-hardening.md#d1--remove-on-listing-from-error-paths-iss-21--immediate))
    - [P2](../designs/design-rfc009-query-path-performance-input-hardening.md#property-2-node-count-persisted-at-save-time): Node count persisted at save time ([D2](../rfcs/009-query-path-performance-input-hardening.md#d2--store-node_count-in-metajson-sidecar-at-save-time-iss-05-short-term))
    - [P3](../designs/design-rfc009-query-path-performance-input-hardening.md#property-3-server-side-pagination): Server-side pagination ([D3](../rfcs/009-query-path-performance-input-hardening.md#d3--server-side-pagination-for-recent_documents-iss-06))
    - [P4](../designs/design-rfc009-query-path-performance-input-hardening.md#property-4-upload-size-bounded): Upload size bounded ([D4](../rfcs/009-query-path-performance-input-hardening.md#d4--chunked-upload-with-size-limit-iss-15))
    - [P5](../designs/design-rfc009-query-path-performance-input-hardening.md#property-5-tessdata-download-bounded): Tessdata download bounded ([D5](../rfcs/009-query-path-performance-input-hardening.md#d5--tessdata-download-hardening-iss-14-immediate))
    - [P6](../designs/design-rfc009-query-path-performance-input-hardening.md#property-6-tessdata-pre-baked-in-production): Tessdata pre-baked in production ([D5b](../rfcs/009-query-path-performance-input-hardening.md#d5b--pre-bake-tessdata-in-docker-image-iss-14-production))
    - [P7](../designs/design-rfc009-query-path-performance-input-hardening.md#property-7-no-minio-fallback-on-registry-path): No MinIO fallback on registry path ([D6](../rfcs/009-query-path-performance-input-hardening.md#d6--remove-minio-fallback-from-_list_docs_with_fallback-iss-05-long-term))
  - Run 3 consecutive full test suite executions to confirm zero flaky failures
  - Manual DoS resistance load test: send 100 concurrent requests with invalid `doc_id` values, verify 0 MinIO GETs per [RFC Test Strategy: ISS-21 + ISS-05 combined](../rfcs/009-query-path-performance-input-hardening.md#iss-21--iss-05-combined--dos-resistance)
  - Ask user for review before committing

## Notes

- [D6](../rfcs/009-query-path-performance-input-hardening.md#d6--remove-minio-fallback-from-_list_docs_with_fallback-iss-05-long-term) is gated on external dependencies: RFC-007 ISS-03 (registry dual-write correctness) and RFC-006 D3 (backfill completion). Do not deploy [Task 5.1](#51-remove-minio-fallback-d6) until both are confirmed complete and `pageindex:registry:complete` Redis flag is set per [RFC-009 Risk 6](../rfcs/009-query-path-performance-input-hardening.md#risks)
- [D3](../rfcs/009-query-path-performance-input-hardening.md#d3--server-side-pagination-for-recent_documents-iss-06) depends on [D2](../rfcs/009-query-path-performance-input-hardening.md#d2--store-node_count-in-metajson-sidecar-at-save-time-iss-05-short-term) (sidecar `node_count` to eliminate tree deserialization) + ISS-07/RFC-008 (Redis singleton to avoid connection churn)
- [D5b](../rfcs/009-query-path-performance-input-hardening.md#d5b--pre-bake-tessdata-in-docker-image-iss-14-production) is ops-only (Dockerfile change), no application code changes required
- [Risk 1](../rfcs/009-query-path-performance-input-hardening.md#risks) ([D1](../rfcs/009-query-path-performance-input-hardening.md#d1--remove-on-listing-from-error-paths-iss-21--immediate) `available` array removal) — check that no client parses the `available` field in error responses; the MCP tool description already directs clients to `recent_documents()` for doc_id discovery
- [Risk 3](../rfcs/009-query-path-performance-input-hardening.md#risks) ([D3](../rfcs/009-query-path-performance-input-hardening.md#d3--server-side-pagination-for-recent_documents-iss-06) pagination breaks `DOCUMENTS_TOTAL` gauge) — [Task 3.2](#32-server-side-pagination-d3) must add a `count()` query to preserve gauge accuracy, since `len(docs)` will return `page_size` instead of corpus count
- [Risk 6](../rfcs/009-query-path-performance-input-hardening.md#risks) ([D6](../rfcs/009-query-path-performance-input-hardening.md#d6--remove-minio-fallback-from-_list_docs_with_fallback-iss-05-long-term) breaking for non-backfilled envs) — [Task 5.1](#51-remove-minio-fallback-d6) gates deployment on the `pageindex:registry:complete` Redis flag; environments without completed backfill will get errors instead of degraded-but-working listings (this is intentional per RFC-009)

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "name": "Batch 0 — Error path fix",
      "tasks": ["1.1"],
      "depends_on": [],
      "notes": "D1 is pure code removal with no dependencies"
    },
    {
      "id": 1,
      "name": "Batch 0 — Tests + Checkpoint",
      "tasks": ["1.2"],
      "depends_on": ["1.1"],
      "notes": "Tests validate the D1 fix from wave 0"
    },
    {
      "id": 2,
      "name": "Batch 1 — Input hardening (parallel)",
      "tasks": ["2.1", "2.2"],
      "depends_on": [],
      "notes": "D4 (upload) and D5 (tessdata) are independent — different files, no cross-dependencies"
    },
    {
      "id": 3,
      "name": "Batch 1 — Tests + Checkpoint",
      "tasks": ["2.3"],
      "depends_on": ["2.1", "2.2"],
      "notes": "Tests validate both D4 and D5 fixes"
    },
    {
      "id": 4,
      "name": "Batch 2 — Sidecar enrichment",
      "tasks": ["3.1"],
      "depends_on": [],
      "notes": "D2 sidecar enrichment can start independently"
    },
    {
      "id": 5,
      "name": "Batch 2 — Server-side pagination",
      "tasks": ["3.2"],
      "depends_on": ["3.1"],
      "notes": "D3 depends on D2 — node_count must be in sidecar before pagination can read it"
    },
    {
      "id": 6,
      "name": "Batch 2 — Tests + Checkpoint",
      "tasks": ["3.3"],
      "depends_on": ["3.2"],
      "notes": "Tests validate both D2 sidecar and D3 pagination"
    },
    {
      "id": 7,
      "name": "Batch 3 — Docker pre-bake",
      "tasks": ["4.1"],
      "depends_on": [],
      "notes": "D5b is an ops-only Dockerfile change — no code dependencies"
    },
    {
      "id": 8,
      "name": "Batch 4 — Registry-only listing",
      "tasks": ["5.1"],
      "depends_on": ["RFC-007 ISS-03", "RFC-006 D3"],
      "notes": "D6 is gated on external dependencies — cannot start until registry is authoritative"
    },
    {
      "id": 9,
      "name": "Batch 4 — Tests + Checkpoint",
      "tasks": ["5.2"],
      "depends_on": ["5.1"],
      "notes": "Tests validate D6 MinIO fallback removal"
    }
  ]
}
```
