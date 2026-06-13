"""FastMCP server composition root and entry point."""

import asyncio
import contextlib
import logging

from fastmcp import FastMCP
from starlette.routing import Route

from . import queue_metrics
from . import tools as _tools
from .auth import BearerAuthMiddleware
from .cache import get_async_redis
from .config import settings
from .metrics import metrics_response
from .upload_app import create_upload_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

mcp = FastMCP("pageindex-local")

# ---------------------------------------------------------------------------
# Query tools only — document processing is handled by arq workers.
# ---------------------------------------------------------------------------
mcp.tool()(_tools.recent_documents)
mcp.tool()(_tools.find_relevant_documents)
mcp.tool()(_tools.get_document)
mcp.tool()(_tools.get_document_structure)
mcp.tool()(_tools.get_page_content)

# ---------------------------------------------------------------------------
# Build the ASGI app (importable by gunicorn as pageindex_mcp.server:app)
# ---------------------------------------------------------------------------
starlette_app = mcp.http_app(transport="streamable-http")
starlette_app.add_middleware(BearerAuthMiddleware)
starlette_app.routes.insert(0, Route("/metrics", metrics_response))
starlette_app.mount("/upload", create_upload_app())

# Preserve FastMCP's own lifespan (session manager) and additionally run the
# arq queue-depth scrape loop for the lifetime of the server process.
_inner_lifespan = starlette_app.router.lifespan_context


@contextlib.asynccontextmanager
async def _lifespan_with_scrape(app, _inner=_inner_lifespan):
    redis = await get_async_redis()
    scrape_task = asyncio.create_task(queue_metrics.queue_depth_scrape_loop(redis))
    try:
        if _inner is None:
            yield
        else:
            async with _inner(app):
                yield
    finally:
        scrape_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scrape_task


starlette_app.router.lifespan_context = _lifespan_with_scrape

# This is what gunicorn imports:
app = starlette_app


def main() -> None:
    """Entry point for local dev via `pageindex-mcp` console script."""
    import anyio
    import uvicorn

    print(
        f"Starting PageIndex MCP server at http://{settings.server_host}:{settings.server_port}/mcp"
    )
    print(f"Upload service at http://{settings.server_host}:{settings.server_port}/upload")
    print(f"Metrics at http://{settings.server_host}:{settings.server_port}/metrics")
    print(f"MinIO endpoint: {settings.minio_endpoint}  bucket: {settings.minio_bucket}")
    print("Press Ctrl+C to stop\n")

    async def _serve() -> None:
        config = uvicorn.Config(
            app,
            host=settings.server_host,
            port=settings.server_port,
            lifespan="on",
            timeout_graceful_shutdown=2,
        )
        server = uvicorn.Server(config)
        await server.serve()

    anyio.run(_serve)
