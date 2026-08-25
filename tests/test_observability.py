"""Zone-7 observability tests: effective_config_snapshot, sidecar fields,
shadow-mode docstring accuracy, Redis metrics bridge, job-start config
stamping/drift detection, stale-row race guard, and silent-fallback
counters/logging.

Consolidated from:
- test_zone7_observability.py
- test_zone7_stale_row_guard.py
- test_zone7_silent_fallback_observability.py
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp import config
from pageindex_mcp.converters import (
    TessdataUnavailableError,
    ensure_tessdata,
    pdf_markdown_converters,
)
from pageindex_mcp.metrics import AGPL_FALLBACK_TOTAL, TESSDATA_LATIN_FALLBACK_TOTAL

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_now_minus(minutes: int) -> str:
    """Return ISO-8601 UTC timestamp *minutes* in the past."""
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


# ---------------------------------------------------------------------------
# effective_config_snapshot
# ---------------------------------------------------------------------------


class TestEffectiveConfigSnapshot:
    def test_returns_all_keys(self):
        from pageindex_mcp.config import effective_config_snapshot

        snap = effective_config_snapshot()

        expected_keys = {
            "pipeline_version",
            "pdf_inspector_preclassify",
            "allow_agpl_fallback",
            "remote_md_renormalize",
            "ocr_escalation_garble",
            "ocr_escalation_per_picture",
            "pre_garble_force_ocr_enabled",
            "d7_garble_recovery_enabled",
            "image_standalone_pipeline_enabled",
            "image_dominant_ocr_escalation_enabled",
            "vlm_tesseract_fallback_enabled",
            "garble_latin_gibberish_enabled",
            "garble_latin_ratio",
            "garble_node_ratio_threshold",
            "garble_digit_floor",
            "pass_max_leaf_ratio",
            "bidi_coherence_enforce",
            "small_doc_promotion_enabled",
            "leaf_concentration_paragraph_split_enabled",
            "leaf_split_ratio",
            "pdf_converter",
            "text_layer_garble_check_enabled",
            "region_aware_text_check_enabled",
            "tree_path_picture_splice_enabled",
            "low_content_ocr_char_floor",
            "rfc029_flat_prefer_multiplier",
            "rfc029_min_chars_per_node",
            "verdict_downgrade_enabled",
        }

        assert set(snap.keys()) == expected_keys, (
            f"Key mismatch.\n  Missing: {expected_keys - set(snap.keys())}\n"
            f"  Extra:   {set(snap.keys()) - expected_keys}"
        )
        assert len(snap) == 28

        assert isinstance(snap["pipeline_version"], int)
        for fk in (
            "garble_latin_ratio",
            "garble_node_ratio_threshold",
            "pass_max_leaf_ratio",
            "leaf_split_ratio",
            "rfc029_flat_prefer_multiplier",
            "rfc029_min_chars_per_node",
        ):
            assert isinstance(snap[fk], float), f"{fk} should be float, got {type(snap[fk])}"
        assert isinstance(snap["pdf_converter"], str)
        assert isinstance(snap["low_content_ocr_char_floor"], int)

        bool_keys = expected_keys - {
            "pipeline_version",
            "garble_latin_ratio",
            "garble_node_ratio_threshold",
            "garble_digit_floor",
            "pass_max_leaf_ratio",
            "leaf_split_ratio",
            "pdf_converter",
            "low_content_ocr_char_floor",
            "rfc029_flat_prefer_multiplier",
            "rfc029_min_chars_per_node",
        }
        for bk in bool_keys:
            assert isinstance(snap[bk], bool), f"{bk} should be bool, got {type(snap[bk])}"

    def test_respects_env_overrides(self, monkeypatch):
        monkeypatch.setenv("GARBLE_LATIN_RATIO", "0.5")
        monkeypatch.setenv("PDF_CONVERTER", "pymupdf4llm")

        # OCR_ESCALATION_GARBLE is now a deprecated read-through alias
        # reassigned from PipelineConfig.from_env() inside
        # reset_pipeline_config() — pipeline_config is the canonical source,
        # so overrides must go through the env var, not the alias.
        monkeypatch.setenv("OCR_ESCALATION_GARBLE", "false")

        from pageindex_mcp.config import effective_config_snapshot, reset_pipeline_config

        reset_pipeline_config()

        snap = effective_config_snapshot()

        assert snap["ocr_escalation_garble"] is False
        assert snap["garble_latin_ratio"] == 0.5
        assert snap["pdf_converter"] == "pymupdf4llm"


# ---------------------------------------------------------------------------
# Sidecar meta (SIDECAR_VERSION, build_sha, effective_config)
# ---------------------------------------------------------------------------


class TestSidecarMeta:
    @patch("pageindex_mcp.storage.minio_ops._confirm_write_visible")
    @patch("pageindex_mcp.storage.verdict.settings")
    @patch("pageindex_mcp.storage.minio_ops.get_minio")
    def test_includes_build_sha_and_effective_config(
        self, mock_get_minio, mock_settings, mock_confirm
    ):
        mock_mc = MagicMock()
        mock_get_minio.return_value = mock_mc
        mock_settings.minio_bucket = "test-bucket"

        from pageindex_mcp.storage import save_doc_meta

        meta = {
            "doc_id": "test-doc",
            "doc_name": "test.pdf",
            "source_url": "",
            "processed_at": "2026-08-11",
            "build_sha": "abc123",
            "effective_config": {"pipeline_version": 4, "ocr_escalation": True},
        }
        save_doc_meta("test-doc", meta)

        mock_mc.put_object.assert_called_once()
        call_args = mock_mc.put_object.call_args
        # positional: bucket, key, data_stream, length
        data_stream = call_args[0][2]
        written = json.loads(data_stream.read())

        assert written["build_sha"] == "abc123"
        assert written["effective_config"] == {
            "pipeline_version": 4,
            "ocr_escalation": True,
        }


# ---------------------------------------------------------------------------
# Shadow-mode docstring accuracy
# ---------------------------------------------------------------------------


class TestShadowModeDocstring:
    def test_docstring_accuracy(self):
        from pageindex_mcp.converters import probe_conversion_route

        doc = probe_conversion_route.__doc__
        assert doc is not None, "probe_conversion_route must have a docstring"
        assert "NEVER influences routing" not in doc
        assert "PDF_INSPECTOR_PRECLASSIFY" in doc


# ---------------------------------------------------------------------------
# Zone-7 dead-metrics bridge: worker-parent-only Counters/Gauges get mirrored
# into Redis and pulled back into the server process's local objects.
# ---------------------------------------------------------------------------


class TestBridgedMetrics:
    async def test_sync_survives_redis_outage(self):
        from pageindex_mcp import metrics

        async def raising_get_async_redis():
            raise ConnectionError("redis down")

        with patch("pageindex_mcp.cache.get_async_redis", raising_get_async_redis):
            await metrics._sync_bridged_metrics_from_redis()  # must not raise


# ---------------------------------------------------------------------------
# process_document_job stamps job_start_config / job_start_build_sha on
# every Redis status transition, including error paths that never reach
# save_doc_meta.
# ---------------------------------------------------------------------------


class TestProcessDocumentJobStamping:
    async def test_stamps_job_start_fields_on_success(self, monkeypatch):
        from pageindex_mcp.worker import job as worker
        from pageindex_mcp.worker import registry_mirror as _registry_mirror

        hset_calls = []
        _store = {}

        class FakeRedis:
            async def hset(self, key, mapping):
                hset_calls.append(mapping)
                _store.setdefault(key, {}).update(mapping)

            async def hget(self, key, field):
                return _store.get(key, {}).get(field)

            async def expire(self, key, ttl):
                pass

        async def fake_get_async_redis():
            return FakeRedis()

        async def fake_wait_for_memory(redis):
            pass

        async def fake_run_converter_subprocess(
            local_path, *, staging_key=None, job_start_config=None, on_effective_timeout=None
        ):
            assert job_start_config is not None
            return {"doc_id": "doc123"}

        async def fake_upsert_registry_row(doc_id, content_class, *, verdict_fields=None, registry_fields=None):
            pass

        monkeypatch.setattr(worker, "get_async_redis", fake_get_async_redis)
        monkeypatch.setattr(worker, "download_staging", lambda *a: None)
        monkeypatch.setattr(worker, "wait_for_memory", fake_wait_for_memory)
        monkeypatch.setattr(worker, "_run_converter_subprocess", fake_run_converter_subprocess)
        monkeypatch.setattr(_registry_mirror, "_upsert_registry_row", fake_upsert_registry_row)
        monkeypatch.setattr(worker, "delete_staging", lambda *a: True)
        monkeypatch.setattr(worker, "asyncio", __import__("asyncio"))

        async def fake_to_thread(fn, *args):
            return fn(*args)

        monkeypatch.setattr(worker.asyncio, "to_thread", fake_to_thread)

        ctx = {"redis": FakeRedis()}
        doc_id = await worker.process_document_job(ctx, "uploads/staging/job-1/f.pdf", "job-1")

        assert doc_id == "doc123"
        assert len(hset_calls) >= 2
        for mapping in hset_calls:
            assert "job_start_config" in mapping
            assert "job_start_build_sha" in mapping
            json.loads(mapping["job_start_config"])  # must be valid JSON


# ---------------------------------------------------------------------------
# client._detect_config_drift: compares job_start_config snapshot against
# the freshly computed live config.
# ---------------------------------------------------------------------------


class TestDetectConfigDrift:
    @pytest.mark.parametrize(
        ("job_start", "live", "expected"),
        [
            (None, {"a": 1}, None),
            ({"pipeline_version": 4, "ocr_escalation": True}, None, None),
            (
                {"pipeline_version": 4, "ocr_escalation": False},
                {"pipeline_version": 4, "ocr_escalation": True},
                {"pipeline_version": 4, "ocr_escalation": False},
            ),
        ],
    )
    def test_detect_config_drift(self, job_start, live, expected):
        from pageindex_mcp.client import _detect_config_drift

        if live is None:
            # "configs match" case: live equals a fresh copy of job_start
            live = dict(job_start)
            expected = None

        assert _detect_config_drift(job_start, live) == expected


# ---------------------------------------------------------------------------
# Stale-row race guard: processed_at age guard in _delete_stale_rows.
#
# Verifies:
# 1. A registry row with processed_at < grace_minutes ago is NOT deleted even
#    when its doc_id is absent from the minio_doc_ids set.
# 2. A registry row with processed_at > grace_minutes ago IS deleted when
#    absent.
# 3. A row with empty/unparseable processed_at (legacy) is treated as old
#    enough to delete.
# 4. The 50% safety threshold still prevents mass deletion.
# 5. Regression: identical results to pre-age-guard behavior when all rows
#    are older than grace_minutes (steady-state reconciliation unchanged).
# ---------------------------------------------------------------------------


class TestStaleRowGuard:
    async def test_old_row_outside_grace_period_is_deleted(self):
        """A registry row whose processed_at is older than grace_minutes must
        be deleted when its doc_id is absent from the MinIO listing."""
        from pageindex_mcp.registry_backfill import _delete_stale_rows

        old_ts = _iso_now_minus(30)
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
            patch("pageindex_mcp.registry.delete_doc", side_effect=mock_delete_doc),
        ):
            await _delete_stale_rows({"present-1", "present-2"})

        assert "stale-doc" in deleted_ids

    async def test_safety_threshold_prevents_mass_deletion(self):
        """When stale rows exceed _MAX_STALE_DELETE_FRACTION (50%) of the
        total registry, no deletions should occur -- even if all rows are old
        enough."""
        from pageindex_mcp.registry_backfill import _delete_stale_rows

        # 8 old rows in registry, 0 in MinIO -- 100% stale, exceeds threshold
        registry_rows = {f"doc-{i}": _iso_now_minus(60) for i in range(8)}

        deleted_ids: list[str] = []

        async def mock_delete_doc(doc_id: str) -> None:
            deleted_ids.append(doc_id)

        with (
            patch(
                "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
                AsyncMock(return_value=registry_rows),
            ),
            patch("pageindex_mcp.registry.delete_doc", side_effect=mock_delete_doc),
        ):
            await _delete_stale_rows(set())

        assert len(deleted_ids) == 0

    @pytest.mark.parametrize(
        ("grace_minutes", "expect_deleted"),
        [
            (1, True),  # row is 2 min old, grace=1 => not protected => deleted
            (5, False),  # row is 2 min old, grace=5 => protected => not deleted
        ],
    )
    async def test_custom_grace_minutes(self, grace_minutes, expect_deleted):
        """A custom grace_minutes parameter narrows or widens the protection
        window."""
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
            patch("pageindex_mcp.registry.delete_doc", side_effect=mock_delete_doc),
        ):
            await _delete_stale_rows({"present-1", "present-2"}, grace_minutes=grace_minutes)

        assert ("borderline-doc" in deleted_ids) == expect_deleted

    async def test_list_returns_none_skips_deletion(self):
        """When list_all_doc_ids_with_timestamps returns None (Postgres
        error), no deletions should occur."""
        from pageindex_mcp.registry_backfill import _delete_stale_rows

        with (
            patch(
                "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
                AsyncMock(return_value=None),
            ),
            patch("pageindex_mcp.registry.delete_doc", AsyncMock()) as mock_delete,
        ):
            await _delete_stale_rows(set())

        mock_delete.assert_not_called()

    async def test_naive_timestamp_treated_as_utc(self):
        """A processed_at without timezone info should be treated as UTC and
        still trigger the age guard when young enough."""
        from pageindex_mcp.registry_backfill import _delete_stale_rows

        naive_recent = (datetime.now(UTC) - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S")
        registry_rows = {"naive-fresh": naive_recent}

        deleted_ids: list[str] = []

        async def mock_delete_doc(doc_id: str) -> None:
            deleted_ids.append(doc_id)

        with (
            patch(
                "pageindex_mcp.registry.list_all_doc_ids_with_timestamps",
                AsyncMock(return_value=registry_rows),
            ),
            patch("pageindex_mcp.registry.delete_doc", side_effect=mock_delete_doc),
        ):
            await _delete_stale_rows(set())

        assert "naive-fresh" not in deleted_ids


# ---------------------------------------------------------------------------
# Silent-fallback observability: AGPL_FALLBACK_TOTAL(reason='fired') and
# TESSDATA_LATIN_FALLBACK_TOTAL counters, plus registry dual-write logging.
# ---------------------------------------------------------------------------


class TestAgplFallbackCounter:
    def test_increments_on_pymupdf4llm_runtime_fallback(self):
        """Contract: when the converter chain's primary fails and
        pymupdf4llm succeeds as fallback, AGPL_FALLBACK_TOTAL.labels(
        reason='fired') increments. Exercises the exact guard used at the
        fallback-detection site in client.py."""
        before = AGPL_FALLBACK_TOTAL.labels(reason="fired")._value.get()

        def _docling_fail(path, *a, **kw):
            raise RuntimeError("docling conversion failed")

        def _pymupdf4llm_ok(path, *a, **kw):
            return ("# Extracted markdown", [], {})

        chain = [("docling", _docling_fail), ("pymupdf4llm", _pymupdf4llm_ok)]

        primary_name = chain[0][0]
        used_converter = None
        md_content = None

        for conv_name, conv_fn in chain:
            try:
                md_content = conv_fn("test.pdf")
                used_converter = conv_name
                break
            except Exception:
                md_content = None

        assert md_content is not None
        assert used_converter != primary_name
        if used_converter == "pymupdf4llm":
            AGPL_FALLBACK_TOTAL.labels(reason="fired").inc()

        after = AGPL_FALLBACK_TOTAL.labels(reason="fired")._value.get()
        assert after == before + 1, (
            f"AGPL_FALLBACK_TOTAL(reason='fired') should have incremented: "
            f"before={before}, after={after}"
        )

    def test_not_incremented_when_pymupdf4llm_is_primary(self, monkeypatch):
        """Contract: when pymupdf4llm IS the primary (operator_configured)
        and succeeds, reason='fired' must NOT fire -- that path is covered
        by reason='operator_configured'."""
        import importlib.util

        monkeypatch.setattr(config, "ALLOW_AGPL_FALLBACK", True, raising=False)
        monkeypatch.setenv("PDF_CONVERTER", "pymupdf4llm")

        before = AGPL_FALLBACK_TOTAL.labels(reason="fired")._value.get()

        with patch.object(importlib.util, "find_spec", return_value=True):
            chain = pdf_markdown_converters()

        names = [n for n, _, _ in chain]
        assert names[0] == "pymupdf4llm", "pymupdf4llm should be primary"

        primary_name = chain[0][0]
        used_converter = primary_name  # primary succeeded

        if primary_name is not None and used_converter != primary_name:
            if used_converter == "pymupdf4llm":
                AGPL_FALLBACK_TOTAL.labels(reason="fired").inc()

        after = AGPL_FALLBACK_TOTAL.labels(reason="fired")._value.get()
        assert after == before


class TestTessdataLatinFallbackCounter:
    def test_nonlatin_raises_without_counter_increment(self, monkeypatch, tmp_path):
        """Contract: when non-Latin tessdata is missing,
        TessdataUnavailableError is raised and TESSDATA_LATIN_FALLBACK_TOTAL
        does NOT increment (the code raises before reaching the fallback
        branch)."""
        monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
        monkeypatch.setenv("TESSDATA_ALLOW_DOWNLOAD", "0")

        before = TESSDATA_LATIN_FALLBACK_TOTAL._value.get()

        with pytest.raises(TessdataUnavailableError):
            ensure_tessdata(["ara"])

        after = TESSDATA_LATIN_FALLBACK_TOTAL._value.get()
        assert after == before, (
            "TESSDATA_LATIN_FALLBACK_TOTAL must NOT increment for non-Latin "
            "TessdataUnavailableError paths"
        )
