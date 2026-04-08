# Scaling PageIndex MCP Server — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate redundant MinIO I/O on every request, offload document processing to dedicated workers, and enable multi-process serving via gunicorn.

**Architecture:** Introduce lightweight `.meta.json` sidecar files so listing never downloads full doc trees. Add a Redis cache layer for `load_doc` shared across gunicorn workers. Move document processing from in-process `asyncio.create_task` to a Redis-backed `arq` task queue consumed by separate worker processes. Serve via gunicorn with uvicorn workers.

**Tech Stack:** Python 3.12, Redis (via `redis[asyncio]`), `arq` task queue, gunicorn + uvicorn, MinIO, FastMCP, FastAPI.

---

### Task 1: Store separate `.meta.json` sidecar files

**Why:** `list_processed_docs()` currently downloads and JSON-parses every full processed doc from MinIO on every call. Writing a small metadata sidecar at index time lets listing read only tiny files.

**Files:**
- Modify: `src/pageindex_mcp/storage.py` — add `save_doc_meta()`, rewrite `list_processed_docs()`
- Modify: `src/pageindex_mcp/client.py:131` — call `save_doc_meta()` after `save_doc()`
- Create: `tests/test_storage_meta.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_storage_meta.py
"""Tests for .meta.json sidecar storage."""

import json
from io import BytesIO
from unittest.mock import MagicMock, patch, call

import pytest

from pageindex_mcp.storage import save_doc_meta, list_processed_docs


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/pageindex_deployment && uv run pytest tests/test_storage_meta.py -v`
Expected: FAIL — `save_doc_meta` does not exist, `list_processed_docs` doesn't filter `.meta.json`.

- [ ] **Step 3: Implement `save_doc_meta()` and rewrite `list_processed_docs()`**

In `src/pageindex_mcp/storage.py`, add `save_doc_meta` after `save_doc`:

```python
_META_FIELDS = ("doc_id", "doc_name", "source_url", "processed_at")


def save_doc_meta(doc_id: str, meta: dict) -> None:
    """Write a lightweight sidecar with only listing-relevant fields."""
    MINIO_OPS.labels(operation="put").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        content = json.dumps(
            {k: meta.get(k, "") for k in _META_FIELDS}, indent=2
        ).encode()
        mc.put_object(
            settings.minio_bucket,
            f"processed/{doc_id}.meta.json",
            BytesIO(content),
            len(content),
            content_type="application/json",
        )
        logger.debug("Saved meta for doc %s (%d bytes)", doc_id, len(content))
    finally:
        MINIO_DURATION.labels(operation="put").observe(time.monotonic() - start)
```

Rewrite `list_processed_docs` to prefer `.meta.json`:

```python
def list_processed_docs() -> list[dict]:
    """List all processed documents.  Reads lightweight .meta.json sidecars
    when available, falling back to full .json for legacy documents."""
    MINIO_OPS.labels(operation="list").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        meta_keys: dict[str, str] = {}   # doc_id -> object_name (prefer .meta.json)
        for obj in mc.list_objects(settings.minio_bucket, prefix="processed/", recursive=True):
            name = obj.object_name
            if name.endswith(".meta.json"):
                doc_id = Path(name).stem.removesuffix(".meta")
                meta_keys[doc_id] = name
            elif name.endswith(".json"):
                doc_id = Path(name).stem
                if doc_id not in meta_keys:
                    meta_keys[doc_id] = name

        docs = []
        for doc_id, obj_name in meta_keys.items():
            response = None
            try:
                response = mc.get_object(settings.minio_bucket, obj_name)
                data = json.loads(response.read())
                docs.append({
                    "doc_id":       data.get("doc_id", doc_id),
                    "doc_name":     data.get("doc_name", data.get("filename", "unknown")),
                    "source_url":   data.get("source_url", ""),
                    "processed_at": data.get("processed_at", ""),
                })
            except Exception as e:
                logger.warning("Failed to read doc metadata %s: %s", obj_name, e)
                continue
            finally:
                if response is not None:
                    try:
                        response.close()
                        response.release_conn()
                    except Exception:
                        pass
        logger.debug("Listed %d processed documents", len(docs))
        return docs
    finally:
        MINIO_DURATION.labels(operation="list").observe(time.monotonic() - start)
```

- [ ] **Step 4: Wire `save_doc_meta` into `client.py`**

In `src/pageindex_mcp/client.py`, add import and call after `save_doc`:

```python
# At top, add save_doc_meta to imports:
from .storage import (
    list_processed_docs,
    load_doc,
    load_hash_cache,
    save_doc,
    save_doc_meta,
    save_hash_cache,
    save_raw,
)

# Inside index(), after the save_doc() call (~line 131), add:
            meta = {
                "doc_id":       doc_id,
                "doc_name":     filename,
                "source_url":   source_url,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }
            await asyncio.to_thread(save_doc_meta, doc_id, meta)
```

- [ ] **Step 5: Also write `.meta.json` on `delete_doc` cleanup**

In `src/pageindex_mcp/storage.py`, update `delete_doc` to also remove the sidecar:

```python
def delete_doc(doc_id: str) -> None:
    """Remove processed/<doc_id>.json, .meta.json, and all uploads/<doc_id>/ objects."""
    MINIO_OPS.labels(operation="delete").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        mc.remove_object(settings.minio_bucket, f"processed/{doc_id}.json")
        # Also remove sidecar if it exists.
        try:
            mc.remove_object(settings.minio_bucket, f"processed/{doc_id}.meta.json")
        except S3Error:
            pass
        removed = 0
        for obj in mc.list_objects(settings.minio_bucket, prefix=f"uploads/{doc_id}/", recursive=True):
            mc.remove_object(settings.minio_bucket, obj.object_name)
            removed += 1
        logger.info("Deleted doc %s from MinIO (processed + meta + %d uploads)", doc_id, removed)
    finally:
        MINIO_DURATION.labels(operation="delete").observe(time.monotonic() - start)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /root/pageindex_deployment && uv run pytest tests/test_storage_meta.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 7: Run existing tests to check for regressions**

Run: `cd /root/pageindex_deployment && uv run pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/pageindex_mcp/storage.py src/pageindex_mcp/client.py tests/test_storage_meta.py
git commit -m "feat: add .meta.json sidecars to avoid full doc downloads on listing"
```

---

### Task 2: Eliminate double/triple loads in RAG pipeline

**Why:** `find_relevant_documents` calls `list_processed_docs()` (loads all docs for IDs), then `_rag_inner` calls `load_doc()` for each doc again. Pass doc_ids through directly instead of reloading.

**Files:**
- Modify: `src/pageindex_mcp/tools/documents.py:68-89` — pass doc_ids list directly
- Modify: `src/pageindex_mcp/helpers.py:58-68` — `_rag` and `_rag_inner` already accept `doc_ids`, no change needed there
- Create: `tests/test_rag_dedup.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rag_dedup.py
"""Verify that find_relevant_documents does not double-load documents."""

import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from pageindex_mcp.tools.documents import find_relevant_documents


@pytest.fixture(autouse=True)
def _reset_metrics():
    """Prevent Prometheus duplicate-registration errors across tests."""
    yield


async def test_find_relevant_documents_loads_each_doc_once():
    """load_doc should be called once per doc during RAG, not twice."""
    fake_meta = [
        {"doc_id": "aaa11111", "doc_name": "a.pdf", "source_url": "", "processed_at": ""},
    ]
    fake_doc = {
        "doc_name": "a.pdf",
        "doc_description": "",
        "structure": [
            {"node_id": "n1", "title": "Intro", "summary": "intro", "text": "hello",
             "start_index": 1, "end_index": 1},
        ],
    }
    with (
        patch("pageindex_mcp.tools.documents.list_processed_docs", return_value=fake_meta),
        patch("pageindex_mcp.helpers.load_doc", return_value=fake_doc) as mock_load,
        patch("pageindex_mcp.helpers._llm", new_callable=AsyncMock) as mock_llm,
    ):
        mock_llm.side_effect = [
            '{"thinking": "relevant", "node_list": ["n1"]}',
            "The answer is hello.",
        ]
        result = await find_relevant_documents("test query")

    # load_doc called once per doc, not twice (once in list + once in rag)
    assert mock_load.call_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/pageindex_deployment && uv run pytest tests/test_rag_dedup.py -v`
Expected: FAIL — `mock_load.call_count` is 2 (list_processed_docs no longer loads full docs after Task 1, but _rag_inner still calls load_doc). Depending on order, this may already pass after Task 1. If it passes, the test still validates the contract going forward.

- [ ] **Step 3: Verify no extra load_doc calls remain**

After Task 1, `list_processed_docs` no longer calls `load_doc`, so the only `load_doc` call is inside `_rag_inner`. This test documents the expected behavior. No code change needed if Task 1 is already applied.

- [ ] **Step 4: Run all tests**

Run: `cd /root/pageindex_deployment && uv run pytest tests/ -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_rag_dedup.py
git commit -m "test: verify RAG pipeline loads each document exactly once"
```

---

### Task 3: Redis-backed shared cache for `load_doc`

**Why:** With multiple gunicorn workers, in-memory caches aren't shared. A Redis cache for `load_doc` prevents redundant MinIO reads across all workers. Documents are immutable after processing, so caching is safe.

**Files:**
- Create: `src/pageindex_mcp/cache.py` — Redis cache get/set/invalidate helpers
- Modify: `src/pageindex_mcp/storage.py` — wrap `load_doc` with cache, invalidate on `save_doc`/`delete_doc`
- Modify: `src/pageindex_mcp/config.py` — add `cache_ttl` setting
- Create: `tests/test_cache.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cache.py
"""Tests for Redis-backed document cache."""

import json
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from pageindex_mcp.cache import doc_cache_get, doc_cache_set, doc_cache_delete, get_cache_redis


SAMPLE_DOC = {"doc_id": "abc12345", "doc_name": "test.pdf", "structure": []}


@pytest.fixture
def fake_redis_sync():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def _patch_redis(fake_redis_sync):
    with patch("pageindex_mcp.cache._redis_sync", fake_redis_sync):
        yield fake_redis_sync


def test_cache_miss_returns_none(_patch_redis):
    assert doc_cache_get("nonexistent") is None


def test_cache_roundtrip(_patch_redis):
    doc_cache_set("abc12345", SAMPLE_DOC)
    cached = doc_cache_get("abc12345")
    assert cached == SAMPLE_DOC


def test_cache_delete(_patch_redis):
    doc_cache_set("abc12345", SAMPLE_DOC)
    doc_cache_delete("abc12345")
    assert doc_cache_get("abc12345") is None


def test_cache_ttl_is_set(_patch_redis):
    redis = _patch_redis
    doc_cache_set("abc12345", SAMPLE_DOC)
    ttl = redis.ttl("pageindex:doc:abc12345")
    assert ttl > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/pageindex_deployment && uv run pytest tests/test_cache.py -v`
Expected: FAIL — `pageindex_mcp.cache` does not exist.

- [ ] **Step 3: Add `cache_ttl` to config**

In `src/pageindex_mcp/config.py`, add to `Settings`:

```python
@dataclass(frozen=True)
class Settings:
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    minio_secure: bool
    doc_store_path: Path
    server_host: str
    server_port: int
    redis_url: str
    upload_api_key: str
    cache_ttl: int
```

And in `_load_settings()`:

```python
        cache_ttl=int(os.environ.get("CACHE_TTL", "300")),
```

- [ ] **Step 4: Implement `cache.py`**

```python
# src/pageindex_mcp/cache.py
"""Redis-backed document cache shared across gunicorn workers."""

import json
import logging

import redis

from .config import settings

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "pageindex:doc:"

_redis_sync: redis.Redis | None = None


def get_cache_redis() -> redis.Redis:
    """Lazy singleton for synchronous Redis client (used by storage layer)."""
    global _redis_sync
    if _redis_sync is None:
        _redis_sync = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_sync


def doc_cache_get(doc_id: str) -> dict | None:
    """Return cached document dict or None on miss."""
    try:
        r = get_cache_redis()
        raw = r.get(f"{_CACHE_PREFIX}{doc_id}")
        if raw is not None:
            return json.loads(raw)
    except Exception:
        logger.debug("Cache get failed for %s", doc_id, exc_info=True)
    return None


def doc_cache_set(doc_id: str, data: dict) -> None:
    """Cache a document dict with TTL."""
    try:
        r = get_cache_redis()
        r.setex(
            f"{_CACHE_PREFIX}{doc_id}",
            settings.cache_ttl,
            json.dumps(data),
        )
    except Exception:
        logger.debug("Cache set failed for %s", doc_id, exc_info=True)


def doc_cache_delete(doc_id: str) -> None:
    """Invalidate cached document."""
    try:
        r = get_cache_redis()
        r.delete(f"{_CACHE_PREFIX}{doc_id}")
    except Exception:
        logger.debug("Cache delete failed for %s", doc_id, exc_info=True)
```

- [ ] **Step 5: Run cache tests**

Run: `cd /root/pageindex_deployment && uv run pytest tests/test_cache.py -v`
Expected: All 4 PASS.

- [ ] **Step 6: Wire cache into `storage.py`**

In `src/pageindex_mcp/storage.py`, add imports and update `load_doc`, `save_doc`, `delete_doc`:

```python
from .cache import doc_cache_get, doc_cache_set, doc_cache_delete
```

Update `load_doc`:
```python
def load_doc(doc_id: str) -> dict:
    """Fetch processed/<doc_id>.json. Uses Redis cache when available."""
    cached = doc_cache_get(doc_id)
    if cached is not None:
        logger.debug("Cache hit for doc %s", doc_id)
        return cached

    MINIO_OPS.labels(operation="get").inc()
    start = time.monotonic()
    mc = get_minio()
    try:
        response = mc.get_object(settings.minio_bucket, f"processed/{doc_id}.json")
        data = json.loads(response.read())
        logger.debug("Loaded doc %s from MinIO", doc_id)
        doc_cache_set(doc_id, data)
        return data
    except S3Error as e:
        if e.code == "NoSuchKey":
            logger.warning("Document not found in MinIO: %s", doc_id)
            raise ValueError(f"Document not found: {doc_id}")
        logger.error("MinIO error loading doc %s: %s", doc_id, e)
        raise
    finally:
        MINIO_DURATION.labels(operation="get").observe(time.monotonic() - start)
        try:
            response.close()
            response.release_conn()
        except Exception:
            pass
```

At the end of `save_doc`, invalidate stale cache:
```python
def save_doc(doc_id: str, data: dict) -> None:
    # ... existing put_object logic ...
    doc_cache_delete(doc_id)
```

At the start of `delete_doc`, invalidate:
```python
def delete_doc(doc_id: str) -> None:
    doc_cache_delete(doc_id)
    # ... existing removal logic ...
```

- [ ] **Step 7: Run all tests**

Run: `cd /root/pageindex_deployment && uv run pytest tests/ -v`
Expected: All PASS. (Existing tests mock `get_minio` so the cache calls use the fakeredis or silently fail — both are fine.)

- [ ] **Step 8: Commit**

```bash
git add src/pageindex_mcp/cache.py src/pageindex_mcp/config.py src/pageindex_mcp/storage.py tests/test_cache.py
git commit -m "feat: add Redis-backed shared cache for load_doc across workers"
```

---

### Task 4: Separate document processing into `arq` workers

**Why:** CPU/IO-heavy indexing (PDF parsing, LibreOffice, LLM calls) currently runs in-process via `asyncio.create_task`, which competes with query serving. Moving to `arq` workers lets processing run in dedicated processes.

**Files:**
- Create: `src/pageindex_mcp/worker.py` — arq worker definition
- Modify: `src/pageindex_mcp/upload_app.py` — enqueue to arq instead of `asyncio.create_task`
- Modify: `pyproject.toml` — add `arq` dependency
- Create: `tests/test_worker.py`

- [ ] **Step 1: Add `arq` dependency**

In `pyproject.toml`, add to `dependencies`:

```
    "arq>=0.26.1",
```

Run: `cd /root/pageindex_deployment && uv sync`

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_worker.py
"""Tests for the arq worker task function."""

from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from pageindex_mcp.worker import process_document_job


async def test_process_document_job_calls_index():
    ctx = {}  # arq passes a context dict
    with patch("pageindex_mcp.worker.CustomPageIndexClient") as MockClient:
        MockClient.return_value.index = AsyncMock(return_value="abc12345")
        result = await process_document_job(ctx, "/tmp/fakedir/report.pdf", "job-1")

    assert result == "abc12345"
    MockClient.return_value.index.assert_awaited_once_with("/tmp/fakedir/report.pdf")


async def test_process_document_job_propagates_errors():
    ctx = {}
    with patch("pageindex_mcp.worker.CustomPageIndexClient") as MockClient:
        MockClient.return_value.index = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            await process_document_job(ctx, "/tmp/fakedir/report.pdf", "job-1")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /root/pageindex_deployment && uv run pytest tests/test_worker.py -v`
Expected: FAIL — `pageindex_mcp.worker` does not exist.

- [ ] **Step 4: Implement `worker.py`**

```python
# src/pageindex_mcp/worker.py
"""arq worker: background document processing.

Start with:
    uv run arq pageindex_mcp.worker.WorkerSettings
"""

import logging
import os
import shutil
import time

import redis.asyncio as aioredis

from .client import CustomPageIndexClient
from .config import settings
from .metrics import ACTIVE_UPLOADS, UPLOADS, UPLOAD_DURATION

logger = logging.getLogger(__name__)

JOB_TTL = 86_400


def _job_key(job_id: str) -> str:
    return f"pageindex:job:{job_id}"


async def process_document_job(ctx: dict, file_path: str, job_id: str) -> str:
    """Index a document file. Called by arq in a worker process."""
    redis: aioredis.Redis = ctx.get("redis") or aioredis.from_url(
        settings.redis_url, decode_responses=True
    )
    filename = os.path.basename(file_path)
    tmp_dir = os.path.dirname(file_path)
    ACTIVE_UPLOADS.inc()
    start = time.monotonic()
    logger.info("Worker processing: job=%s file=%s", job_id, filename)
    try:
        client = CustomPageIndexClient()
        doc_id = await client.index(file_path)
        await redis.hset(_job_key(job_id), mapping={"status": "done", "doc_id": doc_id})
        await redis.expire(_job_key(job_id), JOB_TTL)
        UPLOADS.labels(status="success").inc()
        logger.info("Worker done: job=%s doc_id=%s (%.1fs)", job_id, doc_id, time.monotonic() - start)
        return doc_id
    except Exception as exc:
        await redis.hset(_job_key(job_id), mapping={"status": "error", "error": str(exc)})
        await redis.expire(_job_key(job_id), JOB_TTL)
        UPLOADS.labels(status="error").inc()
        logger.error("Worker failed: job=%s error=%s", job_id, exc, exc_info=True)
        raise
    finally:
        UPLOAD_DURATION.observe(time.monotonic() - start)
        ACTIVE_UPLOADS.dec()
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def startup(ctx: dict) -> None:
    ctx["redis"] = aioredis.from_url(settings.redis_url, decode_responses=True)


async def shutdown(ctx: dict) -> None:
    r = ctx.get("redis")
    if r:
        await r.aclose()


class WorkerSettings:
    functions = [process_document_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = None  # uses REDIS_URL via arq's default

    @staticmethod
    def redis_settings_from_env():
        from arq.connections import RedisSettings
        # arq uses a separate Redis DB to avoid collision with app data
        return RedisSettings.from_dsn(settings.redis_url)
```

- [ ] **Step 5: Run worker tests**

Run: `cd /root/pageindex_deployment && uv run pytest tests/test_worker.py -v`
Expected: All 2 PASS.

- [ ] **Step 6: Rewrite `upload_app.py` to enqueue via arq**

Replace the `_process_file` function and `asyncio.create_task` usage in `src/pageindex_mcp/upload_app.py`:

```python
"""FastAPI sub-app: POST /upload/files and GET /upload/status/{job_id}."""

import asyncio
import logging
import os
import secrets
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import redis.asyncio as aioredis
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, HTTPException, Header, UploadFile

from .client import _SUPPORTED
from .config import settings
from .metrics import ACTIVE_UPLOADS, UPLOADS, UPLOAD_DURATION

logger = logging.getLogger(__name__)

JOB_TTL = 86_400  # 24 hours in seconds
_WRITE_CHUNK = 64 * 1024  # 64 KiB chunks for streaming writes


def _job_key(job_id: str) -> str:
    return f"pageindex:job:{job_id}"


# ---------------------------------------------------------------------------
# Redis lifecycle
# ---------------------------------------------------------------------------

_redis: aioredis.Redis | None = None
_arq_pool = None


def get_redis() -> aioredis.Redis:
    """Dependency: returns the Redis client, initialising it on first call."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def _get_arq_pool():
    """Lazy-init arq connection pool for enqueuing jobs."""
    global _arq_pool
    if _arq_pool is None:
        _arq_pool = await create_pool(
            RedisSettings.from_dsn(settings.redis_url)
        )
    return _arq_pool


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

async def require_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    configured = settings.upload_api_key
    if not configured:
        raise HTTPException(status_code=503, detail="Upload API key not configured")
    if not x_api_key or not secrets.compare_digest(x_api_key, configured):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_upload_app() -> FastAPI:
    """Return a FastAPI app to be mounted at /upload on the parent Starlette app."""
    app = FastAPI(title="PageIndex Upload")

    @app.post("/files", status_code=202)
    async def upload_files(
        files: list[UploadFile],
        _: None = Depends(require_api_key),
        redis: aioredis.Redis = Depends(get_redis),
    ) -> list[dict]:
        """Accept one or more files, enqueue async indexing, return job IDs."""
        logger.info("Upload request received: %d file(s)", len(files))
        arq_pool = await _get_arq_pool()
        results = []
        for file in files:
            filename = Path(file.filename or "upload").name
            ext = Path(filename).suffix.lower()
            if ext not in _SUPPORTED:
                logger.warning("Rejected unsupported file type: %s (%s)", filename, ext)
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Unsupported file type '{ext}'. "
                        f"Supported: {', '.join(sorted(_SUPPORTED))}"
                    ),
                )

            tmp_dir = tempfile.mkdtemp()
            tmp_path = os.path.join(tmp_dir, filename)
            await asyncio.to_thread(_stream_to_disk, file.file, tmp_path)
            logger.debug("Saved upload to temp path: %s", tmp_path)

            job_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            await redis.hset(
                _job_key(job_id),
                mapping={
                    "status": "pending",
                    "filename": filename,
                    "submitted_at": now,
                },
            )
            await redis.expire(_job_key(job_id), JOB_TTL)

            await arq_pool.enqueue_job(
                "process_document_job", tmp_path, job_id,
            )
            results.append({"job_id": job_id, "filename": filename})
            logger.info("Enqueued job %s for file %s", job_id, filename)

        return results

    @app.get("/status/{job_id}")
    async def job_status(
        job_id: str,
        _: None = Depends(require_api_key),
        redis: aioredis.Redis = Depends(get_redis),
    ) -> dict:
        """Return current state of a job: pending, done, or error."""
        data = await redis.hgetall(_job_key(job_id))
        if not data:
            logger.debug("Status poll for unknown/expired job: %s", job_id)
            raise HTTPException(
                status_code=404,
                detail=f"Job '{job_id}' not found or expired",
            )
        logger.debug("Status poll: job=%s status=%s", job_id, data.get("status"))
        return {"job_id": job_id, **data}

    return app


def _stream_to_disk(src, dest_path: str) -> None:
    """Copy a file-like object to *dest_path* in chunks (runs in a thread)."""
    with open(dest_path, "wb") as f:
        while chunk := src.read(_WRITE_CHUNK):
            f.write(chunk)
```

- [ ] **Step 7: Update upload tests for arq**

In `tests/test_upload.py`, the existing tests that mock `CustomPageIndexClient` need updating because processing now goes through arq. For the upload endpoint tests, mock `_get_arq_pool` to return a mock that captures enqueued jobs, and for status tests, write directly to Redis:

```python
# At the top of tests/test_upload.py, add:
from unittest.mock import AsyncMock, patch, MagicMock

# Replace the _wait_for_tasks helper and update tests that relied on
# background task completion.  The upload endpoint now only enqueues —
# it doesn't process.

# Replace _background_tasks import with _get_arq_pool:
from pageindex_mcp.upload_app import (
    create_upload_app,
    get_redis,
    _get_arq_pool,
)
```

Key changes to test patterns:
- `test_single_upload_returns_job_id`: mock `_get_arq_pool` to return `AsyncMock(enqueue_job=AsyncMock())`
- `test_status_done_after_processing`: write `{"status": "done", "doc_id": "deadbeef"}` directly to fakeredis, then check status
- `test_status_error_on_processing_failure`: write error status directly to fakeredis
- Remove `test_cancelled_task_writes_error_status` (arq handles cancellation internally)

- [ ] **Step 8: Run all tests**

Run: `cd /root/pageindex_deployment && uv run pytest tests/ -v`
Expected: All PASS.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml src/pageindex_mcp/worker.py src/pageindex_mcp/upload_app.py tests/test_worker.py tests/test_upload.py
git commit -m "feat: move document processing to arq worker queue"
```

---

### Task 5: Gunicorn with uvicorn workers

**Why:** The server currently runs a single uvicorn process. Gunicorn with uvicorn worker class utilizes multiple CPU cores for query serving.

**Files:**
- Modify: `pyproject.toml` — add `gunicorn` dependency
- Modify: `src/pageindex_mcp/server.py` — expose ASGI app at module level for gunicorn
- Modify: `Dockerfile` — switch CMD to gunicorn
- Create: `gunicorn.conf.py` — gunicorn configuration

- [ ] **Step 1: Add `gunicorn` dependency**

In `pyproject.toml`, add to `dependencies`:

```
    "gunicorn>=22.0.0",
```

Run: `cd /root/pageindex_deployment && uv sync`

- [ ] **Step 2: Expose module-level ASGI app in `server.py`**

Gunicorn needs to import the ASGI app directly. Refactor `src/pageindex_mcp/server.py`:

```python
"""FastMCP server composition root and entry point."""

import logging
from fastmcp import FastMCP
from starlette.routing import Route

from . import tools as _tools
from .config import settings
from .metrics import metrics_response
from .upload_app import create_upload_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

mcp = FastMCP("pageindex-local")

# ---------------------------------------------------------------------------
# Query tools only — document processing is handled by arq workers.
# ---------------------------------------------------------------------------
mcp.tool()(_tools.recent_documents)
mcp.tool()(_tools.find_relevant_documents)
mcp.tool()(_tools.get_document)
mcp.tool()(_tools.get_document_structure)
mcp.tool()(_tools.get_page_content)

# ---------------------------------------------------------------------------
# Build the ASGI app (importable by gunicorn as pageindex_mcp.server:app)
# ---------------------------------------------------------------------------
starlette_app = mcp.http_app(transport="streamable-http")
starlette_app.routes.insert(0, Route("/metrics", metrics_response))
starlette_app.mount("/upload", create_upload_app())

# This is what gunicorn imports:
app = starlette_app


def main() -> None:
    """Entry point for local dev via `pageindex-mcp` console script."""
    import anyio
    import uvicorn

    print(f"Starting PageIndex MCP server at http://{settings.server_host}:{settings.server_port}/mcp")
    print(f"Upload service at http://{settings.server_host}:{settings.server_port}/upload")
    print(f"Metrics at http://{settings.server_host}:{settings.server_port}/metrics")
    print(f"MinIO endpoint: {settings.minio_endpoint}  bucket: {settings.minio_bucket}")
    print("Press Ctrl+C to stop\n")

    async def _serve() -> None:
        config = uvicorn.Config(
            app,
            host=settings.server_host,
            port=settings.server_port,
            lifespan="on",
            timeout_graceful_shutdown=2,
        )
        server = uvicorn.Server(config)
        await server.serve()

    anyio.run(_serve)
```

- [ ] **Step 3: Create `gunicorn.conf.py`**

```python
# gunicorn.conf.py
"""Gunicorn configuration for PageIndex MCP server."""

import multiprocessing
import os

bind = f"{os.environ.get('MCP_HOST', '0.0.0.0')}:{os.environ.get('MCP_PORT', '8201')}"
workers = int(os.environ.get("WEB_CONCURRENCY", min(multiprocessing.cpu_count() * 2 + 1, 9)))
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 120
graceful_timeout = 5
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
```

- [ ] **Step 4: Update Dockerfile**

```dockerfile
FROM python:3.12-slim AS builder

# Install uv and git (needed for git+https:// dependencies)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (cache-friendly layer ordering)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and install the project itself
COPY mcp_server.py gunicorn.conf.py ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# ─── Runtime ─────────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copy the entire virtual environment from the builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/mcp_server.py ./
COPY --from=builder /app/gunicorn.conf.py ./
COPY --from=builder /app/src/ ./src/

# Put the venv's Python on PATH
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8201

# Default: gunicorn with uvicorn workers.
# Override to "arq pageindex_mcp.worker.WorkerSettings" for worker instances.
CMD ["gunicorn", "-c", "gunicorn.conf.py", "pageindex_mcp.server:app"]
```

- [ ] **Step 5: Verify local startup works**

Run: `cd /root/pageindex_deployment && uv run gunicorn -c gunicorn.conf.py pageindex_mcp.server:app --check-config`
Expected: No errors. (Full startup will fail without MinIO/Redis, but config check should pass.)

- [ ] **Step 6: Run all tests**

Run: `cd /root/pageindex_deployment && uv run pytest tests/ -v`
Expected: All PASS. (Tests don't start gunicorn, just import the module.)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml gunicorn.conf.py src/pageindex_mcp/server.py Dockerfile
git commit -m "feat: serve via gunicorn with uvicorn workers for multi-core scaling"
```

---

### Task 6: Update CLAUDE.md with new architecture

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md**

Add to the **Running the Server** section:

```markdown
## Running the Server

```bash
# Development (single process)
uv run python mcp_server.py

# Production (gunicorn with uvicorn workers)
uv run gunicorn -c gunicorn.conf.py pageindex_mcp.server:app

# Start arq workers (separate process for document processing)
uv run arq pageindex_mcp.worker.WorkerSettings
```

Add a note in **Architecture**:

```markdown
**`worker.py`** — arq worker process. Runs `process_document_job` tasks enqueued by the upload endpoint. Start separately from the MCP server so document processing doesn't compete with query serving.

**`cache.py`** — Redis-backed document cache shared across gunicorn workers. `load_doc` checks Redis before hitting MinIO. Invalidated on `save_doc`/`delete_doc`.
```

Add to **Environment variables**:

```markdown
- `WEB_CONCURRENCY` — number of gunicorn workers (default: `2 * CPU + 1`, max 9)
- `CACHE_TTL` — Redis cache TTL in seconds for processed documents (default: `300`)
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with worker, cache, and gunicorn architecture"
```
