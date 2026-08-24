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
from .metrics import metrics_response, registry_metrics_sync_loop
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
# Zone-5 / HR2: delete_document — exposes storage.delete_doc so the right-to-
# erasure cascade is reachable in production (CLAUDE.md Hard Rule 2).
# Gated behind the same UPLOAD_API_KEY as the upload endpoints.
# ---------------------------------------------------------------------------


@mcp.tool()
async def delete_document(doc_id: str) -> dict:
    """HR2 right-to-erasure: cascade-delete a document and all derived stores.

    Purges uploads/, processed/*.json, processed/*.meta.json, Redis cache,
    reconcile-etag, hash-cache, Postgres registry row, and preloaded/ raw
    object — in that order per CLAUDE.md Hard Rule 2.

    Returns ``{"errors": [...]}`` — every individual store failure is reported,
    never raised (partial-failure visibility).

    **Authentication**: requires a valid UPLOAD_API_KEY (same as /upload/files).
    """
    from .storage import delete_doc

    return await delete_doc(doc_id)


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
    # RFC-011 D6 / ISS-33: refuse to start if PII corpus is routed through
    # a non-ZDR endpoint (HR3 enforcement).
    if settings.pii_corpus:
        from .config import _is_zdr_allowlisted

        if not _is_zdr_allowlisted(settings.openai_base_url):
            raise RuntimeError(
                f"PII_CORPUS=true but openai_base_url={settings.openai_base_url!r} "
                "is not on the ZDR allow-list (HR3)"
            )

        # Also validate LLM_FALLBACK_BASE_URL when set — the fallback path
        # in _llm_with_retry must not silently egress PII to a non-ZDR endpoint.
        from .client.llm import _LLM_FALLBACK_BASE_URL

        if _LLM_FALLBACK_BASE_URL and not _is_zdr_allowlisted(_LLM_FALLBACK_BASE_URL):
            raise RuntimeError(
                f"PII_CORPUS=true but LLM_FALLBACK_BASE_URL={_LLM_FALLBACK_BASE_URL!r} "
                "is not on the ZDR allow-list (HR3)"
            )

    # Zone-5: validate cross-module feature wiring contracts at startup.
    # Failures raise AssertionError, refusing to start the server.
    from .helpers import validate_feature_wirings

    try:
        validate_feature_wirings()
    except AssertionError:
        logging.getLogger(__name__).error(
            "Feature wiring validation failed at server startup — refusing to start"
        )
        raise

    redis = await get_async_redis()
    scrape_task = asyncio.create_task(queue_metrics.queue_depth_scrape_loop(redis))
    # Phase 3 audit Issue A follow-up: sync the Redis-mirrored registry write
    # metrics on a background cadence instead of inline in metrics_response(),
    # so a Redis outage/slow connection never delays a /metrics scrape.
    registry_metrics_task = asyncio.create_task(registry_metrics_sync_loop())
    # RFC-006: open the Postgres registry pool so the query read path
    # (_registry_narrow / _list_docs_with_fallback) can actually use it. Without
    # this, get_pool() stays None and every query silently falls back to MinIO.
    if settings.registry_enabled and settings.postgres_dsn:
        from .registry import init_registry

        try:
            await init_registry(settings.postgres_dsn)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "registry: init failed at server startup, queries will fall back to MinIO: %s",
                exc,
            )
        else:
            from .registry_backfill import run_auto_backfill

            try:
                await run_auto_backfill()
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "registry: auto-backfill failed at server startup: %s", exc
                )
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
        registry_metrics_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await registry_metrics_task
        if settings.registry_enabled and settings.postgres_dsn:
            from .registry import close_registry

            await close_registry()
        # Flush the langfuse-python client before the process exits. The query
        # path (find_relevant_documents -> OpenAI SDK via the langfuse.openai
        # wrapper) buffers spans on the SDK's background sender; a short-lived
        # server (e.g. a debug session: start -> one request -> stop) can be
        # killed before that thread flushes, dropping the trace. converters_cli
        # already does this for the ingestion subprocess; the server needs it
        # too. (litellm's private-OTel spans are an ingestion-only concern, so
        # flush_litellm_tracing() is not needed here.)
        from .tracing import flush_langfuse

        flush_langfuse()


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
