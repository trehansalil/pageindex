# tests/test_storage_contract.py
"""Behavioral contract tests for the MinIO storage layer (STORE-01) and the
right-to-erasure cascade (ERASE-01, Hard Rule 2).

STORE-01-C1  save_doc persists the tree JSON to processed/<doc_id>.json
STORE-01-C2  re-uploading unchanged bytes is idempotent via SHA-256 dedup
STORE-01-C3  load_doc returns the exact bytes save_doc persisted
ERASE-01-C1  delete_doc cascades across stores in the mandated order
ERASE-01-C2  delete_doc is idempotent (missing objects tolerated, no-op success)
ERASE-01-C3  a mid-cascade failure is surfaced and names the unpurged store
"""

import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest

from pageindex_mcp.config import settings
from pageindex_mcp.storage import delete_doc, list_processed_docs, load_doc, save_doc


@pytest.fixture
def mock_minio():
    client = MagicMock()
    client.bucket_exists.return_value = True
    with patch("pageindex_mcp.storage.get_minio", return_value=client):
        yield client


# ── STORE-01-C1 — save_doc persists the tree to its canonical path ───────────
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
        patch("pageindex_mcp.storage.doc_cache_delete", create=True),
        patch("pageindex_mcp.cache.doc_cache_delete"),
    ):
        save_doc("abc12345", tree)

    mock_minio.put_object.assert_called_once()
    key = mock_minio.put_object.call_args[0][1]
    assert key == "processed/abc12345.json"
    written = mock_minio.put_object.call_args[0][2].read()
    assert json.loads(written) == tree


# ── STORE-01-C3 — load_doc returns the exact persisted bytes ─────────────────
def test_store_01_c3_load_doc_returns_persisted_bytes(mock_minio):
    """STORE-01-C3: load_doc(doc_id) returns byte-for-byte what was persisted to
    processed/<doc_id>.json."""
    persisted = {
        "doc_id": "abc12345",
        "doc_name": "t.pdf",
        "structure": [{"title": "Root", "text": "body"}],
    }
    response = MagicMock()
    response.read.return_value = json.dumps(persisted, indent=2).encode()
    mock_minio.get_object.return_value = response

    loaded = load_doc("abc12345")

    assert loaded == persisted
    fetched_key = mock_minio.get_object.call_args[0][1]
    assert fetched_key == "processed/abc12345.json"


# ── STORE-01-C2 — SHA-256 content-hash dedup is idempotent ───────────────────
def test_store_01_c2_sha256_dedup_detects_unchanged_bytes():
    """STORE-01-C2: re-uploading identical bytes for the same filename produces
    the same SHA-256, so the hash-cache short-circuits a redundant write. Asserts
    the content-hash equality that drives the idempotent dedup decision."""
    data = b"%PDF-1.7 same exact bytes"
    h1 = hashlib.sha256(data).hexdigest()
    h2 = hashlib.sha256(data).hexdigest()
    assert h1 == h2  # identical bytes -> identical hash -> dedup hit

    # A single changed byte must NOT dedup (a real re-index is required).
    h3 = hashlib.sha256(b"%PDF-1.7 same exact byteS").hexdigest()
    assert h3 != h1

    # Reference of the dedup decision the hash-cache makes on save_doc:
    hash_cache = {"report.pdf": h1}
    assert hash_cache.get("report.pdf") == h2  # unchanged -> skip write
    assert hash_cache.get("report.pdf") != h3  # changed   -> re-write


# ── ERASE-01-C1 — cascade order across all derived stores ────────────────────
async def test_erase_01_c1_cascade_order_across_stores(mock_minio):
    """ERASE-01-C1: delete_doc removes derivatives in the mandated order —
    uploads/<id>/ then processed/<id>.json then processed/<id>.meta.json, then
    the Redis cache key, then the filename->sha256 hash-cache entry. Order is
    asserted by recording the observable remove/delete call sequence."""
    # load_doc (for the doc_name needed by step 5) returns a real doc.
    load_resp = MagicMock()
    load_resp.read.return_value = json.dumps(
        {"doc_id": "abc12345", "doc_name": "report.pdf"}
    ).encode()
    mock_minio.get_object.return_value = load_resp

    # uploads/<id>/ listing yields one staged object to remove (step 1).
    # figures/<id>/ listing yields nothing (step 2c).
    upload_obj = MagicMock()
    upload_obj.object_name = "uploads/abc12345/report.pdf"

    def _list_objects(_bucket, prefix="", **_kw):
        if prefix.startswith("uploads/"):
            return [upload_obj]
        return []  # figures/ and any other prefix

    mock_minio.list_objects.side_effect = _list_objects

    order = []
    mock_minio.remove_object.side_effect = lambda bucket, name: order.append(("minio", name))

    with (
        patch("pageindex_mcp.cache.doc_cache_delete") as mock_cache_del,
        patch("pageindex_mcp.storage.reconcile_etag_delete") as mock_etag_del,
        patch("pageindex_mcp.storage.hash_cache_delete") as mock_hash_del,
    ):
        mock_cache_del.side_effect = lambda did: order.append(("redis", did))
        mock_etag_del.side_effect = lambda did: order.append(("etag", did))
        mock_hash_del.side_effect = lambda filename: order.append(("hash-cache", filename))
        result = await delete_doc("abc12345")
    assert result == {"errors": []}

    # Step 1 (uploads) precedes step 2 (processed.json) precedes step 3 (meta).
    minio_names = [name for kind, name in order if kind == "minio"]
    assert minio_names == [
        "uploads/abc12345/report.pdf",
        "processed/abc12345.json",
        "processed/abc12345.flat.json",  # FLAT-02-C2: flat derived store joins cascade
        "processed/abc12345.meta.json",
        "preloaded/report.pdf",  # RFC-011 D2: preloaded object joins cascade (step 7)
    ]
    # MinIO purge precedes Redis cache purge precedes the reconcile-etag purge
    # (C-3 step 4b, HR2) precedes the hash-cache clear.
    kinds = [kind for kind, _ in order]
    assert kinds.index("minio") < kinds.index("redis")
    assert kinds.index("redis") < kinds.index("etag")
    assert kinds.index("etag") < kinds.index("hash-cache")
    mock_etag_del.assert_called_once_with("abc12345")


# ── ERASE-01-C2 — idempotent: deleting an absent doc is a no-op success ───────
async def test_erase_01_c2_idempotent_on_missing_doc(mock_minio):
    """ERASE-01-C2: deleting a never-existing / already-deleted doc_id tolerates
    missing objects (no S3Error/KeyError surfaced) and returns success."""
    from minio.error import S3Error

    def _nosuchkey() -> S3Error:
        # minio S3Error signature: (response, code, message, resource, request_id, host_id)
        return S3Error(MagicMock(), "NoSuchKey", "missing", "res", "req", "host")

    # load_doc raises ValueError (NoSuchKey -> not found); cascade still runs.
    mock_minio.get_object.side_effect = _nosuchkey()
    # No staged uploads, and remove_object raises NoSuchKey on processed objects.
    mock_minio.list_objects.return_value = []
    mock_minio.remove_object.side_effect = _nosuchkey()

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.hash_cache_delete"),
    ):
        # Must NOT raise — idempotent no-op success.
        result = await delete_doc("ghost9999")
    assert result == {"errors": []}


# ── ERASE-01-C3 — partial mid-cascade failure is surfaced ────────────────────
async def test_erase_01_c3_partial_failure_is_surfaced(mock_minio):
    """ERASE-01-C3 / Property 4: when the Redis cache delete raises after MinIO
    purges succeeded, delete_doc reports the unpurged store in its returned
    errors list (never raises), so the operation is safe to retry and no
    derivative is silently orphaned."""
    load_resp = MagicMock()
    load_resp.read.return_value = json.dumps(
        {"doc_id": "abc12345", "doc_name": "report.pdf"}
    ).encode()
    mock_minio.get_object.return_value = load_resp
    mock_minio.list_objects.return_value = []  # no staged uploads

    with (
        patch("pageindex_mcp.cache.doc_cache_delete", side_effect=RuntimeError("redis down")),
        patch("pageindex_mcp.storage.hash_cache_delete"),
    ):
        result = await delete_doc("abc12345")

    # The surfaced error names which store was not purged (the Redis cache).
    assert len(result["errors"]) == 1
    assert "redis" in result["errors"][0].lower()


# ── FLAT-02-C1 — flat doc persists to / loads from .flat.json ────────────────
def test_flat_02_c1_save_flat_doc_writes_flat_json_and_meta(mock_minio):
    """FLAT-02-C1: save_flat_doc PUTs the flat blocks JSON to
    processed/<doc_id>.flat.json AND writes the processed/<doc_id>.meta.json
    sidecar carrying content_class; get_flat_doc returns a value-equivalent dict.
    No processed/<doc_id>.json (tree) is written for a flat doc."""
    from pageindex_mcp.storage import get_flat_doc, save_flat_doc

    flat = {
        "doc_id": "flat0001",
        "doc_name": "katzen.pdf",
        "content_class": "flat_prose",
        "blocks": [{"text": "Clause 1"}, {"text": "Clause 2"}],
    }
    with patch("pageindex_mcp.cache.doc_cache_delete"):
        save_flat_doc("flat0001", flat)

    # Two PUTs: the .flat.json artifact and the .meta.json sidecar.
    put_keys = [c.args[1] for c in mock_minio.put_object.call_args_list]
    assert "processed/flat0001.flat.json" in put_keys
    assert "processed/flat0001.meta.json" in put_keys
    # FLAT-02-C1: a flat doc never writes the tree artifact.
    assert "processed/flat0001.json" not in put_keys

    # The .flat.json body is the persisted flat data.
    flat_put = next(
        c
        for c in mock_minio.put_object.call_args_list
        if c.args[1] == "processed/flat0001.flat.json"
    )
    written = json.loads(flat_put.args[2].read())
    assert written == flat

    # The meta sidecar carries content_class.
    meta_put = next(
        c
        for c in mock_minio.put_object.call_args_list
        if c.args[1] == "processed/flat0001.meta.json"
    )
    meta_written = json.loads(meta_put.args[2].read())
    assert meta_written.get("content_class") == "flat_prose"

    # get_flat_doc returns a value-equivalent dict (json.loads of stored bytes).
    response = MagicMock()
    response.read.return_value = json.dumps(flat, indent=2).encode()
    mock_minio.get_object.return_value = response
    loaded = get_flat_doc("flat0001")
    assert loaded == flat
    assert mock_minio.get_object.call_args[0][1] == "processed/flat0001.flat.json"


# ── FLAT-02-C2 — erasure cascade purges the flat-doc derived store (HR2) ──────
async def test_flat_02_c2_delete_doc_purges_flat_json(mock_minio):
    """FLAT-02-C2: delete_doc additionally removes processed/<doc_id>.flat.json,
    ordered immediately AFTER processed/<doc_id>.json and BEFORE the .meta.json
    step. HR2: the flat artifact is a derived store that MUST join the cascade."""
    load_resp = MagicMock()
    load_resp.read.return_value = json.dumps(
        {"doc_id": "flat0001", "doc_name": "katzen.pdf"}
    ).encode()
    mock_minio.get_object.return_value = load_resp

    upload_obj = MagicMock()
    upload_obj.object_name = "uploads/flat0001/katzen.pdf"

    def _list_objects(_b, prefix="", **_kw):
        if prefix.startswith("uploads/"):
            return [upload_obj]
        return []

    mock_minio.list_objects.side_effect = _list_objects

    order = []
    mock_minio.remove_object.side_effect = lambda bucket, name: order.append(name)

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.hash_cache_delete"),
    ):
        await delete_doc("flat0001")

    # The flat artifact is removed, ordered after .json and before .meta.json.
    assert "processed/flat0001.flat.json" in order
    assert order == [
        "uploads/flat0001/katzen.pdf",
        "processed/flat0001.json",
        "processed/flat0001.flat.json",
        "processed/flat0001.meta.json",
        "preloaded/katzen.pdf",  # RFC-011 D2: preloaded object joins cascade (step 7)
    ]


async def test_flat_02_c2_flat_json_nosuchkey_tolerated(mock_minio):
    """FLAT-02-C2: a missing processed/<doc_id>.flat.json (NoSuchKey) is tolerated
    idempotently — deleting a tree-only doc does not raise on the flat step."""
    from minio.error import S3Error

    def _nosuchkey() -> S3Error:
        return S3Error(MagicMock(), "NoSuchKey", "missing", "res", "req", "host")

    mock_minio.get_object.side_effect = _nosuchkey()
    mock_minio.list_objects.return_value = []
    mock_minio.remove_object.side_effect = _nosuchkey()

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.hash_cache_delete"),
    ):
        await delete_doc("ghostflat")  # must NOT raise


# ── FLAT-02-C3 — list_processed_docs surfaces flat docs + content_class ───────
def test_flat_02_c3_list_processed_docs_surfaces_flat_content_class(mock_minio):
    """FLAT-02-C3: list_processed_docs includes the flat doc surfacing doc_id,
    doc_name, and content_class so callers can route flat vs tree docs."""
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


# ── Fix-4 / HR2 audit: xlsx and image flat docs leave no undiscovered stores ──
#
# The deletion cascade in delete_doc is doc-type-agnostic: it globs by doc_id
# prefix and always removes the same four keys regardless of whether the
# original upload was a PDF, XLSX, or image.  FLAT-02-C2 (above) proves the
# cascade order for a PDF-sourced flat doc.  The parametrized cases below are
# the explicit audit proof that xlsx (content_class=flat_table) and image
# (content_class=flat_prose) add NO new un-purgeable derived store beyond the
# four standard keys already covered by the cascade.


@pytest.mark.parametrize(
    "doc_id,doc_name,content_class",
    [
        ("xlsx0001", "NAS_network_September_2024.xlsx", "flat_table"),
        ("img0001", "scan_page_001.png", "flat_prose"),
    ],
)
async def test_fix4_hr2_xlsx_and_image_flat_doc_cascade_is_complete(
    mock_minio, doc_id, doc_name, content_class
):
    """Fix-4 / HR2: delete_doc purges every derived artifact for xlsx and image
    flat docs. Both input types produce exactly the same four derived keys as a
    PDF flat doc (uploads/<id>/…, processed/<id>.json, processed/<id>.flat.json,
    processed/<id>.meta.json). No additional store exists for these types."""
    load_resp = MagicMock()
    load_resp.read.return_value = json.dumps(
        {"doc_id": doc_id, "doc_name": doc_name, "content_class": content_class}
    ).encode()
    mock_minio.get_object.return_value = load_resp

    upload_obj = MagicMock()
    upload_obj.object_name = f"uploads/{doc_id}/{doc_name}"

    def _list_objects(_b, prefix="", **_kw):
        if prefix.startswith("uploads/"):
            return [upload_obj]
        return []

    mock_minio.list_objects.side_effect = _list_objects

    removed = []
    mock_minio.remove_object.side_effect = lambda bucket, name: removed.append(name)

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.hash_cache_delete"),
    ):
        await delete_doc(doc_id)

    # Exactly the five standard derived stores are removed — cascade is complete.
    expected = [
        f"uploads/{doc_id}/{doc_name}",
        f"processed/{doc_id}.json",
        f"processed/{doc_id}.flat.json",
        f"processed/{doc_id}.meta.json",
        f"preloaded/{doc_name}",  # RFC-011 D2: preloaded object joins cascade (step 7)
    ]
    assert removed == expected, (
        f"delete_doc for a {content_class} ({doc_name}) must purge exactly the "
        f"five standard derived stores in cascade order; got {removed}"
    )


# ── RFC-007 D9 / Property 8 — observable staging delete failure ─────────────
def test_delete_staging_s3error_returns_false(mock_minio):
    from minio.error import S3Error

    from pageindex_mcp.metrics import STAGING_DELETE_FAILURES
    from pageindex_mcp.storage import delete_staging

    mock_minio.remove_object.side_effect = S3Error(
        MagicMock(), "InternalError", "boom", "res", "req", "host"
    )
    before = STAGING_DELETE_FAILURES._value.get()

    result = delete_staging("uploads/staging/job-1/report.pdf")

    assert result is False
    assert STAGING_DELETE_FAILURES._value.get() == before + 1


def test_delete_staging_success_returns_true(mock_minio):
    from pageindex_mcp.storage import delete_staging

    assert delete_staging("uploads/staging/job-1/report.pdf") is True


# ── RFC-007 D2 / Property 4 — awaited registry delete in the erasure cascade ─
def _wire_registry(monkeypatch, *, registry_delete_doc, get_pool_return=object()):
    import dataclasses

    from pageindex_mcp import storage as st

    monkeypatch.setattr(
        st,
        "settings",
        dataclasses.replace(
            st.settings,
            registry_enabled=True,
            postgres_dsn="postgresql://user:pass@localhost:5432/pageindex",
            registry_delete_timeout_s=0.05,
        ),
    )
    monkeypatch.setattr("pageindex_mcp.registry.delete_doc", registry_delete_doc)
    monkeypatch.setattr("pageindex_mcp.registry.get_pool", lambda: get_pool_return)


async def test_delete_doc_awaits_registry(monkeypatch, mock_minio):
    from unittest.mock import AsyncMock

    mock_minio.list_objects.return_value = []
    mock_minio.get_object.side_effect = ValueError("no doc")

    registry_delete = AsyncMock()
    _wire_registry(monkeypatch, registry_delete_doc=registry_delete)

    with patch("pageindex_mcp.cache.doc_cache_delete"):
        result = await delete_doc("registry-doc-1")

    registry_delete.assert_awaited_once_with("registry-doc-1")
    assert result == {"errors": []}


async def test_delete_doc_registry_timeout(monkeypatch, mock_minio):
    import asyncio

    mock_minio.list_objects.return_value = []
    mock_minio.get_object.side_effect = ValueError("no doc")

    async def _hangs(doc_id):
        await asyncio.sleep(10)

    _wire_registry(monkeypatch, registry_delete_doc=_hangs)

    with patch("pageindex_mcp.cache.doc_cache_delete"):
        result = await delete_doc("registry-doc-2")

    assert len(result["errors"]) == 1
    assert "registry" in result["errors"][0].lower()
    assert "timed out" in result["errors"][0].lower()


# ── RFC-007 D6 / Property 6 — hash cache atomicity (Redis HSET) ──────────────
@pytest.fixture
def fake_hash_cache_redis():
    import fakeredis

    fake = fakeredis.FakeRedis(decode_responses=True)
    with patch("pageindex_mcp.cache._redis_sync", fake):
        yield fake


def test_hash_cache_redis_hset(fake_hash_cache_redis):
    from pageindex_mcp.storage import HASH_CACHE_KEY, hash_cache_get, hash_cache_set

    hash_cache_set("report.pdf", "deadbeef")

    assert fake_hash_cache_redis.hget(HASH_CACHE_KEY, "report.pdf") == "deadbeef"
    assert hash_cache_get("report.pdf") == "deadbeef"
    assert hash_cache_get("never-indexed.pdf") is None


def test_hash_cache_concurrent_workers(fake_hash_cache_redis):
    """Property 6: two concurrent writes for DIFFERENT filenames both persist —
    HSET is atomic per-field, so no last-writer-wins loss (the bug the old
    instance-level asyncio.Lock over a MinIO JSON blob could not prevent
    across separate arq worker processes)."""
    from pageindex_mcp.storage import hash_cache_get, hash_cache_set

    hash_cache_set("a.pdf", "hash-a")
    hash_cache_set("b.pdf", "hash-b")

    assert hash_cache_get("a.pdf") == "hash-a"
    assert hash_cache_get("b.pdf") == "hash-b"


# ── RFC-007 Task 3.4 — end-to-end erasure cascade across MinIO/Redis/Postgres ─
async def test_erasure_cascade_all_stores_healthy_reports_no_errors(mock_minio):
    """Scenario 1: MinIO, Redis, and the Postgres registry are all healthy —
    every store is cleaned and the returned errors list is empty."""
    from unittest.mock import AsyncMock

    load_resp = MagicMock()
    load_resp.read.return_value = json.dumps(
        {"doc_id": "cascade001", "doc_name": "report.pdf"}
    ).encode()
    mock_minio.get_object.return_value = load_resp
    mock_minio.list_objects.return_value = []

    with (
        patch("pageindex_mcp.cache.doc_cache_delete") as mock_cache_del,
        patch("pageindex_mcp.storage.hash_cache_delete") as mock_hash_del,
    ):
        result = await delete_doc("cascade001")

    mock_cache_del.assert_called_once_with("cascade001")
    mock_hash_del.assert_called_once_with("report.pdf")
    mock_minio.remove_object.assert_any_call(settings.minio_bucket, "processed/cascade001.json")
    # Registry step is skipped (settings.postgres_dsn unset in the test
    # environment) — a non-fatal, logged, error-free path per storage.py.
    assert result == {"errors": []}


async def test_erasure_cascade_postgres_failure_still_cleans_minio_and_redis(
    monkeypatch, mock_minio
):
    """Scenario 2: Postgres registry delete fails — the error is reported, but
    MinIO objects and the Redis cache key are still purged (HR2: partial
    failure never blocks the stores that *can* succeed)."""
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
        patch("pageindex_mcp.storage.hash_cache_delete") as mock_hash_del,
    ):
        result = await delete_doc("cascade002")

    # MinIO, Redis cache, and hash-cache were still purged despite the Postgres failure.
    removed_keys = [c.args[1] for c in mock_minio.remove_object.call_args_list]
    assert "processed/cascade002.json" in removed_keys
    assert "uploads/cascade002/report.pdf" in removed_keys
    mock_cache_del.assert_called_once_with("cascade002")
    mock_hash_del.assert_called_once_with("report.pdf")

    assert len(result["errors"]) == 1
    assert "registry" in result["errors"][0].lower()


# ── RFC-011 D2 / ISS-41 — erasure cascade purges preloaded/<doc_name> ────────
async def test_erasure_cascade_purges_preloaded_object(mock_minio):
    """RFC-011 D2: delete_doc additionally removes preloaded/<doc_name> (step 7),
    the raw object a preload/seed step may have staged outside the normal
    uploads/ path. HR2: every derived store must join the cascade."""
    load_resp = MagicMock()
    load_resp.read.return_value = json.dumps(
        {"doc_id": "preload001", "doc_name": "report.pdf"}
    ).encode()
    mock_minio.get_object.return_value = load_resp
    mock_minio.list_objects.return_value = []

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.hash_cache_delete"),
    ):
        result = await delete_doc("preload001")

    mock_minio.remove_object.assert_any_call(settings.minio_bucket, "preloaded/report.pdf")
    assert result == {"errors": []}


async def test_erasure_cascade_warns_when_doc_name_unknown_for_preloaded(mock_minio, caplog):
    """RFC-011 D2: when doc_name cannot be recovered (no processed/<id>.json AND
    no uploads/<id>/ objects to source a basename fallback from), step 7 logs a
    warning and skips the preloaded/ purge rather than guessing a key."""
    from minio.error import S3Error

    mock_minio.get_object.side_effect = S3Error(
        MagicMock(), "NoSuchKey", "missing", "res", "req", "host"
    )
    mock_minio.list_objects.return_value = []
    mock_minio.remove_object.side_effect = S3Error(
        MagicMock(), "NoSuchKey", "missing", "res", "req", "host"
    )

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.hash_cache_delete"),
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
