# tests/test_documents_tools.py
"""Regression tests for RFC-009 D1 (ISS-21): query-path not-found responses must
not trigger an O(N) MinIO listing.

RFC-009 Design Property 1 — "No O(N) listing on error paths": get_document,
get_document_structure, and get_page_content previously built an `available`
array via `list_processed_docs()` (a full MinIO listing) whenever a doc_id
was not found. Any invalid doc_id therefore had a DoS-shaped cost: a full
corpus listing on every miss. The fix drops the `available` enrichment and
returns a bare `{"error": ...}` body without touching storage at all.

These tests assert both halves of the contract:
  1. The JSON body is exactly {"error": "Document not found: <id>"} — no
     `available` key.
  2. `list_processed_docs` is never invoked on the not-found path.
"""

import json
from unittest.mock import patch

from pageindex_mcp.tools import documents


def _not_found(_doc_id):
    raise KeyError("doc not found")


def test_get_document_not_found_no_listing():
    doc_id = "nonexistent-doc-id"
    with (
        patch("pageindex_mcp.tools.documents.get_doc", side_effect=_not_found),
        patch("pageindex_mcp.storage.list_processed_docs") as mock_list,
    ):
        result = documents.get_document(doc_id)

    assert json.loads(result) == {"error": f"Document not found: {doc_id}"}
    mock_list.assert_not_called()


def test_get_document_structure_not_found_no_listing():
    doc_id = "nonexistent-doc-id"
    with (
        patch("pageindex_mcp.tools.documents.get_doc", side_effect=_not_found),
        patch("pageindex_mcp.storage.list_processed_docs") as mock_list,
    ):
        result = documents.get_document_structure(doc_id)

    assert json.loads(result) == {"error": f"Document not found: {doc_id}"}
    mock_list.assert_not_called()


def test_get_page_content_not_found_no_listing():
    doc_id = "nonexistent-doc-id"
    with (
        patch("pageindex_mcp.tools.documents.get_doc", side_effect=_not_found),
        patch("pageindex_mcp.storage.list_processed_docs") as mock_list,
    ):
        result = documents.get_page_content(doc_id, "1-3")

    assert json.loads(result) == {"error": f"Document not found: {doc_id}"}
    mock_list.assert_not_called()
