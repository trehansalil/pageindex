"""Tests for .meta.json sidecar storage."""

import json
from unittest.mock import MagicMock, patch

import pytest

from pageindex_mcp.storage import (
    SIDECAR_VERSION,
    save_doc_meta,
    list_processed_docs,
    delete_doc,
)
from pageindex_mcp.helpers import _garble_check_nodes, validate_tree


@pytest.fixture
def mock_minio():
    client = MagicMock()
    client.bucket_exists.return_value = True
    with patch("pageindex_mcp.storage.get_minio", return_value=client):
        yield client


def test_save_doc_meta_writes_sidecar(mock_minio):
    meta = {
        "doc_id": "abcd1234",
        "doc_name": "report.pdf",
        "source_url": "http://minio:9000/pageindex/uploads/abcd1234/report.pdf",
        "processed_at": "2026-04-08T00:00:00+00:00",
    }
    save_doc_meta("abcd1234", meta)

    mock_minio.put_object.assert_called_once()
    call_args = mock_minio.put_object.call_args
    assert call_args[0][1] == "processed/abcd1234.meta.json"
    written = call_args[0][2].read()
    sidecar = json.loads(written)
    # C-3 sidecar v2: the 4 base fields are still present verbatim …
    assert {k: sidecar[k] for k in meta} == meta
    # … and every sidecar now carries the explicit generation marker.
    assert sidecar["sidecar_version"] == SIDECAR_VERSION


def test_list_processed_docs_reads_meta_files(mock_minio):
    meta_obj = MagicMock()
    meta_obj.object_name = "processed/abcd1234.meta.json"

    full_obj = MagicMock()
    full_obj.object_name = "processed/abcd1234.json"

    mock_minio.list_objects.return_value = [meta_obj, full_obj]

    meta_content = json.dumps(
        {
            "doc_id": "abcd1234",
            "doc_name": "report.pdf",
            "source_url": "",
            "processed_at": "2026-04-08T00:00:00+00:00",
        }
    ).encode()
    response = MagicMock()
    response.read.return_value = meta_content
    mock_minio.get_object.return_value = response

    docs = list_processed_docs()
    assert len(docs) == 1
    assert docs[0]["doc_id"] == "abcd1234"
    assert docs[0]["doc_name"] == "report.pdf"
    # Should only fetch .meta.json, never the full .json
    mock_minio.get_object.assert_called_once()
    fetched_key = mock_minio.get_object.call_args[0][1]
    assert fetched_key.endswith(".meta.json")


def test_list_processed_docs_falls_back_to_full_json(mock_minio):
    """When no .meta.json exists (legacy docs), fall back to full .json."""
    full_obj = MagicMock()
    full_obj.object_name = "processed/old12345.json"
    mock_minio.list_objects.return_value = [full_obj]

    full_content = json.dumps(
        {
            "doc_id": "old12345",
            "doc_name": "legacy.pdf",
            "source_url": "",
            "processed_at": "2026-01-01T00:00:00+00:00",
            "structure": [{"node_id": "n1", "title": "Ch1", "text": "lots of text..."}],
        }
    ).encode()
    response = MagicMock()
    response.read.return_value = full_content
    mock_minio.get_object.return_value = response

    docs = list_processed_docs()
    assert len(docs) == 1
    assert docs[0]["doc_id"] == "old12345"


def test_save_doc_meta_produces_node_count(mock_minio):
    """D2 / RFC-009 Property 2: save_doc_meta persists node_count computed from
    the tree structure into the .meta.json sidecar."""
    # 4 nodes total: Ch1 + its two children (1.1, 1.2) + Ch2.
    structure = [
        {
            "title": "Chapter 1",
            "nodes": [
                {"title": "1.1", "nodes": []},
                {"title": "1.2", "nodes": []},
            ],
        },
        {"title": "Chapter 2", "nodes": []},
    ]
    meta = {
        "doc_id": "tree0001",
        "doc_name": "report.pdf",
        "source_url": "",
        "processed_at": "2026-04-08T00:00:00+00:00",
        "structure": structure,
    }
    save_doc_meta("tree0001", meta)

    written = mock_minio.put_object.call_args[0][2].read()
    sidecar = json.loads(written)
    assert sidecar["node_count"] == 4
    # The (potentially large) structure itself is NOT persisted in the lean sidecar.
    assert "structure" not in sidecar


def test_backward_compat_missing_node_count(mock_minio):
    """D2 / RFC-009 Property 2 backward compat: a legacy .meta.json that predates
    node_count must not break list_processed_docs — the field defaults to None."""
    meta_obj = MagicMock()
    meta_obj.object_name = "processed/legacy01.meta.json"
    mock_minio.list_objects.return_value = [meta_obj]

    # Legacy sidecar: no node_count key at all.
    legacy_meta = json.dumps(
        {
            "doc_id": "legacy01",
            "doc_name": "old.pdf",
            "source_url": "",
            "processed_at": "2026-01-01T00:00:00+00:00",
        }
    ).encode()
    response = MagicMock()
    response.read.return_value = legacy_meta
    mock_minio.get_object.return_value = response

    docs = list_processed_docs()  # must not KeyError
    assert len(docs) == 1
    assert docs[0]["doc_id"] == "legacy01"
    assert docs[0]["node_count"] is None


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


def test_save_doc_meta_verdict_fields_absent_legacy_compat(mock_minio):
    """RFC-014 D2: when verdict fields are absent (legacy caller), sidecar
    is byte-identical to the pre-D2 format — no extra keys added."""
    meta = {
        "doc_id": "legacy02",
        "doc_name": "old.pdf",
        "source_url": "",
        "processed_at": "2026-01-01T00:00:00+00:00",
    }
    save_doc_meta("legacy02", meta)

    written = mock_minio.put_object.call_args[0][2].read()
    sidecar = json.loads(written)
    # C-3 sidecar v2: a thin-input sidecar carries only the 4 base fields plus
    # the always-present sidecar_version marker — no verdict/sha256/description.
    assert set(sidecar.keys()) == {
        "doc_id",
        "doc_name",
        "source_url",
        "processed_at",
        "sidecar_version",
    }
    assert sidecar["sidecar_version"] == SIDECAR_VERSION
    for vf in (
        "verdict",
        "verdict_reason",
        "max_leaf_ratio",
        "pipeline_version",
        "permanent_marginal",
        "promotion_eligible",
        "verdict_computed_at",
        "sha256",
        "doc_description",
        "product",
        "tier",
        "doc_family",
        "effective_date",
    ):
        assert vf not in sidecar


# ── C-3 sidecar v2: sha256 + doc_description fattening ───────────────────────
def test_save_doc_meta_persists_sha256_and_doc_description(mock_minio):
    """C-3 / Finding 9: the fattened sidecar carries sha256 AND doc_description so
    the reconcile cron never has to GET the full processed JSON for a new doc."""
    meta = {
        "doc_id": "fat00001",
        "doc_name": "report.pdf",
        "source_url": "",
        "processed_at": "2026-07-21T00:00:00+00:00",
        "sha256": "deadbeefcafef00d",
        "doc_description": "A one-sentence summary of the document.",
    }
    save_doc_meta("fat00001", meta)

    written = mock_minio.put_object.call_args[0][2].read()
    sidecar = json.loads(written)
    assert sidecar["sha256"] == "deadbeefcafef00d"
    assert sidecar["doc_description"] == "A one-sentence summary of the document."
    assert sidecar["sidecar_version"] == SIDECAR_VERSION


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


def test_save_doc_meta_omits_sha256_when_absent(mock_minio):
    """C-3: sha256 is omit-when-absent so legacy callers that never supply it
    produce a thin (v1-shaped payload + version marker) sidecar."""
    meta = {
        "doc_id": "fat00003",
        "doc_name": "report.pdf",
        "source_url": "",
        "processed_at": "2026-07-21T00:00:00+00:00",
    }
    save_doc_meta("fat00003", meta)

    written = mock_minio.put_object.call_args[0][2].read()
    sidecar = json.loads(written)
    assert "sha256" not in sidecar
    assert sidecar["sidecar_version"] == SIDECAR_VERSION


def test_save_doc_meta_persists_forward_compat_facets(mock_minio):
    """C-3 (forward-compat): C-1 facet fields are lossless on the fat path when
    present, and omit-when-absent so they are a no-op until C-1 lands."""
    meta = {
        "doc_id": "fat00004",
        "doc_name": "report.pdf",
        "source_url": "",
        "processed_at": "2026-07-21T00:00:00+00:00",
        "sha256": "x",
        "doc_description": "d",
        "product": "prod-a",
        "tier": "1",
        "doc_family": "fam",
        "effective_date": "2026-01-01",
    }
    save_doc_meta("fat00004", meta)

    written = mock_minio.put_object.call_args[0][2].read()
    sidecar = json.loads(written)
    assert sidecar["product"] == "prod-a"
    assert sidecar["tier"] == "1"
    assert sidecar["doc_family"] == "fam"
    assert sidecar["effective_date"] == "2026-01-01"


async def test_delete_doc_purges_reconcile_etag(mock_minio):
    """HR2 / step 4b: the reconcile-etag Redis map is a derived store, so
    delete_doc must purge the doc's etag entry — while still removing the
    processed/<id>.meta.json sidecar (keeps test_delete_doc_removes_meta_sidecar
    green)."""
    mock_minio.list_objects.return_value = []
    doc_json = json.dumps(
        {"doc_id": "purge001", "doc_name": "report.pdf", "structure": []}
    ).encode()
    response = MagicMock()
    response.read.return_value = doc_json
    mock_minio.get_object.return_value = response

    with (
        patch("pageindex_mcp.storage.reconcile_etag_delete") as mock_etag_del,
        patch("pageindex_mcp.storage.hash_cache_delete"),
        patch("pageindex_mcp.cache.doc_cache_delete"),
    ):
        await delete_doc("purge001")

    mock_etag_del.assert_called_once_with("purge001")
    calls = [c[0][1] for c in mock_minio.remove_object.call_args_list]
    assert "processed/purge001.meta.json" in calls


async def test_delete_doc_removes_meta_sidecar(mock_minio):
    mock_minio.list_objects.return_value = []
    # delete_doc reads the doc first (to capture doc_name for the hash-cache step
    # of the HR2/ERASE-01 cascade), so get_object must return valid JSON bytes.
    doc_json = json.dumps(
        {"doc_id": "abcd1234", "doc_name": "report.pdf", "structure": []}
    ).encode()
    response = MagicMock()
    response.read.return_value = doc_json
    mock_minio.get_object.return_value = response

    await delete_doc("abcd1234")
    calls = [c[0][1] for c in mock_minio.remove_object.call_args_list]
    assert "processed/abcd1234.meta.json" in calls


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


def test_validate_tree_node_garbling_exceeds_threshold():
    """RFC-018 D3b: when the per-node garbled ratio exceeds the 10% default
    threshold (GARBLE_NODE_RATIO_THRESHOLD), validate_tree rejects the tree
    with reason "node_garbling" — even though the flattened whole-document
    text stays well under the bulk garble gates.

    1 garbled node out of 9 total (root + 8 children) is ~11.1%, which
    exceeds the threshold (the gate uses a strict ">" comparison, so an
    exact 10% ratio does not itself trip the gate)."""
    garbled_node = {"title": "Bad", "text": _pua_heavy_text()}
    tree = [
        {
            "title": "Root",
            "text": "root section text",
            "nodes": [garbled_node] + [_clean_node(i) for i in range(7)],
        }
    ]

    ok, reason = validate_tree(tree)
    assert ok is False
    assert reason == "node_garbling"


def test_validate_tree_node_garbling_below_threshold_passes():
    """RFC-018 D3b: 1 garbled node out of 100 total (root + 99 children) is a
    1% ratio, well under the 10% default threshold, so validate_tree passes."""
    garbled_node = {"title": "Bad", "text": _pua_heavy_text()}
    tree = [
        {
            "title": "Root",
            "text": "root section text",
            "nodes": [garbled_node] + [_clean_node(i) for i in range(98)],
        }
    ]

    ok, reason = validate_tree(tree)
    assert ok is True
    assert reason == ""


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


def test_save_doc_meta_provenance_fields_absent_not_null(mock_minio):
    """RFC-034 D5: provenance fields are omit-when-absent — a legacy caller that
    never supplies them must not see them written as null/None."""
    meta = {
        "doc_id": "prov0002",
        "doc_name": "old.pdf",
        "source_url": "",
        "processed_at": "2026-01-01T00:00:00+00:00",
    }
    save_doc_meta("prov0002", meta)

    written = mock_minio.put_object.call_args[0][2].read()
    sidecar = json.loads(written)
    for pf in (
        "extraction_route",
        "converter_name",
        "converter_contract",
        "remote_build_sha",
        "page_count",
        "inspector_class",
        "total_tree_chars",
    ):
        assert pf not in sidecar


def test_save_doc_meta_extraction_route_remote_and_local(mock_minio):
    """RFC-034 D5: extraction_route round-trips exactly for both the remote and
    local extraction paths."""
    meta_remote = {
        "doc_id": "prov0003",
        "doc_name": "report.pdf",
        "source_url": "",
        "processed_at": "2026-08-08T00:00:00+00:00",
        "extraction_route": "remote",
    }
    save_doc_meta("prov0003", meta_remote)
    written = mock_minio.put_object.call_args[0][2].read()
    assert json.loads(written)["extraction_route"] == "remote"

    meta_local = dict(meta_remote, doc_id="prov0004", extraction_route="local")
    save_doc_meta("prov0004", meta_local)
    written = mock_minio.put_object.call_args[0][2].read()
    assert json.loads(written)["extraction_route"] == "local"


def test_sidecar_version_is_3():
    """RFC-034 D5: SIDECAR_VERSION is bumped to 3 to mark the provenance-field
    generation of the sidecar format."""
    assert SIDECAR_VERSION == 3
