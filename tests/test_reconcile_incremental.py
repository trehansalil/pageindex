"""C-3 / Finding 9 — incremental O(Δ) registry reconcile.

``reconcile_registry_drift`` must stop GETting the full processed JSON for every
doc on every tick. It compares each ``processed/*.meta.json`` object's listing
``etag`` against a Redis last-seen-etag map and only touches the delta:

* fat sidecar (carries sha256 + doc_description) → upsert with NO full-JSON GET;
* thin sidecar → one ``read_registry_fields`` GET, then rewrite as a fat sidecar
  (self-heal) so the next tick is O(Δ);
* no sidecar (orphan .json/.flat.json) → same legacy fallback + fat-sidecar heal;
* unchanged etag → skipped entirely;
* etags are persisted only for docs that upserted successfully;
* deletion detection prunes both the registry row and the stale etag entry.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest

from pageindex_mcp import registry_backfill as rb

# Submodules where the actual names live — patches target these.
from pageindex_mcp.registry_backfill import backfill as _bf
from pageindex_mcp.registry_backfill import reconcile as _rc  # noqa: F401

# Captured at import time — BEFORE the reconcile_env fixture stubs it — so the
# deletion-detection test can restore the real _delete_stale_rows.
_REAL_DELETE_STALE = rb._delete_stale_rows


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
