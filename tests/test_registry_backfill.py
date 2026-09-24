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


def _settings(**overrides):
    """Build a settings snapshot with the given fields overridden."""
    from pageindex_mcp.config import settings as _base_settings

    return dataclasses.replace(_base_settings, **overrides)


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
    async def test_key_retained_when_replay_degraded(self):
        """RFC-042 R3.2: when _upsert_registry_row cannot reach Postgres
        (degraded path returns False), the retry key must NOT be deleted --
        the next sweep retries instead of silently dropping the verdict."""
        redis = _make_redis(
            {
                "pageindex:verdict_retry:doc-keep": {
                    "verdict": "PASS",
                },
            }
        )

        with (
            # registry disabled → _upsert_registry_row degraded early return.
            patch(
                "pageindex_mcp.worker.registry_mirror.settings",
                _settings(registry_enabled=False, postgres_dsn=""),
            ),
            patch(
                "pageindex_mcp.worker.registry_mirror._cas_filter_sidecar_meta",
                AsyncMock(side_effect=lambda doc_id, cc, meta, **kw: meta),
            ),
            patch("pageindex_mcp.storage.verdict.save_doc_meta"),
            patch("pageindex_mcp.storage.save_doc_meta"),
        ):
            await _drain_verdict_retry_queue(redis)

        redis.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sidecar_written_with_winning_values(self):
        """After upsert_doc returns winning values, save_doc_meta is called
        with doc_id and the winning dict."""
        redis = _make_redis(
            {
                "pageindex:verdict_retry:doc-sc": {
                    "verdict": "PASS",
                    "force_verdict_override": True,
                },
            }
        )

        winning = {
            "doc_id": "doc-sc",
            "verdict": "PASS",
            "pipeline_version": 5,
            "permanent_marginal": False,
            "verdict_computed_at": "2026-08-25T12:00:00Z",
        }
        mock_upsert = AsyncMock(return_value=winning)
        mock_save = MagicMock()

        with (
            patch(
                "pageindex_mcp.worker.registry_mirror.settings",
                _settings(registry_enabled=True, postgres_dsn="postgresql://x"),
            ),
            patch("pageindex_mcp.registry.get_pool", return_value=object()),
            patch("pageindex_mcp.registry.queries.upsert_doc", mock_upsert),
            patch("pageindex_mcp.registry.upsert_doc", mock_upsert),
            patch(
                "pageindex_mcp.worker.registry_mirror._cas_filter_sidecar_meta",
                AsyncMock(side_effect=lambda doc_id, cc, meta, **kw: meta),
            ),
            patch("pageindex_mcp.storage.verdict.save_doc_meta", mock_save),
            patch("pageindex_mcp.storage.save_doc_meta", mock_save),
        ):
            await _drain_verdict_retry_queue(redis)

        # save_doc_meta is called via asyncio.to_thread with the winning dict
        # (mutated in place with the write-through consistency_regime stamp).
        mock_save.assert_called_once_with("doc-sc", winning)


# ===========================================================================
# Contract: _delete_stale_rows protects rows with empty/missing processed_at
# ===========================================================================


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
    monkeypatch.setattr(rb, "reconcile_etag_set_many", MagicMock(return_value=True))
    monkeypatch.setattr(rb, "reconcile_etag_generation", MagicMock(return_value="0"))
    monkeypatch.setattr(rb, "reconcile_etag_prune", MagicMock())
    monkeypatch.setattr(rb, "_delete_stale_rows", AsyncMock())
    return redis_mock


@pytest.mark.asyncio
async def test_reconcile_thin_sidecar_self_heals(reconcile_env, monkeypatch):
    """A thin sidecar -- and equally a legacy orphan with no .meta.json at all --
    triggers exactly one read_registry_fields GET and is then rewritten as a fat
    sidecar via the sole write-through path (_upsert_registry_row, RFC-042 D3)
    so subsequent ticks are O(Δ)."""
    import pageindex_mcp.worker.registry_mirror as _rm

    thin = {"doc_id": "d2", "doc_name": "x"}
    rich = {"doc_id": "d2", "doc_name": "x", "sha256": "h2", "doc_description": "dd"}
    monkeypatch.setattr(
        rb, "_list_meta_entries", lambda: ([("processed/d2.meta.json", "e2", "d2")], {})
    )
    monkeypatch.setattr(rb, "reconcile_etag_get_all", MagicMock(return_value={}))
    monkeypatch.setattr(_bf, "_load_meta", lambda k: dict(thin))
    read_rf = MagicMock(return_value=dict(rich))
    monkeypatch.setattr(_bf, "read_registry_fields", read_rf)
    urr = AsyncMock(return_value=True)
    monkeypatch.setattr(_rm, "_upsert_registry_row", urr)
    monkeypatch.setattr(_bf, "upsert_doc", AsyncMock())

    await rb.reconcile_registry_drift()

    assert read_rf.call_count == 1
    urr.assert_awaited_once()
    healed = urr.await_args.kwargs["registry_fields"]
    assert healed["sha256"] == "h2"

    # §2b: the same heal covers a legacy ORPHAN -- processed/<id>.json with no
    # .meta.json at all: one read_registry_fields, one _upsert_registry_row.
    orphan_rich = {"doc_id": "orph1", "doc_name": "x", "sha256": "ho", "doc_description": "d"}
    monkeypatch.setattr(rb, "_list_meta_entries", lambda: ([], {"orph1": None}))
    read_rf_orphan = MagicMock(return_value=dict(orphan_rich))
    monkeypatch.setattr(_bf, "read_registry_fields", read_rf_orphan)
    urr_orphan = AsyncMock(return_value=True)
    monkeypatch.setattr(_rm, "_upsert_registry_row", urr_orphan)

    await rb.reconcile_registry_drift()

    assert read_rf_orphan.call_count == 1
    urr_orphan.assert_awaited_once()
    assert urr_orphan.await_args.kwargs["registry_fields"]["sha256"] == "ho"


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

    rb.reconcile_etag_set_many.assert_called_once_with({"d5": "e5"}, "0")


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
    """_drain_verdict_retry_queue scans Redis keys, parses verdict_fields, and
    replays each via upsert_doc (never the deprecated upsert_verdict wrapper)
    + save_doc_meta, deleting the retry key once the replay succeeds."""
    import json as _json

    verdict_data = {"verdict": "PASS", "pipeline_version": 4}
    key = b"pageindex:verdict_retry:doc-replay-1"

    redis_mock = AsyncMock()
    redis_mock.scan = AsyncMock(return_value=(0, [key]))
    redis_mock.get = AsyncMock(return_value=_json.dumps(verdict_data).encode())
    redis_mock.delete = AsyncMock()

    upsert_mock = AsyncMock(return_value={"doc_id": "doc-replay-1", "verdict": "PASS"})
    upsert_verdict_mock = AsyncMock(return_value=None)
    save_mock = MagicMock()

    # The function lazily imports upsert_doc and save_doc_meta;
    # patch at the module level where the imports resolve.
    with (
        patch(
            "pageindex_mcp.worker.registry_mirror.settings",
            _settings(registry_enabled=True, postgres_dsn="postgresql://x"),
        ),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch("pageindex_mcp.registry.upsert_doc", upsert_mock),
        patch("pageindex_mcp.registry.upsert_verdict", upsert_verdict_mock),
        patch(
            "pageindex_mcp.worker.registry_mirror._cas_filter_sidecar_meta",
            AsyncMock(side_effect=lambda doc_id, cc, meta, **kw: meta),
        ),
        patch("pageindex_mcp.storage.save_doc_meta", save_mock),
    ):
        await _drain_verdict_retry_queue(redis_mock)

    # upsert_doc receives a merged meta dict with doc_id + verdict fields,
    # plus force_verdict_override kwarg (defaults to False when absent).
    expected_meta = {"doc_id": "doc-replay-1", **verdict_data}
    upsert_mock.assert_awaited_once_with(expected_meta, force_verdict_override=False)
    # Zone-4 Phase 3 contract: the replay goes through upsert_doc directly, not
    # the deprecated upsert_verdict wrapper.
    upsert_verdict_mock.assert_not_awaited()
    save_mock.assert_called_once()
    # The retry key is dropped only after the replay succeeded.
    redis_mock.delete.assert_awaited()


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
        patch(
            "pageindex_mcp.worker.registry_mirror.settings",
            _settings(registry_enabled=True, postgres_dsn="postgresql://x"),
        ),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
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


# ---------------------------------------------------------------------------
# Consolidated (test-budget reduction): table-driven replacements.  Each test
# loops over the rows its former per-row siblings covered, collects every
# mismatch and reports the offending row names.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_force_verdict_override_wiring_matrix():
    """_drain_verdict_retry_queue pops force_verdict_override out of the
    deserialized verdict_fields and forwards it as a kwarg to upsert_doc
    (mirroring registry_mirror.py), defaulting to False when absent.  It is
    never persisted as a column in the meta dict."""
    failures = []
    rows = [
        (
            "explicit True",
            {"verdict": "FAIL", "pipeline_version": 5, "force_verdict_override": True},
            True,
        ),
        ("absent", {"verdict": "PASS", "pipeline_version": 4}, False),
    ]

    for name, payload, expected in rows:
        redis = _make_redis({"pageindex:verdict_retry:doc-fvo": dict(payload)})
        mock_upsert = AsyncMock(
            return_value={
                "doc_id": "doc-fvo",
                "verdict": payload["verdict"],
                "pipeline_version": payload["pipeline_version"],
                "permanent_marginal": False,
                "verdict_computed_at": "2026-08-25T00:00:00Z",
            }
        )
        with (
            patch(
                "pageindex_mcp.worker.registry_mirror.settings",
                _settings(registry_enabled=True, postgres_dsn="postgresql://x"),
            ),
            patch("pageindex_mcp.registry.get_pool", return_value=object()),
            patch("pageindex_mcp.registry.queries.upsert_doc", mock_upsert),
            patch("pageindex_mcp.registry.upsert_doc", mock_upsert),
            patch("pageindex_mcp.storage.verdict.save_doc_meta"),
            patch("pageindex_mcp.storage.save_doc_meta"),
        ):
            await _drain_verdict_retry_queue(redis)

        if mock_upsert.await_args is None:
            failures.append(f"{name}: upsert_doc was never awaited")
            continue
        got = mock_upsert.await_args.kwargs.get("force_verdict_override")
        if got is not expected:
            failures.append(f"{name}: kwarg={got!r}, expected {expected!r}")
        meta_arg = mock_upsert.await_args.args[0]
        if "force_verdict_override" in meta_arg:
            failures.append(f"{name}: force_verdict_override persisted into the meta dict")
        # The meta dict carries the doc_id parsed out of the Redis key plus the
        # replayed verdict fields.
        if meta_arg.get("doc_id") != "doc-fvo":
            failures.append(f"{name}: meta doc_id={meta_arg.get('doc_id')!r}")
        for key in ("verdict", "pipeline_version"):
            if meta_arg.get(key) != payload[key]:
                failures.append(f"{name}: meta {key}={meta_arg.get(key)!r}")

    assert not failures, failures


@pytest.mark.asyncio
async def test_delete_stale_rows_empty_processed_at_protection_matrix():
    """Zone-7: _delete_stale_rows protects rows whose processed_at is empty or
    missing via the age guard when cleanup_protect_empty_processed_at is True
    (the default); with the flag off the old "treat as stale" behaviour is
    preserved.  A valid old timestamp is still deleted either way."""
    from pageindex_mcp.config import settings as _base_settings
    from pageindex_mcp.registry_backfill.cleanup import _delete_stale_rows

    # >2 rows in the "should delete" cases so the stale fraction stays under
    # the 50% mass-deletion safety cap.
    in_minio = {
        "in-minio-1": "2026-01-01T00:00:00+00:00",
        "in-minio-2": "2026-01-01T00:00:00+00:00",
    }

    rows = [
        # (name, protect flag, registry rows, minio ids, expected deletions)
        ("empty processed_at protected", True, {"stale-empty-1": ""}, set(), []),
        ("None processed_at protected", True, {"stale-none-1": None}, set(), []),
        (
            "empty processed_at deleted when protection disabled",
            False,
            {"stale-old-1": "", **in_minio},
            set(in_minio),
            ["stale-old-1"],
        ),
        (
            "valid old processed_at still deleted while protected",
            True,
            {"stale-old-2": "2020-01-01T00:00:00+00:00", **in_minio},
            set(in_minio),
            ["stale-old-2"],
        ),
    ]

    failures = []
    for name, protect, registry_rows, minio_ids, expected in rows:
        mock_delete = AsyncMock()
        with (
            patch(
                "pageindex_mcp.config.settings",
                dataclasses.replace(_base_settings, cleanup_protect_empty_processed_at=protect),
            ),
            patch(
                "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
                AsyncMock(return_value=registry_rows),
            ),
            patch("pageindex_mcp.registry.delete_doc", mock_delete),
        ):
            await _delete_stale_rows(minio_ids)
        deleted = [c.args[0] for c in mock_delete.await_args_list]
        if deleted != expected:
            failures.append(f"{name}: deleted {deleted!r}, expected {expected!r}")

    assert not failures, failures


@pytest.mark.asyncio
async def test_reconcile_etag_diff_is_incremental(reconcile_env, monkeypatch):
    """O(Δ) reconciliation: a doc whose stored etag matches the listing etag is
    skipped entirely (no MinIO full-JSON GET, no upsert, no etag rewrite); a
    doc whose etag changed (re-ingested) is upserted and its new etag stored."""
    failures = []

    for name, listing_etag, stored, expect_upsert, expect_etag_write in (
        ("unchanged etag", "e3", {"d3": "e3"}, False, None),
        ("changed etag", "NEW", {"d3": "OLD"}, True, {"d3": "NEW"}),
    ):
        fat = {"doc_id": "d3", "doc_name": "x", "sha256": "h", "doc_description": "d"}
        monkeypatch.setattr(
            rb,
            "_list_meta_entries",
            lambda listing_etag=listing_etag: (
                [("processed/d3.meta.json", listing_etag, "d3")],
                {},
            ),
        )
        monkeypatch.setattr(rb, "reconcile_etag_get_all", MagicMock(return_value=dict(stored)))
        monkeypatch.setattr(_bf, "_load_meta", lambda k: dict(fat))
        read_rf = MagicMock()
        monkeypatch.setattr(_bf, "read_registry_fields", read_rf)
        upsert = AsyncMock()
        monkeypatch.setattr(_bf, "upsert_doc", upsert)
        rb.reconcile_etag_set_many.reset_mock()

        await rb.reconcile_registry_drift()

        if read_rf.call_count:
            failures.append(f"{name}: fat sidecar triggered {read_rf.call_count} full-JSON GET(s)")
        if bool(upsert.await_count) is not expect_upsert:
            failures.append(
                f"{name}: upsert awaited {upsert.await_count}x, expected {expect_upsert}"
            )
        if expect_etag_write is None:
            if rb.reconcile_etag_set_many.called:
                failures.append(f"{name}: etag rewritten despite no change")
        else:
            calls = [c.args[0] for c in rb.reconcile_etag_set_many.call_args_list]
            if calls != [expect_etag_write]:
                failures.append(f"{name}: etag writes {calls!r}, expected [{expect_etag_write!r}]")

    assert not failures, failures


@pytest.mark.asyncio
async def test_backfill_gather_bounds_concurrency_and_isolates_failures():
    """_upsert_all fans out over the sidecar keys under a semaphore (bounded at
    10 concurrent upserts, but genuinely concurrent) and isolates a per-item
    failure: every other key is still upserted and only the failing key is
    returned."""
    max_concurrent = 0
    current = 0
    call_count = 0
    lock = asyncio.Lock()

    async def _upsert(meta):
        nonlocal max_concurrent, current, call_count
        async with lock:
            call_count += 1
            current += 1
            max_concurrent = max(max_concurrent, current)
        await asyncio.sleep(0.01)
        async with lock:
            current -= 1
        if meta["doc_id"] == "doc2":
            raise RuntimeError("simulated upsert failure")

    with (
        patch(
            "pageindex_mcp.registry_backfill.backfill._load_meta",
            side_effect=lambda k: _make_meta(k),
        ) as mock_load,
        patch(
            "pageindex_mcp.registry_backfill.backfill.upsert_doc",
            new_callable=AsyncMock,
            side_effect=_upsert,
        ),
    ):
        from pageindex_mcp.registry_backfill import _upsert_all

        keys = [f"doc{i}.meta.json" for i in range(20)]
        failed = await _upsert_all(keys, dry_run=False)

    assert mock_load.call_count == 20
    assert call_count == 20, "every key must be attempted despite a per-item failure"
    assert failed == ["doc2.meta.json"]
    assert max_concurrent <= 10, f"Expected max 10 concurrent, got {max_concurrent}"
    assert max_concurrent > 1, "Expected some concurrency, got serial execution"
