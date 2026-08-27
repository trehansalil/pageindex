# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
from __future__ import annotations

"""RAG contract, deduplication, and pagination tests."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp.tools import documents
from pageindex_mcp.tools.documents import find_relevant_documents


# --- from test_rag_contract.py ---


# ── RAG-01-C3 — no-docs isError:true (real call into the MCP tool) ───────────
async def test_rag_01_c3_no_documents_raises_tool_error():
    """RAG-01-C3: find_relevant_documents() with zero indexed docs raises a
    ToolError (isError:true) carrying reason=verdict_fail, and never runs a
    tree-search LLM call. Exercises the real tool entry point."""
    from fastmcp.exceptions import ToolError

    # RFC-009 D6: registry-only read path — the empty corpus is an empty registry
    # listing (registry.list_docs -> []), not an empty MinIO scan.
    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.list_docs", new=AsyncMock(return_value=[])),
        patch("pageindex_mcp.helpers.rag._llm", new_callable=AsyncMock) as mock_llm,
        pytest.raises(ToolError, match="verdict_fail"),
    ):
        await find_relevant_documents("any query")

    # No LLM tree-search call was issued on the empty-corpus path.
    mock_llm.assert_not_called()


# ── RAG-01-C1 — prefilter selects candidates before the tree search ──────────
async def test_rag_01_c1_prefilter_excludes_docs_from_search():
    """RAG-01-C1: _rag prefilters candidate docs first; a doc the prefilter drops
    is never handed to the per-doc tree search. We load two docs but make the
    prefilter return only one, then assert the searched doc set == the prefiltered
    set (the excluded doc is never searched)."""
    from pageindex_mcp import helpers

    doc_a = {
        "doc_name": "a.pdf",
        "doc_description": "alpha",
        "structure": [{"node_id": "n1", "title": "A", "summary": "a", "text": "atext"}],
    }
    doc_b = {
        "doc_name": "b.pdf",
        "doc_description": "bravo",
        "structure": [{"node_id": "n2", "title": "B", "summary": "b", "text": "btext"}],
    }
    store = {"aaa": doc_a, "bbb": doc_b}

    searched = []

    async def fake_search_one(query, doc_id, data, semaphore):
        searched.append(doc_id)
        return None  # no matched text; we only care about WHICH docs are searched

    with (
        patch("pageindex_mcp.helpers.rag.get_doc", side_effect=lambda d: store[d]),
        patch(
            "pageindex_mcp.helpers.rag._prefilter_docs", new=AsyncMock(return_value=["aaa"])
        ) as mock_prefilter,
        patch("pageindex_mcp.helpers.rag._search_one_doc", side_effect=fake_search_one),
    ):
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

    n_docs = 6
    store = {
        f"d{i}": {
            "doc_name": f"{i}.pdf",
            "doc_description": "",
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

    with (
        patch("pageindex_mcp.helpers.rag.get_doc", side_effect=lambda d: store[d]),
        patch("pageindex_mcp.helpers.rag._prefilter_docs", new=AsyncMock(return_value=doc_ids)),
        patch("pageindex_mcp.helpers.rag._search_one_doc", side_effect=fake_search_one),
        patch("pageindex_mcp.helpers.rag._SEARCH_CONCURRENCY", 2),
    ):
        await helpers._rag("q", doc_ids)

    # Every candidate doc was searched (N tasks for N prefiltered docs).
    assert searched == set(doc_ids)
    # In-flight searches never exceeded the configured concurrency bound.
    assert max_inflight <= 2
    assert max_inflight >= 1


# --- from test_rag_dedup.py ---


async def test_find_relevant_documents_loads_each_doc_once():
    """load_doc should be called once per doc during RAG, not twice."""
    fake_meta = [
        {"doc_id": "aaa11111", "doc_name": "a.pdf", "source_url": "", "processed_at": ""},
    ]
    fake_doc = {
        "doc_name": "a.pdf",
        "doc_description": "",
        "structure": [
            {
                "node_id": "n1",
                "title": "Intro",
                "summary": "intro",
                "text": "hello",
                "start_index": 1,
                "end_index": 1,
            },
        ],
    }

    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.list_docs", new=AsyncMock(return_value=fake_meta)),
        patch("pageindex_mcp.helpers.rag.get_doc", return_value=fake_doc) as mock_load,
        patch("pageindex_mcp.helpers.rag._llm", new_callable=AsyncMock) as mock_llm,
    ):
        mock_llm.side_effect = [
            '{"thinking": "relevant", "node_list": ["n1"]}',
            "The answer is hello.",
        ]
        result = await find_relevant_documents("test query")

    # read-through get_doc called once per doc, not twice (once in list + once in rag)
    assert mock_load.call_count == 1


# --- from test_pagination.py ---


def _row(i: int, node_count: int | None = 7) -> dict:
    return {
        "doc_id": f"doc-{i:02d}",
        "doc_name": f"Document {i:02d}",
        "source_url": "",
        "processed_at": "",
        "content_class": "",
        "node_count": node_count,
    }


async def test_list_docs_called_with_limit_offset():
    """page=2, page_size=5 must reach registry.list_docs as limit=5, offset=5 —
    never the old limit=100_000 fetch-all-then-slice."""
    list_docs = AsyncMock(return_value=[_row(i) for i in range(6, 11)])
    count_docs = AsyncMock(return_value=20)

    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.list_docs", new=list_docs),
        patch("pageindex_mcp.registry.count_docs", new=count_docs),
    ):
        result = await documents.recent_documents(page=2, page_size=5)

    list_docs.assert_awaited_once_with(limit=5, offset=5)
    assert list_docs.await_args.kwargs != {"limit": 100_000, "offset": 0}
    payload = json.loads(result)
    assert payload["page"] == 2
    assert payload["page_size"] == 5
    assert payload["total"] == 20  # count_docs, not the 5-row slice
    assert len(payload["documents"]) == 5


async def test_get_doc_not_called_for_node_count():
    """node_count must be read from the listing row, so get_doc() (tree
    deserialization) is never invoked on the paginated read path."""
    list_docs = AsyncMock(return_value=[_row(i, node_count=42) for i in range(1, 4)])
    count_docs = AsyncMock(return_value=3)
    get_doc = MagicMock()

    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.list_docs", new=list_docs),
        patch("pageindex_mcp.registry.count_docs", new=count_docs),
        patch.object(documents, "get_doc", new=get_doc),
    ):
        result = await documents.recent_documents(page=1, page_size=10)

    get_doc.assert_not_called()
    payload = json.loads(result)
    assert [d["node_count"] for d in payload["documents"]] == [42, 42, 42]


async def test_node_count_none_surfaces_as_zero():
    """Legacy rows predating the D2 backfill carry node_count=None → surface 0,
    still without any get_doc() call."""
    list_docs = AsyncMock(return_value=[_row(1, node_count=None)])
    count_docs = AsyncMock(return_value=1)
    get_doc = MagicMock()

    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.list_docs", new=list_docs),
        patch("pageindex_mcp.registry.count_docs", new=count_docs),
        patch.object(documents, "get_doc", new=get_doc),
    ):
        result = await documents.recent_documents(page=1, page_size=10)

    get_doc.assert_not_called()
    assert json.loads(result)["documents"][0]["node_count"] == 0


async def test_pagination_integration_20_docs():
    """Integration-style: a registry holding 20 docs, paged via real LIMIT/OFFSET
    semantics, returns exactly the requested window for page 2 / size 5."""
    corpus = [_row(i) for i in range(1, 21)]  # doc-01 .. doc-20

    async def fake_list_docs(limit: int, offset: int):
        return corpus[offset : offset + limit]

    async def fake_count_docs():
        return len(corpus)

    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.list_docs", new=fake_list_docs),
        patch("pageindex_mcp.registry.count_docs", new=fake_count_docs),
    ):
        result = await documents.recent_documents(page=2, page_size=5)

    payload = json.loads(result)
    assert payload["total"] == 20
    assert payload["page"] == 2
    assert payload["page_size"] == 5
    ids = [d["doc_id"] for d in payload["documents"]]
    assert ids == ["doc-06", "doc-07", "doc-08", "doc-09", "doc-10"]


# ── RFC-009 D6 (Property 7) — registry-only listing, no MinIO fallback ────────
#
# These replace the two former fallback-path tests: with D6 landed there is no
# MinIO fallback, so a not-ready registry or a Postgres query error must surface
# as an explicit error, never a degraded O(N) list_processed_docs() scan.


async def test_registry_count_failure_returns_error():
    """If list_docs succeeds but count_docs errors (None), the paginated path
    raises RegistryUnavailableError -> recent_documents raises isError:true
    (Phase 3 audit Issue B) (D6: no MinIO fallback to a possibly-wrong total)."""
    from fastmcp.exceptions import ToolError

    list_docs = AsyncMock(return_value=[_row(i) for i in range(1, 6)])
    count_docs = AsyncMock(return_value=None)
    minio = MagicMock()

    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.list_docs", new=list_docs),
        patch("pageindex_mcp.registry.count_docs", new=count_docs),
        patch("pageindex_mcp.storage.list_processed_docs", new=minio),
        pytest.raises(ToolError, match="registry unavailable"),
    ):
        await documents.recent_documents(page=1, page_size=5)

    minio.assert_not_called()


async def test_postgres_down_returns_error():
    """Postgres unavailable (registry ready, but list_docs returns None on a query
    failure) must surface isError:true from both listing tools (Phase 3 audit
    Issue B) — never a MinIO-derived result, never an unhandled crash."""
    from fastmcp.exceptions import ToolError

    minio = MagicMock()

    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.list_docs", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.count_docs", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.storage.list_processed_docs", new=minio),
    ):
        with pytest.raises(ToolError, match="registry unavailable"):
            await documents.recent_documents(page=1, page_size=5)
        with pytest.raises(ToolError, match="registry unavailable"):
            await documents.find_relevant_documents("q")

    minio.assert_not_called()


async def test_registry_path_returns_correct_results():
    """Healthy registry -> recent_documents returns the correct listing straight
    from a single SQL query (limit/offset + count), no MinIO involvement."""
    rows = [_row(i) for i in range(1, 4)]

    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.list_docs", new=AsyncMock(return_value=rows)),
        patch("pageindex_mcp.registry.count_docs", new=AsyncMock(return_value=3)),
    ):
        payload = json.loads(await documents.recent_documents(page=1, page_size=10))

    assert payload["total"] == 3
    assert [d["doc_id"] for d in payload["documents"]] == ["doc-01", "doc-02", "doc-03"]
    assert all(d["status"] == "completed" for d in payload["documents"])


async def test_no_list_processed_docs_calls():
    """Under every registry-unavailable condition, neither listing function may
    call storage.list_processed_docs() (D6: no O(N) MinIO fallback). The registry
    is mocked as unavailable (returning the standard disabled error shape)."""
    from fastmcp.exceptions import ToolError

    import pageindex_mcp.storage as storage_mod
    from pageindex_mcp.tools.documents import RegistryUnavailableError

    async def _registry_disabled(*_args, **_kwargs):
        raise RegistryUnavailableError("disabled")

    with (
        patch.object(documents, "_require_registry_ready", side_effect=_registry_disabled),
        patch.object(storage_mod, "list_processed_docs") as minio,
    ):
        # Phase 3 audit Issue B: both tools now raise isError:true rather than
        # returning a clean JSON error, so the calling LLM can distinguish a
        # refusal from a legitimate empty result.
        with pytest.raises(ToolError):
            await documents.recent_documents(page=1, page_size=5)
        with pytest.raises(ToolError):
            await documents.find_relevant_documents("q")

    minio.assert_not_called()
