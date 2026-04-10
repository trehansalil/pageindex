# Fix: Stage uploaded files in MinIO for cross-pod worker access

**Date:** 2026-04-10
**Status:** Approved

## Problem

The upload endpoint (`upload_app.py`) saves uploaded files to the server pod's local `/tmp` and passes the filesystem path through the arq Redis queue to the worker. The worker pod (`worker-deployment.yaml`) is a separate container with its own filesystem, so it cannot access the file at that path. Result: `FileNotFoundError`.

## Solution

Stage uploaded file bytes in MinIO (shared storage) instead of local `/tmp`. Pass the MinIO object key to the worker, which downloads the file locally before processing.

## Changes

### `upload_app.py`
- Remove `tempfile.mkdtemp()` and `_stream_to_disk()` usage for upload flow
- After reading file bytes, upload to MinIO at `uploads/staging/{job_id}/{filename}`
- Enqueue arq job with `(staging_key, job_id)` instead of `(tmp_path, job_id)`

### `worker.py`
- Change `process_document_job` signature: receive `staging_key` instead of `file_path`
- Download the staged file from MinIO to a local `/tmp` dir
- Pass local path to `client.index()`
- In `finally`: clean up local temp dir + delete MinIO staging object

### `storage.py`
- Add `upload_staging(job_id, filename, data)` — uploads bytes to `uploads/staging/{job_id}/{filename}`
- Add `download_staging(staging_key, dest_path)` — downloads staging object to local path
- Add `delete_staging(staging_key)` — removes staging object after processing

## Test Plan

End-to-end: upload the DOCX file via `POST /upload/files`, poll status, verify `done` with a `doc_id`.
