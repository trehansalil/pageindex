"""CustomPageIndexClient — multi-format document indexing with MinIO persistence."""

import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import openai
from pageindex import PageIndexClient

from .cache import get_doc
from .config import settings
from .converters import (
    docx_to_markdown,
    html_to_markdown_with_images,
    libreoffice_to_pdf,
    pdf_markdown_converters,
    pptx_to_markdown,
)
from .helpers import (
    LowQualityTreeError,
    _build_node_map,
    _strip_text,
    route_and_extract_flat,
    validate_tree,
)
from .metrics import (
    FLAT_DOCS_TOTAL,
    LOW_QUALITY_TREES,
    PDF_EXTRACT_FALLBACKS,
    PDF_PRIMARY_CONVERTER_FAILURES,
)
from .storage import (
    list_processed_docs,
    load_hash_cache,
    save_doc,
    save_doc_meta,
    save_flat_doc,
    save_hash_cache,
    save_raw,
)

logger = logging.getLogger(__name__)

_SUPPORTED = {".pdf", ".md", ".markdown", ".txt", ".docx", ".pptx", ".html"}


def _is_azure_url(url: str | None) -> bool:
    """Return True when the base URL points to Azure OpenAI."""
    return bool(url and ".openai.azure.com" in url)


def resolve_llm_provider() -> str:
    """LLM-01-C1: Resolve the effective provider: 'openai' | 'compatible' | 'azure'.

    LLM_PROVIDER=auto (default) infers 'azure' from the base URL else 'openai'.
    An explicit openai/compatible/azure value is honored verbatim. 'compatible'
    shares the OpenAI code path (AsyncOpenAI / litellm openai provider + a custom
    base_url); the distinct name exists for validation and documentation when the
    base URL is not the canonical api.openai.com endpoint.

    Any other explicit value is rejected with ValueError so an operator typo
    fails fast at startup instead of being silently auto-routed to the wrong
    backend.
    """
    provider = (settings.llm_provider or "auto").strip().lower()
    if provider in ("openai", "compatible", "azure"):
        return provider
    if provider not in ("", "auto"):
        raise ValueError(
            f"Invalid LLM_PROVIDER={settings.llm_provider!r}; "
            "expected one of: auto, openai, compatible, azure."
        )
    return "azure" if _is_azure_url(settings.openai_base_url) else "openai"


def get_openai_client() -> openai.AsyncOpenAI:
    """LLM-01-C2/C3: Return an AsyncOpenAI/AsyncAzureOpenAI client for the provider.

    Used by the query path (helpers._llm). For openai/compatible providers the
    configured OPENAI_BASE_URL is passed verbatim, so any OpenAI-compatible
    endpoint (vLLM, Together, Groq, OpenRouter, local) works unchanged.
    """
    if resolve_llm_provider() == "azure":
        return openai.AsyncAzureOpenAI(
            api_key=settings.openai_api_key,
            azure_endpoint=settings.openai_base_url,
            api_version=settings.azure_api_version or "2024-08-01-preview",
        )
    return openai.AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )


def configure_litellm() -> None:
    """LLM-01-C4: Point the fork's bare litellm calls at the configured endpoint.

    The pageindex fork calls ``litellm.completion(model=...)`` with no ``api_base``
    (utils.llm_completion / llm_acompletion), so litellm would otherwise resolve the
    base from the environment alone. We set ``litellm.api_base``/``api_key`` (and the
    Azure env litellm requires) explicitly, so the ingestion path deterministically
    targets the same endpoint as the query path. Call once at the converters_cli
    subprocess entry, before client.index().
    """
    import litellm

    if resolve_llm_provider() == "azure":
        # litellm routes Azure only when the model name is ``azure/<deployment>``
        # (operator sets PAGEINDEX_MODEL accordingly); it reads these env vars.
        litellm.api_base = settings.openai_base_url
        if settings.openai_base_url:
            os.environ["AZURE_API_BASE"] = settings.openai_base_url
        if settings.openai_api_key:
            os.environ["AZURE_API_KEY"] = settings.openai_api_key
        os.environ["AZURE_API_VERSION"] = settings.azure_api_version or "2024-08-01-preview"
        return
    litellm.api_base = settings.openai_base_url
    litellm.api_key = settings.openai_api_key


def validate_llm_config() -> None:
    """LLM-01-C5: Fail fast on an inconsistent LLM provider configuration.

    Raises ValueError so a misconfiguration surfaces at startup rather than as an
    opaque litellm/SDK error mid-ingestion.
    """
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY (or CHATGPT_API_KEY) is required for LLM calls.")
    if not settings.openai_base_url:
        raise ValueError(
            f"LLM_PROVIDER={resolve_llm_provider()} requires OPENAI_BASE_URL to be set."
        )


class CustomPageIndexClient(PageIndexClient):
    """
    Extends PageIndexClient to support .docx, .pptx, .html, and .txt formats
    and persist all indexed data to MinIO instead of a local filesystem workspace.

    Usage:
        client = CustomPageIndexClient()
        doc_id = await client.index("/path/to/file.docx")
        structure = await client.get_document_structure(doc_id)
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        retrieve_model: str | None = None,
    ):
        super().__init__(api_key=api_key or settings.openai_api_key)
        self.model = model or settings.llm_model
        self.retrieve_model = retrieve_model
        # Serialises hash-cache reads/writes across parallel index() calls on this instance.
        self._cache_lock = asyncio.Lock()
        # RFC-004 Amendment 1 (Step 5 integration): set to the deterministic
        # content_class when index() routes a doc to the flat success path; stays
        # None for a normal tree doc. converters_cli reads this after index()
        # returns so the worker job hash can carry content_class (FLAT-04-C1).
        self.last_content_class: str | None = None

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    # Complexity grandfathered (core indexing pipeline); see pyproject [tool.ruff].
    async def index(self, file_path: str, mode: str = "auto") -> str:  # noqa: C901, PLR0915
        """Index a document and persist it to MinIO. Returns the 8-char doc_id.

        Skips reprocessing if the file content is unchanged (SHA-256 dedup).
        Supported extensions: .pdf, .md, .markdown, .txt, .docx, .pptx, .html
        """
        # Reset per call so a prior flat doc's content_class can't leak into a
        # subsequent tree doc when this client instance is reused. The flat
        # routing path re-sets it below when (and only when) it applies.
        self.last_content_class = None

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
                        logger.info(
                            "Skipping %s (unchanged, existing doc_id=%s)", filename, d["doc_id"]
                        )
                        # FLAT-04 parity: the SHA-dedup early return must restore
                        # last_content_class (reset to None at the top of index())
                        # so an unchanged flat doc still surfaces content_class in
                        # the converters_cli stdout payload, matching a non-deduped
                        # flat index (cubic PR #9).
                        self.last_content_class = d.get("content_class") or None
                        return d["doc_id"]

        # Convert / index
        tmp_lo_dir = None  # LibreOffice temp dir
        tmp_md_path = None  # HTML → markdown temp file
        md_content = None  # FLAT-03: converter markdown for the flat-routing branch

        try:
            if ext == ".pdf":
                # INDEX-01-C1/C2: try the config-ordered markdown converters
                # (pymupdf4llm / docling, per PDF_CONVERTER), then fall back to
                # the legacy page_index route only if every converter fails.
                md_content = None
                chain = pdf_markdown_converters()
                primary_name = chain[0][0] if chain else None
                used_converter = None
                for idx, (conv_name, conv_fn) in enumerate(chain):
                    try:
                        logger.info("Extracting PDF to markdown via %s: %s", conv_name, filename)
                        md_content = await asyncio.to_thread(conv_fn, file_path)
                        used_converter = conv_name
                        break
                    except Exception as conv_exc:
                        md_content = None
                        if idx == 0:
                            # The CONFIGURED PRIMARY converter failed. Never let this be
                            # masked downstream as a generic "depth<2": log it loudly with
                            # the full traceback (import / model-weights / convert errors)
                            # and a dedicated metric so it is alertable and unambiguous.
                            PDF_PRIMARY_CONVERTER_FAILURES.labels(
                                converter=conv_name, error=type(conv_exc).__name__
                            ).inc()
                            logger.error(
                                "PRIMARY PDF converter '%s' FAILED for %s (%s: %s); falling "
                                "back to the next converter — output quality will likely "
                                "degrade. If this is docling, verify model artifacts are "
                                "present (DOCLING_ARTIFACTS_PATH or network egress) and the "
                                "docling-hierarchical-pdf add-on is installed in THIS image.",
                                conv_name,
                                filename,
                                type(conv_exc).__name__,
                                conv_exc,
                                exc_info=True,
                            )
                        else:
                            logger.warning(
                                "%s failed for %s (%s); trying next converter",
                                conv_name,
                                filename,
                                conv_exc,
                            )
                if md_content is not None:
                    if primary_name is not None and used_converter != primary_name:
                        # We produced markdown, but NOT with the configured primary. Any
                        # resulting flat/garbled tree is a converter problem, not a generic
                        # low-quality document — say so explicitly.
                        logger.error(
                            "PDF %s extracted by FALLBACK converter '%s' because primary "
                            "'%s' failed; a flat 'depth<2' tree downstream is a CONVERTER "
                            "failure, not a low-quality source. Fix the primary converter.",
                            filename,
                            used_converter,
                            primary_name,
                        )
                    with tempfile.NamedTemporaryFile(
                        suffix=".md", delete=False, mode="w", encoding="utf-8"
                    ) as md_tmp:
                        md_tmp.write(md_content)
                        tmp_md_path = md_tmp.name
                    result = await self._run_md_to_tree(tmp_md_path)
                else:
                    PDF_EXTRACT_FALLBACKS.inc()
                    logger.error(
                        "ALL markdown converters failed for %s; falling back to legacy "
                        "page_index. Investigate converter availability in this image.",
                        filename,
                    )
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
                        "LibreOffice/page_index failed for %s (%s), falling back to "
                        "markdown conversion",
                        filename,
                        lo_exc,
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

            # HR5 / WORKER-01-C2: never silently persist a low-quality tree.
            ok, reason = validate_tree(result.get("structure", []))
            if not ok:
                # FLAT-03-C1: a non-garbling rejection (node_count<3 / depth<2) is a
                # *flat* document, not a defective one — route it to the flat success
                # path instead of raising. FLAT-03-C2: 'garbling' is the only remaining
                # terminal low_quality_tree reason and always raises. FLAT-03-C3: the
                # flat_doc_routing kill-switch reverts to legacy reject-on-any-failure.
                if settings.flat_doc_routing and reason in ("node_count<3", "depth<2"):
                    flat_md = md_content
                    if flat_md is None and tmp_md_path is not None:
                        flat_md = await asyncio.to_thread(
                            lambda p: Path(p).read_text(encoding="utf-8", errors="replace"),
                            tmp_md_path,
                        )
                    if flat_md is None and ext in (".md", ".markdown", ".txt"):
                        # The input itself is plain text/markdown (the md_to_tree route
                        # writes no tmp_md_path) — reading it directly is safe.
                        flat_md = await asyncio.to_thread(
                            lambda p: Path(p).read_text(encoding="utf-8", errors="replace"),
                            file_path,
                        )
                    # FLAT-03 follow-up guard (QA-flagged): route to the flat success
                    # path ONLY with genuine extracted text. When flat_md is still None the
                    # doc is a BINARY input (PDF/docx) that fell to the legacy page_index
                    # route with no markdown produced; the only remaining source would be
                    # the raw input file, and reading its raw bytes as text (errors=
                    # "replace") would feed binary garbling into route_and_extract_flat and
                    # fabricate a bogus flat doc. Fall through to the HR5 low_quality_tree
                    # reject below instead — a binary doc with no extractable text layer is
                    # genuinely low-quality, not flat.
                    if flat_md is not None:
                        content_class, blocks = await asyncio.to_thread(
                            route_and_extract_flat, flat_md
                        )
                        logger.info(
                            "Routing %s to flat success path: reason=%s content_class=%s",
                            filename,
                            reason,
                            content_class,
                        )

                        doc_id = str(uuid.uuid4())[:8]
                        await asyncio.to_thread(save_raw, doc_id, filename, file_bytes)

                        protocol = "https" if settings.minio_secure else "http"
                        source_url = (
                            f"{protocol}://{settings.minio_endpoint}"
                            f"/{settings.minio_bucket}/uploads/{doc_id}/{filename}"
                        )
                        processed_at = datetime.now(UTC).isoformat()

                        # FLAT-03-C1: persist via save_flat_doc only — never save_doc, so
                        # no tree artifact processed/<doc_id>.json is written (HR2: no
                        # un-cascaded derivative).
                        await asyncio.to_thread(
                            save_flat_doc,
                            doc_id,
                            {
                                "doc_id": doc_id,
                                "doc_name": filename,
                                "source_url": source_url,
                                "processed_at": processed_at,
                                "sha256": sha256,
                                "content_class": content_class,
                                "blocks": blocks,
                            },
                        )
                        FLAT_DOCS_TOTAL.labels(content_class=content_class).inc()

                        # Reload before writing so we don't overwrite parallel tasks' entries.
                        async with self._cache_lock:
                            cache = await asyncio.to_thread(load_hash_cache)
                            cache[filename] = sha256
                            await asyncio.to_thread(save_hash_cache, cache)

                        logger.info(
                            "Indexed flat doc %s → doc_id=%s (content_class=%s, %d blocks)",
                            filename,
                            doc_id,
                            content_class,
                            len(blocks),
                        )
                        # Step 5 integration: surface content_class to converters_cli
                        # (subprocess reads this after index() returns → worker hash).
                        self.last_content_class = content_class
                        return doc_id

                LOW_QUALITY_TREES.labels(reason=reason).inc()
                logger.warning("Rejecting low-quality tree for %s: reason=%s", filename, reason)
                raise LowQualityTreeError(reason)

            # Persist raw file and processed result
            doc_id = str(uuid.uuid4())[:8]
            await asyncio.to_thread(save_raw, doc_id, filename, file_bytes)

            protocol = "https" if settings.minio_secure else "http"
            source_url = (
                f"{protocol}://{settings.minio_endpoint}"
                f"/{settings.minio_bucket}/uploads/{doc_id}/{filename}"
            )

            processed_at = datetime.now(UTC).isoformat()
            await asyncio.to_thread(
                save_doc,
                doc_id,
                {
                    "doc_id": doc_id,
                    "doc_name": filename,
                    "source_url": source_url,
                    "processed_at": processed_at,
                    "sha256": sha256,
                    "doc_description": result.get("doc_description", ""),
                    "structure": result.get("structure", []),
                },
            )

            meta = {
                "doc_id": doc_id,
                "doc_name": filename,
                "source_url": source_url,
                "processed_at": processed_at,
            }
            await asyncio.to_thread(save_doc_meta, doc_id, meta)

            # Reload before writing so we don't overwrite other parallel tasks' entries.
            async with self._cache_lock:
                cache = await asyncio.to_thread(load_hash_cache)
                cache[filename] = sha256
                await asyncio.to_thread(save_hash_cache, cache)

            logger.info(
                "Indexed %s → doc_id=%s (%d sections)",
                filename,
                doc_id,
                len(result.get("structure", [])),
            )
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

        data = await asyncio.to_thread(get_doc, doc_id)
        structure = data.get("structure", [])
        return json.dumps(
            {
                "doc_id": doc_id,
                "doc_name": data.get("doc_name", data.get("filename", "unknown")),
                "doc_description": data.get("doc_description", ""),
                "section_count": len(structure),
                "sections": [
                    {"title": n.get("title"), "node_id": n.get("node_id")} for n in structure
                ],
            },
            indent=2,
        )

    async def get_document_structure(self, doc_id: str) -> str:
        """Return document tree structure (without text fields) as a JSON string."""
        import json

        data = await asyncio.to_thread(get_doc, doc_id)
        return json.dumps(
            {
                "doc_id": doc_id,
                "structure": _strip_text(data.get("structure", [])),
            },
            indent=2,
        )

    async def get_page_content(self, doc_id: str, pages: str) -> str:
        """Return node text for the specified pages as a JSON string.

        pages: single page ('5'), range ('3-7'), or comma list ('3,5,7').
        """
        import json

        data = await asyncio.to_thread(get_doc, doc_id)
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
                "title": n.get("title"),
                "pages": f"{n.get('start_index')}-{n.get('end_index')}",
                "text": n["text"],
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
