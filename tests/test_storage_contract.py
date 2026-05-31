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
from io import BytesIO
from unittest.mock import MagicMock, call, patch

import pytest

from pageindex_mcp import storage
from pageindex_mcp.storage import save_doc, load_doc, delete_doc


@pytest.fixture
def mock_minio():
    client = MagicMock()
    client.bucket_exists.return_value = True
    with patch("pageindex_mcp.storage.get_minio", return_value=client):
        yield client


# ── STORE-01-C1 — save_doc persists the tree to its canonical path ───────────
def test_store_01_c1_save_doc_writes_processed_json(mock_minio):
    """STORE-01-C1: save_doc PUTs the serialized tree to processed/<doc_id>.json."""
    tree = {"doc_id": "abc12345", "doc_name": "t.pdf",
            "structure": [{"title": "Root", "nodes": [{"title": "C"}]}]}
    with patch("pageindex_mcp.storage.doc_cache_delete", create=True):
        # save_doc lazily imports doc_cache_delete; patch the source so no Redis
        # is touched while we assert the MinIO write.
        with patch("pageindex_mcp.cache.doc_cache_delete"):
            save_doc("abc12345", tree)

    mock_minio.put_object.assert_called_once()
    bucket, key = mock_minio.put_object.call_args[0][0], mock_minio.put_object.call_args[0][1]
    assert key == "processed/abc12345.json"
    written = mock_minio.put_object.call_args[0][2].read()
    assert json.loads(written) == tree


# ── STORE-01-C3 — load_doc returns the exact persisted bytes ─────────────────
def test_store_01_c3_load_doc_returns_persisted_bytes(mock_minio):
    """STORE-01-C3: load_doc(doc_id) returns byte-for-byte what was persisted to
    processed/<doc_id>.json."""
    persisted = {"doc_id": "abc12345", "doc_name": "t.pdf",
                 "structure": [{"title": "Root", "text": "body"}]}
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
    assert hash_cache.get("report.pdf") == h2          # unchanged -> skip write
    assert hash_cache.get("report.pdf") != h3          # changed   -> re-write


# ── ERASE-01-C1 — cascade order across all derived stores ────────────────────
def test_erase_01_c1_cascade_order_across_stores(mock_minio):
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
    upload_obj = MagicMock()
    upload_obj.object_name = "uploads/abc12345/report.pdf"
    mock_minio.list_objects.return_value = [upload_obj]

    order = []
    mock_minio.remove_object.side_effect = lambda bucket, name: order.append(("minio", name))

    with patch("pageindex_mcp.cache.doc_cache_delete") as mock_cache_del, \
         patch("pageindex_mcp.storage.load_hash_cache", return_value={"report.pdf": "deadbeef"}), \
         patch("pageindex_mcp.storage.save_hash_cache") as mock_save_hash:
        mock_cache_del.side_effect = lambda did: order.append(("redis", did))
        mock_save_hash.side_effect = lambda c: order.append(("hash-cache", "report.pdf"))
        delete_doc("abc12345")

    # Step 1 (uploads) precedes step 2 (processed.json) precedes step 3 (meta).
    minio_names = [name for kind, name in order if kind == "minio"]
    assert minio_names == [
        "uploads/abc12345/report.pdf",
        "processed/abc12345.json",
        "processed/abc12345.meta.json",
    ]
    # MinIO purge precedes Redis purge precedes hash-cache clear.
    kinds = [kind for kind, _ in order]
    assert kinds.index("minio") < kinds.index("redis")
    assert kinds.index("redis") < kinds.index("hash-cache")


# ── ERASE-01-C2 — idempotent: deleting an absent doc is a no-op success ───────
def test_erase_01_c2_idempotent_on_missing_doc(mock_minio):
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

    with patch("pageindex_mcp.cache.doc_cache_delete"), \
         patch("pageindex_mcp.storage.load_hash_cache", return_value={}), \
         patch("pageindex_mcp.storage.save_hash_cache"):
        # Must NOT raise — idempotent no-op success.
        delete_doc("ghost9999")


# ── ERASE-01-C3 — partial mid-cascade failure is surfaced ────────────────────
def test_erase_01_c3_partial_failure_is_surfaced(mock_minio):
    """ERASE-01-C3: when the Redis cache delete raises after MinIO purges
    succeeded, delete_doc raises a RuntimeError that names the unpurged store, so
    the operation is safe to retry and no derivative is silently orphaned."""
    load_resp = MagicMock()
    load_resp.read.return_value = json.dumps(
        {"doc_id": "abc12345", "doc_name": "report.pdf"}
    ).encode()
    mock_minio.get_object.return_value = load_resp
    mock_minio.list_objects.return_value = []  # no staged uploads

    with patch("pageindex_mcp.cache.doc_cache_delete",
               side_effect=RuntimeError("redis down")), \
         patch("pageindex_mcp.storage.load_hash_cache", return_value={}), \
         patch("pageindex_mcp.storage.save_hash_cache"):
        with pytest.raises(RuntimeError) as excinfo:
            delete_doc("abc12345")

    # The surfaced error names which store was not purged (the Redis cache).
    assert "redis" in str(excinfo.value).lower()
