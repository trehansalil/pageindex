"""MCP query tools: document listing, retrieval, and structured search."""

import json

from ..helpers import _rag, _strip_text, _build_node_map
from ..storage import list_processed_docs, load_doc


def recent_documents(page: int = 1, page_size: int = 10) -> str:
    """Browse your document collection with pagination. Returns documents sorted
    by upload date (newest first) with processing status."""
    try:
        docs = list_processed_docs()
    except Exception as e:
        return json.dumps({"error": f"Failed to list documents: {e}"})

    start = (page - 1) * page_size
    page_docs = docs[start : start + page_size]

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
    documents = list_processed_docs()
    if not documents:
        return "No documents are indexed. Process documents first."
    return await _rag(query, [d["doc_id"] for d in documents])


def get_document(doc_id: str) -> str:
    """Get detailed information about a specific document by doc_id. Requires
    doc_id (string). Use recent_documents() to find available doc_ids."""
    try:
        data = load_doc(doc_id)
    except ValueError as e:
        available = [d["doc_id"] for d in list_processed_docs()]
        return json.dumps({"error": str(e), "available": available})

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
    try:
        data = load_doc(doc_id)
    except ValueError as e:
        available = [d["doc_id"] for d in list_processed_docs()]
        return json.dumps({"error": str(e), "available": available})

    return json.dumps({
        "doc_id":    doc_id,
        "structure": _strip_text(data.get("structure", [])),
    }, indent=2)


def get_page_content(doc_id: str, pages: str) -> str:
    """Extract specific page content from processed documents. Flexible page
    selection: single page ('5'), ranges ('3-7'), or multiple pages ('3,5,7')."""
    try:
        data = load_doc(doc_id)
    except ValueError as e:
        available = [d["doc_id"] for d in list_processed_docs()]
        return json.dumps({"error": str(e), "available": available})

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
