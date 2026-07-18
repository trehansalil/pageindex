"""RFC-007 D3 / Property 7: registry backfill must never mark the registry
complete when zero .meta.json sidecars were found."""

import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest

from pageindex_mcp import registry_backfill as rb


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
def fake_redis_client():
    client = MagicMock()
    client.aclose = AsyncMock()
    return client


async def test_backfill_zero_keys_skips_complete(monkeypatch, fake_redis_client):
    _wire_settings(monkeypatch)

    monkeypatch.setattr(rb, "init_registry", AsyncMock())
    monkeypatch.setattr(rb, "close_registry", AsyncMock())
    is_registry_complete = AsyncMock(return_value=False)
    monkeypatch.setattr(rb, "is_registry_complete", is_registry_complete)
    set_registry_complete = AsyncMock()
    monkeypatch.setattr(rb, "set_registry_complete", set_registry_complete)
    monkeypatch.setattr(rb, "_list_meta_keys", lambda: [])
    monkeypatch.setattr("redis.asyncio.from_url", lambda *a, **k: fake_redis_client)

    await rb._backfill(dry_run=False, force=False)

    set_registry_complete.assert_not_awaited()
    fake_redis_client.aclose.assert_awaited_once()


async def test_backfill_nonzero_keys_sets_complete(monkeypatch, fake_redis_client):
    """Sanity check: the guard doesn't block the success path — complete is
    still set once every sidecar upserts cleanly."""
    _wire_settings(monkeypatch)

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
