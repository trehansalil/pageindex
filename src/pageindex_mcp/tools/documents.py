"""MCP query tools: document listing, retrieval, and structured search."""

import json
import logging
import time

from fastmcp.exceptions import ToolError

from ..cache import get_doc
from ..config import settings
from ..helpers import (
    _build_node_map,
    _check_registry_complete_cached,
    _extract_page_hits,
    _rag,
    _strip_text,
    flat_doc_view,
)
from ..metrics import (
    DOCUMENTS_TOTAL,
    REGISTRY_FALLBACK_TOTAL,
    TOOL_CALLS,
    TOOL_DURATION,
    TOOL_ERRORS,
)
from ..tracing import trace_tool

logger = logging.getLogger(__name__)


class RegistryUnavailableError(RuntimeError):
    """The Postgres document registry cannot serve the read path.

    RFC-009 D6 (Design Property 7): the listing/query paths are registry-only —
    there is no MinIO fallback. When the registry is disabled, its pool is not
    initialised, the backfill is not yet complete, or a Postgres query fails, the
    read path raises this instead of silently degrading to an O(N) MinIO bucket
    scan (ISS-05). The MCP tool boundary (``find_relevant_documents`` /
    ``recent_documents``) turns it into a clean JSON error envelope.

    ``reason`` mirrors the REGISTRY_FALLBACK_TOTAL 'reason' label so the failure
    mode is observable in both metrics and the error surfaced to the caller.

    Reason vocabulary: ``disabled | pool_not_ready | backfill_incomplete |
    postgres_error | verdict_fail``. ``verdict_fail`` (Phase 3 audit Issue B)
    covers the case where the registry query succeeded but returned zero
    candidates because every match was filtered out as verdict='FAIL' (or the
    corpus itself is empty) — same clean-refusal treatment either way.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Document registry unavailable (reason={reason})")


async def _require_registry_ready() -> None:
    """Assert the Postgres registry can serve the read path, else raise.

    RFC-009 D6 replaces the old fall-back-to-MinIO decision: instead of returning
    False so the caller scans MinIO, every negative branch increments
    REGISTRY_FALLBACK_TOTAL with a 'reason' label (RFC-006 F4 — kept as the
    observability signal for *why* the registry was unavailable) and raises
    RegistryUnavailableError(reason).

    Shared by _list_docs_with_fallback() (full-list read path, find_relevant_
    documents) and _list_docs_paginated() (RFC-009 D3 server-side pagination),
    so the readiness gate lives in exactly one place.
    """
    if not settings.registry_enabled or not settings.postgres_dsn:
        REGISTRY_FALLBACK_TOTAL.labels(reason="disabled").inc()
        logger.warning("registry read path unavailable: registry disabled")
        raise RegistryUnavailableError("disabled")

    from ..registry import get_pool

    if get_pool() is None:
        REGISTRY_FALLBACK_TOTAL.labels(reason="pool_not_ready").inc()
        logger.warning("registry read path unavailable: pool not ready")
        raise RegistryUnavailableError("pool_not_ready")

    # Check the backfill-complete flag (RFC-008 D1: shared cached check in
    # helpers.py, uses the cache.py Redis singleton instead of an ad-hoc
    # connection, and a 60s TTL cache on a positive result).
    if not await _check_registry_complete_cached():
        REGISTRY_FALLBACK_TOTAL.labels(reason="backfill_incomplete").inc()
        logger.warning(
            "registry read path unavailable: backfill not complete "
            "(set pageindex:registry:complete in Redis via registry_backfill.py)"
        )
        raise RegistryUnavailableError("backfill_incomplete")


async def _list_docs_with_fallback() -> tuple[list[dict], bool]:
    """Return (docs, used_registry) for the *full-list* read path.

    RFC-009 D6 (Design Property 7): registry-only — the MinIO fallback is gone.
    Raises RegistryUnavailableError when the registry cannot serve the read path
    (disabled / pool down / backfill incomplete / Postgres query error); the MCP
    tool boundary (find_relevant_documents) turns that into a JSON error
    envelope. Used by find_relevant_documents, which needs every doc_id;
    recent_documents uses _list_docs_paginated() instead (RFC-009 D3).

    The ``used_registry`` element is retained for the caller's log line and is
    always True on the success path now that the fallback branch is removed.
    """
    await _require_registry_ready()

    from ..registry import list_docs

    # Registry is ready — fetch all rows (no server-side pagination at this level;
    # the caller wants the complete corpus).
    docs = await list_docs(limit=100_000, offset=0)
    if docs is None:
        REGISTRY_FALLBACK_TOTAL.labels(reason="postgres_error").inc()
        logger.error("_list_docs_with_fallback: registry query failed")
        raise RegistryUnavailableError("postgres_error")

    if not docs:
        # Phase 3 audit Issue B: list_docs already excludes verdict='FAIL' rows
        # at the SQL layer, so an empty result here means either an empty corpus
        # or every candidate was filtered out as FAIL-verdict. Refuse cleanly via
        # isError:true rather than the old silent "available": [] envelope.
        REGISTRY_FALLBACK_TOTAL.labels(reason="verdict_fail").inc()
        raise RegistryUnavailableError("verdict_fail")

    return docs, True


async def _list_docs_paginated(page: int, page_size: int) -> tuple[list[dict], int, bool]:
    """Return (page_docs, total, used_registry) for recent_documents (RFC-009 D3).

    Registry path: SQL ``LIMIT``/``OFFSET`` via ``registry.list_docs`` plus
    ``registry.count_docs`` for the corpus total — NO fetch-all-then-slice and
    NO tree deserialization (node_count comes straight off the row, D2).

    RFC-009 D6 (Design Property 7): registry-only — the MinIO fetch-all-then-slice
    fallback is removed. Raises RegistryUnavailableError when the registry cannot
    serve the read path; the MCP tool boundary (recent_documents) turns that into
    a JSON error envelope.
    """
    offset = (page - 1) * page_size

    await _require_registry_ready()

    from ..registry import count_docs, list_docs

    docs = await list_docs(limit=page_size, offset=offset)
    total = await count_docs()
    if docs is None or total is None:
        REGISTRY_FALLBACK_TOTAL.labels(reason="postgres_error").inc()
        logger.error("_list_docs_paginated: registry query failed")
        raise RegistryUnavailableError("postgres_error")

    return docs, total, True


async def recent_documents(page: int = 1, page_size: int = 10) -> str:
    """Browse your document collection with pagination. Returns documents sorted
    by upload date (newest first) with processing status."""
    TOOL_CALLS.labels(tool="recent_documents").inc()
    start = time.monotonic()
    logger.info("recent_documents called (page=%d, page_size=%d)", page, page_size)
    try:
        page_docs, total, used_registry = await _list_docs_paginated(page, page_size)
    except RegistryUnavailableError as e:
        # RFC-009 D6: registry-only listing — no MinIO fallback. Phase 3 audit
        # Issue B: real isError:true instead of a success-envelope "error" field,
        # so the calling LLM can distinguish refusal from an empty result set.
        TOOL_ERRORS.labels(tool="recent_documents").inc()
        logger.error("recent_documents: registry unavailable (reason=%s)", e.reason)
        raise ToolError(f"Document registry unavailable (reason={e.reason})") from e
    except Exception as e:
        TOOL_ERRORS.labels(tool="recent_documents").inc()
        logger.error("recent_documents failed to list docs: %s", e)
        return json.dumps({"error": f"Failed to list documents: {e}"})
    finally:
        elapsed = time.monotonic() - start
        TOOL_DURATION.labels(tool="recent_documents").observe(elapsed)
        logger.debug("recent_documents completed in %.3fs", elapsed)

    # RFC-009 D3: DOCUMENTS_TOTAL reflects the whole corpus (count_docs on the
    # registry path / len of the full MinIO listing on the fallback), NOT the
    # page_size-bounded slice we just fetched.
    DOCUMENTS_TOTAL.set(total)
    logger.info(
        "recent_documents: %d total docs (source=%s)",
        total,
        "registry" if used_registry else "minio",
    )

    # RFC-009 D3: node_count comes straight off the listing row (D2 sidecar /
    # registry column) — no get_doc()/tree deserialization per document. Legacy
    # docs predating the D2 backfill carry node_count=None → surface as 0.
    enriched = [
        {
            "doc_id": d["doc_id"],
            "doc_name": d.get("doc_name", "unknown"),
            "status": "completed",
            "node_count": d.get("node_count") or 0,
        }
        for d in page_docs
    ]

    logger.info("recent_documents returning %d/%d documents", len(enriched), total)
    return json.dumps(
        {
            "total": total,
            "page": page,
            "page_size": page_size,
            "documents": enriched,
        },
        indent=2,
    )


async def find_relevant_documents(query: str) -> str:
    """Search documents by query. Uses PageIndex reasoning-based tree search
    to find relevant content. Returns matching document excerpts and source
    metadata (doc_id, doc_name) as JSON — the caller synthesizes the answer."""
    TOOL_CALLS.labels(tool="find_relevant_documents").inc()
    start = time.monotonic()
    logger.info("find_relevant_documents called (query=%r)", query[:100])
    try:
        # LLM-02-C5: nest this request's prefilter + N concurrent search
        # generations under one Langfuse trace named for the tool. No-op when
        # tracing is disabled.
        async with trace_tool("find_relevant_documents"):
            list_t0 = time.monotonic()
            documents, used_registry = await _list_docs_with_fallback()
            logger.info(
                "find_relevant_documents TIMING: list_docs (source=%s) = %.3fs (%d docs)",
                "registry" if used_registry else "minio",
                time.monotonic() - list_t0,
                len(documents),
            )
            # _list_docs_with_fallback() now raises RegistryUnavailableError(
            # "verdict_fail") itself when the SQL query returns zero candidates,
            # so `documents` is always non-empty here (Phase 3 audit Issue B).
            return await _rag(query, [d["doc_id"] for d in documents])
    except RegistryUnavailableError as e:
        # RFC-009 D6: registry-only listing — no MinIO fallback. Phase 3 audit
        # Issue B: real isError:true instead of a success-envelope "error" field,
        # so the calling LLM can distinguish refusal from an empty result set.
        TOOL_ERRORS.labels(tool="find_relevant_documents").inc()
        logger.error("find_relevant_documents: registry unavailable (reason=%s)", e.reason)
        raise ToolError(f"Document registry unavailable (reason={e.reason})") from e
    except Exception as e:
        TOOL_ERRORS.labels(tool="find_relevant_documents").inc()
        logger.error("find_relevant_documents failed: %s", e, exc_info=True)
        raise
    finally:
        elapsed = time.monotonic() - start
        TOOL_DURATION.labels(tool="find_relevant_documents").observe(elapsed)
        logger.debug("find_relevant_documents completed in %.3fs", elapsed)


def get_document(doc_id: str) -> str:
    """Get detailed information about a specific document by doc_id. Requires
    doc_id (string). Use recent_documents() to find available doc_ids."""
    TOOL_CALLS.labels(tool="get_document").inc()
    start = time.monotonic()
    logger.info("get_document called (doc_id=%s)", doc_id)
    try:
        data = get_doc(doc_id)
    except Exception:
        TOOL_ERRORS.labels(tool="get_document").inc()
        logger.warning("get_document: doc %s not found", doc_id)
        return json.dumps({"error": f"Document not found: {doc_id}"})
    finally:
        elapsed = time.monotonic() - start
        TOOL_DURATION.labels(tool="get_document").observe(elapsed)
        logger.debug("get_document completed in %.3fs", elapsed)

    # FLAT-05-C2 (Step 5 integration): a flat doc carries a content_class and no
    # tree — return its verbalized blocks/row_records instead of an (empty) node
    # map. flat_doc_view returns None for a tree doc, so the existing path below
    # is unchanged for tree docs (boundary). HR1: retrieval surface, not accuracy.
    flat = flat_doc_view(data)
    if flat is not None:
        logger.info(
            "get_document: %s is a flat doc (content_class=%s)", doc_id, flat["content_class"]
        )
        return json.dumps(
            {
                "doc_id": doc_id,
                "doc_name": flat["doc_name"],
                "status": "completed",
                "content_class": flat["content_class"],
                "total_nodes": 0,
                "blocks": flat["blocks"],
                "row_records": flat["row_records"],
            },
            indent=2,
        )

    structure = data.get("structure", [])
    nm: dict = {}
    _build_node_map(structure, nm)

    logger.info("get_document: %s has %d nodes", doc_id, len(nm))
    return json.dumps(
        {
            "doc_id": doc_id,
            "doc_name": data.get("doc_name", data.get("filename", "unknown")),
            "status": "completed",
            "total_nodes": len(nm),
            "top_level_sections": [
                {
                    "title": n.get("title"),
                    "node_id": n.get("node_id"),
                    "pages": f"{n.get('start_index')}-{n.get('end_index')}",
                }
                for n in structure
            ],
        },
        indent=2,
    )


def get_document_structure(doc_id: str) -> str:
    """Extract the hierarchical structure of a completed document."""
    TOOL_CALLS.labels(tool="get_document_structure").inc()
    start = time.monotonic()
    logger.info("get_document_structure called (doc_id=%s)", doc_id)
    try:
        data = get_doc(doc_id)
    except Exception:
        TOOL_ERRORS.labels(tool="get_document_structure").inc()
        logger.warning("get_document_structure: doc %s not found", doc_id)
        return json.dumps({"error": f"Document not found: {doc_id}"})
    finally:
        elapsed = time.monotonic() - start
        TOOL_DURATION.labels(tool="get_document_structure").observe(elapsed)
        logger.debug("get_document_structure completed in %.3fs", elapsed)

    # FLAT-05-C2 (Step 5 integration): a flat doc exposes content_class +
    # blocks/row_records in place of an empty structure tree; tree docs unchanged.
    flat = flat_doc_view(data)
    if flat is not None:
        return json.dumps(
            {
                "doc_id": doc_id,
                "content_class": flat["content_class"],
                "structure": [],
                "blocks": flat["blocks"],
                "row_records": flat["row_records"],
            },
            indent=2,
        )

    return json.dumps(
        {
            "doc_id": doc_id,
            "structure": _strip_text(data.get("structure", [])),
        },
        indent=2,
    )


def get_page_content(doc_id: str, pages: str) -> str:
    """Extract specific page content from processed documents. Flexible page
    selection: single page ('5'), ranges ('3-7'), or multiple pages ('3,5,7')."""
    TOOL_CALLS.labels(tool="get_page_content").inc()
    start = time.monotonic()
    logger.info("get_page_content called (doc_id=%s, pages=%s)", doc_id, pages)
    try:
        data = get_doc(doc_id)
    except Exception:
        TOOL_ERRORS.labels(tool="get_page_content").inc()
        logger.warning("get_page_content: doc %s not found", doc_id)
        return json.dumps({"error": f"Document not found: {doc_id}"})
    finally:
        elapsed = time.monotonic() - start
        TOOL_DURATION.labels(tool="get_page_content").observe(elapsed)
        logger.debug("get_page_content completed in %.3fs", elapsed)

    hits = _extract_page_hits(data.get("structure", []), pages)

    if not hits:
        logger.warning("get_page_content: no content for pages %s in doc %s", pages, doc_id)
        return json.dumps({"error": f"No content found for pages '{pages}' in doc '{doc_id}'."})
    logger.info("get_page_content: returning %d hits for pages %s", len(hits), pages)
    return json.dumps({"doc_id": doc_id, "pages": pages, "content": hits}, indent=2)
