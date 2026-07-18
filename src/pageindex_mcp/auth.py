"""Bearer-token authentication middleware for the MCP endpoint."""

import logging
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import settings
from .metrics import MCP_AUTH_DISABLED

logger = logging.getLogger(__name__)

# Paths that bypass bearer auth (metrics for Prometheus, upload has its own API-key auth)
_PUBLIC_PREFIXES = ("/metrics", "/upload")

# Module-level flag so the "auth disabled" warning logs exactly once per process
# lifetime, not once per request (RFC-008 D3/ISS-13).
_auth_warned: bool = False


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Require a valid ``Authorization: Bearer <token>`` header on protected routes."""

    def __init__(self, app):
        super().__init__(app)
        # Set once at middleware construction (startup) — reflects the static
        # config state for the process lifetime (RFC-008 D3/ISS-13).
        MCP_AUTH_DISABLED.set(0 if settings.mcp_bearer_token else 1)

    async def dispatch(self, request: Request, call_next):
        global _auth_warned
        path = request.url.path

        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        token = settings.mcp_bearer_token
        if not token:
            if not settings.mcp_allow_unauthenticated:
                return JSONResponse({"error": "auth not configured"}, status_code=503)
            # Explicit opt-in pass-through (dev/trusted-network mode)
            if not _auth_warned:
                logger.warning("MCP bearer-token auth is DISABLED — MCP_BEARER_TOKEN is empty")
                _auth_warned = True
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse({"error": "Missing Bearer token"}, status_code=401)

        provided = auth.removeprefix("Bearer ")
        if not secrets.compare_digest(provided, token):
            return JSONResponse({"error": "Invalid Bearer token"}, status_code=401)

        return await call_next(request)
