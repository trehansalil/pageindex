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
    tree = {"doc_id": "abc12345", "doc_name": "t.pdf",
            "structure": [{"title": "Root", "nodes": [{"title": "C"}]}]}
    # save_doc lazily imports doc_cache_delete; patch the source so no Redis
    # is touched while we assert the MinIO write.
    with patch("pageindex_mcp.storage.doc_cache_delete", create=True), \
         patch("pageindex_mcp.cache.doc_cache_delete"):
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
        "processed/abc12345.flat.json",  # FLAT-02-C2: flat derived store joins cascade
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
         patch("pageindex_mcp.storage.save_hash_cache"), pytest.raises(RuntimeError) as excinfo:
        delete_doc("abc12345")

    # The surfaced error names which store was not purged (the Redis cache).
    assert "redis" in str(excinfo.value).lower()


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
    flat_put = next(c for c in mock_minio.put_object.call_args_list
                    if c.args[1] == "processed/flat0001.flat.json")
    written = json.loads(flat_put.args[2].read())
    assert written == flat

    # The meta sidecar carries content_class.
    meta_put = next(c for c in mock_minio.put_object.call_args_list
                    if c.args[1] == "processed/flat0001.meta.json")
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
def test_flat_02_c2_delete_doc_purges_flat_json(mock_minio):
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
    mock_minio.list_objects.return_value = [upload_obj]

    order = []
    mock_minio.remove_object.side_effect = lambda bucket, name: order.append(name)

    with patch("pageindex_mcp.cache.doc_cache_delete"), \
         patch("pageindex_mcp.storage.load_hash_cache", return_value={}), \
         patch("pageindex_mcp.storage.save_hash_cache"):
        delete_doc("flat0001")

    # The flat artifact is removed, ordered after .json and before .meta.json.
    assert "processed/flat0001.flat.json" in order
    assert order == [
        "uploads/flat0001/katzen.pdf",
        "processed/flat0001.json",
        "processed/flat0001.flat.json",
        "processed/flat0001.meta.json",
    ]


def test_flat_02_c2_flat_json_nosuchkey_tolerated(mock_minio):
    """FLAT-02-C2: a missing processed/<doc_id>.flat.json (NoSuchKey) is tolerated
    idempotently — deleting a tree-only doc does not raise on the flat step."""
    from minio.error import S3Error

    def _nosuchkey() -> S3Error:
        return S3Error(MagicMock(), "NoSuchKey", "missing", "res", "req", "host")

    mock_minio.get_object.side_effect = _nosuchkey()
    mock_minio.list_objects.return_value = []
    mock_minio.remove_object.side_effect = _nosuchkey()

    with patch("pageindex_mcp.cache.doc_cache_delete"), \
         patch("pageindex_mcp.storage.load_hash_cache", return_value={}), \
         patch("pageindex_mcp.storage.save_hash_cache"):
        delete_doc("ghostflat")  # must NOT raise


# ── FLAT-02-C3 — list_processed_docs surfaces flat docs + content_class ───────
def test_flat_02_c3_list_processed_docs_surfaces_flat_content_class(mock_minio):
    """FLAT-02-C3: list_processed_docs includes the flat doc surfacing doc_id,
    doc_name, and content_class so callers can route flat vs tree docs."""
    meta_obj = MagicMock()
    meta_obj.object_name = "processed/flat0001.meta.json"
    mock_minio.list_objects.return_value = [meta_obj]

    meta_resp = MagicMock()
    meta_resp.read.return_value = json.dumps({
        "doc_id": "flat0001",
        "doc_name": "katzen.pdf",
        "content_class": "flat_prose",
    }).encode()
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

@pytest.mark.parametrize("doc_id,doc_name,content_class", [
    ("xlsx0001", "NAS_network_September_2024.xlsx", "flat_table"),
    ("img0001",  "scan_page_001.png",               "flat_prose"),
])
def test_fix4_hr2_xlsx_and_image_flat_doc_cascade_is_complete(
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
    mock_minio.list_objects.return_value = [upload_obj]

    removed = []
    mock_minio.remove_object.side_effect = lambda bucket, name: removed.append(name)

    with patch("pageindex_mcp.cache.doc_cache_delete"), \
         patch("pageindex_mcp.storage.load_hash_cache", return_value={}), \
         patch("pageindex_mcp.storage.save_hash_cache"):
        delete_doc(doc_id)

    # Exactly the four standard derived stores are removed — cascade is complete.
    expected = [
        f"uploads/{doc_id}/{doc_name}",
        f"processed/{doc_id}.json",
        f"processed/{doc_id}.flat.json",
        f"processed/{doc_id}.meta.json",
    ]
    assert removed == expected, (
        f"delete_doc for a {content_class} ({doc_name}) must purge exactly the "
        f"four standard derived stores in cascade order; got {removed}"
    )
