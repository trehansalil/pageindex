"""RFC-012 Property 6 — Backfill concurrency correctness (D7/ISS-46).

Validates that _upsert_all uses bounded-concurrency asyncio.gather with
Semaphore(10), handles per-item failures gracefully, and calls upsert_doc
once per item.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


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
