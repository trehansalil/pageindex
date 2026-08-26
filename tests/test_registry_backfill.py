"""Registry backfill reconcile wiring tests.

Validates:
  _drain_verdict_retry_queue pops force_verdict_override from the meta dict
  and passes it as a kwarg to upsert_doc.  When absent from verdict_fields,
  defaults to False.  When True, upsert_doc receives force_verdict_override=True.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp.registry_backfill.reconcile import _drain_verdict_retry_queue


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
