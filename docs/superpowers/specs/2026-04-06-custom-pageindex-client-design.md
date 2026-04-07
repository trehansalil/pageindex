# CustomPageIndexClient — Design Spec
**Date:** 2026-04-06

## Overview

Create `CustomPageIndexClient`, a subclass of `PageIndexClient`, that extends document indexing to support `.docx`, `.pptx`, `.html`, and `.txt` formats (in addition to the existing `.pdf` and `.md`) while persisting all data to MinIO (S3-compatible) storage with SHA-256 hash-based deduplication.

Format-conversion logic is moved out of the MCP server tools (`upload_and_process_document`, `process_document`) into this client class.

---

## Goals

1. Single class handles all supported formats end-to-end
2. All output persisted to MinIO — no local filesystem workspace
3. Idempotent: skip reprocessing if file content unchanged (SHA-256 hash check)
4. Image context preserved for all binary formats
5. Consistent storage schema throughout the codebase (`structure` key, MinIO source URL)

---

## Supported Formats & Conversion Strategy

| Extension | Indexing path | Image handling |
|-----------|--------------|----------------|
| `.pdf` | `page_index()` directly | PyMuPDF native |
| `.md`, `.markdown`, `.txt` | `md_to_tree()` directly | N/A |
| `.docx`, `.pptx` | LibreOffice headless → PDF → `page_index()` | PyMuPDF native via PDF |
| `.html` | `html2text` + vision LLM for inline/URL images → `md_to_tree()` | OpenAI vision API description |

---

## MinIO Storage Layout

```
pageindex/
  processed/<doc_id>.json          # indexed tree + metadata
  uploads/<doc_id>/<filename>      # raw source file
  hashes/processed_hashes.json     # {filename: sha256} dedup cache
```

---

## Stored Document Schema (`processed/<doc_id>.json`)

```json
{
  "doc_id": "abc12345",
  "doc_name": "report.docx",
  "source_url": "http://<minio_endpoint>/<bucket>/uploads/abc12345/report.docx",
  "processed_at": "2026-04-06T12:00:00+00:00",
  "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "doc_description": "...",
  "structure": [
    {
      "title": "...",
      "start_index": 1,
      "end_index": 3,
      "node_id": "0000",
      "text": "...",
      "summary": "...",
      "nodes": [
        {
          "title": "...",
          "start_index": 1,
          "end_index": 1,
          "node_id": "0001",
          "text": "...",
          "summary": "..."
        }
      ]
    }
  ]
}
```

Key points:
- `structure` preserves the raw key name from `page_index()` / `md_to_tree()` output — **not** renamed to `tree`
- `source_url` is the MinIO object URL of the raw uploaded file
- `doc_name` is the original filename (e.g. `report.docx`)
- `doc_id` is an 8-character UUID prefix

---

## Hash Cache Schema (`hashes/processed_hashes.json`)

```json
{
  "report.docx": "e3b0c44298fc...",
  "policy.pdf":  "a7ffc6f8bf1e..."
}
```

Keyed by filename. Shared between `CustomPageIndexClient` and `preprocess_client.py`.

---

## New File: `src/pageindex_mcp/client.py`

### `CustomPageIndexClient(PageIndexClient)`

```python
class CustomPageIndexClient(PageIndexClient):
    def __init__(self, api_key=None, model=None, retrieve_model=None): ...
    async def index(self, file_path: str, mode: str = "auto") -> str: ...
    async def get_document(self, doc_id: str) -> str: ...
    async def get_document_structure(self, doc_id: str) -> str: ...
    async def get_page_content(self, doc_id: str, pages: str) -> str: ...
```

#### `__init__`
- Calls `super().__init__(api_key=api_key, model=model, retrieve_model=retrieve_model, workspace=None)`
- `workspace=None` disables filesystem workspace in parent
- No MinIO client on instance — uses `storage.py` singleton

#### `index(file_path, mode="auto") -> str`

Returns `doc_id` (8-char UUID prefix).

Steps:
1. Resolve absolute path, verify file exists
2. Read file bytes, compute SHA-256
3. Load `hashes/processed_hashes.json` from MinIO; if `filename → hash` matches, return existing `doc_id` from `processed/` (skip reprocessing)
4. Convert/index based on extension:
   - `.pdf` → `page_index(file_path, ...)`
   - `.md`, `.markdown`, `.txt` → `md_to_tree(file_path, ...)` (handles asyncio loop safely, same as base class)
   - `.docx`, `.pptx` → `libreoffice_to_pdf(file_path)` → `page_index(pdf_tmp_path, ...)`
   - `.html` → `html_to_markdown_with_images(file_path)` → write temp `.md` → `md_to_tree(md_tmp_path, ...)`
5. Generate `doc_id = str(uuid.uuid4())[:8]`
6. `save_raw(doc_id, filename, file_bytes)` → MinIO `uploads/<doc_id>/<filename>`
7. Build `source_url` from MinIO endpoint + bucket + object path
8. `save_doc(doc_id, {...})` → MinIO `processed/<doc_id>.json`
9. Update `hashes/processed_hashes.json` with `{filename: sha256}`
10. Clean up temp files
11. Return `doc_id`

#### Retrieve methods (lazy, MinIO-only)
- `get_document(doc_id)` — `load_doc(doc_id)`, return metadata JSON (doc_id, doc_name, doc_description, structure summary)
- `get_document_structure(doc_id)` — `load_doc(doc_id)`, return `structure` without `text` fields
- `get_page_content(doc_id, pages)` — `load_doc(doc_id)`, walk node map, return matching node texts

---

## Updated: `src/pageindex_mcp/converters.py`

### New functions

**`libreoffice_to_pdf(input_path: str) -> str`**
- Runs `libreoffice --headless --convert-to pdf --outdir <tmpdir> <input_path>`
- Returns path to the generated PDF temp file
- Raises `RuntimeError` if LibreOffice not found or conversion fails

**`html_to_markdown_with_images(path: str, model: str) -> str`**
- Parse HTML with `html2text` for text content
- Extract `<img>` tags: decode base64 data URIs or fetch URL-referenced images
- For each image: call OpenAI vision API to generate a description
- Insert `[Image: <description>]` markers inline in the markdown at the image's position
- Returns markdown string

### New dependency
Add `html2text>=2020.1.16` to `pyproject.toml`.

---

## Updated: `src/pageindex_mcp/storage.py`

Add hash cache helpers (mirrors `preprocess_client.py` logic, shared MinIO object):

```python
def load_hash_cache() -> dict[str, str]: ...
def save_hash_cache(cache: dict[str, str]) -> None: ...
```

These read/write `hashes/processed_hashes.json`.

---

## Updated: `src/pageindex_mcp/tools/processing.py`

### `_persist()` changes
- Key rename: `"tree": result.get("structure")` → `"structure": result.get("structure")`
- Add `"sha256"` field
- `source_url` becomes MinIO object URL (constructed from `settings.minio_endpoint`, `settings.minio_bucket`, and the uploads path) instead of the original download URL
- Remove `doc_description` from top-level (it's already inside the structure nodes via `if_add_doc_description`)
  - Actually keep `doc_description` at top level for quick access without walking the tree

### `upload_and_process_document` — simplified
- Decode base64 → write temp file with correct extension
- Call `CustomPageIndexClient().index(tmp_path)`
- Return `{"doc_id": doc_id, "doc_name": filename, "message": "..."}`
- All format dispatch logic removed (lives in client now)

### `process_document` — simplified
- Download URL / read local path → write temp file
- Call `CustomPageIndexClient().index(tmp_path)`
- Return same response envelope

---

## Updated: `src/pageindex_mcp/helpers.py` and `tools/documents.py`

- Replace all `data.get("tree", [])` with `data.get("structure", [])`
- No other changes needed

---

## Async & Parallel Execution

`CustomPageIndexClient` is fully async:

- `index()` is `async def` — overrides the sync base class method
- CPU/IO-bound work runs in `asyncio.to_thread()`:
  - `page_index()` call
  - `libreoffice_to_pdf()` subprocess
  - File reads, hash computation, MinIO reads/writes
- `md_to_tree()` is already async — awaited directly
- HTML image descriptions: all images in a document described **concurrently** via `asyncio.gather()`
- Multiple documents can be indexed concurrently by the caller using `asyncio.gather(client.index(f) for f in files)`

`preprocess_client.py` updated to call `CustomPageIndexClient.index()` directly (no MCP round-trip) and process multiple files via `asyncio.gather`.

---

## MCP Server — Tool Trim

Reduce from 10 tools to 5 query-only tools. LLMs connecting to the server see only what they need for retrieval.

**Keep (query tools):**
| Tool | Purpose |
|------|---------|
| `find_relevant_documents` | Primary RAG — tree search + answer generation |
| `recent_documents` | Discover available documents |
| `get_document` | Metadata + top-level section list |
| `get_document_structure` | Full tree without text (navigation) |
| `get_page_content` | Fetch node text for specific pages |

**Remove:**
| Tool | Reason |
|------|--------|
| `get_document_image` | Unsupported / always returns error |
| `remove_document` | Destructive management — not for LLM |
| `sync_preloaded_documents` | Admin operation — not for LLM |
| `process_document` | Processing moved to `CustomPageIndexClient` |
| `upload_and_process_document` | Processing moved to `CustomPageIndexClient` |

`server.py` registers only the 5 kept tools. The 5 removed tool functions are deleted from `tools/processing.py` and `tools/documents.py` (or the relevant ones).

---

## Unchanged

- `src/pageindex_mcp/storage.py` `load_doc` / `save_doc` / `save_raw` — work with any dict schema

---

## Migration Note

Existing documents stored as `processed/<uuid>.json` with `"tree"` key will not be readable by the updated `helpers.py` / `documents.py` until re-indexed. If preserving existing docs matters, a one-time migration script (rename key `tree` → `structure` in-place) can be run against MinIO before deployment.
