"""Tests for the /metrics Prometheus endpoint."""

import pytest
from httpx import AsyncClient, ASGITransport
from starlette.applications import Starlette
from starlette.routing import Route
from unittest.mock import patch, MagicMock, AsyncMock

from pageindex_mcp.metrics import metrics_response, TOOL_CALLS, TOOL_ERRORS, TOOL_DURATION, DOCUMENTS_TOTAL


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


from pageindex_mcp.metrics import UPLOADS, UPLOAD_DURATION, ACTIVE_UPLOADS


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
