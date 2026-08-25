"""Tests for the /metrics Prometheus endpoint."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.routing import Route

from pageindex_mcp.metrics import (
    DOCUMENTS_TOTAL,
    TOOL_CALLS,
    TOOL_ERRORS,
    metrics_response,
)


@pytest.fixture
def metrics_app():
    """Minimal Starlette app with just the /metrics route."""
    return Starlette(routes=[Route("/metrics", metrics_response)])


@pytest.fixture
async def client(metrics_app):
    async with AsyncClient(transport=ASGITransport(app=metrics_app), base_url="http://test") as c:
        yield c


async def test_metrics_endpoint_returns_200(client):
    response = await client.get("/metrics")
    assert response.status_code == 200


async def test_metrics_content_type(client):
    response = await client.get("/metrics")
    assert "text/plain" in response.headers["content-type"]
    assert "0.0.4" in response.headers["content-type"]


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="process_* metrics are Linux-only (prometheus_client reads /proc)",
)
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


def _counter_value(counter, labels=None):
    """Read current value of a Counter for given labels."""
    if labels:
        return counter.labels(**labels)._value.get()
    return counter._value.get()


def _gauge_value(gauge):
    return gauge._value.get()


class TestToolInstrumentation:
    async def test_recent_documents_increments_counter(self):
        # Phase 3 audit Issue B: registry-unavailable now raises isError:true
        # (ToolError) instead of returning a JSON envelope, but TOOL_CALLS still
        # increments unconditionally at the top of the function.
        from fastmcp.exceptions import ToolError

        before = _counter_value(TOOL_CALLS, {"tool": "recent_documents"})
        with patch("pageindex_mcp.storage.list_processed_docs", return_value=[]):
            from pageindex_mcp.tools.documents import recent_documents

            with pytest.raises(ToolError):
                await recent_documents()
        after = _counter_value(TOOL_CALLS, {"tool": "recent_documents"})
        assert after == before + 1

    async def test_recent_documents_updates_documents_gauge(self):
        # RFC-009 D6: registry-only read path — DOCUMENTS_TOTAL reflects
        # registry.count_docs(), not a MinIO listing length.
        fake_docs = [{"doc_id": "a", "doc_name": "a"}, {"doc_id": "b", "doc_name": "b"}]
        from pageindex_mcp.tools import documents

        with (
            patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
            patch("pageindex_mcp.registry.list_docs", new=AsyncMock(return_value=fake_docs)),
            patch("pageindex_mcp.registry.count_docs", new=AsyncMock(return_value=2)),
        ):
            await documents.recent_documents()
        assert _gauge_value(DOCUMENTS_TOTAL) == 2

    def test_get_document_increments_error_counter_on_failure(self):
        before = _counter_value(TOOL_ERRORS, {"tool": "get_document"})
        with (
            patch("pageindex_mcp.tools.documents.get_doc", side_effect=Exception("boom")),
            patch("pageindex_mcp.storage.list_processed_docs", return_value=[]),
        ):
            from pageindex_mcp.tools.documents import get_document

            get_document("nonexistent")
        after = _counter_value(TOOL_ERRORS, {"tool": "get_document"})
        assert after == before + 1


from pageindex_mcp.metrics import ACTIVE_UPLOADS, UPLOADS


class TestUploadInstrumentation:
    def test_upload_success_increments_counter(self):
        before = _counter_value(UPLOADS, {"status": "success"})
        UPLOADS.labels(status="success").inc()
        after = _counter_value(UPLOADS, {"status": "success"})
        assert after == before + 1

    def test_active_uploads_gauge_exists(self):
        val = _gauge_value(ACTIVE_UPLOADS)
        assert val >= 0


import asyncio

from pageindex_mcp.metrics import LLM_CALLS


class TestLLMInstrumentation:
    def test_llm_call_increments_counter(self):
        before = _counter_value(LLM_CALLS)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "test answer"

        # `helpers.rag._llm` does `from ..client import get_openai_client` at
        # call time, which resolves the name off the `pageindex_mcp.client`
        # package (__init__.py's re-export), not off `pageindex_mcp.client.llm`.
        # Patching the `llm` submodule attribute leaves that re-export
        # untouched (mock-where-defined instead of mock-where-used), so the
        # real client was constructed and a live LLM call went out. Patch the
        # name actually consulted by the call site instead.
        with patch("pageindex_mcp.client.get_openai_client") as MockFactory:
            MockFactory.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
            from pageindex_mcp.helpers import _llm

            asyncio.get_event_loop().run_until_complete(_llm("test prompt"))

        after = _counter_value(LLM_CALLS)
        assert after == before + 1


from pageindex_mcp.metrics import MINIO_OPS


class TestStorageInstrumentation:
    def test_list_processed_docs_increments_minio_ops(self):
        before = _counter_value(MINIO_OPS, {"operation": "list"})
        mock_minio = MagicMock()
        mock_minio.list_objects.return_value = []
        with patch("pageindex_mcp.storage.minio_ops.get_minio", return_value=mock_minio):
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
        with (
            patch("pageindex_mcp.storage.minio_ops.get_minio", return_value=mock_minio),
            patch("pageindex_mcp.storage.documents.settings") as mock_settings,
        ):
            mock_settings.minio_bucket = "test"
            from pageindex_mcp.storage import load_doc

            load_doc("abc123")
        after = _counter_value(MINIO_OPS, {"operation": "get"})
        assert after == before + 1


def test_arq_queue_depth_gauge_exposed():
    # Arrange
    from prometheus_client import generate_latest

    from pageindex_mcp.metrics import ARQ_QUEUE_DEPTH, REGISTRY

    # Act
    ARQ_QUEUE_DEPTH.set(3)
    text = generate_latest(REGISTRY).decode()

    # Assert
    assert "pageindex_arq_queue_depth" in text
    assert "pageindex_arq_queue_depth 3.0" in text
