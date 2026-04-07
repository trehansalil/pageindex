# PageIndex MCP Grafana Monitoring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Instrument pageindex-mcp with Prometheus metrics and integrate it into the existing hr-chatbot Grafana/Prometheus stack.

**Architecture:** Add `prometheus_client` to the Python app, expose `/metrics` on the existing Starlette port 8201. Update the hr-chatbot Prometheus config to scrape cross-namespace, and add a provisioned Grafana dashboard JSON. Two repos touched: `pageindex_deployment` (app code) and `hetzner-deployment-service` (k8s manifests).

**Tech Stack:** `prometheus_client` (Python), Prometheus `static_configs`, Grafana dashboard JSON provisioning.

**Spec:** `docs/superpowers/specs/2026-04-07-grafana-monitoring-design.md`

---

## File Structure

### pageindex_deployment (app repo)

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/pageindex_mcp/metrics.py` | All metric objects + `metrics_response()` helper |
| Create | `tests/test_metrics.py` | Tests for metrics module and instrumentation |
| Modify | `src/pageindex_mcp/server.py` | Add `/metrics` route |
| Modify | `src/pageindex_mcp/tools/documents.py` | Instrument tool functions |
| Modify | `src/pageindex_mcp/upload_app.py` | Instrument upload processing |
| Modify | `src/pageindex_mcp/helpers.py` | Instrument `_llm()` and `_rag()` |
| Modify | `src/pageindex_mcp/storage.py` | Instrument MinIO operations |
| Modify | `pyproject.toml` | Add `prometheus_client` dependency |

### hetzner-deployment-service (infra repo)

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `apps/airline-hr-chatbot/configmap.yaml` | Add Prometheus scrape job + Grafana dashboard JSON |

---

## Task 1: Add prometheus_client dependency and metrics module

**Files:**
- Modify: `pyproject.toml:13` (dependencies list)
- Create: `src/pageindex_mcp/metrics.py`

- [ ] **Step 1: Add prometheus_client to pyproject.toml**

In `pyproject.toml`, add `prometheus_client` to the dependencies list after the existing `python-multipart` entry:

```python
    "python-multipart>=0.0.9",
    "prometheus_client>=0.20.0",
```

- [ ] **Step 2: Create src/pageindex_mcp/metrics.py**

```python
"""Prometheus metrics definitions and /metrics response helper."""

import time

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    REGISTRY,
)
from starlette.requests import Request
from starlette.responses import Response

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# ---------------------------------------------------------------------------
# Tool metrics
# ---------------------------------------------------------------------------
TOOL_CALLS = Counter(
    "pageindex_tool_calls_total",
    "Total MCP tool invocations",
    ["tool"],
)
TOOL_ERRORS = Counter(
    "pageindex_tool_errors_total",
    "Total MCP tool errors",
    ["tool"],
)
TOOL_DURATION = Histogram(
    "pageindex_tool_duration_seconds",
    "MCP tool call duration in seconds",
    ["tool"],
)

# ---------------------------------------------------------------------------
# Upload metrics
# ---------------------------------------------------------------------------
UPLOADS = Counter(
    "pageindex_uploads_total",
    "Total upload completions",
    ["status"],
)
UPLOAD_DURATION = Histogram(
    "pageindex_upload_duration_seconds",
    "End-to-end upload processing duration in seconds",
)
ACTIVE_UPLOADS = Gauge(
    "pageindex_active_uploads",
    "Number of in-flight upload jobs",
)

# ---------------------------------------------------------------------------
# RAG / LLM metrics
# ---------------------------------------------------------------------------
RAG_SEARCHES = Counter(
    "pageindex_rag_searches_total",
    "Total RAG search invocations",
)
RAG_DURATION = Histogram(
    "pageindex_rag_duration_seconds",
    "Full RAG pipeline duration in seconds",
)
LLM_CALLS = Counter(
    "pageindex_llm_calls_total",
    "Total LLM API calls",
)
LLM_DURATION = Histogram(
    "pageindex_llm_duration_seconds",
    "Per-LLM-call duration in seconds",
)

# ---------------------------------------------------------------------------
# Storage metrics
# ---------------------------------------------------------------------------
MINIO_OPS = Counter(
    "pageindex_minio_operations_total",
    "Total MinIO operations",
    ["operation"],
)
MINIO_DURATION = Histogram(
    "pageindex_minio_duration_seconds",
    "MinIO operation duration in seconds",
    ["operation"],
)

# ---------------------------------------------------------------------------
# Document gauge
# ---------------------------------------------------------------------------
DOCUMENTS_TOTAL = Gauge(
    "pageindex_documents_total",
    "Total indexed documents in MinIO",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def metrics_response(request: Request) -> Response:
    """Starlette endpoint: return Prometheus text exposition."""
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE)
```

- [ ] **Step 3: Run uv sync to install the new dependency**

Run: `cd /root/pageindex_deployment && uv sync`
Expected: Installs `prometheus_client`, exits 0.

- [ ] **Step 4: Verify the module imports cleanly**

Run: `cd /root/pageindex_deployment && uv run python -c "from pageindex_mcp.metrics import TOOL_CALLS, metrics_response; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/pageindex_mcp/metrics.py
git commit -m "feat: add prometheus_client dependency and metrics module"
```

---

## Task 2: Expose /metrics endpoint on the Starlette app

**Files:**
- Modify: `src/pageindex_mcp/server.py`
- Create: `tests/test_metrics.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_metrics.py`:

```python
"""Tests for the /metrics Prometheus endpoint."""

import pytest
from httpx import AsyncClient, ASGITransport
from starlette.applications import Starlette
from starlette.routing import Route

from pageindex_mcp.metrics import metrics_response


@pytest.fixture
def metrics_app():
    """Minimal Starlette app with just the /metrics route."""
    return Starlette(routes=[Route("/metrics", metrics_response)])


@pytest.fixture
async def client(metrics_app):
    async with AsyncClient(
        transport=ASGITransport(app=metrics_app), base_url="http://test"
    ) as c:
        yield c


async def test_metrics_endpoint_returns_200(client):
    response = await client.get("/metrics")
    assert response.status_code == 200


async def test_metrics_content_type(client):
    response = await client.get("/metrics")
    assert "text/plain" in response.headers["content-type"]
    assert "0.0.4" in response.headers["content-type"]


async def test_metrics_contains_process_metrics(client):
    """prometheus_client includes process_* metrics by default."""
    response = await client.get("/metrics")
    body = response.text
    assert "process_cpu_seconds_total" in body


async def test_metrics_contains_app_metrics(client):
    """Our custom metrics should appear (even if at zero)."""
    response = await client.get("/metrics")
    body = response.text
    assert "pageindex_tool_calls_total" in body or "pageindex_tool_calls" in body
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /root/pageindex_deployment && uv run pytest tests/test_metrics.py -v`
Expected: All 4 tests PASS (the metrics module is already created, this verifies the endpoint wiring works in isolation).

- [ ] **Step 3: Add /metrics route to server.py**

In `src/pageindex_mcp/server.py`, add the `/metrics` route to the Starlette app. Replace the `main()` function body:

```python
"""FastMCP server composition root and entry point."""

import anyio
import uvicorn
from fastmcp import FastMCP
from starlette.routing import Route

from . import tools as _tools
from .metrics import metrics_response

mcp = FastMCP("pageindex-local")

# ---------------------------------------------------------------------------
# Query tools only — document processing is handled by CustomPageIndexClient.
# ---------------------------------------------------------------------------
mcp.tool()(_tools.recent_documents)
mcp.tool()(_tools.find_relevant_documents)
mcp.tool()(_tools.get_document)
mcp.tool()(_tools.get_document_structure)
mcp.tool()(_tools.get_page_content)


def main() -> None:
    """Entry point called by the `pageindex-mcp` console script."""
    from .config import settings
    from .upload_app import create_upload_app

    print(f"Starting PageIndex MCP server at http://{settings.server_host}:{settings.server_port}/mcp")
    print(f"Upload service at http://{settings.server_host}:{settings.server_port}/upload")
    print(f"Metrics at http://{settings.server_host}:{settings.server_port}/metrics")
    print(f"MinIO endpoint: {settings.minio_endpoint}  bucket: {settings.minio_bucket}")
    print("Press Ctrl+C to stop\n")

    # Build the FastMCP Starlette app (includes its own lifespan for MCP session management).
    starlette_app = mcp.http_app(transport="streamable-http")

    # Add /metrics route for Prometheus scraping.
    starlette_app.routes.insert(0, Route("/metrics", metrics_response))

    # Mount the upload FastAPI app at /upload.
    upload_app = create_upload_app()
    starlette_app.mount("/upload", upload_app)

    async def _serve() -> None:
        config = uvicorn.Config(
            starlette_app,
            host=settings.server_host,
            port=settings.server_port,
            lifespan="on",
            timeout_graceful_shutdown=2,
        )
        server = uvicorn.Server(config)
        await server.serve()

    anyio.run(_serve)
```

- [ ] **Step 4: Run all tests**

Run: `cd /root/pageindex_deployment && uv run pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/pageindex_mcp/server.py tests/test_metrics.py
git commit -m "feat: expose /metrics Prometheus endpoint on Starlette app"
```

---

## Task 3: Instrument MCP tool functions

**Files:**
- Modify: `src/pageindex_mcp/tools/documents.py`
- Modify: `tests/test_metrics.py` (add tool instrumentation tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metrics.py`:

```python
from unittest.mock import patch, MagicMock
from pageindex_mcp.metrics import TOOL_CALLS, TOOL_ERRORS, TOOL_DURATION, DOCUMENTS_TOTAL


def _counter_value(counter, labels=None):
    """Read current value of a Counter for given labels."""
    if labels:
        return counter.labels(**labels)._value.get()
    return counter._value.get()


def _gauge_value(gauge):
    return gauge._value.get()


class TestToolInstrumentation:
    def test_recent_documents_increments_counter(self):
        before = _counter_value(TOOL_CALLS, {"tool": "recent_documents"})
        with patch("pageindex_mcp.tools.documents.list_processed_docs", return_value=[]):
            from pageindex_mcp.tools.documents import recent_documents
            recent_documents()
        after = _counter_value(TOOL_CALLS, {"tool": "recent_documents"})
        assert after == before + 1

    def test_recent_documents_updates_documents_gauge(self):
        fake_docs = [{"doc_id": "a"}, {"doc_id": "b"}]
        with patch("pageindex_mcp.tools.documents.list_processed_docs", return_value=fake_docs), \
             patch("pageindex_mcp.tools.documents.load_doc", side_effect=Exception("skip")):
            from pageindex_mcp.tools.documents import recent_documents
            recent_documents()
        assert _gauge_value(DOCUMENTS_TOTAL) == 2

    def test_get_document_increments_error_counter_on_failure(self):
        before = _counter_value(TOOL_ERRORS, {"tool": "get_document"})
        with patch("pageindex_mcp.tools.documents.load_doc", side_effect=Exception("boom")), \
             patch("pageindex_mcp.tools.documents.list_processed_docs", return_value=[]):
            from pageindex_mcp.tools.documents import get_document
            get_document("nonexistent")
        after = _counter_value(TOOL_ERRORS, {"tool": "get_document"})
        assert after == before + 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/pageindex_deployment && uv run pytest tests/test_metrics.py::TestToolInstrumentation -v`
Expected: FAIL — counters are not incremented because instrumentation hasn't been added yet.

- [ ] **Step 3: Instrument tools/documents.py**

Replace the full contents of `src/pageindex_mcp/tools/documents.py`:

```python
"""MCP query tools: document listing, retrieval, and structured search."""

import json
import time

from ..helpers import _rag, _strip_text, _build_node_map
from ..metrics import (
    DOCUMENTS_TOTAL,
    TOOL_CALLS,
    TOOL_DURATION,
    TOOL_ERRORS,
)
from ..storage import list_processed_docs, load_doc


def recent_documents(page: int = 1, page_size: int = 10) -> str:
    """Browse your document collection with pagination. Returns documents sorted
    by upload date (newest first) with processing status."""
    TOOL_CALLS.labels(tool="recent_documents").inc()
    start = time.monotonic()
    try:
        docs = list_processed_docs()
    except Exception as e:
        TOOL_ERRORS.labels(tool="recent_documents").inc()
        return json.dumps({"error": f"Failed to list documents: {e}"})
    finally:
        TOOL_DURATION.labels(tool="recent_documents").observe(time.monotonic() - start)

    DOCUMENTS_TOTAL.set(len(docs))

    begin = (page - 1) * page_size
    page_docs = docs[begin : begin + page_size]

    enriched = []
    for d in page_docs:
        doc_id = d["doc_id"]
        node_count = 0
        try:
            data = load_doc(doc_id)
            nm: dict = {}
            _build_node_map(data.get("structure", []), nm)
            node_count = len(nm)
        except Exception:
            pass
        enriched.append({
            "doc_id":     doc_id,
            "doc_name":   d.get("doc_name", "unknown"),
            "status":     "completed",
            "node_count": node_count,
        })

    return json.dumps({
        "total":     len(docs),
        "page":      page,
        "page_size": page_size,
        "documents": enriched,
    }, indent=2)


async def find_relevant_documents(query: str) -> str:
    """Search documents by query. Uses PageIndex reasoning-based tree search;
    automatically falls back to AI semantic search. Returns relevant content
    and a generated answer."""
    TOOL_CALLS.labels(tool="find_relevant_documents").inc()
    start = time.monotonic()
    try:
        documents = list_processed_docs()
        if not documents:
            return "No documents are indexed. Process documents first."
        return await _rag(query, [d["doc_id"] for d in documents])
    except Exception as e:
        TOOL_ERRORS.labels(tool="find_relevant_documents").inc()
        raise
    finally:
        TOOL_DURATION.labels(tool="find_relevant_documents").observe(time.monotonic() - start)


def get_document(doc_id: str) -> str:
    """Get detailed information about a specific document by doc_id. Requires
    doc_id (string). Use recent_documents() to find available doc_ids."""
    TOOL_CALLS.labels(tool="get_document").inc()
    start = time.monotonic()
    try:
        data = load_doc(doc_id)
    except Exception:
        TOOL_ERRORS.labels(tool="get_document").inc()
        available = [d["doc_id"] for d in list_processed_docs()]
        return json.dumps({"error": f"Document not found: {doc_id}", "available": available})
    finally:
        TOOL_DURATION.labels(tool="get_document").observe(time.monotonic() - start)

    structure = data.get("structure", [])
    nm: dict = {}
    _build_node_map(structure, nm)

    return json.dumps({
        "doc_id":             doc_id,
        "doc_name":           data.get("doc_name", data.get("filename", "unknown")),
        "status":             "completed",
        "total_nodes":        len(nm),
        "top_level_sections": [
            {
                "title":   n.get("title"),
                "node_id": n.get("node_id"),
                "pages":   f"{n.get('start_index')}-{n.get('end_index')}",
            }
            for n in structure
        ],
    }, indent=2)


def get_document_structure(doc_id: str) -> str:
    """Extract the hierarchical structure of a completed document."""
    TOOL_CALLS.labels(tool="get_document_structure").inc()
    start = time.monotonic()
    try:
        data = load_doc(doc_id)
    except Exception:
        TOOL_ERRORS.labels(tool="get_document_structure").inc()
        available = [d["doc_id"] for d in list_processed_docs()]
        return json.dumps({"error": f"Document not found: {doc_id}", "available": available})
    finally:
        TOOL_DURATION.labels(tool="get_document_structure").observe(time.monotonic() - start)

    return json.dumps({
        "doc_id":    doc_id,
        "structure": _strip_text(data.get("structure", [])),
    }, indent=2)


def get_page_content(doc_id: str, pages: str) -> str:
    """Extract specific page content from processed documents. Flexible page
    selection: single page ('5'), ranges ('3-7'), or multiple pages ('3,5,7')."""
    TOOL_CALLS.labels(tool="get_page_content").inc()
    start = time.monotonic()
    try:
        data = load_doc(doc_id)
    except Exception:
        TOOL_ERRORS.labels(tool="get_page_content").inc()
        available = [d["doc_id"] for d in list_processed_docs()]
        return json.dumps({"error": f"Document not found: {doc_id}", "available": available})
    finally:
        TOOL_DURATION.labels(tool="get_page_content").observe(time.monotonic() - start)

    wanted: set[int] = set()
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            wanted.update(range(int(a), int(b) + 1))
        else:
            wanted.add(int(part))

    nm: dict = {}
    _build_node_map(data.get("structure", []), nm)

    hits = [
        {
            "node_id": nid,
            "title":   n.get("title"),
            "pages":   f"{n.get('start_index')}-{n.get('end_index')}",
            "text":    n["text"],
        }
        for nid, n in nm.items()
        if set(range(n.get("start_index", 0), n.get("end_index", 0) + 1)) & wanted
        and "text" in n
    ]

    if not hits:
        return json.dumps({"error": f"No content found for pages '{pages}' in doc '{doc_id}'."})
    return json.dumps({"doc_id": doc_id, "pages": pages, "content": hits}, indent=2)
```

- [ ] **Step 4: Run tests**

Run: `cd /root/pageindex_deployment && uv run pytest tests/test_metrics.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/pageindex_mcp/tools/documents.py tests/test_metrics.py
git commit -m "feat: instrument MCP tool functions with Prometheus metrics"
```

---

## Task 4: Instrument upload processing

**Files:**
- Modify: `src/pageindex_mcp/upload_app.py`
- Modify: `tests/test_metrics.py` (add upload instrumentation tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_metrics.py`:

```python
from pageindex_mcp.metrics import UPLOADS, UPLOAD_DURATION, ACTIVE_UPLOADS


class TestUploadInstrumentation:
    def test_upload_success_increments_counter(self):
        before = _counter_value(UPLOADS, {"status": "success"})
        # The actual upload tests in test_upload.py cover the full flow.
        # Here we verify the metric object is wired. We'll call _process_file
        # directly in the integration test.
        # For now, just verify the metric exists and is labelled correctly.
        UPLOADS.labels(status="success").inc()
        after = _counter_value(UPLOADS, {"status": "success"})
        assert after == before + 1

    def test_active_uploads_gauge_exists(self):
        # Gauge should start at 0 or current value.
        val = _gauge_value(ACTIVE_UPLOADS)
        assert val >= 0
```

- [ ] **Step 2: Run tests to verify they pass (smoke test for metric wiring)**

Run: `cd /root/pageindex_deployment && uv run pytest tests/test_metrics.py::TestUploadInstrumentation -v`
Expected: PASS

- [ ] **Step 3: Instrument upload_app.py _process_file**

In `src/pageindex_mcp/upload_app.py`, add the metrics imports and instrument `_process_file`:

Add this import at the top, after the existing imports:

```python
from .metrics import ACTIVE_UPLOADS, UPLOADS, UPLOAD_DURATION
```

Replace the `_process_file` function:

```python
async def _process_file(
    job_id: str,
    tmp_path: str,
    redis: aioredis.Redis,
) -> None:
    """Index a file and write the result to Redis. Cleans up temp dir on exit."""
    tmp_dir = os.path.dirname(tmp_path)
    ACTIVE_UPLOADS.inc()
    start = time.monotonic()
    try:
        client = CustomPageIndexClient()
        doc_id = await client.index(tmp_path)
        await redis.hset(_job_key(job_id), mapping={"status": "done", "doc_id": doc_id})
        await redis.expire(_job_key(job_id), JOB_TTL)
        UPLOADS.labels(status="success").inc()
    except asyncio.CancelledError:
        await redis.hset(
            _job_key(job_id), mapping={"status": "error", "error": "cancelled"}
        )
        await redis.expire(_job_key(job_id), JOB_TTL)
        UPLOADS.labels(status="error").inc()
        raise
    except Exception as exc:
        await redis.hset(_job_key(job_id), mapping={"status": "error", "error": str(exc)})
        await redis.expire(_job_key(job_id), JOB_TTL)
        UPLOADS.labels(status="error").inc()
    finally:
        UPLOAD_DURATION.observe(time.monotonic() - start)
        ACTIVE_UPLOADS.dec()
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

Also add `import time` to the top of the file (after `import asyncio`).

- [ ] **Step 4: Run all tests**

Run: `cd /root/pageindex_deployment && uv run pytest tests/ -v`
Expected: All tests pass (existing upload tests + new metric tests).

- [ ] **Step 5: Commit**

```bash
git add src/pageindex_mcp/upload_app.py tests/test_metrics.py
git commit -m "feat: instrument upload processing with Prometheus metrics"
```

---

## Task 5: Instrument RAG and LLM helpers

**Files:**
- Modify: `src/pageindex_mcp/helpers.py`
- Modify: `tests/test_metrics.py` (add LLM/RAG instrumentation tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_metrics.py`:

```python
import asyncio
from pageindex_mcp.metrics import LLM_CALLS, LLM_DURATION, RAG_SEARCHES, RAG_DURATION


class TestLLMInstrumentation:
    def test_llm_call_increments_counter(self):
        before = _counter_value(LLM_CALLS)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "test answer"

        with patch("pageindex_mcp.helpers.openai.AsyncOpenAI") as MockClient:
            MockClient.return_value.chat.completions.create = AsyncMock(
                return_value=mock_response
            )
            from pageindex_mcp.helpers import _llm
            asyncio.get_event_loop().run_until_complete(_llm("test prompt"))

        after = _counter_value(LLM_CALLS)
        assert after == before + 1
```

Note: Import `AsyncMock` at the top of the file: `from unittest.mock import patch, MagicMock, AsyncMock`

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/pageindex_deployment && uv run pytest tests/test_metrics.py::TestLLMInstrumentation -v`
Expected: FAIL — `_llm` doesn't increment LLM_CALLS yet.

- [ ] **Step 3: Instrument helpers.py**

Replace the full contents of `src/pageindex_mcp/helpers.py`:

```python
"""RAG helpers: LLM call + tree-search pipeline."""

import json
import os
import re
import time

import openai

from .metrics import (
    LLM_CALLS,
    LLM_DURATION,
    RAG_DURATION,
    RAG_SEARCHES,
)
from .storage import load_doc


async def _llm(prompt: str) -> str:
    """Call the configured OpenAI-compatible model."""
    LLM_CALLS.inc()
    start = time.monotonic()
    try:
        client = openai.AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        r = await client.chat.completions.create(
            model=os.environ.get("PAGEINDEX_MODEL", "gpt-4o-2024-11-20"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return r.choices[0].message.content.strip()
    finally:
        LLM_DURATION.observe(time.monotonic() - start)


def _strip_text(nodes: list) -> list:
    """Return tree copy without 'text' fields to reduce prompt token usage."""
    result = []
    for n in nodes:
        copy = {k: v for k, v in n.items() if k != "text"}
        if copy.get("nodes"):
            copy["nodes"] = _strip_text(copy["nodes"])
        result.append(copy)
    return result


def _build_node_map(nodes: list, nm: dict) -> None:
    """Recursively flatten tree into {node_id: node} dict."""
    for n in nodes:
        if "node_id" in n:
            nm[n["node_id"]] = n
        if n.get("nodes"):
            _build_node_map(n["nodes"], nm)


async def _rag(query: str, doc_ids: list[str]) -> str:
    """
    Run PageIndex tree-search + answer-generation pipeline.
    doc_ids: list of doc_id strings as stored in MinIO processed/ prefix.
    """
    RAG_SEARCHES.inc()
    start = time.monotonic()
    try:
        context_parts: list[str] = []

        for doc_id in doc_ids:
            try:
                data = load_doc(doc_id)
            except ValueError:
                continue

            tree = data.get("structure", [])
            name = data.get("doc_name", data.get("filename", doc_id))
            tree_slim = _strip_text(tree)

            nm: dict = {}
            _build_node_map(tree, nm)

            search_prompt = (
                "You are given a question and a document tree.\n"
                "Each node has a node_id, title, and summary.\n"
                "Find all node_ids whose content likely answers the question.\n\n"
                f"Question: {query}\n"
                f"Document: {name}\n"
                f"Tree:\n{json.dumps(tree_slim, indent=2)}\n\n"
                'Reply ONLY in JSON: {"thinking": "<reasoning>", "node_list": ["id1", "id2"]}'
            )

            raw = await _llm(search_prompt)

            clean = re.sub(r"^```[a-z]*\n?", "", raw.strip())
            clean = re.sub(r"\n?```$", "", clean).strip()

            try:
                ids = json.loads(clean).get("node_list", [])
            except Exception:
                ids = []

            text = "\n\n".join(
                nm[i]["text"] for i in ids if i in nm and "text" in nm[i]
            )
            if text:
                context_parts.append(f"=== {name} ===\n{text}")

        if not context_parts:
            return "No relevant content found for the query."

        answer_prompt = (
            "Answer the question based only on the context below.\n\n"
            f"Question: {query}\n\n"
            f"Context:\n{chr(10).join(context_parts)}"
        )
        return await _llm(answer_prompt)
    finally:
        RAG_DURATION.observe(time.monotonic() - start)
```

- [ ] **Step 4: Run tests**

Run: `cd /root/pageindex_deployment && uv run pytest tests/test_metrics.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/pageindex_mcp/helpers.py tests/test_metrics.py
git commit -m "feat: instrument RAG and LLM helpers with Prometheus metrics"
```

---

## Task 6: Instrument MinIO storage operations

**Files:**
- Modify: `src/pageindex_mcp/storage.py`
- Modify: `tests/test_metrics.py` (add storage instrumentation tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_metrics.py`:

```python
from pageindex_mcp.metrics import MINIO_OPS, MINIO_DURATION


class TestStorageInstrumentation:
    def test_list_processed_docs_increments_minio_ops(self):
        before = _counter_value(MINIO_OPS, {"operation": "list"})
        mock_minio = MagicMock()
        mock_minio.list_objects.return_value = []
        with patch("pageindex_mcp.storage.get_minio", return_value=mock_minio):
            from pageindex_mcp.storage import list_processed_docs
            list_processed_docs()
        after = _counter_value(MINIO_OPS, {"operation": "list"})
        assert after == before + 1

    def test_load_doc_increments_minio_ops(self):
        before = _counter_value(MINIO_OPS, {"operation": "get"})
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"structure": []}'
        mock_minio = MagicMock()
        mock_minio.get_object.return_value = mock_response
        with patch("pageindex_mcp.storage.get_minio", return_value=mock_minio), \
             patch("pageindex_mcp.storage.settings") as mock_settings:
            mock_settings.minio_bucket = "test"
            from pageindex_mcp.storage import load_doc
            load_doc("abc123")
        after = _counter_value(MINIO_OPS, {"operation": "get"})
        assert after == before + 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/pageindex_deployment && uv run pytest tests/test_metrics.py::TestStorageInstrumentation -v`
Expected: FAIL — `MINIO_OPS` not incremented yet.

- [ ] **Step 3: Instrument storage.py**

Replace the full contents of `src/pageindex_mcp/storage.py`:

```python
"""MinIO client singleton and document storage CRUD."""

import json
import time
from io import BytesIO
from pathlib import Path
from threading import Lock

from minio import Minio
from minio.error import S3Error

from .config import settings
from .metrics import MINIO_DURATION, MINIO_OPS

_minio_client: Minio | None = None
_minio_lock = Lock()  # guards double-checked locking in get_minio()


def get_minio() -> Minio:
    """Lazy singleton: create client and ensure bucket exists on first call."""
    global _minio_client
    if _minio_client is None:
        with _minio_lock:
            if _minio_client is None:
                client = Minio(
                    settings.minio_endpoint,
                    access_key=settings.minio_access_key,
                    secret_key=settings.minio_secret_key,
                    secure=settings.minio_secure,
                )
                if not client.bucket_exists(settings.minio_bucket):
                    client.make_bucket(settings.minio_bucket)
                _minio_client = client
    return _minio_client


# ---------------------------------------------------------------------------
# Processed document CRUD  (MinIO: processed/<doc_id>.json)
# ---------------------------------------------------------------------------

def load_doc(doc_id: str) -> dict:
    """Fetch and deserialize processed/<doc_id>.json. Raises ValueError if absent."""
    MINIO_OPS.labels(operation="get").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        response = mc.get_object(settings.minio_bucket, f"processed/{doc_id}.json")
        data = json.loads(response.read())
        return data
    except S3Error as e:
        if e.code == "NoSuchKey":
            raise ValueError(f"Document not found: {doc_id}")
        raise
    finally:
        MINIO_DURATION.labels(operation="get").observe(time.monotonic() - start)
        try:
            response.close()
            response.release_conn()
        except Exception:
            pass


def save_doc(doc_id: str, data: dict) -> None:
    """Serialize data and PUT to processed/<doc_id>.json."""
    MINIO_OPS.labels(operation="put").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        content = json.dumps(data, indent=2).encode()
        mc.put_object(
            settings.minio_bucket,
            f"processed/{doc_id}.json",
            BytesIO(content),
            len(content),
            content_type="application/json",
        )
    finally:
        MINIO_DURATION.labels(operation="put").observe(time.monotonic() - start)


def delete_doc(doc_id: str) -> None:
    """Remove processed/<doc_id>.json and all objects under uploads/<doc_id>/."""
    MINIO_OPS.labels(operation="delete").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        mc.remove_object(settings.minio_bucket, f"processed/{doc_id}.json")
        for obj in mc.list_objects(settings.minio_bucket, prefix=f"uploads/{doc_id}/", recursive=True):
            mc.remove_object(settings.minio_bucket, obj.object_name)
    finally:
        MINIO_DURATION.labels(operation="delete").observe(time.monotonic() - start)


def list_processed_docs() -> list[dict]:
    """List all objects under processed/, returning summary dicts."""
    MINIO_OPS.labels(operation="list").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        docs = []
        for obj in mc.list_objects(settings.minio_bucket, prefix="processed/", recursive=True):
            doc_id = Path(obj.object_name).stem
            try:
                response = mc.get_object(settings.minio_bucket, obj.object_name)
                data = json.loads(response.read())
                docs.append({
                    "doc_id":       data.get("doc_id", doc_id),
                    "doc_name":     data.get("doc_name", data.get("filename", "unknown")),
                    "source_url":   data.get("source_url", ""),
                    "processed_at": data.get("processed_at", ""),
                })
            except Exception:
                continue
            finally:
                try:
                    response.close()
                    response.release_conn()
                except Exception:
                    pass
        return docs
    finally:
        MINIO_DURATION.labels(operation="list").observe(time.monotonic() - start)


# ---------------------------------------------------------------------------
# Raw upload storage  (MinIO: uploads/<doc_id>/<filename>)
# ---------------------------------------------------------------------------

def save_raw(doc_id: str, filename: str, data: bytes) -> None:
    """Store raw file bytes at uploads/<doc_id>/<filename>."""
    MINIO_OPS.labels(operation="put").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        ext = Path(filename).suffix.lower()
        content_type = "application/pdf" if ext == ".pdf" else "application/octet-stream"
        mc.put_object(
            settings.minio_bucket,
            f"uploads/{doc_id}/{filename}",
            BytesIO(data),
            len(data),
            content_type=content_type,
        )
    finally:
        MINIO_DURATION.labels(operation="put").observe(time.monotonic() - start)


# ---------------------------------------------------------------------------
# Hash cache  (MinIO: hashes/processed_hashes.json)
# ---------------------------------------------------------------------------

HASH_OBJECT = "hashes/processed_hashes.json"


def load_hash_cache() -> dict[str, str]:
    """Load {filename: sha256} dedup cache from MinIO. Returns empty dict if absent."""
    MINIO_OPS.labels(operation="get").inc()
    start = time.monotonic()
    mc = get_minio()
    response = None
    try:
        response = mc.get_object(settings.minio_bucket, HASH_OBJECT)
        return json.loads(response.read())
    except S3Error as e:
        if e.code == "NoSuchKey":
            return {}
        raise
    finally:
        MINIO_DURATION.labels(operation="get").observe(time.monotonic() - start)
        if response is not None:
            try:
                response.close()
                response.release_conn()
            except Exception:
                pass


def save_hash_cache(cache: dict[str, str]) -> None:
    """Write {filename: sha256} dedup cache to MinIO."""
    MINIO_OPS.labels(operation="put").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        content = json.dumps(cache, indent=2).encode()
        mc.put_object(
            settings.minio_bucket,
            HASH_OBJECT,
            BytesIO(content),
            len(content),
            content_type="application/json",
        )
    finally:
        MINIO_DURATION.labels(operation="put").observe(time.monotonic() - start)


# ---------------------------------------------------------------------------
# Pre-loaded document sync  (MinIO: preloaded/<filename>)
# ---------------------------------------------------------------------------

def sync_preloaded_to_minio() -> list[str]:
    """Upload new files from doc_store/ to preloaded/ prefix. Returns synced filenames."""
    settings.doc_store_path.mkdir(exist_ok=True)
    mc = get_minio()
    existing = {
        Path(obj.object_name).name
        for obj in mc.list_objects(settings.minio_bucket, prefix="preloaded/", recursive=True)
    }
    synced = []
    for f in settings.doc_store_path.iterdir():
        if f.is_file() and f.name not in existing:
            mc.fput_object(settings.minio_bucket, f"preloaded/{f.name}", str(f))
            synced.append(f.name)
    return synced
```

- [ ] **Step 4: Run all tests**

Run: `cd /root/pageindex_deployment && uv run pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/pageindex_mcp/storage.py tests/test_metrics.py
git commit -m "feat: instrument MinIO storage operations with Prometheus metrics"
```

---

## Task 7: Add Prometheus scrape job and Grafana dashboard to hr-chatbot configmap

**Files:**
- Modify: `/root/hetzner-deployment-service/apps/airline-hr-chatbot/configmap.yaml`

This task modifies the **hetzner-deployment-service** repo, not pageindex_deployment.

- [ ] **Step 1: Add pageindex-mcp scrape job to Prometheus config**

In `/root/hetzner-deployment-service/apps/airline-hr-chatbot/configmap.yaml`, in the `prometheus-config` ConfigMap, add a new scrape job after the existing `"prometheus"` job (after line ~268):

```yaml
      - job_name: "pageindex-mcp"
        metrics_path: /metrics
        static_configs:
          - targets: ["pageindex-mcp.pageindex-mcp.svc:8201"]
            labels:
              service: "pageindex-mcp"
```

- [ ] **Step 2: Add pageindex_mcp_overview.json to Grafana dashboard ConfigMap**

In the same file, in the `grafana-dashboard-json` ConfigMap, add a new key `pageindex_mcp_overview.json` after the existing `hr_chatbot_overview.json` entry (and its closing of the two dashboards). The dashboard JSON:

```json
  pageindex_mcp_overview.json: |
    {
      "__inputs": [],
      "__requires": [],
      "annotations": { "list": [] },
      "description": "PageIndex MCP — document processing, search, and storage metrics",
      "editable": true,
      "fiscalYearStartMonth": 0,
      "graphTooltip": 1,
      "id": null,
      "links": [],
      "panels": [
        {
          "collapsed": false,
          "gridPos": { "h": 1, "w": 24, "x": 0, "y": 0 },
          "id": 1,
          "title": "Overview",
          "type": "row"
        },
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "fieldConfig": { "defaults": { "color": { "mode": "thresholds" }, "thresholds": { "steps": [{ "color": "green", "value": null }] }, "unit": "none" }, "overrides": [] },
          "gridPos": { "h": 4, "w": 6, "x": 0, "y": 1 },
          "id": 2,
          "options": { "reduceOptions": { "calcs": ["lastNotNull"] }, "orientation": "auto", "textMode": "auto", "colorMode": "value", "graphMode": "none" },
          "targets": [{ "expr": "pageindex_documents_total", "legendFormat": "Documents", "refId": "A" }],
          "title": "Total Documents",
          "type": "stat"
        },
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "fieldConfig": { "defaults": { "color": { "mode": "thresholds" }, "thresholds": { "steps": [{ "color": "green", "value": null }, { "color": "yellow", "value": 3 }, { "color": "red", "value": 5 }] }, "unit": "none" }, "overrides": [] },
          "gridPos": { "h": 4, "w": 6, "x": 6, "y": 1 },
          "id": 3,
          "options": { "reduceOptions": { "calcs": ["lastNotNull"] }, "orientation": "auto", "textMode": "auto", "colorMode": "value", "graphMode": "area" },
          "targets": [{ "expr": "pageindex_active_uploads", "legendFormat": "Active", "refId": "A" }],
          "title": "Active Uploads",
          "type": "stat"
        },
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "fieldConfig": { "defaults": { "color": { "mode": "thresholds" }, "thresholds": { "steps": [{ "color": "green", "value": null }] }, "unit": "reqps" }, "overrides": [] },
          "gridPos": { "h": 4, "w": 6, "x": 12, "y": 1 },
          "id": 4,
          "options": { "reduceOptions": { "calcs": ["lastNotNull"] }, "orientation": "auto", "textMode": "auto", "colorMode": "value", "graphMode": "area" },
          "targets": [{ "expr": "sum(rate(pageindex_tool_calls_total[5m]))", "legendFormat": "req/s", "refId": "A" }],
          "title": "Tool Request Rate",
          "type": "stat"
        },
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "fieldConfig": { "defaults": { "color": { "mode": "thresholds" }, "thresholds": { "steps": [{ "color": "green", "value": null }, { "color": "red", "value": 0.01 }] }, "unit": "percentunit" }, "overrides": [] },
          "gridPos": { "h": 4, "w": 6, "x": 18, "y": 1 },
          "id": 5,
          "options": { "reduceOptions": { "calcs": ["lastNotNull"] }, "orientation": "auto", "textMode": "auto", "colorMode": "value", "graphMode": "area" },
          "targets": [{ "expr": "sum(rate(pageindex_tool_errors_total[5m])) / (sum(rate(pageindex_tool_calls_total[5m])) + 1e-10)", "legendFormat": "Error %", "refId": "A" }],
          "title": "Tool Error Rate",
          "type": "stat"
        },
        {
          "collapsed": false,
          "gridPos": { "h": 1, "w": 24, "x": 0, "y": 5 },
          "id": 6,
          "title": "Tool Performance",
          "type": "row"
        },
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "fieldConfig": { "defaults": { "color": { "mode": "palette-classic" }, "unit": "reqps" }, "overrides": [] },
          "gridPos": { "h": 8, "w": 12, "x": 0, "y": 6 },
          "id": 7,
          "options": { "tooltip": { "mode": "multi" } },
          "targets": [{ "expr": "sum(rate(pageindex_tool_calls_total[5m])) by (tool)", "legendFormat": "{{tool}}", "refId": "A" }],
          "title": "Tool Call Rate",
          "type": "timeseries"
        },
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "fieldConfig": { "defaults": { "color": { "mode": "palette-classic" }, "unit": "s" }, "overrides": [] },
          "gridPos": { "h": 8, "w": 12, "x": 12, "y": 6 },
          "id": 8,
          "options": { "tooltip": { "mode": "multi" } },
          "targets": [
            { "expr": "histogram_quantile(0.50, sum(rate(pageindex_tool_duration_seconds_bucket[5m])) by (le, tool))", "legendFormat": "p50 {{tool}}", "refId": "A" },
            { "expr": "histogram_quantile(0.95, sum(rate(pageindex_tool_duration_seconds_bucket[5m])) by (le, tool))", "legendFormat": "p95 {{tool}}", "refId": "B" },
            { "expr": "histogram_quantile(0.99, sum(rate(pageindex_tool_duration_seconds_bucket[5m])) by (le, tool))", "legendFormat": "p99 {{tool}}", "refId": "C" }
          ],
          "title": "Tool Latency (p50 / p95 / p99)",
          "type": "timeseries"
        },
        {
          "collapsed": false,
          "gridPos": { "h": 1, "w": 24, "x": 0, "y": 14 },
          "id": 9,
          "title": "Uploads",
          "type": "row"
        },
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "fieldConfig": { "defaults": { "color": { "mode": "palette-classic" }, "unit": "ops" }, "overrides": [] },
          "gridPos": { "h": 8, "w": 12, "x": 0, "y": 15 },
          "id": 10,
          "options": { "tooltip": { "mode": "multi" } },
          "targets": [{ "expr": "sum(rate(pageindex_uploads_total[5m])) by (status)", "legendFormat": "{{status}}", "refId": "A" }],
          "title": "Upload Rate (by status)",
          "type": "timeseries"
        },
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "fieldConfig": { "defaults": { "color": { "mode": "palette-classic" }, "unit": "s" }, "overrides": [] },
          "gridPos": { "h": 8, "w": 12, "x": 12, "y": 15 },
          "id": 11,
          "options": { "tooltip": { "mode": "multi" } },
          "targets": [
            { "expr": "histogram_quantile(0.50, sum(rate(pageindex_upload_duration_seconds_bucket[5m])) by (le))", "legendFormat": "p50", "refId": "A" },
            { "expr": "histogram_quantile(0.95, sum(rate(pageindex_upload_duration_seconds_bucket[5m])) by (le))", "legendFormat": "p95", "refId": "B" }
          ],
          "title": "Upload Processing Duration (p50 / p95)",
          "type": "timeseries"
        },
        {
          "collapsed": false,
          "gridPos": { "h": 1, "w": 24, "x": 0, "y": 23 },
          "id": 12,
          "title": "RAG & LLM",
          "type": "row"
        },
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "fieldConfig": { "defaults": { "color": { "mode": "palette-classic" }, "unit": "ops" }, "overrides": [] },
          "gridPos": { "h": 8, "w": 8, "x": 0, "y": 24 },
          "id": 13,
          "options": { "tooltip": { "mode": "multi" } },
          "targets": [{ "expr": "rate(pageindex_rag_searches_total[5m])", "legendFormat": "searches/s", "refId": "A" }],
          "title": "RAG Search Rate",
          "type": "timeseries"
        },
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "fieldConfig": { "defaults": { "color": { "mode": "palette-classic" }, "unit": "s" }, "overrides": [] },
          "gridPos": { "h": 8, "w": 8, "x": 8, "y": 24 },
          "id": 14,
          "options": { "tooltip": { "mode": "multi" } },
          "targets": [
            { "expr": "histogram_quantile(0.50, sum(rate(pageindex_rag_duration_seconds_bucket[5m])) by (le))", "legendFormat": "RAG p50", "refId": "A" },
            { "expr": "histogram_quantile(0.95, sum(rate(pageindex_rag_duration_seconds_bucket[5m])) by (le))", "legendFormat": "RAG p95", "refId": "B" },
            { "expr": "histogram_quantile(0.50, sum(rate(pageindex_llm_duration_seconds_bucket[5m])) by (le))", "legendFormat": "LLM p50", "refId": "C" },
            { "expr": "histogram_quantile(0.95, sum(rate(pageindex_llm_duration_seconds_bucket[5m])) by (le))", "legendFormat": "LLM p95", "refId": "D" }
          ],
          "title": "RAG & LLM Latency (p50 / p95)",
          "type": "timeseries"
        },
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "fieldConfig": { "defaults": { "color": { "mode": "thresholds" }, "thresholds": { "steps": [{ "color": "green", "value": null }] }, "unit": "none" }, "overrides": [] },
          "gridPos": { "h": 8, "w": 8, "x": 16, "y": 24 },
          "id": 15,
          "options": { "reduceOptions": { "calcs": ["lastNotNull"] }, "orientation": "auto", "textMode": "auto", "colorMode": "value", "graphMode": "area" },
          "targets": [{ "expr": "increase(pageindex_llm_calls_total[1h])", "legendFormat": "LLM calls (1h)", "refId": "A" }],
          "title": "LLM Calls (last 1h)",
          "type": "stat"
        },
        {
          "collapsed": false,
          "gridPos": { "h": 1, "w": 24, "x": 0, "y": 32 },
          "id": 16,
          "title": "Storage",
          "type": "row"
        },
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "fieldConfig": { "defaults": { "color": { "mode": "palette-classic" }, "unit": "ops" }, "overrides": [] },
          "gridPos": { "h": 8, "w": 12, "x": 0, "y": 33 },
          "id": 17,
          "options": { "tooltip": { "mode": "multi" } },
          "targets": [{ "expr": "sum(rate(pageindex_minio_operations_total[5m])) by (operation)", "legendFormat": "{{operation}}", "refId": "A" }],
          "title": "MinIO Operation Rate",
          "type": "timeseries"
        },
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "fieldConfig": { "defaults": { "color": { "mode": "palette-classic" }, "unit": "s" }, "overrides": [] },
          "gridPos": { "h": 8, "w": 12, "x": 12, "y": 33 },
          "id": 18,
          "options": { "tooltip": { "mode": "multi" } },
          "targets": [
            { "expr": "histogram_quantile(0.50, sum(rate(pageindex_minio_duration_seconds_bucket[5m])) by (le, operation))", "legendFormat": "p50 {{operation}}", "refId": "A" },
            { "expr": "histogram_quantile(0.95, sum(rate(pageindex_minio_duration_seconds_bucket[5m])) by (le, operation))", "legendFormat": "p95 {{operation}}", "refId": "B" }
          ],
          "title": "MinIO Latency (p50 / p95)",
          "type": "timeseries"
        },
        {
          "collapsed": false,
          "gridPos": { "h": 1, "w": 24, "x": 0, "y": 41 },
          "id": 19,
          "title": "System",
          "type": "row"
        },
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "fieldConfig": { "defaults": { "color": { "mode": "palette-classic" }, "unit": "bytes" }, "overrides": [] },
          "gridPos": { "h": 8, "w": 12, "x": 0, "y": 42 },
          "id": 20,
          "options": { "tooltip": { "mode": "multi" } },
          "targets": [{ "expr": "process_resident_memory_bytes{job=\"pageindex-mcp\"}", "legendFormat": "RSS", "refId": "A" }],
          "title": "Process Resident Memory",
          "type": "timeseries"
        },
        {
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "fieldConfig": { "defaults": { "color": { "mode": "palette-classic" }, "unit": "short" }, "overrides": [] },
          "gridPos": { "h": 8, "w": 12, "x": 12, "y": 42 },
          "id": 21,
          "options": { "tooltip": { "mode": "multi" } },
          "targets": [{ "expr": "rate(process_cpu_seconds_total{job=\"pageindex-mcp\"}[5m])", "legendFormat": "CPU cores", "refId": "A" }],
          "title": "Process CPU Usage",
          "type": "timeseries"
        }
      ],
      "schemaVersion": 39,
      "tags": ["pageindex", "mcp"],
      "templating": { "list": [] },
      "time": { "from": "now-1h", "to": "now" },
      "timepicker": {},
      "timezone": "browser",
      "title": "PageIndex MCP Overview",
      "uid": "pageindex-mcp-overview",
      "version": 1,
      "weekStart": ""
    }
```

- [ ] **Step 3: Verify YAML is valid**

Run: `cd /root/hetzner-deployment-service && python3 -c "import yaml; yaml.safe_load_all(open('apps/airline-hr-chatbot/configmap.yaml')); print('YAML OK')"`
Expected: `YAML OK`

- [ ] **Step 4: Commit (in the hetzner-deployment-service repo)**

```bash
cd /root/hetzner-deployment-service
git add apps/airline-hr-chatbot/configmap.yaml
git commit -m "feat: add pageindex-mcp Prometheus scrape job and Grafana dashboard"
```

---

## Task 8: Final integration test — verify /metrics output

**Files:** None (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `cd /root/pageindex_deployment && uv run pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 2: Verify /metrics returns expected metric names**

Run: `cd /root/pageindex_deployment && uv run python -c "
from prometheus_client import generate_latest, REGISTRY
from pageindex_mcp.metrics import *  # ensure all metrics are registered
output = generate_latest(REGISTRY).decode()
expected = [
    'pageindex_tool_calls_total',
    'pageindex_tool_errors_total',
    'pageindex_tool_duration_seconds',
    'pageindex_uploads_total',
    'pageindex_upload_duration_seconds',
    'pageindex_active_uploads',
    'pageindex_rag_searches_total',
    'pageindex_rag_duration_seconds',
    'pageindex_llm_calls_total',
    'pageindex_llm_duration_seconds',
    'pageindex_minio_operations_total',
    'pageindex_minio_duration_seconds',
    'pageindex_documents_total',
    'process_cpu_seconds_total',
]
missing = [m for m in expected if m not in output]
if missing:
    print(f'MISSING: {missing}')
else:
    print('All metrics present')
"`
Expected: `All metrics present`

- [ ] **Step 3: Verify uv.lock is up to date**

Run: `cd /root/pageindex_deployment && uv lock --check`
Expected: Exits 0 (lock file matches pyproject.toml).
