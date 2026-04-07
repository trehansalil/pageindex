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
