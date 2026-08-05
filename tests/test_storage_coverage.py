"""Additional coverage for pageindex_mcp.storage error branches, the legacy
hash-cache migration path and upload-staging helpers,
that the behavioral-contract test files (test_storage_contract.py,
test_storage_meta.py) don't already exercise."""

import json
from unittest.mock import MagicMock, patch

import pytest
from minio.error import S3Error

import pageindex_mcp.storage as storage_mod
from pageindex_mcp.storage import (
    _load_legacy_minio_hash_cache,
    download_staging,
    get_flat_doc,
    get_minio,
    hash_cache_delete,
    hash_cache_get,
    hash_cache_set,
    list_processed_docs,
    load_doc,
    read_registry_fields,
    save_raw,
    upload_staging,
)


def _nosuchkey() -> S3Error:
    return S3Error(MagicMock(), "NoSuchKey", "missing", "res", "req", "host")


def _other_s3error(code="InternalError") -> S3Error:
    return S3Error(MagicMock(), code, "boom", "res", "req", "host")


@pytest.fixture
def mock_minio():
    client = MagicMock()
    client.bucket_exists.return_value = True
    with patch("pageindex_mcp.storage.get_minio", return_value=client):
        yield client


# ── get_minio: lazy singleton bucket-creation branch (lines 41-44) ──────────
def test_get_minio_creates_bucket_when_missing(monkeypatch):
    monkeypatch.setattr(storage_mod, "_minio_client", None)
    fake_client = MagicMock()
    fake_client.bucket_exists.return_value = False
    with patch("pageindex_mcp.storage.make_minio", return_value=fake_client) as mock_minio_cls:
        result = get_minio()

    mock_minio_cls.assert_called_once()
    fake_client.make_bucket.assert_called_once_with(storage_mod.settings.minio_bucket)
    assert result is fake_client
    # Second call must reuse the cached singleton, not construct again.
    with patch("pageindex_mcp.storage.make_minio") as mock_minio_cls_2:
        result2 = get_minio()
    mock_minio_cls_2.assert_not_called()
    assert result2 is fake_client

    monkeypatch.setattr(storage_mod, "_minio_client", None)


def test_get_minio_skips_bucket_creation_when_exists(monkeypatch):
    monkeypatch.setattr(storage_mod, "_minio_client", None)
    fake_client = MagicMock()
    fake_client.bucket_exists.return_value = True
    with patch("pageindex_mcp.storage.make_minio", return_value=fake_client):
        get_minio()
    fake_client.make_bucket.assert_not_called()
    monkeypatch.setattr(storage_mod, "_minio_client", None)


# ── load_doc: non-NoSuchKey S3Error re-raised; response-close swallow ───────
def test_load_doc_reraises_non_nosuchkey_s3error(mock_minio):
    mock_minio.get_object.side_effect = _other_s3error()
    with pytest.raises(S3Error):
        load_doc("abc12345")


def test_load_doc_response_close_exception_is_swallowed(mock_minio):
    response = MagicMock()
    response.read.return_value = json.dumps({"doc_id": "abc"}).encode()
    response.close.side_effect = RuntimeError("close failed")
    mock_minio.get_object.return_value = response

    loaded = load_doc("abc12345")  # must not raise despite close() blowing up
    assert loaded == {"doc_id": "abc"}


# ── get_flat_doc: mirrors load_doc's error branches ─────────────────────────
def test_get_flat_doc_not_found_raises_valueerror(mock_minio):
    mock_minio.get_object.side_effect = _nosuchkey()
    with pytest.raises(ValueError, match="Flat document not found"):
        get_flat_doc("flat0001")


def test_get_flat_doc_reraises_non_nosuchkey_s3error(mock_minio):
    mock_minio.get_object.side_effect = _other_s3error()
    with pytest.raises(S3Error):
        get_flat_doc("flat0001")


def test_get_flat_doc_response_release_conn_exception_is_swallowed(mock_minio):
    response = MagicMock()
    response.read.return_value = json.dumps({"doc_id": "flat0001"}).encode()
    response.release_conn.side_effect = RuntimeError("release failed")
    mock_minio.get_object.return_value = response

    loaded = get_flat_doc("flat0001")
    assert loaded == {"doc_id": "flat0001"}


# ── delete_doc: remaining error branches not covered by the contract tests ──
async def test_delete_doc_read_doc_name_generic_exception_recorded(mock_minio):
    """Lines 182-183: a non-ValueError exception while reading doc_name for
    step 5 is recorded in errors, but the cascade continues (idempotent)."""
    from pageindex_mcp.storage import delete_doc

    mock_minio.get_object.side_effect = RuntimeError("minio unreachable")
    mock_minio.list_objects.return_value = []
    mock_minio.remove_object.side_effect = _nosuchkey()

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.hash_cache_delete"),
    ):
        result = await delete_doc("weird0001")

    assert any("read-doc-name" in e for e in result["errors"])


async def test_delete_doc_uploads_listing_s3error_recorded(mock_minio):
    """Lines 205-206: mc.list_objects raising S3Error during step 1 is
    recorded in errors rather than propagating."""
    from pageindex_mcp.storage import delete_doc

    mock_minio.get_object.side_effect = _nosuchkey()
    mock_minio.list_objects.side_effect = _other_s3error()
    mock_minio.remove_object.side_effect = _nosuchkey()

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.hash_cache_delete"),
    ):
        result = await delete_doc("listfail001")

    assert any("uploads/" in e for e in result["errors"])


async def test_delete_doc_uploads_object_name_none_skipped(mock_minio):
    """Line 194: an upload listing entry with a falsy object_name is skipped
    (continue) instead of being passed to remove_object."""
    from pageindex_mcp.storage import delete_doc

    mock_minio.get_object.side_effect = _nosuchkey()
    blank_obj = MagicMock()
    blank_obj.object_name = ""
    mock_minio.list_objects.return_value = [blank_obj]
    mock_minio.remove_object.side_effect = _nosuchkey()

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.reconcile_etag_delete"),
        patch("pageindex_mcp.storage.hash_cache_delete"),
    ):
        result = await delete_doc("blankobj001")

    assert result["errors"] == []


async def test_delete_doc_recovers_doc_name_from_upload_basename(mock_minio):
    """Lines 199-201: when load_doc yields no doc_name (flat doc / missing
    processed.json), the basename of an uploads/ object recovers it so step 5
    (hash-cache clear) can still run."""
    from pageindex_mcp.storage import delete_doc

    mock_minio.get_object.side_effect = _nosuchkey()
    upload_obj = MagicMock()
    upload_obj.object_name = "uploads/recov0001/basename.pdf"
    mock_minio.list_objects.return_value = [upload_obj]
    mock_minio.remove_object.side_effect = _nosuchkey()

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.hash_cache_delete") as mock_hash_del,
    ):
        await delete_doc("recov0001")

    mock_hash_del.assert_called_once_with("basename.pdf")


@pytest.mark.parametrize(
    "target_key,error_fragment",
    [
        ("processed/errkey001.json", "processed.json"),
        ("processed/errkey001.flat.json", "processed.flat.json"),
        ("processed/errkey001.meta.json", "processed.meta.json"),
        ("preloaded/report.pdf", "preloaded/"),
    ],
)
async def test_delete_doc_non_nosuchkey_remove_errors_recorded(
    mock_minio, target_key, error_fragment
):
    """Lines 214, 222, 230, 283-285: a non-NoSuchKey S3Error while removing any
    of the four cascade artifacts is recorded in errors (not swallowed like
    NoSuchKey is)."""
    from pageindex_mcp.storage import delete_doc

    load_resp = MagicMock()
    load_resp.read.return_value = json.dumps(
        {"doc_id": "errkey001", "doc_name": "report.pdf"}
    ).encode()
    mock_minio.get_object.return_value = load_resp
    mock_minio.list_objects.return_value = []

    def _remove(bucket, name):
        if name == target_key:
            raise _other_s3error()
        raise _nosuchkey()

    mock_minio.remove_object.side_effect = _remove

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.hash_cache_delete"),
    ):
        result = await delete_doc("errkey001")

    assert any(error_fragment in e for e in result["errors"])


async def test_delete_doc_hash_cache_delete_exception_recorded(mock_minio):
    """Lines 246-247: hash_cache_delete raising is recorded as a `hash-cache`
    error rather than propagating out of delete_doc."""
    from pageindex_mcp.storage import delete_doc

    load_resp = MagicMock()
    load_resp.read.return_value = json.dumps(
        {"doc_id": "hcfail001", "doc_name": "report.pdf"}
    ).encode()
    mock_minio.get_object.return_value = load_resp
    mock_minio.list_objects.return_value = []
    mock_minio.remove_object.side_effect = _nosuchkey()

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.hash_cache_delete", side_effect=RuntimeError("hcache down")),
    ):
        result = await delete_doc("hcfail001")

    assert any("hash-cache" in e for e in result["errors"])


# ── read_registry_fields (lines 406-442) ────────────────────────────────────
def test_read_registry_fields_tree_doc_success(mock_minio):
    persisted = {
        "doc_id": "tree0001",
        "doc_name": "report.pdf",
        "source_url": "http://x",
        "processed_at": "2026-01-01T00:00:00Z",
        "sha256": "abc123",
        "doc_description": "desc",
        "product": "prod-a",
        "tier": "1",
        "doc_family": "fam",
        "effective_date": "2026-01-01",
        "structure": [{"title": "Ch1", "nodes": []}],
        "verdict": "PASS",
        "pipeline_version": 2,
        "permanent_marginal": False,
    }
    response = MagicMock()
    response.read.return_value = json.dumps(persisted).encode()
    mock_minio.get_object.return_value = response

    fields = read_registry_fields("tree0001")

    assert mock_minio.get_object.call_args[0][1] == "processed/tree0001.json"
    assert fields["doc_id"] == "tree0001"
    assert fields["sha256"] == "abc123"
    assert fields["node_count"] == 1
    assert fields["verdict"] == "PASS"
    assert fields["pipeline_version"] == 2
    assert fields["permanent_marginal"] is False
    assert "content_class" not in fields


def test_read_registry_fields_flat_doc_reads_flat_json(mock_minio):
    persisted = {"doc_id": "flat0001", "doc_name": "x.pdf"}
    response = MagicMock()
    response.read.return_value = json.dumps(persisted).encode()
    mock_minio.get_object.return_value = response

    fields = read_registry_fields("flat0001", content_class="flat_prose")

    assert mock_minio.get_object.call_args[0][1] == "processed/flat0001.flat.json"
    assert fields["content_class"] == "flat_prose"
    assert fields["node_count"] == 0


def test_read_registry_fields_missing_object_returns_none(mock_minio):
    mock_minio.get_object.side_effect = _nosuchkey()
    assert read_registry_fields("ghost0001") is None


def test_read_registry_fields_malformed_json_returns_none(mock_minio):
    response = MagicMock()
    response.read.return_value = b"not json{"
    mock_minio.get_object.return_value = response
    assert read_registry_fields("bad0001") is None


def test_read_registry_fields_response_close_swallowed(mock_minio):
    response = MagicMock()
    response.read.return_value = json.dumps({"doc_id": "closeerr"}).encode()
    response.close.side_effect = RuntimeError("boom")
    mock_minio.get_object.return_value = response
    fields = read_registry_fields("closeerr")
    assert fields["doc_id"] == "closeerr"


# ── list_processed_docs edge cases (lines 460-462, 486-488, 494-495) ────────
def test_list_processed_docs_flat_json_used_when_no_meta_sidecar(mock_minio):
    flat_obj = MagicMock()
    flat_obj.object_name = "processed/flatonly001.flat.json"
    mock_minio.list_objects.return_value = [flat_obj]

    response = MagicMock()
    response.read.return_value = json.dumps(
        {"doc_id": "flatonly001", "doc_name": "x.pdf", "content_class": "flat_prose"}
    ).encode()
    mock_minio.get_object.return_value = response

    docs = list_processed_docs()
    assert len(docs) == 1
    assert docs[0]["doc_id"] == "flatonly001"
    assert mock_minio.get_object.call_args[0][1] == "processed/flatonly001.flat.json"


def test_list_processed_docs_meta_sidecar_preferred_over_flat_json(mock_minio):
    flat_obj = MagicMock()
    flat_obj.object_name = "processed/dup0001.flat.json"
    meta_obj = MagicMock()
    meta_obj.object_name = "processed/dup0001.meta.json"
    mock_minio.list_objects.return_value = [flat_obj, meta_obj]

    response = MagicMock()
    response.read.return_value = json.dumps({"doc_id": "dup0001", "doc_name": "y.pdf"}).encode()
    mock_minio.get_object.return_value = response

    docs = list_processed_docs()
    assert len(docs) == 1
    assert mock_minio.get_object.call_args[0][1] == "processed/dup0001.meta.json"


def test_list_processed_docs_fetch_failure_is_skipped(mock_minio):
    """Lines 486-488: a per-doc fetch exception logs a warning and yields None,
    which is filtered out of the final docs list rather than failing the call."""
    good_obj = MagicMock()
    good_obj.object_name = "processed/good0001.meta.json"
    bad_obj = MagicMock()
    bad_obj.object_name = "processed/bad0001.meta.json"
    mock_minio.list_objects.return_value = [good_obj, bad_obj]

    good_response = MagicMock()
    good_response.read.return_value = json.dumps(
        {"doc_id": "good0001", "doc_name": "g.pdf"}
    ).encode()

    def _get_object(bucket, key):
        if key == "processed/bad0001.meta.json":
            raise RuntimeError("corrupt object")
        return good_response

    mock_minio.get_object.side_effect = _get_object

    docs = list_processed_docs()
    assert len(docs) == 1
    assert docs[0]["doc_id"] == "good0001"


def test_list_processed_docs_response_close_exception_swallowed(mock_minio):
    meta_obj = MagicMock()
    meta_obj.object_name = "processed/closeerr001.meta.json"
    mock_minio.list_objects.return_value = [meta_obj]

    response = MagicMock()
    response.read.return_value = json.dumps({"doc_id": "closeerr001", "doc_name": "c.pdf"}).encode()
    response.release_conn.side_effect = RuntimeError("release boom")
    mock_minio.get_object.return_value = response

    docs = list_processed_docs()
    assert len(docs) == 1
    assert docs[0]["doc_id"] == "closeerr001"


# ── save_raw (lines 530-544) ─────────────────────────────────────────────────
def test_save_raw_pdf_content_type(mock_minio):
    save_raw("doc001", "report.pdf", b"%PDF-1.7 bytes")
    mock_minio.put_object.assert_called_once()
    call = mock_minio.put_object.call_args
    assert call[0][1] == "uploads/doc001/report.pdf"
    assert call.kwargs["content_type"] == "application/pdf"


def test_save_raw_non_pdf_content_type(mock_minio):
    save_raw("doc002", "scan.png", b"\x89PNG")
    call = mock_minio.put_object.call_args
    assert call[0][1] == "uploads/doc002/scan.png"
    assert call.kwargs["content_type"] == "application/octet-stream"


# ── _load_legacy_minio_hash_cache (lines 563-577) ───────────────────────────
def test_load_legacy_minio_hash_cache_returns_parsed_json(mock_minio):
    response = MagicMock()
    response.read.return_value = json.dumps({"a.pdf": "hash-a"}).encode()
    mock_minio.get_object.return_value = response

    result = _load_legacy_minio_hash_cache()
    assert result == {"a.pdf": "hash-a"}
    mock_minio.get_object.assert_called_once_with(
        storage_mod.settings.minio_bucket, storage_mod.HASH_OBJECT
    )


def test_load_legacy_minio_hash_cache_missing_returns_empty(mock_minio):
    mock_minio.get_object.side_effect = _nosuchkey()
    assert _load_legacy_minio_hash_cache() == {}


def test_load_legacy_minio_hash_cache_reraises_other_s3error(mock_minio):
    mock_minio.get_object.side_effect = _other_s3error()
    with pytest.raises(S3Error):
        _load_legacy_minio_hash_cache()


def test_load_legacy_minio_hash_cache_response_close_swallowed(mock_minio):
    response = MagicMock()
    response.read.return_value = json.dumps({"a.pdf": "h"}).encode()
    response.close.side_effect = RuntimeError("boom")
    mock_minio.get_object.return_value = response
    assert _load_legacy_minio_hash_cache() == {"a.pdf": "h"}


# ── hash_cache_get / set / delete edge cases ────────────────────────────────
@pytest.fixture
def fake_cache_redis():
    import fakeredis

    fake = fakeredis.FakeRedis(decode_responses=True)
    with patch("pageindex_mcp.cache._redis_sync", fake):
        yield fake


def test_hash_cache_get_falls_back_to_legacy_minio(fake_cache_redis, mock_minio):
    """Redis miss falls through to the legacy MinIO blob (D6 migration window)."""
    response = MagicMock()
    response.read.return_value = json.dumps({"legacy.pdf": "legacy-hash"}).encode()
    mock_minio.get_object.return_value = response

    assert hash_cache_get("legacy.pdf") == "legacy-hash"


def test_hash_cache_get_legacy_fallback_exception_returns_none(fake_cache_redis, mock_minio):
    mock_minio.get_object.side_effect = RuntimeError("minio down")
    assert hash_cache_get("whatever.pdf") is None


def test_hash_cache_delete_removes_entry(fake_cache_redis):
    hash_cache_set("d.pdf", "hash-d")
    assert hash_cache_get("d.pdf") == "hash-d"
    hash_cache_delete("d.pdf")
    with patch("pageindex_mcp.storage.get_minio") as mock_get_minio:
        mock_get_minio.return_value.get_object.side_effect = _nosuchkey()
        assert hash_cache_get("d.pdf") is None


# ── upload_staging / download_staging (lines 618-633, 638-645) ─────────────
def test_upload_staging_writes_and_returns_key(mock_minio):
    key = upload_staging("job-1", "report.pdf", b"bytes")
    assert key == "uploads/staging/job-1/report.pdf"
    call = mock_minio.put_object.call_args
    assert call[0][1] == "uploads/staging/job-1/report.pdf"
    assert call.kwargs["content_type"] == "application/octet-stream"


def test_download_staging_calls_fget_object(mock_minio):
    download_staging("uploads/staging/job-1/report.pdf", "/tmp/out.pdf")
    mock_minio.fget_object.assert_called_once_with(
        storage_mod.settings.minio_bucket,
        "uploads/staging/job-1/report.pdf",
        "/tmp/out.pdf",
    )
