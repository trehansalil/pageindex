# PageIndex MCP Server

A [FastMCP](https://github.com/jlowin/fastmcp)-based server that exposes document processing capabilities via the [Model Context Protocol](https://modelcontextprotocol.io/). Documents are parsed into a hierarchical index tree using vectorless, reasoning-based RAG and stored in MinIO object storage.

## Requirements

- Python 3.12+
- [`uv`](https://github.com/astral-sh/uv) for dependency management
- A running MinIO instance
- An OpenAI-compatible API key (for the PageIndex library)

## Setup

```bash
# Install dependencies
uv sync

# For development (adds pytest/httpx)
uv sync --extra dev
```

Copy `.env.example` to `.env` (or export directly) and set:

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` or `CHATGPT_API_KEY` | — | Required by the PageIndex library |
| `MINIO_ENDPOINT` | `10.43.246.106:9000` | MinIO server address |
| `MINIO_ACCESS_KEY` | `minioadmin` | MinIO access key |
| `MINIO_SECRET_KEY` | `minioadmin` | MinIO secret key |
| `MINIO_BUCKET` | `pageindex` | Bucket name |
| `MINIO_SECURE` | `false` | Use TLS for MinIO connection |
| `MCP_HOST` | `0.0.0.0` | Server bind address |
| `MCP_PORT` | `8201` | Server port |

## Running the Server

```bash
uv run python mcp_server.py
# or via the installed script:
uv run pageindex-mcp
```

The server starts at `http://0.0.0.0:8201/mcp` using the `streamable-http` MCP transport.

## MCP Tools

| Tool | Type | Description |
|---|---|---|
| `process_document(url)` | async task | Download a PDF from a URL or local path, build an index tree, store in MinIO |
| `upload_and_process_document(filename, content_base64)` | async task | Same, but accepts base64-encoded content; supports PDF, DOCX, PPTX, MD, TXT |
| `list_documents()` | sync | List all processed documents (doc_id, filename, timestamp) |
| `get_document_summary(doc_id)` | sync | Get top-level sections and summaries for a document |
| `search_document(doc_id, query)` | sync | Keyword search across section titles and summaries |
| `delete_document(doc_id)` | async task | Delete a document and its raw upload from MinIO |
| `sync_preloaded_documents()` | sync | Upload files from `doc_store/` to MinIO's `preloaded/` prefix |

Long-running tools (`process_document`, `upload_and_process_document`, `delete_document`) run as background tasks with progress reporting via FastMCP's `task=True` decorator.

## Uploading Documents

### PDFs via CLI

```bash
# Single file
uv run python upload.py /path/to/document.pdf

# Remote URL
uv run python upload.py https://example.com/report.pdf

# All PDFs in a folder (parallel, default 4 workers)
uv run python upload.py /path/to/folder/
uv run python upload.py /path/to/folder/ --workers 8
```

### Batch processing from `doc_store/`

Place files in the `doc_store/` directory, then run:

```bash
# Process all new/changed files
uv run python preprocess_client.py

# Process a single file
uv run python preprocess_client.py HR_FAQ.docx

# Run in the background (logs to preprocess.log)
uv run python preprocess_client.py --bg
```

`preprocess_client.py` is idempotent — it computes a SHA-256 hash of each file and skips anything unchanged since the last run. The hash cache is stored in MinIO at `hashes/processed_hashes.json`.

Supported formats: `.pdf`, `.docx`, `.pptx`, `.md`, `.txt`

## Storage Layout (MinIO)

```
pageindex/
  processed/<doc_id>.json          # indexed tree (title, summary, nodes)
  uploads/<doc_id>/<filename>      # raw source file
  preloaded/<filename>             # files synced from doc_store/
  hashes/processed_hashes.json     # change-detection cache
```

`doc_id` values are 8-character UUID prefixes generated at processing time.

## Project Structure

```
mcp_server.py              # entry point — delegates to pageindex_mcp.server
upload.py                  # CLI to upload PDFs via process_document
preprocess_client.py       # batch processor for doc_store/ with hash-based deduplication
test.py                    # LangChain ReAct agent example
src/
  pageindex_mcp/
    server.py              # FastMCP app composition and main()
    config.py              # settings loaded from env
    storage.py             # MinIO read/write helpers
    converters.py          # DOCX/PPTX → markdown, node flattening
    tools/
      processing.py        # process_document, upload_and_process_document
      documents.py         # list, summary, search, delete, sync
```

## Architecture Notes

- The `pageindex` library (`page_index_main`, `md_to_tree`) is installed from a private GitHub repo (`trehansalil/PageIndex-salil`) and does the heavy lifting of PDF parsing and hierarchical indexing.
- DOCX/PPTX files are converted to markdown before being passed to `md_to_tree`.
- PageIndex imports are deferred inside tool functions so the server module loads even if the library is not yet on `sys.path`.
- A local `PageIndex/` directory in the repo root is automatically added to `sys.path` if present (useful for development checkouts).
