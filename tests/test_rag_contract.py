# tests/test_rag_contract.py
"""Behavioral contract tests for find_relevant_documents prefilter + concurrent
tree search (RAG-01).

RAG-01-C1  the pipeline prefilters candidate docs before the tree search; docs
           excluded by the prefilter are never searched; no vector index is used
RAG-01-C2  tree search runs concurrently across candidate docs, bounded by a
           semaphore of size PAGEINDEX_SEARCH_CONCURRENCY
RAG-01-C3  with zero indexed docs, the tool returns a JSON error envelope with
           available=[] and an error message; no LLM tree-search call is made
"""

import json
from unittest.mock import AsyncMock, patch

import pytest


# ── RAG-01-C3 — no-docs error envelope (real call into the MCP tool) ─────────
async def test_rag_01_c3_no_documents_returns_error_envelope():
    """RAG-01-C3: find_relevant_documents() with zero indexed docs returns the
    query_error_shape envelope (available=[] + error key) and never runs a
    tree-search LLM call. Exercises the real tool entry point."""
    from pageindex_mcp.tools.documents import find_relevant_documents

    with patch("pageindex_mcp.tools.documents.list_processed_docs", return_value=[]), \
         patch("pageindex_mcp.helpers._llm", new_callable=AsyncMock) as mock_llm:
        raw = await find_relevant_documents("any query")

    payload = json.loads(raw)
    assert payload["available"] == []
    assert "error" in payload
    # No LLM tree-search call was issued on the empty-corpus path.
    mock_llm.assert_not_called()


# ── RAG-01-C1 — prefilter selects candidates before the tree search ──────────
async def test_rag_01_c1_prefilter_excludes_docs_from_search():
    """RAG-01-C1: _rag prefilters candidate docs first; a doc the prefilter drops
    is never handed to the per-doc tree search. We load two docs but make the
    prefilter return only one, then assert the searched doc set == the prefiltered
    set (the excluded doc is never searched)."""
    from pageindex_mcp import helpers

    doc_a = {"doc_name": "a.pdf", "doc_description": "alpha",
             "structure": [{"node_id": "n1", "title": "A", "summary": "a", "text": "atext"}]}
    doc_b = {"doc_name": "b.pdf", "doc_description": "bravo",
             "structure": [{"node_id": "n2", "title": "B", "summary": "b", "text": "btext"}]}
    store = {"aaa": doc_a, "bbb": doc_b}

    searched = []

    async def fake_search_one(query, doc_id, data, semaphore):
        searched.append(doc_id)
        return None  # no matched text; we only care about WHICH docs are searched

    with patch("pageindex_mcp.helpers.get_doc", side_effect=lambda d: store[d]), \
         patch("pageindex_mcp.helpers._prefilter_docs",
               new=AsyncMock(return_value=["aaa"])) as mock_prefilter, \
         patch("pageindex_mcp.helpers._search_one_doc", side_effect=fake_search_one):
        await helpers._rag("q", ["aaa", "bbb"])

    # Prefilter ran before search and selected only 'aaa'.
    mock_prefilter.assert_awaited_once()
    # 'bbb' was excluded by the prefilter and therefore never searched.
    assert searched == ["aaa"]
    assert "bbb" not in searched


# ── RAG-01-C2 — concurrent search bounded by the search-concurrency semaphore ─
async def test_rag_01_c2_concurrent_search_bounded_by_semaphore():
    """RAG-01-C2: tree search fans out exactly one task per candidate doc, all
    bounded by an asyncio.Semaphore of size PAGEINDEX_SEARCH_CONCURRENCY. We force
    a small concurrency limit and assert the max in-flight searches never exceeds
    it while every candidate doc is still searched."""
    from pageindex_mcp import helpers
    import asyncio

    n_docs = 6
    store = {
        f"d{i}": {
            "doc_name": f"{i}.pdf", "doc_description": "",
            "structure": [{"node_id": f"n{i}", "title": str(i), "summary": "s", "text": "t"}],
        }
        for i in range(n_docs)
    }
    doc_ids = list(store.keys())

    inflight = 0
    max_inflight = 0
    searched = set()

    async def fake_search_one(query, doc_id, data, semaphore):
        nonlocal inflight, max_inflight
        async with semaphore:
            inflight += 1
            max_inflight = max(max_inflight, inflight)
            await asyncio.sleep(0)  # yield so overlap can occur
            searched.add(doc_id)
            inflight -= 1
        return None

    with patch("pageindex_mcp.helpers.get_doc", side_effect=lambda d: store[d]), \
         patch("pageindex_mcp.helpers._prefilter_docs",
               new=AsyncMock(return_value=doc_ids)), \
         patch("pageindex_mcp.helpers._search_one_doc", side_effect=fake_search_one), \
         patch("pageindex_mcp.helpers._SEARCH_CONCURRENCY", 2):
        await helpers._rag("q", doc_ids)

    # Every candidate doc was searched (N tasks for N prefiltered docs).
    assert searched == set(doc_ids)
    # In-flight searches never exceeded the configured concurrency bound.
    assert max_inflight <= 2
    assert max_inflight >= 1
