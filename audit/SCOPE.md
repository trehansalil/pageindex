<!-- Space: CITRA -->
<!-- Title: Audit: Scope Definition -->
<!-- Parent: PageIndex Docstore Audit -->
<!-- Confluence-Page-ID: 5092474885 -->
<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5092474885/Audit+Scope+Definition -->

# Docstore Audit — Scope Definition (Wave 1)

**Last updated:** 2026-07-15 (added corpus quality scope, updated file inventory for RFC-010 changes)

## 1. What Are "Docstore Files"?

The docstore subsystem is the entire document lifecycle — ingestion, conversion, storage, retrieval, and deletion. It spans the following files:

### Core Pipeline (src/pageindex_mcp/)

| File                     | Role                                                                                                                             |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| `storage.py`           | MinIO object-storage backend: save/load/delete documents, flat docs, metadata sidecars, hash caches, staging uploads             |
| `cache.py`             | Redis caching layer: async/sync Redis clients, document cache get/set/delete, TTL management                                     |
| `worker.py`            | Arq async job worker:`process_document_job` lifecycle, subprocess converter invocation, DLQ, reaper cron                       |
| `converters.py`        | PDF/DOCX/XLSX/PPTX→Markdown conversion: Docling pipeline, pymupdf4llm fallback, heading-depth recovery, OCR escalation          |
| `converters_cli.py`    | CLI entry point for converter subprocess (invoked by worker)                                                                     |
| `helpers.py`           | Tree building/validation:`build_tree`, `validate_tree`, `split_oversized_leaf_nodes`, LLM classification, table extraction |
| `client.py`            | `CustomPageIndexClient` — MCP client with `index()`, `get_document()`, `get_document_structure()`, search, RAG query    |
| `config.py`            | `Settings` (pydantic-settings): Redis, MinIO, LLM, Langfuse, OCR, and scaling env-var configuration                            |
| `server.py`            | FastMCP server registration + Starlette upload app mount (`/upload/files`, `/upload/status/{job_id}`)                        |
| `upload_app.py`        | Starlette upload sub-application: file staging, arq job enqueue, status polling                                                  |
| `registry.py`          | Corpus-scale document registry: PostgreSQL-backed catalog for document metadata at scale (new, uncommitted)                      |
| `registry_backfill.py` | One-shot backfill script: populate registry from existing MinIO processed docs (new, uncommitted)                                |
| `metrics.py`           | Prometheus instrumentation: counters/histograms for MinIO ops, tool calls, uploads, worker jobs                                  |
| `queue_metrics.py`     | Arq queue depth + DLQ metrics collector                                                                                          |
| `memory_admission.py`  | Memory-pressure gate: blocks worker subprocess spawn until RSS is below threshold                                                |
| `auth.py`              | API key validation for upload endpoints                                                                                          |
| `tools/documents.py`   | MCP tool definitions:`get_document`, `search_documents`, `get_document_structure`, `query_documents`, `list_documents` |
| `tools/processing.py`  | MCP tool definitions for processing status queries                                                                               |
| `tools/__init__.py`    | Tool package init                                                                                                                |

### Support Scripts (project root)

| File                     | Role                                                                                    |
| ------------------------ | --------------------------------------------------------------------------------------- |
| `upload.py`            | HTTP upload client: sends files to`/upload/files` endpoint                            |
| `preprocess_client.py` | Batch preprocessing: iterates`doc_store/`, SHA256 dedup, calls `index()` via client |
| `mcp_server.py`        | Dev server entry point (`uv run python mcp_server.py`)                                |

### Configuration & Infrastructure

| File                   | Role                                                 |
| ---------------------- | ---------------------------------------------------- |
| `docker-compose.yml` | Redis, MinIO, arq worker, server service definitions |
| `.env.example`       | Env-var catalog with defaults                        |
| `pyproject.toml`     | Dependencies, extras, tool config                    |
| `gunicorn.conf.py`   | Gunicorn/Uvicorn worker config                       |

### Test Files (tests/)

| File                                                                                          | Covers                                     |
| --------------------------------------------------------------------------------------------- | ------------------------------------------ |
| `test_storage_contract.py`                                                                  | Storage save/load/delete/flat contracts    |
| `test_storage_meta.py`                                                                      | Metadata sidecar writes                    |
| `test_cache.py`, `test_cache_contract.py`                                                 | Redis cache hit/miss/invalidation          |
| `test_worker.py`, `test_worker_contract.py`                                               | Worker job lifecycle                       |
| `test_worker_resiliency.py`                                                                 | Max-jobs, reaper, stale-job cleanup        |
| `test_worker_subprocess.py`                                                                 | OOM, timeout, child-failure error handling |
| `test_upload.py`, `test_upload_contract.py`                                               | Upload staging + enqueue                   |
| `test_staging_e2e.py`                                                                       | MinIO staging round-trip                   |
| `test_converters_contract.py`, `test_converters_footprint.py`, `test_converters_cli.py` | Converter pipeline                         |
| `test_helpers_contract.py`, `test_validate_tree_contract.py`                              | Tree building/validation                   |
| `test_depth_inference.py`, `test_outline_inference.py`                                    | Heading depth recovery                     |
| `test_client.py`, `test_client_contract.py`                                               | MCP client                                 |
| `test_config.py`                                                                            | Settings/env-var parsing                   |
| `test_metrics.py`, `test_queue_metrics.py`                                                | Prometheus instrumentation                 |
| `test_memory_admission.py`                                                                  | Memory gate                                |
| `test_rag_contract.py`, `test_rag_dedup.py`                                               | RAG query deduplication                    |
| `test_registry_contract.py`                                                                 | Registry contracts (new)                   |
| `test_tracing.py`                                                                           | Langfuse/LLM tracing                       |
| `test_read_pdf_outline.py`                                                                  | PDF outline extraction                     |

## 2. System Boundaries

The docstore interacts with:

- **MinIO** — Primary object storage for raw uploads (`uploads/`), processed trees (`processed/*.json`), flat docs (`processed/*.flat.json`), metadata sidecars (`processed/*.meta.json`), hash caches (`processed/_hash_cache.json`), and staging files (`staging/`)
- **Redis** — Dual role: (1) arq job queue bus for async ingestion jobs, (2) document cache with TTL for query-path reads
- **PostgreSQL** — New corpus-scale registry (uncommitted `registry.py`); document metadata catalog
- **LLM layer** — OpenAI-compatible endpoint (configurable via `OPENAI_BASE_URL`/`LLM_PROVIDER`) for content classification and RAG queries; litellm for ingestion-path LLM calls
- **Langfuse** — Observability/tracing for LLM calls (both query and ingestion paths)
- **Docling / pymupdf4llm** — PDF extraction libraries (Docling is primary, pymupdf4llm is AGPL fallback)
- **Tesseract OCR** — OCR escalation for garbled/scanned PDFs (configured via `DOCLING_OCR_LANG`)
- **Prometheus** — Metrics scraping via `/metrics` endpoint
- **MCP protocol** — FastMCP server exposes 5+ query tools to LLM clients
- **HTTP clients** — Starlette upload API (`POST /upload/files`, `GET /upload/status/{job_id}`)

## 3. Expected Behavior (End-to-End)

Documents enter the system via either the HTTP upload API (`/upload/files`) or batch preprocessing (`preprocess_client.py`). Uploaded files are staged to MinIO's `staging/` prefix and an arq job is enqueued. The arq worker picks up the job, applies a memory-admission gate, then spawns a subprocess running `converters_cli.py` which converts the document (PDF/DOCX/XLSX/PPTX) to Markdown via Docling (with optional OCR escalation for scanned/garbled pages). The Markdown is then passed to `helpers.py` which builds a hierarchical tree representation, runs `validate_tree()` to enforce quality gates (node count, depth, garbling detection), classifies the content via LLM, and optionally splits oversized leaf nodes. The validated tree is serialized to MinIO (`processed/<doc_id>.json`) with a metadata sidecar (`.meta.json`). On the query path, MCP tools load documents from MinIO (with Redis cache), traverse the tree structure, and serve results to LLM clients via the MCP protocol. Documents can be deleted with full cascade across all MinIO prefixes, Redis cache, and (eventually) the PostgreSQL registry.

## 4. Audit Goals

This audit systematically examines the docstore subsystem for:

1. **Broken functionality** — Runtime errors, data loss paths, dead code paths
2. **Misconfigurations** — Env-var defaults that silently degrade behavior, missing validation
3. **Incomplete implementations** — Stubbed functions, TODO markers, partially-wired features
4. **Poor error handling** — Swallowed exceptions, missing retries, unclear failure modes
5. **Performance bottlenecks** — O(n) scans, unbounded allocations, missing connection pooling
6. **Architectural anti-patterns** — Tight coupling, layering violations, inconsistent abstractions
7. **Data integrity risks** — Partial writes without rollback, cache/storage inconsistency, cascade gaps in deletion
8. **Security concerns** — Credential exposure, injection vectors, missing auth on paths

## 5. Corpus Quality Scope (added 2026-07-15)

In addition to the code-level audit, the scope was expanded to include a **corpus quality analysis** of all 25 documents in `doc_store/`. This analysis evaluates the end-to-end ingestion pipeline's output quality by comparing preprocessed results against source PDFs.

### 5.1 Corpus

- **Location:** `doc_store/` (25 documents: 24 PDF, 1 JPG)
- **Languages:** German (11), English (7), Arabic (7)
- **Formats:** text-layer PDFs, scanned PDFs, image-dominant PDFs, infographics, tables

### 5.2 Verdict Taxonomy

| Verdict      | Criteria |
| ------------ | -------- |
| **PASS**     | Well-distributed tree (max_leaf < 15%), correct depth, no garbling, clean text |
| **MARGINAL** | Functional but with quality issues: high leaf concentration (15–75%), minor OCR noise, tab artifacts, incomplete splitting |
| **FAIL**     | Unusable output: zero text extracted, mojibake/garbled content persisted, >75% single-leaf concentration |

### 5.3 Systemic Gaps Tracked

Six root-cause gaps were identified in the baseline (2026-07-11) and tracked through RFC-010 D1–D5 remediation:

| Gap | Description | Root Cause |
| --- | ----------- | ---------- |
| Gap 1 | OCR escalation never fires on image-only flat docs | Missing image-ratio pre-check in client.py |
| Gap 2 | Garble-gate checks structure only, not text content | `_tree_is_garbled` / `_flat_text_is_garbled` not checking for mojibake/digit-junk patterns |
| Gap 3 | Latin inline `Article (N)` markers not matched by splitter | Regex lacked parenthesized form; line-anchored but markers are inline |
| Gap 4 | Presentation-form Arabic bypasses logical-form regex | Splitter regex matches logical-form المادة but not presentation-form ﺍﳌـﺎﺩﺓ (U+FExx) |
| Gap 5 | Arabic OCR quality — في→# substitution | Docling bug replacing في with `#` in Arabic markdown output |
| Gap 6 | Table column structure degrades on complex tables | Docling markdown table rendering limitation on wide/complex layouts |

### 5.4 MARGINAL Deep Analysis Methodology

For the 17 MARGINAL-verdict documents, the audit compares:
1. **E2E baseline metrics** (2026-07-10, pre-RFC-010) against **doc_store run metrics** (2026-07-14, post-D1–D5)
2. **Preprocessed JSON structure** (node count, depth, max_leaf concentration) against **source PDF content** (actual sections, articles, tables)
3. **Text quality signals** (garbling ratio, digit ratio, OCR noise patterns, mojibake occurrences) across runs

### 5.5 New Test Files (RFC-010)

| File | Lines | Coverage |
| ---- | ----- | -------- |
| `tests/test_rfc010_helpers.py` | 181 | GLYPH\<N\> marker detection, symbolic token exclusion in garble-gate, extended garble heuristics |
| `tests/test_rfc010_converters.py` | 156 | `_normalize_indented_headings` (D2), `_fix_fi_hash_substitution` (D5) |
