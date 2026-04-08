# tests/test_rag_dedup.py
"""Verify that find_relevant_documents does not double-load documents."""

import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from pageindex_mcp.tools.documents import find_relevant_documents


async def test_find_relevant_documents_loads_each_doc_once():
    """load_doc should be called once per doc during RAG, not twice."""
    fake_meta = [
        {"doc_id": "aaa11111", "doc_name": "a.pdf", "source_url": "", "processed_at": ""},
    ]
    fake_doc = {
        "doc_name": "a.pdf",
        "doc_description": "",
        "structure": [
            {"node_id": "n1", "title": "Intro", "summary": "intro", "text": "hello",
             "start_index": 1, "end_index": 1},
        ],
    }
    with (
        patch("pageindex_mcp.tools.documents.list_processed_docs", return_value=fake_meta),
        patch("pageindex_mcp.helpers.load_doc", return_value=fake_doc) as mock_load,
        patch("pageindex_mcp.helpers._llm", new_callable=AsyncMock) as mock_llm,
    ):
        mock_llm.side_effect = [
            '{"thinking": "relevant", "node_list": ["n1"]}',
            "The answer is hello.",
        ]
        result = await find_relevant_documents("test query")

    # load_doc called once per doc, not twice (once in list + once in rag)
    assert mock_load.call_count == 1
