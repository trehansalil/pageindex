# tests/test_pagination.py
"""RFC-009 D3 (ISS-06) — server-side pagination for recent_documents.

Validates Design Property 3 ("Server-side pagination"): recent_documents must
push LIMIT/OFFSET down to registry.list_docs instead of fetching the whole
corpus and slicing in Python, and must read node_count off the listing row
(D2 sidecar / registry column) instead of deserializing each document's tree.

Contract:
  1. registry.list_docs is called with limit=page_size, offset=(page-1)*page_size
     — never the old limit=100_000 fetch-all.
  2. get_doc() (tree deserialization) is NOT invoked to compute node_count.
  3. DOCUMENTS_TOTAL reflects registry.count_docs() (whole corpus), not the slice.
  4. End-to-end paging over 20 docs returns the correct page_size-bounded window.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from pageindex_mcp.tools import documents


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
    raises RegistryUnavailableError -> recent_documents returns an explicit error
    (D6: no MinIO fallback to a possibly-wrong total)."""
    list_docs = AsyncMock(return_value=[_row(i) for i in range(1, 6)])
    count_docs = AsyncMock(return_value=None)
    minio = MagicMock()

    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.list_docs", new=list_docs),
        patch("pageindex_mcp.registry.count_docs", new=count_docs),
        patch("pageindex_mcp.storage.list_processed_docs", new=minio),
    ):
        result = await documents.recent_documents(page=1, page_size=5)

    payload = json.loads(result)
    assert "error" in payload
    assert "registry unavailable" in payload["error"].lower()
    minio.assert_not_called()


async def test_postgres_down_returns_error():
    """Postgres unavailable (registry ready, but list_docs returns None on a query
    failure) must surface an explicit error from both listing tools — never a
    MinIO-derived result, never an unhandled crash."""
    minio = MagicMock()

    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.list_docs", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.count_docs", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.storage.list_processed_docs", new=minio),
    ):
        recent = json.loads(await documents.recent_documents(page=1, page_size=5))
        find = json.loads(await documents.find_relevant_documents("q"))

    assert "error" in recent and "registry unavailable" in recent["error"].lower()
    assert "error" in find and "registry unavailable" in find["error"].lower()
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
    call storage.list_processed_docs() (D6: no O(N) MinIO fallback). The default
    test settings leave POSTGRES_DSN unset -> the real _require_registry_ready
    gate raises 'disabled', exercising the true unavailable path end-to-end."""
    import pageindex_mcp.storage as storage_mod

    with patch.object(storage_mod, "list_processed_docs") as minio:
        recent = json.loads(await documents.recent_documents(page=1, page_size=5))
        find = json.loads(await documents.find_relevant_documents("q"))

    minio.assert_not_called()
    # Both tools returned a clean JSON error rather than crashing.
    assert "error" in recent
    assert "error" in find
