# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Storage operations: MinIO path prefix, presign public route, core storage,
memory-admission, Redis singleton and doc-cache tests."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import fakeredis.aioredis
import pytest
import urllib3
from minio.error import S3Error

from pageindex_mcp import memory_admission as ma
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
from pageindex_mcp.storage.documents import ensure_quarantine_lifecycle

# --- from test_storage.py ---


def _obj(name: str) -> MagicMock:
    obj = MagicMock()
    obj.object_name = name
    return obj


def _nosuchkey() -> S3Error:
    return S3Error(MagicMock(), "NoSuchKey", "missing", "res", "req", "host")


def _other_s3error(code="InternalError") -> S3Error:
    return S3Error(MagicMock(), code, "boom", "res", "req", "host")


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


# ── get_minio: lazy singleton / bucket-creation branch ───────────────────────
# ── STORE-01-C1/C2/C3 — save_doc / load_doc ───────────────────────────────────
def test_store_01_c1_save_doc_writes_processed_json(mock_minio):
    """STORE-01-C1: save_doc PUTs the serialized tree to
    processed/<doc_id>.json behind the write-visibility barrier; load_doc
    re-raises any S3Error that is not NoSuchKey."""
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

    # STORE-01: load_doc tolerates NoSuchKey but never swallows another S3Error.
    mock_minio.get_object.side_effect = _other_s3error()
    with pytest.raises(S3Error):
        load_doc("abc12345")

    # Zone-4 Phase 3: the write-visibility barrier removal is scoped to
    # save_doc_meta (the archival sidecar) -- save_doc, the primary processed
    # artifact, must STILL call _confirm_write_visible.
    mock_minio.reset_mock()
    mock_minio.get_object.side_effect = None
    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.minio_ops._confirm_write_visible") as mock_barrier,
    ):
        save_doc("abc12345", tree)
    mock_barrier.assert_called_once()

    mock_minio.reset_mock()
    with patch("pageindex_mcp.storage.minio_ops._confirm_write_visible") as mock_barrier:
        save_doc_meta(
            "abc12345",
            {
                "doc_id": "abc12345",
                "doc_name": "t.pdf",
                "source_url": "",
                "processed_at": "2026-08-21T00:00:00+00:00",
            },
        )
    mock_barrier.assert_not_called()
    mock_minio.put_object.assert_called_once()  # the sidecar IS still written


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


# ── read_registry_fields ──────────────────────────────────────────────────────


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
    assert len(result["errors"]) == 1
    assert result["errors"][0].startswith("registry: ")


async def test_erase_01_c2_prefix_loops_tolerate_nosuchkey_but_surface_other_errors(
    mock_minio, monkeypatch
):
    """ERASE-01-C2: the two cascade steps that iterate a prefix -- _erase_uploads
    and _erase_figures -- treat NoSuchKey on remove_object as idempotent success,
    exactly like every step routed through _remove_object_idempotent, while still
    surfacing any other S3Error.

    The sibling test above stubs list_objects to [], so those loops never reach
    remove_object and cannot see this. A retry after a partial failure does: the
    objects are still listed but already purged.
    """
    upload_obj = MagicMock()
    upload_obj.object_name = "uploads/retry01/report.pdf"
    figure_obj = MagicMock()
    figure_obj.object_name = "figures/retry01/fig-1.png"
    mock_minio.get_object.side_effect = _nosuchkey()

    def _listing(bucket, prefix="", recursive=False):
        if prefix.startswith("uploads/"):
            return [upload_obj]
        if prefix.startswith("figures/"):
            return [figure_obj]
        return []

    mock_minio.list_objects.side_effect = _listing

    async def _registry_ok(doc_id):
        return None

    _wire_registry(monkeypatch, registry_delete_doc=_registry_ok)

    failures: list[str] = []
    for label, side_effect, want_clean in (
        ("NoSuchKey", _nosuchkey(), True),
        ("InternalError", _other_s3error(), False),
    ):
        mock_minio.remove_object.side_effect = side_effect
        with (
            patch("pageindex_mcp.cache.doc_cache_delete"),
            patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
            patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
        ):
            result = await delete_doc("retry01")
        errors = result["errors"]
        if want_clean and errors:
            failures.append(f"{label}: expected a clean retry, got errors={errors}")
        if not want_clean and not any(e.startswith("uploads/") for e in errors):
            failures.append(f"{label}: expected uploads/ to be reported, got {errors}")

    assert not failures, "; ".join(failures)


async def test_erase_01_c1_cascade_order_observable_and_hash_cache_cleared(mock_minio, monkeypatch):
    """ERASE-01-C1 (HR2): a DSR delete of a fully-indexed doc removes the MinIO
    objects in the mandated order uploads/<id>/ -> processed/<id>.json ->
    processed/<id>.meta.json, THEN deletes the Redis cache key
    pageindex:doc:<id>, and clears the filename->sha256 hash-cache entry so a
    re-upload re-indexes. Order is asserted against the observed call sequence,
    not against the manifest constant."""
    events: list[str] = []

    load_resp = MagicMock()
    load_resp.read.return_value = json.dumps(
        {"doc_id": "order001", "doc_name": "report.pdf"}
    ).encode()
    mock_minio.get_object.return_value = load_resp
    upload_obj = MagicMock()
    upload_obj.object_name = "uploads/order001/report.pdf"
    mock_minio.list_objects.return_value = [upload_obj]
    mock_minio.remove_object.side_effect = lambda bucket, name: events.append(name)

    async def _registry_ok(doc_id):
        events.append("registry")

    _wire_registry(monkeypatch, registry_delete_doc=_registry_ok)

    with (
        patch(
            "pageindex_mcp.cache.doc_cache_delete",
            side_effect=lambda d: events.append(f"redis:{d}"),
        ),
        patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
        patch(
            "pageindex_mcp.storage.hash_cache.hash_cache_delete",
            side_effect=lambda n: events.append(f"hash:{n}"),
        ),
    ):
        result = await delete_doc("order001")

    mandated = [
        "uploads/order001/report.pdf",
        "processed/order001.json",
        "processed/order001.meta.json",
        "redis:order001",
        # The hash-cache entry is keyed by filename, so a re-upload of
        # report.pdf re-indexes instead of deduping to the erased doc_id.
        "hash:report.pdf",
    ]
    missing = [e for e in mandated if e not in events]
    assert not missing, f"HR2 cascade never reached: {missing} (observed {events})"
    positions = [events.index(e) for e in mandated]
    assert positions == sorted(positions), (
        f"HR2 cascade order violated: expected {mandated}, observed {events}"
    )
    assert result["errors"] == []


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
        result = await delete_doc("ghostflat")  # must NOT raise

    assert not any("flat" in e for e in result["errors"]), result["errors"]


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


async def test_delete_doc_non_nosuchkey_remove_errors_recorded(mock_minio):
    """A non-NoSuchKey S3Error while removing any cascade artifact is
    recorded in errors (not swallowed like NoSuchKey is)."""
    failures = []
    for target_key, error_fragment in [
        ("processed/errkey001.json", "processed.json"),
        ("processed/errkey001.meta.json", "processed.meta.json"),
        ("preloaded/report.pdf", "preloaded/"),
    ]:
        load_resp = MagicMock()
        load_resp.read.return_value = json.dumps(
            {"doc_id": "errkey001", "doc_name": "report.pdf"}
        ).encode()
        mock_minio.get_object.return_value = load_resp
        mock_minio.list_objects.return_value = []

        def _remove(bucket, name, _target=target_key):
            if name == _target:
                raise _other_s3error()
            raise _nosuchkey()

        mock_minio.remove_object.side_effect = _remove

        with (
            patch("pageindex_mcp.cache.doc_cache_delete"),
            patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
        ):
            result = await delete_doc("errkey001")

        if not any(error_fragment in e for e in result["errors"]):
            failures.append(f"{target_key}: no {error_fragment!r} in {result['errors']}")
    assert not failures, failures


# ── RFC-007 D9 / Property 8 — observable staging delete failure ─────────────


# ── RFC-007 D2 / Property 4 — awaited registry delete in the erasure cascade ─
# ── RFC-007 Task 3.4 — end-to-end erasure cascade across MinIO/Redis/Postgres ─
async def test_erasure_cascade_postgres_failure_still_cleans_minio_and_redis(
    monkeypatch, mock_minio
):
    """ERASE-01-C3: Postgres registry delete fails mid-cascade — the failure is
    surfaced in ``errors`` naming the store that was NOT purged (registry),
    while the MinIO objects and the Redis cache key are still purged (HR2:
    partial failure never blocks the stores that *can* succeed), and the
    operation is safe to retry: a second delete_doc once the registry recovers
    completes with no errors."""
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
    # No store is reported as erased while still unpurged: the only failing
    # store is the one named, and it is the only one missing from the cascade.

    # C3: safe to retry to completion. Re-run with the registry healthy; the
    # already-purged stores tolerate their missing objects and the previously
    # unpurged registry row is now reached, leaving no errors behind.
    deleted_rows = []

    async def _registry_ok(doc_id):
        deleted_rows.append(doc_id)

    _wire_registry(monkeypatch, registry_delete_doc=_registry_ok)
    # Post-first-pass state: the uploads/ and figures/ prefixes are now empty
    # and every remaining object is already gone (NoSuchKey), which the
    # cascade tolerates idempotently.
    mock_minio.list_objects.return_value = []
    mock_minio.remove_object.side_effect = _nosuchkey()

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
        patch("pageindex_mcp.storage.hash_cache.hash_cache_delete"),
    ):
        retry = await delete_doc("cascade002")

    assert deleted_rows == ["cascade002"], "retry did not reach the unpurged registry store"
    assert retry["errors"] == [], f"retry left errors behind: {retry['errors']}"


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
    assert len(result["errors"]) == 1
    assert result["errors"][0].startswith("registry: ")


# ── save_raw ───────────────────────────────────────────────────────────────
# ── upload_staging / download_staging ────────────────────────────────────────


# ── _load_legacy_minio_hash_cache ────────────────────────────────────────────


# ── hash_cache_get / set / delete ────────────────────────────────────────────


# ── .meta.json sidecar: save_doc_meta ────────────────────────────────────────


# ── C-3 sidecar v2: sha256 + doc_description fattening ───────────────────────


# ── RFC-034 D5: extraction provenance fields ─────────────────────────────────


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

    assert (
        _garble_check_nodes(
            tree,
            script_context=ScriptContext(
                dominant_script=None, had_presentation_forms=False, source="test"
            ),
            config=GarbleConfig(),
        )
        == 1
    )


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


# ---------------------------------------------------------------------------
# Zone-7 (HR2 compliance): hash_cache_delete purges both Redis AND legacy MinIO
# ---------------------------------------------------------------------------


def test_hash_cache_delete_issues_redis_hdel_and_legacy_purge(fake_cache_redis):
    """Contract: hash_cache_delete must issue both Redis HDEL AND attempt
    legacy MinIO blob purge. When legacy blob contains the filename, it
    must be removed."""
    hash_cache_set("purge.pdf", "hash-purge")
    assert hash_cache_get("purge.pdf") == "hash-purge"

    with patch("pageindex_mcp.storage.hash_cache._purge_legacy_hash_entry") as mock_legacy:
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
    from pageindex_mcp.storage.documents import _ERASURE_MANIFEST, ErasureStep

    # All entries are ErasureStep instances
    for entry in _ERASURE_MANIFEST:
        assert isinstance(entry, ErasureStep), f"Expected ErasureStep, got {type(entry).__name__}"

    # Step numbers must be non-decreasing (manifest is ordered by step)
    step_numbers = [e.step for e in _ERASURE_MANIFEST]
    assert step_numbers == sorted(step_numbers), (
        f"Manifest steps not in non-decreasing order: {step_numbers}"
    )

    # All required step names must be present
    expected_names = {
        "uploads",
        "processed_json",
        "processed_flat_json",
        "figures",
        "verdicts",
        "meta_json",
        "quarantine",
        "redis_cache",
        "reconcile_etag",
        "hash_cache",
        "registry",
        "preloaded",
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
    assert name_to_step["quarantine"] == 3
    assert name_to_step["redis_cache"] == 4
    assert name_to_step["hash_cache"] == 5
    assert name_to_step["registry"] == 6
    assert name_to_step["preloaded"] == 7

    # Relative ordering of the manifest tuple itself (drives execution order).
    order = [e.name for e in _ERASURE_MANIFEST]
    for earlier, later in (
        ("uploads", "processed_json"),
        ("processed_json", "meta_json"),
        ("meta_json", "quarantine"),
        ("quarantine", "redis_cache"),
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
        "quarantine": False,
        "redis_cache": True,
        "reconcile_etag": True,
        "hash_cache": True,
        "registry": True,
        # Optional: RFC-011 D2 — only preloaded ingests have a raw object here.
        "preloaded": False,
    }
    actual_required = {e.name: e.required for e in _ERASURE_MANIFEST}
    assert actual_required == expected_required

    # Every step exposes a non-empty description and a callable executor.
    for entry in _ERASURE_MANIFEST:
        assert entry.description.strip(), f"{entry.name} has no description"
        assert callable(entry.execute), f"{entry.name}.execute is not callable"

    # The sync/async split is pinned, not incidental. ``delete_doc`` awaits a
    # coroutine executor directly and pushes a plain one through
    # ``asyncio.to_thread``, so writing a blocking step as ``async def`` puts
    # its network round-trips back on the event loop while still looking
    # asynchronous -- which is what a twelve-store HR2 cascade did before
    # RFC-049. Only the two steps that genuinely await belong on this list.
    coroutine_steps = {e.name for e in _ERASURE_MANIFEST if inspect.iscoroutinefunction(e.execute)}
    assert coroutine_steps == {"verdicts", "registry"}, (
        "verdicts awaits get_doc_sha256 and registry awaits a bounded "
        "asyncio.wait_for; every other step drives the synchronous MinIO/Redis "
        f"clients and must stay a plain def. Got: {sorted(coroutine_steps)}"
    )


# ---------------------------------------------------------------------------
# Regression: delete_doc with declarative manifest produces equivalent output
# ---------------------------------------------------------------------------


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
        await delete_doc("unknown-name-1")

    # hash_cache_delete should NOT be called (no doc_name)
    mock_hc.assert_not_called()


async def test_delete_doc_recovers_doc_name_past_extracted_md_sidecar(mock_minio):
    """RFC-050 D3 / HR2: when only the <name>.extracted.md sidecar is under
    uploads/<doc_id>/ (the original upload save failed), the recovered
    doc_name is <name>, not the sidecar's name -- else the hash-cache and
    preloaded/ purges miss."""
    mock_minio.get_object.side_effect = _nosuchkey()
    sidecar = MagicMock(object_name="uploads/sidecar01/katzen.pdf.extracted.md")
    mock_minio.list_objects.side_effect = lambda _b, prefix="", **_k: (
        [sidecar] if prefix.startswith("uploads/") else []
    )

    with (
        patch("pageindex_mcp.cache.doc_cache_delete"),
        patch("pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete"),
        patch("pageindex_mcp.storage.hash_cache.hash_cache_delete") as mock_hc,
    ):
        await delete_doc("sidecar01")

    mock_hc.assert_called_once_with("katzen.pdf")
    removed = [c.args[1] for c in mock_minio.remove_object.call_args_list]
    assert "uploads/sidecar01/katzen.pdf.extracted.md" in removed
    assert "preloaded/katzen.pdf" in removed


# --- from test_minio_path_prefix.py ---


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


# --- from test_presign_public_route.py ---


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

    # ---------------------------------------------------------------------------
    # Zone-5: Regression — save_doc_meta preserves existing consistency_regime
    # ---------------------------------------------------------------------------


def test_save_doc_meta_preserves_consistency_regime_on_verdict_update(mock_minio):
    """Regression: save_doc_meta must preserve an existing consistency_regime
    field during read-merge-write when the new call supplies only verdict
    fields (no consistency_regime). Without this, a subsequent verdict-only
    write from the promotion sweep would silently drop the forensic regime
    stamp set by _upsert_registry_row."""

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
    save_doc_meta(
        "regime-preserve-1",
        {
            "verdict": "PASS",
            "pipeline_version": 5,
            "verdict_computed_at": "2026-08-28T01:00:00+00:00",
        },
    )

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


# ---------------------------------------------------------------------------
# Consolidated (test-budget reduction): table-driven replacements.  Every row
# the former per-row tests covered is still checked; mismatches are collected
# and reported by row name.  The HR2 erasure-cascade tests above are
# deliberately left untouched and un-merged.
# ---------------------------------------------------------------------------


@patch("pageindex_mcp.storage.minio_ops.get_minio")
def test_wipe_processed_removes_every_processed_object(mock_get):
    """wipe_processed() deletes all processed/* objects (tree, sidecar and
    flat alike) and is a no-op on an empty listing.  The verdicts/ prefix is
    not part of the listing it walks."""
    mc = MagicMock()
    mock_get.return_value = mc
    mc.list_objects.return_value = [
        _obj("processed/doc1.json"),
        _obj("processed/doc1.meta.json"),
        _obj("processed/doc2.json"),
    ]

    wipe_processed()

    removed = {c.args[1] for c in mc.mock_calls if c[0] == "remove_object"}
    assert removed == {
        "processed/doc1.json",
        "processed/doc1.meta.json",
        "processed/doc2.json",
    }

    mc.reset_mock()
    mc.list_objects.return_value = []
    wipe_processed()
    mc.remove_object.assert_not_called()


def test_flat_02_c3_list_processed_docs_surfaces_flat_content_class(mock_minio):
    """FLAT-02-C3: list_processed_docs surfaces a flat doc with its
    content_class, and prefers the .meta.json sidecar over the .flat.json
    artifact when both are listed for the same doc_id."""
    meta_obj = MagicMock()
    meta_obj.object_name = "processed/flat0001.meta.json"
    mock_minio.list_objects.return_value = [meta_obj]

    meta_resp = MagicMock()
    meta_resp.read.return_value = json.dumps(
        {"doc_id": "flat0001", "doc_name": "katzen.pdf", "content_class": "flat_prose"}
    ).encode()
    mock_minio.get_object.return_value = meta_resp

    docs = list_processed_docs()
    assert len(docs) == 1
    assert docs[0]["doc_id"] == "flat0001"
    assert docs[0]["doc_name"] == "katzen.pdf"
    assert docs[0]["content_class"] == "flat_prose"

    # Sidecar preferred over the .flat.json blob for the same doc_id.
    flat_obj = MagicMock()
    flat_obj.object_name = "processed/dup0001.flat.json"
    dup_meta = MagicMock()
    dup_meta.object_name = "processed/dup0001.meta.json"
    mock_minio.list_objects.return_value = [flat_obj, dup_meta]
    response = MagicMock()
    response.read.return_value = json.dumps({"doc_id": "dup0001", "doc_name": "y.pdf"}).encode()
    mock_minio.get_object.return_value = response

    docs = list_processed_docs()
    assert len(docs) == 1
    assert mock_minio.get_object.call_args[0][1] == "processed/dup0001.meta.json"


def test_read_registry_fields_tree_doc_and_missing_object(mock_minio):
    """read_registry_fields projects the registry columns (incl. a derived
    node_count and the verdict triple, but never content_class) out of
    processed/<doc_id>.json, and degrades to None when the object is absent."""
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

    mock_minio.get_object.side_effect = _nosuchkey()
    assert read_registry_fields("ghost0001") is None


def test_hash_cache_roundtrip_and_staging_helpers(mock_minio, fake_cache_redis):
    """Property 6: the hash cache is a Redis HSET, so writes for different
    filenames are independent (no last-writer-wins loss across arq worker
    processes) and deleting one entry leaves the others intact.  Also pins the
    staging helpers and the legacy MinIO hash blob's missing-object fallback."""
    # upload_staging writes uploads/staging/<job>/<name> as an octet-stream and
    # returns that key; delete_staging reports success.
    assert upload_staging("job-1", "report.pdf", b"bytes") == "uploads/staging/job-1/report.pdf"
    call = mock_minio.put_object.call_args
    assert call[0][1] == "uploads/staging/job-1/report.pdf"
    assert call.kwargs["content_type"] == "application/octet-stream"
    assert delete_staging("uploads/staging/job-1/report.pdf") is True

    # The legacy MinIO hash-cache blob degrades to {} when absent.
    mock_minio.get_object.side_effect = _nosuchkey()
    assert _load_legacy_minio_hash_cache() == {}

    hash_cache_set("a.pdf", "hash-a")
    hash_cache_set("b.pdf", "hash-b")
    assert hash_cache_get("a.pdf") == "hash-a"
    assert hash_cache_get("b.pdf") == "hash-b"

    hash_cache_delete("a.pdf")

    with patch("pageindex_mcp.storage.minio_ops.get_minio") as mock_get_minio:
        mock_get_minio.return_value.get_object.side_effect = _nosuchkey()
        assert hash_cache_get("a.pdf") is None
    assert hash_cache_get("b.pdf") == "hash-b"


def test_save_doc_meta_sidecar_field_projection(mock_minio):
    """The .meta.json sidecar persists the RFC-014 D2 verdict fields, the C-3
    sidecar-v2 fattening fields (doc_description by KEY PRESENCE, so "" is
    kept) and the RFC-034 D5 extraction-provenance fields — and never invents
    effective_config_at_job_start when the caller did not supply it."""
    meta = {
        "doc_id": "sidecar-1",
        "doc_name": "report.pdf",
        "source_url": "",
        "processed_at": "2026-08-08T00:00:00+00:00",
        # RFC-014 D2 verdict fields
        "verdict": "PASS",
        "verdict_reason": "cat_b_promoted",
        "max_leaf_ratio": 0.12,
        "pipeline_version": 1,
        "permanent_marginal": False,
        "promotion_eligible": True,
        "verdict_computed_at": "2026-07-16T00:00:00+00:00",
        # C-3 sidecar v2 fattening
        "sha256": "abc",
        "doc_description": "",
        # RFC-034 D5 provenance
        "extraction_route": "remote",
        "converter_name": "docling",
        "converter_contract": "2.1.0",
        "remote_build_sha": "abc1234",
        "page_count": 42,
        "inspector_class": "standard",
        "total_tree_chars": 123456,
        # supplied config, but NOT effective_config_at_job_start
        "build_sha": "abc123",
        "effective_config": {"pipeline_version": 4},
    }
    save_doc_meta("sidecar-1", meta)

    sidecar = json.loads(mock_minio.put_object.call_args[0][2].read())

    expected = {
        k: v
        for k, v in meta.items()
        if k
        not in {"doc_id", "doc_name", "source_url", "processed_at", "build_sha", "effective_config"}
    }
    missing = {
        k: (sidecar.get(k, "<absent>"), v)
        for k, v in expected.items()
        if sidecar.get(k, "<absent>") != v
    }
    assert not missing, f"sidecar dropped/renamed fields (got, want): {missing}"
    # doc_description is written by key presence, not truthiness.
    assert "doc_description" in sidecar
    # Never synthesised when the caller did not supply it.
    assert "effective_config_at_job_start" not in sidecar


# --- from test_minio_path_prefix.py ---


def test_prefixed_pool_manager_url_rewriting():
    """The public-route prefix is spliced in front of the path, exactly once,
    without touching the (signature-covered) query string."""

    def _capture(prefix, url, **kw):
        pm = PrefixedPoolManager(prefix)
        with patch.object(urllib3.PoolManager, "urlopen") as mock:
            pm.urlopen("GET", url, **kw)
        return mock.call_args.args[1]

    rows = [
        (
            "prefix inserted before path",
            "https://infra.example.com/pageindex/a.pdf",
            "https://infra.example.com/minio/pageindex/a.pdf",
        ),
        (
            # Rewriting the query would invalidate the signature.
            "query string preserved exactly",
            "https://infra.example.com/pageindex/?list-type=2&prefix=proc%2F",
            "https://infra.example.com/minio/pageindex/?list-type=2&prefix=proc%2F",
        ),
        (
            # urllib3 re-enters urlopen on redirect; /minio/minio/... would 404.
            "already-prefixed path not prefixed twice",
            "https://infra.example.com/minio/pageindex/a.pdf",
            "https://infra.example.com/minio/pageindex/a.pdf",
        ),
        (
            # /minio-staging is a different path, not an already-prefixed one.
            "prefix lookalike still prefixed",
            "https://infra.example.com/minio-staging/a",
            "https://infra.example.com/minio/minio-staging/a",
        ),
    ]
    failures = []
    for name, url, expected in rows:
        got = _capture("/minio", url)
        if got != expected:
            failures.append(f"{name}: got {got!r}, expected {expected!r}")
    assert not failures, failures


def test_prefixed_pool_inherits_sdk_settings():
    """Passing http_client= replaces the SDK's own pool, so the prefixed pool
    must carry the same timeout/retry/CA policy or those guarantees silently
    vanish on exactly the deployments that use the public route.  Explicit
    kwargs still win."""
    kw = PrefixedPoolManager("/minio").connection_pool_kw

    assert kw["timeout"].connect_timeout == 300
    assert kw["timeout"].read_timeout == 300
    assert kw["maxsize"] == 10
    assert kw["cert_reqs"] == "CERT_REQUIRED"
    assert kw["ca_certs"]
    assert kw["retries"].total == 5
    assert kw["retries"].status_forcelist == [500, 502, 503, 504]

    assert PrefixedPoolManager("/minio", maxsize=3).connection_pool_kw["maxsize"] == 3

    # make_minio installs that prefixed pool as the client's http transport,
    # and a path baked into the endpoint is still rejected by the SDK -- which
    # is the reason this whole workaround exists.
    client = make_minio("infra.example.com", "k", "s", secure=True, path_prefix="/minio")
    assert isinstance(client._http, PrefixedPoolManager)
    with pytest.raises(ValueError, match="path in endpoint"):
        make_minio("infra.example.com/minio", "k", "s", secure=True, path_prefix="")


def test_minio_route_settings_normalization(monkeypatch, reloadable_config):
    """MINIO_PATH_PREFIX / MINIO_PRESIGN_PATH_PREFIX default to "" and normalise
    'minio', '/minio' and '/minio/' to '/minio'.  MINIO_PRESIGN_SECURE defaults
    to True and is env-readable.  DOCLING_SERVICE_URL loses a trailing slash
    (otherwise '{url}/convert/pdf' becomes '//convert/pdf', which 404s) and
    stays None when unset."""
    cfg = reloadable_config
    failures = []

    def _reload(**env):
        for key in (
            "MINIO_PATH_PREFIX",
            "MINIO_PRESIGN_PATH_PREFIX",
            "MINIO_PRESIGN_SECURE",
            "DOCLING_SERVICE_URL",
        ):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        importlib.reload(cfg)
        return cfg.settings

    if _reload().minio_path_prefix != "":
        failures.append("MINIO_PATH_PREFIX unset: expected ''")
    if _reload().minio_presign_path_prefix != "":
        failures.append("MINIO_PRESIGN_PATH_PREFIX unset: expected ''")
    if _reload().minio_presign_secure is not True:
        failures.append("MINIO_PRESIGN_SECURE unset: expected True")
    if _reload(MINIO_PRESIGN_SECURE="false").minio_presign_secure is not False:
        failures.append("MINIO_PRESIGN_SECURE=false: expected False")

    for raw in ("minio", "/minio", "/minio/"):
        got = _reload(MINIO_PATH_PREFIX=raw).minio_path_prefix
        if got != "/minio":
            failures.append(f"MINIO_PATH_PREFIX={raw!r}: got {got!r}")
        got = _reload(MINIO_PRESIGN_PATH_PREFIX=raw).minio_presign_path_prefix
        if got != "/minio":
            failures.append(f"MINIO_PRESIGN_PATH_PREFIX={raw!r}: got {got!r}")

    s = _reload(DOCLING_SERVICE_URL="https://docling.example.com/")
    if s.docling_service_url != "https://docling.example.com":
        failures.append(f"docling trailing slash: got {s.docling_service_url!r}")
    elif f"{s.docling_service_url}/convert/pdf" != "https://docling.example.com/convert/pdf":
        failures.append("docling: joined convert path is malformed")
    if _reload().docling_service_url is not None:
        failures.append("DOCLING_SERVICE_URL unset: expected None")

    assert not failures, failures


# --- from test_presign_public_route.py ---


def test_presigned_url_route_prefix_matrix():
    """The route prefix is spliced in AFTER signing (the signature covers
    /pageindex/<key>, the route serves it under /minio) and the query string is
    never touched.  Which prefix applies depends on which endpoint signed the
    URL: the presign host's prefix when a presign endpoint is configured, the
    main endpoint's prefix otherwise, and nothing at all for a ClusterIP
    endpoint that has no route prefix of its own."""
    import pageindex_mcp.storage as storage

    # The presign client itself is built for the PUBLIC host: MINIO_SECURE=false
    # must not downgrade an HTTPS presign host, and the region must be pinned or
    # the SDK issues GetBucketLocation against a host that cannot route it.
    with (
        patch.object(storage.minio_ops, "_presign_client", None),
        patch.object(storage.minio_ops, "make_minio") as mock_cls,
        patch.object(storage.minio_ops, "settings") as mock_settings,
    ):
        _presign_settings(mock_settings)
        storage._get_presign_minio()
    assert mock_cls.call_args.kwargs["secure"] is True
    assert mock_cls.call_args.kwargs.get("region") == "us-east-1"

    signed_query = "X-Amz-Signature=abc&X-Amz-Credential=k%2Fus-east-1&X-Amz-Expires=900"
    rows = [
        (
            "presign prefix spliced after signing",
            {"minio_presign_path_prefix": "/minio"},
            "https://infra.example.com/pageindex/uploads/a.pdf?X-Amz-Signature=abc",
            "https://infra.example.com/minio/pageindex/uploads/a.pdf?X-Amz-Signature=abc",
        ),
        (
            "query string untouched",
            {"minio_presign_path_prefix": "/minio"},
            f"https://infra.example.com/pageindex/uploads/a.pdf?{signed_query}",
            f"https://infra.example.com/minio/pageindex/uploads/a.pdf?{signed_query}",
        ),
        (
            "no prefix leaves the url unchanged",
            {"minio_presign_path_prefix": ""},
            "https://infra.example.com/pageindex/uploads/a.pdf?X-Amz-Signature=abc",
            "https://infra.example.com/pageindex/uploads/a.pdf?X-Amz-Signature=abc",
        ),
        (
            "presign prefix ignored when the endpoint addresses MinIO directly",
            {
                "minio_presign_endpoint": None,
                "minio_endpoint": "10.43.23.66:9000",
                "minio_path_prefix": "",
                "minio_presign_path_prefix": "/minio",
            },
            "http://10.43.23.66:9000/pageindex/uploads/a.pdf?X-Amz-Signature=abc",
            "http://10.43.23.66:9000/pageindex/uploads/a.pdf?X-Amz-Signature=abc",
        ),
        (
            # No separate presign endpoint: the URL is built from the main
            # endpoint, so it needs the MAIN endpoint's route prefix or it 404s.
            "main prefix used when no presign endpoint",
            {
                "minio_presign_endpoint": None,
                "minio_endpoint": "infra.example.com",
                "minio_path_prefix": "/minio",
                "minio_presign_path_prefix": "",
            },
            "https://infra.example.com/pageindex/uploads/a.pdf?X-Amz-Signature=abc",
            "https://infra.example.com/minio/pageindex/uploads/a.pdf?X-Amz-Signature=abc",
        ),
    ]

    failures = []
    for name, overrides, signed, expected in rows:
        mock_client = MagicMock()
        mock_client.presigned_get_object.return_value = signed
        with (
            patch.object(storage.minio_ops, "_get_presign_minio", return_value=mock_client),
            patch.object(storage.minio_ops, "settings") as mock_settings,
        ):
            _presign_settings(mock_settings, **overrides)
            url = storage.presigned_get_url("uploads/a.pdf")
        if url != expected:
            failures.append(f"{name}: got {url!r}, expected {expected!r}")

    assert not failures, failures


# ---------------------------------------------------------------------------
# --- from test_memory_redis.py (memory admission, Redis singleton, cache) ---
# ---------------------------------------------------------------------------

_MEMINFO_SAMPLE = (
    "MemTotal:        7937224 kB\n"
    "MemFree:          200000 kB\n"
    "MemAvailable:    2500000 kB\n"
    "Buffers:           10000 kB\n"
)

SAMPLE_DOC = {"doc_id": "abc12345", "doc_name": "test.pdf", "structure": []}


async def test_wait_for_memory_admission_matrix(tmp_path, monkeypatch):
    """The admission gate, end to end: read_meminfo_available_bytes parses
    MemAvailable into bytes and fails OPEN (None) on an unreadable
    /proc/meminfo; _has_headroom compares against the floor and treats an
    unreadable reading as "proceed"; and wait_for_memory admits immediately
    when there is headroom, polls until memory frees, fails OPEN (returns False
    but still proceeds) once the max-wait cap is hit — a job is never stuck
    forever — and fails open again when the Redis holding the admission lock is
    unreachable.  Every failure mode here is fail-OPEN by design: the gate is
    never allowed to be worse than admitting unconditionally."""
    failures = []

    good = tmp_path / "meminfo"
    good.write_text(_MEMINFO_SAMPLE)
    if ma.read_meminfo_available_bytes(path=str(good)) != 2500000 * 1024:
        failures.append("MemAvailable was not parsed into bytes")
    if ma.read_meminfo_available_bytes(path=str(tmp_path / "nope")) is not None:
        failures.append("unreadable meminfo did not fail open with None")

    for name, available, expected in (
        ("above floor", 3_000_000_000, True),
        ("below floor", 1_000_000_000, False),
        ("unreadable -> fail open", None, True),
    ):
        got = ma._has_headroom(available, floor=2_300_000_000)
        if got is not expected:
            failures.append(f"_has_headroom({name}): got {got!r}, expected {expected!r}")

    def _const(value):
        return lambda path="/proc/meminfo": value

    def _sequence(values):
        it = iter(values)
        return lambda path="/proc/meminfo": next(it, values[-1])

    original_poll = ma.MEM_ADMISSION_POLL_S
    original_max = ma.MEM_ADMISSION_MAX_WAIT_S

    rows = [
        # (name, meminfo reader, poll_s, max_wait_s, expected return)
        ("headroom now", _const(3_000_000_000), original_poll, original_max, True),
        (
            "waits then proceeds",
            _sequence([1_000_000_000, 1_000_000_000, 3_000_000_000]),
            0.01,
            original_max,
            True,
        ),
        ("fails open at max wait", _const(1_000_000_000), 0.01, 0.05, False),
    ]

    for name, reader, poll, max_wait, expected in rows:
        monkeypatch.setattr(ma, "read_meminfo_available_bytes", reader)
        monkeypatch.setattr(ma, "MEM_ADMISSION_POLL_S", poll)
        monkeypatch.setattr(ma, "MEM_ADMISSION_MAX_WAIT_S", max_wait)
        # Explicit floor: the default now resolves from settings.docling_service_url
        # (RFC-050 D1), which must not make this matrix's outcomes depend on the
        # runtime environment's .env.
        got = await ma.wait_for_memory(fakeredis.aioredis.FakeRedis(), floor=2_300_000_000)
        if got is not expected:
            failures.append(f"{name}: wait_for_memory returned {got!r}, expected {expected!r}")

    # A Redis that cannot hold the admission lock must not crash the job.
    class _BrokenRedis:
        async def set(self, *a, **k):
            raise RuntimeError("redis down")

        async def delete(self, *a, **k):
            raise RuntimeError("redis down")

    monkeypatch.setattr(ma, "read_meminfo_available_bytes", _const(3_000_000_000))
    monkeypatch.setattr(ma, "MEM_ADMISSION_POLL_S", original_poll)
    monkeypatch.setattr(ma, "MEM_ADMISSION_MAX_WAIT_S", original_max)
    if await ma.wait_for_memory(_BrokenRedis(), floor=2_300_000_000) is not True:
        failures.append("lock-Redis failure: admission did not fail open")

    assert not failures, failures


def test_cgroup_available_bytes_matrix(tmp_path):
    """RFC-050 D1. cgroup v2: a finite memory.max yields (max - working set),
    working set being current minus memory.stat's inactive_file (kubelet), or
    current when memory.stat is absent; "max" or an absurd (>= 2**60) sentinel
    is unlimited (None, fall back to host); missing files fail open to None.
    No v2 memory.max -> v1 limit_in_bytes/usage_in_bytes (minus memory.stat
    total_inactive_file); the v1 "no limit" sentinel (~2**63) is unlimited.
    effective_available_bytes = min(host, cgroup), host alone without a limit."""
    v2_cases = [
        # name, files, expected
        (
            "v2 limited",
            {"memory.max": "2147483648", "memory.current": "1073741824"},
            2147483648 - 1073741824,
        ),
        ("v2 unlimited (max)", {"memory.max": "max", "memory.current": "0"}, None),
        ("v2 unlimited (huge sentinel)", {"memory.max": str(2**62), "memory.current": "0"}, None),
        ("no cgroup files at all", {}, None),
        (
            "v2 page cache is reclaimable (working set = current - inactive_file)",
            {
                "memory.max": "2000",
                "memory.current": "1500",
                "memory.stat": "anon 700\ninactive_file 600\nactive_file 200\n",
            },
            2000 - (1500 - 600),
        ),
        (
            "v2 inactive_file above current clamps working set at 0",
            {"memory.max": "2000", "memory.current": "100", "memory.stat": "inactive_file 900\n"},
            2000,
        ),
    ]
    v1_cases = [
        # name, limit, usage, memory.stat, expected
        ("v1 limited", "1073741824", "268435456", None, 1073741824 - 268435456),
        ("v1 unlimited sentinel", str(2**63 - 1), "0", None, None),
        (
            "v1 total_inactive_file subtracted",
            "1000",
            "800",
            "inactive_file 5\ntotal_inactive_file 300\n",
            1000 - (800 - 300),
        ),
    ]
    failures = []
    for i, (name, files, expected) in enumerate(v2_cases):
        d = tmp_path / f"v2_{i}"
        d.mkdir()
        for fname, content in files.items():
            (d / fname).write_text(content)
        got = ma.read_cgroup_available_bytes(
            v2_max_path=str(d / "memory.max"),
            v2_current_path=str(d / "memory.current"),
            v1_limit_path=str(d / "nope.limit"),
            v1_usage_path=str(d / "nope.usage"),
        )
        if got != expected:
            failures.append(f"{name}: got {got!r}, expected {expected!r}")
    for i, (name, limit, usage, stat, expected) in enumerate(v1_cases):
        d = tmp_path / f"v1_{i}"
        d.mkdir()
        (d / "memory.limit_in_bytes").write_text(limit)
        (d / "memory.usage_in_bytes").write_text(usage)
        if stat is not None:
            (d / "memory.stat").write_text(stat)
        got = ma.read_cgroup_available_bytes(
            v2_max_path=str(d / "nope.max"),
            v2_current_path=str(d / "nope.current"),
            v1_limit_path=str(d / "memory.limit_in_bytes"),
            v1_usage_path=str(d / "memory.usage_in_bytes"),
        )
        if got != expected:
            failures.append(f"{name}: got {got!r}, expected {expected!r}")

    # effective_available_bytes: a tighter cgroup limit wins; none -> host.
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(_MEMINFO_SAMPLE)  # MemAvailable: 2500000 kB = 2_560_000_000 bytes
    eff = tmp_path / "eff"
    eff.mkdir()
    (eff / "memory.max").write_text("1000000000")
    (eff / "memory.current").write_text("0")
    for name, d, expected in [
        ("cgroup tighter than host wins", eff, 1_000_000_000),
        ("no cgroup -> host passes through", tmp_path / "absent", 2_560_000_000),
    ]:
        got = ma.effective_available_bytes(
            meminfo_path=str(meminfo),
            v2_max_path=str(d / "memory.max"),
            v2_current_path=str(d / "memory.current"),
            v1_limit_path=str(d / "nope.limit"),
            v1_usage_path=str(d / "nope.usage"),
        )
        if got != expected:
            failures.append(f"effective: {name}: got {got!r}, expected {expected!r}")

    assert not failures, failures


def test_resolve_admission_floor(monkeypatch):
    """RFC-050 D1: the service floor (800 MiB default) applies only when
    Docling offload is actually configured (config.docling_offload_configured:
    DOCLING_SERVICE_URL set AND a docling converter entry); local mode keeps
    the original ~2.2 GiB floor. An explicit service flag always wins."""
    import dataclasses
    import importlib.util

    url = "http://docling-service.pageindex.svc:8080"
    cases = [
        # docling_service_url, have_docling, expected_floor
        (None, True, ma.MEM_ADMISSION_FLOOR_BYTES),
        ("", True, ma.MEM_ADMISSION_FLOOR_BYTES),
        (url, True, ma.MEM_ADMISSION_FLOOR_SERVICE_BYTES),
        # URL set but no docling chain entry -> indexer never offloads -> local floor.
        (url, False, ma.MEM_ADMISSION_FLOOR_BYTES),
    ]
    real_find_spec = importlib.util.find_spec
    base_settings = ma.settings
    failures = []
    for docling_service_url, have_docling, expected_floor in cases:
        monkeypatch.setattr(
            importlib.util,
            "find_spec",
            lambda n, *a, _have=have_docling: (
                (object() if _have else None) if n == "docling" else real_find_spec(n, *a)
            ),
        )
        patched = dataclasses.replace(base_settings, docling_service_url=docling_service_url)
        monkeypatch.setattr(ma, "settings", patched)
        got = ma.resolve_admission_floor()
        if got != expected_floor:
            failures.append(f"url={docling_service_url!r} docling={have_docling}: got {got}")
        if ma.resolve_admission_floor(True) != ma.MEM_ADMISSION_FLOOR_SERVICE_BYTES:
            failures.append(f"url={docling_service_url!r}: explicit True must give service floor")
    assert not failures, failures


@pytest.mark.asyncio
async def test_concurrent_admission_only_one_admits(monkeypatch):
    """Two simultaneous callers with capacity for exactly one — only one admits,
    because the admission lock is held across the whole check-then-admit
    decision."""
    monkeypatch.setattr(ma, "MEM_ADMISSION_POLL_S", 0.01)
    monkeypatch.setattr(ma, "MEM_ADMISSION_MAX_WAIT_S", 0.15)

    admitted = 0
    original_has_headroom = ma._has_headroom

    def _shrinking_headroom(available_bytes, floor=ma.MEM_ADMISSION_FLOOR_BYTES):
        nonlocal admitted
        if admitted == 0:
            admitted += 1
            return original_has_headroom(available_bytes, floor)
        return False

    monkeypatch.setattr(
        ma, "read_meminfo_available_bytes", lambda path="/proc/meminfo": 3_000_000_000
    )
    monkeypatch.setattr(ma, "_has_headroom", _shrinking_headroom)

    redis_client = fakeredis.aioredis.FakeRedis()
    results = await asyncio.gather(
        ma.wait_for_memory(redis_client),
        ma.wait_for_memory(redis_client),
    )

    true_count = sum(1 for r in results if r is True)
    assert true_count == 1, f"Expected exactly 1 admission, got {true_count}"

    # That mutual exclusion holds because the lock is taken BEFORE the headroom
    # check and released only after the decision.
    events: list[str] = []
    original_acquire = ma._try_acquire_lock
    original_release = ma._release_lock

    async def _tracking_acquire(redis_client):
        events.append("acquire")
        return await original_acquire(redis_client)

    def _tracking_read(*args, **kwargs):
        events.append("check")
        return 3_000_000_000

    async def _tracking_release(redis_client):
        events.append("release")
        return await original_release(redis_client)

    monkeypatch.setattr(ma, "_try_acquire_lock", _tracking_acquire)
    monkeypatch.setattr(ma, "_release_lock", _tracking_release)
    monkeypatch.setattr(ma, "read_meminfo_available_bytes", _tracking_read)
    monkeypatch.setattr(ma, "_has_headroom", original_has_headroom)

    assert await ma.wait_for_memory(fakeredis.aioredis.FakeRedis()) is True
    assert events == ["acquire", "check", "release"], (
        f"Expected lock held through check-then-admit, got: {events}"
    )


@pytest.mark.asyncio
@patch("pageindex_mcp.worker.job.get_async_redis", new_callable=AsyncMock)
async def test_worker_redis_fallback_uses_singleton(mock_get_redis):
    """When ctx has no 'redis' key, the fallback calls get_async_redis()."""
    redis = AsyncMock()
    # _set_job_status compare-and-sets through a Lua script; "OK" is the
    # script's success return, and a bare AsyncMock reads as a refused
    # transition.
    redis.eval = AsyncMock(return_value="OK")
    mock_get_redis.return_value = redis

    with (
        patch("pageindex_mcp.worker.job.download_staging"),
        patch(
            "pageindex_mcp.worker.job._run_converter_subprocess",
            new_callable=AsyncMock,
            return_value={"ok": True, "doc_id": "test123", "peak_rss_kib": 0, "duration_ms": 0},
        ),
        patch("pageindex_mcp.worker.job.delete_staging"),
        patch("pageindex_mcp.worker.job.shutil"),
    ):
        from pageindex_mcp.worker import process_document_job

        ctx: dict = {}
        await process_document_job(ctx, "uploads/staging/job-1/report.pdf", "job-1")

    # Zone-7 added several best-effort Redis metric-bridge mirror calls (each
    # independently resolving the singleton), so the fallback is no longer
    # called exactly once -- but every call must still resolve through
    # get_async_redis(), never a fresh aioredis.from_url().
    mock_get_redis.assert_called()


def test_store_01_c3_load_doc_returns_persisted_tree(mock_minio):
    """STORE-01-C3: load_doc(doc_id) returns the tree previously written by
    save_doc to processed/<doc_id>.json, deserialized into a value-equivalent
    dict (json.loads of the stored bytes, not the raw bytes).  A doc_id with no
    object raises ValueError rather than leaking the S3Error."""
    tree = {
        "doc_id": "rt000001",
        "doc_name": "roundtrip.pdf",
        "structure": [{"title": "Root", "nodes": [{"title": "Child", "text": "body"}]}],
    }
    with patch("pageindex_mcp.cache.doc_cache_delete"):
        save_doc("rt000001", tree)

    persisted_bytes = mock_minio.put_object.call_args[0][2]
    persisted_bytes.seek(0)
    response = MagicMock()
    response.read.return_value = persisted_bytes.read()
    mock_minio.get_object.return_value = response

    loaded = load_doc("rt000001")

    assert mock_minio.get_object.call_args[0][1] == "processed/rt000001.json"
    assert loaded == tree
    assert loaded is not tree  # a fresh deserialization, not the same object

    mock_minio.get_object.side_effect = _nosuchkey()
    with pytest.raises(ValueError, match="Document not found"):
        load_doc("rt000001")


def test_store_01_c2_unchanged_bytes_resolve_to_the_existing_doc_id(mock_minio, fake_cache_redis):
    """STORE-01-C2 (storage half): the SHA-256 dedup short-circuit reads two
    storage facts — the filename-keyed hash-cache entry and the processed-doc
    listing.  For unchanged bytes the cached hash matches, and the listing
    still carries an entry for that filename, so the caller can return the
    existing doc_id without a new processed/<doc_id>.json write.  Changed bytes
    produce a mismatch and no short-circuit.

    Scope note: the indexer-side branch that acts on these two facts (and the
    pageindex:job:<job_id> status write) lives in client/indexer.py and is not
    exercised here — this file owns only the storage-layer inputs.
    """
    sha_v1 = "a" * 64
    sha_v2 = "b" * 64
    hash_cache_set("dedup.pdf", sha_v1)

    meta_obj = MagicMock()
    meta_obj.object_name = "processed/dedup001.meta.json"
    mock_minio.list_objects.return_value = [meta_obj]
    resp = MagicMock()
    resp.read.return_value = json.dumps(
        {"doc_id": "dedup001", "doc_name": "dedup.pdf", "content_class": "flat_prose"}
    ).encode()
    mock_minio.get_object.return_value = resp

    # Unchanged bytes -> cached hash matches, so the caller short-circuits.
    assert hash_cache_get("dedup.pdf") == sha_v1
    existing = [d for d in list_processed_docs() if d["doc_name"] == "dedup.pdf"]
    assert [d["doc_id"] for d in existing] == ["dedup001"]
    assert mock_minio.put_object.call_count == 0, "dedup path must write nothing new"

    # Changed bytes -> mismatch, no short-circuit.
    assert hash_cache_get("dedup.pdf") != sha_v2


# ── RFC-050 D6 / HR5: ensure_quarantine_lifecycle ────────────────────────────
# 30-day expiration rule on quarantine/, read-merge-write, idempotent, and
# never raises (a missing lifecycle permission must not break ingestion).


def test_ensure_quarantine_lifecycle(monkeypatch, caplog):
    """Merge + idempotency across three starting configs, then the never-raises
    contract: missing s3:Get/PutBucketLifecycleConfiguration permission degrades
    to a warning, not a broken get_minio()."""
    from minio.commonconfig import ENABLED, Filter
    from minio.lifecycleconfig import Expiration, LifecycleConfig, Rule

    monkeypatch.delenv("QUARANTINE_TTL_DAYS", raising=False)

    def _applied(client):
        call = client.set_bucket_lifecycle.call_args
        return call.args[1] if len(call.args) > 1 else call.kwargs["config"]

    other = Rule(
        ENABLED, rule_filter=Filter(prefix="uploads/"), rule_id="uploads-90d",
        expiration=Expiration(days=90),
    )
    present = Rule(
        ENABLED, rule_filter=Filter(prefix="quarantine/"), rule_id="quarantine-30d",
        expiration=Expiration(days=30),
    )
    cases = [
        # name, existing config, expect set, expected other rule ids kept
        ("empty_config_adds_rule", None, True, set()),
        ("unrelated_rule_kept_and_merged", LifecycleConfig([other]), True, {"uploads-90d"}),
        ("idempotent_second_call_noop", LifecycleConfig([present]), False, set()),
    ]
    failures = []
    for name, existing, expect_set, kept in cases:
        client = MagicMock()
        client.get_bucket_lifecycle.return_value = existing
        ensure_quarantine_lifecycle(client, "pageindex")
        if client.set_bucket_lifecycle.called is not expect_set:
            failures.append(f"{name}: set called={client.set_bucket_lifecycle.called}")
            continue
        if not expect_set:
            continue
        config = _applied(client)
        q = [r for r in config.rules if r.rule_id == "quarantine-30d"]
        others = {r.rule_id for r in config.rules} - {"quarantine-30d"}
        if not (
            len(q) == 1
            and q[0].rule_filter.prefix == "quarantine/"
            and q[0].expiration.days == 30
            and others == kept
        ):
            failures.append(f"{name}: bad merged config {[r.rule_id for r in config.rules]}")
        # Calling again with the resulting config in place must be a no-op.
        client2 = MagicMock()
        client2.get_bucket_lifecycle.return_value = config
        ensure_quarantine_lifecycle(client2, "pageindex")
        if client2.set_bucket_lifecycle.called:
            failures.append(f"{name}: second call was not a no-op")
    assert not failures, failures

    # Never raises: AccessDenied on read -> warning, no write.
    client = MagicMock()
    client.get_bucket_lifecycle.side_effect = _other_s3error("AccessDenied")
    with caplog.at_level("WARNING"):
        ensure_quarantine_lifecycle(client, "pageindex")  # must not raise
    assert not client.set_bucket_lifecycle.called
    assert any("quarantine" in rec.message.lower() for rec in caplog.records)

    # NoSuchLifecycleConfiguration is the expected "empty" signal, not a failure.
    client2 = MagicMock()
    client2.get_bucket_lifecycle.side_effect = _nosuchkey_lifecycle_error()
    ensure_quarantine_lifecycle(client2, "pageindex")
    assert client2.set_bucket_lifecycle.called

    # A failure in set_bucket_lifecycle itself must also be swallowed.
    client3 = MagicMock()
    client3.get_bucket_lifecycle.return_value = None
    client3.set_bucket_lifecycle.side_effect = RuntimeError("boom")
    with caplog.at_level("WARNING"):
        ensure_quarantine_lifecycle(client3, "pageindex")  # must not raise

    # Wiring: documents.py (owner of the quarantine prefix) registers the rule
    # as a get_minio() bucket-init hook at import; a raising hook is isolated.
    from pageindex_mcp.storage import minio_ops

    assert ensure_quarantine_lifecycle in minio_ops._BUCKET_INIT_HOOKS
    boom = MagicMock(side_effect=RuntimeError("hook boom"))
    monkeypatch.setattr(minio_ops, "_BUCKET_INIT_HOOKS", [boom, ensure_quarantine_lifecycle])
    client4 = MagicMock()
    client4.get_bucket_lifecycle.return_value = None
    minio_ops._run_bucket_init_hooks(client4, "pageindex")  # must not raise
    assert client4.set_bucket_lifecycle.called


def _nosuchkey_lifecycle_error():
    return S3Error(MagicMock(), "NoSuchLifecycleConfiguration", "none", "res", "req", "host")
