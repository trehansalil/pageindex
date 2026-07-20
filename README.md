# PageIndex MCP Server

A [FastMCP](https://github.com/jlowin/fastmcp)-based server that exposes document
ingestion and retrieval over [Model Context Protocol](https://modelcontextprotocol.io/).
Documents are parsed into hierarchical **index trees** using a vectorless,
reasoning-based RAG approach — no vector database required — and stored in MinIO object
storage. Queries are answered by reasoning over tree structure rather than
nearest-neighbour embedding search.

> **Positioning.** The value here is architectural — no vector store to operate,
> inspectable document trees, and queries that align with document structure. We do
> *not* claim tree-reasoning RAG beats vector RAG on retrieval accuracy.

The runtime is split into two processes that share Redis, MinIO, and Postgres:

- **① MCP Server** (FastMCP on gunicorn + uvicorn) — serves query tools,
  upload API, Prometheus `/metrics` endpoint.
  ([`server.py`](src/pageindex_mcp/server.py))
- **② Async Worker** (arq) — consumes upload jobs, runs PDF/Office extraction and
  tree-building in an isolated subprocess, enforces quality gate, persists
  results.
  ([`worker.py`](src/pageindex_mcp/worker.py))

## Architecture

The diagram below is rendered inline (GitHub-native Mermaid). The **editable,
fully-detailed source of truth** is [`docs/architecture.drawio`](docs/architecture.drawio)
— open it in [diagrams.net](https://app.diagrams.net/) (`File → Open`) or the
VS Code *Draw.io Integration* extension. Keep `.drawio` in sync when
component layout changes; this Mermaid view is a simplified mirror.
See also [`ARCHITECTURE.md`](ARCHITECTURE.md) for ADRs and design rationale,
[`PRD.md`](PRD.md) for product requirements, and [`DESIGN.md`](DESIGN.md)
for MCP tool contracts and API specifications.

```mermaid
flowchart LR
    subgraph clients["Clients"]
        MCPC["MCP Client (Claude)<br/>Authorization: Bearer"]
        UPC["Upload Client /<br/>preprocess_client.py"]
    end

    subgraph server["① MCP Server Process — FastMCP on gunicorn + uvicorn (:8201)"]
        AUTH["BearerAuthMiddleware (auth.py)<br/>401 gate · exempts /metrics, /upload"]
        UPAPI["Upload API (upload_app.py)<br/>POST /upload/files<br/>GET /upload/status/{job_id}"]
        MET["/metrics endpoint<br/>Prometheus, no-auth"]
        TOOLS["5 MCP Query Tools (tools/documents.py)<br/>recent_documents · find_relevant_documents<br/>get_document · get_document_structure · get_page_content"]
        RAG["_rag / _rag_inner (helpers.py)<br/>tree-search RAG"]
        PF["_prefilter_docs<br/>LLM relevance filter"]
        S1["_search_one_doc<br/>tree select + flat adapter"]
        LLMQ["_llm() — OpenAI SDK call"]
        GET["cache.get_doc read-through (cache.py)<br/>tree → flat fallback"]
    end

    subgraph worker["② Async Worker Process — arq"]
        PDJ["process_document_job (worker.py)<br/>orchestrator"]
        SUB["_run_converter_subprocess<br/>OOM / leak isolation"]
        DLQ["DLQ / job status = error"]
        subgraph subp["Converter subprocess converters_cli.main (fresh interpreter per job)"]
            CFG["configure_litellm() / validate_llm_config()"]
            IDX["CustomPageIndexClient.index()<br/>pageindex fork → litellm"]
            CONV["converters.py — extraction + tree build<br/>Docling (MIT) primary · pymupdf4llm (AGPL) fallback<br/>pypdfium2 outline · md → tree"]
            VT{"validate_tree()<br/>HR5 quality gate"}
            OCR["OCR escalation (Tesseract)<br/>lang auto-detect · re-run Docling full-page OCR"]
            VLM["VLM fallback (RFC-016)<br/>rasterize → vision LLM<br/>last-resort garble recovery"]
            ST["TREE route<br/>save_doc + meta + raw"]
            SF["FLAT route (success)<br/>save_flat_doc"]
            GB["GARBLED → terminal reject"]
        end
    end

    subgraph infra["Storage & External Services"]
        MINIO[("MinIO (S3) — bucket: pageindex<br/>uploads/ · processed/*.json<br/>*.flat.json · *.meta.json · hashes/")]
        REDIS[("Redis (DB 1)<br/>doc cache TTL 300s · job status TTL 24h<br/>arq queue + DLQ")]
        PG[("PostgreSQL 16<br/>document registry<br/>dual-write + backfill")]
        PROV["LLM Provider<br/>OpenAI · Azure · OpenAI-compatible"]
        LF["Langfuse (tracing + cost)"]
        PROM["Prometheus"]
    end

    MCPC -->|"/mcp (Bearer)"| AUTH
    UPC -->|"HTTPS upload"| UPAPI
    AUTH -->|authorized| TOOLS
    TOOLS --> RAG --> PF --> S1
    PF -->|"filter LLM"| LLMQ
    S1 -->|"search LLM ×N"| LLMQ
    S1 --> GET
    GET -. "cache get/set" .-> REDIS
    GET --> MINIO
    LLMQ --> PROV
    LLMQ -. "query traces" .-> LF
    UPAPI -->|"upload_staging"| MINIO
    UPAPI -->|"enqueue job + status"| REDIS
    REDIS -->|"arq dequeue"| PDJ
    PDJ --> SUB --> CFG --> IDX --> CONV
    CONV --> VT
    VT -->|"clean tree"| ST --> MINIO
    VT -->|"clean flat"| SF --> MINIO
    VT -->|"garbled"| OCR -->|"re-run"| CONV
    VT -->|"still garbled"| VLM -->|"re-run"| CONV
    VLM -->|"still garbled"| GB --> DLQ
    OCR -->|"still garbled"| VLM
    IDX -. "ingestion traces" .-> LF
    PDJ -. "dual-write" .-> PG
    MET --> PROM
```

**Two LLM paths, deliberately wired differently.** The *query* path calls the provider
through the OpenAI SDK ([`helpers.py`](src/pageindex_mcp/helpers.py) `_llm()`); the
*ingestion* path runs inside the converter subprocess
([`converters_cli.py`](src/pageindex_mcp/converters_cli.py)) and goes through the
`pageindex` fork → `litellm`. Both are routed by the same environment levers
(`LLM_PROVIDER` / `OPENAI_BASE_URL` — see [`config.py`](src/pageindex_mcp/config.py))
and both report to a single Langfuse project when
[tracing](src/pageindex_mcp/tracing.py) is enabled.

**Quality gate.** [`validate_tree()`](src/pageindex_mcp/helpers.py) runs before anything
is persisted. A clean hierarchical tree takes the **TREE route**; a clean-but-flat
document (`node_count < 3` / `depth < 2`) still **succeeds** via the **FLAT route**
(`processed/<id>.flat.json`). A garbled or image-dominant PDF first gets one **OCR
escalation** retry (Tesseract, language auto-detected from filename/content —
`ara`/`deu`/`eng`), then optionally a **VLM fallback** (rasterize pages → vision LLM
extraction, RFC-016) before terminal reject (`low_quality_tree` → DLQ).

**Document registry.** A Postgres [registry](src/pageindex_mcp/registry.py) (RFC-006)
provides a durable catalog of all processed documents. New documents are dual-written
into the registry during ingestion. On startup, both the server and worker run an
automatic [backfill](src/pageindex_mcp/registry_backfill.py) (`run_auto_backfill()`) to
sync any MinIO-only documents into Postgres and set the `pageindex:registry:complete`
Redis flag.

## Requirements

- Python 3.12+
- [`uv`](https://github.com/astral-sh/uv) for dependency management
- A running **MinIO** instance (object storage)
- A running **Redis** instance (job queue + cache)
- A running **PostgreSQL 16+** instance (document registry)
- An OpenAI / Azure / OpenAI-compatible API key (for the PageIndex library)
- **Tesseract OCR** (`tesseract-ocr`) on the worker's `PATH` for OCR
  escalation on garbled/image-dominant PDFs (see [OCR escalation](#ocr-escalation-optional))

## Setup

```bash
# Install runtime dependencies
uv sync

# For development (adds pytest/httpx/fakeredis/ruff)
uv sync --extra dev

# Include AGPL-licensed pymupdf4llm fallback (optional)
uv sync --extra agpl-fallback
```

Copy [`.env.example`](.env.example) to `.env` (or export directly) and set the variables below.
All settings are loaded in [`config.py`](src/pageindex_mcp/config.py).

### LLM (required)

| Variable | Default | Description |
| --------------------------------------- | ----------------------------- | --------------------------------------------------------------------------- |
| `OPENAI_API_KEY` or `CHATGPT_API_KEY`  | —                             | Required by the PageIndex library                                           |
| `LLM_PROVIDER`                          | `auto`                        | Provider selector: `auto` \| `openai` \| `compatible` \| `azure`           |
| `OPENAI_BASE_URL`                       | `https://api.openai.com/v1`   | OpenAI API or OpenAI-compatible endpoint (vLLM/Together/Groq/OpenRouter/local) |
| `AZURE_API_VERSION`                     | —                             | Required only for Azure (e.g., `2024-08-01-preview`)                        |
| `PAGEINDEX_MODEL`                       | `gpt-4o-2024-11-20`          | Model for ingestion; use `azure/<deployment>` for Azure                     |
| `PAGEINDEX_FILTER_MODEL`               | `gpt-4o-mini`                 | Model for document pre-filtering                                            |
| `PAGEINDEX_SEARCH_MODEL`               | `gpt-4o-mini`                 | Model for tree search                                                       |
| `PAGEINDEX_SEARCH_CONCURRENCY`         | `3`                           | Concurrent tree-search tasks                                                |
| `PAGEINDEX_CATALOG_TOPK`              | `200`                         | Max documents considered during catalog pre-filter                          |
| `PAGEINDEX_REGISTRY_QUERY_CONCURRENCY` | `15`                          | Bound on concurrent `get_doc()` fan-out during RAG search; clamped to >=1   |
| `PAGEINDEX_REGISTRY_RECONCILE_INTERVAL_S` | `1200`                     | Cadence of the registry drift-reconciliation cron job; clamped to [60, 86400]s |

> **PII routing (HR3).** Route documents containing personal data only through a
> no-training + zero-retention LLM tier (OpenAI ZDR / Anthropic ZDR / Azure
> modified-abuse-monitoring), with EU residency where the corpus warrants.
> `OPENAI_BASE_URL` is the routing lever; a self-hosted model is the ultimate
> residency fallback.

### Auth

| Variable                     | Default       | Description                                                                 |
| ---------------------------- | ------------- | --------------------------------------------------------------------------- |
| `MCP_BEARER_TOKEN`           | —             | `Authorization: Bearer <token>` on `/mcp`; empty = 503 unless `MCP_ALLOW_UNAUTHENTICATED=true` |
| `UPLOAD_API_KEY`             | —             | Required by `POST /upload/files` via `X-API-Key` header; empty = 503, wrong/missing header = 401 |
| `MCP_ALLOW_UNAUTHENTICATED`  | `false`       | Explicit opt-in for unauthenticated dev mode (fail-closed by default)      |
| `PII_CORPUS`                 | `false`       | When `true`, refuses startup unless a ZDR-compliant endpoint is configured |

See [`auth.py`](src/pageindex_mcp/auth.py) for the `BearerAuthMiddleware` implementation.

### Redis, MinIO & Postgres

| Variable            | Default                       | Description                          |
| ------------------- | ----------------------------- | ------------------------------------ |
| `REDIS_URL`         | `redis://localhost:6379/1`    | Redis connection (cache + arq queue) |
| `CACHE_TTL`         | `300`                         | Document cache TTL (seconds)         |
| `MINIO_ENDPOINT`    | `localhost:9000`              | MinIO server address                 |
| `MINIO_ACCESS_KEY`  | `minioadmin`                  | MinIO access key                     |
| `MINIO_SECRET_KEY`  | `minioadmin`                  | MinIO secret key                     |
| `MINIO_BUCKET`      | `pageindex`                   | Bucket name                          |
| `MINIO_SECURE`      | `false`                       | Use TLS for MinIO connection         |
| `POSTGRES_DSN`      | —                             | asyncpg DSN (e.g. `postgresql://user:pass@host:5432/dbname`) |
| `REGISTRY_ENABLED`  | `true`                        | Master switch for the Postgres document registry; `false` bypasses all registry code |

### Server

| Variable          | Default   | Description                                                 |
| ----------------- | --------- | ----------------------------------------------------------- |
| `MCP_HOST`        | `0.0.0.0` | Server bind address                                         |
| `MCP_PORT`        | `8201`    | Server port (`/mcp`, `/upload`, `/metrics` share this port) |
| `WEB_CONCURRENCY` | `1`       | Keep at `1` — MCP sessions are in-memory per worker         |

### PDF extraction

| Variable               | Default   | Description                                                                 |
| ---------------------- | --------- | --------------------------------------------------------------------------- |
| `PDF_CONVERTER`        | `docling` | Primary PDF→markdown converter: `docling` (MIT) or `pymupdf4llm` (AGPL)   |
| `FLAT_DOC_ROUTING`     | `true`    | Allow flat-but-clean documents to succeed via the FLAT route               |
| `DOCLING_DO_OCR`       | `0`       | Enable Docling's built-in OCR (`1`/`0`); text-layer PDFs need no OCR      |
| `DOCLING_NUM_THREADS`  | `1`       | Intra-op parallelism for Docling; raise only where the node has RAM headroom |
| `DOCLING_ARTIFACTS_PATH` | —       | Directory of pre-downloaded Docling model weights for offline use          |

### OCR escalation (optional)

Runs once, only when `validate_tree()` flags a document as garbled or
image-dominant. Language is auto-detected from filename/content
(Unicode-script ratio — `ara`/`deu`/`eng`), never guessed by an LLM.

| Variable                | Default   | Description                                                                  |
| ----------------------- | --------- | ---------------------------------------------------------------------------- |
| `OCR_ESCALATION`        | `1`       | Enable Tesseract OCR retry on garbled/image-dominant PDFs; `0` disables it  |
| `TESSDATA_PREFIX`       | —         | Directory holding `<lang>.traineddata`; unset trusts system Tesseract install |
| `TESSDATA_ALLOW_DOWNLOAD` | `0`     | `1` fetches missing traineddata from the official tessdata repo at runtime; production images pre-bake `deu`/`eng`/`ara` instead (no egress) |

### VLM fallback (optional)

Last-resort garble recovery (RFC-016). When OCR escalation still produces
garbled output, pages are rasterized at 200 DPI via pypdfium2 and sent to a
vision-capable LLM for text extraction. Config-gated off by default.

| Variable       | Default   | Description                                                        |
| -------------- | --------- | ------------------------------------------------------------------ |
| `VLM_FALLBACK` | `false`   | Enable VLM last-resort fallback; uses the same ZDR client lever   |
| `VLM_MODEL`    | `gpt-4.1` | Vision-capable model for page rasterization extraction            |

### Langfuse tracing (optional)

Tracing activates **only when both keys are set** (unset = disabled, zero overhead).

| Variable                | Default                        | Description                                                                 |
| ----------------------- | ------------------------------ | --------------------------------------------------------------------------- |
| `LANGFUSE_PUBLIC_KEY`   | —                              | Project public key                                                          |
| `LANGFUSE_SECRET_KEY`   | —                              | Project secret key                                                          |
| `LANGFUSE_HOST`         | `https://cloud.langfuse.com`   | EU region default; US = `https://us.cloud.langfuse.com`                     |
| `LANGFUSE_TRACE_CONTENT`| `false`                        | `false` masks prompt/completion bodies (usage + cost still recorded). `true` exports full document text — **inappropriate for PII corpora** |

### Worker tuning (optional)

| Variable                          | Default        | Description                                                    |
| --------------------------------- | -------------- | -------------------------------------------------------------- |
| `MEM_ADMISSION_FLOOR_BYTES`       | `2300000000`   | Minimum free RAM (bytes) before admitting a new extraction job |
| `MEM_ADMISSION_MAX_WAIT_S`        | `120`          | Max seconds to wait for RAM headroom before rejecting a job    |
| `MEM_ADMISSION_POLL_S`            | `3`            | Poll interval (seconds) for the memory admission gate          |
| `ARQ_QUEUE_DEPTH_SCRAPE_INTERVAL_S` | `5`          | How often to refresh the arq queue-depth Prometheus gauge      |

See [`memory_admission.py`](src/pageindex_mcp/memory_admission.py) and
[`queue_metrics.py`](src/pageindex_mcp/queue_metrics.py) for implementations.

## Running

Server and worker run as **separate processes**, both must be running.
On startup, both processes automatically backfill the Postgres document
registry from MinIO if needed (`run_auto_backfill()`).

```bash
# ① MCP server (development, single process, port 8201)
uv run python mcp_server.py
# or via installed console script:
uv run pageindex-mcp

# ② Async worker (separate shell)
uv run arq pageindex_mcp.worker.WorkerSettings
```

The server starts at `http://0.0.0.0:8201/mcp` using `streamable-http` MCP
transport. The upload API is mounted at `/upload` and metrics at `/metrics` on the
same port.

### Production

```bash
# Server: gunicorn + uvicorn workers (keep WEB_CONCURRENCY=1; scale horizontally)
uv run gunicorn -c gunicorn.conf.py pageindex_mcp.server:app

# Worker: one or more separate processes
uv run arq pageindex_mcp.worker.WorkerSettings
```

### Manual registry backfill

The automatic startup backfill handles most cases. For manual control:

```bash
# Dry-run (preview what would be synced)
uv run python -m pageindex_mcp.registry_backfill --dry-run

# Force re-backfill even if registry is already marked complete
uv run python -m pageindex_mcp.registry_backfill --force
```

## Local Testing with Docker Compose

[`docker-compose.yml`](docker-compose.yml) stands up runtime dependencies (Redis + MinIO + Postgres) so you can
test locally without a production cluster. Requires Docker Compose v2.24+.

**Option 1 — infra only** (Redis + MinIO + Postgres), run Python app on host:

```bash
docker compose up -d   # starts redis, minio, postgres, and creates bucket
```

Point your `.env` at the local services (a fresh clone can `cp .env.example .env`
which already uses these values):

```dotenv
REDIS_URL=redis://localhost:6379/1
MINIO_ENDPOINT=localhost:9000
POSTGRES_DSN=postgresql://pageindex:pageindex@localhost:5432/pageindex
MCP_PORT=8201
```

```bash
uv run python mcp_server.py                     # server (shell 1)
uv run arq pageindex_mcp.worker.WorkerSettings   # worker (shell 2)
```

> Both processes read `.env` via `load_dotenv`, so values apply in every
> shell. If your `.env` still carries production cluster values
> (`REDIS_URL=redis://10.43.…`, `MCP_PORT=8111`), the host server binds the wrong
> port and the worker can't reach Redis — change to local values first.

**Option 2 — full stack** (Redis + MinIO + Postgres + server + worker, all containerised):

```bash
cp .env.example .env   # set OPENAI_API_KEY (and UPLOAD_API_KEY)
docker compose --profile app up -d --build
```

| Service       | URL / port                    | Notes                                             |
| ------------- | ----------------------------- | ------------------------------------------------- |
| MCP server    | `http://localhost:8201/mcp`   | `/upload` and `/metrics` mounted on same port     |
| MinIO console | `http://localhost:9001`       | login `minioadmin` / `minioadmin`                 |
| Redis         | `localhost:6379`              | DB `1` (matches `REDIS_URL`)                      |
| PostgreSQL    | `localhost:5432`              | DB `pageindex`, user `pageindex`/`pageindex`      |

`REDIS_URL`, `MINIO_ENDPOINT`, `POSTGRES_DSN`, and `MCP_PORT` from `.env` are overridden inside
compose so containers reach local `redis` / `minio` / `postgres` services; secrets such as
`OPENAI_API_KEY` are still read from `.env`. Building the image needs access to the
private `trehansalil/PageIndex-salil` dependency — to skip the build, edit the
`image:` line under the `x-app` anchor in `docker-compose.yml` to
`ghcr.io/trehansalil/pageindex-mcp:latest` and run `docker compose --profile app up -d`
(omit `--build`).

```bash
# Smoke test once full stack is up:
curl -s localhost:8201/metrics | head   # public, no auth

# Upload a document. X-API-Key must match UPLOAD_API_KEY in .env (.env.example uses
# dev-api-key). doc_store/ is gitignored — point at any local PDF/DOCX you have:
curl -s -X POST localhost:8201/upload/files \
  -H "X-API-Key: dev-api-key" -F files=@/path/to/your-document.pdf
# -> [{"job_id": "...", "filename": "..."}]   (202 Accepted; LLM runs in worker)

# Poll until worker finishes (status: pending -> done|error):
curl -s -H "X-API-Key: dev-api-key" localhost:8201/upload/status/<job_id>

docker compose --profile app down   # stop (add -v to wipe volumes)
```

## Ingesting Documents

Ingestion is **asynchronous**: the HTTP API stages the file and enqueues a job; the
arq worker does extraction, tree-building, quality-gating, and storage.

### Upload API (HTTP)

```bash
# Enqueue one or more files (202 Accepted). X-API-Key must match UPLOAD_API_KEY.
curl -s -X POST localhost:8201/upload/files \
  -H "X-API-Key: dev-api-key" \
  -F files=@/path/to/document.pdf
# -> [{"job_id": "...", "filename": "document.pdf"}]

# Poll job status until it reaches done | error:
curl -s -H "X-API-Key: dev-api-key" localhost:8201/upload/status/<job_id>
```

Supported formats: `.pdf`, `.docx`, `.pptx`, `.md`, `.txt`.

### Batch processing (`doc_store/`)

`preprocess_client.py` processes local files through the same isolated converter
subprocess as the worker, with hash-based change detection:

```bash
# Process all new/changed files in doc_store/
uv run python preprocess_client.py

# Process a single file
uv run python preprocess_client.py HR_FAQ.docx

# Run in background (logs to preprocess.log)
uv run python preprocess_client.py --bg
```

Idempotent — computes a SHA-256 hash of each file and skips anything
unchanged since the last run. The hash cache is stored in MinIO at
`hashes/processed_hashes.json`.

## MCP Query Tools

The server registers five **read-only** query tools ([`tools/documents.py`](src/pageindex_mcp/tools/documents.py)).
Document *processing* is not an MCP tool — it goes through the Upload API and
arq worker (see above).

| Tool                                        | Description                                                                                                  |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `recent_documents(page, page_size)`         | Browse the collection with pagination; newest first, with processing status                                  |
| `find_relevant_documents(query)`            | Reasoning-based tree search across documents; returns matching excerpts + source metadata as JSON             |
| `get_document(doc_id)`                      | Detailed information about one document                                                                      |
| `get_document_structure(doc_id)`            | Hierarchical structure (tree) of a completed document                                                        |
| `get_page_content(doc_id, pages)`           | Extract page content — single (`5`), range (`3-7`), or list (`3,5,7`)                                       |

## Storage Layout (MinIO)

```
pageindex/
  uploads/<doc_id>/<filename>            # raw source file
  uploads/staging/<job_id>/<file>         # staged upload awaiting worker
  processed/<doc_id>.json                # indexed tree (TREE route)
  processed/<doc_id>.flat.json           # flat document (FLAT route — success, no hierarchy)
  processed/<doc_id>.meta.json           # document metadata
  hashes/processed_hashes.json           # change-detection cache
```

Redis (`DB 1`) holds document cache (`pageindex:doc:<id>`, TTL 300s), job
status (`pageindex:job:<id>`, TTL 24h), arq job queue + DLQ.

PostgreSQL holds the document registry — a durable catalog of all processed
documents with metadata, used by `recent_documents` for fast paginated listing.

`doc_id` values are 8-character UUID prefixes generated at processing time.

> **Right-to-erasure (HR2).** Deleting the raw upload does **not** auto-remove
> derivatives. Erasure must cascade across every derived store, in this order:
> (1) MinIO `uploads/<doc_id>/*`, (2) `processed/<doc_id>.json`,
> (2b) `processed/<doc_id>.flat.json`, (3) `processed/<doc_id>.meta.json`,
> (4) Redis cache key, (5) hash-cache entry, (6) Postgres registry row,
> (7) `preloaded/<doc_name>` raw object. Idempotent and best-effort per step —
> every individual store failure is reported back to the caller, never raised.

## Quality Gates

Eight gate scripts under `scripts/gates/` enforce code and build quality:

| Gate         | Script            | What it checks                                                          |
| ------------ | ----------------- | ----------------------------------------------------------------------- |
| Static       | `static.sh`       | `ruff check`, `ruff format`, mypy, secrets scan, layer-isolation        |
| Unit         | `unit.sh`         | pytest pass, coverage thresholds, assertion density, layer test-files    |
| Contracts    | `contracts.sh`    | Every contract YAML maps to tests; every module has a contract          |
| DAG          | `dag.sh`          | `dag.yaml` acyclicity, node artifacts resolve, execution-log topology   |
| Build        | `build.sh`        | Wheel build, Docker build                                               |
| Supply-chain | `supply-chain.sh` | *(not yet configured)*                                                  |
| Integration  | `integration.sh`  | *(requires infrastructure)*                                             |
| E2E          | `e2e.sh`          | *(requires infrastructure)*                                             |

```bash
# Run all gates
for gate in scripts/gates/*.sh; do bash "$gate"; done

# Run a single gate
bash scripts/gates/static.sh
bash scripts/gates/unit.sh
```

## Testing

```bash
# Full test suite (553 tests)
uv run pytest

# Run with coverage
uv run pytest --cov=pageindex_mcp

# Single test file
uv run pytest tests/test_converters.py -v
```

## Project Structure

```
mcp_server.py                  # entry point — delegates to pageindex_mcp.server:main
preprocess_client.py           # batch processor for doc_store/ (hash-based dedup, subprocess)
gunicorn.conf.py               # production server config (uvicorn workers)
stress_test.py                 # load/stress harness
upload.py                      # legacy MCP client (targets removed process_document tool)
scripts/
  gates/                       # 8 quality gate scripts (static, unit, contracts, dag, build, ...)
  prebake_tessdata.sh          # pre-bake tessdata for offline workers
src/
  pageindex_mcp/
    server.py                  # FastMCP app composition, tool registration, main()
    config.py                  # settings loaded from env
    auth.py                    # BearerAuthMiddleware (401 gate; exempts /metrics, /upload)
    upload_app.py              # Upload API: POST /upload/files, GET /upload/status/{job_id}
    worker.py                  # arq worker: process_document_job, subprocess spawn, DLQ
    converters_cli.py          # converter subprocess entry point (fresh interpreter per job)
    converters.py              # PDF/Office → markdown extraction + tree build
    client.py                  # CustomPageIndexClient.index(); LLM provider abstraction
    cache.py                   # Redis read-through doc/job cache (tree → flat fallback)
    helpers.py                 # _rag / _prefilter_docs / _search_one_doc / validate_tree
    storage.py                 # MinIO read/write helpers
    registry.py                # Postgres document registry (RFC-006)
    registry_backfill.py       # MinIO → Postgres backfill (auto-runs on startup)
    memory_admission.py        # Redis-backed memory admission gate (worker backpressure)
    metrics.py                 # Prometheus metrics + /metrics response
    queue_metrics.py           # arq queue-depth Prometheus scrape loop
    tracing.py                 # Langfuse trace helpers (query + ingestion paths)
    hash_cache_migrate.py      # hash cache migration helper
    tools/
      documents.py             # 5 MCP query tools
```

## Architecture Notes

- **Vectorless tree RAG.** The `pageindex` library (installed from the private
  `trehansalil/PageIndex-salil` fork) builds a hierarchical index and does
  reasoning-based search over the tree — no vector database.
- **Markdown-first PDF extraction.** Docling (MIT) is the primary route;
  `pymupdf4llm` is the AGPL fallback (behind the `agpl-fallback` optional extra).
  Markdown-first fixes ligature-garbling seen in naive PDF text extraction.
  Document outlines are read via `pypdfium2` (BSD). Note: PyMuPDF/`pymupdf4llm`
  is **AGPL-3.0** (transitive) — serving it over a network is a legal decision to
  clear, not a settled safe-harbor.
- **Subprocess isolation.** Extraction runs in a fresh interpreter per job
  (`converters_cli.main`). Docling leaks ~237 MB RSS per document in-process, so the
  worker isolates it in a child process for OOM/leak containment.
- **Quality gate (HR5).** `validate_tree()` runs before `save_doc`. A failing
  (garbled) tree never persists — it surfaces a `low_quality_tree` error and
  lands in the DLQ. Flat-but-clean documents take the success route, not reject.
  The garble gate flags null bytes, replacement chars, `GLYPH<N>` placeholders
  (docling-parse's unmapped-glyph marker for symbolic/composite fonts — see
  [docling-project/docling#3802](https://github.com/docling-project/docling/issues/3802)),
  high PUA/digit/control-char ratios, sparse mojibake (localized Arabic-Latin
  script mixing), and single-token repetition. A garbled or image-dominant PDF
  gets one Tesseract OCR retry (`OCR_ESCALATION`), then optionally a VLM
  fallback (`VLM_FALLBACK`), before terminal reject.
- **VLM last-resort fallback (RFC-016).** When OCR escalation still produces
  garbled output, pages are rasterized at 200 DPI via pypdfium2 and sent to a
  vision-capable LLM. Config-gated off by default (`VLM_FALLBACK=false`). Uses
  the same ZDR-compliant client lever — no separate API key needed.
- **Document registry (RFC-006).** Postgres provides a durable catalog of processed
  documents. New documents are dual-written during ingestion. Both server and worker
  auto-backfill the registry from MinIO on startup.
- **LLM provider abstraction.** OpenAI, Azure, and any OpenAI-compatible endpoint are
  supported without code changes via `LLM_PROVIDER` / `OPENAI_BASE_URL`. The
  query path uses the OpenAI SDK; the ingestion path uses `litellm`.
- **Observability.** Prometheus scrapes `/metrics` (LLM, RAG, tool, MinIO,
  `low_quality_tree`, flat-doc, arq queue-depth counters). When configured, both LLM
  paths report cost traces to a single Langfuse project.
- **Memory admission gate.** The worker uses a Redis-backed memory admission gate
  to prevent OOM: new extraction jobs are held until the node has sufficient free
  RAM (`MEM_ADMISSION_FLOOR_BYTES`).
- PageIndex imports are deferred inside functions so the server module loads even
  when the library is not yet on `sys.path`; a local `PageIndex/` directory in the
  repo root is auto-added to `sys.path` when present (development checkouts).
