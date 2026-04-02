# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

PageIndex MCP Server — a [FastMCP](https://github.com/jlowin/fastmcp)-based server that exposes document processing capabilities (vectorless reasoning-based RAG) via the Model Context Protocol. Documents are indexed into a hierarchical tree structure and stored in MinIO object storage.

## Environment Setup

Uses `uv` for dependency management (Python 3.12 required):

```bash
uv sync                  # install all dependencies
uv sync --extra dev      # include pytest/httpx for testing
```

Environment variables (copy to `.env`):
- `OPENAI_API_KEY` or `CHATGPT_API_KEY` — required by the PageIndex library
- `MINIO_ENDPOINT` — defaults to `10.43.246.106:9000`
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` — defaults to `minioadmin`/`minioadmin`
- `MINIO_BUCKET` — defaults to `pageindex`
- `MINIO_SECURE` — defaults to `false`

## Running the Server

```bash
uv run python mcp_server.py      # starts HTTP MCP server at http://0.0.0.0:8201/mcp
```

## Uploading Documents

```bash
# PDFs — single file, URL, or folder
uv run python upload.py /path/to/document.pdf
uv run python upload.py https://example.com/report.pdf
uv run python upload.py /path/to/folder/ --workers 4

# Office/image files — converts to PDF via LibreOffice/Pillow first
uv run python convert_and_upload.py /path/to/file.docx
uv run python convert_and_upload.py /path/to/folder/ --convert-only

# Process files from doc_store/ (with hash-based change detection)
uv run python preprocess_client.py                # all files in doc_store/
uv run python preprocess_client.py HR_FAQ.docx    # single file
uv run python preprocess_client.py --bg           # background, logs to preprocess.log
```

## Architecture

**`mcp_server.py`** — the core server. Registers these MCP tools via `FastMCP`:
- `process_document(url)` — downloads/reads a PDF, runs `page_index_main` to build an index tree, stores raw upload + processed JSON in MinIO
- `upload_and_process_document(filename, content_base64)` — same but accepts base64 content; converts DOCX/PPTX to markdown first via `md_to_tree`
- `list_documents()` / `get_document_summary(doc_id)` / `search_document(doc_id, query)` — query the stored index
- `delete_document(doc_id)` / `sync_preloaded_documents()` — management operations

**Storage layout in MinIO:**
- `processed/<doc_id>.json` — the indexed tree (title, summary, nodes hierarchy)
- `uploads/<doc_id>/<filename>` — the raw source file
- `preloaded/<filename>` — source files synced from local `doc_store/`
- `hashes/processed_hashes.json` — change-detection cache used by `preprocess_client.py`

**`preprocess_client.py`** — idempotent batch processor: reads files from `doc_store/`, computes SHA-256, skips unchanged files (comparing against the MinIO hash cache), then calls `upload_and_process_document` via the FastMCP `Client`.

**`upload.py`** / **`convert_and_upload.py`** — CLI helpers that call `process_document` via `langchain_mcp_adapters.MultiServerMCPClient`. `convert_and_upload.py` additionally runs LibreOffice headless for office formats and Pillow for images before uploading.

**`test.py`** — a LangChain ReAct agent example that connects to the running server and queries it with an LLM.

**Key dependency:** `pageindex` is installed from a private GitHub repo (`trehansalil/PageIndex-salil`). The `page_index_main` function does the heavy lifting of PDF parsing and hierarchical indexing; `md_to_tree` handles markdown/text inputs.

## Notes

- `doc_id` values are 8-character UUID prefixes generated at processing time.
- Long-running tools (`process_document`, `upload_and_process_document`, `delete_document`) are decorated with `task=True` so FastMCP runs them as async background tasks with progress reporting.
- The server path is hardcoded to port `8201` in all client scripts.
