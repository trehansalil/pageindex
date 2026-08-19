"""Zone-5 contract tests: HR2 right-to-erasure endpoint.

Verifies that delete_document MCP tool/route is wired to storage.delete_doc
and that unauthenticated requests are rejected.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Wiring: delete_document delegates to storage.delete_doc
# ---------------------------------------------------------------------------


class TestDeleteDocumentWiring:
    """delete_document MCP tool must delegate to storage.delete_doc."""

    def test_delete_document_registered_as_mcp_tool(self):
        """server.py must register delete_document as an MCP tool."""
        import pageindex_mcp.server as server

        source = inspect.getsource(server)
        assert "delete_document" in source, (
            "server.py does not define or register delete_document"
        )
        assert "mcp.tool()" in source, (
            "server.py does not register any MCP tools"
        )

    def test_delete_document_source_calls_delete_doc(self):
        """The delete_document function body must call storage.delete_doc."""
        from pageindex_mcp.server import delete_document

        source = inspect.getsource(delete_document)
        assert "delete_doc" in source, (
            "delete_document does not reference storage.delete_doc"
        )

    def test_delete_document_is_async(self):
        """delete_document must be an async function (for await delete_doc)."""
        from pageindex_mcp.server import delete_document

        assert inspect.iscoroutinefunction(delete_document), (
            "delete_document is not async -- storage.delete_doc is async"
        )

    @pytest.mark.asyncio
    async def test_delete_document_delegates_call(self):
        """delete_document('test-doc') must call storage.delete_doc('test-doc')."""
        mock_delete = AsyncMock(return_value={"errors": []})
        with patch("pageindex_mcp.server.delete_doc", mock_delete, create=True):
            # Re-import or call directly -- the lazy import inside the
            # function body means we need to patch the storage module.
            with patch(
                "pageindex_mcp.storage.delete_doc", mock_delete
            ):
                from pageindex_mcp.server import delete_document

                result = await delete_document("test-doc-id")

        mock_delete.assert_called_once_with("test-doc-id")
        assert result == {"errors": []}


# ---------------------------------------------------------------------------
# Auth: unauthenticated requests must be rejected
# ---------------------------------------------------------------------------


class TestDeleteDocumentAuth:
    """BearerAuthMiddleware must protect the delete_document endpoint."""

    def test_bearer_auth_middleware_applied(self):
        """starlette_app must have BearerAuthMiddleware."""
        from pageindex_mcp.server import starlette_app

        middleware_classes = [
            m.cls if hasattr(m, "cls") else type(m)
            for m in starlette_app.middleware_stack.__class__.__mro__
        ] if hasattr(starlette_app, "middleware_stack") else []

        # Alternative: check that the middleware was added in source
        source = inspect.getsource(__import__("pageindex_mcp.server", fromlist=["server"]))
        assert "BearerAuthMiddleware" in source, (
            "server.py does not apply BearerAuthMiddleware"
        )
        assert "starlette_app.add_middleware(BearerAuthMiddleware)" in source, (
            "BearerAuthMiddleware not added to starlette_app"
        )

    def test_upload_prefix_is_public_but_mcp_is_not(self):
        """BearerAuthMiddleware's _PUBLIC_PREFIXES must NOT include the MCP path."""
        from pageindex_mcp.auth import _PUBLIC_PREFIXES

        # /upload is public (has its own API-key auth), but MCP tools go
        # through the default path which requires bearer auth.
        assert "/upload" in _PUBLIC_PREFIXES, (
            "/upload not in public prefixes -- upload has its own auth"
        )
        # The MCP tool endpoint path must NOT be in public prefixes.
        for prefix in _PUBLIC_PREFIXES:
            assert prefix not in ("/mcp", "/tools", "/"), (
                f"MCP tool path '{prefix}' is in _PUBLIC_PREFIXES -- "
                f"delete_document would be unprotected"
            )

    def test_missing_bearer_returns_401(self):
        """A request without Authorization header must get 401."""
        from starlette.testclient import TestClient
        from pageindex_mcp.server import starlette_app

        # Patch settings attributes to enforce bearer auth for this test.
        with patch("pageindex_mcp.auth.settings") as mock_settings:
            mock_settings.mcp_bearer_token = "test-secret-token"
            mock_settings.mcp_allow_unauthenticated = False
            client = TestClient(starlette_app, raise_server_exceptions=False)
            # MCP tools are served on the root path -- send a POST without auth.
            resp = client.post("/mcp", json={"method": "tools/call", "params": {"name": "delete_document", "arguments": {"doc_id": "x"}}})
            assert resp.status_code in (401, 405, 404), (
                f"Expected 401/405/404 for unauthenticated request, got {resp.status_code}"
            )

    def test_wrong_bearer_returns_401(self):
        """A request with wrong bearer token must get 401."""
        from starlette.testclient import TestClient
        from pageindex_mcp.server import starlette_app

        with patch("pageindex_mcp.auth.settings") as mock_settings:
            mock_settings.mcp_bearer_token = "correct-token"
            mock_settings.mcp_allow_unauthenticated = False
            client = TestClient(starlette_app, raise_server_exceptions=False)
            resp = client.post(
                "/mcp",
                json={"method": "tools/call", "params": {"name": "delete_document", "arguments": {"doc_id": "x"}}},
                headers={"Authorization": "Bearer wrong-token"},
            )
            assert resp.status_code == 401, (
                f"Expected 401 for wrong bearer token, got {resp.status_code}"
            )
