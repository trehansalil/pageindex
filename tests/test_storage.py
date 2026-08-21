# tests/test_storage.py
"""Consolidated tests for pageindex_mcp.storage: MinIO read/write, sidecar
persistence, the right-to-erasure cascade (ERASE-01 / Hard Rule 2), and the
hash-cache / staging helpers.

STORE-01-C1  save_doc persists the tree JSON to processed/<doc_id>.json
STORE-01-C2  re-uploading unchanged bytes is idempotent via SHA-256 dedup
STORE-01-C3  load_doc returns the exact bytes save_doc persisted
ERASE-01-C1  delete_doc cascades across stores in the mandated order
ERASE-01-C2  delete_doc is idempotent (missing objects tolerated, no-op success)
ERASE-01-C3  a mid-cascade failure is surfaced and names the unpurged store
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from minio.error import S3Error

from pageindex_mcp.helpers import _garble_check_nodes
from pageindex_mcp.storage import (
    _load_legacy_minio_hash_cache,
    delete_doc,
    delete_staging,
    get_flat_doc,
    hash_cache_delete,
    hash_cache_get,
    hash_cache_set,
    list_processed_docs,
    load_doc,
    read_registry_fields,
    save_doc,
    save_doc_meta,
    save_flat_doc,
    upload_staging,
    wipe_processed,
)


def _obj(name: str) -> MagicMock:
    obj = MagicMock()
    obj.object_name = name
    return obj


def _nosuchkey() -> S3Error:
    return S3Error(MagicMock(), "NoSuchKey", "missing", "res", "req", "host")


def _other_s3error(code="InternalError") -> S3Error:
    return S3Error(MagicMock(), code, "boom", "res", "req", "host")


@pytest.fixture
def mock_minio():
    client = MagicMock()
    client.bucket_exists.return_value = True
    with patch("pageindex_mcp.storage.minio_ops.get_minio", return_value=client):
        yield client


@pytest.fixture
def fake_cache_redis():
    import fakeredis

    fake = fakeredis.FakeRedis(decode_responses=True)
    with patch("pageindex_mcp.cache._redis_sync", fake):
        yield fake


def _wire_registry(monkeypatch, *, registry_delete_doc, get_pool_return=object()):
    import dataclasses

    from pageindex_mcp.storage import documents as _docs_mod

    monkeypatch.setattr(
        _docs_mod,
        "settings",
        dataclasses.replace(
            _docs_mod.settings,
            registry_enabled=True,
            postgres_dsn="postgresql://user:pass@localhost:5432/pageindex",
            registry_delete_timeout_s=0.05,
        ),
    )
    monkeypatch.setattr("pageindex_mcp.registry.delete_doc", registry_delete_doc)
    monkeypatch.setattr("pageindex_mcp.registry.get_pool", lambda: get_pool_return)


# ── wipe_processed() property tests (post-Zone-4 verdict ledger redesign) ────
# wipe_processed() deletes all processed/* objects and leaves the verdicts/
# prefix untouched. No snapshot step is involved.


@patch("pageindex_mcp.storage.minio_ops.get_minio")
def test_wipe_processed_deletes_all_processed_objects(mock_get):
    mc = MagicMock()
    mock_get.return_value = mc
    mc.list_objects.return_value = [
        _obj("processed/doc1.json"),
        _obj("processed/doc1.meta.json"),
        _obj("processed/doc2.json"),
    ]

    wipe_processed()

    remove_calls = [c for c in mc.mock_calls if c[0] == "remove_object"]
    removed = {c.args[1] for c in remove_calls}
    assert removed == {
        "processed/doc1.json",
        "processed/doc1.meta.json",
        "processed/doc2.json",
    }


@patch("pageindex_mcp.storage.minio_ops.get_minio")
def test_wipe_processed_empty_listing_is_noop(mock_get):
    mc = MagicMock()
    mock_get.return_value = mc
    mc.list_objects.return_value = []

    wipe_processed()

    mc.remove_object.assert_not_called()


# ── get_minio: lazy singleton / bucket-creation branch ───────────────────────
# ── STORE-01-C1/C2/C3 — save_doc / load_doc ───────────────────────────────────
def test_store_01_c1_save_doc_writes_processed_json(mock_minio):
    """STORE-01-C1: save_doc PUTs the serialized tree to processed/<doc_id>.json."""
    tree = {
        "doc_id": "abc12345",
        "doc_name": "t.pdf",
        "structure": [{"title": "Root", "nodes": [{"title": "C"}]}],
    }
    # save_doc lazily imports doc_cache_delete; patch the source so no Redis
    # is touched while we assert the MinIO write.
    with (
        patch("pageindex_mcp.cache.doc_cache_delete", create=True),
        patch("pageindex_mcp.cache.doc_cache_delete"),
    ):
        save_doc("abc12345", tree)

    mock_minio.put_object.assert_called_once()
    key = mock_minio.put_object.call_args[0][1]
    assert key == "processed/abc12345.json"
    written = mock_minio.put_object.call_args[0][2].read()
    assert json.loads(written) == tree


def test_load_doc_reraises_non_nosuchkey_s3error(mock_minio):
    mock_minio.get_object.side_effect = _other_s3error()
    with pytest.raises(S3Error):
        load_doc("abc12345")


# ── FLAT-02 — save_flat_doc / get_flat_doc ────────────────────────────────────
def test_flat_02_c1_save_flat_doc_writes_flat_json_and_meta(mock_minio):
    """FLAT-02-C1: save_flat_doc PUTs the flat blocks JSON to
    processed/<doc_id>.flat.json AND writes the processed/<doc_id>.meta.json
    sidecar carrying content_class; get_flat_doc returns a value-equivalent dict.
    No processed/<doc_id>.json (tree) is written for a flat doc."""
    flat = {
        "doc_id": "flat0001",
        "doc_name": "katzen.pdf",
        "content_class": "flat_prose",
        "blocks": [{"text": "Clause 1"}, {"text": "Clause 2"}],
    }
    with patch("pageindex_mcp.cache.doc_cache_delete"):
        save_flat_doc("flat0001", flat)

    put_keys = [c.args[1] for c in mock_minio.put_object.call_args_list]
    assert "processed/flat0001.flat.json" in put_keys
    assert "processed/flat0001.meta.json" in put_keys
    assert "processed/flat0001.json" not in put_keys

    flat_put = next(
        c
        for c in mock_minio.put_object.call_args_list
        if c.args[1] == "processed/flat0001.flat.json"
    )
    written = json.loads(flat_put.args[2].read())
    assert written == flat

    meta_put = next(
        c
        for c in mock_minio.put_object.call_args_list
        if c.args[1] == "processed/flat0001.meta.json"
    )
    meta_written = json.loads(meta_put.args[2].read())
    assert meta_written.get("content_class") == "flat_prose"

    response = MagicMock()
    response.read.return_value = json.dumps(flat, indent=2).encode()
    mock_minio.get_object.return_value = response
    loaded = get_flat_doc("flat0001")
    assert loaded == flat
    assert mock_minio.get_object.call_args[0][1] == "processed/flat0001.flat.json"


# ── FLAT-02-C3 — list_processed_docs surfaces flat docs + content_class ───────
def test_flat_02_c3_list_processed_docs_surfaces_flat_content_class(mock_minio):
    meta_obj = MagicMock()
    meta_obj.object_name = "processed/flat0001.meta.json"
    mock_minio.list_objects.return_value = [meta_obj]

    meta_resp = MagicMock()
    meta_resp.read.return_value = json.dumps(
        {
            "doc_id": "flat0001",
            "doc_name": "katzen.pdf",
            "content_class": "flat_prose",
        }
    ).encode()
    mock_minio.get_object.return_value = meta_resp

    docs = list_processed_docs()

    assert len(docs) == 1
    entry = docs[0]
    assert entry["doc_id"] == "flat0001"
    assert entry["doc_name"] == "katzen.pdf"
    assert entry["content_class"] == "flat_prose"


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


# ── read_registry_fields ──────────────────────────────────────────────────────
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


def test_read_registry_fields_missing_object_returns_none(mock_minio):
    mock_minio.get_object.side_effect = _nosuchkey()
    assert read_registry_fields("ghost0001") is None


# ── ERASE-01 — delete_doc cascade order / idempotency / partial failure ──────
async def test_erase_01_c2_idempotent_on_missing_doc(mock_minio):
    """ERASE-01-C2: deleting a never-existing / already-deleted doc_id tolerates
    missing objects (no S3Error/KeyError surfaced) and returns success."""
    mock_minio.get_object.side_effect = _nosuchkey()
    mock_minio.list_objects.return_value = []
    mock_minio.remove_object.side_effect = _nosuchkey()

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
        patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
    ):
        result = await delete_doc("ghost9999")  # must NOT raise
    assert result == {"errors": []}


async def test_flat_02_c2_flat_json_nosuchkey_tolerated(mock_minio):
    """FLAT-02-C2: a missing processed/<doc_id>.flat.json (NoSuchKey) is tolerated
    idempotently — deleting a tree-only doc does not raise on the flat step."""
    mock_minio.get_object.side_effect = _nosuchkey()
    mock_minio.list_objects.return_value = []
    mock_minio.remove_object.side_effect = _nosuchkey()

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
    ):
        await delete_doc("ghostflat")  # must NOT raise


async def test_delete_doc_read_doc_name_generic_exception_recorded(mock_minio):
    """A non-ValueError exception while reading doc_name for step 5 is
    recorded in errors, but the cascade continues (idempotent)."""
    mock_minio.get_object.side_effect = RuntimeError("minio unreachable")
    mock_minio.list_objects.return_value = []
    mock_minio.remove_object.side_effect = _nosuchkey()

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
    ):
        result = await delete_doc("weird0001")

    assert any("read-doc-name" in e for e in result["errors"])


@pytest.mark.parametrize(
    "target_key,error_fragment",
    [
        ("processed/errkey001.json", "processed.json"),
        ("processed/errkey001.meta.json", "processed.meta.json"),
        ("preloaded/report.pdf", "preloaded/"),
    ],
)
async def test_delete_doc_non_nosuchkey_remove_errors_recorded(
    mock_minio, target_key, error_fragment
):
    """A non-NoSuchKey S3Error while removing any cascade artifact is
    recorded in errors (not swallowed like NoSuchKey is)."""
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
        patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
    ):
        result = await delete_doc("errkey001")

    assert any(error_fragment in e for e in result["errors"])


# ── RFC-007 D9 / Property 8 — observable staging delete failure ─────────────
def test_delete_staging_success_returns_true(mock_minio):
    assert delete_staging("uploads/staging/job-1/report.pdf") is True


# ── RFC-007 D2 / Property 4 — awaited registry delete in the erasure cascade ─
# ── RFC-007 Task 3.4 — end-to-end erasure cascade across MinIO/Redis/Postgres ─
async def test_erasure_cascade_postgres_failure_still_cleans_minio_and_redis(
    monkeypatch, mock_minio
):
    """Postgres registry delete fails — the error is reported, but MinIO
    objects and the Redis cache key are still purged (HR2: partial failure
    never blocks the stores that *can* succeed)."""
    load_resp = MagicMock()
    load_resp.read.return_value = json.dumps(
        {"doc_id": "cascade002", "doc_name": "report.pdf"}
    ).encode()
    mock_minio.get_object.return_value = load_resp
    upload_obj = MagicMock()
    upload_obj.object_name = "uploads/cascade002/report.pdf"
    mock_minio.list_objects.return_value = [upload_obj]

    async def _registry_raises(doc_id):
        raise RuntimeError("postgres connection refused")

    _wire_registry(monkeypatch, registry_delete_doc=_registry_raises)

    with (
        patch("pageindex_mcp.cache.doc_cache_delete") as mock_cache_del,
        patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
        patch("pageindex_mcp.storage.hash_cache.hash_cache_delete") as mock_hash_del,
    ):
        result = await delete_doc("cascade002")

    removed_keys = [c.args[1] for c in mock_minio.remove_object.call_args_list]
    assert "processed/cascade002.json" in removed_keys
    assert "uploads/cascade002/report.pdf" in removed_keys
    mock_cache_del.assert_called_once_with("cascade002")
    mock_hash_del.assert_called_once_with("report.pdf")

    assert len(result["errors"]) == 1
    assert "registry" in result["errors"][0].lower()


# ── RFC-011 D2 / ISS-41 — erasure cascade purges preloaded/<doc_name> ────────
async def test_erasure_cascade_warns_when_doc_name_unknown_for_preloaded(mock_minio, caplog):
    """RFC-011 D2: when doc_name cannot be recovered, step 7 logs a warning
    and skips the preloaded/ purge rather than guessing a key."""
    mock_minio.get_object.side_effect = S3Error(
        MagicMock(), "NoSuchKey", "missing", "res", "req", "host"
    )
    mock_minio.list_objects.return_value = []
    mock_minio.remove_object.side_effect = S3Error(
        MagicMock(), "NoSuchKey", "missing", "res", "req", "host"
    )

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
        patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
        caplog.at_level("WARNING"),
    ):
        result = await delete_doc("nodocname001")

    assert any(
        "step7" in rec.getMessage() and "doc_name unknown" in rec.getMessage()
        for rec in caplog.records
    )
    preloaded_calls = [
        c for c in mock_minio.remove_object.call_args_list if c.args[1].startswith("preloaded/")
    ]
    assert preloaded_calls == []
    assert result["errors"] == []


# ── save_raw ───────────────────────────────────────────────────────────────
# ── upload_staging / download_staging ────────────────────────────────────────
def test_upload_staging_writes_and_returns_key(mock_minio):
    key = upload_staging("job-1", "report.pdf", b"bytes")
    assert key == "uploads/staging/job-1/report.pdf"
    call = mock_minio.put_object.call_args
    assert call[0][1] == "uploads/staging/job-1/report.pdf"
    assert call.kwargs["content_type"] == "application/octet-stream"


# ── _load_legacy_minio_hash_cache ────────────────────────────────────────────
def test_load_legacy_minio_hash_cache_missing_returns_empty(mock_minio):
    mock_minio.get_object.side_effect = _nosuchkey()
    assert _load_legacy_minio_hash_cache() == {}


# ── hash_cache_get / set / delete ────────────────────────────────────────────
def test_hash_cache_concurrent_workers(fake_cache_redis):
    """Property 6: two concurrent writes for DIFFERENT filenames both persist —
    HSET is atomic per-field, so no last-writer-wins loss (the bug the old
    instance-level asyncio.Lock over a MinIO JSON blob could not prevent
    across separate arq worker processes)."""
    hash_cache_set("a.pdf", "hash-a")
    hash_cache_set("b.pdf", "hash-b")

    assert hash_cache_get("a.pdf") == "hash-a"
    assert hash_cache_get("b.pdf") == "hash-b"


def test_hash_cache_delete_removes_entry(fake_cache_redis):
    hash_cache_set("d.pdf", "hash-d")
    assert hash_cache_get("d.pdf") == "hash-d"
    hash_cache_delete("d.pdf")
    with patch("pageindex_mcp.storage.minio_ops.get_minio") as mock_get_minio:
        mock_get_minio.return_value.get_object.side_effect = _nosuchkey()
        assert hash_cache_get("d.pdf") is None


# ── .meta.json sidecar: save_doc_meta ────────────────────────────────────────
def test_save_doc_meta_verdict_fields_present(mock_minio):
    """RFC-014 D2: verdict fields are included in sidecar when present."""
    meta = {
        "doc_id": "v001",
        "doc_name": "test.pdf",
        "source_url": "",
        "processed_at": "2026-07-16T00:00:00+00:00",
        "verdict": "PASS",
        "verdict_reason": "cat_b_promoted",
        "max_leaf_ratio": 0.12,
        "pipeline_version": 1,
        "permanent_marginal": False,
        "promotion_eligible": True,
        "verdict_computed_at": "2026-07-16T00:00:00+00:00",
    }
    save_doc_meta("v001", meta)

    written = mock_minio.put_object.call_args[0][2].read()
    sidecar = json.loads(written)
    assert sidecar["verdict"] == "PASS"
    assert sidecar["verdict_reason"] == "cat_b_promoted"
    assert sidecar["max_leaf_ratio"] == 0.12
    assert sidecar["pipeline_version"] == 1
    assert sidecar["permanent_marginal"] is False
    assert sidecar["promotion_eligible"] is True
    assert sidecar["verdict_computed_at"] == "2026-07-16T00:00:00+00:00"


# ── C-3 sidecar v2: sha256 + doc_description fattening ───────────────────────
def test_save_doc_meta_doc_description_empty_string_kept(mock_minio):
    """C-3: doc_description is written by KEY PRESENCE, not truthiness — an empty
    string is a valid description and must be persisted (so _is_fat sees it)."""
    meta = {
        "doc_id": "fat00002",
        "doc_name": "report.pdf",
        "source_url": "",
        "processed_at": "2026-07-21T00:00:00+00:00",
        "sha256": "abc",
        "doc_description": "",
    }
    save_doc_meta("fat00002", meta)

    written = mock_minio.put_object.call_args[0][2].read()
    sidecar = json.loads(written)
    assert "doc_description" in sidecar
    assert sidecar["doc_description"] == ""


# ── RFC-034 D5: extraction provenance fields ─────────────────────────────────
def test_save_doc_meta_provenance_fields_present(mock_minio):
    """RFC-034 D5: all 7 provenance fields are persisted in the sidecar when
    present in the caller's meta dict."""
    meta = {
        "doc_id": "prov0001",
        "doc_name": "report.pdf",
        "source_url": "",
        "processed_at": "2026-08-08T00:00:00+00:00",
        "extraction_route": "remote",
        "converter_name": "docling",
        "converter_contract": "2.1.0",
        "remote_build_sha": "abc1234",
        "page_count": 42,
        "inspector_class": "standard",
        "total_tree_chars": 123456,
    }
    save_doc_meta("prov0001", meta)

    written = mock_minio.put_object.call_args[0][2].read()
    sidecar = json.loads(written)
    assert sidecar["extraction_route"] == "remote"
    assert sidecar["converter_name"] == "docling"
    assert sidecar["converter_contract"] == "2.1.0"
    assert sidecar["remote_build_sha"] == "abc1234"
    assert sidecar["page_count"] == 42
    assert sidecar["inspector_class"] == "standard"
    assert sidecar["total_tree_chars"] == 123456


def test_save_doc_meta_effective_config_at_job_start_absent_when_not_supplied(mock_minio):
    meta = {
        "doc_id": "drift0002",
        "doc_name": "report.pdf",
        "source_url": "",
        "processed_at": "2026-08-11T00:00:00+00:00",
        "build_sha": "abc123",
        "effective_config": {"pipeline_version": 4},
    }
    save_doc_meta("drift0002", meta)

    written = mock_minio.put_object.call_args[0][2].read()
    sidecar = json.loads(written)
    assert "effective_config_at_job_start" not in sidecar


# ── Zone 6: read-merge-write ─────────────────────────────────────────────────
# ── RFC-018 D3b: per-node garble ratio gate ─────────────────────────────────
def _pua_heavy_text() -> str:
    """4 PUA chars (U+E000-U+E003) in a 16-char blob = 25% PUA ratio, well
    above the 3% per-blob PUA threshold used by ``_is_garbled_blob``."""
    return " normal text"


def _clean_node(i: int) -> dict:
    return {"title": f"Section {i}", "text": f"This is section {i} content"}


def test_per_node_garble_catches_pua_node():
    """RFC-018 D3b: a single PUA-heavy node among 99 clean siblings is counted
    exactly once by _garble_check_nodes, even though the bulk/flattened text
    ratio would dilute the PUA signal well under the 3% blob-level gate."""
    garbled_node = {"title": "Bad", "text": _pua_heavy_text()}
    tree = [garbled_node] + [_clean_node(i) for i in range(99)]

    assert _garble_check_nodes(tree) == 1
