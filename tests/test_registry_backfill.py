"""Registry backfill: gather, incremental reconciliation, and backfill tests."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp import registry_backfill as rb
from pageindex_mcp.registry_backfill import backfill as _bf
from pageindex_mcp.registry_backfill import reconcile as _rc  # noqa: F401
from pageindex_mcp.registry_backfill.reconcile import _drain_verdict_retry_queue

# Captured at import time — BEFORE the reconcile_env fixture stubs it — so the
# deletion-detection test can restore the real _delete_stale_rows.
_REAL_DELETE_STALE = rb._delete_stale_rows


# --- from test_registry_backfill.py ---


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_redis(keys_and_values: dict[str, dict]) -> AsyncMock:
    """Build a fake async Redis client with SCAN + GET + DELETE support.

    ``keys_and_values`` maps key-strings to their JSON-decoded value dicts.
    """
    client = AsyncMock()

    # SCAN returns all keys on the first call, then cursor=0 to signal completion.
    encoded_keys = [k.encode() for k in keys_and_values]
    client.scan = AsyncMock(return_value=(0, encoded_keys))

    # GET returns the JSON-encoded value for the requested key.
    async def _get(key):
        key_str = key.decode() if isinstance(key, bytes) else key
        val = keys_and_values.get(key_str)
        if val is None:
            return None
        return json.dumps(val).encode()

    client.get = AsyncMock(side_effect=_get)
    client.delete = AsyncMock()
    return client


# ===========================================================================
# Wiring: _drain_verdict_retry_queue pops force_verdict_override
# ===========================================================================


class TestDrainVerdictRetryQueueWiring:
    """Wiring test: _drain_verdict_retry_queue pops force_verdict_override
    from the deserialized verdict_fields dict and passes it as a kwarg to
    upsert_doc, mirroring registry_mirror.py's treatment."""

    @pytest.mark.asyncio
    async def test_force_override_true_popped_and_passed(self):
        """When verdict_fields contains force_verdict_override=True, it is
        popped from the meta dict and forwarded as kwarg to upsert_doc."""
        redis = _make_redis({
            "pageindex:verdict_retry:doc-fvo": {
                "verdict": "FAIL",
                "pipeline_version": 5,
                "force_verdict_override": True,
            },
        })

        mock_upsert = AsyncMock(return_value={
            "doc_id": "doc-fvo",
            "verdict": "FAIL",
            "pipeline_version": 5,
            "permanent_marginal": False,
            "verdict_computed_at": "2026-08-25T00:00:00Z",
        })

        with (
            patch("pageindex_mcp.registry.queries.upsert_doc", mock_upsert),
            patch("pageindex_mcp.registry.upsert_doc", mock_upsert),
            patch("pageindex_mcp.storage.verdict.save_doc_meta"),
            patch("pageindex_mcp.storage.save_doc_meta"),
        ):
            await _drain_verdict_retry_queue(redis)

        mock_upsert.assert_awaited()
        assert mock_upsert.await_args is not None
        # force_verdict_override must be passed as kwarg, not in the meta dict
        call_kwargs = mock_upsert.await_args.kwargs
        assert call_kwargs["force_verdict_override"] is True

        meta_arg = mock_upsert.await_args.args[0]
        assert "force_verdict_override" not in meta_arg

    @pytest.mark.asyncio
    async def test_force_override_absent_defaults_to_false(self):
        """When verdict_fields lacks force_verdict_override, default is False."""
        redis = _make_redis({
            "pageindex:verdict_retry:doc-nofvo": {
                "verdict": "PASS",
                "pipeline_version": 4,
            },
        })

        mock_upsert = AsyncMock(return_value={
            "doc_id": "doc-nofvo",
            "verdict": "PASS",
            "pipeline_version": 4,
            "permanent_marginal": False,
            "verdict_computed_at": "2026-08-25T00:00:00Z",
        })

        with (
            patch("pageindex_mcp.registry.queries.upsert_doc", mock_upsert),
            patch("pageindex_mcp.registry.upsert_doc", mock_upsert),
            patch("pageindex_mcp.storage.verdict.save_doc_meta"),
            patch("pageindex_mcp.storage.save_doc_meta"),
        ):
            await _drain_verdict_retry_queue(redis)

        assert mock_upsert.await_args is not None
        call_kwargs = mock_upsert.await_args.kwargs
        assert call_kwargs["force_verdict_override"] is False

    @pytest.mark.asyncio
    async def test_meta_dict_contains_doc_id(self):
        """The meta dict passed to upsert_doc must contain doc_id extracted
        from the Redis key, plus the verdict_fields values."""
        redis = _make_redis({
            "pageindex:verdict_retry:doc-meta": {
                "verdict": "MARGINAL",
                "pipeline_version": 3,
            },
        })

        mock_upsert = AsyncMock(return_value={
            "doc_id": "doc-meta",
            "verdict": "MARGINAL",
            "pipeline_version": 3,
            "permanent_marginal": False,
            "verdict_computed_at": "",
        })

        with (
            patch("pageindex_mcp.registry.queries.upsert_doc", mock_upsert),
            patch("pageindex_mcp.registry.upsert_doc", mock_upsert),
            patch("pageindex_mcp.storage.verdict.save_doc_meta"),
            patch("pageindex_mcp.storage.save_doc_meta"),
        ):
            await _drain_verdict_retry_queue(redis)

        assert mock_upsert.await_args is not None
        meta_arg = mock_upsert.await_args.args[0]
        assert meta_arg["doc_id"] == "doc-meta"
        assert meta_arg["verdict"] == "MARGINAL"
        assert meta_arg["pipeline_version"] == 3

    @pytest.mark.asyncio
    async def test_key_deleted_after_successful_upsert(self):
        """After a successful upsert, the Redis retry key must be deleted."""
        redis = _make_redis({
            "pageindex:verdict_retry:doc-del": {
                "verdict": "PASS",
            },
        })

        mock_upsert = AsyncMock(return_value={
            "doc_id": "doc-del", "verdict": "PASS",
            "pipeline_version": 4, "permanent_marginal": False,
            "verdict_computed_at": "",
        })

        with (
            patch("pageindex_mcp.registry.queries.upsert_doc", mock_upsert),
            patch("pageindex_mcp.registry.upsert_doc", mock_upsert),
            patch("pageindex_mcp.storage.verdict.save_doc_meta"),
            patch("pageindex_mcp.storage.save_doc_meta"),
        ):
            await _drain_verdict_retry_queue(redis)

        # delete called for the key
        redis.delete.assert_awaited()

    @pytest.mark.asyncio
    async def test_sidecar_written_with_winning_values(self):
        """After upsert_doc returns winning values, save_doc_meta is called
        with doc_id and the winning dict."""
        redis = _make_redis({
            "pageindex:verdict_retry:doc-sc": {
                "verdict": "PASS",
                "force_verdict_override": True,
            },
        })

        winning = {
            "doc_id": "doc-sc", "verdict": "PASS",
            "pipeline_version": 5, "permanent_marginal": False,
            "verdict_computed_at": "2026-08-25T12:00:00Z",
        }
        mock_upsert = AsyncMock(return_value=winning)
        mock_save = MagicMock()

        with (
            patch("pageindex_mcp.registry.queries.upsert_doc", mock_upsert),
            patch("pageindex_mcp.registry.upsert_doc", mock_upsert),
            patch("pageindex_mcp.storage.verdict.save_doc_meta", mock_save),
            patch("pageindex_mcp.storage.save_doc_meta", mock_save),
        ):
            await _drain_verdict_retry_queue(redis)

        # save_doc_meta is called via asyncio.to_thread
        mock_save.assert_called_once_with("doc-sc", winning)


# ===========================================================================
# Contract: _delete_stale_rows protects rows with empty/missing processed_at
# ===========================================================================


class TestDeleteStaleRowsEmptyProcessedAtProtection:
    """Zone-7: _delete_stale_rows must protect rows with empty/missing
    processed_at via age guard when cleanup_protect_empty_processed_at is True
    (default). When False, old behavior (treat as stale) is preserved."""

    @pytest.mark.asyncio
    async def test_empty_processed_at_protected_by_default(self):
        """When cleanup_protect_empty_processed_at is True (default), rows with
        empty processed_at are excluded from stale deletion."""
        from pageindex_mcp.config import settings as _base_settings
        from pageindex_mcp.registry_backfill.cleanup import _delete_stale_rows

        protected_settings = dataclasses.replace(
            _base_settings, cleanup_protect_empty_processed_at=True
        )

        # Registry has one row with empty processed_at that's not in MinIO
        registry_rows = {"stale-empty-1": ""}

        mock_delete = AsyncMock()
        mock_list = AsyncMock(return_value=registry_rows)

        with (
            patch("pageindex_mcp.config.settings", protected_settings),
            patch("pageindex_mcp.registry.list_all_doc_ids_with_timestamps", mock_list),
            patch("pageindex_mcp.registry.delete_doc", mock_delete),
        ):
            await _delete_stale_rows(set())  # empty MinIO set -> row is "stale"

        # Row should be PROTECTED (not deleted) because processed_at is empty
        mock_delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_processed_at_protected_by_default(self):
        """When cleanup_protect_empty_processed_at is True, rows with None
        processed_at are also protected."""
        from pageindex_mcp.config import settings as _base_settings
        from pageindex_mcp.registry_backfill.cleanup import _delete_stale_rows

        protected_settings = dataclasses.replace(
            _base_settings, cleanup_protect_empty_processed_at=True
        )

        registry_rows = {"stale-none-1": None}

        mock_delete = AsyncMock()
        mock_list = AsyncMock(return_value=registry_rows)

        with (
            patch("pageindex_mcp.config.settings", protected_settings),
            patch("pageindex_mcp.registry.list_all_doc_ids_with_timestamps", mock_list),
            patch("pageindex_mcp.registry.delete_doc", mock_delete),
        ):
            await _delete_stale_rows(set())

        mock_delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_processed_at_deleted_when_protection_disabled(self):
        """When cleanup_protect_empty_processed_at is False, rows with empty
        processed_at are treated as stale candidates (old behavior)."""
        from pageindex_mcp.config import settings as _base_settings
        from pageindex_mcp.registry_backfill.cleanup import _delete_stale_rows

        unprotected_settings = dataclasses.replace(
            _base_settings, cleanup_protect_empty_processed_at=False
        )

        # Need >2 rows so the stale fraction stays under the 50% safety cap.
        # 1 stale + 2 in-MinIO = 33% < 50%.
        registry_rows = {
            "stale-old-1": "",
            "in-minio-1": "2026-01-01T00:00:00+00:00",
            "in-minio-2": "2026-01-01T00:00:00+00:00",
        }

        mock_delete = AsyncMock()
        mock_list = AsyncMock(return_value=registry_rows)

        with (
            patch("pageindex_mcp.config.settings", unprotected_settings),
            patch("pageindex_mcp.registry.list_all_doc_ids_with_timestamps", mock_list),
            patch("pageindex_mcp.registry.delete_doc", mock_delete),
        ):
            await _delete_stale_rows({"in-minio-1", "in-minio-2"})

        # Row should be DELETED because protection is disabled
        mock_delete.assert_awaited_once_with("stale-old-1")

    @pytest.mark.asyncio
    async def test_normal_old_processed_at_still_deleted_when_protected(self):
        """Rows with a valid old processed_at are still treated as stale
        even when cleanup_protect_empty_processed_at is True."""
        from pageindex_mcp.config import settings as _base_settings
        from pageindex_mcp.registry_backfill.cleanup import _delete_stale_rows

        protected_settings = dataclasses.replace(
            _base_settings, cleanup_protect_empty_processed_at=True
        )

        # Old timestamp - well outside the grace period.
        # Need >2 rows so the stale fraction stays under 50% safety cap.
        registry_rows = {
            "stale-old-2": "2020-01-01T00:00:00+00:00",
            "in-minio-1": "2026-01-01T00:00:00+00:00",
            "in-minio-2": "2026-01-01T00:00:00+00:00",
        }

        mock_delete = AsyncMock()
        mock_list = AsyncMock(return_value=registry_rows)

        with (
            patch("pageindex_mcp.config.settings", protected_settings),
            patch("pageindex_mcp.registry.list_all_doc_ids_with_timestamps", mock_list),
            patch("pageindex_mcp.registry.delete_doc", mock_delete),
        ):
            await _delete_stale_rows({"in-minio-1", "in-minio-2"})

        # Old row with valid timestamp should be deleted
        mock_delete.assert_awaited_once_with("stale-old-2")


# --- from test_rfc012_backfill_gather.py ---


def _make_meta(key: str) -> dict:
    doc_id = key.removesuffix(".meta.json")
    # Fat v2 sidecar (sha256 + doc_description present) so _enrich_one's
    # _is_fat() fast path is taken and no full-JSON MinIO GET (via
    # read_registry_fields) is attempted — these tests mock upsert_doc /
    # _load_meta only, not the network calls behind the thin-sidecar
    # self-heal path.
    return {
        "doc_id": doc_id,
        "doc_name": f"test-{doc_id}",
        "sha256": "0" * 64,
        "doc_description": "test description",
    }


@pytest.mark.asyncio
@patch("pageindex_mcp.registry_backfill.backfill.upsert_doc", new_callable=AsyncMock)
@patch("pageindex_mcp.registry_backfill.backfill._load_meta", side_effect=lambda k: _make_meta(k))
async def test_backfill_upsert_called_per_item(mock_load, mock_upsert):
    from pageindex_mcp.registry_backfill import _upsert_all

    keys = [f"doc{i}.meta.json" for i in range(5)]
    failed = await _upsert_all(keys, dry_run=False)

    assert failed == []
    assert mock_upsert.call_count == 5
    assert mock_load.call_count == 5


@pytest.mark.asyncio
@patch("pageindex_mcp.registry_backfill.backfill._load_meta", side_effect=lambda k: _make_meta(k))
async def test_backfill_gather_handles_per_item_failure(mock_load):
    call_count = 0

    async def _upsert_side_effect(meta):
        nonlocal call_count
        call_count += 1
        if meta["doc_id"] == "doc2":
            raise RuntimeError("simulated upsert failure")

    with patch(
        "pageindex_mcp.registry_backfill.backfill.upsert_doc",
        new_callable=AsyncMock,
        side_effect=_upsert_side_effect,
    ):
        from pageindex_mcp.registry_backfill import _upsert_all

        keys = [f"doc{i}.meta.json" for i in range(5)]
        failed = await _upsert_all(keys, dry_run=False)

    assert call_count == 5
    assert len(failed) == 1
    assert "doc2.meta.json" in failed


@pytest.mark.asyncio
@patch("pageindex_mcp.registry_backfill.backfill._load_meta", side_effect=lambda k: _make_meta(k))
async def test_backfill_semaphore_bounds_concurrency(mock_load):
    max_concurrent = 0
    current = 0
    lock = asyncio.Lock()

    async def _upsert_tracking(meta):
        nonlocal max_concurrent, current
        async with lock:
            current += 1
            if current > max_concurrent:
                max_concurrent = current
        await asyncio.sleep(0.01)
        async with lock:
            current -= 1

    with patch(
        "pageindex_mcp.registry_backfill.backfill.upsert_doc",
        new_callable=AsyncMock,
        side_effect=_upsert_tracking,
    ):
        from pageindex_mcp.registry_backfill import _upsert_all

        keys = [f"doc{i}.meta.json" for i in range(20)]
        failed = await _upsert_all(keys, dry_run=False)

    assert failed == []
    assert max_concurrent <= 10, f"Expected max 10 concurrent, got {max_concurrent}"
    assert max_concurrent > 1, "Expected some concurrency, got serial execution"


# --- from test_reconcile_incremental.py ---


def _wire_settings(monkeypatch):
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
def reconcile_env(monkeypatch):
    """Wire the guard chain (settings, registry pool, async redis heartbeat) so a
    test can focus on the incremental-diff behavior. Returns the redis mock."""
    _wire_settings(monkeypatch)
    # Patch both the schema module (where queries.py looks it up) and the
    # package attribute (where reconcile.py's lazy import resolves it).
    _pool_stub = lambda: object()  # noqa: E731
    monkeypatch.setattr("pageindex_mcp.registry.schema.get_pool", _pool_stub)
    monkeypatch.setattr("pageindex_mcp.registry.get_pool", _pool_stub)
    redis_mock = MagicMock()
    redis_mock.set = AsyncMock()
    monkeypatch.setattr("pageindex_mcp.cache.get_async_redis", AsyncMock(return_value=redis_mock))
    # Neutralize the etag map + stale-delete side-effects unless a test overrides.
    # These are looked up via _pkg() in reconcile.py, so patching the package works.
    monkeypatch.setattr(rb, "reconcile_etag_set_many", MagicMock())
    monkeypatch.setattr(rb, "reconcile_etag_prune", MagicMock())
    monkeypatch.setattr(rb, "_delete_stale_rows", AsyncMock())
    return redis_mock


@pytest.mark.asyncio
async def test_reconcile_fat_sidecar_avoids_full_json_get(reconcile_env, monkeypatch):
    """Finding-9 proof: a fat sidecar (sha256 + doc_description present) is
    upserted WITHOUT a single read_registry_fields (full-JSON) GET."""
    fat = {"doc_id": "d1", "doc_name": "x", "sha256": "h1", "doc_description": "d"}
    # _list_meta_entries looked up via _pkg() in reconcile.py
    monkeypatch.setattr(
        rb, "_list_meta_entries", lambda: ([("processed/d1.meta.json", "e1", "d1")], {})
    )
    monkeypatch.setattr(rb, "reconcile_etag_get_all", MagicMock(return_value={}))
    # _load_meta is called inside _upsert_all (backfill.py) — patch the backfill module
    monkeypatch.setattr(_bf, "_load_meta", lambda k: dict(fat))
    read_rf = MagicMock()
    monkeypatch.setattr(_bf, "read_registry_fields", read_rf)
    upsert = AsyncMock()
    monkeypatch.setattr(_bf, "upsert_doc", upsert)

    await rb.reconcile_registry_drift()

    assert read_rf.call_count == 0  # the whole point of C-3
    upsert.assert_awaited_once()
    rb.reconcile_etag_set_many.assert_called_once_with({"d1": "e1"})


@pytest.mark.asyncio
async def test_reconcile_thin_sidecar_self_heals(reconcile_env, monkeypatch):
    """A thin sidecar triggers exactly one read_registry_fields GET and is then
    rewritten as a fat sidecar (save_doc_meta) so subsequent ticks are O(Δ)."""
    thin = {"doc_id": "d2", "doc_name": "x"}
    rich = {"doc_id": "d2", "doc_name": "x", "sha256": "h2", "doc_description": "dd"}
    monkeypatch.setattr(
        rb, "_list_meta_entries", lambda: ([("processed/d2.meta.json", "e2", "d2")], {})
    )
    monkeypatch.setattr(rb, "reconcile_etag_get_all", MagicMock(return_value={}))
    monkeypatch.setattr(_bf, "_load_meta", lambda k: dict(thin))
    read_rf = MagicMock(return_value=dict(rich))
    monkeypatch.setattr(_bf, "read_registry_fields", read_rf)
    save_meta = MagicMock()
    monkeypatch.setattr(_bf, "save_doc_meta", save_meta)
    monkeypatch.setattr(_bf, "upsert_doc", AsyncMock())

    await rb.reconcile_registry_drift()

    assert read_rf.call_count == 1
    save_meta.assert_called_once()
    healed = save_meta.call_args[0][1]
    assert healed["sha256"] == "h2"


@pytest.mark.asyncio
async def test_reconcile_no_sidecar_legacy_orphan_heal(reconcile_env, monkeypatch):
    """§2b: a doc with processed/<id>.json but NO .meta.json (orphan) is healed —
    read_registry_fields once + save_doc_meta writes a fresh fat sidecar."""
    rich = {"doc_id": "orph1", "doc_name": "x", "sha256": "ho", "doc_description": "d"}
    monkeypatch.setattr(rb, "_list_meta_entries", lambda: ([], {"orph1": None}))
    monkeypatch.setattr(rb, "reconcile_etag_get_all", MagicMock(return_value={}))
    read_rf = MagicMock(return_value=dict(rich))
    monkeypatch.setattr(_bf, "read_registry_fields", read_rf)
    save_meta = MagicMock()
    monkeypatch.setattr(_bf, "save_doc_meta", save_meta)
    upsert = AsyncMock()
    monkeypatch.setattr(_bf, "upsert_doc", upsert)

    await rb.reconcile_registry_drift()

    assert read_rf.call_count == 1
    save_meta.assert_called_once()
    assert save_meta.call_args[0][1]["sha256"] == "ho"
    upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_incremental_skips_unchanged(reconcile_env, monkeypatch):
    """Stored etag == listing etag → the doc is skipped entirely: no
    read_registry_fields, no upsert, no etag rewrite (O(Δ)=0)."""
    monkeypatch.setattr(
        rb, "_list_meta_entries", lambda: ([("processed/d3.meta.json", "e3", "d3")], {})
    )
    monkeypatch.setattr(rb, "reconcile_etag_get_all", MagicMock(return_value={"d3": "e3"}))
    read_rf = MagicMock()
    monkeypatch.setattr(_bf, "read_registry_fields", read_rf)
    upsert = AsyncMock()
    monkeypatch.setattr(_bf, "upsert_doc", upsert)
    monkeypatch.setattr(_bf, "_load_meta", lambda k: {"doc_id": "d3"})

    await rb.reconcile_registry_drift()

    assert read_rf.call_count == 0
    upsert.assert_not_awaited()
    rb.reconcile_etag_set_many.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_changed_etag_reprocessed(reconcile_env, monkeypatch):
    """Stored etag != listing etag (doc re-ingested) → upserted and the new etag
    is persisted."""
    fat = {"doc_id": "d4", "doc_name": "x", "sha256": "h", "doc_description": "d"}
    monkeypatch.setattr(
        rb, "_list_meta_entries", lambda: ([("processed/d4.meta.json", "NEW", "d4")], {})
    )
    monkeypatch.setattr(rb, "reconcile_etag_get_all", MagicMock(return_value={"d4": "OLD"}))
    monkeypatch.setattr(_bf, "_load_meta", lambda k: dict(fat))
    monkeypatch.setattr(_bf, "read_registry_fields", MagicMock())
    upsert = AsyncMock()
    monkeypatch.setattr(_bf, "upsert_doc", upsert)

    await rb.reconcile_registry_drift()

    upsert.assert_awaited_once()
    rb.reconcile_etag_set_many.assert_called_once_with({"d4": "NEW"})


@pytest.mark.asyncio
async def test_reconcile_stores_etag_only_after_successful_upsert(reconcile_env, monkeypatch):
    """A doc whose upsert fails must NOT have its etag stored (so it retries next
    tick); the succeeding doc's etag IS stored."""
    fat5 = {"doc_id": "d5", "doc_name": "x", "sha256": "h", "doc_description": "d"}
    fat6 = {"doc_id": "d6", "doc_name": "y", "sha256": "h", "doc_description": "d"}
    monkeypatch.setattr(
        rb,
        "_list_meta_entries",
        lambda: (
            [
                ("processed/d5.meta.json", "e5", "d5"),
                ("processed/d6.meta.json", "e6", "d6"),
            ],
            {},
        ),
    )
    monkeypatch.setattr(rb, "reconcile_etag_get_all", MagicMock(return_value={}))
    monkeypatch.setattr(_bf, "_load_meta", lambda k: dict(fat5) if "d5" in k else dict(fat6))
    monkeypatch.setattr(_bf, "read_registry_fields", MagicMock())

    async def _upsert(meta):
        if meta["doc_id"] == "d6":
            raise RuntimeError("simulated upsert failure")

    monkeypatch.setattr(_bf, "upsert_doc", AsyncMock(side_effect=_upsert))

    await rb.reconcile_registry_drift()

    rb.reconcile_etag_set_many.assert_called_once_with({"d5": "e5"})


@pytest.mark.asyncio
async def test_reconcile_deletion_detection(reconcile_env, monkeypatch):
    """A registry doc_id absent from the MinIO listing is deleted, and the full
    live doc-id set is passed to reconcile_etag_prune so its etag is pruned."""
    fat = {"doc_id": "d7", "doc_name": "x", "sha256": "h", "doc_description": "d"}
    monkeypatch.setattr(
        rb, "_list_meta_entries", lambda: ([("processed/d7.meta.json", "e7", "d7")], {})
    )
    monkeypatch.setattr(rb, "reconcile_etag_get_all", MagicMock(return_value={"d7": "e7"}))
    monkeypatch.setattr(_bf, "_load_meta", lambda k: dict(fat))
    monkeypatch.setattr(_bf, "read_registry_fields", MagicMock())
    monkeypatch.setattr(_bf, "upsert_doc", AsyncMock())
    # Use the REAL _delete_stale_rows this time (fixture stubbed it out).
    monkeypatch.setattr(rb, "_delete_stale_rows", _REAL_DELETE_STALE)
    monkeypatch.setattr(
        "pageindex_mcp.registry.list_all_doc_ids", AsyncMock(return_value={"d7", "gone1"})
    )
    monkeypatch.setattr(
        "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
        AsyncMock(
            return_value={"d7": "2020-01-01T00:00:00+00:00", "gone1": "2020-01-01T00:00:00+00:00"}
        ),
    )
    reg_delete = AsyncMock()
    monkeypatch.setattr("pageindex_mcp.registry.delete_doc", reg_delete)

    await rb.reconcile_registry_drift()

    reg_delete.assert_awaited_once_with("gone1")
    rb.reconcile_etag_prune.assert_called_once_with({"d7"})


# ---------------------------------------------------------------------------
# Zone-4 Phase 3: _drain_verdict_retry_queue runs unconditionally (regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_verdict_retry_queue_called_unconditionally(reconcile_env, monkeypatch):
    """Zone-4 Phase 3 regression: _drain_verdict_retry_queue must be called
    unconditionally during reconcile_registry_drift -- no mode guard, no
    registry_verdict_authority check."""
    monkeypatch.setattr(rb, "_list_meta_entries", lambda: ([], {}))
    monkeypatch.setattr(rb, "reconcile_etag_get_all", MagicMock(return_value={}))

    drain_mock = AsyncMock()
    monkeypatch.setattr(
        "pageindex_mcp.registry_backfill.reconcile._drain_verdict_retry_queue",
        drain_mock,
    )

    await rb.reconcile_registry_drift()

    drain_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_drain_verdict_retry_queue_replays_keys():
    """_drain_verdict_retry_queue scans Redis keys, parses verdict_fields,
    and replays each via upsert_doc + save_doc_meta."""
    import json as _json

    verdict_data = {"verdict": "PASS", "pipeline_version": 4}
    key = b"pageindex:verdict_retry:doc-replay-1"

    redis_mock = AsyncMock()
    redis_mock.scan = AsyncMock(return_value=(0, [key]))
    redis_mock.get = AsyncMock(return_value=_json.dumps(verdict_data).encode())
    redis_mock.delete = AsyncMock()

    upsert_mock = AsyncMock(return_value={"doc_id": "doc-replay-1", "verdict": "PASS"})
    save_mock = MagicMock()

    # The function lazily imports upsert_doc and save_doc_meta;
    # patch at the module level where the imports resolve.
    with (
        patch("pageindex_mcp.registry.upsert_doc", upsert_mock),
        patch("pageindex_mcp.storage.save_doc_meta", save_mock),
    ):
        await _drain_verdict_retry_queue(redis_mock)

    # upsert_doc receives a merged meta dict with doc_id + verdict fields,
    # plus force_verdict_override kwarg (defaults to False when absent).
    expected_meta = {"doc_id": "doc-replay-1", **verdict_data}
    upsert_mock.assert_awaited_once_with(expected_meta, force_verdict_override=False)
    save_mock.assert_called_once()
    redis_mock.delete.assert_awaited()


@pytest.mark.asyncio
async def test_drain_verdict_retry_queue_calls_upsert_doc_not_upsert_verdict():
    """Zone-4 Phase 3 contract: _drain_verdict_retry_queue must call
    upsert_doc directly (not the deprecated upsert_verdict wrapper).
    This verifies the import path inside the function body."""
    import json as _json

    verdict_data = {"verdict": "MARGINAL", "pipeline_version": 5}
    key = b"pageindex:verdict_retry:doc-direct-1"

    redis_mock = AsyncMock()
    redis_mock.scan = AsyncMock(return_value=(0, [key]))
    redis_mock.get = AsyncMock(return_value=_json.dumps(verdict_data).encode())
    redis_mock.delete = AsyncMock()

    upsert_doc_mock = AsyncMock(return_value=None)
    upsert_verdict_mock = AsyncMock(return_value=None)

    with (
        patch("pageindex_mcp.registry.upsert_doc", upsert_doc_mock),
        patch("pageindex_mcp.registry.upsert_verdict", upsert_verdict_mock),
        patch("pageindex_mcp.storage.save_doc_meta", MagicMock()),
    ):
        await _drain_verdict_retry_queue(redis_mock)

    # upsert_doc called, upsert_verdict NOT called
    upsert_doc_mock.assert_awaited_once()
    upsert_verdict_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_drain_verdict_retry_queue_skips_sidecar_when_upsert_returns_none():
    """Zone-4 Phase 3 contract: when upsert_doc returns None (pool
    unavailable or empty doc_id), save_doc_meta must NOT be called."""
    import json as _json

    verdict_data = {"verdict": "PASS", "pipeline_version": 2}
    key = b"pageindex:verdict_retry:doc-none-1"

    redis_mock = AsyncMock()
    redis_mock.scan = AsyncMock(return_value=(0, [key]))
    redis_mock.get = AsyncMock(return_value=_json.dumps(verdict_data).encode())
    redis_mock.delete = AsyncMock()

    save_mock = MagicMock()

    with (
        patch("pageindex_mcp.registry.upsert_doc", AsyncMock(return_value=None)),
        patch("pageindex_mcp.storage.save_doc_meta", save_mock),
    ):
        await _drain_verdict_retry_queue(redis_mock)

    save_mock.assert_not_called()


@pytest.mark.asyncio
async def test_drain_verdict_retry_queue_never_raises():
    """Zone-4 Phase 3 contract: _drain_verdict_retry_queue must never
    propagate exceptions to the caller -- it is best-effort."""
    redis_mock = AsyncMock()
    redis_mock.scan = AsyncMock(side_effect=ConnectionError("Redis totally down"))

    # Must NOT raise
    await _drain_verdict_retry_queue(redis_mock)
