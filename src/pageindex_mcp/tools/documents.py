"""MCP query tools: document listing, retrieval, and structured search."""

import json
import logging
import time

from ..cache import get_doc
from ..config import settings
from ..helpers import _build_node_map, _rag, _strip_text, flat_doc_view
from ..metrics import (
    DOCUMENTS_TOTAL,
    REGISTRY_FALLBACK_TOTAL,
    TOOL_CALLS,
    TOOL_DURATION,
    TOOL_ERRORS,
)
from ..storage import list_processed_docs
from ..tracing import trace_tool

logger = logging.getLogger(__name__)


async def _list_docs_with_fallback() -> tuple[list[dict], bool]:
    """Return (docs, used_registry).

    RFC-006 F4/F5: attempt to read from the Postgres registry; fall back to the
    MinIO listing path when:
      - REGISTRY_ENABLED=False or POSTGRES_DSN not set
      - the Postgres pool is not initialised
      - the registry backfill is not yet complete (registry_complete flag absent)
      - any Postgres error occurs

    Each fallback path increments REGISTRY_FALLBACK_TOTAL with a 'reason' label
    so under-coverage is never silent (RFC-006 F4).
    """
    if not settings.registry_enabled or not settings.postgres_dsn:
        REGISTRY_FALLBACK_TOTAL.labels(reason="disabled").inc()
        logger.debug("_list_docs_with_fallback: registry disabled — using MinIO listing")
        return list_processed_docs(), False

    from ..registry import get_pool, is_registry_complete, list_docs

    pool = get_pool()
    if pool is None:
        REGISTRY_FALLBACK_TOTAL.labels(reason="pool_not_ready").inc()
        logger.warning(
            "_list_docs_with_fallback: registry pool not ready — falling back to MinIO listing"
        )
        return list_processed_docs(), False

    # Check the backfill-complete flag from Redis before trusting the registry.
    # Importing cache lazily avoids a circular import (cache → storage → tools).
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url, decode_responses=False)
        complete = await is_registry_complete(r)
        await r.aclose()
    except Exception as exc:
        logger.warning("_list_docs_with_fallback: Redis error checking registry flag: %s", exc)
        complete = False

    if not complete:
        REGISTRY_FALLBACK_TOTAL.labels(reason="backfill_incomplete").inc()
        logger.warning(
            "_list_docs_with_fallback: registry backfill not complete — "
            "falling back to MinIO listing (set pageindex:registry:complete in Redis "
            "via registry_backfill.py to switch over)"
        )
        return list_processed_docs(), False

    # Registry is ready — fetch all rows (no server-side pagination at this level;
    # the tool layer does its own page slicing after receiving the full list).
    docs = await list_docs(limit=100_000, offset=0)
    if docs is None:
        REGISTRY_FALLBACK_TOTAL.labels(reason="postgres_error").inc()
        logger.warning(
            "_list_docs_with_fallback: registry query failed — falling back to MinIO listing"
        )
        return list_processed_docs(), False

    return docs, True


async def recent_documents(page: int = 1, page_size: int = 10) -> str:
    """Browse your document collection with pagination. Returns documents sorted
    by upload date (newest first) with processing status."""
    TOOL_CALLS.labels(tool="recent_documents").inc()
    start = time.monotonic()
    logger.info("recent_documents called (page=%d, page_size=%d)", page, page_size)
    try:
        docs, used_registry = await _list_docs_with_fallback()
    except Exception as e:
        TOOL_ERRORS.labels(tool="recent_documents").inc()
        logger.error("recent_documents failed to list docs: %s", e)
        return json.dumps({"error": f"Failed to list documents: {e}"})
    finally:
        elapsed = time.monotonic() - start
        TOOL_DURATION.labels(tool="recent_documents").observe(elapsed)
        logger.debug("recent_documents completed in %.3fs", elapsed)

    DOCUMENTS_TOTAL.set(len(docs))
    logger.info(
        "recent_documents: %d total docs (source=%s)",
        len(docs),
        "registry" if used_registry else "minio",
    )

    begin = (page - 1) * page_size
    page_docs = docs[begin : begin + page_size]

    enriched = []
    for d in page_docs:
        doc_id = d["doc_id"]
        node_count = 0
        try:
            data = get_doc(doc_id)
            nm: dict = {}
            _build_node_map(data.get("structure", []), nm)
            node_count = len(nm)
        except Exception:
            logger.warning("recent_documents: failed to load doc %s for enrichment", doc_id)
        enriched.append(
            {
                "doc_id": doc_id,
                "doc_name": d.get("doc_name", "unknown"),
                "status": "completed",
                "node_count": node_count,
            }
        )

    logger.info("recent_documents returning %d/%d documents", len(enriched), len(docs))
    return json.dumps(
        {
            "total": len(docs),
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
            if not documents:
                logger.warning("find_relevant_documents: no documents indexed")
                TOOL_ERRORS.labels(tool="find_relevant_documents").inc()
                return json.dumps(
                    {
                        "error": "No documents are indexed. Process documents first.",
                        "available": [],
                    }
                )
            return await _rag(query, [d["doc_id"] for d in documents])
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
        available = [d["doc_id"] for d in list_processed_docs()]
        return json.dumps({"error": f"Document not found: {doc_id}", "available": available})
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
        available = [d["doc_id"] for d in list_processed_docs()]
        return json.dumps({"error": f"Document not found: {doc_id}", "available": available})
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
        available = [d["doc_id"] for d in list_processed_docs()]
        return json.dumps({"error": f"Document not found: {doc_id}", "available": available})
    finally:
        elapsed = time.monotonic() - start
        TOOL_DURATION.labels(tool="get_page_content").observe(elapsed)
        logger.debug("get_page_content completed in %.3fs", elapsed)

    wanted: set[int] = set()
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            wanted.update(range(int(a), int(b) + 1))
        else:
            wanted.add(int(part))

    nm: dict = {}
    _build_node_map(data.get("structure", []), nm)

    hits = [
        {
            "node_id": nid,
            "title": n.get("title"),
            "pages": f"{n.get('start_index')}-{n.get('end_index')}",
            "text": n["text"],
        }
        for nid, n in nm.items()
        if set(range(n.get("start_index", 0), n.get("end_index", 0) + 1)) & wanted and "text" in n
    ]

    if not hits:
        logger.warning("get_page_content: no content for pages %s in doc %s", pages, doc_id)
        return json.dumps({"error": f"No content found for pages '{pages}' in doc '{doc_id}'."})
    logger.info("get_page_content: returning %d hits for pages %s", len(hits), pages)
    return json.dumps({"doc_id": doc_id, "pages": pages, "content": hits}, indent=2)
