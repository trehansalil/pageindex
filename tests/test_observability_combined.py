# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
from __future__ import annotations

"""Observability, metrics, tracing, and queue metrics tests."""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import openai
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.routing import Route

from pageindex_mcp import config, queue_metrics
from pageindex_mcp.converters import (
    TessdataUnavailableError,
    ensure_tessdata,
    pdf_markdown_converters,
)
from pageindex_mcp.metrics import (
    ACTIVE_UPLOADS,
    AGPL_FALLBACK_TOTAL,
    ARQ_QUEUE_DEPTH,
    DOCUMENTS_TOTAL,
    LLM_CALLS,
    MINIO_OPS,
    TESSDATA_LATIN_FALLBACK_TOTAL,
    TOOL_CALLS,
    TOOL_ERRORS,
    UPLOADS,
    metrics_response,
)
import pageindex_mcp.tracing as tracing


# --- from test_observability.py ---

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
            "ocr_escalation_low_content",
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
        assert len(snap) == 29

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

    def test_allow_agpl_fallback_consistent_after_reset(self, monkeypatch):
        """Regression (HR4 audit trail): effective_config_snapshot()
        allow_agpl_fallback field must be consistent with
        pipeline_config.allow_agpl_fallback after reset_pipeline_config()
        with ALLOW_AGPL_FALLBACK=0."""
        monkeypatch.setenv("ALLOW_AGPL_FALLBACK", "0")

        from pageindex_mcp.config import (
            effective_config_snapshot,
            pipeline_config,
            reset_pipeline_config,
        )

        reset_pipeline_config()

        # Re-import after reset to get the fresh singleton
        from pageindex_mcp.config import pipeline_config as fresh_pc

        snap = effective_config_snapshot()

        assert fresh_pc.allow_agpl_fallback is False
        assert snap["allow_agpl_fallback"] is False, (
            "effective_config_snapshot()['allow_agpl_fallback'] must match "
            "pipeline_config.allow_agpl_fallback after reset"
        )


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

        monkeypatch.setenv("ALLOW_AGPL_FALLBACK", "1")
        monkeypatch.setenv("PDF_CONVERTER", "pymupdf4llm")

        from pageindex_mcp.config import reset_pipeline_config

        reset_pipeline_config()

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


# --- from test_metrics.py ---


@pytest.fixture
def metrics_app():
    """Minimal Starlette app with just the /metrics route."""
    return Starlette(routes=[Route("/metrics", metrics_response)])


@pytest.fixture
async def client(metrics_app):
    async with AsyncClient(transport=ASGITransport(app=metrics_app), base_url="http://test") as c:
        yield c


async def test_metrics_endpoint_returns_200(client):
    response = await client.get("/metrics")
    assert response.status_code == 200


async def test_metrics_content_type(client):
    response = await client.get("/metrics")
    assert "text/plain" in response.headers["content-type"]
    assert "0.0.4" in response.headers["content-type"]


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="process_* metrics are Linux-only (prometheus_client reads /proc)",
)
async def test_metrics_contains_process_metrics(client):
    """prometheus_client includes process_* metrics by default."""
    response = await client.get("/metrics")
    body = response.text
    assert "process_cpu_seconds_total" in body


async def test_metrics_contains_app_metrics(client):
    """Our custom metrics should appear (even if at zero)."""
    response = await client.get("/metrics")
    body = response.text
    assert "pageindex_tool_calls_total" in body or "pageindex_tool_calls" in body


def _counter_value(counter, labels=None):
    """Read current value of a Counter for given labels."""
    if labels:
        return counter.labels(**labels)._value.get()
    return counter._value.get()


def _gauge_value(gauge):
    return gauge._value.get()


class TestToolInstrumentation:
    async def test_recent_documents_increments_counter(self):
        # Phase 3 audit Issue B: registry-unavailable now raises isError:true
        # (ToolError) instead of returning a JSON envelope, but TOOL_CALLS still
        # increments unconditionally at the top of the function.
        from fastmcp.exceptions import ToolError

        before = _counter_value(TOOL_CALLS, {"tool": "recent_documents"})
        with patch("pageindex_mcp.storage.list_processed_docs", return_value=[]):
            from pageindex_mcp.tools.documents import recent_documents

            with pytest.raises(ToolError):
                await recent_documents()
        after = _counter_value(TOOL_CALLS, {"tool": "recent_documents"})
        assert after == before + 1

    async def test_recent_documents_updates_documents_gauge(self):
        # RFC-009 D6: registry-only read path — DOCUMENTS_TOTAL reflects
        # registry.count_docs(), not a MinIO listing length.
        fake_docs = [{"doc_id": "a", "doc_name": "a"}, {"doc_id": "b", "doc_name": "b"}]
        from pageindex_mcp.tools import documents

        with (
            patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
            patch("pageindex_mcp.registry.list_docs", new=AsyncMock(return_value=fake_docs)),
            patch("pageindex_mcp.registry.count_docs", new=AsyncMock(return_value=2)),
        ):
            await documents.recent_documents()
        assert _gauge_value(DOCUMENTS_TOTAL) == 2

    def test_get_document_increments_error_counter_on_failure(self):
        before = _counter_value(TOOL_ERRORS, {"tool": "get_document"})
        with (
            patch("pageindex_mcp.tools.documents.get_doc", side_effect=Exception("boom")),
            patch("pageindex_mcp.storage.list_processed_docs", return_value=[]),
        ):
            from pageindex_mcp.tools.documents import get_document

            get_document("nonexistent")
        after = _counter_value(TOOL_ERRORS, {"tool": "get_document"})
        assert after == before + 1


class TestUploadInstrumentation:
    def test_upload_success_increments_counter(self):
        before = _counter_value(UPLOADS, {"status": "success"})
        UPLOADS.labels(status="success").inc()
        after = _counter_value(UPLOADS, {"status": "success"})
        assert after == before + 1

    def test_active_uploads_gauge_exists(self):
        val = _gauge_value(ACTIVE_UPLOADS)
        assert val >= 0


class TestLLMInstrumentation:
    def test_llm_call_increments_counter(self):
        before = _counter_value(LLM_CALLS)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "test answer"

        # `helpers.rag._llm` does `from ..client import get_openai_client` at
        # call time, which resolves the name off the `pageindex_mcp.client`
        # package (__init__.py's re-export), not off `pageindex_mcp.client.llm`.
        # Patching the `llm` submodule attribute leaves that re-export
        # untouched (mock-where-defined instead of mock-where-used), so the
        # real client was constructed and a live LLM call went out. Patch the
        # name actually consulted by the call site instead.
        with patch("pageindex_mcp.client.get_openai_client") as MockFactory:
            MockFactory.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
            from pageindex_mcp.helpers import _llm

            asyncio.get_event_loop().run_until_complete(_llm("test prompt"))

        after = _counter_value(LLM_CALLS)
        assert after == before + 1


class TestStorageInstrumentation:
    def test_list_processed_docs_increments_minio_ops(self):
        before = _counter_value(MINIO_OPS, {"operation": "list"})
        mock_minio = MagicMock()
        mock_minio.list_objects.return_value = []
        with patch("pageindex_mcp.storage.minio_ops.get_minio", return_value=mock_minio):
            from pageindex_mcp.storage import list_processed_docs

            list_processed_docs()
        after = _counter_value(MINIO_OPS, {"operation": "list"})
        assert after == before + 1

    def test_load_doc_increments_minio_ops(self):
        before = _counter_value(MINIO_OPS, {"operation": "get"})
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"structure": []}'
        mock_minio = MagicMock()
        mock_minio.get_object.return_value = mock_response
        with (
            patch("pageindex_mcp.storage.minio_ops.get_minio", return_value=mock_minio),
            patch("pageindex_mcp.storage.documents.settings") as mock_settings,
        ):
            mock_settings.minio_bucket = "test"
            from pageindex_mcp.storage import load_doc

            load_doc("abc123")
        after = _counter_value(MINIO_OPS, {"operation": "get"})
        assert after == before + 1


def test_arq_queue_depth_gauge_exposed():
    # Arrange
    from prometheus_client import generate_latest

    from pageindex_mcp.metrics import ARQ_QUEUE_DEPTH, REGISTRY

    # Act
    ARQ_QUEUE_DEPTH.set(3)
    text = generate_latest(REGISTRY).decode()

    # Assert
    assert "pageindex_arq_queue_depth" in text
    assert "pageindex_arq_queue_depth 3.0" in text


# --- from test_tracing.py ---


def _fake_settings(**overrides):
    """Mutable stand-in for the frozen Settings singleton (see test_client)."""
    base = {
        "openai_base_url": "https://api.openai.com/v1",
        "openai_api_key": "test-key",
        "azure_api_version": None,
        "llm_provider": "auto",
        "langfuse_public_key": "",
        "langfuse_secret_key": "",
        "langfuse_host": "https://cloud.langfuse.com",
        "langfuse_trace_content": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _reset_guards():
    """Reset the once-per-process init guard so each test starts clean."""
    tracing._initialized = False
    yield
    tracing._initialized = False


# ---------------------------------------------------------------------------
# LLM-02-C1: tracing activates only when both keys are set; else inert
# ---------------------------------------------------------------------------
def test_llm_02_c1_disabled_when_keys_missing(monkeypatch):
    """LLM-02-C1: no keys (or only one) => disabled and init_langfuse is a no-op."""
    monkeypatch.setattr(tracing, "settings", _fake_settings())
    assert tracing.langfuse_enabled() is False
    tracing.init_langfuse()
    assert tracing._initialized is False  # no singleton constructed

    # Only one key present is still disabled.
    monkeypatch.setattr(tracing, "settings", _fake_settings(langfuse_public_key="pk-x"))
    assert tracing.langfuse_enabled() is False


def test_llm_02_c1_enabled_when_both_keys_set(monkeypatch):
    """LLM-02-C1: both keys present => enabled."""
    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )
    assert tracing.langfuse_enabled() is True


# ---------------------------------------------------------------------------
# LLM-02-C2: query path yields a langfuse.openai-wrapped client when enabled
# ---------------------------------------------------------------------------
def test_llm_02_c2_traced_client_when_enabled(monkeypatch):
    """LLM-02-C2: enabled => get_openai_client takes the instrumented branch.

    The ``langfuse.openai`` wrapper instruments ``openai`` globally at import
    rather than by subclassing, so traced-ness is not visible on the client class.
    The deterministic signal that the instrumented branch ran is that
    get_openai_client calls init_langfuse and imports langfuse.openai -- only the
    enabled branch does either.
    """
    import sys

    from pageindex_mcp import client as client_mod

    called = {"init": 0}
    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )
    monkeypatch.setattr(tracing, "init_langfuse", lambda: called.__setitem__("init", 1))

    # openai/compatible provider
    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(llm_provider="compatible", openai_base_url="https://openrouter.ai/api/v1"),
    )
    c = client_mod.get_openai_client()
    assert called["init"] == 1  # enabled branch ran
    assert "langfuse.openai" in sys.modules  # instrumentation import triggered
    assert isinstance(c, openai.AsyncOpenAI)  # SDK-compatible
    assert str(c.base_url).rstrip("/") == "https://openrouter.ai/api/v1"

    # azure provider still yields an AzureOpenAI client
    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(llm_provider="azure", openai_base_url="https://r.openai.azure.com"),
    )
    assert isinstance(client_mod.get_openai_client(), openai.AsyncAzureOpenAI)


def test_llm_02_c2_get_openai_client_falls_back_when_disabled(monkeypatch):
    """LLM-02-C2: disabled => get_openai_client takes the plain LLM-01 branch."""
    from pageindex_mcp import client as client_mod

    called = {"init": 0}
    monkeypatch.setattr(tracing, "settings", _fake_settings())
    monkeypatch.setattr(tracing, "init_langfuse", lambda: called.__setitem__("init", 1))
    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(openai_base_url="https://api.openai.com/v1"),
    )
    c = client_mod.get_openai_client()
    assert called["init"] == 0  # disabled branch -- no Langfuse init
    assert isinstance(c, openai.AsyncOpenAI)
    assert not isinstance(c, openai.AsyncAzureOpenAI)


# ---------------------------------------------------------------------------
# LLM-02-C3: ingestion path registers the litellm Langfuse callback
# ---------------------------------------------------------------------------
def test_llm_02_c3_registers_litellm_callback(monkeypatch):
    """LLM-02-C3: enabled => configure_litellm appends 'langfuse_otel' to callbacks."""
    import litellm

    from pageindex_mcp import client as client_mod

    monkeypatch.setattr(litellm, "callbacks", [], raising=False)
    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )
    tracing._initialized = True  # skip real singleton
    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(
            llm_provider="compatible",
            openai_base_url="http://localhost:8000/v1",
            openai_api_key="sk-local",
        ),
    )

    client_mod.configure_litellm()
    assert "langfuse_otel" in litellm.callbacks
    assert litellm.turn_off_message_logging is True  # masked by default

    # Idempotent: a second call does not duplicate the callback.
    client_mod.configure_litellm()
    assert litellm.callbacks.count("langfuse_otel") == 1


def test_llm_02_c3_no_callback_when_disabled(monkeypatch):
    """LLM-02-C3: disabled => configure_litellm registers no callback."""
    import litellm

    from pageindex_mcp import client as client_mod

    monkeypatch.setattr(litellm, "callbacks", [], raising=False)
    monkeypatch.setattr(tracing, "settings", _fake_settings())
    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(
            llm_provider="compatible",
            openai_base_url="http://localhost:8000/v1",
            openai_api_key="sk-local",
        ),
    )
    client_mod.configure_litellm()
    assert "langfuse_otel" not in litellm.callbacks


# ---------------------------------------------------------------------------
# LLM-02-C4: masking on by default, passthrough when content capture is on
# ---------------------------------------------------------------------------
def test_llm_02_c4_masks_by_default(monkeypatch):
    """LLM-02-C4: with trace_content False, _mask redacts strings recursively."""
    monkeypatch.setattr(tracing, "settings", _fake_settings(langfuse_trace_content=False))
    assert tracing._mask("secret prompt") == tracing._MASK_SENTINEL
    masked = tracing._mask({"messages": ["a", {"content": "b"}]})
    assert masked == {"messages": [tracing._MASK_SENTINEL, {"content": tracing._MASK_SENTINEL}]}


def test_llm_02_c4_mask_preserves_non_string_scalars(monkeypatch):
    """LLM-02-C4: numeric/bool/None fields keep their type -- only strings redact.

    Guards against the mask coercing structured fields (temperature, max_tokens,
    token counts, timestamps, flags) into the string sentinel, which would change
    their type and risk breaking downstream parsing or dropping usage signals.
    """
    monkeypatch.setattr(tracing, "settings", _fake_settings(langfuse_trace_content=False))
    assert tracing._mask(42) == 42
    assert tracing._mask(0.7) == 0.7
    assert tracing._mask(True) is True
    assert tracing._mask(None) is None
    payload = {
        "model": "gpt-4.1",  # string -> masked
        "temperature": 0.7,  # float -> kept
        "max_tokens": 256,  # int -> kept
        "stream": False,  # bool -> kept
        "usage": {"total_tokens": 123},  # nested numeric -> kept
    }
    assert tracing._mask(payload) == {
        "model": tracing._MASK_SENTINEL,
        "temperature": 0.7,
        "max_tokens": 256,
        "stream": False,
        "usage": {"total_tokens": 123},
    }


def test_llm_02_c4_passthrough_when_content_enabled(monkeypatch):
    """LLM-02-C4: with trace_content True, _mask returns data verbatim."""
    monkeypatch.setattr(tracing, "settings", _fake_settings(langfuse_trace_content=True))
    payload = {"messages": ["hello", "world"]}
    assert tracing._mask("hello") == "hello"
    assert tracing._mask(payload) == payload


# ---------------------------------------------------------------------------
# LLM-02-C5: trace_tool groups a tool call's generations under one trace
# ---------------------------------------------------------------------------
async def test_llm_02_c5_noop_when_disabled(monkeypatch):
    """LLM-02-C5: disabled => trace_tool is a transparent no-op."""
    monkeypatch.setattr(tracing, "settings", _fake_settings())
    ran = False
    async with tracing.trace_tool("find_relevant_documents"):
        ran = True
    assert ran is True


async def test_llm_02_c5_opens_single_span_when_enabled(monkeypatch):
    """LLM-02-C5: enabled => one span named for the tool wraps the body."""
    entered = {"name": None, "count": 0}

    class _FakeSpanCM:
        def __enter__(self):
            entered["count"] += 1
            return self

        def __exit__(self, *exc):
            return False

    class _FakeClient:
        def start_as_current_span(self, name):
            entered["name"] = name
            return _FakeSpanCM()

    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )
    tracing._initialized = True
    monkeypatch.setattr("langfuse.get_client", lambda: _FakeClient())

    async with tracing.trace_tool("find_relevant_documents"):
        pass

    assert entered["name"] == "find_relevant_documents"
    assert entered["count"] == 1


async def test_llm_02_c5_body_exception_propagates_when_enabled(monkeypatch):
    """LLM-02-C5: a tool-body exception is NOT swallowed by trace_tool.

    Regression for the double-yield bug: the body is yielded outside the
    span-setup try, so its exception must propagate to the caller (which records
    TOOL_ERRORS and re-raises) rather than being caught and re-yielded.
    """

    class _FakeSpanCM:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False  # do not suppress

    class _FakeClient:
        def start_as_current_span(self, name):
            return _FakeSpanCM()

    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )
    tracing._initialized = True
    monkeypatch.setattr("langfuse.get_client", lambda: _FakeClient())

    with pytest.raises(ValueError, match="boom"):
        async with tracing.trace_tool("find_relevant_documents"):
            raise ValueError("boom")


async def test_llm_02_c5_runs_untraced_when_span_setup_fails(monkeypatch):
    """LLM-02-C5: if span setup raises, the body still runs (untraced), once."""
    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )
    tracing._initialized = True

    def _boom():
        raise RuntimeError("no client")

    monkeypatch.setattr("langfuse.get_client", _boom)

    ran = False
    async with tracing.trace_tool("find_relevant_documents"):
        ran = True
    assert ran is True


async def test_llm_02_c5_runs_untraced_when_span_enter_fails(monkeypatch):
    """LLM-02-C5: a span __enter__ failure must NOT break the tool (cubic P2).

    Tracing errors at context entry fall back to running the body untraced.
    """

    class _BadSpanCM:
        def __enter__(self):
            raise RuntimeError("enter failed")

        def __exit__(self, *exc):
            return False

    class _FakeClient:
        def start_as_current_span(self, name):
            return _BadSpanCM()

    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )
    tracing._initialized = True
    monkeypatch.setattr("langfuse.get_client", lambda: _FakeClient())

    ran = False
    async with tracing.trace_tool("find_relevant_documents"):
        ran = True
    assert ran is True  # body still ran, untraced


async def test_llm_02_c5_span_close_failure_does_not_break_tool(monkeypatch):
    """LLM-02-C5: a failure inside span __exit__ must not break the tool either."""

    class _SpanCM:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            raise RuntimeError("exit failed")

    class _FakeClient:
        def start_as_current_span(self, name):
            return _SpanCM()

    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )
    tracing._initialized = True
    monkeypatch.setattr("langfuse.get_client", lambda: _FakeClient())

    ran = False
    async with tracing.trace_tool("find_relevant_documents"):
        ran = True
    assert ran is True


# ---------------------------------------------------------------------------
# LLM-02-C3: flushing both providers before a short-lived subprocess exits
# ---------------------------------------------------------------------------
def test_llm_02_c3_flush_langfuse_not_gated_on_init(monkeypatch):
    """LLM-02-C3: flush_langfuse runs whenever enabled, even if _initialized False.

    The converters_cli subprocess may flush before the singleton was eagerly
    constructed; get_client() lazily returns it, so the flush must not be skipped.
    """
    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )
    tracing._initialized = False  # singleton NOT eagerly constructed

    flushed = {"count": 0}

    class _FakeClient:
        def flush(self):
            flushed["count"] += 1

    monkeypatch.setattr("langfuse.get_client", lambda: _FakeClient())
    tracing.flush_langfuse()
    assert flushed["count"] == 1  # flushed despite _initialized False


def test_llm_02_c3_flush_langfuse_noop_when_disabled(monkeypatch):
    """LLM-02-C3: flush_langfuse is a no-op (no client touched) when disabled."""
    monkeypatch.setattr(tracing, "settings", _fake_settings())

    def _boom():
        raise AssertionError("get_client must not be called when disabled")

    monkeypatch.setattr("langfuse.get_client", _boom)
    tracing.flush_langfuse()  # must not raise


def test_llm_02_c3_flush_litellm_tracing_noop_when_disabled(monkeypatch):
    """LLM-02-C3: client.flush_litellm_tracing is a safe no-op when disabled."""
    from pageindex_mcp import client as client_mod

    monkeypatch.setattr(tracing, "settings", _fake_settings())
    client_mod.flush_litellm_tracing()  # disabled -> returns without touching litellm


def test_llm_02_c3_flush_litellm_tracing_force_flushes_otel_processor(monkeypatch):
    """LLM-02-C3: enabled => the langfuse_otel logger's OTel span processor is flushed.

    litellm's langfuse_otel exports through a private OTel TracerProvider, so the
    flush must reach the logger instance's tracer.span_processor.force_flush().
    """
    from pageindex_mcp import client as client_mod

    monkeypatch.setattr(
        tracing,
        "settings",
        _fake_settings(langfuse_public_key="pk-x", langfuse_secret_key="sk-x"),
    )

    forced = {"count": 0}

    class _FakeProcessor:
        def force_flush(self, *a, **k):
            forced["count"] += 1

    class _FakeTracer:
        span_processor = _FakeProcessor()

    class LangfuseOtelLogger:  # name matched by the flush helper
        tracer = _FakeTracer()

    class _Other:  # must be ignored
        tracer = _FakeTracer()

    monkeypatch.setattr(
        "litellm.litellm_core_utils.litellm_logging._in_memory_loggers",
        [_Other(), LangfuseOtelLogger()],
        raising=False,
    )
    client_mod.flush_litellm_tracing()
    assert forced["count"] == 1  # only the langfuse_otel logger was flushed


# --- from test_queue_metrics.py ---


async def test_read_queue_depth_counts_arq_queue():
    # Arrange
    redis = fakeredis.aioredis.FakeRedis()
    await redis.zadd("arq:queue", {"job-a": 1.0, "job-b": 2.0})

    # Act
    depth = await queue_metrics.read_queue_depth(redis)

    # Assert
    assert depth == 2


async def test_read_queue_depth_zero_when_empty():
    redis = fakeredis.aioredis.FakeRedis()
    assert await queue_metrics.read_queue_depth(redis) == 0


async def test_scrape_loop_sets_gauge_then_stops():
    # Arrange
    redis = fakeredis.aioredis.FakeRedis()
    await redis.zadd("arq:queue", {"job-a": 1.0})

    # Act: run one tick then cancel
    task = asyncio.create_task(queue_metrics.queue_depth_scrape_loop(redis, interval=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Assert
    assert ARQ_QUEUE_DEPTH._value.get() == 1.0


async def test_server_lifespan_starts_and_stops_scrape_task(monkeypatch):
    # Arrange
    started = asyncio.Event()
    stopped = {"cancelled": False}

    async def fake_loop(redis, interval=0.01):
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            stopped["cancelled"] = True
            raise

    monkeypatch.setattr(queue_metrics, "queue_depth_scrape_loop", fake_loop)

    from pageindex_mcp.server import _lifespan_with_scrape

    class _DummyApp:
        pass

    # Act: enter then exit the composed lifespan
    async with _lifespan_with_scrape(_DummyApp(), _inner=None):
        await asyncio.wait_for(started.wait(), timeout=1)

    # Assert: task was cancelled on shutdown
    assert stopped["cancelled"] is True
