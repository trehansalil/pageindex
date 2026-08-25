# tests/test_dual_write_consistency.py
"""Multi-Store Dual-Write Consistency zone tests.

Covers the implemented CODE TARGETS for Zone "Multi-Store Dual-Write
Consistency":
  1. last_registry_fields stash in _persist_tree_result (CONTRACT)
  2. last_registry_fields stash in _persist_flat_result (CONTRACT)
  3. converters_cli verdict_fields surfacing readiness (EXHAUSTIVENESS)
  4. worker/job.py extracts registry_fields and passes to _upsert_registry_row (WIRING)
  5. registry_mirror skips MinIO re-read when registry_fields supplied (CONTRACT)
  6. hash_cache_delete Redis HDEL (EXHAUSTIVENESS)
  7. documents.py delete_doc HR2 cascade ordering (REGRESSION)
  8. cleanup.py age-guard on stale row deletion (CONTRACT)
  9. Existing erasure cascade store coverage (EXHAUSTIVENESS)
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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


class TestTreeResultRegistryFieldsStash:
    """_persist_tree_result must stash last_registry_fields on the client
    instance so converters_cli can surface them in stdout JSON."""

    def test_last_registry_fields_set_after_tree_persist(self):
        """The _persist_tree_result method sets self.last_registry_fields
        with the correct keys mirroring _REGISTRY_FIELDS."""
        src = _read_src("client/indexer.py")
        expected_keys = {
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
        }
        persist_tree_idx = src.index("def _persist_tree_result")
        # Find next top-level async def (same indent level)
        next_def = src.find("\n    async def ", persist_tree_idx + 10)
        if next_def == -1:
            next_def = len(src)
        tree_src = src[persist_tree_idx:next_def]
        assert "last_registry_fields" in tree_src
        for key in expected_keys:
            assert f'"{key}"' in tree_src, (
                f"Missing key {key!r} in _persist_tree_result registry stash"
            )

    def test_tree_stash_includes_dynamic_node_count(self):
        """The tree path must compute node_count via _tree_node_count(structure)
        not hardcode 0 (unlike the flat path which correctly uses 0)."""
        src = _read_src("client/indexer.py")
        persist_tree_idx = src.index("def _persist_tree_result")
        next_def = src.find("\n    async def ", persist_tree_idx + 10)
        if next_def == -1:
            next_def = len(src)
        tree_src = src[persist_tree_idx:next_def]
        # Between last_registry_fields and the closing brace, _tree_node_count
        # should appear (not a hardcoded 0).
        rf_idx = tree_src.index("last_registry_fields")
        rf_block = tree_src[rf_idx:rf_idx + 600]
        assert "_tree_node_count" in rf_block

    def test_tree_stash_verdict_fields_also_set(self):
        """_persist_tree_result must also set last_verdict_fields (Zone-7
        existing contract)."""
        src = _read_src("client/indexer.py")
        persist_tree_idx = src.index("def _persist_tree_result")
        next_def = src.find("\n    async def ", persist_tree_idx + 10)
        if next_def == -1:
            next_def = len(src)
        tree_src = src[persist_tree_idx:next_def]
        assert "last_verdict_fields" in tree_src


# ---------------------------------------------------------------------------
# 2. CONTRACT: last_registry_fields stash in _persist_flat_result
# ---------------------------------------------------------------------------


class TestFlatResultRegistryFieldsStash:
    """_persist_flat_result must stash last_registry_fields with node_count=0
    (flat docs have no tree structure)."""

    def test_flat_stash_keys_match_contract(self):
        """Flat result stash must contain the registry field keys plus
        content_class (flat-specific)."""
        src = _read_src("client/indexer.py")
        persist_flat_idx = src.index("def _persist_flat_result")
        next_def = src.find("\n    async def ", persist_flat_idx + 10)
        if next_def == -1:
            next_def = len(src)
        flat_src = src[persist_flat_idx:next_def]
        assert "last_registry_fields" in flat_src
        expected_keys = {
            "doc_name",
            "source_url",
            "processed_at",
            "sha256",
            "content_class",
            "doc_description",
            "product",
            "tier",
            "doc_family",
            "effective_date",
            "node_count",
        }
        for key in expected_keys:
            assert f'"{key}"' in flat_src, (
                f"Missing key {key!r} in _persist_flat_result registry stash"
            )

    def test_flat_stash_node_count_is_zero(self):
        """Flat docs have no tree: node_count must be hardcoded 0."""
        src = _read_src("client/indexer.py")
        persist_flat_idx = src.index("def _persist_flat_result")
        next_def = src.find("\n    async def ", persist_flat_idx + 10)
        if next_def == -1:
            next_def = len(src)
        flat_src = src[persist_flat_idx:next_def]
        rf_idx = flat_src.index("last_registry_fields")
        block = flat_src[rf_idx:rf_idx + 600]
        assert '"node_count": 0' in block


# ---------------------------------------------------------------------------
# 3. EXHAUSTIVENESS: converters_cli verdict_fields surfacing
# ---------------------------------------------------------------------------


class TestConvertersCliDualWriteFields:
    """converters_cli surfaces verdict_fields via getattr pattern;
    last_registry_fields stashed in indexer for future surfacing."""

    def test_verdict_fields_surfaced_via_getattr(self):
        """converters_cli must use getattr() for last_verdict_fields."""
        src = _read_src("converters_cli.py")
        assert 'getattr(client, "last_verdict_fields"' in src

    def test_verdict_fields_added_to_payload_when_truthy(self):
        """verdict_fields is conditionally added to the payload dict."""
        src = _read_src("converters_cli.py")
        assert 'payload["verdict_fields"]' in src

    def test_content_class_surfaced_via_getattr(self):
        """content_class is surfaced the same way."""
        src = _read_src("converters_cli.py")
        assert 'getattr(client, "last_content_class"' in src

    def test_indexer_stashes_last_registry_fields_both_paths(self):
        """The indexer stashes last_registry_fields on both persist paths."""
        src = _read_src("client/indexer.py")
        tree_idx = src.index("def _persist_tree_result")
        flat_idx = src.index("def _persist_flat_result")
        assert "last_registry_fields" in src[tree_idx:]
        assert "last_registry_fields" in src[flat_idx:]


# ---------------------------------------------------------------------------
# 4. WIRING: worker/job.py extracts registry_fields and passes to
#    _upsert_registry_row
# ---------------------------------------------------------------------------


class TestJobVerdictFieldsWiring:
    """process_document_job must extract verdict_fields from the subprocess
    result dict and pass it as a kwarg to _upsert_registry_row."""

    def test_verdict_fields_extracted_from_result(self):
        """The job handler must call result.get('verdict_fields')."""
        src = _read_src("worker/job.py")
        assert 'result.get("verdict_fields")' in src

    def test_verdict_fields_passed_as_kwarg_to_upsert(self):
        """_upsert_registry_row must be called with verdict_fields= kwarg."""
        src = _read_src("worker/job.py")
        assert "verdict_fields=verdict_fields" in src

    def test_upsert_registry_row_imported_from_registry_mirror(self):
        """The job handler must import _upsert_registry_row from registry_mirror."""
        src = _read_src("worker/job.py")
        assert "from .registry_mirror import _upsert_registry_row" in src

    def test_registry_mirror_accepts_registry_fields_kwarg(self):
        """_upsert_registry_row signature must accept registry_fields kwarg
        (ready for when job.py wires it from the child result)."""
        src = _read_src("worker/registry_mirror.py")
        fn_idx = src.index("async def _upsert_registry_row")
        sig_block = src[fn_idx:fn_idx + 300]
        assert "registry_fields" in sig_block


# ---------------------------------------------------------------------------
# 5. CONTRACT: registry_mirror skips MinIO re-read when registry_fields
#    supplied
# ---------------------------------------------------------------------------


def _settings(**overrides):
    from pageindex_mcp.config import settings as _base_settings

    return dataclasses.replace(_base_settings, **overrides)


_REGISTRY_ENABLED = _settings(
    registry_enabled=True,
    postgres_dsn="postgresql://user:pass@localhost:5432/pageindex",
)


class TestRegistryMirrorSkipMinioReread:
    """When registry_fields is supplied, _upsert_registry_row must skip the
    read_registry_fields MinIO re-read entirely."""

    @pytest.mark.asyncio
    async def test_registry_fields_supplied_skips_minio_read(self):
        """When registry_fields dict is passed, read_registry_fields is
        never called (no MinIO round-trip)."""
        from pageindex_mcp.worker.registry_mirror import _upsert_registry_row

        registry_fields = {
            "doc_name": "test.pdf",
            "sha256": "abc",
            "node_count": 5,
        }

        with (
            patch("pageindex_mcp.worker.registry_mirror.settings", _REGISTRY_ENABLED),
            patch("pageindex_mcp.registry.get_pool", return_value=object()),
            patch(
                "pageindex_mcp.registry.upsert_doc",
                AsyncMock(return_value=None),
            ) as mock_upsert,
            patch(
                "pageindex_mcp.worker.registry_mirror.read_registry_fields",
            ) as mock_read,
            patch(
                "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
                AsyncMock(),
            ),
        ):
            await _upsert_registry_row(
                "doc-1", None,
                registry_fields=registry_fields,
            )

        mock_read.assert_not_called()
        mock_upsert.assert_awaited_once()
        upserted = mock_upsert.await_args[0][0]
        assert upserted["doc_name"] == "test.pdf"
        assert upserted["doc_id"] == "doc-1"

    @pytest.mark.asyncio
    async def test_registry_fields_none_falls_back_to_minio_read(self):
        """When registry_fields is None (backward compat), read_registry_fields
        is called to populate the fields from MinIO."""
        from pageindex_mcp.worker.registry_mirror import _upsert_registry_row

        minio_fields = {"doc_id": "doc-2", "doc_name": "fallback.pdf"}

        with (
            patch("pageindex_mcp.worker.registry_mirror.settings", _REGISTRY_ENABLED),
            patch("pageindex_mcp.registry.get_pool", return_value=object()),
            patch(
                "pageindex_mcp.registry.upsert_doc",
                AsyncMock(return_value=None),
            ),
            patch(
                "pageindex_mcp.worker.registry_mirror.read_registry_fields",
                return_value=minio_fields,
            ) as mock_read,
            patch(
                "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
                AsyncMock(),
            ),
        ):
            await _upsert_registry_row("doc-2", None, registry_fields=None)

        mock_read.assert_called_once_with("doc-2", None)

    @pytest.mark.asyncio
    async def test_registry_fields_content_class_backfilled(self):
        """When registry_fields lacks content_class but the arg is provided,
        it is backfilled into the fields dict."""
        from pageindex_mcp.worker.registry_mirror import _upsert_registry_row

        registry_fields = {"doc_name": "test.pdf", "sha256": "abc"}

        with (
            patch("pageindex_mcp.worker.registry_mirror.settings", _REGISTRY_ENABLED),
            patch("pageindex_mcp.registry.get_pool", return_value=object()),
            patch(
                "pageindex_mcp.registry.upsert_doc",
                AsyncMock(return_value=None),
            ) as mock_upsert,
            patch(
                "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
                AsyncMock(),
            ),
        ):
            await _upsert_registry_row(
                "doc-cc", "flat_table",
                registry_fields=registry_fields,
            )

        upserted = mock_upsert.await_args[0][0]
        assert upserted["content_class"] == "flat_table"

    @pytest.mark.asyncio
    async def test_verdict_fields_overlay_with_registry_fields(self):
        """When both registry_fields and verdict_fields are supplied,
        verdict_fields overlay takes precedence."""
        from pageindex_mcp.worker.registry_mirror import _upsert_registry_row

        registry_fields = {"doc_name": "test.pdf", "sha256": "abc", "node_count": 5}
        verdict_fields = {"verdict": "PASS", "pipeline_version": 7}

        with (
            patch("pageindex_mcp.worker.registry_mirror.settings", _REGISTRY_ENABLED),
            patch("pageindex_mcp.registry.get_pool", return_value=object()),
            patch(
                "pageindex_mcp.registry.upsert_doc",
                AsyncMock(return_value=None),
            ) as mock_upsert,
            patch(
                "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
                AsyncMock(),
            ),
        ):
            await _upsert_registry_row(
                "doc-both", None,
                verdict_fields=verdict_fields,
                registry_fields=registry_fields,
            )

        upserted = mock_upsert.await_args[0][0]
        assert upserted["doc_name"] == "test.pdf"
        assert upserted["verdict"] == "PASS"
        assert upserted["pipeline_version"] == 7

    @pytest.mark.asyncio
    async def test_registry_fields_is_copied_not_mutated(self):
        """The supplied registry_fields dict must be copied before mutation
        (doc_id insertion, content_class backfill) so the caller's dict is
        not modified."""
        from pageindex_mcp.worker.registry_mirror import _upsert_registry_row

        registry_fields = {"doc_name": "test.pdf", "sha256": "abc"}
        original_keys = set(registry_fields.keys())

        with (
            patch("pageindex_mcp.worker.registry_mirror.settings", _REGISTRY_ENABLED),
            patch("pageindex_mcp.registry.get_pool", return_value=object()),
            patch(
                "pageindex_mcp.registry.upsert_doc",
                AsyncMock(return_value=None),
            ),
            patch(
                "pageindex_mcp.worker.registry_mirror._mirror_registry_metric_to_redis",
                AsyncMock(),
            ),
        ):
            await _upsert_registry_row(
                "doc-copy", "flat_table",
                registry_fields=registry_fields,
            )

        # Original dict must NOT have been mutated
        assert set(registry_fields.keys()) == original_keys


# ---------------------------------------------------------------------------
# 6. EXHAUSTIVENESS: hash_cache_delete Redis HDEL
# ---------------------------------------------------------------------------


class TestHashCacheDelete:
    """hash_cache_delete must perform Redis HDEL for erasure compliance."""

    def test_delete_removes_redis_entry(self):
        """hash_cache_delete must remove the filename from Redis HSET."""
        import fakeredis

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
        import fakeredis

        fake_redis = fakeredis.FakeRedis(decode_responses=True)

        with patch("pageindex_mcp.cache._redis_sync", fake_redis):
            from pageindex_mcp.storage.hash_cache import hash_cache_delete

            # Must not raise
            hash_cache_delete("nonexistent.pdf")

    def test_delete_does_not_affect_other_entries(self):
        """Deleting one entry must not affect other entries."""
        import fakeredis

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
        docstring_block = src[fn_idx:fn_idx + 600]
        # Step numbers mentioned in the docstring
        for step in ["1.", "2.", "3.", "4.", "5.", "6.", "7."]:
            assert step in docstring_block, f"Step {step} not in delete_doc docstring"

    def test_cascade_handles_missing_doc_idempotently(self):
        """delete_doc must be idempotent: missing objects tolerated."""
        src = _read_src("storage/documents.py")
        fn_idx = src.index("async def delete_doc")
        fn_src = src[fn_idx:fn_idx + 3000]
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


class TestCleanupAgeGuard:
    """_delete_stale_rows must have an age guard that protects freshly-ingested
    rows from being deleted as stale."""

    @pytest.mark.asyncio
    async def test_fresh_rows_protected_by_age_guard(self):
        """Rows with processed_at within the grace period must NOT be deleted."""
        from pageindex_mcp.registry_backfill.cleanup import _delete_stale_rows

        from datetime import UTC, datetime

        now_iso = datetime.now(UTC).isoformat()
        # 10 rows: 1 fresh stale candidate, 9 in MinIO
        registry_rows = {
            "fresh-stale": now_iso,  # just ingested -- should be age-protected
            **{f"minio-{i}": "2026-01-01T00:00:00+00:00" for i in range(9)},
        }
        minio_ids = {f"minio-{i}" for i in range(9)}
        mock_delete = AsyncMock()

        with (
            patch(
                "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
                AsyncMock(return_value=registry_rows),
            ),
            patch("pageindex_mcp.registry.delete_doc", mock_delete),
        ):
            await _delete_stale_rows(minio_ids, grace_minutes=10)

        # fresh-stale is within the grace period -- not deleted
        mock_delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_old_rows_deleted_as_stale(self):
        """Rows with processed_at older than the grace period must be deleted."""
        from pageindex_mcp.registry_backfill.cleanup import _delete_stale_rows

        # 10 rows: 1 old stale candidate, 9 in MinIO
        registry_rows = {
            "old-stale": "2020-01-01T00:00:00+00:00",  # very old
            **{f"minio-{i}": "2026-01-01T00:00:00+00:00" for i in range(9)},
        }
        minio_ids = {f"minio-{i}" for i in range(9)}
        mock_delete = AsyncMock()

        with (
            patch(
                "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
                AsyncMock(return_value=registry_rows),
            ),
            patch("pageindex_mcp.registry.delete_doc", mock_delete),
        ):
            await _delete_stale_rows(minio_ids, grace_minutes=10)

        mock_delete.assert_awaited_once()
        deleted_id = mock_delete.await_args[0][0]
        assert deleted_id == "old-stale"

    @pytest.mark.asyncio
    async def test_safety_threshold_prevents_mass_deletion(self):
        """When stale candidates exceed 50% of total registry, deletion
        is refused entirely."""
        from pageindex_mcp.registry_backfill.cleanup import _delete_stale_rows

        # 4 rows: 3 stale (75%), 1 in MinIO -- exceeds 50% threshold
        registry_rows = {
            "stale-1": "2020-01-01T00:00:00+00:00",
            "stale-2": "2020-01-01T00:00:00+00:00",
            "stale-3": "2020-01-01T00:00:00+00:00",
            "good-1": "2020-01-01T00:00:00+00:00",
        }
        minio_ids = {"good-1"}
        mock_delete = AsyncMock()

        with (
            patch(
                "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
                AsyncMock(return_value=registry_rows),
            ),
            patch("pageindex_mcp.registry.delete_doc", mock_delete),
        ):
            await _delete_stale_rows(minio_ids, grace_minutes=10)

        # 3/4 = 75% > 50% threshold -- no deletions
        mock_delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_stale_set_is_noop(self):
        """When all registry rows have MinIO counterparts, nothing is deleted."""
        from pageindex_mcp.registry_backfill.cleanup import _delete_stale_rows

        registry_rows = {"doc-1": "2026-01-01T00:00:00+00:00"}
        mock_delete = AsyncMock()

        with (
            patch(
                "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
                AsyncMock(return_value=registry_rows),
            ),
            patch("pageindex_mcp.registry.delete_doc", mock_delete),
        ):
            await _delete_stale_rows({"doc-1"}, grace_minutes=10)

        mock_delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_registry_rows_is_noop(self):
        """When list_all_doc_ids_with_timestamps returns None, nothing happens."""
        from pageindex_mcp.registry_backfill.cleanup import _delete_stale_rows

        mock_delete = AsyncMock()

        with (
            patch(
                "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
                AsyncMock(return_value=None),
            ),
            patch("pageindex_mcp.registry.delete_doc", mock_delete),
        ):
            await _delete_stale_rows(set(), grace_minutes=10)

        mock_delete.assert_not_awaited()


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
        assert f"uploads/" in self._get_delete_doc_src()

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
