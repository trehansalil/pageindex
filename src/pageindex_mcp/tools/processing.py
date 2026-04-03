"""MCP tools: PDF URL processing and base64 upload/processing."""

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


async def process_document(url: str) -> str:
    """
    Process a PDF document from a URL or local file path.
    Builds a hierarchical index tree and stores it in MinIO.
    Returns a doc_id you can use with other tools.
    Processing may take 1-3 minutes for large documents.

    url: HTTPS URL or absolute local file path to a PDF
    """
    from pageindex import PageIndexClient

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

        pi_client = PageIndexClient(
            model=os.environ.get("PAGEINDEX_MODEL", "gpt-4o-2024-11-20"),
            workspace=None,
        )

        doc_id_local = await asyncio.to_thread(pi_client.index, pdf_path, "pdf")
        doc_data = json.loads(pi_client.get_document(doc_id_local))
        structure = json.loads(pi_client.get_document_structure(doc_id_local))

        doc_id = str(uuid.uuid4())[:8]
        await asyncio.to_thread(save_raw, doc_id, filename, file_bytes)
        await asyncio.to_thread(save_doc, doc_id, {
            "doc_id":          doc_id,
            "filename":        filename,
            "source_url":      url,
            "processed_at":    datetime.now(timezone.utc).isoformat(),
            "tree":            structure,
            "doc_name":        doc_data.get("doc_name", ""),
            "doc_description": doc_data.get("doc_description", ""),
        })

        return json.dumps({
            "doc_id":   doc_id,
            "filename": filename,
            "message":  f"Document processed successfully. Use doc_id '{doc_id}' with other tools.",
        })

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
    from pageindex import PageIndexClient

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

        pi_client = PageIndexClient(
            model=os.environ.get("PAGEINDEX_MODEL", "gpt-4o-2024-11-20"),
            workspace=None,
        )

        if ext == ".pdf":
            if file_bytes[:4] != b"%PDF":
                return json.dumps({"error": "Not a valid PDF file"})
            doc_id_local = await asyncio.to_thread(pi_client.index, tmp_path, "pdf")

        elif ext in (".docx", ".pptx"):
            converter = docx_to_markdown if ext == ".docx" else pptx_to_markdown
            md_content = await asyncio.to_thread(converter, tmp_path)
            with tempfile.NamedTemporaryFile(
                suffix=".md", delete=False, mode="w", encoding="utf-8"
            ) as md_tmp:
                md_tmp.write(md_content)
                md_tmp_path = md_tmp.name
            doc_id_local = await asyncio.to_thread(pi_client.index, md_tmp_path, "md")

        else:  # .md or .txt
            doc_id_local = await asyncio.to_thread(pi_client.index, tmp_path, "md")

        doc_data  = json.loads(pi_client.get_document(doc_id_local))
        structure = json.loads(pi_client.get_document_structure(doc_id_local))

        doc_id = str(uuid.uuid4())[:8]
        await asyncio.to_thread(save_raw, doc_id, filename, file_bytes)
        await asyncio.to_thread(save_doc, doc_id, {
            "doc_id":          doc_id,
            "filename":        filename,
            "source_url":      "",
            "processed_at":    datetime.now(timezone.utc).isoformat(),
            "tree":            structure,
            "doc_name":        doc_data.get("doc_name", ""),
            "doc_description": doc_data.get("doc_description", ""),
        })

        return json.dumps({
            "doc_id":   doc_id,
            "filename": filename,
            "message":  f"Document processed successfully. Use doc_id '{doc_id}' with other tools.",
        })

    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if md_tmp_path and os.path.exists(md_tmp_path):
            os.unlink(md_tmp_path)
