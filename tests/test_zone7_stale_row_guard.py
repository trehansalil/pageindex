"""Zone-7: Stale-row race guard — processed_at age guard in _delete_stale_rows.

Tests verify:
1. A registry row with processed_at < grace_minutes ago is NOT deleted even when
   its doc_id is absent from the minio_doc_ids set.
2. A registry row with processed_at > grace_minutes ago IS deleted when absent.
3. A row with empty processed_at (legacy) is treated as old enough to delete.
4. The 50% safety threshold still prevents mass deletion.
5. Regression: identical results to pre-age-guard behavior when all rows are
   older than grace_minutes (steady-state reconciliation unchanged).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_now_minus(minutes: int) -> str:
    """Return ISO-8601 UTC timestamp *minutes* in the past."""
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


def _iso_now_plus(minutes: int) -> str:
    """Return ISO-8601 UTC timestamp *minutes* in the future."""
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


# ---------------------------------------------------------------------------
# Test 1: Fresh row (within grace period) is NOT deleted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fresh_row_within_grace_period_not_deleted():
    """A registry row whose processed_at is younger than grace_minutes must
    survive stale deletion even when its doc_id is absent from the MinIO
    listing snapshot."""
    from pageindex_mcp.registry_backfill import _delete_stale_rows

    # fresh-doc: 2 minutes old, well within the default 10-min grace
    fresh_ts = _iso_now_minus(2)
    registry_rows = {"fresh-doc": fresh_ts, "old-doc": _iso_now_minus(60)}

    deleted_ids: list[str] = []

    async def mock_delete_doc(doc_id: str) -> None:
        deleted_ids.append(doc_id)

    with (
        patch(
            "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
            AsyncMock(return_value=registry_rows),
        ),
        patch(
            "pageindex_mcp.registry.delete_doc",
            side_effect=mock_delete_doc,
        ),
    ):
        # MinIO listing has neither doc — both are "stale" by set difference
        await _delete_stale_rows(set())

    # old-doc should be deleted; fresh-doc should be protected by the age guard
    assert "old-doc" in deleted_ids
    assert "fresh-doc" not in deleted_ids


# ---------------------------------------------------------------------------
# Test 2: Old row (outside grace period) IS deleted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_old_row_outside_grace_period_is_deleted():
    """A registry row whose processed_at is older than grace_minutes must be
    deleted when its doc_id is absent from the MinIO listing."""
    from pageindex_mcp.registry_backfill import _delete_stale_rows

    old_ts = _iso_now_minus(30)
    # Include extra docs present in MinIO to stay under the 50% safety threshold
    registry_rows = {
        "stale-doc": old_ts,
        "present-1": _iso_now_minus(60),
        "present-2": _iso_now_minus(60),
    }

    deleted_ids: list[str] = []

    async def mock_delete_doc(doc_id: str) -> None:
        deleted_ids.append(doc_id)

    with (
        patch(
            "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
            AsyncMock(return_value=registry_rows),
        ),
        patch(
            "pageindex_mcp.registry.delete_doc",
            side_effect=mock_delete_doc,
        ),
    ):
        await _delete_stale_rows({"present-1", "present-2"})

    assert "stale-doc" in deleted_ids


# ---------------------------------------------------------------------------
# Test 3: Empty processed_at (legacy row) treated as old enough to delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_processed_at_treated_as_old():
    """Rows with processed_at == '' (legacy/schema-default) are NOT protected
    by the age guard — they are treated as old enough to be stale candidates."""
    from pageindex_mcp.registry_backfill import _delete_stale_rows

    registry_rows = {
        "legacy-doc": "",
        "present-1": _iso_now_minus(60),
        "present-2": _iso_now_minus(60),
    }

    deleted_ids: list[str] = []

    async def mock_delete_doc(doc_id: str) -> None:
        deleted_ids.append(doc_id)

    with (
        patch(
            "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
            AsyncMock(return_value=registry_rows),
        ),
        patch(
            "pageindex_mcp.registry.delete_doc",
            side_effect=mock_delete_doc,
        ),
    ):
        await _delete_stale_rows({"present-1", "present-2"})

    assert "legacy-doc" in deleted_ids


# ---------------------------------------------------------------------------
# Test 4: Unparseable processed_at treated as old enough to delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unparseable_processed_at_treated_as_old():
    """Rows with a non-ISO processed_at value are not protected by the age
    guard — they are treated as old enough to be deleted."""
    from pageindex_mcp.registry_backfill import _delete_stale_rows

    registry_rows = {
        "garbled-ts-doc": "not-a-date",
        "present-1": _iso_now_minus(60),
        "present-2": _iso_now_minus(60),
    }

    deleted_ids: list[str] = []

    async def mock_delete_doc(doc_id: str) -> None:
        deleted_ids.append(doc_id)

    with (
        patch(
            "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
            AsyncMock(return_value=registry_rows),
        ),
        patch(
            "pageindex_mcp.registry.delete_doc",
            side_effect=mock_delete_doc,
        ),
    ):
        await _delete_stale_rows({"present-1", "present-2"})

    assert "garbled-ts-doc" in deleted_ids


# ---------------------------------------------------------------------------
# Test 5: 50% safety threshold still prevents mass deletion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_safety_threshold_prevents_mass_deletion():
    """When stale rows exceed _MAX_STALE_DELETE_FRACTION (50%) of the total
    registry, no deletions should occur — even if all rows are old enough."""
    from pageindex_mcp.registry_backfill import _delete_stale_rows

    # 8 old rows in registry, 0 in MinIO — 100% stale, exceeds 50% threshold
    registry_rows = {
        f"doc-{i}": _iso_now_minus(60) for i in range(8)
    }

    deleted_ids: list[str] = []

    async def mock_delete_doc(doc_id: str) -> None:
        deleted_ids.append(doc_id)

    with (
        patch(
            "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
            AsyncMock(return_value=registry_rows),
        ),
        patch(
            "pageindex_mcp.registry.delete_doc",
            side_effect=mock_delete_doc,
        ),
    ):
        await _delete_stale_rows(set())

    # Safety valve triggers: no deletions
    assert len(deleted_ids) == 0


# ---------------------------------------------------------------------------
# Test 6: Safety threshold allows deletion when stale fraction is small
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_safety_threshold_allows_small_fraction():
    """When stale rows are under the 50% threshold, deletion proceeds."""
    from pageindex_mcp.registry_backfill import _delete_stale_rows

    # 10 rows total, 3 stale (30% < 50%)
    minio_present = {f"doc-{i}" for i in range(7)}
    registry_rows = {f"doc-{i}": _iso_now_minus(60) for i in range(10)}

    deleted_ids: list[str] = []

    async def mock_delete_doc(doc_id: str) -> None:
        deleted_ids.append(doc_id)

    with (
        patch(
            "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
            AsyncMock(return_value=registry_rows),
        ),
        patch(
            "pageindex_mcp.registry.delete_doc",
            side_effect=mock_delete_doc,
        ),
    ):
        await _delete_stale_rows(minio_present)

    assert set(deleted_ids) == {"doc-7", "doc-8", "doc-9"}


# ---------------------------------------------------------------------------
# Test 7: Custom grace_minutes is respected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_custom_grace_minutes():
    """A custom grace_minutes parameter narrows or widens the protection
    window. With grace_minutes=1, a 2-minute-old row is NOT protected."""
    from pageindex_mcp.registry_backfill import _delete_stale_rows

    two_min_ago = _iso_now_minus(2)
    registry_rows = {
        "borderline-doc": two_min_ago,
        "present-1": _iso_now_minus(60),
        "present-2": _iso_now_minus(60),
    }

    deleted_ids: list[str] = []

    async def mock_delete_doc(doc_id: str) -> None:
        deleted_ids.append(doc_id)

    with (
        patch(
            "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
            AsyncMock(return_value=registry_rows),
        ),
        patch(
            "pageindex_mcp.registry.delete_doc",
            side_effect=mock_delete_doc,
        ),
    ):
        # grace=1 minute, row is 2 minutes old => not protected => deleted
        await _delete_stale_rows({"present-1", "present-2"}, grace_minutes=1)

    assert "borderline-doc" in deleted_ids


@pytest.mark.asyncio
async def test_custom_grace_minutes_protects_when_wider():
    """With grace_minutes=5, a 2-minute-old row IS protected."""
    from pageindex_mcp.registry_backfill import _delete_stale_rows

    two_min_ago = _iso_now_minus(2)
    registry_rows = {"borderline-doc": two_min_ago}

    deleted_ids: list[str] = []

    async def mock_delete_doc(doc_id: str) -> None:
        deleted_ids.append(doc_id)

    with (
        patch(
            "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
            AsyncMock(return_value=registry_rows),
        ),
        patch(
            "pageindex_mcp.registry.delete_doc",
            side_effect=mock_delete_doc,
        ),
    ):
        # grace=5 minutes, row is 2 minutes old => protected => NOT deleted
        await _delete_stale_rows(set(), grace_minutes=5)

    assert "borderline-doc" not in deleted_ids


# ---------------------------------------------------------------------------
# Test 8: Regression — steady-state (all old rows) behaves identically
# to pre-age-guard logic
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regression_all_old_rows_behave_identically():
    """When every registry row is older than grace_minutes, _delete_stale_rows
    produces the same result as the pre-age-guard implementation: all stale
    doc_ids (absent from MinIO listing) are deleted, subject to the 50% cap."""
    from pageindex_mcp.registry_backfill import _delete_stale_rows

    minio_ids = {"doc-a", "doc-b", "doc-c"}
    # 5 registry rows, 2 are stale (40% < 50%), all old enough
    registry_rows = {
        "doc-a": _iso_now_minus(120),
        "doc-b": _iso_now_minus(120),
        "doc-c": _iso_now_minus(120),
        "doc-x": _iso_now_minus(120),
        "doc-y": _iso_now_minus(120),
    }

    deleted_ids: list[str] = []

    async def mock_delete_doc(doc_id: str) -> None:
        deleted_ids.append(doc_id)

    with (
        patch(
            "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
            AsyncMock(return_value=registry_rows),
        ),
        patch(
            "pageindex_mcp.registry.delete_doc",
            side_effect=mock_delete_doc,
        ),
    ):
        await _delete_stale_rows(minio_ids)

    # Exactly the stale set is deleted (same as pre-age-guard)
    assert set(deleted_ids) == {"doc-x", "doc-y"}


# ---------------------------------------------------------------------------
# Test 9: list_all_doc_ids_with_timestamps returns None => no deletion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_returns_none_skips_deletion():
    """When list_all_doc_ids_with_timestamps returns None (Postgres error),
    no deletions should occur."""
    from pageindex_mcp.registry_backfill import _delete_stale_rows

    with (
        patch(
            "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
            AsyncMock(return_value=None),
        ),
        patch(
            "pageindex_mcp.registry.delete_doc",
            AsyncMock(),
        ) as mock_delete,
    ):
        await _delete_stale_rows(set())

    mock_delete.assert_not_called()


# ---------------------------------------------------------------------------
# Test 10: Mixed scenario — fresh + old + legacy + present-in-minio
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mixed_fresh_old_legacy_present():
    """End-to-end mix: docs present in MinIO, fresh (protected), old (deleted),
    and legacy (deleted)."""
    from pageindex_mcp.registry_backfill import _delete_stale_rows

    registry_rows = {
        "in-minio":    _iso_now_minus(120),   # present in MinIO => not stale
        "fresh-race":  _iso_now_minus(3),     # absent from MinIO, but < 10 min => protected
        "old-stale":   _iso_now_minus(60),    # absent from MinIO, > 10 min => deleted
        "legacy-stale": "",                   # absent from MinIO, empty ts => deleted
    }
    minio_doc_ids = {"in-minio"}

    deleted_ids: list[str] = []

    async def mock_delete_doc(doc_id: str) -> None:
        deleted_ids.append(doc_id)

    with (
        patch(
            "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
            AsyncMock(return_value=registry_rows),
        ),
        patch(
            "pageindex_mcp.registry.delete_doc",
            side_effect=mock_delete_doc,
        ),
    ):
        await _delete_stale_rows(minio_doc_ids)

    assert "in-minio" not in deleted_ids      # present in MinIO
    assert "fresh-race" not in deleted_ids     # age-guarded
    assert "old-stale" in deleted_ids          # legitimately stale
    assert "legacy-stale" in deleted_ids       # legacy empty ts => old


# ---------------------------------------------------------------------------
# Test 11: Naive timestamp (no tzinfo) handled correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_naive_timestamp_treated_as_utc():
    """A processed_at without timezone info should be treated as UTC
    and still trigger the age guard when young enough."""
    from pageindex_mcp.registry_backfill import _delete_stale_rows

    # Naive (no Z or +00:00) but recent
    naive_recent = (datetime.now(UTC) - timedelta(minutes=3)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    registry_rows = {"naive-fresh": naive_recent}

    deleted_ids: list[str] = []

    async def mock_delete_doc(doc_id: str) -> None:
        deleted_ids.append(doc_id)

    with (
        patch(
            "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
            AsyncMock(return_value=registry_rows),
        ),
        patch(
            "pageindex_mcp.registry.delete_doc",
            side_effect=mock_delete_doc,
        ),
    ):
        await _delete_stale_rows(set())

    # Naive recent timestamp => assumed UTC => within grace => protected
    assert "naive-fresh" not in deleted_ids
