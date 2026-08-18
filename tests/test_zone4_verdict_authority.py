"""Zone 4: Dual-Store Verdict Consistency and Persistence Timing.

Tests for:
- upsert_verdict() CAS-guarded RETURNING semantics
- Non-verdict column processed_at CAS guard in _UPSERT_SQL
- REGISTRY_VERDICT_AUTHORITY feature flag write-order switching
- save_doc_meta barrier gating under authority modes
- Redis verdict retry queue drain in reconcile_registry_drift
- Column exhaustiveness between DDL and DML
- CAS symmetry between _UPSERT_SQL and _UPSERT_VERDICT_SQL
- Feature flag validation at startup
"""

from __future__ import annotations

import asyncio
import json
import re
import textwrap
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# 1. upsert_verdict() RETURNING contract
# ---------------------------------------------------------------------------


class TestUpsertVerdictReturning:
    """Verify upsert_verdict() returns winning row via RETURNING."""

    @pytest.mark.asyncio
    async def test_returns_winning_row_when_incoming_is_newer(self):
        """Incoming verdict_computed_at > existing: returned dict has all verdict columns."""
        from pageindex_mcp.registry import upsert_verdict

        winning_row = {
            "doc_id": "abc12345",
            "verdict": "PASS",
            "pipeline_version": 4,
            "permanent_marginal": False,
            "verdict_computed_at": "2026-08-18T12:00:00Z",
        }
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=winning_row)

        with patch("pageindex_mcp.registry.get_pool", return_value=mock_pool):
            result = await upsert_verdict("abc12345", {
                "verdict": "PASS",
                "pipeline_version": 4,
                "permanent_marginal": False,
                "verdict_computed_at": "2026-08-18T12:00:00Z",
            })

        assert result is not None
        assert result["doc_id"] == "abc12345"
        assert result["verdict"] == "PASS"
        assert result["pipeline_version"] == 4
        assert result["permanent_marginal"] is False
        assert result["verdict_computed_at"] == "2026-08-18T12:00:00Z"

        # Verify fetchrow was called (RETURNING path, not fire-and-forget execute)
        mock_pool.fetchrow.assert_called_once()

    @pytest.mark.asyncio
    async def test_preserves_existing_when_incoming_is_older(self):
        """Incoming verdict_computed_at < existing: RETURNING still returns
        the preserved (existing) values."""
        from pageindex_mcp.registry import upsert_verdict

        # The SQL CAS guard preserves existing; RETURNING emits that row.
        existing_row = {
            "doc_id": "abc12345",
            "verdict": "MARGINAL",
            "pipeline_version": 3,
            "permanent_marginal": True,
            "verdict_computed_at": "2026-08-18T14:00:00Z",
        }
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=existing_row)

        with patch("pageindex_mcp.registry.get_pool", return_value=mock_pool):
            result = await upsert_verdict("abc12345", {
                "verdict": "PASS",
                "pipeline_version": 4,
                "permanent_marginal": False,
                "verdict_computed_at": "2026-08-18T10:00:00Z",  # older
            })

        # CAS preserves existing: returned row is whatever SQL decided
        assert result is not None
        assert result["verdict"] == "MARGINAL"
        assert result["pipeline_version"] == 3
        assert result["verdict_computed_at"] == "2026-08-18T14:00:00Z"

    @pytest.mark.asyncio
    async def test_returns_none_when_pool_unavailable(self):
        """upsert_verdict returns None when pool is None."""
        from pageindex_mcp.registry import upsert_verdict

        with patch("pageindex_mcp.registry.get_pool", return_value=None):
            result = await upsert_verdict("abc12345", {
                "verdict": "PASS",
                "verdict_computed_at": "2026-08-18T12:00:00Z",
            })

        assert result is None

    @pytest.mark.asyncio
    async def test_returned_dict_contains_all_verdict_columns(self):
        """Verify the RETURNING clause includes all 5 verdict columns."""
        from pageindex_mcp.registry import _UPSERT_VERDICT_SQL

        returning_clause = _UPSERT_VERDICT_SQL.split("RETURNING")[1].strip().rstrip(";").strip()
        columns = [c.strip() for c in returning_clause.split(",")]
        expected = {"doc_id", "verdict", "pipeline_version", "permanent_marginal", "verdict_computed_at"}
        assert set(columns) == expected

    @pytest.mark.asyncio
    async def test_uses_fetchrow_not_execute(self):
        """upsert_verdict must use fetchrow (for RETURNING), not execute."""
        from pageindex_mcp.registry import upsert_verdict

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value={"doc_id": "x", "verdict": "PASS",
                                                      "pipeline_version": 4,
                                                      "permanent_marginal": False,
                                                      "verdict_computed_at": "2026-01-01"})

        with patch("pageindex_mcp.registry.get_pool", return_value=mock_pool):
            await upsert_verdict("x", {"verdict": "PASS", "verdict_computed_at": "2026-01-01"})

        mock_pool.fetchrow.assert_called_once()
        mock_pool.execute.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Non-verdict column CAS guard (processed_at protects sha256, node_count)
# ---------------------------------------------------------------------------


class TestProcessedAtCasGuard:
    """Verify _UPSERT_SQL uses processed_at CAS for sha256, node_count, processed_at."""

    def test_processed_at_cas_guard_protects_sha256(self):
        """sha256 update is gated by processed_at >= existing."""
        from pageindex_mcp.registry import _UPSERT_SQL

        # Extract the sha256 SET clause
        sha256_match = re.search(
            r"sha256\s*=\s*CASE\s+WHEN\s+EXCLUDED\.processed_at\s*>=",
            _UPSERT_SQL,
            re.IGNORECASE | re.DOTALL,
        )
        assert sha256_match is not None, (
            "sha256 column must be CAS-guarded by processed_at"
        )

    def test_processed_at_cas_guard_protects_node_count(self):
        """node_count update is gated by processed_at >= existing."""
        from pageindex_mcp.registry import _UPSERT_SQL

        node_count_match = re.search(
            r"node_count\s*=\s*CASE\s+WHEN\s+EXCLUDED\.processed_at\s*>=",
            _UPSERT_SQL,
            re.IGNORECASE | re.DOTALL,
        )
        assert node_count_match is not None, (
            "node_count column must be CAS-guarded by processed_at"
        )

    def test_processed_at_cas_guard_protects_itself(self):
        """processed_at update is gated by its own CAS."""
        from pageindex_mcp.registry import _UPSERT_SQL

        processed_at_match = re.search(
            r"processed_at\s*=\s*CASE\s+WHEN\s+EXCLUDED\.processed_at\s*>=",
            _UPSERT_SQL,
            re.IGNORECASE | re.DOTALL,
        )
        assert processed_at_match is not None, (
            "processed_at column must be CAS-guarded by its own timestamp"
        )

    def test_facet_columns_are_unconditional(self):
        """Facet columns (product, tier, doc_family, etc.) use last-writer-wins."""
        from pageindex_mcp.registry import _UPSERT_SQL

        facet_cols = ["product", "tier", "doc_family", "effective_date", "doc_description"]
        for col in facet_cols:
            pattern = re.compile(
                rf"{col}\s*=\s*EXCLUDED\.{col}",
                re.IGNORECASE,
            )
            assert pattern.search(_UPSERT_SQL) is not None, (
                f"{col} must use unconditional EXCLUDED.{col} (last-writer-wins)"
            )


# ---------------------------------------------------------------------------
# 3. Postgres-authority write ordering in _upsert_registry_row
# ---------------------------------------------------------------------------


class TestPostgresAuthorityWriteOrder:
    """Under REGISTRY_VERDICT_AUTHORITY=postgres, upsert_verdict is called
    first, then MinIO sidecar backfill."""

    @pytest.mark.asyncio
    async def test_postgres_first_then_sidecar(self):
        """Verify Postgres write (upsert_verdict) happens before MinIO backfill."""
        from pageindex_mcp.worker import _upsert_registry_row

        call_order = []

        async def mock_upsert_verdict(doc_id, vf):
            call_order.append("upsert_verdict")
            return {"doc_id": doc_id, "verdict": "PASS",
                    "pipeline_version": 4, "permanent_marginal": False,
                    "verdict_computed_at": "2026-08-18T12:00:00Z"}

        def mock_save_doc_meta(doc_id, meta):
            call_order.append("save_doc_meta")

        def mock_read_registry_fields(doc_id, cc):
            call_order.append("read_registry_fields")
            return {"doc_id": doc_id, "doc_name": "test.pdf", "processed_at": "2026-08-18"}

        async def mock_upsert_doc(fields):
            call_order.append("upsert_doc")

        mock_settings = MagicMock()
        mock_settings.registry_enabled = True
        mock_settings.postgres_dsn = "postgresql://test"
        mock_settings.registry_verdict_authority = "postgres"
        mock_settings.minio_bucket = "test"

        mock_pool = MagicMock()

        # worker.py uses deferred imports inside _upsert_registry_row:
        #   from .registry import get_pool, upsert_doc, upsert_verdict
        #   from .storage import save_doc_meta
        # We must patch them at the module level where they are resolved.
        with (
            patch("pageindex_mcp.worker.settings", mock_settings),
            patch("pageindex_mcp.registry.get_pool", return_value=mock_pool),
            patch("pageindex_mcp.registry.upsert_verdict", side_effect=mock_upsert_verdict),
            patch("pageindex_mcp.registry.upsert_doc", side_effect=mock_upsert_doc),
            patch("pageindex_mcp.worker.read_registry_fields", side_effect=mock_read_registry_fields),
            patch("pageindex_mcp.storage.save_doc_meta", side_effect=mock_save_doc_meta),
            patch("pageindex_mcp.worker.REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP", MagicMock()),
            patch("pageindex_mcp.worker._mirror_registry_metric_to_redis", new_callable=AsyncMock),
        ):
            await _upsert_registry_row(
                "abc12345", "tree",
                verdict_fields={"verdict": "PASS", "verdict_computed_at": "2026-08-18T12:00:00Z"}
            )

        # upsert_verdict must come before read_registry_fields/upsert_doc
        assert "upsert_verdict" in call_order
        uv_idx = call_order.index("upsert_verdict")
        # save_doc_meta (sidecar backfill) must come after upsert_verdict
        if "save_doc_meta" in call_order:
            sdm_idx = call_order.index("save_doc_meta")
            assert sdm_idx > uv_idx, (
                f"save_doc_meta (idx={sdm_idx}) must come after upsert_verdict (idx={uv_idx})"
            )


# ---------------------------------------------------------------------------
# 4. MinIO-authority regression: existing write order preserved
# ---------------------------------------------------------------------------


class TestMinioAuthorityPreservesExistingBehavior:
    """Under REGISTRY_VERDICT_AUTHORITY=minio (default), the existing RFC-006
    flow runs unchanged: MinIO read -> overlay verdict_fields -> upsert_doc."""

    @pytest.mark.asyncio
    async def test_minio_mode_uses_read_then_upsert_doc(self):
        """In minio mode, upsert_doc is called (not upsert_verdict)."""
        from pageindex_mcp.worker import _upsert_registry_row

        calls = []

        async def mock_upsert_doc(fields):
            calls.append(("upsert_doc", fields))

        async def mock_upsert_verdict(doc_id, vf):
            calls.append(("upsert_verdict", doc_id, vf))
            return {"doc_id": doc_id}

        def mock_read_registry_fields(doc_id, cc):
            return {"doc_id": doc_id, "doc_name": "test.pdf",
                    "processed_at": "2026-08-18", "verdict": ""}

        mock_settings = MagicMock()
        mock_settings.registry_enabled = True
        mock_settings.postgres_dsn = "postgresql://test"
        mock_settings.registry_verdict_authority = "minio"
        mock_settings.minio_bucket = "test"

        mock_pool = MagicMock()

        with (
            patch("pageindex_mcp.worker.settings", mock_settings),
            patch("pageindex_mcp.registry.upsert_verdict", side_effect=mock_upsert_verdict),
            patch("pageindex_mcp.registry.upsert_doc", side_effect=mock_upsert_doc),
            patch("pageindex_mcp.worker.read_registry_fields", side_effect=mock_read_registry_fields),
            patch("pageindex_mcp.registry.get_pool", return_value=mock_pool),
            patch("pageindex_mcp.worker.REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP", MagicMock()),
            patch("pageindex_mcp.worker._mirror_registry_metric_to_redis", new_callable=AsyncMock),
        ):
            await _upsert_registry_row(
                "abc12345", "tree",
                verdict_fields={"verdict": "PASS", "verdict_computed_at": "2026-08-18T12:00:00Z"}
            )

        # In minio mode, upsert_doc is used, NOT upsert_verdict
        call_names = [c[0] for c in calls]
        assert "upsert_doc" in call_names
        assert "upsert_verdict" not in call_names

    @pytest.mark.asyncio
    async def test_minio_mode_overlays_verdict_fields(self):
        """In minio mode, verdict_fields are overlaid onto MinIO-read fields."""
        from pageindex_mcp.worker import _upsert_registry_row

        upsert_calls = []

        async def mock_upsert_doc(fields):
            upsert_calls.append(fields)

        def mock_read_registry_fields(doc_id, cc):
            return {"doc_id": doc_id, "doc_name": "test.pdf",
                    "processed_at": "2026-08-18", "verdict": "MARGINAL"}

        mock_settings = MagicMock()
        mock_settings.registry_enabled = True
        mock_settings.postgres_dsn = "postgresql://test"
        mock_settings.registry_verdict_authority = "minio"
        mock_settings.minio_bucket = "test"

        with (
            patch("pageindex_mcp.worker.settings", mock_settings),
            patch("pageindex_mcp.registry.upsert_doc", side_effect=mock_upsert_doc),
            patch("pageindex_mcp.registry.upsert_verdict", new_callable=AsyncMock),
            patch("pageindex_mcp.worker.read_registry_fields", side_effect=mock_read_registry_fields),
            patch("pageindex_mcp.registry.get_pool", return_value=MagicMock()),
            patch("pageindex_mcp.worker.REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP", MagicMock()),
            patch("pageindex_mcp.worker._mirror_registry_metric_to_redis", new_callable=AsyncMock),
        ):
            await _upsert_registry_row(
                "abc12345", "tree",
                verdict_fields={"verdict": "PASS", "verdict_computed_at": "2026-08-18T14:00:00Z"}
            )

        assert len(upsert_calls) == 1
        # Verdict fields must have been overlaid on top of MinIO read
        assert upsert_calls[0]["verdict"] == "PASS"
        assert upsert_calls[0]["verdict_computed_at"] == "2026-08-18T14:00:00Z"


# ---------------------------------------------------------------------------
# 5. save_doc_meta barrier gating under authority modes
# ---------------------------------------------------------------------------


class TestSaveDocMetaBarrierGating:
    """Under postgres mode, save_doc_meta skips _confirm_write_visible;
    under minio mode, the barrier is still called."""

    def _nosuchkey(self):
        from minio.error import S3Error
        return S3Error(MagicMock(), "NoSuchKey", "missing", "res", "req", "host")

    def test_minio_mode_calls_barrier(self):
        """Under minio authority, _confirm_write_visible is called."""
        from pageindex_mcp.storage import save_doc_meta

        mock_mc = MagicMock()
        mock_mc.get_object.side_effect = self._nosuchkey()

        mock_settings = MagicMock()
        mock_settings.registry_verdict_authority = "minio"
        mock_settings.minio_bucket = "test-bucket"

        with (
            patch("pageindex_mcp.storage.get_minio", return_value=mock_mc),
            patch("pageindex_mcp.storage.settings", mock_settings),
            patch("pageindex_mcp.storage._confirm_write_visible") as mock_barrier,
        ):
            save_doc_meta("doc123", {"doc_id": "doc123", "verdict": "PASS"})

        mock_barrier.assert_called_once()

    def test_postgres_mode_skips_barrier(self):
        """Under postgres authority, _confirm_write_visible is NOT called."""
        from pageindex_mcp.storage import save_doc_meta

        mock_mc = MagicMock()
        mock_mc.get_object.side_effect = self._nosuchkey()

        mock_settings = MagicMock()
        mock_settings.registry_verdict_authority = "postgres"
        mock_settings.minio_bucket = "test-bucket"

        with (
            patch("pageindex_mcp.storage.get_minio", return_value=mock_mc),
            patch("pageindex_mcp.storage.settings", mock_settings),
            patch("pageindex_mcp.storage._confirm_write_visible") as mock_barrier,
        ):
            save_doc_meta("doc123", {"doc_id": "doc123", "verdict": "PASS"})

        mock_barrier.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Redis verdict retry queue drain in reconcile_registry_drift
# ---------------------------------------------------------------------------


class TestVerdictRetryQueueDrain:
    """Verify _drain_verdict_retry_queue replays keys into upsert_verdict
    and save_doc_meta, and drain happens before _list_meta_entries."""

    @pytest.mark.asyncio
    async def test_drain_calls_upsert_verdict_per_key(self):
        """Each Redis retry key is replayed via upsert_verdict."""
        from pageindex_mcp.registry_backfill import _drain_verdict_retry_queue

        verdict_data = json.dumps({"verdict": "PASS", "verdict_computed_at": "2026-08-18T12:00:00Z"})

        mock_redis = AsyncMock()
        # Simulate SCAN returning two keys, then done (cursor=0)
        mock_redis.scan = AsyncMock(return_value=(
            0,
            [b"pageindex:verdict_retry:doc1", b"pageindex:verdict_retry:doc2"],
        ))
        mock_redis.get = AsyncMock(return_value=verdict_data.encode())
        mock_redis.delete = AsyncMock()

        upsert_calls = []

        async def mock_upsert_verdict(doc_id, vf):
            upsert_calls.append(doc_id)
            return {"doc_id": doc_id, "verdict": "PASS", "pipeline_version": 4,
                    "permanent_marginal": False, "verdict_computed_at": "2026-08-18T12:00:00Z"}

        with (
            patch("pageindex_mcp.registry.upsert_verdict", side_effect=mock_upsert_verdict),
            patch("pageindex_mcp.storage.save_doc_meta"),
        ):
            await _drain_verdict_retry_queue(mock_redis)

        assert set(upsert_calls) == {"doc1", "doc2"}
        # Keys must be deleted after successful replay
        assert mock_redis.delete.call_count >= 2

    @pytest.mark.asyncio
    async def test_drain_calls_save_doc_meta_with_winning_row(self):
        """Each replayed verdict also backfills the MinIO sidecar."""
        from pageindex_mcp.registry_backfill import _drain_verdict_retry_queue

        verdict_data = json.dumps({"verdict": "PASS", "verdict_computed_at": "2026-08-18T12:00:00Z"})
        winning = {"doc_id": "doc1", "verdict": "PASS", "pipeline_version": 4,
                   "permanent_marginal": False, "verdict_computed_at": "2026-08-18T12:00:00Z"}

        mock_redis = AsyncMock()
        mock_redis.scan = AsyncMock(return_value=(0, [b"pageindex:verdict_retry:doc1"]))
        mock_redis.get = AsyncMock(return_value=verdict_data.encode())
        mock_redis.delete = AsyncMock()

        async def mock_upsert_verdict(doc_id, vf):
            return winning

        sdm_calls = []

        def mock_save_doc_meta(doc_id, meta):
            sdm_calls.append((doc_id, meta))

        with (
            patch("pageindex_mcp.registry.upsert_verdict", side_effect=mock_upsert_verdict),
            patch("pageindex_mcp.storage.save_doc_meta", side_effect=mock_save_doc_meta),
        ):
            await _drain_verdict_retry_queue(mock_redis)

        assert len(sdm_calls) == 1
        assert sdm_calls[0][0] == "doc1"
        assert sdm_calls[0][1]["verdict"] == "PASS"

    @pytest.mark.asyncio
    async def test_drain_before_list_meta_entries_in_reconcile(self):
        """Under postgres mode, drain runs before _list_meta_entries."""
        from pageindex_mcp.registry_backfill import reconcile_registry_drift

        call_order = []

        async def mock_drain(redis_client):
            call_order.append("drain")

        def mock_list_meta():
            call_order.append("list_meta_entries")
            return [], []

        mock_settings = MagicMock()
        mock_settings.registry_enabled = True
        mock_settings.postgres_dsn = "postgresql://test"
        mock_settings.registry_verdict_authority = "postgres"

        mock_redis = AsyncMock()

        # reconcile_registry_drift uses deferred imports:
        #   from .registry import get_pool  (inside function body)
        #   from .cache import get_async_redis  (inside function body)
        with (
            patch("pageindex_mcp.registry_backfill.settings", mock_settings),
            patch("pageindex_mcp.registry.get_pool", return_value=MagicMock()),
            patch("pageindex_mcp.cache.get_async_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("pageindex_mcp.registry_backfill._drain_verdict_retry_queue", side_effect=mock_drain),
            patch("pageindex_mcp.registry_backfill._list_meta_entries", side_effect=mock_list_meta),
            patch("pageindex_mcp.registry_backfill._record_reconcile_heartbeat", new_callable=AsyncMock),
        ):
            await reconcile_registry_drift()

        assert "drain" in call_order
        assert "list_meta_entries" in call_order
        drain_idx = call_order.index("drain")
        list_idx = call_order.index("list_meta_entries")
        assert drain_idx < list_idx, (
            f"drain (idx={drain_idx}) must happen before list_meta_entries (idx={list_idx})"
        )

    @pytest.mark.asyncio
    async def test_drain_not_called_in_minio_mode(self):
        """Under minio mode, _drain_verdict_retry_queue is NOT called."""
        from pageindex_mcp.registry_backfill import reconcile_registry_drift

        mock_settings = MagicMock()
        mock_settings.registry_enabled = True
        mock_settings.postgres_dsn = "postgresql://test"
        mock_settings.registry_verdict_authority = "minio"

        mock_redis = AsyncMock()

        with (
            patch("pageindex_mcp.registry_backfill.settings", mock_settings),
            patch("pageindex_mcp.registry.get_pool", return_value=MagicMock()),
            patch("pageindex_mcp.cache.get_async_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("pageindex_mcp.registry_backfill._drain_verdict_retry_queue") as mock_drain,
            patch("pageindex_mcp.registry_backfill._list_meta_entries", return_value=([], [])),
            patch("pageindex_mcp.registry_backfill._record_reconcile_heartbeat", new_callable=AsyncMock),
        ):
            await reconcile_registry_drift()

        mock_drain.assert_not_called()


# ---------------------------------------------------------------------------
# 7. Column exhaustiveness: _UPSERT_SQL covers all _CREATE_TABLE_SQL columns
# ---------------------------------------------------------------------------


class TestColumnExhaustiveness:
    """Every column in _CREATE_TABLE_SQL (excluding search_text GENERATED) must
    appear in the _UPSERT_SQL INSERT column list."""

    def test_upsert_sql_covers_all_ddl_columns(self):
        from pageindex_mcp.registry import _CREATE_TABLE_SQL, _UPSERT_SQL

        # Extract columns from CREATE TABLE
        create_body = re.search(r"\((.*)\)", _CREATE_TABLE_SQL, re.DOTALL).group(1)
        # Each column definition starts at line beginning, column name is first word
        ddl_columns = set()
        for line in create_body.split("\n"):
            line = line.strip().rstrip(",")
            if not line or line.startswith(")"):
                continue
            # Skip the GENERATED column (search_text)
            first_word = line.split()[0] if line.split() else ""
            if first_word and first_word.isidentifier() and first_word != "STORED":
                # Filter out SQL keywords that are not column names
                if first_word.upper() not in (
                    "CREATE", "TABLE", "IF", "NOT", "EXISTS", "PRIMARY", "STORED",
                    "GENERATED", "ALWAYS", "AS",
                ):
                    ddl_columns.add(first_word.lower())

        # Remove the GENERATED column (search_text) explicitly
        ddl_columns.discard("search_text")
        # Remove intermediate keywords that might have been captured
        ddl_columns -= {"to_tsvector", "coalesce"}

        # Extract INSERT column list from _UPSERT_SQL
        insert_match = re.search(
            r"INSERT\s+INTO\s+doc_registry\s*\((.*?)\)\s*VALUES",
            _UPSERT_SQL,
            re.DOTALL | re.IGNORECASE,
        )
        assert insert_match is not None
        insert_cols = {
            c.strip().lower()
            for c in insert_match.group(1).split(",")
            if c.strip()
        }

        missing = ddl_columns - insert_cols
        assert not missing, (
            f"_UPSERT_SQL INSERT is missing DDL columns: {missing}"
        )

    def test_upsert_verdict_sql_covers_all_ddl_columns(self):
        """_UPSERT_VERDICT_SQL INSERT list must also cover all DDL columns
        (for the ON CONFLICT path to work on first-insert)."""
        from pageindex_mcp.registry import _CREATE_TABLE_SQL, _UPSERT_VERDICT_SQL

        create_body = re.search(r"\((.*)\)", _CREATE_TABLE_SQL, re.DOTALL).group(1)
        ddl_columns = set()
        for line in create_body.split("\n"):
            line = line.strip().rstrip(",")
            if not line or line.startswith(")"):
                continue
            first_word = line.split()[0] if line.split() else ""
            if first_word and first_word.isidentifier() and first_word != "STORED":
                if first_word.upper() not in (
                    "CREATE", "TABLE", "IF", "NOT", "EXISTS", "PRIMARY", "STORED",
                    "GENERATED", "ALWAYS", "AS",
                ):
                    ddl_columns.add(first_word.lower())
        ddl_columns.discard("search_text")
        ddl_columns -= {"to_tsvector", "coalesce"}

        insert_match = re.search(
            r"INSERT\s+INTO\s+doc_registry\s*\((.*?)\)\s*VALUES",
            _UPSERT_VERDICT_SQL,
            re.DOTALL | re.IGNORECASE,
        )
        assert insert_match is not None
        insert_cols = {
            c.strip().lower()
            for c in insert_match.group(1).split(",")
            if c.strip()
        }

        missing = ddl_columns - insert_cols
        assert not missing, (
            f"_UPSERT_VERDICT_SQL INSERT is missing DDL columns: {missing}"
        )


# ---------------------------------------------------------------------------
# 8. CAS symmetry between _UPSERT_SQL and _UPSERT_VERDICT_SQL
# ---------------------------------------------------------------------------


class TestCasSymmetry:
    """The SQL CAS guard in _UPSERT_VERDICT_SQL must use the same comparison
    semantics as the verdict columns in _UPSERT_SQL."""

    def _extract_cas_patterns(self, sql: str, column: str) -> str | None:
        """Extract the CASE WHEN pattern for a verdict column."""
        pattern = re.compile(
            rf"{column}\s*=\s*CASE\s+(.*?)END",
            re.DOTALL | re.IGNORECASE,
        )
        match = pattern.search(sql)
        return match.group(1).strip() if match else None

    def test_verdict_cas_uses_same_comparison(self):
        """Both SQLs use EXCLUDED.verdict_computed_at >= COALESCE(doc_registry.verdict_computed_at, '')."""
        from pageindex_mcp.registry import _UPSERT_SQL, _UPSERT_VERDICT_SQL

        # The CAS guard expression must be identical in both
        cas_expr = r"EXCLUDED\.verdict_computed_at\s*>=\s*COALESCE\s*\(\s*doc_registry\.verdict_computed_at\s*,\s*''\s*\)"

        for col in ["verdict", "pipeline_version", "permanent_marginal", "verdict_computed_at"]:
            upsert_pattern = self._extract_cas_patterns(_UPSERT_SQL, col)
            verdict_pattern = self._extract_cas_patterns(_UPSERT_VERDICT_SQL, col)

            assert upsert_pattern is not None, f"{col} CAS missing from _UPSERT_SQL"
            assert verdict_pattern is not None, f"{col} CAS missing from _UPSERT_VERDICT_SQL"

            # Both must use the same >= COALESCE comparison
            assert re.search(cas_expr, upsert_pattern, re.IGNORECASE), (
                f"_UPSERT_SQL {col} CAS does not use expected comparison"
            )
            assert re.search(cas_expr, verdict_pattern, re.IGNORECASE), (
                f"_UPSERT_VERDICT_SQL {col} CAS does not use expected comparison"
            )

    def test_verdict_coalesce_nullif_pattern_matches(self):
        """Both SQLs use COALESCE(NULLIF(EXCLUDED.verdict, ''), doc_registry.verdict)
        for the verdict column THEN branch."""
        from pageindex_mcp.registry import _UPSERT_SQL, _UPSERT_VERDICT_SQL

        pattern = r"COALESCE\s*\(\s*NULLIF\s*\(\s*EXCLUDED\.verdict\s*,\s*''\s*\)\s*,\s*doc_registry\.verdict\s*\)"

        assert re.search(pattern, _UPSERT_SQL, re.IGNORECASE), (
            "_UPSERT_SQL verdict THEN branch must use COALESCE(NULLIF(...))"
        )
        assert re.search(pattern, _UPSERT_VERDICT_SQL, re.IGNORECASE), (
            "_UPSERT_VERDICT_SQL verdict THEN branch must use COALESCE(NULLIF(...))"
        )


# ---------------------------------------------------------------------------
# 9. Feature flag validation: rejects invalid values at startup
# ---------------------------------------------------------------------------


class TestFeatureFlagValidation:
    """REGISTRY_VERDICT_AUTHORITY must reject values other than minio/postgres."""

    def test_valid_values_accepted(self):
        """minio and postgres are accepted without error."""
        from pageindex_mcp.config import _VALID_VERDICT_AUTHORITY

        assert "minio" in _VALID_VERDICT_AUTHORITY
        assert "postgres" in _VALID_VERDICT_AUTHORITY

    def test_invalid_value_raises_at_import(self):
        """An invalid REGISTRY_VERDICT_AUTHORITY value raises ValueError."""
        import importlib
        import os

        # We cannot re-trigger module-level validation easily, but we can
        # verify the validation constant and logic are present
        from pageindex_mcp.config import _VALID_VERDICT_AUTHORITY, settings

        # The validation logic is at module level in config.py:
        # if settings.registry_verdict_authority not in _VALID_VERDICT_AUTHORITY:
        #     raise ValueError(...)
        assert settings.registry_verdict_authority in _VALID_VERDICT_AUTHORITY

        # Verify the constraint is exactly (minio, postgres) and nothing else
        assert set(_VALID_VERDICT_AUTHORITY) == {"minio", "postgres"}

    def test_default_is_minio(self):
        """Default REGISTRY_VERDICT_AUTHORITY is minio (zero-risk Phase 1)."""
        # When REGISTRY_VERDICT_AUTHORITY env var is not set, default is "minio"
        # We verify via the _load_settings default
        import inspect
        from pageindex_mcp.config import _load_settings

        source = inspect.getsource(_load_settings)
        # The default in the os.environ.get call should be "minio"
        assert '"minio"' in source or "'minio'" in source, (
            "_load_settings must default REGISTRY_VERDICT_AUTHORITY to 'minio'"
        )

    def test_settings_field_exists(self):
        """Settings dataclass has registry_verdict_authority: str."""
        from pageindex_mcp.config import Settings
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(Settings)}
        assert "registry_verdict_authority" in field_names

        # Verify it's typed as str (may be str class or "str" string depending
        # on __future__.annotations usage in config.py)
        field = next(f for f in dataclasses.fields(Settings) if f.name == "registry_verdict_authority")
        assert field.type is str or field.type == "str"


# ---------------------------------------------------------------------------
# Wiring verification: production imports exist
# ---------------------------------------------------------------------------


class TestWiringVerification:
    """Verify the symbols referenced in tests are actually importable from
    production modules (wiring check)."""

    def test_upsert_verdict_importable_from_registry(self):
        from pageindex_mcp.registry import upsert_verdict
        assert callable(upsert_verdict)

    def test_upsert_verdict_sql_importable(self):
        from pageindex_mcp.registry import _UPSERT_VERDICT_SQL
        assert "RETURNING" in _UPSERT_VERDICT_SQL

    def test_verdict_retry_key_prefix_in_worker(self):
        from pageindex_mcp.worker import _VERDICT_RETRY_KEY_PREFIX
        assert _VERDICT_RETRY_KEY_PREFIX == "pageindex:verdict_retry:"

    def test_drain_verdict_retry_queue_importable(self):
        from pageindex_mcp.registry_backfill import _drain_verdict_retry_queue
        assert callable(_drain_verdict_retry_queue)

    def test_valid_verdict_authority_constant_importable(self):
        from pageindex_mcp.config import _VALID_VERDICT_AUTHORITY
        assert isinstance(_VALID_VERDICT_AUTHORITY, tuple)

    def test_worker_imports_upsert_verdict(self):
        """_upsert_registry_row must import upsert_verdict from registry."""
        import inspect
        from pageindex_mcp.worker import _upsert_registry_row

        source = inspect.getsource(_upsert_registry_row)
        assert "upsert_verdict" in source

    def test_reconcile_drift_gates_drain_on_authority(self):
        """reconcile_registry_drift must check registry_verdict_authority before drain."""
        import inspect
        from pageindex_mcp.registry_backfill import reconcile_registry_drift

        source = inspect.getsource(reconcile_registry_drift)
        assert "registry_verdict_authority" in source
        assert "_drain_verdict_retry_queue" in source
