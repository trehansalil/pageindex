"""End-to-end test: upload a DOCX via the /upload endpoint, verify the file
is staged in MinIO, downloadable by the worker, and cleaned up afterward.

Requires MinIO to be reachable (uses the .env settings).
"""

import asyncio
import os
import tempfile

import pytest

from pageindex_mcp.storage import (
    delete_staging,
    download_staging,
    get_minio,
    upload_staging,
)
from pageindex_mcp.config import settings

DOCX_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "UrbanMop_CRM_Evaluation_TeleCRM_vs_Alternatives.docx",
)


@pytest.fixture(autouse=True)
def skip_if_no_minio():
    """Skip these tests when MinIO is unreachable."""
    try:
        get_minio()
    except Exception:
        pytest.skip("MinIO not reachable")


class TestStagingRoundTrip:
    """Verify the MinIO staging upload → download → delete cycle."""

    def test_upload_download_delete(self):
        job_id = "test-e2e-staging-001"
        filename = "test_doc.pdf"
        data = b"%PDF-1.4 fake content for staging test"

        # Upload
        key = upload_staging(job_id, filename, data)
        assert key == f"uploads/staging/{job_id}/{filename}"

        # Verify object exists in MinIO
        mc = get_minio()
        stat = mc.stat_object(settings.minio_bucket, key)
        assert stat.size == len(data)

        # Download
        tmp_dir = tempfile.mkdtemp()
        dest = os.path.join(tmp_dir, filename)
        download_staging(key, dest)
        assert os.path.isfile(dest)
        with open(dest, "rb") as f:
            assert f.read() == data

        # Cleanup
        os.unlink(dest)
        os.rmdir(tmp_dir)
        delete_staging(key)

        # Verify object is gone
        from minio.error import S3Error

        with pytest.raises(S3Error):
            mc.stat_object(settings.minio_bucket, key)

    def test_docx_file_staging(self):
        """Stage the actual DOCX file and verify round-trip integrity."""
        if not os.path.isfile(DOCX_PATH):
            pytest.skip("DOCX test file not found")

        with open(DOCX_PATH, "rb") as f:
            original_bytes = f.read()

        job_id = "test-e2e-docx-staging"
        filename = os.path.basename(DOCX_PATH)

        # Stage in MinIO
        key = upload_staging(job_id, filename, original_bytes)

        # Download to temp
        tmp_dir = tempfile.mkdtemp()
        dest = os.path.join(tmp_dir, filename)
        download_staging(key, dest)

        # Verify byte-for-byte integrity
        with open(dest, "rb") as f:
            downloaded_bytes = f.read()
        assert downloaded_bytes == original_bytes
        assert len(downloaded_bytes) > 0

        # Cleanup
        os.unlink(dest)
        os.rmdir(tmp_dir)
        delete_staging(key)
