"""CustomPageIndexClient — multi-format document indexing with MinIO persistence."""

import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pageindex import PageIndexClient

from .config import settings
from .converters import docx_to_markdown, html_to_markdown_with_images, libreoffice_to_pdf, pptx_to_markdown
from .helpers import _build_node_map, _strip_text
from .storage import (
    list_processed_docs,
    load_doc,
    load_hash_cache,
    save_doc,
    save_doc_meta,
    save_hash_cache,
    save_raw,
)

logger = logging.getLogger(__name__)

_SUPPORTED = {".pdf", ".md", ".markdown", ".txt", ".docx", ".pptx", ".html"}


class CustomPageIndexClient(PageIndexClient):
    """
    Extends PageIndexClient to support .docx, .pptx, .html, and .txt formats
    and persist all indexed data to MinIO instead of a local filesystem workspace.

    Usage:
        client = CustomPageIndexClient()
        doc_id = await client.index("/path/to/file.docx")
        structure = await client.get_document_structure(doc_id)
    """

    def __init__(self, api_key: str = None, model: str = None, retrieve_model: str = None):
        super().__init__(api_key=api_key or settings.openai_api_key)
        self.model = model or settings.llm_model
        self.retrieve_model = retrieve_model
        # Serialises hash-cache reads/writes across parallel index() calls on this instance.
        self._cache_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    async def index(self, file_path: str, mode: str = "auto") -> str:
        """Index a document and persist it to MinIO. Returns the 8-char doc_id.

        Skips reprocessing if the file content is unchanged (SHA-256 dedup).
        Supported extensions: .pdf, .md, .markdown, .txt, .docx, .pptx, .html
        """
        file_path = os.path.abspath(file_path)
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        filename = os.path.basename(file_path)
        ext = Path(filename).suffix.lower()
        logger.info("Indexing file: %s (ext=%s)", filename, ext)

        if ext not in _SUPPORTED:
            raise ValueError(
                f"Unsupported format '{ext}'. Supported: {', '.join(sorted(_SUPPORTED))}"
            )

        file_bytes = await asyncio.to_thread(Path(file_path).read_bytes)
        sha256 = hashlib.sha256(file_bytes).hexdigest()
        logger.debug("File %s: size=%d bytes, sha256=%s", filename, len(file_bytes), sha256[:12])

        # Hash-based dedup: skip if content unchanged.
        # Lock prevents parallel calls from all seeing a cache-miss simultaneously.
        async with self._cache_lock:
            cache = await asyncio.to_thread(load_hash_cache)
            if cache.get(filename) == sha256:
                docs = await asyncio.to_thread(list_processed_docs)
                for d in docs:
                    if d.get("doc_name") == filename:
                        logger.info("Skipping %s (unchanged, existing doc_id=%s)", filename, d["doc_id"])
                        return d["doc_id"]

        # Convert / index
        tmp_lo_dir = None   # LibreOffice temp dir
        tmp_md_path = None  # HTML → markdown temp file

        try:
            if ext == ".pdf":
                logger.info("Running page_index on PDF: %s", filename)
                result = await asyncio.to_thread(self._run_page_index, file_path)

            elif ext in (".md", ".markdown", ".txt"):
                logger.info("Running md_to_tree on: %s", filename)
                result = await self._run_md_to_tree(file_path)

            elif ext in (".docx", ".pptx"):
                try:
                    logger.info("Converting %s to PDF via LibreOffice", filename)
                    pdf_path = await asyncio.to_thread(libreoffice_to_pdf, file_path)
                    tmp_lo_dir = os.path.dirname(pdf_path)
                    logger.info("Running page_index on converted PDF: %s", pdf_path)
                    result = await asyncio.to_thread(self._run_page_index, pdf_path)
                except Exception as lo_exc:
                    logger.warning(
                        "LibreOffice/page_index failed for %s (%s), falling back to markdown conversion",
                        filename, lo_exc,
                    )
                    if tmp_lo_dir:
                        shutil.rmtree(tmp_lo_dir, ignore_errors=True)
                        tmp_lo_dir = None
                    converter = docx_to_markdown if ext == ".docx" else pptx_to_markdown
                    md_content = await asyncio.to_thread(converter, file_path)
                    with tempfile.NamedTemporaryFile(
                        suffix=".md", delete=False, mode="w", encoding="utf-8"
                    ) as md_tmp:
                        md_tmp.write(md_content)
                        tmp_md_path = md_tmp.name
                    result = await self._run_md_to_tree(tmp_md_path)

            else:  # .html
                logger.info("Converting HTML to markdown: %s", filename)
                md_content = await html_to_markdown_with_images(file_path, self.model)
                with tempfile.NamedTemporaryFile(
                    suffix=".md", delete=False, mode="w", encoding="utf-8"
                ) as md_tmp:
                    md_tmp.write(md_content)
                    tmp_md_path = md_tmp.name
                result = await self._run_md_to_tree(tmp_md_path)

            # Persist raw file and processed result
            doc_id = str(uuid.uuid4())[:8]
            await asyncio.to_thread(save_raw, doc_id, filename, file_bytes)

            protocol = "https" if settings.minio_secure else "http"
            source_url = (
                f"{protocol}://{settings.minio_endpoint}"
                f"/{settings.minio_bucket}/uploads/{doc_id}/{filename}"
            )

            processed_at = datetime.now(timezone.utc).isoformat()
            await asyncio.to_thread(save_doc, doc_id, {
                "doc_id":          doc_id,
                "doc_name":        filename,
                "source_url":      source_url,
                "processed_at":    processed_at,
                "sha256":          sha256,
                "doc_description": result.get("doc_description", ""),
                "structure":       result.get("structure", []),
            })

            meta = {
                "doc_id":       doc_id,
                "doc_name":     filename,
                "source_url":   source_url,
                "processed_at": processed_at,
            }
            await asyncio.to_thread(save_doc_meta, doc_id, meta)

            # Reload before writing so we don't overwrite other parallel tasks' entries.
            async with self._cache_lock:
                cache = await asyncio.to_thread(load_hash_cache)
                cache[filename] = sha256
                await asyncio.to_thread(save_hash_cache, cache)

            logger.info("Indexed %s → doc_id=%s (%d sections)", filename, doc_id, len(result.get("structure", [])))
            return doc_id

        finally:
            if tmp_lo_dir:
                shutil.rmtree(tmp_lo_dir, ignore_errors=True)
            if tmp_md_path and os.path.exists(tmp_md_path):
                os.unlink(tmp_md_path)

    # ------------------------------------------------------------------
    # Retrieval (lazy-load from MinIO)
    # ------------------------------------------------------------------

    async def get_document(self, doc_id: str) -> str:
        """Return document metadata as a JSON string."""
        import json
        data = await asyncio.to_thread(load_doc, doc_id)
        structure = data.get("structure", [])
        return json.dumps({
            "doc_id":          doc_id,
            "doc_name":        data.get("doc_name", data.get("filename", "unknown")),
            "doc_description": data.get("doc_description", ""),
            "section_count":   len(structure),
            "sections": [
                {"title": n.get("title"), "node_id": n.get("node_id")}
                for n in structure
            ],
        }, indent=2)

    async def get_document_structure(self, doc_id: str) -> str:
        """Return document tree structure (without text fields) as a JSON string."""
        import json
        data = await asyncio.to_thread(load_doc, doc_id)
        return json.dumps({
            "doc_id":    doc_id,
            "structure": _strip_text(data.get("structure", [])),
        }, indent=2)

    async def get_page_content(self, doc_id: str, pages: str) -> str:
        """Return node text for the specified pages as a JSON string.

        pages: single page ('5'), range ('3-7'), or comma list ('3,5,7').
        """
        import json
        data = await asyncio.to_thread(load_doc, doc_id)
        nm: dict = {}
        _build_node_map(data.get("structure", []), nm)

        wanted: set[int] = set()
        for part in pages.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                wanted.update(range(int(a), int(b) + 1))
            else:
                wanted.add(int(part))

        hits = [
            {
                "node_id": nid,
                "title":   n.get("title"),
                "pages":   f"{n.get('start_index')}-{n.get('end_index')}",
                "text":    n["text"],
            }
            for nid, n in nm.items()
            if set(range(n.get("start_index", 0), n.get("end_index", 0) + 1)) & wanted
            and "text" in n
        ]

        if not hits:
            return json.dumps({"error": f"No content found for pages '{pages}' in doc '{doc_id}'."})
        return json.dumps({"doc_id": doc_id, "pages": pages, "content": hits}, indent=2)

    # ------------------------------------------------------------------
    # Private indexing helpers
    # ------------------------------------------------------------------

    def _run_page_index(self, pdf_path: str) -> dict:
        from pageindex import page_index
        return page_index(
            doc=pdf_path,
            model=self.model,
            if_add_node_id="yes",
            if_add_node_summary="yes",
            if_add_node_text="yes",
            if_add_doc_description="yes",
        )

    async def _run_md_to_tree(self, md_path: str) -> dict:
        from pageindex.page_index_md import md_to_tree

        coro = md_to_tree(
            md_path=md_path,
            if_thinning=False,
            if_add_node_summary="yes",
            summary_token_threshold=200,
            model=self.model,
            if_add_doc_description="yes",
            if_add_node_text="yes",
            if_add_node_id="yes",
        )
        # md_to_tree is a coroutine; if we're already in an event loop, await directly.
        # If called from a thread (asyncio.to_thread), spin a new loop.
        try:
            asyncio.get_running_loop()
            return await coro
        except RuntimeError:
            return asyncio.run(coro)
