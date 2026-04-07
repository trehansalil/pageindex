"""MCP query tools: document listing, retrieval, and structured search."""

import json
import time

from ..helpers import _rag, _strip_text, _build_node_map
from ..metrics import (
    DOCUMENTS_TOTAL,
    TOOL_CALLS,
    TOOL_DURATION,
    TOOL_ERRORS,
)
from ..storage import list_processed_docs, load_doc


def recent_documents(page: int = 1, page_size: int = 10) -> str:
    """Browse your document collection with pagination. Returns documents sorted
    by upload date (newest first) with processing status."""
    TOOL_CALLS.labels(tool="recent_documents").inc()
    start = time.monotonic()
    try:
        docs = list_processed_docs()
    except Exception as e:
        TOOL_ERRORS.labels(tool="recent_documents").inc()
        return json.dumps({"error": f"Failed to list documents: {e}"})
    finally:
        TOOL_DURATION.labels(tool="recent_documents").observe(time.monotonic() - start)

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
            pass
        enriched.append({
            "doc_id":     doc_id,
            "doc_name":   d.get("doc_name", "unknown"),
            "status":     "completed",
            "node_count": node_count,
        })

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
    try:
        documents = list_processed_docs()
        if not documents:
            return "No documents are indexed. Process documents first."
        return await _rag(query, [d["doc_id"] for d in documents])
    except Exception as e:
        TOOL_ERRORS.labels(tool="find_relevant_documents").inc()
        raise
    finally:
        TOOL_DURATION.labels(tool="find_relevant_documents").observe(time.monotonic() - start)


def get_document(doc_id: str) -> str:
    """Get detailed information about a specific document by doc_id. Requires
    doc_id (string). Use recent_documents() to find available doc_ids."""
    TOOL_CALLS.labels(tool="get_document").inc()
    start = time.monotonic()
    try:
        data = load_doc(doc_id)
    except Exception:
        TOOL_ERRORS.labels(tool="get_document").inc()
        available = [d["doc_id"] for d in list_processed_docs()]
        return json.dumps({"error": f"Document not found: {doc_id}", "available": available})
    finally:
        TOOL_DURATION.labels(tool="get_document").observe(time.monotonic() - start)

    structure = data.get("structure", [])
    nm: dict = {}
    _build_node_map(structure, nm)

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
    try:
        data = load_doc(doc_id)
    except Exception:
        TOOL_ERRORS.labels(tool="get_document_structure").inc()
        available = [d["doc_id"] for d in list_processed_docs()]
        return json.dumps({"error": f"Document not found: {doc_id}", "available": available})
    finally:
        TOOL_DURATION.labels(tool="get_document_structure").observe(time.monotonic() - start)

    return json.dumps({
        "doc_id":    doc_id,
        "structure": _strip_text(data.get("structure", [])),
    }, indent=2)


def get_page_content(doc_id: str, pages: str) -> str:
    """Extract specific page content from processed documents. Flexible page
    selection: single page ('5'), ranges ('3-7'), or multiple pages ('3,5,7')."""
    TOOL_CALLS.labels(tool="get_page_content").inc()
    start = time.monotonic()
    try:
        data = load_doc(doc_id)
    except Exception:
        TOOL_ERRORS.labels(tool="get_page_content").inc()
        available = [d["doc_id"] for d in list_processed_docs()]
        return json.dumps({"error": f"Document not found: {doc_id}", "available": available})
    finally:
        TOOL_DURATION.labels(tool="get_page_content").observe(time.monotonic() - start)

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
        return json.dumps({"error": f"No content found for pages '{pages}' in doc '{doc_id}'."})
    return json.dumps({"doc_id": doc_id, "pages": pages, "content": hits}, indent=2)
