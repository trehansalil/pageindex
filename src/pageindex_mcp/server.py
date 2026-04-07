"""FastMCP server composition root and entry point."""

import anyio
import uvicorn
from fastmcp import FastMCP

from . import tools as _tools

mcp = FastMCP("pageindex-local")

# ---------------------------------------------------------------------------
# Query tools only — document processing is handled by CustomPageIndexClient.
# ---------------------------------------------------------------------------
mcp.tool()(_tools.recent_documents)
mcp.tool()(_tools.find_relevant_documents)
mcp.tool()(_tools.get_document)
mcp.tool()(_tools.get_document_structure)
mcp.tool()(_tools.get_page_content)


def main() -> None:
    """Entry point called by the `pageindex-mcp` console script."""
    from .config import settings
    from .upload_app import create_upload_app

    print(f"Starting PageIndex MCP server at http://{settings.server_host}:{settings.server_port}/mcp")
    print(f"Upload service at http://{settings.server_host}:{settings.server_port}/upload")
    print(f"MinIO endpoint: {settings.minio_endpoint}  bucket: {settings.minio_bucket}")
    print("Press Ctrl+C to stop\n")

    # Build the FastMCP Starlette app (includes its own lifespan for MCP session management).
    starlette_app = mcp.http_app(transport="streamable-http")

    # Mount the upload FastAPI app at /upload.
    upload_app = create_upload_app()
    starlette_app.mount("/upload", upload_app)

    async def _serve() -> None:
        config = uvicorn.Config(
            starlette_app,
            host=settings.server_host,
            port=settings.server_port,
            lifespan="on",
            timeout_graceful_shutdown=2,
        )
        server = uvicorn.Server(config)
        await server.serve()

    anyio.run(_serve)
