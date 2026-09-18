# CLAUDE.md

## Identity

PageIndex MCP Server is a vectorless / tree-reasoning RAG document-ingestion platform exposed over the Model Context Protocol. The core stack is FastMCP + arq (async job queue) + MinIO (object storage) + Redis (cache + job bus) + Prometheus (metrics). It targets generic document corpora; German insurance T&C PDFs are the first validation vertical. Python 3.12, `uv` for dependency management. VCS: GitHub — CI via GitHub Actions (`.github/workflows/`).

## Hard Rules

1. **Never claim vectorless/tree RAG beats vector RAG on accuracy.** Benchmark numbers were refuted in verification. Position only on architectural merits: no vector DB, inspectable trees, structural-query alignment.
2. **Right-to-erasure must cascade across every derived store.** Deleting the raw upload does NOT auto-remove derivatives. Purge MinIO `uploads/`, `processed/*.json`, `processed/*.meta.json`, Redis cache, and any documented backup explicitly — in that order.
3. **Route PII-bearing documents only through a no-training + zero-retention LLM tier** (OpenAI ZDR / Anthropic ZDR / Azure modified-abuse-monitoring); EU residency where the corpus warrants. `OPENAI_BASE_URL` is the routing lever; a self-hosted model is the ultimate residency fallback.
4. **AGPL-3.0 awareness.** pymupdf4llm/PyMuPDF are AGPL-3.0 (transitive dep). Serving them over a network is a legal decision to clear, not a settled safe-harbor. The MIT escape is Docling.
5. **Never silently persist a low-quality tree.** `validate_tree()` must run before `save_doc`; a failing tree must surface as an arq `low_quality_tree` error, not a stored artifact.
6. **`/mem-search` always covers both claude-mem projects.** Every memory search must query `pageindex` **and** `pageindex_deployment` — never one alone, or half the history is silently invisible. The claude-mem `search` / `timeline` / `get_observations` tools take a single `project` string, so issue each call twice (in parallel) and merge/dedupe the results. Applies to all three layers of the workflow, not just the first search.


## Document Map

| Artifact | Look there for |
|---|---|
| `PRD.md` | Product Overview & Vision · Positioning & Differentiation · Target Users & Use Cases · Functional Requirements · Quality Bar & Acceptance Criteria · Non-Functional Requirements · Success Metrics · Out of Scope / Non-Goals · Open Questions & Risks |
| `ARCHITECTURE.md` | System Overview · Component Architecture · Ingestion Pipeline & Data Flow · PDF Extraction Strategy · Tree Quality Gate · Cross-Document Graph & Versioning · Data Model & Storage Layout (MinIO layout, env-var catalog) · Compliance & Data Residency · Observability · CI/CD · Architecture Decision Records · Risks & Thin-Evidence Flags |
| `DESIGN.md` | Design Scope · MCP Tool Contracts (the 5 registered query tools) · Upload & Job-Status API (`POST /upload/files`, `GET /upload/status/{job_id}`) · Output Schema & Machine-Consumability · Erasure / DSR Operation · Observability Surface · Honesty Notes & Open Items |

## Running Tests (read this before running the suite)

**Use `make test`. Never run the suite unbounded, and never in the background.**

On 2026-09-17 an agent ran `uv run pytest -q` detached (`run_in_background`),
with no `timeout` and no memory cap, and the workflow then ended. Nothing
reaped it. It grew to **9.7 GiB** (4.1 resident + 5.8 swapped) on this 7.6 GiB
host, drove free swap to 108 kB of 8 GB, and put the kernel into a 44-minute
thrash. The OOM killer then took traefik, the webhook and postgres twice —
kubelet gives pods `oom_score_adj` 992-1000 while a plain dev process sits at
0, so **the kernel sacrificed the cluster and never touched the process
responsible.** The host could not self-recover; it needed a manual restart.

- `make test` — the suite in its own cgroup scope: `MemoryMax` (default 3G,
  override `TEST_MEM_MAX`), `MemorySwapMax=0` so a leak fails fast instead of
  thrashing, and `oom_score_adj=900` so this process is the kernel's preferred
  victim ahead of k3s. Pass extra args via `PYTEST_ARGS`.
- `make test PYTEST_ARGS="tests/test_x.py -q"` — a subset, same protection.
- `make test-uncapped` — escape hatch for a host without systemd. Still
  `timeout`-wrapped. Never background it.

**For agents and subagents, these are hard rules:**
1. No `nohup`, no `&`, no `run_in_background` for a test run. Ever. If a
   foreground run is too slow, narrow the selection — do not detach it.
2. Every `pytest` invocation carries an explicit `timeout`. `make test`
   already does.
3. `pytest-timeout` is **not installed**; `--timeout=` will fail the run.
   Reaching for it and then dropping the wrapper is exactly what caused the
   outage above.
4. Swap is **not** in `/etc/fstab` by deliberate choice — it does not survive
   a reboot. Do not assume a cushion exists.

## Commands

```bash
# Dependencies
uv sync                              # install runtime deps
uv sync --extra dev                  # add pytest + httpx

# Local/remote toggles — resolve into .env.active, which everything else reads.
# Defaults: remote MinIO/Redis/Postgres + remote (Scaleway) Docling.
# See docs/ENV_PROFILES.md. `make help` lists every target.
make env-remote                      # snapshot the k3s infra namespace (needs kubectl)
make env                             # PROFILE=remote|local|hybrid, or MINIO=/DOCLING=/APP=
make up                              # server + worker as host processes, logs in .run/
make preflight                       # prove every remote hop before spending LLM budget
make ingest                          # ingest doc_store/  (or: make ingest-minio PREFIX=…)

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

