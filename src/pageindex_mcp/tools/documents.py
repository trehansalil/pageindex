"""MCP tools: document listing, summary, search, deletion, and preloaded sync."""

import asyncio
import json

from ..converters import flatten_nodes
from ..storage import (
    delete_doc,
    list_processed_docs,
    load_doc,
    sync_preloaded_to_minio,
)


def list_documents() -> str:
    """
    List all documents that have been processed and stored in MinIO.
    Returns doc_id, filename, and processing timestamp for each document.
    """
    try:
        docs = list_processed_docs()
    except Exception as e:
        return json.dumps({"error": f"Failed to list documents: {e}"})

    if not docs:
        return json.dumps({"documents": [], "message": "No documents processed yet. Use process_document first."})
    return json.dumps({"documents": docs, "count": len(docs)})


def get_document_summary(doc_id: str) -> str:
    """
    Get a comprehensive summary of a processed document.
    Returns the top-level structure, section titles, and any available summaries.
    Use list_documents() to find available doc_ids.
    """
    try:
        data = load_doc(doc_id)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    tree     = data.get("tree", [])
    filename = data.get("filename", "unknown")

    sections = []
    for node in tree:
        sections.append({
            "title":       node.get("title", ""),
            "summary":     node.get("summary", ""),
            "pages":       f"{node.get('start_index', '?')}–{node.get('end_index', '?')}",
            "subsections": len(node.get("nodes", [])),
        })

    return json.dumps({
        "doc_id":         doc_id,
        "filename":       filename,
        "total_sections": len(sections),
        "sections":       sections,
    })


def search_document(doc_id: str, query: str) -> str:
    """
    Search within a processed document for sections matching the query.
    Searches section titles and summaries using keyword matching.
    Returns matching sections with their summaries and page ranges.
    Use list_documents() to find available doc_ids.
    """
    try:
        data = load_doc(doc_id)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    tree        = data.get("tree", [])
    query_lower = query.lower()

    results: list = []
    flatten_nodes(tree, results, query_lower)

    if not results:
        return json.dumps({
            "doc_id":  doc_id,
            "query":   query,
            "matches": [],
            "message": "No matching sections found. Try a different query or use get_document_summary for an overview.",
        })

    return json.dumps({
        "doc_id":      doc_id,
        "query":       query,
        "match_count": len(results),
        "matches":     results[:20],
    })


async def delete_document(doc_id: str) -> str:
    """
    Delete a processed document and its raw upload from MinIO.
    Use list_documents() to find available doc_ids.
    """
    try:
        load_doc(doc_id)  # verify exists
    except ValueError as e:
        return json.dumps({"error": str(e)})
    await asyncio.to_thread(delete_doc, doc_id)
    return json.dumps({"message": f"Document '{doc_id}' deleted successfully."})


def sync_preloaded_documents() -> str:
    """
    Upload any files in the local doc_store/ directory to MinIO (preloaded/ prefix).
    Run this once after deploying to persist pre-loaded source documents.
    """
    try:
        synced = sync_preloaded_to_minio()
    except Exception as e:
        return json.dumps({"error": str(e)})
    if not synced:
        return json.dumps({"message": "All pre-loaded documents already synced.", "synced": []})
    return json.dumps({"message": f"Synced {len(synced)} file(s) to MinIO.", "synced": synced})
