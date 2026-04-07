"""MCP query tools: document listing, retrieval, and structured search."""

import json
import logging
import time

from ..helpers import _rag, _strip_text, _build_node_map
from ..metrics import (
    DOCUMENTS_TOTAL,
    TOOL_CALLS,
    TOOL_DURATION,
    TOOL_ERRORS,
)
from ..storage import list_processed_docs, load_doc

logger = logging.getLogger(__name__)


def recent_documents(page: int = 1, page_size: int = 10) -> str:
    """Browse your document collection with pagination. Returns documents sorted
    by upload date (newest first) with processing status."""
    TOOL_CALLS.labels(tool="recent_documents").inc()
    start = time.monotonic()
    logger.info("recent_documents called (page=%d, page_size=%d)", page, page_size)
    try:
        docs = list_processed_docs()
    except Exception as e:
        TOOL_ERRORS.labels(tool="recent_documents").inc()
        logger.error("recent_documents failed to list docs: %s", e)
        return json.dumps({"error": f"Failed to list documents: {e}"})
    finally:
        elapsed = time.monotonic() - start
        TOOL_DURATION.labels(tool="recent_documents").observe(elapsed)
        logger.debug("recent_documents completed in %.3fs", elapsed)

    DOCUMENTS_TOTAL.set(len(docs))

    begin = (page - 1) * page_size
    page_docs = docs[begin : begin + page_size]

    enriched = []
    for d in page_docs:
        doc_id = d["doc_id"]
        node_count = 0
        try:
            data = load_doc(doc_id)
            nm: dict = {}
            _build_node_map(data.get("structure", []), nm)
            node_count = len(nm)
        except Exception:
            logger.warning("recent_documents: failed to load doc %s for enrichment", doc_id)
        enriched.append({
            "doc_id":     doc_id,
            "doc_name":   d.get("doc_name", "unknown"),
            "status":     "completed",
            "node_count": node_count,
        })

    logger.info("recent_documents returning %d/%d documents", len(enriched), len(docs))
    return json.dumps({
        "total":     len(docs),
        "page":      page,
        "page_size": page_size,
        "documents": enriched,
    }, indent=2)


async def find_relevant_documents(query: str) -> str:
    """Search documents by query. Uses PageIndex reasoning-based tree search;
    automatically falls back to AI semantic search. Returns relevant content
    and a generated answer."""
    TOOL_CALLS.labels(tool="find_relevant_documents").inc()
    start = time.monotonic()
    logger.info("find_relevant_documents called (query=%r)", query[:100])
    try:
        documents = list_processed_docs()
        logger.info("find_relevant_documents: %d documents indexed", len(documents))
        if not documents:
            logger.warning("find_relevant_documents: no documents indexed")
            return "No documents are indexed. Process documents first."
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
        data = load_doc(doc_id)
    except Exception:
        TOOL_ERRORS.labels(tool="get_document").inc()
        logger.warning("get_document: doc %s not found", doc_id)
        available = [d["doc_id"] for d in list_processed_docs()]
        return json.dumps({"error": f"Document not found: {doc_id}", "available": available})
    finally:
        elapsed = time.monotonic() - start
        TOOL_DURATION.labels(tool="get_document").observe(elapsed)
        logger.debug("get_document completed in %.3fs", elapsed)

    structure = data.get("structure", [])
    nm: dict = {}
    _build_node_map(structure, nm)

    logger.info("get_document: %s has %d nodes", doc_id, len(nm))
    return json.dumps({
        "doc_id":             doc_id,
        "doc_name":           data.get("doc_name", data.get("filename", "unknown")),
        "status":             "completed",
        "total_nodes":        len(nm),
        "top_level_sections": [
            {
                "title":   n.get("title"),
                "node_id": n.get("node_id"),
                "pages":   f"{n.get('start_index')}-{n.get('end_index')}",
            }
            for n in structure
        ],
    }, indent=2)


def get_document_structure(doc_id: str) -> str:
    """Extract the hierarchical structure of a completed document."""
    TOOL_CALLS.labels(tool="get_document_structure").inc()
    start = time.monotonic()
    logger.info("get_document_structure called (doc_id=%s)", doc_id)
    try:
        data = load_doc(doc_id)
    except Exception:
        TOOL_ERRORS.labels(tool="get_document_structure").inc()
        logger.warning("get_document_structure: doc %s not found", doc_id)
        available = [d["doc_id"] for d in list_processed_docs()]
        return json.dumps({"error": f"Document not found: {doc_id}", "available": available})
    finally:
        elapsed = time.monotonic() - start
        TOOL_DURATION.labels(tool="get_document_structure").observe(elapsed)
        logger.debug("get_document_structure completed in %.3fs", elapsed)

    return json.dumps({
        "doc_id":    doc_id,
        "structure": _strip_text(data.get("structure", [])),
    }, indent=2)


def get_page_content(doc_id: str, pages: str) -> str:
    """Extract specific page content from processed documents. Flexible page
    selection: single page ('5'), ranges ('3-7'), or multiple pages ('3,5,7')."""
    TOOL_CALLS.labels(tool="get_page_content").inc()
    start = time.monotonic()
    logger.info("get_page_content called (doc_id=%s, pages=%s)", doc_id, pages)
    try:
        data = load_doc(doc_id)
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
            "title":   n.get("title"),
            "pages":   f"{n.get('start_index')}-{n.get('end_index')}",
            "text":    n["text"],
        }
        for nid, n in nm.items()
        if set(range(n.get("start_index", 0), n.get("end_index", 0) + 1)) & wanted
        and "text" in n
    ]

    if not hits:
        logger.warning("get_page_content: no content for pages %s in doc %s", pages, doc_id)
        return json.dumps({"error": f"No content found for pages '{pages}' in doc '{doc_id}'."})
    logger.info("get_page_content: returning %d hits for pages %s", len(hits), pages)
    return json.dumps({"doc_id": doc_id, "pages": pages, "content": hits}, indent=2)
