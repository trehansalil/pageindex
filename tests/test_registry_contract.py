"""Contract tests for the Postgres document registry (RFC-006).

Two layers:

1. **Unit tests** (always run) — patch ``registry.get_pool`` with an
   ``AsyncMock`` so the pure control-flow of each coroutine is exercised without
   a live Postgres: the pool-None fallback guards, the ``upsert_doc`` payload
   mapping, the Stage B recency fallback, the Stage A facet-resolution logic,
   and the Redis ``registry_complete`` flag helpers (via ``fakeredis``).

2. **Integration tests** (``integration`` marker, skipped when ``POSTGRES_DSN``
   is unset or unreachable) — run the real SQL against Postgres to verify the
   things mocks cannot: the generated ``tsvector`` column, ``ts_rank`` ordering,
   ``processed_at DESC`` listing, the ON CONFLICT upsert, and idempotent delete.
"""

from __future__ import annotations

import copy
import os
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
import pytest_asyncio

from pageindex_mcp import registry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_module_state():
    """Snapshot and restore the registry module globals mutated by tests.

    ``_pool`` and ``_KNOWN_FACETS`` are module-level state; without this a test
    that populates facets or sets a pool would leak into the next test.
    """
    saved_pool = registry._pool
    saved_facets = copy.deepcopy(registry._KNOWN_FACETS)
    try:
        yield
    finally:
        registry._pool = saved_pool
        registry._KNOWN_FACETS.clear()
        registry._KNOWN_FACETS.update(saved_facets)


@pytest.fixture
def no_pool():
    """Force the pool to None so the fallback guards are exercised."""
    with patch.object(registry, "get_pool", return_value=None):
        yield


def _mock_pool() -> AsyncMock:
    """An AsyncMock standing in for an asyncpg pool."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    return pool


# ---------------------------------------------------------------------------
# Unit — pool-None fallback guards (RFC-006: every coroutine degrades to None /
# no-op so the caller can fall back to MinIO instead of raising)
# ---------------------------------------------------------------------------


async def test_reads_return_none_when_pool_absent(no_pool):
    assert await registry.list_docs() is None
    assert await registry.count_docs() is None
    assert await registry.stage_b_candidates("anything", 10) is None
    assert await registry.stage_a_filter("anything") is None


async def test_writes_are_noops_when_pool_absent(no_pool):
    # Must not raise even though there is no pool to write to.
    await registry.upsert_doc({"doc_id": "x"})
    await registry.delete_doc("x")


# ---------------------------------------------------------------------------
# Unit — upsert_doc payload mapping
# ---------------------------------------------------------------------------


async def test_upsert_maps_all_fields_in_order():
    pool = _mock_pool()
    meta = {
        "doc_id": "abc123",
        "doc_name": "AKB.pdf",
        "source_url": "s3://x",
        "processed_at": "2026-07-10T00:00:00",
        "content_class": "flat_table",
        "sha256": "deadbeef",
        "product": "huk",
        "tier": "komfort",
        "doc_family": "phv",
        "effective_date": "2026-01-01",
        "doc_description": "liability terms",
    }
    with patch.object(registry, "get_pool", return_value=pool):
        await registry.upsert_doc(meta)

    args = pool.execute.await_args.args
    # args[0] is the SQL; args[1:] are the positional params in column order.
    assert args[0] is registry._UPSERT_SQL
    assert args[1:] == (
        "abc123", "AKB.pdf", "s3://x", "2026-07-10T00:00:00", "flat_table",
        "deadbeef", "huk", "komfort", "phv", "2026-01-01", "liability terms",
    )


async def test_upsert_defaults_missing_keys_to_empty_string():
    pool = _mock_pool()
    with patch.object(registry, "get_pool", return_value=pool):
        await registry.upsert_doc({"doc_id": "abc123", "doc_name": "only-name.pdf"})

    args = pool.execute.await_args.args
    assert args[1] == "abc123"
    assert args[2] == "only-name.pdf"
    # Everything else defaults to "".
    assert all(a == "" for a in args[3:])


async def test_upsert_skips_row_with_empty_doc_id():
    pool = _mock_pool()
    with patch.object(registry, "get_pool", return_value=pool):
        await registry.upsert_doc({"doc_name": "orphan.pdf"})  # no doc_id
    pool.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# Unit — list / count mapping and error degradation
# ---------------------------------------------------------------------------


async def test_list_docs_maps_rows_to_legacy_shape():
    pool = _mock_pool()
    pool.fetch.return_value = [
        {"doc_id": "d1", "doc_name": "a.pdf", "source_url": "",
         "processed_at": "2026-07-10", "content_class": ""},
    ]
    with patch.object(registry, "get_pool", return_value=pool):
        rows = await registry.list_docs(limit=5, offset=0)

    assert rows == [
        {"doc_id": "d1", "doc_name": "a.pdf", "source_url": "",
         "processed_at": "2026-07-10", "content_class": ""},
    ]
    pool.fetch.assert_awaited_once_with(registry._LIST_SQL, 5, 0)


async def test_count_docs_returns_int():
    pool = _mock_pool()
    pool.fetchval.return_value = 42
    with patch.object(registry, "get_pool", return_value=pool):
        assert await registry.count_docs() == 42


async def test_count_docs_returns_none_on_error():
    pool = _mock_pool()
    pool.fetchval.side_effect = RuntimeError("connection reset")
    with patch.object(registry, "get_pool", return_value=pool):
        assert await registry.count_docs() is None


# ---------------------------------------------------------------------------
# Unit — Stage B recency fallback and error degradation
# ---------------------------------------------------------------------------


async def test_stage_b_returns_ranked_matches():
    pool = _mock_pool()
    pool.fetch.return_value = [
        {"doc_id": "d1", "doc_name": "hit.pdf", "source_url": "",
         "processed_at": "2026-07-10", "content_class": ""},
    ]
    with patch.object(registry, "get_pool", return_value=pool):
        rows = await registry.stage_b_candidates("liability", 200)

    assert rows is not None
    assert [r["doc_id"] for r in rows] == ["d1"]
    # A single fetch: the ts_rank query hit, so no fallback query.
    pool.fetch.assert_awaited_once_with(registry._STAGE_B_SQL, "liability", 200)


async def test_stage_b_falls_back_to_recency_on_no_match():
    pool = _mock_pool()
    recent = [
        {"doc_id": "r1", "doc_name": "recent.pdf", "source_url": "",
         "processed_at": "2026-07-10", "content_class": ""},
    ]
    # First fetch (ts_rank) → empty; second fetch (recency fallback) → recent.
    pool.fetch.side_effect = [[], recent]
    with patch.object(registry, "get_pool", return_value=pool):
        rows = await registry.stage_b_candidates("zzzznomatch", 200)

    assert rows is not None
    assert [r["doc_id"] for r in rows] == ["r1"]
    assert pool.fetch.await_count == 2
    assert pool.fetch.await_args_list[1].args == (registry._STAGE_B_FALLBACK_SQL, 200)


async def test_stage_b_returns_none_on_error():
    pool = _mock_pool()
    pool.fetch.side_effect = RuntimeError("boom")
    with patch.object(registry, "get_pool", return_value=pool):
        assert await registry.stage_b_candidates("q", 10) is None


# ---------------------------------------------------------------------------
# Unit — Stage A facet resolution (exact case-folded, never substring)
# ---------------------------------------------------------------------------


async def test_stage_a_is_noop_when_facets_unpopulated():
    """Pre-Tier-1: all facet sets empty → transparent pass-through (None)."""
    pool = _mock_pool()
    with patch.object(registry, "get_pool", return_value=pool):
        assert await registry.stage_a_filter("huk coburg policy") is None
    pool.fetch.assert_not_awaited()


async def test_stage_a_resolves_facet_from_query_and_filters():
    registry.refresh_known_facets({"product": {"HUK"}})
    pool = _mock_pool()
    pool.fetch.return_value = [
        {"doc_id": "d1", "doc_name": "a.pdf", "source_url": "",
         "processed_at": "2026-07-10", "content_class": ""},
    ]
    with patch.object(registry, "get_pool", return_value=pool):
        rows = await registry.stage_a_filter("please find the huk policy")

    assert rows is not None
    assert [r["doc_id"] for r in rows] == ["d1"]
    sql, *params = pool.fetch.await_args.args
    assert "product = $1" in sql
    assert params == ["huk"]  # case-folded to match the stored known value


async def test_stage_a_does_not_substring_match():
    """'huককা' inside a longer token must NOT match the 'huk' facet value."""
    registry.refresh_known_facets({"product": {"huk"}})
    pool = _mock_pool()
    with patch.object(registry, "get_pool", return_value=pool):
        # 'hukcoburg' is a single token; 'huk' is not a standalone word here.
        assert await registry.stage_a_filter("hukcoburg terms") is None
    pool.fetch.assert_not_awaited()


async def test_stage_a_honours_caller_facet_hints():
    registry.refresh_known_facets({"tier": {"komfort"}})
    pool = _mock_pool()
    pool.fetch.return_value = []
    with patch.object(registry, "get_pool", return_value=pool):
        # Query text has no facet, but the caller supplies an explicit hint.
        await registry.stage_a_filter("anything", facet_hints={"tier": "KOMFORT"})

    sql, *params = pool.fetch.await_args.args
    assert "tier = $1" in sql
    assert params == ["komfort"]


def test_refresh_known_facets_casefolds_and_ignores_unknown_columns():
    registry.refresh_known_facets(
        {"product": {"HUK", "Allianz"}, "not_a_column": {"x"}}
    )
    assert registry._KNOWN_FACETS["product"] == {"huk", "allianz"}
    assert "not_a_column" not in registry._KNOWN_FACETS


# ---------------------------------------------------------------------------
# Unit — Redis registry_complete flag helpers
# ---------------------------------------------------------------------------


async def test_registry_complete_flag_roundtrip():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    assert await registry.is_registry_complete(r) is False
    await registry.set_registry_complete(r)
    assert await registry.is_registry_complete(r) is True


async def test_is_registry_complete_swallows_redis_error():
    r = AsyncMock()
    r.get.side_effect = ConnectionError("redis down")
    assert await registry.is_registry_complete(r) is False


# ---------------------------------------------------------------------------
# Integration — real Postgres (skipped when POSTGRES_DSN unset/unreachable)
# ---------------------------------------------------------------------------

_DSN = os.environ.get("POSTGRES_DSN")


@pytest_asyncio.fixture
async def reg():
    """Initialise the real registry against Postgres, truncating around each test.

    Skips the test when no DSN is configured or Postgres is unreachable, so the
    suite stays green in environments without the compose stack.
    """
    dsn = _DSN
    if not dsn:
        pytest.skip("POSTGRES_DSN not set — skipping registry integration tests")
    registry._pool = None
    try:
        await registry.init_registry(dsn)
    except Exception as exc:
        pytest.skip(f"Postgres unreachable ({exc}) — skipping integration tests")
    pool = registry.get_pool()
    assert pool is not None
    await pool.execute("TRUNCATE doc_registry")
    try:
        yield pool
    finally:
        await pool.execute("TRUNCATE doc_registry")
        await registry.close_registry()


pytestmark_integration = pytest.mark.integration


@pytest.mark.integration
async def test_init_registry_is_idempotent(reg):
    # A second init on an already-open pool is a no-op and must not raise.
    await registry.init_registry(_DSN)
    assert registry.get_pool() is not None


@pytest.mark.integration
async def test_upsert_insert_then_update_roundtrip(reg):
    await registry.upsert_doc({"doc_id": "d1", "doc_name": "first.pdf"})
    assert await registry.count_docs() == 1

    # Same doc_id → ON CONFLICT update, not a second row.
    await registry.upsert_doc({"doc_id": "d1", "doc_name": "renamed.pdf"})
    assert await registry.count_docs() == 1
    rows = await registry.list_docs()
    assert rows[0]["doc_name"] == "renamed.pdf"


@pytest.mark.integration
async def test_list_docs_newest_first_and_paginates(reg):
    await registry.upsert_doc({"doc_id": "old", "doc_name": "old.pdf",
                               "processed_at": "2026-01-01T00:00:00"})
    await registry.upsert_doc({"doc_id": "new", "doc_name": "new.pdf",
                               "processed_at": "2026-07-10T00:00:00"})

    rows = await registry.list_docs(limit=10)
    assert [r["doc_id"] for r in rows] == ["new", "old"]

    page = await registry.list_docs(limit=1, offset=1)
    assert [r["doc_id"] for r in page] == ["old"]


@pytest.mark.integration
async def test_stage_b_ranks_relevant_and_excludes_irrelevant(reg):
    await registry.upsert_doc({
        "doc_id": "liab", "doc_name": "AVB-PHV.pdf",
        "doc_description": "private liability insurance Haftpflicht terms"})
    await registry.upsert_doc({
        "doc_id": "motor", "doc_name": "AKB.pdf",
        "doc_description": "motor vehicle Kfz insurance conditions"})

    rows = await registry.stage_b_candidates("Haftpflicht liability", topk=10)
    ids = [r["doc_id"] for r in rows]
    assert "liab" in ids
    assert "motor" not in ids


@pytest.mark.integration
async def test_stage_b_recency_fallback_when_no_lexical_match(reg):
    await registry.upsert_doc({"doc_id": "a", "doc_name": "a.pdf",
                               "processed_at": "2026-01-01T00:00:00",
                               "doc_description": "alpha"})
    await registry.upsert_doc({"doc_id": "b", "doc_name": "b.pdf",
                               "processed_at": "2026-07-10T00:00:00",
                               "doc_description": "beta"})

    # A query that matches nothing lexically → recency-ordered fallback.
    rows = await registry.stage_b_candidates("zzzznomatchtoken", topk=10)
    assert [r["doc_id"] for r in rows] == ["b", "a"]


@pytest.mark.integration
async def test_delete_doc_is_idempotent(reg):
    await registry.upsert_doc({"doc_id": "gone", "doc_name": "gone.pdf"})
    assert await registry.count_docs() == 1

    await registry.delete_doc("gone")
    assert await registry.count_docs() == 0

    # Second delete of an absent row is a safe no-op.
    await registry.delete_doc("gone")
    assert await registry.count_docs() == 0
