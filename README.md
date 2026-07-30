# PageIndex MCP Server

A self-hosted, vectorless/tree-reasoning RAG platform exposed over the [Model Context Protocol](https://modelcontextprotocol.io/). Documents are parsed into inspectable hierarchical **index trees** and stored in MinIO object storage. Downstream LLM agents query them via [FastMCP](https://github.com/jlowin/fastmcp) tools — no vector database required.

> **Positioning.** The value proposition is architectural: no vector store to operate,
> inspectable document trees, and queries that align with document structure. We do
> *not* claim tree-reasoning RAG beats vector RAG on retrieval accuracy.

The runtime is split into two processes that share Redis, MinIO, and Postgres:

| Process | Role | Entry point |
|---------|------|-------------|
| **MCP Server** | Serves 5 query tools, upload API, Prometheus `/metrics` | [`server.py`](src/pageindex_mcp/server.py) |
| **Async Worker** | Consumes upload jobs, runs PDF/Office extraction + tree-building in an isolated subprocess, enforces quality gate | [`worker.py`](src/pageindex_mcp/worker.py) |

## Architecture

The diagram below is rendered inline via GitHub-native Mermaid. The **editable, fully-detailed source of truth** is [`docs/architecture.drawio`](docs/architecture.drawio) — open it in [diagrams.net](https://app.diagrams.net/) or the VS Code *Draw.io Integration* extension.

See also [`ARCHITECTURE.md`](ARCHITECTURE.md) for ADRs and design rationale,
[`PRD.md`](PRD.md) for product requirements, and [`DESIGN.md`](DESIGN.md) for API contracts.

```mermaid
graph TD
    subgraph clients["Clients"]
        MCPC["MCP Client (LLM agent)"]
        UPC["Upload Client (curl / SDK)"]
    end

    subgraph server["MCP Server — FastMCP on gunicorn + uvicorn"]
        AUTH["BearerAuthMiddleware"]
        TOOLS["5 MCP query tools"]
        RAG["_rag → _prefilter_docs → _search_one_doc"]
        UPAPI["Upload API — POST /files · GET /status"]
        METRICS["/metrics — Prometheus"]
    end

    subgraph worker["Worker Process — arq"]
        PDJ["process_document_job"]
        SUB["_run_converter_subprocess\nOOM / leak isolation"]
        DLQ["DLQ / job status = error"]
        subgraph subp["Converter subprocess — fresh interpreter per job"]
            CFG["configure_litellm\nvalidate_llm_config"]
            IDX["CustomPageIndexClient.index\npageindex fork → litellm"]
            CONV["converters.py — extraction + tree build\nDocling (MIT) primary · pymupdf4llm (AGPL) fallback\npypdfium2 outline · md → tree"]
            VT{"validate_tree\nquality gate"}
            OCR["OCR escalation — Tesseract\nlang auto-detect · full-page OCR"]
            VLM["VLM fallback — RFC-016\nrasterize → vision LLM"]
            ST["TREE route\nsave_doc + meta + raw"]
            SF["FLAT route\nsave_flat_doc"]
            GB["GARBLED → terminal reject"]
        end
    end

    subgraph infra["Storage & External Services"]
        MINIO[("MinIO — bucket: pageindex\nuploads/ · processed/*.json\n*.flat.json · *.meta.json · hashes/")]
        REDIS[("Redis DB 1\ndoc cache TTL 300s · job status TTL 24h\narq queue + DLQ")]
        PG[("PostgreSQL 16\ndocument registry\ndual-write + backfill")]
        PROV["LLM Provider\nOpenAI · Azure · compatible"]
        LF["Langfuse — tracing + cost"]
        PROM["Prometheus"]
    end

    MCPC -->|"/mcp (Bearer)"| AUTH
    UPC -->|"POST /upload (X-API-Key)"| UPAPI
    AUTH --> TOOLS --> RAG
    RAG -->|load tree| MINIO
    RAG -->|cache get/set| REDIS
    RAG -->|registry narrow| PG
    RAG -->|LLM search| PROV
    UPAPI -->|enqueue| REDIS
    UPAPI -->|stage file| MINIO
    PDJ --> SUB
    SUB -->|success| ST & SF
    SUB -->|failure| DLQ
    CFG --> IDX --> CONV --> VT
    VT -->|pass, hierarchical| ST
    VT -->|pass, flat| SF
    VT -->|garbled| OCR -->|still garbled| VLM
    VLM -->|recovered| ST
    VLM -->|still garbled| GB --> DLQ
    ST & SF -->|persist| MINIO
    ST & SF -->|dual-write| PG
    METRICS --> PROM
    IDX -->|trace| LF
    RAG -->|trace| LF
```

### Data flow summary

**Query path.** An MCP client authenticates via Bearer token, invokes one of five query tools, which read cached trees from Redis (falling back to MinIO), optionally narrow via Postgres registry, and run LLM-based reasoning search. Both LLM providers (OpenAI SDK for queries, litellm for ingestion) are configured through the same `LLM_PROVIDER` / `OPENAI_BASE_URL` levers and report to a single Langfuse project when [tracing](src/pageindex_mcp/tracing.py) is enabled.

**Ingestion path.** Files uploaded via `POST /upload/files` are staged in MinIO and enqueued in arq. The worker spawns a **separate Python subprocess** per job for OOM/leak isolation. Inside the subprocess, Docling (MIT, primary) or pymupdf4llm (AGPL, fallback) converts the document to markdown, which is then built into a hierarchical tree. [`validate_tree()`](src/pageindex_mcp/helpers.py) runs before anything is persisted:

- **TREE route** — clean hierarchical tree → `processed/<id>.json`
- **FLAT route** — clean but no hierarchy (`node_count < 3` / `depth < 2`) → `processed/<id>.flat.json`
- **Garbled** → one OCR escalation retry (Tesseract, language auto-detected: `ara`/`deu`/`eng`), then optionally VLM fallback (rasterize → vision LLM), before terminal reject → DLQ

**Document registry.** A Postgres [registry](src/pageindex_mcp/registry.py) provides a durable catalog of all processed documents. New documents are dual-written during ingestion. On startup, both server and worker auto-[backfill](src/pageindex_mcp/registry_backfill.py) any MinIO-only documents into Postgres.

## Requirements

- **Python 3.12+**
- [`uv`](https://github.com/astral-sh/uv) for dependency management
- **MinIO** — object storage
- **Redis** — job queue + document cache
- **PostgreSQL 16+** — document registry
- An **OpenAI / Azure / OpenAI-compatible** API key
- **Tesseract OCR** (`tesseract-ocr`) with language packs for OCR escalation (optional)

## Getting Started

### 1. Install dependencies

```bash
uv sync                    # runtime deps
uv sync --extra dev        # add pytest, httpx, etc.
```

### 2. Start infrastructure

[`docker-compose.yml`](docker-compose.yml) stands up Redis, MinIO, and Postgres locally. Requires Docker Compose v2.24+.

```bash
docker compose up -d
```

### 3. Configure environment

```bash
cp .env.example .env       # then fill in OPENAI_API_KEY
```

Minimum `.env` for local development:

```dotenv
OPENAI_API_KEY=sk-your-key-here
REDIS_URL=redis://localhost:6379/1
MINIO_ENDPOINT=localhost:9000
POSTGRES_DSN=postgresql://pageindex:pageindex@localhost:5432/pageindex
MCP_PORT=8201
```

Both processes read `.env` via `load_dotenv`.

### 4. Run the server and worker

```bash
# Shell 1 — MCP server (development, single process)
uv run python mcp_server.py

# Shell 2 — arq worker (must run separately)
uv run arq pageindex_mcp.worker.WorkerSettings
```

### 5. Smoke test

```bash
# Prometheus metrics (no auth required)
curl -s localhost:8201/metrics | head

# Upload a document (X-API-Key must match UPLOAD_API_KEY in .env)
curl -s -X POST localhost:8201/upload/files \
  -H "X-API-Key: dev-api-key" \
  -F files=@/path/to/document.pdf
# → [{"job_id": "...", "filename": "..."}]  (202 Accepted)

# Poll until the worker finishes
curl -s -H "X-API-Key: dev-api-key" \
  localhost:8201/upload/status/<job_id>
# → {"status": "done", "doc_id": "a1b2c3d4", ...}
```

### Production deployment

```bash
# Server — gunicorn + uvicorn (keep WEB_CONCURRENCY=1; scale via pod replicas)
uv run gunicorn -c gunicorn.conf.py pageindex_mcp.server:app

# Worker — one or more separate processes
uv run arq pageindex_mcp.worker.WorkerSettings
```

### Full-stack Docker deployment

```bash
docker compose --profile app up -d --build
```

> Building the image requires access to the private `trehansalil/PageIndex-salil` dependency.
> To skip the build, edit the `image:` line under `x-app` in `docker-compose.yml` to
> `ghcr.io/trehansalil/pageindex-mcp:latest` and run `docker compose --profile app up -d`.

### Local endpoints

| Service | URL | Notes |
|---------|-----|-------|
| MCP server | `http://localhost:8201/mcp` | `/upload` and `/metrics` on the same port |
| MinIO console | `http://localhost:9001` | `minioadmin` / `minioadmin` |
| Redis | `localhost:6379` | DB `1` |
| PostgreSQL | `localhost:5432` | DB `pageindex`, user `pageindex`/`pageindex` |

## MCP Query Tools

The server registers five **read-only** query tools via [`tools/documents.py`](src/pageindex_mcp/tools/documents.py). Document *processing* is not an MCP tool — it goes through the Upload API + arq worker.

| Tool | Description |
|------|-------------|
| `recent_documents(page, page_size)` | Browse the collection with pagination; newest first |
| `find_relevant_documents(query)` | Reasoning-based tree search across all documents; returns matching excerpts + source metadata |
| `get_document(doc_id)` | Metadata and top-level section list for one document |
| `get_document_structure(doc_id)` | Full hierarchical tree structure (text bodies omitted) |
| `get_page_content(doc_id, pages)` | Page content — single (`"5"`), range (`"3-7"`), or list (`"3,5,7"`) |

## Upload API

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/upload/files` | `POST` | `X-API-Key` | Upload one or more files for async indexing (202 Accepted) |
| `/upload/status/{job_id}` | `GET` | `X-API-Key` | Poll job state: `pending` → `done` \| `error` |

**Supported formats:** `.pdf`, `.docx`, `.pptx`, `.html`, `.md`, `.txt`, and image formats.

**Idempotent dedup:** The worker computes a SHA-256 hash per file and skips re-processing unchanged content. The hash cache is stored in MinIO at `hashes/processed_hashes.json`.

## Batch Preprocessing

[`preprocess_client.py`](preprocess_client.py) processes files from the local `doc_store/` directory through the same converter subprocess, with hash-based change detection:

```bash
uv run python preprocess_client.py              # all new/changed files
uv run python preprocess_client.py HR_FAQ.docx   # single file
uv run python preprocess_client.py --bg          # background (logs to preprocess.log)
```

## Configuration

All configuration is env-var driven. Copy `.env.example` to `.env` and fill in required values.

### LLM provider

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | API key (falls back to `CHATGPT_API_KEY` if unset) |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Endpoint for OpenAI-compatible providers |
| `LLM_PROVIDER` | `auto` | Provider mode: `auto` \| `openai` \| `compatible` \| `azure` |
| `AZURE_API_VERSION` | — | Required only for Azure (e.g. `2024-08-01-preview`) |
| `PAGEINDEX_MODEL` | `gpt-4o-2024-11-20` | Model for tree generation; use `azure/<deployment>` for Azure |
| `PAGEINDEX_FILTER_MODEL` | `gpt-4o-mini` | Model for document pre-filtering |
| `PAGEINDEX_SEARCH_MODEL` | `gpt-4o-mini` | Model for tree search |
| `PAGEINDEX_SEARCH_CONCURRENCY` | `3` | Concurrent tree-search tasks |

> **PII routing.** For documents containing personal data, route through a no-training + zero-retention LLM tier
> (OpenAI ZDR / Anthropic ZDR / Azure modified-abuse-monitoring) with EU residency where the corpus warrants.
> `OPENAI_BASE_URL` is the routing lever; a self-hosted model is the ultimate residency fallback.

### Auth

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_BEARER_TOKEN` | — | Bearer token for `/mcp`; empty = 503 unless `MCP_ALLOW_UNAUTHENTICATED=true` |
| `MCP_ALLOW_UNAUTHENTICATED` | `false` | Allow unauthenticated MCP access (development only) |
| `UPLOAD_API_KEY` | — | `X-API-Key` required for `/upload` endpoints |

### Storage

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `CACHE_TTL` | `300` | Document cache TTL (seconds) |
| `MINIO_ENDPOINT` | `localhost:9000` | MinIO endpoint |
| `MINIO_ACCESS_KEY` | `minioadmin` | MinIO access key |
| `MINIO_SECRET_KEY` | `minioadmin` | MinIO secret key |
| `MINIO_BUCKET` | `pageindex` | Bucket name |
| `MINIO_SECURE` | `false` | Use TLS for MinIO |
| `POSTGRES_DSN` | — | asyncpg DSN (e.g. `postgresql://user:pass@host:5432/dbname`) |
| `REGISTRY_ENABLED` | `true` | Master switch for Postgres registry; `false` falls back to MinIO listing |

### Server

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_HOST` | `0.0.0.0` | Server bind address |
| `MCP_PORT` | `8201` | Server port (`/mcp`, `/upload`, `/metrics` share this port) |
| `WEB_CONCURRENCY` | `1` | Keep at `1` — MCP sessions are in-memory per worker |
| `MAX_UPLOAD_SIZE_MB` | `100` | Upload size cap; requests over this are rejected with HTTP 413 |

### Registry

| Variable | Default | Description |
|----------|---------|-------------|
| `PAGEINDEX_CATALOG_TOPK` | `200` | Max documents returned by BM25 stage before LLM prefilter |
| `PAGEINDEX_REGISTRY_QUERY_CONCURRENCY` | `15` | Concurrent `get_doc()` fan-out during RAG search |
| `PAGEINDEX_REGISTRY_RECONCILE_INTERVAL_S` | `1200` | Drift-reconciliation cron interval; clamped to [60, 86400]s |
| `REGISTRY_DELETE_TIMEOUT_S` | `5.0` | Timeout for registry delete operations |

### PDF extraction

| Variable | Default | Description |
|----------|---------|-------------|
| `PDF_CONVERTER` | `docling` | Primary PDF→markdown converter: `docling` (MIT) or `pymupdf4llm` (AGPL) |
| `FLAT_DOC_ROUTING` | `true` | Allow flat-but-clean documents to succeed via FLAT route |
| `DOCLING_DO_OCR` | `0` | Enable Docling's built-in OCR (`1`/`0`) |
| `DOCLING_NUM_THREADS` | `1` | Intra-op parallelism for Docling |
| `DOCLING_ARTIFACTS_PATH` | — | Pre-downloaded Docling model weights for offline use |

### OCR escalation (optional)

Runs once when `validate_tree()` flags a document as garbled or image-dominant. Language is auto-detected from filename and content (Unicode-script ratio — `ara`/`deu`/`eng`).

| Variable | Default | Description |
|----------|---------|-------------|
| `OCR_ESCALATION` | `1` | Enable force-full-page-OCR retry on garbled PDFs |
| `TESSDATA_PREFIX` | — | Directory holding `.traineddata` files; empty = system default |
| `TESSDATA_ALLOW_DOWNLOAD` | `0` | Allow runtime download of missing language packs |

### VLM fallback (optional)

Last-resort fallback when OCR escalation still produces garbled output. Pages are rasterized at 200 DPI via pypdfium2 and sent to a vision-capable LLM. Config-gated off by default.

| Variable | Default | Description |
|----------|---------|-------------|
| `VLM_FALLBACK` | `false` | Enable VLM last-resort fallback |
| `VLM_MODEL` | `gpt-4.1` | Vision-capable model for rasterization extraction |
| `VLM_DESCRIBE_IMAGES` | `false` | Enable VLM-based image description generation |

### Langfuse tracing (optional)

Tracing activates **only when both keys are set** (unset = disabled, zero overhead).

| Variable | Default | Description |
|----------|---------|-------------|
| `LANGFUSE_PUBLIC_KEY` | — | Project public key |
| `LANGFUSE_SECRET_KEY` | — | Project secret key |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` | EU default; US = `https://us.cloud.langfuse.com` |
| `LANGFUSE_TRACE_CONTENT` | `false` | `false` masks prompt/completion bodies (usage + cost still recorded) |

### Worker tuning (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `MEM_ADMISSION_FLOOR_BYTES` | `2300000000` | Minimum free RAM (bytes) before admitting an extraction job |
| `MEM_ADMISSION_MAX_WAIT_S` | `120` | Max seconds to wait for RAM headroom before rejecting |
| `MEM_ADMISSION_POLL_S` | `3` | Poll interval (seconds) for the memory admission gate |
| `ARQ_QUEUE_DEPTH_SCRAPE_INTERVAL_S` | `5` | Scrape interval for arq queue-depth Prometheus gauge |

### Compliance

| Variable | Default | Description |
|----------|---------|-------------|
| `PII_CORPUS` | `false` | When `true`, startup asserts `OPENAI_BASE_URL` is on the ZDR allow-list |

## Storage Layout (MinIO)

```
pageindex/                              (bucket)
├── uploads/<doc_id>/<filename>          # raw source file
├── uploads/staging/<job_id>/<file>      # staged upload awaiting worker
├── preloaded/<filename>                 # files synced from local doc_store/
├── processed/<doc_id>.json             # indexed tree (TREE route)
├── processed/<doc_id>.flat.json        # flat document (FLAT route)
├── processed/<doc_id>.meta.json        # document metadata sidecar
└── hashes/processed_hashes.json        # {filename: sha256} dedup cache
```

### Right-to-erasure (GDPR Art. 17)

Deletion must cascade across **every** derived store, in this order:

1. MinIO `uploads/<doc_id>/*`
2. MinIO `processed/<doc_id>.json` / `.flat.json` / `.meta.json`
3. MinIO `hashes/processed_hashes.json` (remove entry)
4. Redis cache key (`pageindex:doc:<doc_id>`)
5. Postgres registry row
6. MinIO `preloaded/<filename>` (if applicable)

> Backup snapshots require manual purge — the automated fan-out only touches live stores.

## Observability

### Prometheus metrics

Exposed at `GET /metrics` (unauthenticated; restrict via network policy in production). Selected key metrics:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `pageindex_tool_calls_total` | Counter | `tool` | MCP tool invocation count |
| `pageindex_tool_errors_total` | Counter | `tool` | MCP tool error count |
| `pageindex_tool_duration_seconds` | Histogram | `tool` | Tool latency |
| `pageindex_uploads_total` | Counter | `status` | Upload completions |
| `pageindex_active_uploads` | Gauge | — | In-flight upload jobs |
| `pageindex_arq_queue_depth` | Gauge | — | Jobs waiting in arq queue |
| `pageindex_rag_searches_total` | Counter | — | RAG search invocations |
| `pageindex_rag_duration_seconds` | Histogram | — | Full RAG pipeline duration |
| `pageindex_llm_calls_total` | Counter | — | LLM API calls |
| `pageindex_llm_duration_seconds` | Histogram | — | Per-LLM-call latency |
| `pageindex_minio_operations_total` | Counter | `operation` | MinIO operation count |
| `pageindex_documents_total` | Gauge | — | Total indexed documents |
| `pageindex_low_quality_trees_total` | Counter | `reason` | Trees rejected by quality gate |
| `pageindex_flat_docs_total` | Counter | `content_class` | Documents routed to flat path |
| `pageindex_ocr_escalation_total` | Counter | `result` | OCR retries on garbled PDFs |
| `pageindex_vlm_fallback_total` | Counter | `result` | VLM last-resort attempts |
| `pageindex_registry_fallback_total` | Counter | `reason` | Registry-to-MinIO fallbacks |
| `pageindex_converter_child_peak_rss_kib` | Gauge | — | Peak RSS of last converter subprocess |
| `pageindex_converter_child_oom_total` | Counter | — | Converter OOM kills |

Full list: 33 metrics. See [`metrics.py`](src/pageindex_mcp/metrics.py).

### Langfuse tracing

Both LLM paths (query via OpenAI SDK, ingestion via litellm) feed one Langfuse project when configured. `LANGFUSE_TRACE_CONTENT=false` (default) masks prompt/completion bodies; usage, model, and cost are always recorded.

### Alerting recommendations

| Alert | Condition |
|-------|-----------|
| High error rate | `rate(pageindex_tool_errors_total[5m]) / rate(pageindex_tool_calls_total[5m]) > 0.1` |
| Quality gate firing | `rate(pageindex_low_quality_trees_total[1h]) > 0` |
| Upload backlog | `pageindex_active_uploads > 10` for 5 min |
| Slow RAG | `histogram_quantile(0.95, pageindex_rag_duration_seconds) > 30` |

## Quality Gates

Eight gate scripts under [`scripts/gates/`](scripts/gates/) enforce code and build quality:

| Gate | Script | What it checks |
|------|--------|----------------|
| Static | `static.sh` | ruff check, ruff format, mypy, secrets scan, layer isolation |
| Unit | `unit.sh` | pytest pass, coverage thresholds, assertion density |
| Contracts | `contracts.sh` | Contract YAML → test mapping |
| DAG | `dag.sh` | `dag.yaml` acyclicity, node artifact resolution |
| Build | `build.sh` | Wheel build, Docker build |
| Supply-chain | `supply-chain.sh` | *(not yet configured)* |
| Integration | `integration.sh` | *(requires infrastructure)* |
| E2E | `e2e.sh` | *(requires infrastructure)* |

```bash
# Run all gates
for gate in scripts/gates/*.sh; do bash "$gate"; done

# Run a single gate
bash scripts/gates/static.sh
bash scripts/gates/unit.sh
```

## Testing

```bash
uv run pytest                              # full suite (988 tests)
uv run pytest --cov=pageindex_mcp          # with coverage
uv run pytest tests/test_converters.py -v  # single file
```

### Manual registry backfill

The automatic startup backfill handles most cases. For manual control:

```bash
uv run python -m pageindex_mcp.registry_backfill --dry-run   # preview
uv run python -m pageindex_mcp.registry_backfill --force      # force re-backfill
```

## Project Structure

```
src/pageindex_mcp/
├── server.py               # composition root: FastMCP + auth + /metrics + /upload mount
├── config.py               # frozen Settings dataclass; all env vars loaded here
├── auth.py                 # BearerAuthMiddleware (exempts /metrics, /upload)
├── upload_app.py           # FastAPI sub-app: POST /upload/files, GET /upload/status/{job_id}
├── worker.py               # arq worker: process_document_job, subprocess spawn, DLQ
├── converters_cli.py       # converter subprocess entry point (fresh interpreter per job)
├── converters.py           # PDF/Office → markdown extraction + tree build
├── client.py               # CustomPageIndexClient.index(); LLM provider abstraction
├── helpers.py              # _rag, _prefilter_docs, _search_one_doc, validate_tree
├── cache.py                # Redis read-through doc/job cache (tree → flat fallback)
├── storage.py              # MinIO read/write/delete helpers
├── registry.py             # Postgres document registry (RFC-006)
├── registry_backfill.py    # MinIO → Postgres backfill (auto-runs on startup)
├── memory_admission.py     # Redis-backed memory admission gate (worker backpressure)
├── metrics.py              # 33 Prometheus metrics + /metrics response handler
├── queue_metrics.py        # arq queue-depth Prometheus scrape loop
├── tracing.py              # Langfuse trace helpers (query + ingestion paths)
├── hash_cache_migrate.py   # hash cache migration helper
└── tools/
    └── documents.py        # 5 MCP query tools
```

## Architecture Notes

- **Vectorless tree RAG.** The [`pageindex`](https://github.com/trehansalil/PageIndex-salil) library builds a hierarchical index per document and performs reasoning-based search over the tree — no vector database involved.

- **Markdown-first PDF extraction.** [Docling](https://github.com/DS4SD/docling) (MIT) is the primary PDF→markdown route; `pymupdf4llm` (AGPL-3.0) is the fallback (behind the `agpl-fallback` optional extra). Markdown-first fixes ligature-garbling (`Haftpficht` → `Haftpflicht`) seen in naive PDF text extraction. Document outlines are read via `pypdfium2` (BSD).

- **Multi-stage garble detection.** [`validate_tree()`](src/pageindex_mcp/helpers.py) checks for known ligature artifacts, high PUA/digit/control-char ratios, sparse mojibake (localized Arabic-Latin script mixing), and single-token repetition. A garbled PDF first gets one Tesseract OCR retry, then optionally a VLM fallback, before terminal reject.

- **Document registry (RFC-006).** Postgres provides a durable, searchable catalog of processed documents with GIN full-text index and BM25 candidate narrowing. Replaces O(N) MinIO listing at corpus scale.

- **LLM provider abstraction.** OpenAI, Azure, and any OpenAI-compatible endpoint (vLLM, Together, Groq, OpenRouter, local) are supported without code changes via `LLM_PROVIDER` / `OPENAI_BASE_URL`. The query path uses the OpenAI SDK; the ingestion path uses `litellm`.

- **Memory admission gate.** The worker uses a Redis-backed memory admission gate to prevent OOM: new extraction jobs are held until the node has sufficient free RAM (`MEM_ADMISSION_FLOOR_BYTES`).

- **Subprocess isolation.** Each extraction job runs in a fresh Python subprocess (`converters_cli.py`), isolating memory leaks and OOM crashes from the long-lived worker process. Peak RSS is tracked via `pageindex_converter_child_peak_rss_kib`.

- **AGPL-3.0 awareness.** `pymupdf4llm`/PyMuPDF are AGPL-3.0. Serving them over a network is a legal decision. The MIT escape is Docling (default). See `ARCHITECTURE.md` ADR-001.

## License

See [LICENSE](LICENSE).
