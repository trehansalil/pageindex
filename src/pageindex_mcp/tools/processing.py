"""MCP tools: PDF URL processing and base64 upload/processing.

Calls pageindex functions (page_index, md_to_tree) directly — matching
the output format of run_pageindex.py — so the stored tree always
contains text, summary, and node_id fields needed by _rag().
"""

import asyncio
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..converters import docx_to_markdown, pptx_to_markdown
from ..storage import save_doc, save_raw

_MODEL = os.environ.get("PAGEINDEX_MODEL", "gpt-4o-2024-11-20")


def _index_pdf(pdf_path: str) -> dict:
    """Run pageindex on a PDF and return the full result dict (with text)."""
    from pageindex import page_index

    return page_index(
        doc=pdf_path,
        model=_MODEL,
        if_add_node_id="yes",
        if_add_node_summary="yes",
        if_add_node_text="yes",
        if_add_doc_description="yes",
    )


async def _index_markdown(md_path: str) -> dict:
    """Run md_to_tree on a markdown file and return the full result dict."""
    from pageindex.page_index_md import md_to_tree

    return await md_to_tree(
        md_path=md_path,
        if_thinning=False,
        if_add_node_summary="yes",
        summary_token_threshold=200,
        model=_MODEL,
        if_add_doc_description="yes",
        if_add_node_text="yes",
        if_add_node_id="yes",
    )


def _persist(result: dict, filename: str, source_url: str, file_bytes: bytes) -> dict:
    """Save processed result + raw file to MinIO and return the response dict."""
    doc_id = str(uuid.uuid4())[:8]
    save_raw(doc_id, filename, file_bytes)
    save_doc(doc_id, {
        "doc_id":          doc_id,
        "filename":        filename,
        "source_url":      source_url,
        "processed_at":    datetime.now(timezone.utc).isoformat(),
        "doc_name":        result.get("doc_name", ""),
        "doc_description": result.get("doc_description", ""),
        "tree":            result.get("structure", []),
    })
    return {
        "doc_id":   doc_id,
        "filename": filename,
        "message":  f"Document processed successfully. Use doc_id '{doc_id}' with other tools.",
    }


async def process_document(url: str) -> str:
    """
    Process a PDF document from a URL or local file path.
    Builds a hierarchical index tree and stores it in MinIO.
    Returns a doc_id you can use with other tools.
    Processing may take 1-3 minutes for large documents.

    url: HTTPS URL or absolute local file path to a PDF
    """
    url = url.strip()
    tmp_path = None

    try:
        is_local = not url.startswith("http://") and not url.startswith("https://")

        if is_local:
            pdf_path = url
            if url.startswith("file://"):
                from urllib.request import url2pathname
                pdf_path = url2pathname(url[7:])
            if not os.path.isabs(pdf_path):
                pdf_path = os.path.abspath(pdf_path)
            if not os.path.isfile(pdf_path):
                return json.dumps({"error": f"File not found: {pdf_path}"})
            filename = os.path.basename(pdf_path)
            with open(pdf_path, "rb") as f:
                file_bytes = f.read()
        else:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.get(
                    url,
                    follow_redirects=True,
                    headers={
                        "Accept": "application/pdf, application/octet-stream, */*",
                        "User-Agent": "Mozilla/5.0 (compatible; PageIndex/1.0)",
                    },
                )
                response.raise_for_status()

            file_bytes = response.content
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name

            from urllib.parse import urlparse
            parsed = urlparse(url)
            filename = os.path.basename(parsed.path) or "document.pdf"
            if not filename.lower().endswith(".pdf"):
                filename += ".pdf"
            pdf_path = tmp_path

        if file_bytes[:4] != b"%PDF":
            return json.dumps({"error": "Not a valid PDF file"})

        result = await asyncio.to_thread(_index_pdf, pdf_path)
        resp = await asyncio.to_thread(_persist, result, filename, url, file_bytes)
        return json.dumps(resp)

    except httpx.HTTPError as e:
        return json.dumps({"error": f"Failed to download document: {e}"})
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


async def upload_and_process_document(filename: str, content_base64: str) -> str:
    """
    Upload and process a document from base64-encoded content.
    Supports: PDF (.pdf), Word (.docx), PowerPoint (.pptx),
              Markdown (.md), plain text (.txt).
    Returns a doc_id you can use with other tools.
    Processing may take 1-3 minutes for large PDF documents.

    filename: original filename including extension e.g. "report.pdf"
    content_base64: base64-encoded bytes of the file
    """
    import base64 as _base64

    filename = filename.strip()
    ext = Path(filename).suffix.lower()
    supported = (".pdf", ".md", ".txt", ".docx", ".pptx")

    if ext not in supported:
        return json.dumps({
            "error": f"Unsupported file type '{ext}'. Supported: {', '.join(supported)}"
        })

    tmp_path    = None
    md_tmp_path = None

    try:
        try:
            file_bytes = _base64.b64decode(content_base64)
        except Exception:
            return json.dumps({"error": "Invalid base64 content"})

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        if ext == ".pdf":
            if file_bytes[:4] != b"%PDF":
                return json.dumps({"error": "Not a valid PDF file"})
            result = await asyncio.to_thread(_index_pdf, tmp_path)

        elif ext in (".docx", ".pptx"):
            converter = docx_to_markdown if ext == ".docx" else pptx_to_markdown
            md_content = await asyncio.to_thread(converter, tmp_path)
            with tempfile.NamedTemporaryFile(
                suffix=".md", delete=False, mode="w", encoding="utf-8"
            ) as md_tmp:
                md_tmp.write(md_content)
                md_tmp_path = md_tmp.name
            result = await _index_markdown(md_tmp_path)

        else:  # .md or .txt
            result = await _index_markdown(tmp_path)

        resp = await asyncio.to_thread(_persist, result, filename, "", file_bytes)
        return json.dumps(resp)

    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if md_tmp_path and os.path.exists(md_tmp_path):
            os.unlink(md_tmp_path)
