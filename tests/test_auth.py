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
    no_token_settings = dataclasses.replace(
        auth_module.settings, mcp_bearer_token="", mcp_allow_unauthenticated=True
    )
    with patch.object(auth_module, "settings", no_token_settings):
        app = _make_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c


@pytest.fixture
async def client_no_token_no_allow():
    no_token_settings = dataclasses.replace(
        auth_module.settings, mcp_bearer_token="", mcp_allow_unauthenticated=False
    )
    with patch.object(auth_module, "settings", no_token_settings):
        app = _make_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c


@pytest.fixture
async def client_with_token():
    with_token_settings = dataclasses.replace(
        auth_module.settings,
        mcp_bearer_token="secret-token",
        mcp_allow_unauthenticated=False,
    )
    with patch.object(auth_module, "settings", with_token_settings):
        app = _make_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c


async def test_passthrough_when_token_unset(client_no_token):
    """No token configured but explicitly opted-in via MCP_ALLOW_UNAUTHENTICATED -> passthrough."""
    response = await client_no_token.get("/protected")
    assert response.status_code == 200
    assert response.text == "ok"


async def test_503_when_token_unset_and_allow_unauthenticated_unset(
    client_no_token_no_allow,
):
    """RFC-011 D4: fail closed by default when no token is configured and the
    opt-in flag is not set."""
    response = await client_no_token_no_allow.get("/protected")
    assert response.status_code == 503
    assert response.json() == {"error": "auth not configured"}


async def test_passthrough_when_allow_unauthenticated_true(client_no_token, caplog):
    """Explicit opt-in (MCP_ALLOW_UNAUTHENTICATED=true) still allows pass-through,
    with the disabled-auth gauge set and the warning logged."""
    with caplog.at_level("WARNING", logger="pageindex_mcp.auth"):
        response = await client_no_token.get("/protected")

    assert response.status_code == 200
    assert response.text == "ok"
    assert MCP_AUTH_DISABLED._value.get() == 1
    warnings = [
        record
        for record in caplog.records
        if "MCP bearer-token auth is DISABLED" in record.message
    ]
    assert len(warnings) == 1


async def test_normal_auth_flow_unchanged_when_token_set(client_with_token):
    """Regression guard: when a bearer token is configured, auth behavior is
    unaffected by mcp_allow_unauthenticated."""
    no_auth_response = await client_with_token.get("/protected")
    assert no_auth_response.status_code == 401

    ok_response = await client_with_token.get(
        "/protected", headers={"Authorization": "Bearer secret-token"}
    )
    assert ok_response.status_code == 200
    assert ok_response.text == "ok"


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
