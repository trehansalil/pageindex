# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Registry operations: core registry, mirror sync, and dual-write consistency tests."""

from __future__ import annotations

import copy
import dataclasses
import inspect
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


# ---------------------------------------------------------------------------
# Unit — Stage B recency fallback and error degradation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Unit — Stage A facet resolution (exact case-folded, never substring)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Unit — Redis registry_complete flag helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Zone-4 Phase 3: upsert_doc RETURNING with CAS guards (contract)
# ---------------------------------------------------------------------------


async def test_upsert_doc_uses_fetchrow_not_execute():
    """Zone-4 Phase 3: upsert_doc uses fetchrow (not execute) so it can return
    the RETURNING row as a plain dict, degrades to None when fetchrow returns
    nothing, and is what the deprecated upsert_verdict wrapper delegates to."""
    pool = _mock_pool()
    pool.fetchrow = AsyncMock(
        return_value={
            "doc_id": "fr-1",
            "verdict": "PASS",
            "pipeline_version": 3,
            "permanent_marginal": False,
            "verdict_computed_at": "2026-08-01",
        }
    )
    with patch("pageindex_mcp.registry.schema.get_pool", return_value=pool):
        result = await registry.upsert_doc({"doc_id": "fr-1", "verdict": "PASS"})

    pool.fetchrow.assert_awaited_once()
    pool.execute.assert_not_awaited()
    # Zone-4 Phase 3 contract: a plain dict, not an asyncpg Record.
    assert isinstance(result, dict)
    assert result["doc_id"] == "fr-1"
    assert result["verdict"] == "PASS"

    # Edge case: fetchrow returning nothing degrades to None, never raises.
    pool.fetchrow = AsyncMock(return_value=None)
    with patch("pageindex_mcp.registry.schema.get_pool", return_value=pool):
        assert await registry.upsert_doc({"doc_id": "none-1"}) is None

    # upsert_verdict is a thin deprecated wrapper that delegates here.
    pool.fetchrow = AsyncMock(
        return_value={
            "doc_id": "dep-1",
            "verdict": "PASS",
            "pipeline_version": 2,
            "permanent_marginal": False,
            "verdict_computed_at": "2026-08-01",
        }
    )
    with (
        patch("pageindex_mcp.registry.schema.get_pool", return_value=pool),
        warnings.catch_warnings(record=True) as caught,
    ):
        warnings.simplefilter("always")
        wrapped = await registry.upsert_verdict("dep-1", {"verdict": "PASS", "pipeline_version": 2})

    assert wrapped is not None and wrapped["doc_id"] == "dep-1"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


# ---------------------------------------------------------------------------
# Zone-4 Phase 3: registry_verdict_authority removed from Settings (contract)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Zone-4 Phase 3: upsert_doc returns dict not Record (contract)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Wiring: force_verdict_override threads through _upsert_registry_row
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Wiring: force_verdict_override import verification
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Regression: backward compat with verdict_fields=None
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Contract: pool-not-ready queues verdict retry
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Contract: registry_fields kwarg skips MinIO re-read (Zone-7)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Contract: verdict_fields overlay on top of registry_fields (Zone-7)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Contract: registry disabled -> early return, no upsert
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Regression: registry disabled logs degraded-consistency warning
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Contract: best-effort sidecar backfill with winning row
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Contract: save_doc_meta failure during sidecar backfill is swallowed
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Contract: upsert_doc exception triggers metric mirror + failure mirror
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Contract: no MinIO read when both fields=None and verdict_fields=None
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 2. CONTRACT: last_registry_fields stash in _persist_flat_result
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 3. EXHAUSTIVENESS: converters_cli verdict_fields surfacing
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 4. WIRING: worker/job.py extracts registry_fields and passes to
#    _upsert_registry_row
# ---------------------------------------------------------------------------


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
        docstring_block = src[fn_idx : fn_idx + 600]
        # Step numbers mentioned in the docstring
        for step in ["1.", "2.", "3.", "4.", "5.", "6.", "7."]:
            assert step in docstring_block, f"Step {step} not in delete_doc docstring"

    def test_cascade_handles_missing_doc_idempotently(self):
        """delete_doc must be idempotent: missing objects tolerated."""
        src = _read_src("storage/documents.py")
        fn_idx = src.index("async def delete_doc")
        fn_src = src[fn_idx : fn_idx + 3000]
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
        assert "uploads/" in self._get_delete_doc_src()

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


# ---------------------------------------------------------------------------
# Zone-5: Regression — upsert_doc exception enqueues verdict retry
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Zone-5: Wiring — REGISTRY_CONSISTENCY_DEGRADED metric fires on disabled/pool-None
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Zone-5: Contract — consistency_regime='postgres-authoritative' stamped on winning dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_upsert_stamps_postgres_authoritative_in_sidecar():
    """Contract: _upsert_registry_row takes ONE linear path -- read the MinIO
    registry fields, do ONE upsert_doc, then backfill the sidecar with the
    winning row stamped consistency_regime='postgres-authoritative'.  The
    backfill itself is best-effort and swallows a save_doc_meta failure."""
    winning = {"doc_id": "regime-1", "verdict": "PASS", "pipeline_version": 4}
    save_calls = []

    def _capture_save(doc_id, meta):
        save_calls.append((doc_id, dict(meta)))

    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", _MIRROR_REGISTRY_ENABLED),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch(
            "pageindex_mcp.registry.upsert_doc",
            AsyncMock(return_value=winning),
        ) as mock_upsert,
        patch(
            "pageindex_mcp.worker.registry_mirror.read_registry_fields",
            return_value={"doc_id": "regime-1", "sha256": "abc"},
        ) as mock_read,
        patch(
            "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
            AsyncMock(),
        ),
        patch("pageindex_mcp.storage.save_doc_meta", _capture_save),
    ):
        await _upsert_registry_row("regime-1", "flat_table")

    # The single linear Postgres-authoritative path: read MinIO, ONE upsert_doc.
    # Two reads, both for the same doc_id/content_class -- one to build the
    # upsert payload, one from the RFC-042 D3 CAS guard
    # (_cas_filter_sidecar_meta) before the sidecar backfill.
    assert mock_read.call_count == 2
    assert all(call.args == ("regime-1", "flat_table") for call in mock_read.call_args_list)
    mock_upsert.assert_awaited_once()
    assert mock_upsert.await_args[0][0]["sha256"] == "abc"

    assert len(save_calls) == 1
    doc_id, meta = save_calls[0]
    # Best-effort sidecar convergence: the winning row dict returned by
    # upsert_doc is what gets written back, under the same doc_id.
    assert doc_id == "regime-1"
    assert {k: meta[k] for k in winning} == winning
    assert meta["consistency_regime"] == "postgres-authoritative"

    # Zone-4 Phase 3 contract: the sidecar backfill is best-effort -- a
    # save_doc_meta failure is logged and swallowed, never raised, so a MinIO
    # hiccup cannot fail the caller's job.
    def _exploding_save(doc_id, meta):
        raise RuntimeError("MinIO unreachable during sidecar backfill")

    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", _MIRROR_REGISTRY_ENABLED),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch("pageindex_mcp.registry.upsert_doc", AsyncMock(return_value=winning)),
        patch(
            "pageindex_mcp.worker.registry_mirror.read_registry_fields",
            return_value={"doc_id": "regime-1"},
        ),
        patch(
            "pageindex_mcp.worker.registry_mirror._mirror_registry_write_failure_to_redis",
            AsyncMock(),
        ),
        patch("pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis", AsyncMock()),
        patch("pageindex_mcp.storage.save_doc_meta", _exploding_save),
    ):
        await _upsert_registry_row("regime-1", None)  # must NOT raise


# ---------------------------------------------------------------------------
# Zone-5: Contract — pool not ready stamps 'sidecar-only' in sidecar
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Zone-5: Contract — delete_doc SET LOCAL statement_timeout precedes DELETE
# ---------------------------------------------------------------------------


async def test_delete_doc_statement_timeout_precedes_delete_with_correct_value():
    """Contract: delete_doc in queries.py must execute SET LOCAL
    statement_timeout inside a transaction block BEFORE the DELETE, using
    the correct timeout value derived from settings.registry_delete_timeout_s."""
    import dataclasses

    from pageindex_mcp.config import settings as base_settings

    timeout_s = 3.0
    patched_settings = dataclasses.replace(base_settings, registry_delete_timeout_s=timeout_s)
    expected_ms = int(timeout_s * 1000)

    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="DELETE 1")
    conn.transaction = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    pool = _mock_pool()
    pool.acquire = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        )
    )

    with (
        patch("pageindex_mcp.registry.schema.get_pool", return_value=pool),
        patch("pageindex_mcp.config.settings", patched_settings),
    ):
        await registry.delete_doc("timeout-doc")

    # Two execute calls: SET LOCAL then DELETE
    assert conn.execute.await_count == 2
    set_call = conn.execute.await_args_list[0]
    delete_call = conn.execute.await_args_list[1]

    # SET LOCAL must come first and contain the correct timeout
    assert "SET LOCAL statement_timeout" in set_call.args[0]
    assert str(expected_ms) in set_call.args[0]

    # DELETE must be second with client-side timeout= kwarg
    assert "DELETE" in delete_call.args[0]
    assert delete_call.kwargs["timeout"] == timeout_s


# ---------------------------------------------------------------------------
# Zone-5: Wiring — REGISTRY_CONSISTENCY_DEGRADED across metrics modules
# ---------------------------------------------------------------------------


def test_registry_consistency_degraded_metric_wiring():
    """Wiring: REGISTRY_CONSISTENCY_DEGRADED is defined in metrics.definitions,
    re-exported (identically) from metrics/__init__, and registered in
    metrics.sync._BRIDGED_METRICS under 'registry_consistency_degraded'."""
    from pageindex_mcp.metrics import REGISTRY_CONSISTENCY_DEGRADED as reexported
    from pageindex_mcp.metrics.definitions import REGISTRY_CONSISTENCY_DEGRADED as gauge
    from pageindex_mcp.metrics.sync import _BRIDGED_METRICS

    assert gauge is not None
    assert gauge._name == "pageindex_registry_consistency_degraded_total"
    assert reexported is gauge, "metrics/__init__ must re-export the same object"
    assert "registry_consistency_degraded" in _BRIDGED_METRICS
    assert _BRIDGED_METRICS["registry_consistency_degraded"] is gauge


# ---------------------------------------------------------------------------
# Consolidated (test-budget reduction): table-driven replacements.
# Each test below loops over the rows the former per-row tests covered,
# collects EVERY mismatch, and asserts once naming the offending rows.
# ---------------------------------------------------------------------------


async def test_registry_reads_degrade_to_none():
    """Every read coroutine degrades to None instead of raising: pool absent
    (RFC-006 fallback guards), Postgres error, and Redis error."""
    failures = []

    with patch("pageindex_mcp.registry.schema.get_pool", return_value=None):
        for name, coro in (
            ("list_docs", registry.list_docs()),
            ("count_docs", registry.count_docs()),
            ("stage_b_candidates", registry.stage_b_candidates("anything", 10)),
            ("stage_a_filter", registry.stage_a_filter("anything")),
        ):
            if await coro is not None:
                failures.append(f"{name}(no pool) did not return None")

    # count_docs swallows a Postgres-side error and still degrades to None.
    pool = _mock_pool()
    pool.fetchval.side_effect = RuntimeError("connection reset")
    with patch("pageindex_mcp.registry.schema.get_pool", return_value=pool):
        if await registry.count_docs() is not None:
            failures.append("count_docs(fetchval raises) did not return None")

    # is_registry_complete swallows a Redis error and degrades to False.
    r = AsyncMock()
    r.get.side_effect = ConnectionError("redis down")
    if await registry.is_registry_complete(r) is not False:
        failures.append("is_registry_complete(redis down) did not return False")

    assert not failures, failures


async def test_stage_a_facet_resolution_and_stage_b_fallback():
    """Stage A resolves facets by exact case-folded token only: unpopulated
    facets pass through (None), a substring hit never matches, and
    refresh_known_facets casefolds values while dropping unknown columns.
    Stage B then degrades a no-match ts_rank query to a recency fallback."""
    failures = []
    pool = _mock_pool()

    # 1. Facet sets empty -> transparent pass-through, no SQL issued.
    with patch("pageindex_mcp.registry.schema.get_pool", return_value=pool):
        if await registry.stage_a_filter("huk coburg policy") is not None:
            failures.append("unpopulated facets: expected None pass-through")
    if pool.fetch.await_count:
        failures.append("unpopulated facets: fetch was issued")

    # 2. 'huk' inside 'hukcoburg' is not a standalone token -> no match.
    registry.refresh_known_facets({"product": {"huk"}})
    with patch("pageindex_mcp.registry.schema.get_pool", return_value=pool):
        if await registry.stage_a_filter("hukcoburg terms") is not None:
            failures.append("substring 'hukcoburg': expected None (no facet match)")
    if pool.fetch.await_count:
        failures.append("substring match: fetch was issued")

    # 3. refresh_known_facets casefolds and ignores unknown columns.
    registry.refresh_known_facets({"product": {"HUK", "Allianz"}, "not_a_column": {"x"}})
    if registry._KNOWN_FACETS.get("product") != {"huk", "allianz"}:
        failures.append(f"casefold: got {registry._KNOWN_FACETS.get('product')!r}")
    if "not_a_column" in registry._KNOWN_FACETS:
        failures.append("unknown column 'not_a_column' was retained")

    # 4. Stage B: a ts_rank query that matches nothing falls back to a second
    # recency query rather than returning an empty candidate set.
    recent = [
        {
            "doc_id": "r1",
            "doc_name": "recent.pdf",
            "source_url": "",
            "processed_at": "2026-07-10",
            "content_class": "",
        },
    ]
    pool_b = _mock_pool()
    pool_b.fetch.side_effect = [[], recent]
    with patch("pageindex_mcp.registry.schema.get_pool", return_value=pool_b):
        rows = await registry.stage_b_candidates("zzzznomatch", 200)
    if rows is None or [r["doc_id"] for r in rows] != ["r1"]:
        failures.append(f"stage B recency fallback returned {rows!r}")
    if pool_b.fetch.await_count != 2:
        failures.append(f"stage B issued {pool_b.fetch.await_count} queries, expected 2")
    elif pool_b.fetch.await_args_list[1].args != (registry._STAGE_B_FALLBACK_SQL, 200):
        failures.append(f"stage B fallback SQL/args = {pool_b.fetch.await_args_list[1].args!r}")

    assert not failures, failures


def test_registry_sql_contract():
    """RFC-014 D2 / RFC-037 D1 / Zone-4: the registry DDL and upsert SQL must
    carry the idempotent-migration, RETURNING and CAS-guard clauses."""
    from pageindex_mcp.registry.queries import _UPSERT_SQL

    migrate = registry._MIGRATE_VERDICT_SQL
    returning = _UPSERT_SQL.split("RETURNING")[1] if "RETURNING" in _UPSERT_SQL else ""

    checks = [
        # (row name, haystack, needle)
        ("migrate: verdict IF NOT EXISTS", migrate, "ADD COLUMN IF NOT EXISTS verdict"),
        (
            "migrate: pipeline_version IF NOT EXISTS",
            migrate,
            "ADD COLUMN IF NOT EXISTS pipeline_version",
        ),
        (
            "migrate: permanent_marginal IF NOT EXISTS",
            migrate,
            "ADD COLUMN IF NOT EXISTS permanent_marginal",
        ),
        ("upsert: has RETURNING", _UPSERT_SQL, "RETURNING"),
        ("upsert RETURNING: doc_id", returning, "doc_id"),
        ("upsert RETURNING: verdict", returning, "verdict"),
        ("upsert RETURNING: pipeline_version", returning, "pipeline_version"),
        ("upsert RETURNING: permanent_marginal", returning, "permanent_marginal"),
        ("upsert RETURNING: verdict_computed_at", returning, "verdict_computed_at"),
        ("verdict CAS: EXCLUDED PASS priority", _UPSERT_SQL, "EXCLUDED.verdict = 'PASS' THEN 3"),
        (
            "verdict CAS: incumbent PASS priority",
            _UPSERT_SQL,
            "doc_registry.verdict = 'PASS' THEN 3",
        ),
        (
            "processed_at CAS guard",
            _UPSERT_SQL,
            "EXCLUDED.processed_at >= COALESCE(doc_registry.processed_at",
        ),
    ]
    missing = [name for name, hay, needle in checks if needle not in hay]
    assert not missing, f"missing SQL clauses: {missing}"


def test_settings_carry_no_verdict_authority_mode_flag():
    """Zone-4 Phase 3 contract: Postgres is unconditionally the sole verdict
    authority — neither the Settings field nor its env-var loading path may
    exist.  Also pins the two config knobs that DO have to exist."""
    import pageindex_mcp.config as config_mod
    from pageindex_mcp.config import PipelineConfig, Settings
    from pageindex_mcp.registry.queries import upsert_doc

    failures = []

    if "registry_verdict_authority" in {f.name for f in dataclasses.fields(Settings)}:
        failures.append("registry_verdict_authority must be removed from Settings")

    executable_lines = [
        line
        for line in inspect.getsource(config_mod).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    for line in executable_lines:
        if "REGISTRY_VERDICT_AUTHORITY" in line:
            failures.append(f"REGISTRY_VERDICT_AUTHORITY in executable line: {line.strip()}")

    sig = inspect.signature(upsert_doc)
    if "force_verdict_override" not in sig.parameters:
        failures.append("upsert_doc lacks force_verdict_override parameter")
    elif sig.parameters["force_verdict_override"].default is not False:
        failures.append("force_verdict_override default is not False")

    if "verdict_downgrade_enabled" not in {f.name for f in dataclasses.fields(PipelineConfig)}:
        failures.append("PipelineConfig lacks verdict_downgrade_enabled")

    assert not failures, failures


def _mirror_patches(
    *,
    settings_obj=None,
    pool=object(),
    upsert_mock=None,
    read_mock=None,
    save_mock=None,
    extra=(),
):
    """The standard _upsert_registry_row harness, as a reusable patch stack."""
    stack = [
        patch(
            "pageindex_mcp.worker.registry_mirror.settings",
            settings_obj if settings_obj is not None else _MIRROR_REGISTRY_ENABLED,
        ),
        patch("pageindex_mcp.registry.get_pool", return_value=pool),
        patch("pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis", AsyncMock()),
    ]
    if upsert_mock is not None:
        stack.append(patch("pageindex_mcp.registry.upsert_doc", upsert_mock))
    if read_mock is not None:
        stack.append(patch("pageindex_mcp.worker.registry_mirror.read_registry_fields", read_mock))
    if save_mock is not None:
        stack.append(patch("pageindex_mcp.storage.save_doc_meta", save_mock))
    stack.extend(extra)
    return stack


async def _run_mirror(doc_id, content_class, *, minio_fields, upsert_return=None, **kwargs):
    """Drive _upsert_registry_row once; return (upserted_dict, read_mock)."""
    import contextlib

    mock_upsert = AsyncMock(return_value=upsert_return)
    mock_read = MagicMock(return_value=minio_fields)
    with contextlib.ExitStack() as es:
        for p in _mirror_patches(upsert_mock=mock_upsert, read_mock=mock_read):
            es.enter_context(p)
        await _upsert_registry_row(doc_id, content_class, **kwargs)
    upserted = mock_upsert.await_args[0][0] if mock_upsert.await_args else None
    return upserted, mock_read


@pytest.mark.asyncio
async def test_upsert_registry_row_field_source_matrix():
    """Zone-7 / RFC-014: the payload _upsert_registry_row sends to upsert_doc is
    assembled from (MinIO read | registry_fields) with verdict_fields overlaid
    on top, and registry_fields suppresses the MinIO re-read entirely.

    Replaces the former one-test-per-combination suite; every row is checked
    and every mismatch is reported by row name.
    """
    base_rf = {
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
    vf = {"verdict": "PASS", "pipeline_version": 5, "verdict_computed_at": "2026-08-26T01:00:00Z"}

    failures = []

    # Row 1: no registry_fields, no verdict_fields -> MinIO read is the source.
    upserted, read = await _run_mirror(
        "compat-1",
        None,
        minio_fields={"doc_id": "compat-1", "doc_name": "old.pdf", "sha256": "def"},
    )
    if read.call_args_list != [(("compat-1", None),)]:
        failures.append(
            f"minio-only: expected one read('compat-1', None), got {read.call_args_list}"
        )
    if upserted.get("sha256") != "def" or upserted.get("doc_id") != "compat-1":
        failures.append(f"minio-only: bad payload {upserted!r}")
    if "verdict" in upserted:
        failures.append("minio-only: verdict leaked into payload")

    # Row 2: verdict_fields only -> overlay wins over stale MinIO artifact data.
    upserted, _ = await _run_mirror(
        "vf-1",
        None,
        minio_fields={"doc_id": "vf-1", "doc_name": "test.pdf", "verdict": "MARGINAL"},
        verdict_fields=dict(vf),
    )
    for key, want in vf.items():
        if upserted.get(key) != want:
            failures.append(f"verdict-overlay: {key}={upserted.get(key)!r}, want {want!r}")

    # Row 3: registry_fields supplied -> no MinIO round-trip at all.
    upserted, read = await _run_mirror(
        "rf-1", None, minio_fields={"doc_id": "SHOULD-NOT-BE-CALLED"}, registry_fields=dict(base_rf)
    )
    if read.called:
        failures.append("registry_fields: read_registry_fields was called (MinIO re-read)")
    for key, want in (
        ("doc_id", "rf-1"),
        ("sha256", "abc123"),
        ("doc_name", "test.pdf"),
        ("node_count", 5),
    ):
        if upserted.get(key) != want:
            failures.append(f"registry_fields: {key}={upserted.get(key)!r}, want {want!r}")

    # Row 4: both supplied -> verdict_fields overlay beats registry_fields.
    upserted, read = await _run_mirror(
        "overlay-1",
        None,
        minio_fields={"should": "not-be-called"},
        registry_fields=dict(base_rf),
        verdict_fields={**vf, "node_count": 10},
    )
    if read.called:
        failures.append("both: read_registry_fields was called")
    for key, want in (
        ("verdict", "PASS"),
        ("pipeline_version", 5),
        ("node_count", 10),
        ("sha256", "abc123"),
    ):
        if upserted.get(key) != want:
            failures.append(f"both: {key}={upserted.get(key)!r}, want {want!r}")

    # Row 5: content_class arg is backfilled into a registry_fields dict that
    # lacks it -- and the caller's dict is copied, never mutated in place.
    caller_dict = {"doc_name": "test.pdf", "sha256": "abc"}
    original_keys = set(caller_dict)
    upserted, _ = await _run_mirror(
        "doc-cc", "flat_table", minio_fields=None, registry_fields=caller_dict
    )
    if upserted.get("content_class") != "flat_table":
        failures.append(f"content_class backfill: got {upserted.get('content_class')!r}")
    if set(caller_dict) != original_keys:
        failures.append(f"caller's registry_fields was mutated: {set(caller_dict) - original_keys}")

    # Row 6: nothing to write (MinIO read returns None, no verdict_fields) ->
    # no upsert is attempted at all.
    upserted, _ = await _run_mirror("empty-1", None, minio_fields=None)
    if upserted is not None:
        failures.append(f"no-source: upsert_doc was awaited with {upserted!r}")

    assert not failures, failures


@pytest.mark.asyncio
async def test_force_verdict_override_wiring_matrix():
    """force_verdict_override is popped out of verdict_fields and forwarded as a
    kwarg to upsert_doc (never persisted as a column), defaulting to False."""
    import contextlib

    failures = []
    for row, verdict_fields, expected in (
        ("explicit True", {"verdict": "FAIL", "force_verdict_override": True}, True),
        ("absent", {"verdict": "PASS"}, False),
    ):
        mock_upsert = AsyncMock(return_value=None)
        with contextlib.ExitStack() as es:
            for p in _mirror_patches(
                pool=MagicMock(),
                upsert_mock=mock_upsert,
                read_mock=MagicMock(return_value={"doc_id": "w1", "content_class": "flat_prose"}),
                save_mock=MagicMock(),
            ):
                es.enter_context(p)
            await _upsert_registry_row("w1", "flat_prose", verdict_fields=dict(verdict_fields))

        kwargs = mock_upsert.await_args.kwargs
        if kwargs.get("force_verdict_override") is not expected:
            failures.append(
                f"{row}: kwarg={kwargs.get('force_verdict_override')!r}, want {expected!r}"
            )
        if "force_verdict_override" in mock_upsert.await_args.args[0]:
            failures.append(f"{row}: force_verdict_override persisted into the meta dict")

    assert not failures, failures


@pytest.mark.asyncio
async def test_upsert_registry_row_degraded_paths(caplog):
    """Both degraded modes (registry disabled, pool not ready) must: skip the
    Postgres upsert, log 'degraded consistency' naming the doc, increment
    REGISTRY_CONSISTENCY_DEGRADED, mirror the bridged counter, and stamp the
    sidecar consistency_regime as 'sidecar-only'."""
    import contextlib

    disabled = _mirror_settings(registry_enabled=False, postgres_dsn="")
    failures = []

    for row, settings_obj, pool in (
        ("registry disabled", disabled, object()),
        ("pool not ready", _MIRROR_REGISTRY_ENABLED, None),
    ):
        mock_upsert = AsyncMock()
        mock_gauge = MagicMock()
        mock_bridged = AsyncMock()
        saves = []
        caplog.clear()
        with contextlib.ExitStack() as es:
            for p in _mirror_patches(
                settings_obj=settings_obj,
                pool=pool,
                upsert_mock=mock_upsert,
                read_mock=MagicMock(return_value=None),
                save_mock=lambda doc_id, meta: saves.append((doc_id, dict(meta))),
                extra=[
                    patch(
                        "pageindex_mcp.worker.registry_mirror.REGISTRY_CONSISTENCY_DEGRADED",
                        mock_gauge,
                    ),
                    patch(
                        "pageindex_mcp.worker.registry_mirror._mirror_bridged_incr", mock_bridged
                    ),
                    patch(
                        "pageindex_mcp.worker.registry_mirror._enqueue_verdict_retry", AsyncMock()
                    ),
                    caplog.at_level(logging.INFO, logger="pageindex_mcp.worker.registry_mirror"),
                ],
            ):
                es.enter_context(p)
            await _upsert_registry_row("doc-degraded", None, verdict_fields={"verdict": "PASS"})

        if mock_upsert.await_count:
            failures.append(f"{row}: upsert_doc was awaited despite degradation")
        degraded = [r.message for r in caplog.records if "degraded consistency" in r.message]
        if not degraded:
            failures.append(
                f"{row}: no 'degraded consistency' log; got {[r.message for r in caplog.records]}"
            )
        elif "doc-degraded" not in degraded[0]:
            failures.append(f"{row}: degraded log omits the doc_id: {degraded[0]!r}")
        if mock_gauge.inc.call_count != 1:
            failures.append(
                f"{row}: REGISTRY_CONSISTENCY_DEGRADED.inc called {mock_gauge.inc.call_count}x"
            )
        if mock_bridged.await_args_list != [(("registry_consistency_degraded",),)]:
            failures.append(f"{row}: bridged incr calls = {mock_bridged.await_args_list}")
        if len(saves) != 1 or saves[0][1].get("consistency_regime") != "sidecar-only":
            failures.append(f"{row}: sidecar regime stamp = {saves!r}")

    # The degraded sidecar stamp is itself best-effort: a save_doc_meta blow-up
    # (e.g. MinIO unreachable while Postgres is already down) must be swallowed,
    # never raised to the caller.
    def _exploding_save(doc_id, meta):
        raise RuntimeError("MinIO unreachable during degraded sidecar stamp")

    with contextlib.ExitStack() as es:
        for p in _mirror_patches(
            settings_obj=disabled,
            read_mock=MagicMock(return_value=None),
            save_mock=_exploding_save,
            extra=[
                patch(
                    "pageindex_mcp.worker.registry_mirror.REGISTRY_CONSISTENCY_DEGRADED",
                    MagicMock(),
                ),
                patch("pageindex_mcp.worker.registry_mirror._mirror_bridged_incr", AsyncMock()),
            ],
        ):
            es.enter_context(p)
        # Must NOT raise.
        await _upsert_registry_row("doc-degraded", None, verdict_fields={"verdict": "PASS"})

    assert not failures, failures


@pytest.mark.asyncio
async def test_upsert_registry_row_pool_not_ready_enqueue_matrix():
    """Pool not ready: verdict_fields are preserved for later replay via
    _enqueue_verdict_retry; with nothing to retry (batch CLI path) no key is
    written."""
    import contextlib

    failures = []
    for row, verdict_fields, expected_calls in (
        ("verdict present", {"verdict": "PASS"}, [(("doc-retry", {"verdict": "PASS"}),)]),
        ("verdict absent", None, []),
    ):
        mock_enqueue = AsyncMock()
        with contextlib.ExitStack() as es:
            for p in _mirror_patches(
                pool=None,
                read_mock=MagicMock(return_value=None),
                save_mock=MagicMock(),
                extra=[
                    patch(
                        "pageindex_mcp.worker.registry_mirror._enqueue_verdict_retry", mock_enqueue
                    )
                ],
            ):
                es.enter_context(p)
            await _upsert_registry_row("doc-retry", None, verdict_fields=verdict_fields)
        if mock_enqueue.await_args_list != expected_calls:
            failures.append(f"{row}: enqueue calls = {mock_enqueue.await_args_list}")

    assert not failures, failures


@pytest.mark.asyncio
async def test_upsert_failure_mirrors_to_redis_and_enqueues_verdict_retry():
    """Zone-5 regression: when upsert_doc raises, _upsert_registry_row must not
    propagate, must mirror the write failure to Redis, and must enqueue the
    verdict for replay — but only when there IS a verdict to replay."""
    import contextlib

    failures = []
    for row, verdict_fields, expected_calls in (
        (
            "verdict present",
            {"verdict": "PASS", "pipeline_version": 7},
            [(("retry-1", {"verdict": "PASS", "pipeline_version": 7}),)],
        ),
        ("verdict absent", None, []),
    ):
        mock_enqueue = AsyncMock()
        mock_fail_mirror = AsyncMock()
        with contextlib.ExitStack() as es:
            for p in _mirror_patches(
                upsert_mock=AsyncMock(side_effect=RuntimeError("connection refused")),
                read_mock=MagicMock(return_value={"doc_id": "retry-1"}),
                extra=[
                    patch(
                        "pageindex_mcp.worker.registry_mirror._mirror_registry_write_failure_to_redis",
                        mock_fail_mirror,
                    ),
                    patch(
                        "pageindex_mcp.worker.registry_mirror._enqueue_verdict_retry", mock_enqueue
                    ),
                ],
            ):
                es.enter_context(p)
            # Must NOT raise.
            await _upsert_registry_row("retry-1", None, verdict_fields=verdict_fields)

        if mock_fail_mirror.await_count != 1:
            failures.append(f"{row}: write-failure mirror awaited {mock_fail_mirror.await_count}x")
        if mock_enqueue.await_args_list != expected_calls:
            failures.append(f"{row}: enqueue calls = {mock_enqueue.await_args_list}")

    assert not failures, failures


def test_indexer_registry_fields_stash_contract():
    """Dual-write contract: _persist_tree_result and _persist_flat_result both
    stash last_registry_fields (plus last_verdict_fields on the tree path), the
    tree path computes node_count dynamically and the flat path pins it to 0."""
    src = _read_src("client/indexer.py")

    def _block(anchor: str) -> str:
        idx = src.index(anchor)
        nxt = src.find("\n    async def ", idx + 10)
        return src[idx : nxt if nxt != -1 else len(src)]

    common_keys = [
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
    ]
    tree_src = _block("def _persist_tree_result")
    flat_src = _block("def _persist_flat_result")

    failures = []
    for path_name, block, keys in (
        ("tree", tree_src, common_keys),
        ("flat", flat_src, [*common_keys, "content_class"]),
    ):
        if "last_registry_fields" not in block:
            failures.append(f"{path_name}: last_registry_fields not stashed")
            continue
        for key in keys:
            if f'"{key}"' not in block:
                failures.append(f"{path_name}: missing registry key {key!r}")

    if "last_verdict_fields" not in tree_src:
        failures.append("tree: last_verdict_fields not stashed")
    tree_stash = tree_src[tree_src.index("last_registry_fields") :][:600]
    if "_tree_node_count" not in tree_stash:
        failures.append("tree: node_count is not computed via _tree_node_count")
    flat_stash = flat_src[flat_src.index("last_registry_fields") :][:600]
    if '"node_count": 0' not in flat_stash:
        failures.append("flat: node_count is not hardcoded to 0")

    assert not failures, failures


def test_dual_write_wiring_source_contract():
    """Wiring exhaustiveness: converters_cli surfaces the child's verdict /
    content-class stashes via getattr, and worker/job.py extracts verdict_fields
    from the child result and forwards it to _upsert_registry_row, which accepts
    a registry_fields kwarg."""
    cli_src = _read_src("converters_cli.py")
    job_src = _read_src("worker/job.py")
    mirror_src = _read_src("worker/registry_mirror.py")
    mirror_sig = mirror_src[mirror_src.index("async def _upsert_registry_row") :][:300]

    checks = [
        (
            "converters_cli: verdict_fields getattr",
            cli_src,
            'getattr(client, "last_verdict_fields"',
        ),
        ("converters_cli: verdict_fields payload", cli_src, 'payload["verdict_fields"]'),
        ("converters_cli: content_class getattr", cli_src, 'getattr(client, "last_content_class"'),
        ("job.py: extracts verdict_fields", job_src, 'result.get("verdict_fields")'),
        ("job.py: forwards verdict_fields kwarg", job_src, "verdict_fields=verdict_fields"),
        (
            "job.py: imports _upsert_registry_row",
            job_src,
            "from .registry_mirror import _upsert_registry_row",
        ),
        ("registry_mirror: registry_fields kwarg", mirror_sig, "registry_fields"),
    ]
    missing = [name for name, hay, needle in checks if needle not in hay]
    assert not missing, f"missing wiring: {missing}"


@pytest.mark.asyncio
async def test_delete_stale_rows_guard_matrix():
    """_delete_stale_rows guards: rows younger than the grace period are
    age-protected, genuinely old orphans are deleted, a stale fraction above
    the 50% safety threshold refuses every deletion, and both "nothing stale"
    and "registry unreadable" are no-ops."""
    from pageindex_mcp.registry_backfill.cleanup import _delete_stale_rows

    now_iso = datetime.now(UTC).isoformat()
    old = "2020-01-01T00:00:00+00:00"
    recent = "2026-01-01T00:00:00+00:00"
    in_minio_9 = {f"minio-{i}": recent for i in range(9)}

    rows = [
        # (name, registry_rows, minio_ids, expected deleted doc_ids)
        ("fresh row age-protected", {"fresh-stale": now_iso, **in_minio_9}, set(in_minio_9), []),
        ("old orphan deleted", {"old-stale": old, **in_minio_9}, set(in_minio_9), ["old-stale"]),
        (
            "75% stale exceeds safety threshold",
            {"stale-1": old, "stale-2": old, "stale-3": old, "good-1": old},
            {"good-1"},
            [],
        ),
        ("no stale candidates", {"doc-1": recent}, {"doc-1"}, []),
        ("registry unreadable", None, set(), []),
    ]

    failures = []
    for name, registry_rows, minio_ids, expected in rows:
        mock_delete = AsyncMock()
        with (
            patch(
                "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
                AsyncMock(return_value=registry_rows),
            ),
            patch("pageindex_mcp.registry.delete_doc", mock_delete),
        ):
            await _delete_stale_rows(minio_ids, grace_minutes=10)
        deleted = [c.args[0] for c in mock_delete.await_args_list]
        if deleted != expected:
            failures.append(f"{name}: deleted {deleted!r}, expected {expected!r}")

    assert not failures, failures


# ---------------------------------------------------------------------------
# --- from test_rfc_registry.py (RFC-026 design properties) ---
# ---------------------------------------------------------------------------


def _rfc026_structure_with_chars(n_chars):
    """3 non-empty text parts joined by 2 newlines -> flat_text_len = n_chars."""
    text_budget = n_chars - 2
    dominant = max(text_budget - 2, 1)
    return [
        {"node_id": "1", "title": "", "text": "x" * dominant, "nodes": []},
        {"node_id": "2", "title": "", "text": "x", "nodes": []},
        {"node_id": "3", "title": "", "text": "x", "nodes": []},
    ]


def test_classify_verdict_zero_content_fail_floor():
    """RFC-026 D0 (Design Property 1): node_count == 0 or total_chars == 0 is a
    hard ("FAIL", "zero_content") floor that fires BEFORE the
    image_enrichment_promoted branch, whatever content_class /
    image_enrichment_ratio say.  A document with real content never trips it."""
    from pageindex_mcp.helpers import classify_verdict

    empty_nodes = [
        {
            "node_id": "1",
            "title": "",
            "text": "",
            "nodes": [{"node_id": "2", "title": "", "text": "", "nodes": []}],
        },
    ]
    real_content = [
        {"node_id": "n1", "title": "Section A", "text": "y" * 50, "nodes": []},
        {"node_id": "n2", "title": "Section B", "text": "z" * 50, "nodes": []},
        {"node_id": "n3", "title": "Section C", "text": "w" * 50, "nodes": []},
    ]

    failures = []
    for name, structure, content_class, ratio, expected in (
        ("node_count == 0", [], "flat_prose", 0.9, ("FAIL", "zero_content")),
        ("total_chars == 0", empty_nodes, "flat_prose", 0.9, ("FAIL", "zero_content")),
        ("beats image-enrichment branch", [], "flat_prose", 1.0, ("FAIL", "zero_content")),
    ):
        got = classify_verdict(structure, content_class, None, image_enrichment_ratio=ratio)
        if got != expected:
            failures.append(f"{name}: got {got!r}, expected {expected!r}")

    _, reason = classify_verdict(real_content, "", None)
    if reason == "zero_content":
        failures.append("control: a document with real content reported zero_content")

    assert not failures, failures


def test_classify_verdict_image_enrichment_volume_floor(monkeypatch):
    """RFC-026 D1 (Design Property 2): on the image_enrichment_promoted branch
    the rescue fires only at total_chars >= MIN_IMAGE_PROMOTED_CHARS
    (boundary-inclusive).  Below the floor the doc falls through to the
    structural gates and FAILs.  The floor is env-overridable."""
    from pageindex_mcp.config import reset_pipeline_config
    from pageindex_mcp.helpers import classify_verdict

    rows = [
        # (name, env floor, n_chars, content_class, expected verdict, rescue fired?)
        ("default floor, one below", None, 499, "flat_prose", "FAIL", False),
        ("default floor, exactly at", None, 500, "flat_prose", "PASS", True),
        ("env floor 100, above", "100", 150, "flat_mixed", "PASS", True),
        ("env floor 100, below", "100", 50, "flat_mixed", "FAIL", False),
    ]

    failures = []
    try:
        for name, env_floor, n_chars, content_class, want_verdict, want_rescue in rows:
            if env_floor is None:
                monkeypatch.delenv("MIN_IMAGE_PROMOTED_CHARS", raising=False)
            else:
                monkeypatch.setenv("MIN_IMAGE_PROMOTED_CHARS", env_floor)
            reset_pipeline_config()

            verdict, reason = classify_verdict(
                _rfc026_structure_with_chars(n_chars),
                content_class,
                None,
                image_enrichment_ratio=0.85,
            )
            if verdict != want_verdict:
                failures.append(f"{name}: verdict {verdict!r}, expected {want_verdict!r}")
            rescued = reason == "image_enrichment_promoted"
            if rescued is not want_rescue:
                failures.append(f"{name}: reason {reason!r} (rescue fired={rescued})")
    finally:
        monkeypatch.delenv("MIN_IMAGE_PROMOTED_CHARS", raising=False)
        reset_pipeline_config()

    assert not failures, failures


def test_page_rotation_detection_and_transform(tmp_path, monkeypatch):
    """RFC-026 D2 (Design Property 3): an explicit non-zero /Rotate is
    authoritative; the aspect-ratio landscape heuristic is consulted only when
    /Rotate == 0.  _normalize_pdf_page_rotation then bakes the heuristic
    rotation into a corrected copy -- but only behind the enabled gate, and
    never for a page whose explicit /Rotate is already effective."""
    fitz = pytest.importorskip("fitz")

    from pageindex_mcp import converters
    from pageindex_mcp.converters import (
        _normalize_pdf_page_rotation,
        _page_rotation_correction_info,
    )

    def _make_pdf(name, width, height, rotate=0):
        doc = fitz.open()
        page = doc.new_page(width=width, height=height)
        if rotate:
            page.set_rotation(rotate)
        path = str(tmp_path / name)
        doc.save(path)
        doc.close()
        return path

    rows = [
        # (name, width, height, /Rotate, expected rotate, expected likely_landscape)
        ("explicit /Rotate=90", 600, 800, 90, 90, False),
        ("wide page, /Rotate=0 -> aspect heuristic", 800, 600, 0, 0, True),
        ("tall page, /Rotate=0 -> aspect heuristic", 600, 800, 0, 0, False),
        ("explicit /Rotate=180 beats aspect", 800, 600, 180, 180, False),
    ]

    failures = []
    for i, (name, w, h, rot, want_rot, want_landscape) in enumerate(rows):
        path = _make_pdf(f"rot{i}.pdf", width=w, height=h, rotate=rot)
        doc = fitz.open(path)
        try:
            result = _page_rotation_correction_info(doc[0])
        finally:
            doc.close()
        if result["rotate"] != want_rot:
            failures.append(f"{name}: rotate={result['rotate']}, expected {want_rot}")
        if result["likely_landscape"] is not want_landscape:
            failures.append(
                f"{name}: likely_landscape={result['likely_landscape']}, expected {want_landscape}"
            )

    # Transform layer: gate enabled + wide page with /Rotate=0 -> a corrected
    # copy with /Rotate=90 baked in.
    monkeypatch.setattr(converters.pictures, "_PAGE_ROTATION_DETECTION_ENABLED", True)
    src = _make_pdf("wide_no_rotate.pdf", width=800, height=600, rotate=0)
    out = _normalize_pdf_page_rotation(src)
    if out == src:
        failures.append("gate enabled: expected a rewritten copy, got the original path")
    else:
        fixed = fitz.open(out)
        try:
            if fixed[0].rotation != 90:
                failures.append(f"gate enabled: baked rotation {fixed[0].rotation}, expected 90")
        finally:
            fixed.close()
            os.unlink(out)

    # An explicit /Rotate=180 is already the effective rotation -> no rewrite.
    explicit = _make_pdf("disagree_transform.pdf", width=800, height=600, rotate=180)
    if _normalize_pdf_page_rotation(explicit) != explicit:
        failures.append("explicit /Rotate=180: page was rewritten to the aspect-implied rotation")

    # Gate disabled -> transform skipped entirely.
    monkeypatch.setattr(converters.pictures, "_PAGE_ROTATION_DETECTION_ENABLED", False)
    needs_fix = _make_pdf("needs_fix.pdf", width=800, height=600, rotate=0)
    if _normalize_pdf_page_rotation(needs_fix) != needs_fix:
        failures.append("gate disabled: transform still ran")

    assert not failures, failures


def test_validate_tree_garble_priority_over_structure():
    """RFC-026 D5 (Design Property 6): validate_tree() reports 'garbling'
    whenever the content is garbled, never letting a structural early-exit
    (node_count<3 / depth<2) or the per-node 'node_garbling' reason shadow it.
    A clean thin tree still reports its structural reason."""
    from pageindex_mcp.helpers import validate_tree

    garbled = " ".join(["xkjqz"] * 40)
    clean = "This is a perfectly ordinary section of legible English prose text here."

    rows = [
        (
            "garbled + node_count<3",
            [{"node_id": "1", "title": "Root", "text": garbled, "nodes": []}],
            "garbling",
        ),
        (
            "garbled + depth<2",
            [
                {"node_id": "1", "title": "S1", "text": garbled, "nodes": []},
                {"node_id": "2", "title": "S2", "text": garbled, "nodes": []},
                {"node_id": "3", "title": "S3", "text": garbled, "nodes": []},
            ],
            "garbling",
        ),
        (
            "bulk garbling beats per-node node_garbling",
            [
                {
                    "node_id": "1",
                    "title": "Root",
                    "text": garbled,
                    "nodes": [{"node_id": "1.1", "title": "Child", "text": garbled, "nodes": []}],
                },
                {"node_id": "2", "title": "S2", "text": garbled, "nodes": []},
                {"node_id": "3", "title": "S3", "text": garbled, "nodes": []},
            ],
            "garbling",
        ),
        (
            "control: clean thin tree keeps its structural reason",
            [{"node_id": "1", "title": "Root", "text": clean, "nodes": []}],
            "node_count<3",
        ),
    ]

    failures = []
    for name, structure, expected_reason in rows:
        ok, reason = validate_tree(structure)
        if ok is not False:
            failures.append(f"{name}: validate_tree returned ok={ok!r}, expected False")
        if reason != expected_reason:
            failures.append(f"{name}: reason {reason!r}, expected {expected_reason!r}")

    assert not failures, failures
