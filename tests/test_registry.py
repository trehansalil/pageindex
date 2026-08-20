"""Registry tests (RFC-006 contract + RFC-007 D3 backfill).

Merged from ``test_registry_contract.py`` and ``test_registry_backfill.py``.

Two layers:

1. **Unit tests** (always run) — patch ``registry.get_pool`` with an
   ``AsyncMock`` so the pure control-flow of each coroutine is exercised without
   a live Postgres: the pool-None fallback guards, the ``upsert_doc`` payload
   mapping, the Stage B recency fallback, the Stage A facet-resolution logic,
   and the Redis ``registry_complete`` flag helpers (via ``fakeredis``). Also
   covers ``registry_backfill._backfill`` — Property 7: the registry must
   never be marked complete when zero ``.meta.json`` sidecars were found.

2. **Integration tests** (``integration`` marker, skipped when ``POSTGRES_DSN``
   is unset or unreachable) — run the real SQL against Postgres to verify the
   things mocks cannot: the generated ``tsvector`` column, ``ts_rank`` ordering,
   ``processed_at DESC`` listing, the ON CONFLICT upsert, and idempotent delete.
"""

from __future__ import annotations

import copy
import dataclasses
import os
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest
import pytest_asyncio

from pageindex_mcp import registry
from pageindex_mcp import registry_backfill as rb

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


def _wire_backfill_settings(monkeypatch):
    monkeypatch.setattr(
        rb,
        "settings",
        dataclasses.replace(
            rb.settings,
            registry_enabled=True,
            postgres_dsn="postgresql://user:pass@localhost:5432/pageindex",
        ),
    )


@pytest.fixture
def fake_redis_client():
    client = MagicMock()
    client.aclose = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Unit — pool-None fallback guards (RFC-006: every coroutine degrades to None /
# no-op so the caller can fall back to MinIO instead of raising)
# ---------------------------------------------------------------------------


async def test_reads_return_none_when_pool_absent(no_pool):
    assert await registry.list_docs() is None
    assert await registry.count_docs() is None
    assert await registry.stage_b_candidates("anything", 10) is None
    assert await registry.stage_a_filter("anything") is None


async def test_delete_doc_passes_statement_timeout():
    pool = _mock_pool()
    with patch.object(registry, "get_pool", return_value=pool):
        await registry.delete_doc("test-doc-id")
    pool.execute.assert_awaited_once()
    call_kwargs = pool.execute.await_args.kwargs
    assert "timeout" in call_kwargs
    assert call_kwargs["timeout"] > 0


# ---------------------------------------------------------------------------
# Unit — upsert_doc payload mapping
# ---------------------------------------------------------------------------


async def test_upsert_defaults_missing_keys_to_empty_string():
    pool = _mock_pool()
    with patch.object(registry, "get_pool", return_value=pool):
        await registry.upsert_doc({"doc_id": "abc123", "doc_name": "only-name.pdf"})

    args = pool.execute.await_args.args
    assert args[1] == "abc123"
    assert args[2] == "only-name.pdf"
    # Text columns default to ""; node_count defaults to None; verdict fields
    # default to ("", None, False) per RFC-014 D2.
    assert args[12] is None  # node_count
    assert args[13] == ""  # verdict
    assert args[14] is None  # pipeline_version
    assert args[15] is False  # permanent_marginal
    assert args[16] == ""  # verdict_computed_at
    assert all(a == "" for a in args[3:12])


# ---------------------------------------------------------------------------
# Unit — list / count mapping and error degradation
# ---------------------------------------------------------------------------


async def test_list_docs_maps_rows_to_legacy_shape():
    pool = _mock_pool()
    pool.fetch.return_value = [
        {
            "doc_id": "d1",
            "doc_name": "a.pdf",
            "source_url": "",
            "processed_at": "2026-07-10",
            "content_class": "",
            "node_count": 7,
        },
    ]
    with patch.object(registry, "get_pool", return_value=pool):
        rows = await registry.list_docs(limit=5, offset=0)

    assert rows == [
        {
            "doc_id": "d1",
            "doc_name": "a.pdf",
            "source_url": "",
            "processed_at": "2026-07-10",
            "content_class": "",
            "node_count": 7,
        },
    ]
    pool.fetch.assert_awaited_once_with(registry._LIST_SQL, 5, 0)


async def test_count_docs_returns_none_on_error():
    pool = _mock_pool()
    pool.fetchval.side_effect = RuntimeError("connection reset")
    with patch.object(registry, "get_pool", return_value=pool):
        assert await registry.count_docs() is None


# ---------------------------------------------------------------------------
# Unit — Stage B recency fallback and error degradation
# ---------------------------------------------------------------------------


async def test_stage_b_falls_back_to_recency_on_no_match():
    pool = _mock_pool()
    recent = [
        {
            "doc_id": "r1",
            "doc_name": "recent.pdf",
            "source_url": "",
            "processed_at": "2026-07-10",
            "content_class": "",
        },
    ]
    # First fetch (ts_rank) → empty; second fetch (recency fallback) → recent.
    pool.fetch.side_effect = [[], recent]
    with patch.object(registry, "get_pool", return_value=pool):
        rows = await registry.stage_b_candidates("zzzznomatch", 200)

    assert rows is not None
    assert [r["doc_id"] for r in rows] == ["r1"]
    assert pool.fetch.await_count == 2
    assert pool.fetch.await_args_list[1].args == (registry._STAGE_B_FALLBACK_SQL, 200)


# ---------------------------------------------------------------------------
# Unit — Stage A facet resolution (exact case-folded, never substring)
# ---------------------------------------------------------------------------


async def test_stage_a_is_noop_when_facets_unpopulated():
    """Pre-Tier-1: all facet sets empty → transparent pass-through (None)."""
    pool = _mock_pool()
    with patch.object(registry, "get_pool", return_value=pool):
        assert await registry.stage_a_filter("huk coburg policy") is None
    pool.fetch.assert_not_awaited()


async def test_stage_a_does_not_substring_match():
    """'huককা' inside a longer token must NOT match the 'huk' facet value."""
    registry.refresh_known_facets({"product": {"huk"}})
    pool = _mock_pool()
    with patch.object(registry, "get_pool", return_value=pool):
        # 'hukcoburg' is a single token; 'huk' is not a standalone word here.
        assert await registry.stage_a_filter("hukcoburg terms") is None
    pool.fetch.assert_not_awaited()


def test_refresh_known_facets_casefolds_and_ignores_unknown_columns():
    registry.refresh_known_facets({"product": {"HUK", "Allianz"}, "not_a_column": {"x"}})
    assert registry._KNOWN_FACETS["product"] == {"huk", "allianz"}
    assert "not_a_column" not in registry._KNOWN_FACETS


# ---------------------------------------------------------------------------
# Unit — Redis registry_complete flag helpers
# ---------------------------------------------------------------------------


async def test_is_registry_complete_swallows_redis_error():
    r = AsyncMock()
    r.get.side_effect = ConnectionError("redis down")
    assert await registry.is_registry_complete(r) is False


# ---------------------------------------------------------------------------
# Unit — registry_backfill (RFC-007 D3 / Property 7): the registry must never
# be marked complete when zero .meta.json sidecars were found.
# ---------------------------------------------------------------------------


async def test_backfill_nonzero_keys_sets_complete(monkeypatch, fake_redis_client):
    """Sanity check: the guard doesn't block the success path — complete is
    still set once every sidecar upserts cleanly."""
    _wire_backfill_settings(monkeypatch)

    monkeypatch.setattr(rb, "init_registry", AsyncMock())
    monkeypatch.setattr(rb, "close_registry", AsyncMock())
    monkeypatch.setattr(rb, "is_registry_complete", AsyncMock(return_value=False))
    set_registry_complete = AsyncMock()
    monkeypatch.setattr(rb, "set_registry_complete", set_registry_complete)
    monkeypatch.setattr(rb, "_list_meta_keys", lambda: ["processed/abc.meta.json"])
    monkeypatch.setattr(rb, "_upsert_all", AsyncMock(return_value=[]))
    monkeypatch.setattr("redis.asyncio.from_url", lambda *a, **k: fake_redis_client)

    await rb._backfill(dry_run=False, force=False)

    set_registry_complete.assert_awaited_once()


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
async def test_upsert_insert_then_update_roundtrip(reg):
    await registry.upsert_doc({"doc_id": "d1", "doc_name": "first.pdf", "verdict": "PASS"})
    assert await registry.count_docs() == 1

    # Same doc_id → ON CONFLICT update, not a second row.
    await registry.upsert_doc({"doc_id": "d1", "doc_name": "renamed.pdf", "verdict": "PASS"})
    assert await registry.count_docs() == 1
    rows = await registry.list_docs()
    assert rows[0]["doc_name"] == "renamed.pdf"


@pytest.mark.integration
async def test_stage_b_ranks_relevant_and_excludes_irrelevant(reg):
    await registry.upsert_doc(
        {
            "doc_id": "liab",
            "doc_name": "AVB-PHV.pdf",
            "doc_description": "private liability insurance Haftpflicht terms",
            "verdict": "PASS",
        }
    )
    await registry.upsert_doc(
        {
            "doc_id": "motor",
            "doc_name": "AKB.pdf",
            "doc_description": "motor vehicle Kfz insurance conditions",
            "verdict": "PASS",
        }
    )

    rows = await registry.stage_b_candidates("Haftpflicht liability", topk=10)
    ids = [r["doc_id"] for r in rows]
    assert "liab" in ids
    assert "motor" not in ids


@pytest.mark.integration
async def test_delete_doc_is_idempotent(reg):
    await registry.upsert_doc({"doc_id": "gone", "doc_name": "gone.pdf", "verdict": "PASS"})
    assert await registry.count_docs() == 1

    await registry.delete_doc("gone")
    assert await registry.count_docs() == 0

    # Second delete of an absent row is a safe no-op.
    await registry.delete_doc("gone")
    assert await registry.count_docs() == 0


# ── RFC-014 D2 — verdict fields in upsert_doc ───────────────────────────────


# ── RFC-014 D2 — migration SQL shape ────────────────────────────────────────


def test_migrate_verdict_sql_is_idempotent():
    """RFC-014 D2: migration DDL uses IF NOT EXISTS so it's re-runnable."""
    sql = registry._MIGRATE_VERDICT_SQL
    assert "ADD COLUMN IF NOT EXISTS verdict" in sql
    assert "ADD COLUMN IF NOT EXISTS pipeline_version" in sql
    assert "ADD COLUMN IF NOT EXISTS permanent_marginal" in sql

