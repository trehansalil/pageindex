# tests/test_minio_helper.py
"""RFC-033 D3 property test: MinIO read retries recover from transient
failures without masking permanent ones.

Property 3 (design-rfc033 Correctness Properties): get_object_with_retry()
MUST recover from up to two consecutive NoSuchKey errors and return the
object on a later attempt, but MUST surface a clean error (not swallow it)
when every attempt fails.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from minio.error import S3Error
from scripts.minio_helper import RETRY_DELAYS, cmd_meta, cmd_tree


def _no_such_key_error():
    return S3Error(
        response=MagicMock(),
        code="NoSuchKey",
        message="The specified key does not exist.",
        resource="/pageindex/processed/doc1.meta.json",
        request_id="req1",
        host_id="host1",
    )


def _response(payload):
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    return response


@patch("scripts.minio_helper.time.sleep")
@patch("scripts.minio_helper.client")
def test_cmd_meta_recovers_after_two_transient_failures(mock_client, mock_sleep, capsys):
    """Property 3: get_object raising NoSuchKey on the first two attempts and
    succeeding on the third must still yield valid JSON output from cmd_meta."""
    c = MagicMock()
    mock_client.return_value = c
    c.get_object.side_effect = [
        _no_such_key_error(),
        _no_such_key_error(),
        _response({"sha256": "abc123", "doc_name": "t.pdf"}),
    ]

    cmd_meta("doc1")

    out = capsys.readouterr().out
    assert json.loads(out) == {"sha256": "abc123", "doc_name": "t.pdf"}
    assert c.get_object.call_count == 3


@patch("scripts.minio_helper.time.sleep")
@patch("scripts.minio_helper.client")
def test_cmd_meta_raises_clean_error_after_all_attempts_fail(mock_client, mock_sleep):
    """Property 3: get_object raising NoSuchKey on every attempt must
    surface the error to the caller, not swallow it silently."""
    total_attempts = 1 + len(RETRY_DELAYS)
    c = MagicMock()
    mock_client.return_value = c
    c.get_object.side_effect = [_no_such_key_error() for _ in range(total_attempts)]

    with pytest.raises(S3Error) as excinfo:
        cmd_meta("doc1")

    assert excinfo.value.code == "NoSuchKey"
    assert c.get_object.call_count == total_attempts


@patch("scripts.minio_helper.time.sleep")
@patch("scripts.minio_helper.client")
def test_cmd_tree_falls_back_to_flat_without_burning_backoff(mock_client, mock_sleep, capsys):
    """A flat-only document must resolve on the first attempt: the fallback key
    is tried before any backoff sleep, so the normal FLAT-03-C1 path is free."""
    c = MagicMock()
    mock_client.return_value = c
    c.get_object.side_effect = [_no_such_key_error(), _response({"flat": True})]

    cmd_tree("doc1")

    assert json.loads(capsys.readouterr().out) == {"flat": True}
    assert c.get_object.call_count == 2
    mock_sleep.assert_not_called()


@patch("scripts.minio_helper.time.sleep")
@patch("scripts.minio_helper.client")
def test_cmd_meta_reraises_non_transient_s3_error_immediately(mock_client, mock_sleep):
    """AccessDenied is not transient: it must surface on the first attempt
    rather than being retried three more times."""
    c = MagicMock()
    mock_client.return_value = c
    c.get_object.side_effect = S3Error(
        response=MagicMock(),
        code="AccessDenied",
        message="denied",
        resource="/pageindex/processed/doc1.meta.json",
        request_id="req1",
        host_id="host1",
    )

    with pytest.raises(S3Error) as excinfo:
        cmd_meta("doc1")

    assert excinfo.value.code == "AccessDenied"
    assert c.get_object.call_count == 1
    mock_sleep.assert_not_called()
