"""Tests for .meta.json sidecar storage."""

import json
from unittest.mock import MagicMock, patch

import pytest

from pageindex_mcp.storage import save_doc_meta, list_processed_docs, delete_doc


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
    assert json.loads(written) == meta


def test_list_processed_docs_reads_meta_files(mock_minio):
    meta_obj = MagicMock()
    meta_obj.object_name = "processed/abcd1234.meta.json"

    full_obj = MagicMock()
    full_obj.object_name = "processed/abcd1234.json"

    mock_minio.list_objects.return_value = [meta_obj, full_obj]

    meta_content = json.dumps({
        "doc_id": "abcd1234",
        "doc_name": "report.pdf",
        "source_url": "",
        "processed_at": "2026-04-08T00:00:00+00:00",
    }).encode()
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

    full_content = json.dumps({
        "doc_id": "old12345",
        "doc_name": "legacy.pdf",
        "source_url": "",
        "processed_at": "2026-01-01T00:00:00+00:00",
        "structure": [{"node_id": "n1", "title": "Ch1", "text": "lots of text..."}],
    }).encode()
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
    legacy_meta = json.dumps({
        "doc_id": "legacy01",
        "doc_name": "old.pdf",
        "source_url": "",
        "processed_at": "2026-01-01T00:00:00+00:00",
    }).encode()
    response = MagicMock()
    response.read.return_value = legacy_meta
    mock_minio.get_object.return_value = response

    docs = list_processed_docs()  # must not KeyError
    assert len(docs) == 1
    assert docs[0]["doc_id"] == "legacy01"
    assert docs[0]["node_count"] is None


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
