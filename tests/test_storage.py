# tests/test_storage.py
"""RFC-033 D0 property test: hysteresis snapshot survives the processed/* wipe.

Property 0 (design-rfc033 Correctness Properties): after wipe_processed()
completes, snapshots/_prior_verdicts.json MUST exist in MinIO AND all objects
under processed/* MUST be deleted. find_prior_verdict() MUST read the
relocated snapshots/ prefix and return the verdict stored before the wipe.
"""

import json
import re
from unittest.mock import MagicMock, patch

import pytest

from pageindex_mcp.storage import find_prior_verdict, wipe_processed


@pytest.fixture
def mock_minio():
    client = MagicMock()
    client.bucket_exists.return_value = True
    with patch("pageindex_mcp.storage.get_minio", return_value=client):
        yield client


def _meta_object(name):
    obj = MagicMock()
    obj.object_name = name
    return obj


def _response(payload):
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    return response


def test_wipe_processed_snapshot_survives_wipe(mock_minio):
    """Property 0: snapshots/_prior_verdicts.json exists AFTER processed/* is
    deleted, and the snapshot write happens before any processed/* removal."""
    mock_minio.list_objects.return_value = [
        _meta_object("processed/doc1.meta.json"),
        _meta_object("processed/doc1.json"),
    ]
    mock_minio.get_object.return_value = _response(
        {"sha256": "abc123", "doc_name": "t.pdf", "verdict": "PASS"}
    )

    wipe_processed()

    put_call = next(c for c in mock_minio.mock_calls if c[0] == "put_object")
    assert put_call.args[1] == "snapshots/_prior_verdicts.json"

    put_index = mock_minio.mock_calls.index(put_call)
    remove_calls = [c for c in mock_minio.mock_calls if c[0] == "remove_object"]
    assert remove_calls, "wipe_processed must remove processed/* objects"
    for index, call in enumerate(mock_minio.mock_calls):
        if call[0] != "remove_object":
            continue
        assert index > put_index, "snapshot must be written before any deletion"
        assert call.args[1].startswith("processed/")

    # Property 0, second half: ALL listed processed/* objects are deleted, and
    # the snapshot key -- which lives outside processed/ -- is never a target.
    removed = {c.args[1] for c in remove_calls}
    assert removed == {"processed/doc1.meta.json", "processed/doc1.json"}
    assert "snapshots/_prior_verdicts.json" not in removed


def test_wipe_processed_aborts_when_snapshot_missing(mock_minio):
    """Property 0 is unsatisfiable without a snapshot: if snapshot_prior_verdicts()
    failed open, wipe_processed() must refuse to delete processed/*."""
    mock_minio.list_objects.return_value = [_meta_object("processed/doc1.meta.json")]
    mock_minio.get_object.return_value = _response(
        {"sha256": "abc123", "doc_name": "t.pdf", "verdict": "PASS"}
    )
    mock_minio.stat_object.side_effect = Exception("NoSuchKey")

    with pytest.raises(RuntimeError, match=re.escape("snapshots/_prior_verdicts.json")):
        wipe_processed()

    mock_minio.remove_object.assert_not_called()


def test_find_prior_verdict_reads_relocated_snapshot_after_wipe(mock_minio):
    """After the processed/* wipe, find_prior_verdict() must fall back to
    snapshots/_prior_verdicts.json (not processed/_prior_verdicts.json) and
    resolve the verdict stored before the wipe."""
    # Post-wipe: processed/ is empty, only the snapshot prefix has data.
    mock_minio.list_objects.return_value = []
    mock_minio.get_object.return_value = _response(
        {
            "snapshot_at": "2026-01-01T00:00:00+00:00",
            "entries": [
                {
                    "sha256": "abc123",
                    "doc_name": "t.pdf",
                    "doc_id": "old-doc-id",
                    "verdict": "PASS",
                }
            ],
        }
    )

    verdict = find_prior_verdict("abc123", "t.pdf", "new-doc-id")

    assert verdict == "PASS"
    fetched_key = mock_minio.get_object.call_args[0][1]
    assert fetched_key == "snapshots/_prior_verdicts.json"
