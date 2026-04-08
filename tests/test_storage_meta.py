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


def test_delete_doc_removes_meta_sidecar(mock_minio):
    mock_minio.list_objects.return_value = []
    delete_doc("abcd1234")
    calls = [c[0][1] for c in mock_minio.remove_object.call_args_list]
    assert "processed/abcd1234.meta.json" in calls
