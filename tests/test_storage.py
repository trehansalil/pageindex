# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Storage operations: MinIO path prefix, presign public route, and core storage tests."""
from __future__ import annotations

import importlib
import inspect
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import urllib3
from minio.error import S3Error

from pageindex_mcp.helpers import GarbleConfig, ScriptContext, _garble_check_nodes
from pageindex_mcp.minio_client import PrefixedPoolManager, make_minio
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


# --- from test_storage.py ---


def _obj(name: str) -> MagicMock:
    obj = MagicMock()
    obj.object_name = name
    return obj


def _nosuchkey() -> S3Error:
    return S3Error(MagicMock(), "NoSuchKey", "missing", "res", "req", "host")


def _other_s3error(code="InternalError") -> S3Error:
    return S3Error(MagicMock(), code, "boom", "res", "req", "host")


@pytest.fixture
def fake_cache_redis(fake_redis_sync):
    with patch("pageindex_mcp.cache._redis_sync", fake_redis_sync):
        yield fake_redis_sync


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
def test_flat_02_c1_save_flat_doc_writes_flat_json_only(mock_minio):
    """FLAT-02-C1: save_flat_doc PUTs the flat blocks JSON to
    processed/<doc_id>.flat.json; get_flat_doc returns a value-equivalent
    dict. No processed/<doc_id>.json (tree) is written for a flat doc.

    RFC-042 D3: the processed/<doc_id>.meta.json sidecar is no longer
    written here -- that write-through belongs solely to
    _upsert_registry_row (registry_mirror.py), which backfills the sidecar
    from the Postgres-arbitrated row after the worker parent's dual-write.
    """
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
    assert "processed/flat0001.meta.json" not in put_keys
    assert "processed/flat0001.json" not in put_keys

    flat_put = next(
        c
        for c in mock_minio.put_object.call_args_list
        if c.args[1] == "processed/flat0001.flat.json"
    )
    written = json.loads(flat_put.args[2].read())
    assert written == flat

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
    # Zone-4 Phase 3 / HR2: registry pool is never initialized in this test
    # process, so the cascade surfaces the skip as an observable error
    # instead of silently dropping the Postgres row deletion.
    assert result["errors"] == ["registry: pool not ready, skipped Postgres row deletion"]


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
    # Zone-4 Phase 3 / HR2: registry pool is never initialized in this test
    # process, so the cascade surfaces the skip as an observable error
    # instead of silently dropping the Postgres row deletion.
    assert result["errors"] == ["registry: pool not ready, skipped Postgres row deletion"]


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

    assert _garble_check_nodes(
        tree,
        script_context=ScriptContext(dominant_script=None, had_presentation_forms=False, source="test"),
        config=GarbleConfig(),
    ) == 1


# ---------------------------------------------------------------------------
# Zone-4 Phase 3: delete_doc errors[] observable for registry skip (contract)
# ---------------------------------------------------------------------------


async def test_delete_doc_errors_registry_disabled(mock_minio):
    """When registry_enabled=False, delete_doc appends an observable
    errors[] entry so the caller knows erasure did not reach Postgres."""
    import dataclasses

    from pageindex_mcp.storage import documents as _docs_mod

    mock_minio.get_object.side_effect = _nosuchkey()
    mock_minio.list_objects.return_value = []
    mock_minio.remove_object.side_effect = _nosuchkey()

    original = _docs_mod.settings
    patched = dataclasses.replace(original, registry_enabled=False, postgres_dsn="")
    with (
        patch.object(_docs_mod, "settings", patched),
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
        patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
    ):
        result = await delete_doc("reg-off-1")

    registry_errors = [e for e in result["errors"] if "registry" in e.lower()]
    assert len(registry_errors) >= 1
    assert any("skipped" in e.lower() or "registry_enabled" in e.lower() for e in registry_errors)


async def test_delete_doc_errors_pool_not_ready(monkeypatch, mock_minio):
    """When pool is not ready, delete_doc appends an observable errors[] entry."""
    mock_minio.get_object.side_effect = _nosuchkey()
    mock_minio.list_objects.return_value = []
    mock_minio.remove_object.side_effect = _nosuchkey()

    _wire_registry(monkeypatch, registry_delete_doc=AsyncMock(), get_pool_return=None)

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
        patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
    ):
        result = await delete_doc("pool-down-1")

    registry_errors = [e for e in result["errors"] if "registry" in e.lower()]
    assert len(registry_errors) >= 1
    assert any("pool not ready" in e.lower() for e in registry_errors)


# ---------------------------------------------------------------------------
# Zone-4 Phase 3: save_doc_meta no longer calls _confirm_write_visible
# (regression test)
# ---------------------------------------------------------------------------


def test_save_doc_meta_does_not_call_confirm_write_visible(mock_minio):
    """Zone-4 Phase 3: save_doc_meta must NOT call _confirm_write_visible.
    The sidecar is archival-only; the barrier was removed."""
    meta = {
        "doc_id": "barrier-1",
        "doc_name": "test.pdf",
        "source_url": "",
        "processed_at": "2026-08-21T00:00:00+00:00",
    }
    with patch(
        "pageindex_mcp.storage.minio_ops._confirm_write_visible"
    ) as mock_barrier:
        save_doc_meta("barrier-1", meta)

    mock_barrier.assert_not_called()
    # But put_object IS called (the sidecar is still written)
    mock_minio.put_object.assert_called_once()


# ---------------------------------------------------------------------------
# Zone-4 Phase 3: delete_doc surfaces registry timeout in errors[] (contract)
# ---------------------------------------------------------------------------


async def test_delete_doc_errors_registry_timeout(monkeypatch, mock_minio):
    """Zone-4 Phase 3 / HR2: when the registry delete times out, the timeout
    is surfaced as an observable errors[] entry (not silently swallowed)."""
    import asyncio as _asyncio

    load_resp = MagicMock()
    load_resp.read.return_value = json.dumps(
        {"doc_id": "timeout-1", "doc_name": "report.pdf"}
    ).encode()
    mock_minio.get_object.return_value = load_resp
    mock_minio.list_objects.return_value = []

    async def _slow_delete(doc_id):
        await _asyncio.sleep(10)  # longer than the timeout

    _wire_registry(monkeypatch, registry_delete_doc=_slow_delete)

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
        patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
    ):
        result = await delete_doc("timeout-1")

    registry_errors = [e for e in result["errors"] if "registry" in e.lower()]
    assert len(registry_errors) >= 1
    assert any("timed out" in e.lower() or "timeout" in e.lower() for e in registry_errors)


# ---------------------------------------------------------------------------
# Zone-4 Phase 3: save_doc retains write-visibility barrier (contract)
# ---------------------------------------------------------------------------


def test_save_doc_still_calls_confirm_write_visible(mock_minio):
    """Zone-4 Phase 3 contract: save_doc (primary processed artifact) must
    STILL call _confirm_write_visible -- the barrier removal is scoped
    exclusively to save_doc_meta (sidecar), not save_doc."""
    tree = {
        "doc_id": "barrier-keep-1",
        "doc_name": "t.pdf",
        "structure": [{"title": "Root", "nodes": []}],
    }
    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch(
            "pageindex_mcp.storage.minio_ops._confirm_write_visible"
        ) as mock_barrier,
    ):
        save_doc("barrier-keep-1", tree)

    mock_barrier.assert_called_once()


# ---------------------------------------------------------------------------
# Zone-7 (HR2 compliance): hash_cache_delete purges both Redis AND legacy MinIO
# ---------------------------------------------------------------------------


def test_hash_cache_delete_issues_redis_hdel_and_legacy_purge(fake_cache_redis):
    """Contract: hash_cache_delete must issue both Redis HDEL AND attempt
    legacy MinIO blob purge. When legacy blob contains the filename, it
    must be removed."""
    hash_cache_set("purge.pdf", "hash-purge")
    assert hash_cache_get("purge.pdf") == "hash-purge"

    with patch(
        "pageindex_mcp.storage.hash_cache._purge_legacy_hash_entry"
    ) as mock_legacy:
        hash_cache_delete("purge.pdf")

    # Redis entry removed
    assert fake_cache_redis.hget("pageindex:hashes", "purge.pdf") is None
    # Legacy purge attempted
    mock_legacy.assert_called_once_with("purge.pdf")


def test_hash_cache_delete_legacy_blob_not_exist_no_error(fake_cache_redis):
    """Contract: when legacy blob does not exist, no error is raised."""
    hash_cache_set("nolegacy.pdf", "hash-nolegacy")

    with patch(
        "pageindex_mcp.storage.hash_cache._load_legacy_minio_hash_cache",
        return_value={},
    ):
        # Should not raise
        hash_cache_delete("nolegacy.pdf")

    assert fake_cache_redis.hget("pageindex:hashes", "nolegacy.pdf") is None


def test_hash_cache_delete_legacy_purge_failure_redis_still_deleted(fake_cache_redis):
    """Contract: when legacy blob purge fails, Redis HDEL must still have
    succeeded (best-effort)."""
    hash_cache_set("faillegacy.pdf", "hash-fail")

    # Exercise the REAL _purge_legacy_hash_entry against an unreachable MinIO:
    # the guard inside it must swallow the failure so hash_cache_delete never
    # raises and the Redis HDEL stands.
    with patch(
        "pageindex_mcp.storage.minio_ops.get_minio",
        side_effect=RuntimeError("MinIO down"),
    ):
        hash_cache_delete("faillegacy.pdf")  # must NOT raise

    assert fake_cache_redis.hget("pageindex:hashes", "faillegacy.pdf") is None

    # Same guarantee when the blob loads but the write-back put_object fails.
    hash_cache_set("faillegacy2.pdf", "hash-fail2")
    mc = MagicMock()
    mc.put_object.side_effect = RuntimeError("write-back refused")
    with (
        patch("pageindex_mcp.storage.minio_ops.get_minio", return_value=mc),
        patch(
            "pageindex_mcp.storage.hash_cache._load_legacy_minio_hash_cache",
            return_value={"faillegacy2.pdf": "stale"},
        ),
    ):
        hash_cache_delete("faillegacy2.pdf")  # must NOT raise

    assert fake_cache_redis.hget("pageindex:hashes", "faillegacy2.pdf") is None


# ---------------------------------------------------------------------------
# Exhaustiveness: _ERASURE_MANIFEST step ordering (HR2 cascade order)
# ---------------------------------------------------------------------------


def test_erasure_manifest_ordering_matches_hr2_spec():
    """Exhaustiveness: _ERASURE_MANIFEST step names must appear in HR2 cascade
    order (uploads, processed, meta, redis-cache, reconcile-etag, hash-cache,
    registry, preloaded). Each step must be an ErasureStep instance."""
    from pageindex_mcp.storage.documents import ErasureStep, _ERASURE_MANIFEST

    # All entries are ErasureStep instances
    for entry in _ERASURE_MANIFEST:
        assert isinstance(entry, ErasureStep), (
            f"Expected ErasureStep, got {type(entry).__name__}"
        )

    # Step numbers must be non-decreasing (manifest is ordered by step)
    step_numbers = [e.step for e in _ERASURE_MANIFEST]
    assert step_numbers == sorted(step_numbers), (
        f"Manifest steps not in non-decreasing order: {step_numbers}"
    )

    # All required step names must be present
    expected_names = {
        "uploads", "processed_json", "processed_flat_json", "figures",
        "verdicts", "meta_json", "redis_cache", "reconcile_etag",
        "hash_cache", "registry", "preloaded",
    }
    actual_names = {e.name for e in _ERASURE_MANIFEST}
    assert actual_names == expected_names, (
        f"Missing: {expected_names - actual_names}; Extra: {actual_names - expected_names}"
    )

    # Verify ordering: uploads (1) < processed (2) < meta (3) < redis/etag (4) < hash (5) < registry (6) < preloaded (7)
    name_to_step = {e.name: e.step for e in _ERASURE_MANIFEST}
    assert name_to_step["uploads"] == 1
    assert name_to_step["processed_json"] == 2
    assert name_to_step["meta_json"] == 3
    assert name_to_step["redis_cache"] == 4
    assert name_to_step["hash_cache"] == 5
    assert name_to_step["registry"] == 6
    assert name_to_step["preloaded"] == 7

    # Relative ordering of the manifest tuple itself (drives execution order).
    order = [e.name for e in _ERASURE_MANIFEST]
    for earlier, later in (
        ("uploads", "processed_json"),
        ("processed_json", "meta_json"),
        ("meta_json", "redis_cache"),
        ("redis_cache", "reconcile_etag"),
        ("reconcile_etag", "hash_cache"),
        ("hash_cache", "registry"),
        ("registry", "preloaded"),
    ):
        assert order.index(earlier) < order.index(later), (
            f"HR2 cascade violated: {earlier} must precede {later} in {order}"
        )


def test_erasure_manifest_required_flags_match_behaviour():
    """Exhaustiveness: every manifest step's ``required`` flag is pinned, so a
    store silently flipping from compliance-mandatory to optional (or back)
    breaks this test rather than the HR2 audit."""
    from pageindex_mcp.storage.documents import _ERASURE_MANIFEST

    expected_required = {
        "uploads": True,
        "processed_json": True,
        # Optional: only flat-doc ingests emit a .flat.json artifact.
        "processed_flat_json": False,
        # Optional: text-only documents never produce figure crops.
        "figures": False,
        # Optional: an unreachable sidecar carries no sha256 to key on.
        "verdicts": False,
        "meta_json": True,
        "redis_cache": True,
        "reconcile_etag": True,
        "hash_cache": True,
        "registry": True,
        # Optional: RFC-011 D2 — only preloaded ingests have a raw object here.
        "preloaded": False,
    }
    actual_required = {e.name: e.required for e in _ERASURE_MANIFEST}
    assert actual_required == expected_required

    # Every step exposes a non-empty description and an awaitable executor.
    for entry in _ERASURE_MANIFEST:
        assert entry.description.strip(), f"{entry.name} has no description"
        assert callable(entry.execute), f"{entry.name}.execute is not callable"
        assert inspect.iscoroutinefunction(entry.execute), (
            f"{entry.name}.execute must be a coroutine function"
        )


# ---------------------------------------------------------------------------
# Regression: delete_doc with declarative manifest produces equivalent output
# ---------------------------------------------------------------------------


async def test_delete_doc_full_success_returns_registry_only_error(mock_minio, monkeypatch):
    """Regression: full success scenario -- all stores cleared, only registry
    skip error (pool not initialized in test) is returned."""
    load_resp = MagicMock()
    load_resp.read.return_value = json.dumps(
        {"doc_id": "regr-ok-1", "doc_name": "report.pdf"}
    ).encode()
    mock_minio.get_object.return_value = load_resp
    mock_minio.list_objects.return_value = []
    mock_minio.remove_object.side_effect = _nosuchkey()

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
        patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
    ):
        result = await delete_doc("regr-ok-1")

    # Only registry pool-not-ready error expected in test context
    assert len(result["errors"]) == 1
    assert "registry" in result["errors"][0].lower()


async def test_delete_doc_partial_minio_failure_records_specific_store(mock_minio, monkeypatch):
    """Regression: partial MinIO failure records the failing store in errors[]."""
    load_resp = MagicMock()
    load_resp.read.return_value = json.dumps(
        {"doc_id": "regr-partial-1", "doc_name": "report.pdf"}
    ).encode()
    mock_minio.get_object.return_value = load_resp
    mock_minio.list_objects.return_value = []

    def _fail_processed(bucket, name):
        if name == "processed/regr-partial-1.json":
            raise _other_s3error()
        raise _nosuchkey()

    mock_minio.remove_object.side_effect = _fail_processed

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
        patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
    ):
        result = await delete_doc("regr-partial-1")

    assert any("processed.json" in e for e in result["errors"])


async def test_delete_doc_unknown_doc_name_skips_hash_cache_and_preloaded(mock_minio):
    """Regression: when doc_name cannot be recovered, steps 5 (hash-cache)
    and 7 (preloaded) are skipped without error but logged."""
    mock_minio.get_object.side_effect = _nosuchkey()
    mock_minio.list_objects.return_value = []
    mock_minio.remove_object.side_effect = _nosuchkey()

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
        patch("pageindex_mcp.storage.hash_cache.hash_cache_delete") as mock_hc,
    ):
        result = await delete_doc("unknown-name-1")

    # hash_cache_delete should NOT be called (no doc_name)
    mock_hc.assert_not_called()


# --- from test_minio_path_prefix.py ---


class TestPrefixedPoolManager:
    def _capture(self, prefix, url, **kw):
        pm = PrefixedPoolManager(prefix)
        with patch.object(urllib3.PoolManager, "urlopen") as mock:
            pm.urlopen("GET", url, **kw)
        return mock.call_args

    def test_prefix_inserted_before_path(self):
        args = self._capture("/minio", "https://infra.example.com/pageindex/a.pdf")
        assert args.args[1] == "https://infra.example.com/minio/pageindex/a.pdf"

    def test_query_string_preserved_exactly(self):
        """The signature covers the query — rewriting it would invalidate it."""
        url = "https://infra.example.com/pageindex/?list-type=2&prefix=proc%2F"
        args = self._capture("/minio", url)
        assert args.args[1].endswith("?list-type=2&prefix=proc%2F")

    def test_already_prefixed_path_not_prefixed_twice(self):
        """urllib3 follows redirects by re-entering urlopen, so a redirect back
        to /minio/... must not become /minio/minio/..."""
        args = self._capture("/minio", "https://infra.example.com/minio/pageindex/a.pdf")
        assert args.args[1] == "https://infra.example.com/minio/pageindex/a.pdf"

    def test_prefix_lookalike_path_is_still_prefixed(self):
        """/minio-staging is a different path, not an already-prefixed one."""
        args = self._capture("/minio", "https://infra.example.com/minio-staging/a")
        assert args.args[1] == "https://infra.example.com/minio/minio-staging/a"


class TestPrefixedPoolInheritsSdkSettings:
    """Passing http_client= replaces the SDK's own pool, so the prefixed pool
    must carry the same timeout/retry/CA policy or those guarantees silently
    vanish on exactly the deployments that use the public route."""

    def test_timeout_and_retries_match_sdk_defaults(self):
        pm = PrefixedPoolManager("/minio")
        kw = pm.connection_pool_kw

        assert kw["timeout"].connect_timeout == 300
        assert kw["timeout"].read_timeout == 300
        assert kw["maxsize"] == 10
        assert kw["cert_reqs"] == "CERT_REQUIRED"
        assert kw["ca_certs"]
        assert kw["retries"].total == 5
        assert kw["retries"].status_forcelist == [500, 502, 503, 504]

    def test_explicit_kwargs_still_override(self):
        pm = PrefixedPoolManager("/minio", maxsize=3)
        assert pm.connection_pool_kw["maxsize"] == 3


class TestMakeMinio:
    def test_prefix_installs_custom_http_client(self):
        client = make_minio("infra.example.com", "k", "s", secure=True, path_prefix="/minio")
        assert isinstance(client._http, PrefixedPoolManager)

    def test_endpoint_with_path_is_still_rejected(self):
        """Guards the reason this module exists — if the SDK ever accepted a
        path, the whole workaround could be dropped."""
        with pytest.raises(ValueError, match="path in endpoint"):
            make_minio("infra.example.com/minio", "k", "s", secure=True, path_prefix="")


@pytest.fixture
def reloadable_config(monkeypatch):
    """Yield pageindex_mcp.config, restoring the module-level singleton after.

    ``importlib.reload`` rebinds ``config.settings``, and monkeypatch only
    rewinds the environment — not the reloaded module. Without this teardown a
    test that reloads under MINIO_PATH_PREFIX=/minio leaves that value visible
    to every later test that reads ``config.settings`` directly.
    """
    import pageindex_mcp.config as cfg

    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    original = cfg.settings
    try:
        yield cfg
    finally:
        cfg.settings = original


class TestConfig:
    def test_minio_path_prefix_defaults_empty(self, monkeypatch, reloadable_config):
        monkeypatch.delenv("MINIO_PATH_PREFIX", raising=False)

        importlib.reload(reloadable_config)
        assert reloadable_config.settings.minio_path_prefix == ""

    def test_minio_path_prefix_normalized(self, monkeypatch, reloadable_config):
        for raw in ("minio", "/minio", "/minio/"):
            monkeypatch.setenv("MINIO_PATH_PREFIX", raw)
            importlib.reload(reloadable_config)
            assert reloadable_config.settings.minio_path_prefix == "/minio", raw


class TestPresignFallsBackToMainPrefix:
    """With no separate presign endpoint, presigned URLs are built from the main
    endpoint — so they need the main endpoint's route prefix, or they 404."""

    def test_main_prefix_used_when_no_presign_endpoint(self):
        import pageindex_mcp.storage as storage

        signed = "https://infra.example.com/pageindex/uploads/a.pdf?X-Amz-Signature=abc"
        with patch.object(storage.minio_ops, "settings") as s:
            s.minio_endpoint = "infra.example.com"
            s.minio_path_prefix = "/minio"
            s.minio_presign_endpoint = None
            s.minio_presign_path_prefix = ""
            out = storage._apply_route_prefix(signed)

        assert out == (
            "https://infra.example.com/minio/pageindex/uploads/a.pdf?X-Amz-Signature=abc"
        )

    def test_presign_endpoint_prefix_wins_when_set(self):
        import pageindex_mcp.storage as storage

        signed = "https://public.example.com/pageindex/uploads/a.pdf?X-Amz-Signature=abc"
        with patch.object(storage.minio_ops, "settings") as s:
            s.minio_endpoint = "10.43.0.1:9000"
            s.minio_path_prefix = ""
            s.minio_presign_endpoint = "public.example.com"
            s.minio_presign_path_prefix = "/minio"
            out = storage._apply_route_prefix(signed)

        assert out == (
            "https://public.example.com/minio/pageindex/uploads/a.pdf?X-Amz-Signature=abc"
        )


# --- from test_presign_public_route.py ---


class TestPresignSettings:
    def test_presign_secure_defaults_to_true(self, monkeypatch):
        monkeypatch.delenv("MINIO_PRESIGN_SECURE", raising=False)
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        import pageindex_mcp.config as cfg

        importlib.reload(cfg)
        assert cfg.settings.minio_presign_secure is True

    def test_presign_secure_read_from_env(self, monkeypatch):
        monkeypatch.setenv("MINIO_PRESIGN_SECURE", "false")
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        import pageindex_mcp.config as cfg

        importlib.reload(cfg)
        assert cfg.settings.minio_presign_secure is False

    def test_presign_path_prefix_defaults_to_empty(self, monkeypatch):
        monkeypatch.delenv("MINIO_PRESIGN_PATH_PREFIX", raising=False)
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        import pageindex_mcp.config as cfg

        importlib.reload(cfg)
        assert cfg.settings.minio_presign_path_prefix == ""

    def test_presign_path_prefix_normalized(self, monkeypatch):
        """Accept 'minio', '/minio' and '/minio/' — all mean the same route."""
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        import pageindex_mcp.config as cfg

        for raw in ("minio", "/minio", "/minio/"):
            monkeypatch.setenv("MINIO_PRESIGN_PATH_PREFIX", raw)
            importlib.reload(cfg)
            assert cfg.settings.minio_presign_path_prefix == "/minio", raw


class TestDoclingUrlNormalization:
    """`{url}/convert/pdf` on a trailing-slash URL yields `//convert/pdf`, which
    the Scaleway function 404s. Observed live against a real conversion call."""

    def test_trailing_slash_stripped(self, monkeypatch):
        monkeypatch.setenv("DOCLING_SERVICE_URL", "https://docling.example.com/")
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        import pageindex_mcp.config as cfg

        importlib.reload(cfg)
        assert cfg.settings.docling_service_url == "https://docling.example.com"
        assert f"{cfg.settings.docling_service_url}/convert/pdf" == (
            "https://docling.example.com/convert/pdf"
        )

    def test_unset_stays_none(self, monkeypatch):
        monkeypatch.delenv("DOCLING_SERVICE_URL", raising=False)
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        import pageindex_mcp.config as cfg

        importlib.reload(cfg)
        assert cfg.settings.docling_service_url is None


def _presign_settings(mock_settings, **overrides):
    mock_settings.minio_presign_endpoint = "infra.example.com"
    mock_settings.minio_endpoint = "10.43.0.1:9000"
    mock_settings.minio_path_prefix = ""
    mock_settings.minio_bucket = "pageindex"
    mock_settings.minio_access_key = "key"
    mock_settings.minio_secret_key = "secret"
    mock_settings.minio_secure = False  # internal endpoint is plaintext
    mock_settings.minio_presign_secure = True  # public endpoint is HTTPS
    mock_settings.minio_presign_path_prefix = ""
    mock_settings.minio_region = "us-east-1"
    for k, v in overrides.items():
        setattr(mock_settings, k, v)
    return mock_settings


class TestPresignClientConstruction:
    def test_uses_presign_secure_not_minio_secure(self):
        """MINIO_SECURE=false must not downgrade a public HTTPS presign host."""
        import pageindex_mcp.storage as storage

        with (
            patch.object(storage.minio_ops, "_presign_client", None),
            patch.object(storage.minio_ops, "make_minio") as mock_cls,
            patch.object(storage.minio_ops, "settings") as mock_settings,
        ):
            _presign_settings(mock_settings)
            storage._get_presign_minio()

        assert mock_cls.call_args.kwargs["secure"] is True

    def test_pins_region_to_avoid_live_bucket_location_lookup(self):
        """Unset region makes the SDK call GetBucketLocation on the public host,
        which is not routable for that verb — it raised instead of signing."""
        import pageindex_mcp.storage as storage

        with (
            patch.object(storage.minio_ops, "_presign_client", None),
            patch.object(storage.minio_ops, "make_minio") as mock_cls,
            patch.object(storage.minio_ops, "settings") as mock_settings,
        ):
            _presign_settings(mock_settings)
            storage._get_presign_minio()

        assert mock_cls.call_args.kwargs.get("region") == "us-east-1"


class TestPresignPathPrefix:
    def test_prefix_spliced_after_signing(self):
        """Signature covers /pageindex/<key>; the route serves it under /minio."""
        import pageindex_mcp.storage as storage

        mock_client = MagicMock()
        mock_client.presigned_get_object.return_value = (
            "https://infra.example.com/pageindex/uploads/a.pdf?X-Amz-Signature=abc"
        )
        with (
            patch.object(storage.minio_ops, "_get_presign_minio", return_value=mock_client),
            patch.object(storage.minio_ops, "settings") as mock_settings,
        ):
            _presign_settings(mock_settings, minio_presign_path_prefix="/minio")
            url = storage.presigned_get_url("uploads/a.pdf")

        assert url == (
            "https://infra.example.com/minio/pageindex/uploads/a.pdf?X-Amz-Signature=abc"
        )

    def test_query_string_is_untouched(self):
        """Rewriting the query would invalidate the signature."""
        import pageindex_mcp.storage as storage

        signed_query = "X-Amz-Signature=abc&X-Amz-Credential=k%2Fus-east-1&X-Amz-Expires=900"
        mock_client = MagicMock()
        mock_client.presigned_get_object.return_value = (
            f"https://infra.example.com/pageindex/uploads/a.pdf?{signed_query}"
        )
        with (
            patch.object(storage.minio_ops, "_get_presign_minio", return_value=mock_client),
            patch.object(storage.minio_ops, "settings") as mock_settings,
        ):
            _presign_settings(mock_settings, minio_presign_path_prefix="/minio")
            url = storage.presigned_get_url("uploads/a.pdf")

        assert url.split("?", 1)[1] == signed_query

    def test_no_prefix_leaves_url_unchanged(self):
        import pageindex_mcp.storage as storage

        signed = "https://infra.example.com/pageindex/uploads/a.pdf?X-Amz-Signature=abc"
        mock_client = MagicMock()
        mock_client.presigned_get_object.return_value = signed
        with (
            patch.object(storage.minio_ops, "_get_presign_minio", return_value=mock_client),
            patch.object(storage.minio_ops, "settings") as mock_settings,
        ):
            _presign_settings(mock_settings, minio_presign_path_prefix="")
            url = storage.presigned_get_url("uploads/a.pdf")

        assert url == signed

    # ---------------------------------------------------------------------------
    # Zone-5: Regression — save_doc_meta preserves existing consistency_regime
    # ---------------------------------------------------------------------------


def test_save_doc_meta_preserves_consistency_regime_on_verdict_update(mock_minio):
    """Regression: save_doc_meta must preserve an existing consistency_regime
    field during read-merge-write when the new call supplies only verdict
    fields (no consistency_regime). Without this, a subsequent verdict-only
    write from the promotion sweep would silently drop the forensic regime
    stamp set by _upsert_registry_row."""
    import io

    # Existing sidecar with consistency_regime already stamped
    existing_sidecar = {
        "doc_id": "regime-preserve-1",
        "doc_name": "test.pdf",
        "source_url": "",
        "processed_at": "2026-08-28T00:00:00+00:00",
        "consistency_regime": "postgres-authoritative",
        "verdict": "MARGINAL",
        "pipeline_version": 3,
    }
    existing_bytes = json.dumps(existing_sidecar).encode()

    # Mock the get_object to return existing sidecar
    response = MagicMock()
    response.read.return_value = existing_bytes
    response.close = MagicMock()
    response.release_conn = MagicMock()
    mock_minio.get_object.return_value = response

    # Call save_doc_meta with verdict-only update (no consistency_regime)
    save_doc_meta("regime-preserve-1", {
        "verdict": "PASS",
        "pipeline_version": 5,
        "verdict_computed_at": "2026-08-28T01:00:00+00:00",
    })

    # Verify the written sidecar preserved consistency_regime
    written = mock_minio.put_object.call_args[0][2].read()
    sidecar = json.loads(written)
    assert sidecar.get("consistency_regime") == "postgres-authoritative", (
        "consistency_regime must be preserved during read-merge-write when "
        "the new call supplies only verdict fields"
    )
    # Verdict fields must be updated
    assert sidecar["verdict"] == "PASS"
    assert sidecar["pipeline_version"] == 5


class TestPresignPathPrefix:
    def test_prefix_ignored_when_endpoint_addresses_minio_directly(self):
        """A ClusterIP endpoint has no route prefix, so nothing is spliced —
        the presign prefix belongs to the presign host, not this one."""
        import pageindex_mcp.storage as storage

        signed = "http://10.43.23.66:9000/pageindex/uploads/a.pdf?X-Amz-Signature=abc"
        mock_client = MagicMock()
        mock_client.presigned_get_object.return_value = signed
        with (
            patch.object(storage.minio_ops, "_get_presign_minio", return_value=mock_client),
            patch.object(storage.minio_ops, "settings") as mock_settings,
        ):
            _presign_settings(
                mock_settings,
                minio_presign_endpoint=None,
                minio_endpoint="10.43.23.66:9000",
                minio_path_prefix="",
                minio_presign_path_prefix="/minio",
            )
            url = storage.presigned_get_url("uploads/a.pdf")

        assert url == signed
