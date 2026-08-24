"""RFC-015 D1 (task 1.1 / 1.5) — preprocess_client.SUPPORTED sourced from the
canonical pageindex_mcp.client._SUPPORTED set.

Prior to this change, preprocess_client.py hardcoded its own
``SUPPORTED = {".pdf", ".docx", ".pptx", ".md", ".txt", ".html"}``, silently
excluding extensions the HTTP upload path already supports (``.jpg``,
``.xlsx``, ``.png``, ...). Batch preprocessing of doc_store/ would drop those
files with no warning (see corpus audit 2026-07-17).
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


def test_supported_is_canonical_client_set():
    """preprocess_client.SUPPORTED is the same object/value as
    pageindex_mcp.client._SUPPORTED — no local duplicate definition."""
    import preprocess_client

    from pageindex_mcp.client import _SUPPORTED

    assert preprocess_client.SUPPORTED is _SUPPORTED


def test_supported_includes_previously_dropped_extensions():
    """Regression guard for the corpus-audit finding: .jpg, .xlsx, .png must
    be enqueue-able via the batch preprocessing tool."""
    import preprocess_client

    for ext in (".jpg", ".xlsx", ".png"):
        assert ext in preprocess_client.SUPPORTED, (
            f"{ext} missing from preprocess_client.SUPPORTED — batch "
            "preprocessing would silently skip these files"
        )


def test_supported_is_superset_of_old_hardcoded_set():
    """No regression: every extension in the old hardcoded set is still
    covered by the canonical set."""
    import preprocess_client

    old_hardcoded = {".pdf", ".docx", ".pptx", ".md", ".txt", ".html"}
    assert old_hardcoded <= preprocess_client.SUPPORTED


@pytest.mark.asyncio
async def test_jpg_file_enqueues_via_process_one(tmp_path):
    """Integration-style: a .jpg file in doc_store/ is accepted by
    _files_to_process and drives a converter-subprocess enqueue via
    _process_one — it is not silently skipped."""
    import preprocess_client

    jpg = tmp_path / "photo.jpg"
    jpg.write_bytes(b"\xff\xd8\xff\xe0fakejpegdata")

    # _files_to_process filters against SUPPORTED — confirm .jpg passes.
    with patch("preprocess_client.DOC_STORE", tmp_path):
        files = preprocess_client._files_to_process(None)
    assert jpg in files

    # Stub the converter-subprocess call (the actual "enqueue") so this stays
    # fast/offline, then confirm _process_one drives it for the .jpg file.
    mock_result = {"doc_id": "fake-doc-id", "content_class": "image"}
    fake_run = AsyncMock(return_value=mock_result)
    sem = asyncio.Semaphore(1)

    with patch("pageindex_mcp.worker._run_converter_subprocess", fake_run, create=True):
        await preprocess_client._process_one(sem, jpg)

    fake_run.assert_awaited_once_with(str(jpg))
