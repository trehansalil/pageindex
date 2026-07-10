"""Tests for BearerAuthMiddleware (RFC-008 D3/ISS-13: warn when auth is disabled)."""

import dataclasses
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

import pageindex_mcp.auth as auth_module
from pageindex_mcp.auth import BearerAuthMiddleware
from pageindex_mcp.metrics import MCP_AUTH_DISABLED


async def _ok(request):
    return PlainTextResponse("ok")


def _make_app():
    app = Starlette(routes=[Route("/protected", _ok)])
    app.add_middleware(BearerAuthMiddleware)
    return app


@pytest.fixture(autouse=True)
def _reset_auth_warned():
    """Reset the module-level once-only warning flag between tests."""
    auth_module._auth_warned = False
    yield
    auth_module._auth_warned = False


@pytest.fixture
async def client_no_token():
    no_token_settings = dataclasses.replace(auth_module.settings, mcp_bearer_token="")
    with patch.object(auth_module, "settings", no_token_settings):
        app = _make_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c


@pytest.fixture
async def client_with_token():
    with_token_settings = dataclasses.replace(
        auth_module.settings, mcp_bearer_token="secret-token"
    )
    with patch.object(auth_module, "settings", with_token_settings):
        app = _make_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c


async def test_passthrough_when_token_unset(client_no_token):
    """Existing behavior unchanged: no token configured -> request still passes through."""
    response = await client_no_token.get("/protected")
    assert response.status_code == 200
    assert response.text == "ok"


async def test_warning_logged_exactly_once_across_multiple_requests(
    client_no_token, caplog
):
    with caplog.at_level("WARNING", logger="pageindex_mcp.auth"):
        await client_no_token.get("/protected")
        await client_no_token.get("/protected")
        await client_no_token.get("/protected")

    warnings = [
        record
        for record in caplog.records
        if "MCP bearer-token auth is DISABLED" in record.message
    ]
    assert len(warnings) == 1


async def test_gauge_is_one_when_auth_disabled(client_no_token):
    await client_no_token.get("/protected")
    assert MCP_AUTH_DISABLED._value.get() == 1


async def test_gauge_is_zero_when_token_configured(client_with_token):
    response = await client_with_token.get(
        "/protected", headers={"Authorization": "Bearer secret-token"}
    )
    assert response.status_code == 200
    assert MCP_AUTH_DISABLED._value.get() == 0
