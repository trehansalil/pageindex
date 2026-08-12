"""Zone-7 observability tests: effective_config_snapshot, sidecar fields,
shadow-mode docstring accuracy."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Test 1: effective_config_snapshot returns all 20 keys with correct types
# ---------------------------------------------------------------------------

def test_effective_config_snapshot_returns_all_keys():
    from pageindex_mcp.config import effective_config_snapshot

    snap = effective_config_snapshot()

    expected_keys = {
        "pipeline_version",
        "pdf_inspector_preclassify",
        "allow_agpl_fallback",
        "remote_md_renormalize",
        "ocr_escalation",
        "pre_garble_force_ocr_enabled",
        "d7_garble_recovery_enabled",
        "image_standalone_pipeline_enabled",
        "image_dominant_ocr_escalation_enabled",
        "vlm_tesseract_fallback_enabled",
        "garble_latin_gibberish_enabled",
        "garble_latin_ratio",
        "garble_node_ratio_threshold",
        "pass_max_leaf_ratio",
        "bidi_coherence_enforce",
        "small_doc_promotion_enabled",
        "leaf_concentration_paragraph_split_enabled",
        "leaf_split_ratio",
        "pdf_converter",
        "text_layer_garble_check_enabled",
        "region_aware_text_check_enabled",
    }

    assert set(snap.keys()) == expected_keys, (
        f"Key mismatch.\n  Missing: {expected_keys - set(snap.keys())}\n"
        f"  Extra:   {set(snap.keys()) - expected_keys}"
    )
    assert len(snap) == 21

    # Type checks
    assert isinstance(snap["pipeline_version"], int)
    for fk in ("garble_latin_ratio", "garble_node_ratio_threshold", "pass_max_leaf_ratio"):
        assert isinstance(snap[fk], float), f"{fk} should be float, got {type(snap[fk])}"
    assert isinstance(snap["pdf_converter"], str)

    bool_keys = expected_keys - {
        "pipeline_version",
        "garble_latin_ratio",
        "garble_node_ratio_threshold",
        "pass_max_leaf_ratio",
        "leaf_split_ratio",
        "pdf_converter",
    }
    for bk in bool_keys:
        assert isinstance(snap[bk], bool), f"{bk} should be bool, got {type(snap[bk])}"


# ---------------------------------------------------------------------------
# Test 2: effective_config_snapshot respects env overrides
# ---------------------------------------------------------------------------

def test_effective_config_snapshot_respects_env_overrides(monkeypatch):
    monkeypatch.setenv("GARBLE_LATIN_RATIO", "0.5")
    monkeypatch.setenv("PDF_CONVERTER", "pymupdf4llm")

    # OCR_ESCALATION is a module-level constant (consolidated in config.py).
    # To test it, patch the constant directly rather than setting the env var
    # after import.
    import pageindex_mcp.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "OCR_ESCALATION", False)

    from pageindex_mcp.config import effective_config_snapshot

    snap = effective_config_snapshot()

    assert snap["ocr_escalation"] is False
    assert snap["garble_latin_ratio"] == 0.5
    assert snap["pdf_converter"] == "pymupdf4llm"


# ---------------------------------------------------------------------------
# Test 3: SIDECAR_VERSION is 4
# ---------------------------------------------------------------------------

def test_sidecar_version_is_4():
    from pageindex_mcp.storage import SIDECAR_VERSION

    assert SIDECAR_VERSION == 4


# ---------------------------------------------------------------------------
# Test 4: sidecar includes build_sha and effective_config
# ---------------------------------------------------------------------------

@patch("pageindex_mcp.storage._confirm_write_visible")
@patch("pageindex_mcp.storage.settings")
@patch("pageindex_mcp.storage.get_minio")
def test_sidecar_includes_build_sha_and_effective_config(
    mock_get_minio, mock_settings, mock_confirm
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
# Test 5: sidecar omits build_sha / effective_config when absent
# ---------------------------------------------------------------------------

@patch("pageindex_mcp.storage._confirm_write_visible")
@patch("pageindex_mcp.storage.settings")
@patch("pageindex_mcp.storage.get_minio")
def test_sidecar_omits_new_fields_when_absent(
    mock_get_minio, mock_settings, mock_confirm
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
    }
    save_doc_meta("test-doc", meta)

    mock_mc.put_object.assert_called_once()
    call_args = mock_mc.put_object.call_args
    data_stream = call_args[0][2]
    written = json.loads(data_stream.read())

    assert "build_sha" not in written
    assert "effective_config" not in written


# ---------------------------------------------------------------------------
# Test 6: shadow mode docstring accuracy
# ---------------------------------------------------------------------------

def test_shadow_mode_docstring_accuracy():
    from pageindex_mcp.converters import probe_conversion_route

    doc = probe_conversion_route.__doc__
    assert doc is not None, "probe_conversion_route must have a docstring"
    assert "NEVER influences routing" not in doc
    assert "PDF_INSPECTOR_PRECLASSIFY" in doc


# ---------------------------------------------------------------------------
# Test 7: Zone-7 dead-metrics bridge — worker-parent-only Counters/Gauges get
# mirrored into Redis and pulled back into the server process's local objects.
# ---------------------------------------------------------------------------

async def test_sync_bridged_metrics_from_redis_pulls_all_registered_names():
    from pageindex_mcp import metrics

    fake_values = {
        metrics.bridge_redis_key(name): str(i)
        for i, name in enumerate(metrics._BRIDGED_METRICS, start=1)
    }

    async def fake_mget(keys):
        return [fake_values.get(k) for k in keys]

    fake_redis = MagicMock()
    fake_redis.mget = fake_mget

    async def fake_get_async_redis():
        return fake_redis

    with patch("pageindex_mcp.cache.get_async_redis", fake_get_async_redis):
        await metrics._sync_bridged_metrics_from_redis()

    for i, name in enumerate(metrics._BRIDGED_METRICS, start=1):
        metric = metrics._BRIDGED_METRICS[name]
        assert metric._value.get() == float(i)


async def test_sync_bridged_metrics_from_redis_survives_redis_outage():
    from pageindex_mcp import metrics

    async def raising_get_async_redis():
        raise ConnectionError("redis down")

    with patch("pageindex_mcp.cache.get_async_redis", raising_get_async_redis):
        await metrics._sync_bridged_metrics_from_redis()  # must not raise


@pytest.mark.parametrize(
    ("name", "helper", "amount"),
    [
        ("active_uploads", "_mirror_bridged_incr", 1),
        ("converter_child_oom_total", "_mirror_bridged_incr", 1),
        ("converter_child_peak_rss_kib", "_mirror_bridged_set", 12345),
    ],
)
async def test_worker_mirror_helpers_write_to_bridge_key(name, helper, amount):
    from pageindex_mcp import worker
    from pageindex_mcp.metrics import bridge_redis_key

    fake_redis = AsyncMock()

    async def fake_get_async_redis():
        return fake_redis

    with patch("pageindex_mcp.worker.get_async_redis", fake_get_async_redis):
        await getattr(worker, helper)(name, amount)

    if helper == "_mirror_bridged_incr":
        fake_redis.incrby.assert_called_once_with(bridge_redis_key(name), amount)
    else:
        fake_redis.set.assert_called_once_with(bridge_redis_key(name), amount)


# ---------------------------------------------------------------------------
# Test 8: process_document_job stamps job_start_config / job_start_build_sha
# on every Redis status transition, including error paths that never reach
# save_doc_meta.
# ---------------------------------------------------------------------------

async def test_process_document_job_stamps_job_start_fields_on_success(monkeypatch):
    from pageindex_mcp import worker

    hset_calls = []

    class FakeRedis:
        async def hset(self, key, mapping):
            hset_calls.append(mapping)

        async def expire(self, key, ttl):
            pass

    async def fake_get_async_redis():
        return FakeRedis()

    async def fake_download_staging(staging_key, local_path):
        with open(local_path, "wb") as f:
            f.write(b"fake")

    async def fake_wait_for_memory(redis):
        pass

    async def fake_run_converter_subprocess(local_path, *, staging_key=None, job_start_config=None):
        assert job_start_config is not None
        return {"doc_id": "doc123"}

    async def fake_upsert_registry_row(doc_id, content_class):
        pass

    async def fake_delete_staging_thread(*args):
        return True

    monkeypatch.setattr(worker, "get_async_redis", fake_get_async_redis)
    monkeypatch.setattr(worker, "download_staging", lambda *a: None)
    monkeypatch.setattr(worker, "wait_for_memory", fake_wait_for_memory)
    monkeypatch.setattr(worker, "_run_converter_subprocess", fake_run_converter_subprocess)
    monkeypatch.setattr(worker, "_upsert_registry_row", fake_upsert_registry_row)
    monkeypatch.setattr(worker, "delete_staging", lambda *a: True)
    monkeypatch.setattr(
        worker,
        "asyncio",
        __import__("asyncio"),
    )

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


async def test_process_document_job_stamps_job_start_fields_on_converter_timeout(monkeypatch):
    from pageindex_mcp import worker

    hset_calls = []

    class FakeRedis:
        async def hset(self, key, mapping):
            hset_calls.append(mapping)

        async def expire(self, key, ttl):
            pass

    async def fake_get_async_redis():
        return FakeRedis()

    async def fake_wait_for_memory(redis):
        pass

    async def fake_run_converter_subprocess(local_path, *, staging_key=None, job_start_config=None):
        raise TimeoutError()

    monkeypatch.setattr(worker, "get_async_redis", fake_get_async_redis)
    monkeypatch.setattr(worker, "download_staging", lambda *a: None)
    monkeypatch.setattr(worker, "wait_for_memory", fake_wait_for_memory)
    monkeypatch.setattr(worker, "_run_converter_subprocess", fake_run_converter_subprocess)
    monkeypatch.setattr(worker, "delete_staging", lambda *a: True)

    async def fake_to_thread(fn, *args):
        return fn(*args)

    monkeypatch.setattr(worker.asyncio, "to_thread", fake_to_thread)

    ctx = {"redis": FakeRedis(), "job_try": 2}
    with pytest.raises(TimeoutError):
        await worker.process_document_job(ctx, "uploads/staging/job-1/f.pdf", "job-1")

    assert len(hset_calls) >= 2
    error_mapping = [m for m in hset_calls if m.get("status") == "error"][0]
    assert error_mapping["reason"] == "converter_timeout"
    assert "job_start_config" in error_mapping
    assert "job_start_build_sha" in error_mapping


# ---------------------------------------------------------------------------
# Test 9: client.index() config-drift detection stamps
# effective_config_at_job_start into the meta dict passed to save_doc_meta
# when job_start_config differs from the freshly computed snapshot.
# ---------------------------------------------------------------------------

def test_detect_config_drift_returns_none_when_no_job_start_config():
    from pageindex_mcp.client import _detect_config_drift

    assert _detect_config_drift(None, {"a": 1}) is None


def test_detect_config_drift_returns_none_when_configs_match():
    from pageindex_mcp.client import _detect_config_drift

    cfg = {"pipeline_version": 4, "ocr_escalation": True}
    assert _detect_config_drift(dict(cfg), cfg) is None


def test_detect_config_drift_returns_job_start_config_on_mismatch():
    from pageindex_mcp.client import _detect_config_drift

    job_start = {"pipeline_version": 4, "ocr_escalation": False}
    live = {"pipeline_version": 4, "ocr_escalation": True}
    assert _detect_config_drift(job_start, live) == job_start
