# CLAUDE.md

## Identity

PageIndex MCP Server is a vectorless / tree-reasoning RAG document-ingestion platform exposed over the Model Context Protocol. The core stack is FastMCP + arq (async job queue) + MinIO (object storage) + Redis (cache + job bus) + Prometheus (metrics). It targets generic document corpora; German insurance T&C PDFs are the first validation vertical. Python 3.12, `uv` for dependency management. VCS: GitHub — CI via GitHub Actions (`.github/workflows/`).

## Hard Rules

1. **Never claim vectorless/tree RAG beats vector RAG on accuracy.** Benchmark numbers were refuted in verification. Position only on architectural merits: no vector DB, inspectable trees, structural-query alignment.
2. **Right-to-erasure must cascade across every derived store.** Deleting the raw upload does NOT auto-remove derivatives. Purge MinIO `uploads/`, `processed/*.json`, `processed/*.meta.json`, Redis cache, and any documented backup explicitly — in that order.
3. **Route PII-bearing documents only through a no-training + zero-retention LLM tier** (OpenAI ZDR / Anthropic ZDR / Azure modified-abuse-monitoring); EU residency where the corpus warrants. `OPENAI_BASE_URL` is the routing lever; a self-hosted model is the ultimate residency fallback.
4. **AGPL-3.0 awareness.** pymupdf4llm/PyMuPDF are AGPL-3.0 (transitive dep). Serving them over a network is a legal decision to clear, not a settled safe-harbor. The MIT escape is Docling.
5. **Never silently persist a low-quality tree.** `validate_tree()` must run before `save_doc`; a failing tree must surface as an arq `low_quality_tree` error, not a stored artifact.

## Document Map

| Artifact | Look there for |
|---|---|
| `PRD.md` | Product Overview & Vision · Positioning & Differentiation · Target Users & Use Cases · Functional Requirements · Quality Bar & Acceptance Criteria · Non-Functional Requirements · Success Metrics · Out of Scope / Non-Goals · Open Questions & Risks |
| `ARCHITECTURE.md` | System Overview · Component Architecture · Ingestion Pipeline & Data Flow · PDF Extraction Strategy · Tree Quality Gate · Cross-Document Graph & Versioning · Data Model & Storage Layout (MinIO layout, env-var catalog) · Compliance & Data Residency · Observability · CI/CD · Architecture Decision Records · Risks & Thin-Evidence Flags |
| `DESIGN.md` | Design Scope · MCP Tool Contracts (the 5 registered query tools) · Upload & Job-Status API (`POST /upload/files`, `GET /upload/status/{job_id}`) · Output Schema & Machine-Consumability · Erasure / DSR Operation · Observability Surface · Honesty Notes & Open Items |

## Commands

```bash
# Dependencies
uv sync                              # install runtime deps
uv sync --extra dev                  # add pytest + httpx

# Development server (single process, port 8201)
uv run python mcp_server.py

# Production server (gunicorn + uvicorn workers)
uv run gunicorn -c gunicorn.conf.py pageindex_mcp.server:app

# Arq worker — run as a SEPARATE process from the server
uv run arq pageindex_mcp.worker.WorkerSettings

# Ingest documents (HTTP API; upload.py is NOT an active MCP tool)
#   POST  /upload/files          — enqueue a processing job
#   GET   /upload/status/{job_id} — poll for result

# Batch-preprocess local doc_store/ with hash-based change detection
uv run python preprocess_client.py
uv run python preprocess_client.py <filename>   # single file
uv run python preprocess_client.py --bg         # background, logs to preprocess.log

# Tests
uv run pytest
```

## Current Phase

Bootstrap planning artifacts complete (PRD.md, ARCHITECTURE.md, DESIGN.md written). Now executing **Tier-0 remediation**: markdown-first PDF extraction route + tree quality gate, per `PRD.md` § Functional Requirements and `ARCHITECTURE.md` § Ingestion Pipeline & Data Flow / Tree Quality Gate.
