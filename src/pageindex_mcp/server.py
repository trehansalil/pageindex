"""FastMCP server composition root and entry point."""

from fastmcp import FastMCP

from . import tools as _tools

mcp = FastMCP("pageindex-local")

# ---------------------------------------------------------------------------
# All tools — FastMCP handles sync and async functions automatically.
# No special flags needed for long-running async tools.
# ---------------------------------------------------------------------------
mcp.tool()(_tools.recent_documents)
mcp.tool()(_tools.find_relevant_documents)
mcp.tool()(_tools.get_document)
mcp.tool()(_tools.get_document_structure)
mcp.tool()(_tools.get_page_content)
mcp.tool()(_tools.get_document_image)
mcp.tool()(_tools.remove_document)
mcp.tool()(_tools.sync_preloaded_documents)
mcp.tool()(_tools.process_document)
mcp.tool()(_tools.upload_and_process_document)


def main() -> None:
    """Entry point called by the `pageindex-mcp` console script."""
    from .config import settings

    print(f"Starting PageIndex MCP server at http://{settings.server_host}:{settings.server_port}/mcp")
    print(f"MinIO endpoint: {settings.minio_endpoint}  bucket: {settings.minio_bucket}")
    print("Press Ctrl+C to stop\n")

    mcp.run(
        transport="streamable-http",
        host=settings.server_host,
        port=settings.server_port,
    )
