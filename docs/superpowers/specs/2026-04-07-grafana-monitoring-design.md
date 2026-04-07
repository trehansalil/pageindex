# PageIndex MCP Grafana Monitoring

Integrate pageindex-mcp into the existing Grafana + Prometheus stack running in the `hr-chatbot` namespace. No new monitoring infrastructure — reuse what's already deployed.

## Approach

Expose a `/metrics` endpoint on the existing Starlette app (port 8201) using `prometheus_client`. The hr-chatbot Prometheus scrapes it cross-namespace via static config. A new provisioned dashboard appears in the existing Grafana instance at `grafana-hr.saliltrehan.com`.

## Metrics Catalog

### Counters

| Metric | Labels | Description |
|--------|--------|-------------|
| `pageindex_tool_calls_total` | `tool` | Per-tool invocation count |
| `pageindex_tool_errors_total` | `tool` | Per-tool error count |
| `pageindex_uploads_total` | `status` (success/error) | Upload completions |
| `pageindex_rag_searches_total` | — | RAG search invocations |
| `pageindex_llm_calls_total` | — | OpenAI API calls |
| `pageindex_minio_operations_total` | `operation` (get/put/list/delete) | MinIO calls |

### Histograms

| Metric | Labels | Description |
|--------|--------|-------------|
| `pageindex_tool_duration_seconds` | `tool` | Tool call latency |
| `pageindex_upload_duration_seconds` | — | End-to-end upload processing time |
| `pageindex_rag_duration_seconds` | — | Full RAG pipeline latency |
| `pageindex_llm_duration_seconds` | — | Per-LLM-call latency |
| `pageindex_minio_duration_seconds` | `operation` | MinIO operation latency |

### Gauges

| Metric | Description |
|--------|-------------|
| `pageindex_documents_total` | Total indexed documents in MinIO |
| `pageindex_active_uploads` | In-flight upload jobs |

Built-in `process_*` metrics (memory, CPU, open FDs) are included automatically by `prometheus_client`.

## Instrumentation Points

### New file: `src/pageindex_mcp/metrics.py`

Defines all metric objects as module-level singletons. Exports a `metrics_response()` helper that calls `prometheus_client.generate_latest()` and returns a Starlette `Response` with content type `text/plain; version=0.0.4; charset=utf-8`.

### Modified files

**`pyproject.toml`** — Add `prometheus_client>=0.20.0` to dependencies.

**`server.py`** — Add a `/metrics` GET route to the Starlette app. Placed before the `/upload` mount so it's handled by the root app.

**`tools/documents.py`** — Each tool function gets timing/counting:
- Increment `tool_calls_total{tool=<name>}` on entry
- Observe `tool_duration_seconds{tool=<name>}` on exit
- Increment `tool_errors_total{tool=<name>}` on exception
- `recent_documents()` also sets `documents_total` gauge from the document count

**`upload_app.py`** — In `_process_file`:
- Increment/decrement `active_uploads` gauge
- Observe `upload_duration_seconds` on completion
- Increment `uploads_total{status=success|error}`

**`helpers.py`** — In `_llm()`:
- Observe `llm_duration_seconds`, increment `llm_calls_total`

In `_rag()`:
- Observe `rag_duration_seconds`, increment `rag_searches_total`

**`storage.py`** — Wrap MinIO-calling functions (`load_doc`, `save_doc`, `delete_doc`, `list_processed_docs`, `save_raw`, `load_hash_cache`, `save_hash_cache`):
- Observe `minio_duration_seconds{operation=get|put|list|delete}`
- Increment `minio_operations_total{operation=...}`

### Instrumentation style

Thin inline instrumentation using `time.monotonic()` diffs. No decorators or middleware abstractions. Each function gets 3-4 lines of timing/counting. Readable and greppable.

## Infrastructure Changes (hetzner-deployment-service)

All changes are in `apps/airline-hr-chatbot/configmap.yaml`. No new k8s resources.

### Prometheus scrape config

Add a new job to the existing `prometheus-config` ConfigMap:

```yaml
- job_name: "pageindex-mcp"
  metrics_path: /metrics
  static_configs:
    - targets: ["pageindex-mcp.pageindex-mcp.svc:8201"]
      labels:
        service: "pageindex-mcp"
```

### Grafana dashboard

Add a new entry `pageindex_mcp_overview.json` to the existing `grafana-dashboard-json` ConfigMap, alongside `hr_chatbot_overview.json`.

Dashboard rows:

| Row | Panels |
|-----|--------|
| Overview | Total documents (stat), Active uploads (stat), Request rate (stat), Error rate (stat) |
| Tool Performance | Per-tool call rate (time series), Per-tool latency p50/p95/p99 (time series) |
| Uploads | Upload rate by status (time series), Processing duration p50/p95 (time series) |
| RAG & LLM | Search rate (time series), RAG duration (time series), LLM call latency (time series), LLM call count (stat) |
| Storage | MinIO operation rate by type (time series), MinIO latency (time series) |
| System | Process resident memory (time series), Process CPU seconds rate (time series) |

All panels use the existing `prometheus` datasource UID. Time series panels default to 5m rate windows. Stat panels show instant values.

## What's already in place

- Promtail already scrapes `pageindex-mcp` namespace logs (configmap.yaml `kubernetes_sd_configs` includes `pageindex-mcp`)
- Grafana auto-provisions dashboards from the `grafana-dashboard-json` ConfigMap every 30s
- No new ports, Services, PVCs, Ingress rules, or DNS records needed
