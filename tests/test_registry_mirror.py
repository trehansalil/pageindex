# tests/test_registry_mirror.py
"""Zone-4 Registry Dual-Write Consistency: _upsert_registry_row contract tests.

Verifies:
1. _upsert_registry_row follows a single linear path (Postgres-authoritative).
2. Backward compat with verdict_fields=None (preprocess_client.py call site).
3. verdict_fields overlay onto MinIO-read fields before upsert.
4. Pool-not-ready queues verdict retry via _enqueue_verdict_retry.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp.worker.registry_mirror import (
    _enqueue_verdict_retry,
    _upsert_registry_row,
)


def _settings(**overrides):
    from pageindex_mcp.config import settings as _base_settings

    return dataclasses.replace(_base_settings, **overrides)


_REGISTRY_ENABLED = _settings(
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
        patch("pageindex_mcp.worker.registry_mirror.settings", _REGISTRY_ENABLED),
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
        patch("pageindex_mcp.worker.registry_mirror.settings", _REGISTRY_ENABLED),
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
        patch("pageindex_mcp.worker.registry_mirror.settings", _REGISTRY_ENABLED),
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
        patch("pageindex_mcp.worker.registry_mirror.settings", _REGISTRY_ENABLED),
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
        patch("pageindex_mcp.worker.registry_mirror.settings", _REGISTRY_ENABLED),
        patch("pageindex_mcp.registry.get_pool", return_value=None),
        patch(
            "pageindex_mcp.worker.registry_mirror._enqueue_verdict_retry",
            AsyncMock(),
        ) as mock_enqueue,
    ):
        await _upsert_registry_row("doc-noretry", None)

    mock_enqueue.assert_not_awaited()


# ---------------------------------------------------------------------------
# Contract: registry disabled -> early return, no upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_registry_row_registry_disabled_noop():
    """When registry_enabled is False, _upsert_registry_row returns immediately."""
    disabled = _settings(registry_enabled=False, postgres_dsn="")

    with (
        patch("pageindex_mcp.worker.registry_mirror.settings", disabled),
        patch("pageindex_mcp.registry.upsert_doc", AsyncMock()) as mock_upsert,
    ):
        await _upsert_registry_row("doc-x", None)

    mock_upsert.assert_not_awaited()


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
        patch("pageindex_mcp.worker.registry_mirror.settings", _REGISTRY_ENABLED),
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
        patch("pageindex_mcp.worker.registry_mirror.settings", _REGISTRY_ENABLED),
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
        patch("pageindex_mcp.worker.registry_mirror.settings", _REGISTRY_ENABLED),
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
        patch("pageindex_mcp.worker.registry_mirror.settings", _REGISTRY_ENABLED),
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
