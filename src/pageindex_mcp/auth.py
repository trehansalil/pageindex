"""Bearer-token authentication middleware for the MCP endpoint."""

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import settings

# Paths that bypass bearer auth (metrics for Prometheus, upload has its own API-key auth)
_PUBLIC_PREFIXES = ("/metrics", "/upload")


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Require a valid ``Authorization: Bearer <token>`` header on protected routes."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        token = settings.mcp_bearer_token
        if not token:
            # No token configured — auth is disabled (dev mode)
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse({"error": "Missing Bearer token"}, status_code=401)

        provided = auth.removeprefix("Bearer ")
        if not secrets.compare_digest(provided, token):
            return JSONResponse({"error": "Invalid Bearer token"}, status_code=401)

        return await call_next(request)
