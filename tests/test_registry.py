# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Registry operations: core registry, mirror sync, and dual-write consistency tests."""

from __future__ import annotations

import copy
import dataclasses
import inspect
import json
import logging
import os
import warnings
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import pytest
import pytest_asyncio

from pageindex_mcp import registry
from pageindex_mcp import registry_backfill as rb
from pageindex_mcp.registry_backfill import backfill as _bf
from pageindex_mcp.worker.registry_mirror import (
    _enqueue_verdict_retry,
    _upsert_registry_row,
)


# --- from test_registry.py ---


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
    with patch("pageindex_mcp.registry.schema.get_pool", return_value=None):
        yield


def _mock_pool() -> AsyncMock:
    """An AsyncMock standing in for an asyncpg pool."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


def _wire_backfill_settings(monkeypatch):
    patched = dataclasses.replace(
        rb.settings,
        registry_enabled=True,
        postgres_dsn="postgresql://user:pass@localhost:5432/pageindex",
    )
    monkeypatch.setattr(rb, "settings", patched)
    monkeypatch.setattr(_bf, "settings", patched)


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
    with patch("pageindex_mcp.registry.schema.get_pool", return_value=pool):
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
    with patch("pageindex_mcp.registry.schema.get_pool", return_value=pool):
        await registry.upsert_doc({"doc_id": "abc123", "doc_name": "only-name.pdf"})

    # Zone-4 Phase 3: upsert_doc now uses fetchrow (RETURNING) not execute.
    args = pool.fetchrow.await_args.args
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
    with patch("pageindex_mcp.registry.schema.get_pool", return_value=pool):
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
    with patch("pageindex_mcp.registry.schema.get_pool", return_value=pool):
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
    with patch("pageindex_mcp.registry.schema.get_pool", return_value=pool):
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
    with patch("pageindex_mcp.registry.schema.get_pool", return_value=pool):
        assert await registry.stage_a_filter("huk coburg policy") is None
    pool.fetch.assert_not_awaited()


async def test_stage_a_does_not_substring_match():
    """'huk' inside a longer token must NOT match the 'huk' facet value."""
    registry.refresh_known_facets({"product": {"huk"}})
    pool = _mock_pool()
    with patch("pageindex_mcp.registry.schema.get_pool", return_value=pool):
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

    monkeypatch.setattr(_bf, "init_registry", AsyncMock())
    monkeypatch.setattr(_bf, "close_registry", AsyncMock())
    monkeypatch.setattr(_bf, "is_registry_complete", AsyncMock(return_value=False))
    set_registry_complete = AsyncMock()
    monkeypatch.setattr(_bf, "set_registry_complete", set_registry_complete)
    monkeypatch.setattr(_bf, "_list_meta_keys", lambda: ["processed/abc.meta.json"])
    monkeypatch.setattr(_bf, "_upsert_all", AsyncMock(return_value=[]))
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


# ---------------------------------------------------------------------------
# Zone-4 Phase 3: upsert_doc RETURNING with CAS guards (contract)
# ---------------------------------------------------------------------------


def test_upsert_sql_has_returning_clause():
    """Zone-4 Phase 3: _UPSERT_SQL must include RETURNING with the verdict
    columns so the caller knows the winning values after CAS resolution."""
    from pageindex_mcp.registry.queries import _UPSERT_SQL

    sql = _UPSERT_SQL
    assert "RETURNING" in sql
    assert "doc_id" in sql.split("RETURNING")[1]
    assert "verdict" in sql.split("RETURNING")[1]
    assert "pipeline_version" in sql.split("RETURNING")[1]
    assert "permanent_marginal" in sql.split("RETURNING")[1]
    assert "verdict_computed_at" in sql.split("RETURNING")[1]


def test_upsert_sql_has_verdict_cas_guard():
    """RFC-037 D1: verdict columns are guarded by a max-priority-wins CASE."""
    from pageindex_mcp.registry.queries import _UPSERT_SQL

    sql = _UPSERT_SQL
    assert "EXCLUDED.verdict = 'PASS' THEN 3" in sql
    assert "doc_registry.verdict = 'PASS' THEN 3" in sql


def test_upsert_sql_has_processed_at_cas_guard():
    """Zone-4: descriptor columns sha256/node_count guarded by processed_at CAS."""
    from pageindex_mcp.registry.queries import _UPSERT_SQL

    sql = _UPSERT_SQL
    assert "EXCLUDED.processed_at >= COALESCE(doc_registry.processed_at" in sql


async def test_upsert_doc_uses_fetchrow_not_execute():
    """Zone-4 Phase 3: upsert_doc must use fetchrow (not execute) so it can
    return the RETURNING row as a dict."""
    pool = _mock_pool()
    pool.fetchrow = AsyncMock(
        return_value={"doc_id": "fr-1", "verdict": "PASS", "pipeline_version": 3,
                      "permanent_marginal": False, "verdict_computed_at": "2026-08-01"}
    )
    with patch("pageindex_mcp.registry.schema.get_pool", return_value=pool):
        result = await registry.upsert_doc({"doc_id": "fr-1", "verdict": "PASS"})

    pool.fetchrow.assert_awaited_once()
    assert result is not None
    assert result["doc_id"] == "fr-1"
    assert result["verdict"] == "PASS"


async def test_upsert_doc_returns_none_when_fetchrow_returns_none():
    """upsert_doc returns None when fetchrow returns None (edge case)."""
    pool = _mock_pool()
    pool.fetchrow = AsyncMock(return_value=None)
    with patch("pageindex_mcp.registry.schema.get_pool", return_value=pool):
        result = await registry.upsert_doc({"doc_id": "none-1"})

    assert result is None


async def test_upsert_verdict_deprecated_wrapper_delegates_to_upsert_doc():
    """Zone-4 Phase 3: upsert_verdict is a thin deprecated wrapper that
    delegates to upsert_doc with a minimal meta dict."""
    pool = _mock_pool()
    pool.fetchrow = AsyncMock(return_value={"doc_id": "dep-1", "verdict": "PASS",
                                            "pipeline_version": 2, "permanent_marginal": False,
                                            "verdict_computed_at": "2026-08-01"})
    with (
        patch("pageindex_mcp.registry.schema.get_pool", return_value=pool),
        warnings.catch_warnings(record=True) as w,
    ):
        warnings.simplefilter("always")
        result = await registry.upsert_verdict(
            "dep-1", {"verdict": "PASS", "pipeline_version": 2}
        )

    assert result is not None
    assert result["doc_id"] == "dep-1"
    # DeprecationWarning emitted
    assert any(issubclass(warning.category, DeprecationWarning) for warning in w)


# ---------------------------------------------------------------------------
# Zone-4 Phase 3: registry_verdict_authority removed from Settings (contract)
# ---------------------------------------------------------------------------


def test_settings_no_registry_verdict_authority_field():
    """Zone-4 Phase 3 contract: the registry_verdict_authority field must
    NOT exist on Settings.  Postgres is unconditionally the sole verdict
    authority; no mode flag remains."""
    from pageindex_mcp.config import Settings

    field_names = {f.name for f in dataclasses.fields(Settings)}
    assert "registry_verdict_authority" not in field_names, (
        "registry_verdict_authority must be removed from Settings (Zone-4 Phase 3)"
    )


def test_settings_has_no_verdict_authority_env_var():
    """Zone-4 Phase 3 contract: no environment variable loading path for
    REGISTRY_VERDICT_AUTHORITY should exist in config module."""
    import pageindex_mcp.config as config_mod

    source = inspect.getsource(config_mod)
    # The string should not appear in any executable line (comments are OK).
    # Filter out comment lines.
    executable_lines = [
        line for line in source.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    for line in executable_lines:
        assert "REGISTRY_VERDICT_AUTHORITY" not in line, (
            f"Found REGISTRY_VERDICT_AUTHORITY in executable config line: {line.strip()}"
        )


# ---------------------------------------------------------------------------
# Zone-4 Phase 3: upsert_doc returns dict not Record (contract)
# ---------------------------------------------------------------------------


async def test_upsert_doc_returns_dict_type():
    """Zone-4 Phase 3 contract: upsert_doc must return a plain dict (not an
    asyncpg Record) so callers can use it as a regular dict without conversion."""
    pool = _mock_pool()
    # Simulate an asyncpg Record-like object that supports dict()
    mock_row = MagicMock()
    mock_row.__iter__ = MagicMock(return_value=iter([
        ("doc_id", "dt-1"), ("verdict", "PASS"),
        ("pipeline_version", 3), ("permanent_marginal", False),
        ("verdict_computed_at", "2026-08-01"),
    ]))
    mock_row.keys = MagicMock(return_value=[
        "doc_id", "verdict", "pipeline_version",
        "permanent_marginal", "verdict_computed_at",
    ])
    mock_row.__getitem__ = lambda self, k: {
        "doc_id": "dt-1", "verdict": "PASS",
        "pipeline_version": 3, "permanent_marginal": False,
        "verdict_computed_at": "2026-08-01",
    }[k]

    # dict(mock_row) needs to work -- simulate by making fetchrow return
    # something that dict() can convert
    pool.fetchrow = AsyncMock(return_value={
        "doc_id": "dt-1", "verdict": "PASS",
        "pipeline_version": 3, "permanent_marginal": False,
        "verdict_computed_at": "2026-08-01",
    })
    with patch("pageindex_mcp.registry.schema.get_pool", return_value=pool):
        result = await registry.upsert_doc({"doc_id": "dt-1", "verdict": "PASS"})

    assert isinstance(result, dict)
    assert result["doc_id"] == "dt-1"


# ---------------------------------------------------------------------------
# Wiring: force_verdict_override threads through _upsert_registry_row
# ---------------------------------------------------------------------------


class TestForceVerdictOverrideWiring:
    """Wiring test: force_verdict_override is popped from verdict_fields in
    _upsert_registry_row and passed as a kwarg to upsert_doc.  It must NOT
    be persisted as a column value in the meta dict sent to Postgres."""

    @pytest.mark.asyncio
    async def test_force_override_popped_and_passed_to_upsert_doc(self):
        """force_verdict_override=True in verdict_fields is popped and
        forwarded as kwarg to upsert_doc."""
        mock_upsert = AsyncMock(return_value={
            "doc_id": "w1", "verdict": "FAIL",
            "pipeline_version": 5, "permanent_marginal": False,
            "verdict_computed_at": "2026-08-25T00:00:00Z",
        })
        mock_fields = {
            "doc_id": "w1", "verdict": "FAIL",
            "pipeline_version": 5, "content_class": "flat_prose",
        }

        with (
            patch("pageindex_mcp.worker.registry_mirror.settings",
                  MagicMock(registry_enabled=True, postgres_dsn="postgresql://x")),
            patch("pageindex_mcp.registry.get_pool", return_value=MagicMock()),
            patch("pageindex_mcp.registry.upsert_doc", mock_upsert),
            patch("pageindex_mcp.worker.registry_mirror.read_registry_fields",
                  return_value=mock_fields),
            patch("pageindex_mcp.storage.verdict.save_doc_meta"),
            patch("pageindex_mcp.worker.registry_mirror.REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP",
                  MagicMock()),
            patch("pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
                  AsyncMock()),
        ):
            await _upsert_registry_row(
                "w1", "flat_prose",
                verdict_fields={"verdict": "FAIL", "force_verdict_override": True},
            )

        mock_upsert.assert_awaited_once()
        call_kwargs = mock_upsert.await_args.kwargs
        assert call_kwargs.get("force_verdict_override") is True
        # The meta dict (positional arg) must NOT contain force_verdict_override
        meta_arg = mock_upsert.await_args.args[0]
        assert "force_verdict_override" not in meta_arg

    @pytest.mark.asyncio
    async def test_default_override_false_when_not_in_fields(self):
        """When verdict_fields lacks force_verdict_override, default is False."""
        mock_upsert = AsyncMock(return_value={
            "doc_id": "w2", "verdict": "PASS",
            "pipeline_version": 4, "permanent_marginal": False,
            "verdict_computed_at": "2026-08-25T00:00:00Z",
        })
        mock_fields = {
            "doc_id": "w2", "verdict": "PASS",
            "pipeline_version": 4, "content_class": "flat_prose",
        }

        with (
            patch("pageindex_mcp.worker.registry_mirror.settings",
                  MagicMock(registry_enabled=True, postgres_dsn="postgresql://x")),
            patch("pageindex_mcp.registry.get_pool", return_value=MagicMock()),
            patch("pageindex_mcp.registry.upsert_doc", mock_upsert),
            patch("pageindex_mcp.worker.registry_mirror.read_registry_fields",
                  return_value=mock_fields),
            patch("pageindex_mcp.storage.verdict.save_doc_meta"),
            patch("pageindex_mcp.worker.registry_mirror.REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP",
                  MagicMock()),
            patch("pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
                  AsyncMock()),
        ):
            await _upsert_registry_row(
                "w2", "flat_prose",
                verdict_fields={"verdict": "PASS"},
            )

        call_kwargs = mock_upsert.await_args.kwargs
        assert call_kwargs.get("force_verdict_override") is False


# ---------------------------------------------------------------------------
# Wiring: force_verdict_override import verification
# ---------------------------------------------------------------------------


def test_force_verdict_override_importable_from_queries():
    """The force_verdict_override parameter must exist on upsert_doc."""
    from pageindex_mcp.registry.queries import upsert_doc

    sig = inspect.signature(upsert_doc)
    assert "force_verdict_override" in sig.parameters
    param = sig.parameters["force_verdict_override"]
    assert param.default is False


def test_verdict_downgrade_enabled_in_pipeline_config():
    """VERDICT_DOWNGRADE_ENABLED must be a field on PipelineConfig."""
    from pageindex_mcp.config import PipelineConfig

    field_names = {f.name for f in dataclasses.fields(PipelineConfig)}
    assert "verdict_downgrade_enabled" in field_names


# --- from test_registry_mirror.py ---


# ---------------------------------------------------------------------------
# Helper: settings factory for mirror tests
# ---------------------------------------------------------------------------


def _mirror_settings(**overrides):
    from pageindex_mcp.config import settings as _base_settings

    return dataclasses.replace(_base_settings, **overrides)


_MIRROR_REGISTRY_ENABLED = _mirror_settings(
    registry_enabled=True,
    postgres_dsn="postgresql://user:pass@localhost:5432/pageindex",
)


# ---------------------------------------------------------------------------
# Contract: single linear path (no branching on registry_verdict_authority)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_single_linear_path_reads_minio_then_upserts():
    """_upsert_registry_row reads MinIO fields, then does ONE upsert_doc call
    (the single linear Postgres-authoritative path)."""
    minio_fields = {"doc_id": "doc-1", "doc_name": "test.pdf", "sha256": "abc"}
    winning_row = {"doc_id": "doc-1", "verdict": "PASS", "pipeline_version": 3}

    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", _MIRROR_REGISTRY_ENABLED),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch(
            "pageindex_mcp.registry.upsert_doc",
            AsyncMock(return_value=winning_row),
        ) as mock_upsert,
        patch(
            "pageindex_mcp.worker.registry_mirror.read_registry_fields",
            return_value=minio_fields,
        ) as mock_read,
        patch(
            "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
            AsyncMock(),
        ),
        patch("pageindex_mcp.storage.verdict.save_doc_meta") as mock_save_meta,
    ):
        await _upsert_registry_row("doc-1", "flat_table")

    # Exactly one read (MinIO), exactly one upsert (Postgres)
    mock_read.assert_called_once_with("doc-1", "flat_table")
    mock_upsert.assert_awaited_once()
    # upsert receives the MinIO-read fields dict
    upserted = mock_upsert.await_args[0][0]
    assert upserted["doc_id"] == "doc-1"
    assert upserted["sha256"] == "abc"


# ---------------------------------------------------------------------------
# Regression: backward compat with verdict_fields=None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_registry_row_verdict_fields_none_backward_compat():
    """preprocess_client.py calls _upsert_registry_row(doc_id, content_class)
    without verdict_fields. This must not raise and must still upsert the
    MinIO-read fields."""
    minio_fields = {"doc_id": "batch-1", "doc_name": "batch.pdf"}

    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", _MIRROR_REGISTRY_ENABLED),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch(
            "pageindex_mcp.registry.upsert_doc",
            AsyncMock(return_value=None),
        ) as mock_upsert,
        patch(
            "pageindex_mcp.worker.registry_mirror.read_registry_fields",
            return_value=minio_fields,
        ),
        patch(
            "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
            AsyncMock(),
        ),
    ):
        # verdict_fields not passed (defaults to None)
        await _upsert_registry_row("batch-1", None)

    mock_upsert.assert_awaited_once()
    upserted = mock_upsert.await_args[0][0]
    assert upserted["doc_id"] == "batch-1"
    # No verdict fields overlay
    assert "verdict" not in upserted


@pytest.mark.asyncio
async def test_upsert_registry_row_verdict_fields_overlay():
    """When verdict_fields is provided, it overlays on top of MinIO-read fields
    so job-context data takes precedence over stale artifact data."""
    minio_fields = {"doc_id": "vf-1", "doc_name": "test.pdf", "verdict": "MARGINAL"}
    verdict_fields = {"verdict": "PASS", "pipeline_version": 5, "verdict_computed_at": "2026-08-01"}

    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", _MIRROR_REGISTRY_ENABLED),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch(
            "pageindex_mcp.registry.upsert_doc",
            AsyncMock(return_value=None),
        ) as mock_upsert,
        patch(
            "pageindex_mcp.worker.registry_mirror.read_registry_fields",
            return_value=minio_fields,
        ),
        patch(
            "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
            AsyncMock(),
        ),
    ):
        await _upsert_registry_row("vf-1", None, verdict_fields=verdict_fields)

    upserted = mock_upsert.await_args[0][0]
    assert upserted["verdict"] == "PASS"  # overlay wins
    assert upserted["pipeline_version"] == 5
    assert upserted["verdict_computed_at"] == "2026-08-01"


# ---------------------------------------------------------------------------
# Contract: pool-not-ready queues verdict retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_registry_row_pool_not_ready_enqueues_verdict_retry():
    """When get_pool() returns None and verdict_fields is provided,
    _enqueue_verdict_retry is called to preserve the verdict for later replay."""
    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", _MIRROR_REGISTRY_ENABLED),
        patch("pageindex_mcp.registry.get_pool", return_value=None),
        patch(
            "pageindex_mcp.worker.registry_mirror._enqueue_verdict_retry",
            AsyncMock(),
        ) as mock_enqueue,
    ):
        await _upsert_registry_row("doc-retry", None, verdict_fields={"verdict": "PASS"})

    mock_enqueue.assert_awaited_once_with("doc-retry", {"verdict": "PASS"})


@pytest.mark.asyncio
async def test_upsert_registry_row_pool_not_ready_no_verdict_fields_no_enqueue():
    """When get_pool() returns None and verdict_fields is None (batch CLI path),
    nothing is enqueued."""
    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", _MIRROR_REGISTRY_ENABLED),
        patch("pageindex_mcp.registry.get_pool", return_value=None),
        patch(
            "pageindex_mcp.worker.registry_mirror._enqueue_verdict_retry",
            AsyncMock(),
        ) as mock_enqueue,
    ):
        await _upsert_registry_row("doc-noretry", None)

    mock_enqueue.assert_not_awaited()


# ---------------------------------------------------------------------------
# Contract: registry_fields kwarg skips MinIO re-read (Zone-7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_registry_row_registry_fields_skips_minio_read():
    """Zone-7: when registry_fields is provided, _upsert_registry_row must NOT
    call read_registry_fields (no MinIO re-read). upsert_doc receives the
    registry_fields values directly."""
    registry_fields = {
        "doc_name": "test.pdf",
        "source_url": "http://x",
        "processed_at": "2026-08-26T00:00:00Z",
        "sha256": "abc123",
        "doc_description": "desc",
        "product": "",
        "tier": "",
        "doc_family": "",
        "effective_date": "",
        "node_count": 5,
    }

    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", _MIRROR_REGISTRY_ENABLED),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch(
            "pageindex_mcp.registry.upsert_doc",
            AsyncMock(return_value=None),
        ) as mock_upsert,
        patch(
            "pageindex_mcp.worker.registry_mirror.read_registry_fields",
            return_value={"doc_id": "SHOULD-NOT-BE-CALLED"},
        ) as mock_read,
        patch(
            "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
            AsyncMock(),
        ),
    ):
        await _upsert_registry_row(
            "rf-1", None, registry_fields=registry_fields,
        )

    # read_registry_fields must NOT be called when registry_fields is provided
    mock_read.assert_not_called()
    mock_upsert.assert_awaited_once()
    upserted = mock_upsert.await_args[0][0]
    assert upserted["doc_id"] == "rf-1"
    assert upserted["sha256"] == "abc123"
    assert upserted["doc_name"] == "test.pdf"
    assert upserted["node_count"] == 5


@pytest.mark.asyncio
async def test_upsert_registry_row_registry_fields_none_falls_back_to_minio():
    """Zone-7 backward compat: when registry_fields is None (older child binary
    or batch CLI), read_registry_fields IS called."""
    minio_fields = {"doc_id": "compat-1", "doc_name": "old.pdf", "sha256": "def"}

    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", _MIRROR_REGISTRY_ENABLED),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch(
            "pageindex_mcp.registry.upsert_doc",
            AsyncMock(return_value=None),
        ) as mock_upsert,
        patch(
            "pageindex_mcp.worker.registry_mirror.read_registry_fields",
            return_value=minio_fields,
        ) as mock_read,
        patch(
            "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
            AsyncMock(),
        ),
    ):
        await _upsert_registry_row("compat-1", None, registry_fields=None)

    mock_read.assert_called_once_with("compat-1", None)
    mock_upsert.assert_awaited_once()
    upserted = mock_upsert.await_args[0][0]
    assert upserted["doc_id"] == "compat-1"


# ---------------------------------------------------------------------------
# Contract: verdict_fields overlay on top of registry_fields (Zone-7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_registry_row_verdict_fields_override_registry_fields():
    """Zone-7: when both registry_fields and verdict_fields are provided,
    verdict_fields values must override any overlapping keys in
    registry_fields (overlay semantics preserved)."""
    registry_fields = {
        "doc_name": "test.pdf",
        "source_url": "http://x",
        "processed_at": "2026-08-26T00:00:00Z",
        "sha256": "abc123",
        "doc_description": "",
        "product": "",
        "tier": "",
        "doc_family": "",
        "effective_date": "",
        "node_count": 5,
    }
    verdict_fields = {
        "verdict": "PASS",
        "pipeline_version": 5,
        "verdict_computed_at": "2026-08-26T01:00:00Z",
        "node_count": 10,  # overlapping key -- verdict_fields should win
    }

    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", _MIRROR_REGISTRY_ENABLED),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch(
            "pageindex_mcp.registry.upsert_doc",
            AsyncMock(return_value=None),
        ) as mock_upsert,
        patch(
            "pageindex_mcp.worker.registry_mirror.read_registry_fields",
            return_value={"should": "not-be-called"},
        ) as mock_read,
        patch(
            "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
            AsyncMock(),
        ),
    ):
        await _upsert_registry_row(
            "overlay-1", None,
            verdict_fields=verdict_fields,
            registry_fields=registry_fields,
        )

    mock_read.assert_not_called()
    mock_upsert.assert_awaited_once()
    upserted = mock_upsert.await_args[0][0]
    # verdict_fields overlay wins over registry_fields for overlapping keys
    assert upserted["verdict"] == "PASS"
    assert upserted["pipeline_version"] == 5
    assert upserted["node_count"] == 10  # verdict_fields value wins
    # registry_fields base values still present
    assert upserted["sha256"] == "abc123"
    assert upserted["doc_name"] == "test.pdf"


# ---------------------------------------------------------------------------
# Contract: registry disabled -> early return, no upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_registry_row_registry_disabled_noop():
    """When registry_enabled is False, _upsert_registry_row returns immediately."""
    disabled = _mirror_settings(registry_enabled=False, postgres_dsn="")

    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", disabled),
        patch("pageindex_mcp.registry.upsert_doc", AsyncMock()) as mock_upsert,
    ):
        await _upsert_registry_row("doc-x", None)

    mock_upsert.assert_not_awaited()


# ---------------------------------------------------------------------------
# Regression: registry disabled logs degraded-consistency warning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_registry_row_disabled_logs_degraded_consistency(caplog):
    """When registry_enabled is False, _upsert_registry_row must log a message
    containing 'degraded consistency' so operators can detect that the effective
    consistency model changed (sidecar-only, no Postgres authority)."""
    disabled = _mirror_settings(registry_enabled=False, postgres_dsn="")

    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", disabled),
        caplog.at_level(logging.INFO, logger="pageindex_mcp.worker.registry_mirror"),
    ):
        await _upsert_registry_row("doc-degraded", None)

    degraded_msgs = [
        r.message for r in caplog.records if "degraded consistency" in r.message
    ]
    assert len(degraded_msgs) >= 1, (
        f"Expected 'degraded consistency' log but got: "
        f"{[r.message for r in caplog.records]}"
    )
    # The message should mention the doc_id for traceability
    assert "doc-degraded" in degraded_msgs[0]


@pytest.mark.asyncio
async def test_upsert_registry_row_pool_not_ready_logs_degraded_consistency(caplog):
    """When registry is enabled but pool is not ready, _upsert_registry_row must
    also log 'degraded consistency'."""
    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", _MIRROR_REGISTRY_ENABLED),
        patch("pageindex_mcp.registry.get_pool", return_value=None),
        patch(
            "pageindex_mcp.worker.registry_mirror._enqueue_verdict_retry",
            AsyncMock(),
        ),
        caplog.at_level(logging.INFO, logger="pageindex_mcp.worker.registry_mirror"),
    ):
        await _upsert_registry_row("doc-pooldown", None)

    degraded_msgs = [
        r.message for r in caplog.records if "degraded consistency" in r.message
    ]
    assert len(degraded_msgs) >= 1, (
        f"Expected 'degraded consistency' log but got: "
        f"{[r.message for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# Contract: best-effort sidecar backfill with winning row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_registry_row_backfills_sidecar_with_winning_row():
    """After a successful upsert, save_doc_meta is called with the winning row
    dict returned by upsert_doc (best-effort sidecar convergence)."""
    winning = {"doc_id": "bf-1", "verdict": "PASS", "pipeline_version": 4}
    save_calls = []

    def _capture_save(doc_id, meta):
        save_calls.append((doc_id, meta))

    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", _MIRROR_REGISTRY_ENABLED),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch(
            "pageindex_mcp.registry.upsert_doc",
            AsyncMock(return_value=winning),
        ),
        patch(
            "pageindex_mcp.worker.registry_mirror.read_registry_fields",
            return_value={"doc_id": "bf-1"},
        ),
        patch(
            "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
            AsyncMock(),
        ),
        # save_doc_meta is lazily imported from ..storage inside the function;
        # patching the storage module's attribute catches both direct and
        # asyncio.to_thread calls.
        patch("pageindex_mcp.storage.save_doc_meta", _capture_save),
    ):
        await _upsert_registry_row("bf-1", None)

    assert len(save_calls) == 1
    assert save_calls[0] == ("bf-1", winning)


# ---------------------------------------------------------------------------
# Contract: save_doc_meta failure during sidecar backfill is swallowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_registry_row_sidecar_backfill_failure_swallowed():
    """Zone-4 Phase 3 contract: save_doc_meta failure during best-effort
    sidecar backfill must be swallowed (logged, never raised) so the
    caller's job status is not affected by a MinIO hiccup."""
    winning = {"doc_id": "sw-1", "verdict": "PASS", "pipeline_version": 4}

    def _exploding_save(doc_id, meta):
        raise RuntimeError("MinIO unreachable during sidecar backfill")

    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", _MIRROR_REGISTRY_ENABLED),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch(
            "pageindex_mcp.registry.upsert_doc",
            AsyncMock(return_value=winning),
        ),
        patch(
            "pageindex_mcp.worker.registry_mirror.read_registry_fields",
            return_value={"doc_id": "sw-1"},
        ),
        patch(
            "pageindex_mcp.worker.registry_mirror._mirror_registry_write_failure_to_redis",
            AsyncMock(),
        ),
        patch(
            "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
            AsyncMock(),
        ),
        patch("pageindex_mcp.storage.save_doc_meta", _exploding_save),
    ):
        # Must NOT raise — the exception is caught inside _upsert_registry_row
        await _upsert_registry_row("sw-1", None)


# ---------------------------------------------------------------------------
# Contract: upsert_doc exception triggers metric mirror + failure mirror
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_registry_row_upsert_failure_mirrors_to_redis():
    """Zone-4 Phase 3 contract: when upsert_doc raises, the function must
    call _mirror_registry_write_failure_to_redis and NOT propagate the
    exception to the caller."""
    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", _MIRROR_REGISTRY_ENABLED),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch(
            "pageindex_mcp.registry.upsert_doc",
            AsyncMock(side_effect=RuntimeError("Postgres connection refused")),
        ),
        patch(
            "pageindex_mcp.worker.registry_mirror.read_registry_fields",
            return_value={"doc_id": "fail-1"},
        ),
        patch(
            "pageindex_mcp.worker.registry_mirror._mirror_registry_write_failure_to_redis",
            AsyncMock(),
        ) as mock_fail_mirror,
        patch(
            "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
            AsyncMock(),
        ),
    ):
        # Must NOT raise
        await _upsert_registry_row("fail-1", None)

    mock_fail_mirror.assert_awaited_once()


# ---------------------------------------------------------------------------
# Contract: no MinIO read when both fields=None and verdict_fields=None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_registry_row_no_minio_no_verdict_skips_upsert():
    """When read_registry_fields returns None and no verdict_fields are
    provided, no upsert is attempted (nothing to write)."""
    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", _MIRROR_REGISTRY_ENABLED),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch(
            "pageindex_mcp.registry.upsert_doc",
            AsyncMock(),
        ) as mock_upsert,
        patch(
            "pageindex_mcp.worker.registry_mirror.read_registry_fields",
            return_value=None,
        ),
        patch(
            "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
            AsyncMock(),
        ),
    ):
        await _upsert_registry_row("empty-1", None)

    mock_upsert.assert_not_awaited()


# --- from test_dual_write_consistency.py ---


# ---------------------------------------------------------------------------
# Source-reading helper (avoids importing client module which has a broken
# VERDICT_DOWNGRADE_ENABLED import in the current branch state -- the
# indexer.py imports a module-level constant that hasn't been added to
# config.py yet).
# ---------------------------------------------------------------------------

_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp"


def _read_src(relpath: str) -> str:
    return (_SRC_ROOT / relpath).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. CONTRACT: last_registry_fields stash in _persist_tree_result
# ---------------------------------------------------------------------------


class TestTreeResultRegistryFieldsStash:
    """_persist_tree_result must stash last_registry_fields on the client
    instance so converters_cli can surface them in stdout JSON."""

    def test_last_registry_fields_set_after_tree_persist(self):
        """The _persist_tree_result method sets self.last_registry_fields
        with the correct keys mirroring _REGISTRY_FIELDS."""
        src = _read_src("client/indexer.py")
        expected_keys = {
            "doc_name",
            "source_url",
            "processed_at",
            "sha256",
            "doc_description",
            "product",
            "tier",
            "doc_family",
            "effective_date",
            "node_count",
        }
        persist_tree_idx = src.index("def _persist_tree_result")
        # Find next top-level async def (same indent level)
        next_def = src.find("\n    async def ", persist_tree_idx + 10)
        if next_def == -1:
            next_def = len(src)
        tree_src = src[persist_tree_idx:next_def]
        assert "last_registry_fields" in tree_src
        for key in expected_keys:
            assert f'"{key}"' in tree_src, (
                f"Missing key {key!r} in _persist_tree_result registry stash"
            )

    def test_tree_stash_includes_dynamic_node_count(self):
        """The tree path must compute node_count via _tree_node_count(structure)
        not hardcode 0 (unlike the flat path which correctly uses 0)."""
        src = _read_src("client/indexer.py")
        persist_tree_idx = src.index("def _persist_tree_result")
        next_def = src.find("\n    async def ", persist_tree_idx + 10)
        if next_def == -1:
            next_def = len(src)
        tree_src = src[persist_tree_idx:next_def]
        # Between last_registry_fields and the closing brace, _tree_node_count
        # should appear (not a hardcoded 0).
        rf_idx = tree_src.index("last_registry_fields")
        rf_block = tree_src[rf_idx:rf_idx + 600]
        assert "_tree_node_count" in rf_block

    def test_tree_stash_verdict_fields_also_set(self):
        """_persist_tree_result must also set last_verdict_fields (Zone-7
        existing contract)."""
        src = _read_src("client/indexer.py")
        persist_tree_idx = src.index("def _persist_tree_result")
        next_def = src.find("\n    async def ", persist_tree_idx + 10)
        if next_def == -1:
            next_def = len(src)
        tree_src = src[persist_tree_idx:next_def]
        assert "last_verdict_fields" in tree_src


# ---------------------------------------------------------------------------
# 2. CONTRACT: last_registry_fields stash in _persist_flat_result
# ---------------------------------------------------------------------------


class TestFlatResultRegistryFieldsStash:
    """_persist_flat_result must stash last_registry_fields with node_count=0
    (flat docs have no tree structure)."""

    def test_flat_stash_keys_match_contract(self):
        """Flat result stash must contain the registry field keys plus
        content_class (flat-specific)."""
        src = _read_src("client/indexer.py")
        persist_flat_idx = src.index("def _persist_flat_result")
        next_def = src.find("\n    async def ", persist_flat_idx + 10)
        if next_def == -1:
            next_def = len(src)
        flat_src = src[persist_flat_idx:next_def]
        assert "last_registry_fields" in flat_src
        expected_keys = {
            "doc_name",
            "source_url",
            "processed_at",
            "sha256",
            "content_class",
            "doc_description",
            "product",
            "tier",
            "doc_family",
            "effective_date",
            "node_count",
        }
        for key in expected_keys:
            assert f'"{key}"' in flat_src, (
                f"Missing key {key!r} in _persist_flat_result registry stash"
            )

    def test_flat_stash_node_count_is_zero(self):
        """Flat docs have no tree: node_count must be hardcoded 0."""
        src = _read_src("client/indexer.py")
        persist_flat_idx = src.index("def _persist_flat_result")
        next_def = src.find("\n    async def ", persist_flat_idx + 10)
        if next_def == -1:
            next_def = len(src)
        flat_src = src[persist_flat_idx:next_def]
        rf_idx = flat_src.index("last_registry_fields")
        block = flat_src[rf_idx:rf_idx + 600]
        assert '"node_count": 0' in block


# ---------------------------------------------------------------------------
# 3. EXHAUSTIVENESS: converters_cli verdict_fields surfacing
# ---------------------------------------------------------------------------


class TestConvertersCliDualWriteFields:
    """converters_cli surfaces verdict_fields via getattr pattern;
    last_registry_fields stashed in indexer for future surfacing."""

    def test_verdict_fields_surfaced_via_getattr(self):
        """converters_cli must use getattr() for last_verdict_fields."""
        src = _read_src("converters_cli.py")
        assert 'getattr(client, "last_verdict_fields"' in src

    def test_verdict_fields_added_to_payload_when_truthy(self):
        """verdict_fields is conditionally added to the payload dict."""
        src = _read_src("converters_cli.py")
        assert 'payload["verdict_fields"]' in src

    def test_content_class_surfaced_via_getattr(self):
        """content_class is surfaced the same way."""
        src = _read_src("converters_cli.py")
        assert 'getattr(client, "last_content_class"' in src

    def test_indexer_stashes_last_registry_fields_both_paths(self):
        """The indexer stashes last_registry_fields on both persist paths."""
        src = _read_src("client/indexer.py")
        tree_idx = src.index("def _persist_tree_result")
        flat_idx = src.index("def _persist_flat_result")
        assert "last_registry_fields" in src[tree_idx:]
        assert "last_registry_fields" in src[flat_idx:]


# ---------------------------------------------------------------------------
# 4. WIRING: worker/job.py extracts registry_fields and passes to
#    _upsert_registry_row
# ---------------------------------------------------------------------------


class TestJobVerdictFieldsWiring:
    """process_document_job must extract verdict_fields from the subprocess
    result dict and pass it as a kwarg to _upsert_registry_row."""

    def test_verdict_fields_extracted_from_result(self):
        """The job handler must call result.get('verdict_fields')."""
        src = _read_src("worker/job.py")
        assert 'result.get("verdict_fields")' in src

    def test_verdict_fields_passed_as_kwarg_to_upsert(self):
        """_upsert_registry_row must be called with verdict_fields= kwarg."""
        src = _read_src("worker/job.py")
        assert "verdict_fields=verdict_fields" in src

    def test_upsert_registry_row_imported_from_registry_mirror(self):
        """The job handler must import _upsert_registry_row from registry_mirror."""
        src = _read_src("worker/job.py")
        assert "from .registry_mirror import _upsert_registry_row" in src

    def test_registry_mirror_accepts_registry_fields_kwarg(self):
        """_upsert_registry_row signature must accept registry_fields kwarg
        (ready for when job.py wires it from the child result)."""
        src = _read_src("worker/registry_mirror.py")
        fn_idx = src.index("async def _upsert_registry_row")
        sig_block = src[fn_idx:fn_idx + 300]
        assert "registry_fields" in sig_block


# ---------------------------------------------------------------------------
# 5. CONTRACT: registry_mirror skips MinIO re-read when registry_fields
#    supplied
# ---------------------------------------------------------------------------


def _dw_settings(**overrides):
    from pageindex_mcp.config import settings as _base_settings

    return dataclasses.replace(_base_settings, **overrides)


_DW_REGISTRY_ENABLED = _dw_settings(
    registry_enabled=True,
    postgres_dsn="postgresql://user:pass@localhost:5432/pageindex",
)


class TestRegistryMirrorSkipMinioReread:
    """When registry_fields is supplied, _upsert_registry_row must skip the
    read_registry_fields MinIO re-read entirely."""

    @pytest.mark.asyncio
    async def test_registry_fields_supplied_skips_minio_read(self):
        """When registry_fields dict is passed, read_registry_fields is
        never called (no MinIO round-trip)."""
        registry_fields = {
            "doc_name": "test.pdf",
            "sha256": "abc",
            "node_count": 5,
        }

        with (
            patch("pageindex_mcp.worker.registry_mirror.settings", _DW_REGISTRY_ENABLED),
            patch("pageindex_mcp.registry.get_pool", return_value=object()),
            patch(
                "pageindex_mcp.registry.upsert_doc",
                AsyncMock(return_value=None),
            ) as mock_upsert,
            patch(
                "pageindex_mcp.worker.registry_mirror.read_registry_fields",
            ) as mock_read,
            patch(
                "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
                AsyncMock(),
            ),
        ):
            await _upsert_registry_row(
                "doc-1", None,
                registry_fields=registry_fields,
            )

        mock_read.assert_not_called()
        mock_upsert.assert_awaited_once()
        upserted = mock_upsert.await_args[0][0]
        assert upserted["doc_name"] == "test.pdf"
        assert upserted["doc_id"] == "doc-1"

    @pytest.mark.asyncio
    async def test_registry_fields_none_falls_back_to_minio_read(self):
        """When registry_fields is None (backward compat), read_registry_fields
        is called to populate the fields from MinIO."""
        minio_fields = {"doc_id": "doc-2", "doc_name": "fallback.pdf"}

        with (
            patch("pageindex_mcp.worker.registry_mirror.settings", _DW_REGISTRY_ENABLED),
            patch("pageindex_mcp.registry.get_pool", return_value=object()),
            patch(
                "pageindex_mcp.registry.upsert_doc",
                AsyncMock(return_value=None),
            ),
            patch(
                "pageindex_mcp.worker.registry_mirror.read_registry_fields",
                return_value=minio_fields,
            ) as mock_read,
            patch(
                "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
                AsyncMock(),
            ),
        ):
            await _upsert_registry_row("doc-2", None, registry_fields=None)

        mock_read.assert_called_once_with("doc-2", None)

    @pytest.mark.asyncio
    async def test_registry_fields_content_class_backfilled(self):
        """When registry_fields lacks content_class but the arg is provided,
        it is backfilled into the fields dict."""
        registry_fields = {"doc_name": "test.pdf", "sha256": "abc"}

        with (
            patch("pageindex_mcp.worker.registry_mirror.settings", _DW_REGISTRY_ENABLED),
            patch("pageindex_mcp.registry.get_pool", return_value=object()),
            patch(
                "pageindex_mcp.registry.upsert_doc",
                AsyncMock(return_value=None),
            ) as mock_upsert,
            patch(
                "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
                AsyncMock(),
            ),
        ):
            await _upsert_registry_row(
                "doc-cc", "flat_table",
                registry_fields=registry_fields,
            )

        upserted = mock_upsert.await_args[0][0]
        assert upserted["content_class"] == "flat_table"

    @pytest.mark.asyncio
    async def test_verdict_fields_overlay_with_registry_fields(self):
        """When both registry_fields and verdict_fields are supplied,
        verdict_fields overlay takes precedence."""
        registry_fields = {"doc_name": "test.pdf", "sha256": "abc", "node_count": 5}
        verdict_fields = {"verdict": "PASS", "pipeline_version": 7}

        with (
            patch("pageindex_mcp.worker.registry_mirror.settings", _DW_REGISTRY_ENABLED),
            patch("pageindex_mcp.registry.get_pool", return_value=object()),
            patch(
                "pageindex_mcp.registry.upsert_doc",
                AsyncMock(return_value=None),
            ) as mock_upsert,
            patch(
                "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
                AsyncMock(),
            ),
        ):
            await _upsert_registry_row(
                "doc-both", None,
                verdict_fields=verdict_fields,
                registry_fields=registry_fields,
            )

        upserted = mock_upsert.await_args[0][0]
        assert upserted["doc_name"] == "test.pdf"
        assert upserted["verdict"] == "PASS"
        assert upserted["pipeline_version"] == 7

    @pytest.mark.asyncio
    async def test_registry_fields_is_copied_not_mutated(self):
        """The supplied registry_fields dict must be copied before mutation
        (doc_id insertion, content_class backfill) so the caller's dict is
        not modified."""
        registry_fields = {"doc_name": "test.pdf", "sha256": "abc"}
        original_keys = set(registry_fields.keys())

        with (
            patch("pageindex_mcp.worker.registry_mirror.settings", _DW_REGISTRY_ENABLED),
            patch("pageindex_mcp.registry.get_pool", return_value=object()),
            patch(
                "pageindex_mcp.registry.upsert_doc",
                AsyncMock(return_value=None),
            ),
            patch(
                "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
                AsyncMock(),
            ),
        ):
            await _upsert_registry_row(
                "doc-copy", "flat_table",
                registry_fields=registry_fields,
            )

        # Original dict must NOT have been mutated
        assert set(registry_fields.keys()) == original_keys


# ---------------------------------------------------------------------------
# 6. EXHAUSTIVENESS: hash_cache_delete Redis HDEL
# ---------------------------------------------------------------------------


class TestHashCacheDelete:
    """hash_cache_delete must perform Redis HDEL for erasure compliance."""

    def test_delete_removes_redis_entry(self):
        """hash_cache_delete must remove the filename from Redis HSET."""
        fake_redis = fakeredis.FakeRedis(decode_responses=True)

        with patch("pageindex_mcp.cache._redis_sync", fake_redis):
            from pageindex_mcp.storage.hash_cache import (
                HASH_CACHE_KEY,
                hash_cache_delete,
                hash_cache_set,
            )

            hash_cache_set("file1.pdf", "hash1")
            assert fake_redis.hget(HASH_CACHE_KEY, "file1.pdf") == "hash1"

            hash_cache_delete("file1.pdf")

            assert fake_redis.hget(HASH_CACHE_KEY, "file1.pdf") is None

    def test_delete_idempotent_on_missing_entry(self):
        """Deleting a non-existent key must not raise."""
        fake_redis = fakeredis.FakeRedis(decode_responses=True)

        with patch("pageindex_mcp.cache._redis_sync", fake_redis):
            from pageindex_mcp.storage.hash_cache import hash_cache_delete

            # Must not raise
            hash_cache_delete("nonexistent.pdf")

    def test_delete_does_not_affect_other_entries(self):
        """Deleting one entry must not affect other entries."""
        fake_redis = fakeredis.FakeRedis(decode_responses=True)

        with patch("pageindex_mcp.cache._redis_sync", fake_redis):
            from pageindex_mcp.storage.hash_cache import (
                HASH_CACHE_KEY,
                hash_cache_delete,
                hash_cache_set,
            )

            hash_cache_set("a.pdf", "hash-a")
            hash_cache_set("b.pdf", "hash-b")

            hash_cache_delete("a.pdf")

            assert fake_redis.hget(HASH_CACHE_KEY, "a.pdf") is None
            assert fake_redis.hget(HASH_CACHE_KEY, "b.pdf") == "hash-b"


# ---------------------------------------------------------------------------
# 7. REGRESSION: documents.py delete_doc HR2 cascade ordering
# ---------------------------------------------------------------------------


class TestDeleteDocCascadeOrdering:
    """delete_doc must execute the HR2 erasure cascade in the mandated
    order: uploads -> processed -> verdicts -> meta -> Redis -> hash-cache
    -> registry -> preloaded."""

    def test_cascade_steps_documented_in_docstring(self):
        """delete_doc docstring must mention all cascade step numbers."""
        src = _read_src("storage/documents.py")
        # Find the delete_doc function's docstring
        fn_idx = src.index("async def delete_doc")
        docstring_block = src[fn_idx:fn_idx + 600]
        # Step numbers mentioned in the docstring
        for step in ["1.", "2.", "3.", "4.", "5.", "6.", "7."]:
            assert step in docstring_block, f"Step {step} not in delete_doc docstring"

    def test_cascade_handles_missing_doc_idempotently(self):
        """delete_doc must be idempotent: missing objects tolerated."""
        src = _read_src("storage/documents.py")
        fn_idx = src.index("async def delete_doc")
        fn_src = src[fn_idx:fn_idx + 3000]
        # NoSuchKey must be tolerated in multiple steps
        assert "NoSuchKey" in fn_src

    @pytest.mark.asyncio
    async def test_delete_doc_returns_errors_dict(self):
        """delete_doc must return {"errors": [...]} structure."""
        from pageindex_mcp.storage.documents import delete_doc

        mock_mc = MagicMock()
        mock_mc.list_objects.return_value = []
        # Make all remove_object calls succeed
        mock_mc.remove_object.return_value = None
        # load_doc raises ValueError (doc already gone)
        from minio.error import S3Error

        mock_mc.get_object.side_effect = S3Error(
            MagicMock(), "NoSuchKey", "missing", "res", "req", "host"
        )

        with (
            patch("pageindex_mcp.storage.minio_ops.get_minio", return_value=mock_mc),
            patch("pageindex_mcp.storage.documents.load_doc", side_effect=ValueError("gone")),
            patch("pageindex_mcp.cache.doc_cache_delete"),
            patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
        ):
            result = await delete_doc("test-id")

        assert isinstance(result, dict)
        assert "errors" in result


# ---------------------------------------------------------------------------
# 8. CONTRACT: cleanup.py age-guard on stale row deletion
# ---------------------------------------------------------------------------


class TestCleanupAgeGuard:
    """_delete_stale_rows must have an age guard that protects freshly-ingested
    rows from being deleted as stale."""

    @pytest.mark.asyncio
    async def test_fresh_rows_protected_by_age_guard(self):
        """Rows with processed_at within the grace period must NOT be deleted."""
        from pageindex_mcp.registry_backfill.cleanup import _delete_stale_rows

        now_iso = datetime.now(UTC).isoformat()
        # 10 rows: 1 fresh stale candidate, 9 in MinIO
        registry_rows = {
            "fresh-stale": now_iso,  # just ingested -- should be age-protected
            **{f"minio-{i}": "2026-01-01T00:00:00+00:00" for i in range(9)},
        }
        minio_ids = {f"minio-{i}" for i in range(9)}
        mock_delete = AsyncMock()

        with (
            patch(
                "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
                AsyncMock(return_value=registry_rows),
            ),
            patch("pageindex_mcp.registry.delete_doc", mock_delete),
        ):
            await _delete_stale_rows(minio_ids, grace_minutes=10)

        # fresh-stale is within the grace period -- not deleted
        mock_delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_old_rows_deleted_as_stale(self):
        """Rows with processed_at older than the grace period must be deleted."""
        from pageindex_mcp.registry_backfill.cleanup import _delete_stale_rows

        # 10 rows: 1 old stale candidate, 9 in MinIO
        registry_rows = {
            "old-stale": "2020-01-01T00:00:00+00:00",  # very old
            **{f"minio-{i}": "2026-01-01T00:00:00+00:00" for i in range(9)},
        }
        minio_ids = {f"minio-{i}" for i in range(9)}
        mock_delete = AsyncMock()

        with (
            patch(
                "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
                AsyncMock(return_value=registry_rows),
            ),
            patch("pageindex_mcp.registry.delete_doc", mock_delete),
        ):
            await _delete_stale_rows(minio_ids, grace_minutes=10)

        mock_delete.assert_awaited_once()
        deleted_id = mock_delete.await_args[0][0]
        assert deleted_id == "old-stale"

    @pytest.mark.asyncio
    async def test_safety_threshold_prevents_mass_deletion(self):
        """When stale candidates exceed 50% of total registry, deletion
        is refused entirely."""
        from pageindex_mcp.registry_backfill.cleanup import _delete_stale_rows

        # 4 rows: 3 stale (75%), 1 in MinIO -- exceeds 50% threshold
        registry_rows = {
            "stale-1": "2020-01-01T00:00:00+00:00",
            "stale-2": "2020-01-01T00:00:00+00:00",
            "stale-3": "2020-01-01T00:00:00+00:00",
            "good-1": "2020-01-01T00:00:00+00:00",
        }
        minio_ids = {"good-1"}
        mock_delete = AsyncMock()

        with (
            patch(
                "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
                AsyncMock(return_value=registry_rows),
            ),
            patch("pageindex_mcp.registry.delete_doc", mock_delete),
        ):
            await _delete_stale_rows(minio_ids, grace_minutes=10)

        # 3/4 = 75% > 50% threshold -- no deletions
        mock_delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_stale_set_is_noop(self):
        """When all registry rows have MinIO counterparts, nothing is deleted."""
        from pageindex_mcp.registry_backfill.cleanup import _delete_stale_rows

        registry_rows = {"doc-1": "2026-01-01T00:00:00+00:00"}
        mock_delete = AsyncMock()

        with (
            patch(
                "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
                AsyncMock(return_value=registry_rows),
            ),
            patch("pageindex_mcp.registry.delete_doc", mock_delete),
        ):
            await _delete_stale_rows({"doc-1"}, grace_minutes=10)

        mock_delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_registry_rows_is_noop(self):
        """When list_all_doc_ids_with_timestamps returns None, nothing happens."""
        from pageindex_mcp.registry_backfill.cleanup import _delete_stale_rows

        mock_delete = AsyncMock()

        with (
            patch(
                "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
                AsyncMock(return_value=None),
            ),
            patch("pageindex_mcp.registry.delete_doc", mock_delete),
        ):
            await _delete_stale_rows(set(), grace_minutes=10)

        mock_delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# 9. EXHAUSTIVENESS: HR2 cascade store coverage
# ---------------------------------------------------------------------------


class TestHR2CascadeStoreCoverage:
    """delete_doc source must reference all stores from CLAUDE.md HR2:
    uploads, processed.json, flat.json, figures, verdicts, meta.json,
    Redis cache, hash-cache, registry, preloaded."""

    def _get_delete_doc_src(self):
        src = _read_src("storage/documents.py")
        fn_idx = src.index("async def delete_doc")
        return src[fn_idx:]

    def test_uploads_store_covered(self):
        assert f"uploads/" in self._get_delete_doc_src()

    def test_processed_json_store_covered(self):
        assert "processed/" in self._get_delete_doc_src()

    def test_flat_json_store_covered(self):
        assert ".flat.json" in self._get_delete_doc_src()

    def test_figures_store_covered(self):
        assert "figures/" in self._get_delete_doc_src()

    def test_verdicts_store_covered(self):
        assert "verdicts/" in self._get_delete_doc_src()

    def test_meta_json_store_covered(self):
        assert ".meta.json" in self._get_delete_doc_src()

    def test_redis_cache_store_covered(self):
        assert "doc_cache_delete" in self._get_delete_doc_src()

    def test_hash_cache_store_covered(self):
        assert "hash_cache_delete" in self._get_delete_doc_src()

    def test_preloaded_store_covered(self):
        assert "preloaded/" in self._get_delete_doc_src()
