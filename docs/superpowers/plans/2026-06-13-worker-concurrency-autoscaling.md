# PageIndex Worker — Conditional 2-Pod Autoscaling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Process document batches across up to 2 worker pods when the node has real memory headroom, keeping the Docling pipeline byte-for-byte unchanged and guaranteeing no OOM/eviction.

**Architecture:** Two independent units. (1) A `pageindex_arq_queue_depth` Prometheus gauge published by the already-scraped server process via a periodic `ZCARD arq:queue`, plus a KEDA Prometheus-scaler `ScaledObject` that scales the `pageindex-mcp-worker` Deployment 1↔2 on that gauge. (2) A memory-admission gate in `process_document_job` that, before each conversion, reads node `MemAvailable` from `/proc/meminfo` under a short Redis lock and proceeds only above a configured floor — so two concurrent pods serialize heavy jobs and parallelize light ones. Every failure path fails open to today's single-job behavior.

**Tech Stack:** Python 3 / asyncio, arq (Redis task queue), `redis.asyncio`, `prometheus_client`, Starlette/FastMCP, k3s, KEDA (Helm), Prometheus (in `infra` ns). Tests: pytest (`asyncio_mode = "auto"`), `fakeredis[aioredis]`.

**Spec:** `docs/superpowers/specs/2026-06-13-worker-concurrency-autoscaling-design.md`

---

## File Structure

**Repo `pageindex_deployment` (app code):**
- `src/pageindex_mcp/metrics.py` — *modify*: add `ARQ_QUEUE_DEPTH` gauge (one declaration, follows existing `ACTIVE_UPLOADS` pattern).
- `src/pageindex_mcp/queue_metrics.py` — *create*: `read_queue_depth(pool)` + `queue_depth_scrape_loop(pool, interval)` background coroutine. One responsibility: keep the gauge current.
- `src/pageindex_mcp/server.py` — *modify*: wrap the app lifespan to start/stop the scrape loop.
- `src/pageindex_mcp/memory_admission.py` — *create*: the gate. `read_meminfo_available_bytes()`, `MEM_ADMISSION_FLOOR_BYTES`/`MEM_ADMISSION_MAX_WAIT_S`/backoff constants, and `await wait_for_memory(redis)`. One responsibility: decide when it is safe to start a conversion.
- `src/pageindex_mcp/worker.py` — *modify*: call `await wait_for_memory(redis)` immediately before `_run_converter_subprocess`.
- `tests/test_queue_metrics.py`, `tests/test_memory_admission.py` — *create*.
- `tests/test_worker.py` — *modify*: assert the gate is invoked before the subprocess.

**Repo `hetzner-deployment-service` (GitOps):**
- `apps/keda/namespace.yaml`, `apps/keda/README.md` — *create*: KEDA install runbook + namespace.
- `apps/pageindex-mcp/worker-scaledobject.yaml` — *create*: KEDA `ScaledObject` (min 1 / max 2).
- `apps/pageindex-mcp/worker-deployment.yaml` — *modify*: drop the static `replicas: 1` line (KEDA owns replica count) and document why.
- `apps/pageindex-mcp/configmap.yaml` — *modify*: add the three gate/scrape env defaults.

**Key facts already verified (do not re-derive):**
- `/metrics` is served by the **server** process (`server.py` mounts `Route("/metrics", metrics_response)` on the Starlette app behind `pageindex-mcp.pageindex-mcp.svc:8201`). Prometheus in `infra` already scrapes it (job `pageindex-mcp`). So a gauge set in the server process is scraped with **no Prometheus config change**. The **worker** process runs `arq …WorkerSettings` and serves no HTTP — it must NOT host the gauge.
- `upload_app._get_arq_pool()` returns an `arq` pool in the server process; arq's default queue is the Redis sorted set `arq:queue`.
- Prometheus service: `prometheus.infra.svc.cluster.local:9090`.
- `process_document_job` (worker.py:267) calls `result = await _run_converter_subprocess(local_path)` (worker.py:301) and already has `redis` bound at the top of the function. The gate goes immediately before that call.
- `Settings` (config.py) is a plain annotated class built in `_load_settings()` from `os.environ`. Worker tunables (`MAX_JOBS`, `CHILD_TIMEOUT`) are module-level `os.getenv` constants in `worker.py` — the gate follows that same lighter pattern, NOT the `Settings` class.

---

## Task 1: Verification spike — does the worker pod see node-level `/proc/meminfo`?

The whole gate depends on a container reading **node** `MemAvailable`, not a cgroup-limited view. On cgroup-v2 k3s a container's `/proc/meminfo` normally reflects the host. Confirm before building on it. This task writes no code.

**Files:** none (investigation + decision record).

- [ ] **Step 1: Read the worker pod's view of meminfo and compare to the node**

This is a read-only command against our own app's pod. If running it requires explicit authorization in your environment, ask the user to paste the output of:

```bash
POD=$(kubectl get pod -n pageindex-mcp -l app=pageindex-mcp-worker -o jsonpath='{.items[0].metadata.name}')
echo "--- pod view ---"
kubectl exec -n pageindex-mcp "$POD" -- grep -E 'MemTotal|MemAvailable' /proc/meminfo
echo "--- node view ---"
kubectl get --raw "/api/v1/nodes/$(kubectl get node -o jsonpath='{.items[0].metadata.name}')/proxy/stats/summary" 2>/dev/null | grep -m1 availableBytes || free -m
```

Expected: pod `MemTotal` ≈ node total (~7.6Gi), and pod `MemAvailable` tracks the node, not 2500Mi (the cgroup limit).

- [ ] **Step 2: Record the decision**

If the pod sees node-level meminfo → proceed to Task 2 unchanged.
If the pod sees a cgroup-limited `MemAvailable` (≈ the 2500Mi limit) → STOP. The `/proc/meminfo` signal is wrong for this purpose. Implement the gate against the **Redis reservation-counter fallback** in Appendix A instead of `read_meminfo_available_bytes()`. The rest of the tasks (lock, wait loop, wiring, KEDA) are unchanged — only the "how much is available" reader swaps.

- [ ] **Step 3: Commit the finding**

```bash
# Append the result to the spec's Risks section so the decision is durable.
git add docs/superpowers/specs/2026-06-13-worker-concurrency-autoscaling-design.md
git commit -m "docs: record /proc/meminfo container-view verification for admission gate"
```

---

## Task 2: Add the `pageindex_arq_queue_depth` gauge

**Files:**
- Modify: `src/pageindex_mcp/metrics.py` (after the `ACTIVE_UPLOADS` block, ~line 49)
- Test: `tests/test_metrics.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_metrics.py`:

```python
def test_arq_queue_depth_gauge_exposed():
    # Arrange
    from pageindex_mcp.metrics import ARQ_QUEUE_DEPTH, REGISTRY
    from prometheus_client import generate_latest

    # Act
    ARQ_QUEUE_DEPTH.set(3)
    text = generate_latest(REGISTRY).decode()

    # Assert
    assert "pageindex_arq_queue_depth" in text
    assert "pageindex_arq_queue_depth 3.0" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_metrics.py::test_arq_queue_depth_gauge_exposed -v`
Expected: FAIL with `ImportError: cannot import name 'ARQ_QUEUE_DEPTH'`.

- [ ] **Step 3: Add the gauge**

In `src/pageindex_mcp/metrics.py`, immediately after the `ACTIVE_UPLOADS = Gauge(...)` block:

```python
ARQ_QUEUE_DEPTH = Gauge(
    "pageindex_arq_queue_depth",
    "Number of jobs waiting in the arq queue (ZCARD arq:queue); drives KEDA autoscaling",
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_metrics.py::test_arq_queue_depth_gauge_exposed -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pageindex_mcp/metrics.py tests/test_metrics.py
git commit -m "feat: add pageindex_arq_queue_depth gauge for autoscaling"
```

---

## Task 3: Queue-depth scrape loop + wire into the server

**Files:**
- Create: `src/pageindex_mcp/queue_metrics.py`
- Modify: `src/pageindex_mcp/server.py`
- Test: `tests/test_queue_metrics.py`

- [ ] **Step 1: Write the failing test for `read_queue_depth`**

Create `tests/test_queue_metrics.py`:

```python
import asyncio

import fakeredis.aioredis
import pytest

from pageindex_mcp import queue_metrics
from pageindex_mcp.metrics import ARQ_QUEUE_DEPTH


async def test_read_queue_depth_counts_arq_queue():
    # Arrange
    redis = fakeredis.aioredis.FakeRedis()
    await redis.zadd("arq:queue", {"job-a": 1.0, "job-b": 2.0})

    # Act
    depth = await queue_metrics.read_queue_depth(redis)

    # Assert
    assert depth == 2


async def test_read_queue_depth_zero_when_empty():
    redis = fakeredis.aioredis.FakeRedis()
    assert await queue_metrics.read_queue_depth(redis) == 0


async def test_scrape_loop_sets_gauge_then_stops():
    # Arrange
    redis = fakeredis.aioredis.FakeRedis()
    await redis.zadd("arq:queue", {"job-a": 1.0})

    # Act: run one tick then cancel
    task = asyncio.create_task(queue_metrics.queue_depth_scrape_loop(redis, interval=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Assert
    assert ARQ_QUEUE_DEPTH._value.get() == 1.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_queue_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pageindex_mcp.queue_metrics'`.

- [ ] **Step 3: Implement `queue_metrics.py`**

Create `src/pageindex_mcp/queue_metrics.py`:

```python
"""Publish the arq queue depth as a Prometheus gauge for KEDA autoscaling.

Runs inside the server process (the one Prometheus scrapes at /metrics). The
worker process serves no HTTP, so the gauge cannot live there.
"""

from __future__ import annotations

import asyncio
import logging
import os

from redis.asyncio import Redis

from .metrics import ARQ_QUEUE_DEPTH

logger = logging.getLogger(__name__)

# arq's default queue is a Redis sorted set under this key.
_ARQ_QUEUE_KEY = "arq:queue"

SCRAPE_INTERVAL_S = float(os.getenv("ARQ_QUEUE_DEPTH_SCRAPE_INTERVAL_S", "5"))


async def read_queue_depth(redis: Redis) -> int:
    """Return the number of jobs currently waiting in the arq queue."""
    return int(await redis.zcard(_ARQ_QUEUE_KEY))


async def queue_depth_scrape_loop(redis: Redis, interval: float = SCRAPE_INTERVAL_S) -> None:
    """Periodically refresh ARQ_QUEUE_DEPTH. Cancel to stop. Never crashes the loop."""
    while True:
        try:
            ARQ_QUEUE_DEPTH.set(await read_queue_depth(redis))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a scrape blip must not kill the loop
            logger.warning("queue-depth scrape failed; will retry", exc_info=True)
        await asyncio.sleep(interval)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_queue_metrics.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Write the failing test for the server lifespan wiring**

Add to `tests/test_queue_metrics.py`:

```python
async def test_server_lifespan_starts_and_stops_scrape_task(monkeypatch):
    # Arrange
    started = asyncio.Event()
    stopped = {"cancelled": False}

    async def fake_loop(redis, interval=0.01):
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            stopped["cancelled"] = True
            raise

    monkeypatch.setattr(queue_metrics, "queue_depth_scrape_loop", fake_loop)

    from pageindex_mcp.server import _lifespan_with_scrape

    class _DummyApp:
        pass

    # Act: enter then exit the composed lifespan
    async with _lifespan_with_scrape(_DummyApp(), _inner=None):
        await asyncio.wait_for(started.wait(), timeout=1)

    # Assert: task was cancelled on shutdown
    assert stopped["cancelled"] is True
```

- [ ] **Step 6: Run to verify it fails**

Run: `pytest tests/test_queue_metrics.py::test_server_lifespan_starts_and_stops_scrape_task -v`
Expected: FAIL with `ImportError: cannot import name '_lifespan_with_scrape'`.

- [ ] **Step 7: Wire the lifespan in `server.py`**

In `src/pageindex_mcp/server.py`, add imports near the top:

```python
import contextlib

from redis.asyncio import from_url as redis_from_url

from . import queue_metrics
```

Replace the app-composition tail (the block that currently ends with `app = starlette_app`) with a lifespan that wraps the existing one and owns the scrape task:

```python
starlette_app = mcp.http_app(transport="streamable-http")
starlette_app.add_middleware(BearerAuthMiddleware)
starlette_app.routes.insert(0, Route("/metrics", metrics_response))
starlette_app.mount("/upload", create_upload_app())

# Preserve FastMCP's own lifespan (session manager) and additionally run the
# arq queue-depth scrape loop for the lifetime of the server process.
_inner_lifespan = starlette_app.router.lifespan_context


@contextlib.asynccontextmanager
async def _lifespan_with_scrape(app, _inner=_inner_lifespan):
    redis = redis_from_url(settings.redis_url, decode_responses=True)
    scrape_task = asyncio.create_task(queue_metrics.queue_depth_scrape_loop(redis))
    try:
        if _inner is None:
            yield
        else:
            async with _inner(app):
                yield
    finally:
        scrape_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scrape_task
        await redis.aclose()


starlette_app.router.lifespan_context = _lifespan_with_scrape

# This is what gunicorn imports:
app = starlette_app
```

Add `import asyncio` at the top if not already present.

- [ ] **Step 8: Run to verify it passes**

Run: `pytest tests/test_queue_metrics.py -v`
Expected: PASS (4 tests).

- [ ] **Step 9: Run the broader suite to confirm no import/lifespan regressions**

Run: `pytest tests/test_upload.py tests/test_upload_contract.py tests/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/pageindex_mcp/queue_metrics.py src/pageindex_mcp/server.py tests/test_queue_metrics.py
git commit -m "feat: publish arq queue depth gauge from server process"
```

---

## Task 4: Memory-admission gate — the meminfo reader and decision

**Files:**
- Create: `src/pageindex_mcp/memory_admission.py`
- Test: `tests/test_memory_admission.py`

- [ ] **Step 1: Write the failing tests for the reader + threshold**

Create `tests/test_memory_admission.py`:

```python
import pytest

from pageindex_mcp import memory_admission as ma

_MEMINFO_SAMPLE = (
    "MemTotal:        7937224 kB\n"
    "MemFree:          200000 kB\n"
    "MemAvailable:    2500000 kB\n"
    "Buffers:           10000 kB\n"
)


def test_parse_meminfo_available_bytes(tmp_path):
    # Arrange
    p = tmp_path / "meminfo"
    p.write_text(_MEMINFO_SAMPLE)

    # Act
    avail = ma.read_meminfo_available_bytes(path=str(p))

    # Assert: 2500000 kB -> bytes
    assert avail == 2500000 * 1024


def test_read_meminfo_fails_open_returns_none_when_unreadable(tmp_path):
    # Arrange: nonexistent path
    missing = tmp_path / "nope"

    # Act
    avail = ma.read_meminfo_available_bytes(path=str(missing))

    # Assert: unreadable -> None signals "fail open" to the caller
    assert avail is None


def test_has_headroom_true_above_floor():
    assert ma._has_headroom(3_000_000_000, floor=2_300_000_000) is True


def test_has_headroom_false_below_floor():
    assert ma._has_headroom(1_000_000_000, floor=2_300_000_000) is False


def test_has_headroom_fails_open_when_available_is_none():
    # None (unreadable meminfo) must be treated as "proceed" — never worse than today.
    assert ma._has_headroom(None, floor=2_300_000_000) is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_memory_admission.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pageindex_mcp.memory_admission'`.

- [ ] **Step 3: Implement the reader + threshold in `memory_admission.py`**

Create `src/pageindex_mcp/memory_admission.py`:

```python
"""Cross-pod memory-admission gate for document conversion.

Before a worker starts a Docling conversion (peak ~1.9Gi), it waits until the
node reports enough free memory for one job plus margin. With up to 2 worker
pods (arq max_jobs=1 each), this serializes heavy jobs and parallelizes light
ones — without a static per-pod cap that could OOM the 7.6Gi node.

Every failure path FAILS OPEN (proceeds), so behavior is never worse than the
single-worker baseline that exists today.
"""

from __future__ import annotations

import asyncio
import logging
import os

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# One ~1.9Gi job + margin. Default ≈ 2.2Gi.
MEM_ADMISSION_FLOOR_BYTES = int(os.getenv("MEM_ADMISSION_FLOOR_BYTES", str(2_300_000_000)))
# Hard cap on how long a job waits before proceeding anyway (fail-open).
MEM_ADMISSION_MAX_WAIT_S = float(os.getenv("MEM_ADMISSION_MAX_WAIT_S", "120"))
# Backoff between re-checks while waiting.
MEM_ADMISSION_POLL_S = float(os.getenv("MEM_ADMISSION_POLL_S", "3"))

# Short Redis lock so two pods don't both pass the check against the same free
# memory in the same instant. TTL auto-releases if a holder dies.
_ADMISSION_LOCK_KEY = "pageindex:admission"
_ADMISSION_LOCK_TTL_S = 5


def read_meminfo_available_bytes(path: str = "/proc/meminfo") -> int | None:
    """Return node MemAvailable in bytes, or None if unreadable (caller fails open)."""
    try:
        with open(path, encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    # Format: "MemAvailable:    2500000 kB"
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        logger.warning("Could not read MemAvailable from %s; failing open", path, exc_info=True)
        return None
    return None


def _has_headroom(available_bytes: int | None, floor: int = MEM_ADMISSION_FLOOR_BYTES) -> bool:
    """True if it's safe to start a job. Unreadable (None) -> True (fail open)."""
    if available_bytes is None:
        return True
    return available_bytes >= floor
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_memory_admission.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/pageindex_mcp/memory_admission.py tests/test_memory_admission.py
git commit -m "feat: add memory-admission meminfo reader and headroom check"
```

---

## Task 5: Memory-admission gate — the lock + bounded wait loop

**Files:**
- Modify: `src/pageindex_mcp/memory_admission.py`
- Test: `tests/test_memory_admission.py`

- [ ] **Step 1: Write the failing tests for `wait_for_memory`**

Add to `tests/test_memory_admission.py`:

```python
import fakeredis.aioredis

from pageindex_mcp import memory_admission as ma


async def test_wait_for_memory_proceeds_immediately_when_headroom(monkeypatch):
    # Arrange
    redis = fakeredis.aioredis.FakeRedis()
    monkeypatch.setattr(ma, "read_meminfo_available_bytes", lambda path="/proc/meminfo": 3_000_000_000)

    # Act
    waited = await ma.wait_for_memory(redis)

    # Assert: proceeded, no meaningful wait
    assert waited is True


async def test_wait_for_memory_waits_then_proceeds_when_memory_frees(monkeypatch):
    # Arrange: first reads are below floor, then jumps above
    reads = iter([1_000_000_000, 1_000_000_000, 3_000_000_000])
    monkeypatch.setattr(ma, "read_meminfo_available_bytes", lambda path="/proc/meminfo": next(reads, 3_000_000_000))
    monkeypatch.setattr(ma, "MEM_ADMISSION_POLL_S", 0.01)
    redis = fakeredis.aioredis.FakeRedis()

    # Act
    waited = await ma.wait_for_memory(redis)

    # Assert
    assert waited is True


async def test_wait_for_memory_fails_open_after_max_wait(monkeypatch):
    # Arrange: always below floor; cap is tiny
    monkeypatch.setattr(ma, "read_meminfo_available_bytes", lambda path="/proc/meminfo": 1_000_000_000)
    monkeypatch.setattr(ma, "MEM_ADMISSION_POLL_S", 0.01)
    monkeypatch.setattr(ma, "MEM_ADMISSION_MAX_WAIT_S", 0.05)
    redis = fakeredis.aioredis.FakeRedis()

    # Act
    waited = await ma.wait_for_memory(redis)

    # Assert: proceeded anyway (job is never stuck forever)
    assert waited is False


async def test_wait_for_memory_fails_open_on_redis_error(monkeypatch):
    # Arrange: a redis whose set() raises
    class _BrokenRedis:
        async def set(self, *a, **k):
            raise RuntimeError("redis down")

        async def delete(self, *a, **k):
            raise RuntimeError("redis down")

    monkeypatch.setattr(ma, "read_meminfo_available_bytes", lambda path="/proc/meminfo": 3_000_000_000)

    # Act
    waited = await ma.wait_for_memory(_BrokenRedis())

    # Assert: lock failure must not crash; proceed
    assert waited is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_memory_admission.py -k wait_for_memory -v`
Expected: FAIL with `AttributeError: module 'pageindex_mcp.memory_admission' has no attribute 'wait_for_memory'`.

- [ ] **Step 3: Implement `wait_for_memory`**

Append to `src/pageindex_mcp/memory_admission.py`:

```python
async def _try_acquire_lock(redis: Redis) -> bool:
    """Best-effort short lock. Any error -> treat as acquired (fail open)."""
    try:
        return bool(await redis.set(_ADMISSION_LOCK_KEY, "1", nx=True, ex=_ADMISSION_LOCK_TTL_S))
    except Exception:  # noqa: BLE001
        logger.warning("admission lock acquire failed; proceeding", exc_info=True)
        return True


async def _release_lock(redis: Redis) -> None:
    try:
        await redis.delete(_ADMISSION_LOCK_KEY)
    except Exception:  # noqa: BLE001
        logger.debug("admission lock release failed (TTL will reclaim)", exc_info=True)


async def wait_for_memory(redis: Redis) -> bool:
    """Block until the node has headroom for one conversion, or the wait cap elapses.

    Returns True if it proceeded because headroom was available, False if it
    proceeded because the wait cap was hit (fail-open). Never raises for an
    expected operational error — the caller always proceeds afterwards.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + MEM_ADMISSION_MAX_WAIT_S

    while True:
        got_lock = await _try_acquire_lock(redis)
        try:
            available = read_meminfo_available_bytes()
            if _has_headroom(available):
                return True
        finally:
            if got_lock:
                await _release_lock(redis)

        if loop.time() >= deadline:
            logger.warning(
                "admission wait cap (%.0fs) hit; proceeding without confirmed headroom",
                MEM_ADMISSION_MAX_WAIT_S,
            )
            return False

        await asyncio.sleep(MEM_ADMISSION_POLL_S)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_memory_admission.py -v`
Expected: PASS (9 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/pageindex_mcp/memory_admission.py tests/test_memory_admission.py
git commit -m "feat: add bounded memory-admission wait loop with redis lock"
```

---

## Task 6: Wire the gate into `process_document_job`

**Files:**
- Modify: `src/pageindex_mcp/worker.py` (add import; insert call before `_run_converter_subprocess` at ~line 301)
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_worker.py` (imports as needed at top of file):

```python
async def test_process_document_job_awaits_memory_gate_before_subprocess(monkeypatch):
    """The admission gate must run before the converter subprocess is spawned."""
    import pageindex_mcp.worker as worker

    calls = []

    async def fake_wait_for_memory(redis):
        calls.append("gate")
        return True

    async def fake_subprocess(path):
        calls.append("subprocess")
        return {"doc_id": "doc-123"}

    # Arrange
    monkeypatch.setattr(worker, "wait_for_memory", fake_wait_for_memory)
    monkeypatch.setattr(worker, "_run_converter_subprocess", fake_subprocess)
    monkeypatch.setattr(worker, "download_staging", lambda key, path: None)
    monkeypatch.setattr(worker, "delete_staging", lambda key: None)

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    ctx = {"redis": redis, "job_try": 1}

    # Act
    doc_id = await worker.process_document_job(
        ctx, "uploads/staging/job-1/file.pdf", "job-1"
    )

    # Assert: gate ran, and it ran before the subprocess
    assert doc_id == "doc-123"
    assert calls == ["gate", "subprocess"]
```

(If `fakeredis` / `download_staging` patching patterns already exist in `test_worker.py`, mirror those instead of the lines above.)

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_worker.py::test_process_document_job_awaits_memory_gate_before_subprocess -v`
Expected: FAIL — either `AttributeError: ... has no attribute 'wait_for_memory'` or `calls == ['subprocess']` (gate not invoked).

- [ ] **Step 3: Add the import and the gate call**

In `src/pageindex_mcp/worker.py`, add to the imports:

```python
from .memory_admission import wait_for_memory
```

Then in `process_document_job`, locate:

```python
        try:
            result = await _run_converter_subprocess(local_path)
```

and insert the gate immediately before the `try:`:

```python
        # Memory-admission gate: with up to 2 worker pods, wait until the node
        # has headroom for one ~1.9Gi conversion before spawning the child.
        # Fails open (proceeds) on any error or after the wait cap.
        await wait_for_memory(redis)
        try:
            result = await _run_converter_subprocess(local_path)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_worker.py::test_process_document_job_awaits_memory_gate_before_subprocess -v`
Expected: PASS.

- [ ] **Step 5: Run the full worker suite for regressions**

Run: `pytest tests/test_worker.py tests/test_worker_contract.py tests/test_worker_resiliency.py tests/test_worker_subprocess.py -v`
Expected: PASS (existing tests unaffected — the gate is awaited and returns immediately when patched/headroom present).

- [ ] **Step 6: Commit**

```bash
git add src/pageindex_mcp/worker.py tests/test_worker.py
git commit -m "feat: gate document conversion on node memory headroom"
```

---

## Task 7: Coverage check + env defaults in the ConfigMap

**Files:**
- Modify: `hetzner-deployment-service/apps/pageindex-mcp/configmap.yaml`

- [ ] **Step 1: Verify coverage of the new modules ≥ 80%**

Run: `pytest tests/test_memory_admission.py tests/test_queue_metrics.py --cov=pageindex_mcp.memory_admission --cov=pageindex_mcp.queue_metrics --cov-report=term-missing`
Expected: both modules ≥ 80%. If a branch is uncovered, add a focused test before proceeding.

- [ ] **Step 2: Add the tunables to the ConfigMap (documented defaults)**

In `apps/pageindex-mcp/configmap.yaml`, under the `data:` map, add:

```yaml
  # --- Worker concurrency / memory-admission (see worker-scaledobject.yaml) ---
  # Node MemAvailable floor (bytes) required before a worker starts a conversion.
  # ~2.2Gi = one ~1.9Gi Docling job + margin on the 7.6Gi node.
  MEM_ADMISSION_FLOOR_BYTES: "2300000000"
  # Hard cap (s) a job waits for headroom before proceeding anyway (fail-open).
  MEM_ADMISSION_MAX_WAIT_S: "120"
  # Re-check cadence (s) while waiting for headroom.
  MEM_ADMISSION_POLL_S: "3"
  # How often the server publishes pageindex_arq_queue_depth (s).
  ARQ_QUEUE_DEPTH_SCRAPE_INTERVAL_S: "5"
```

These flow into both the server pod (`ARQ_QUEUE_DEPTH_SCRAPE_INTERVAL_S`) and worker pod (`MEM_ADMISSION_*`) — both already `envFrom: configMapRef: pageindex-mcp-config`.

- [ ] **Step 3: Commit (GitOps repo)**

```bash
cd /root/hetzner-deployment-service
git add apps/pageindex-mcp/configmap.yaml
git commit -m "chore: add worker memory-admission and scrape env defaults"
```

---

## Task 8: Install KEDA

**Files:**
- Create: `hetzner-deployment-service/apps/keda/namespace.yaml`
- Create: `hetzner-deployment-service/apps/keda/README.md`

KEDA is a cluster-scoped operator installed via Helm; it is not a plain manifest. This task records the install so it is reproducible.

- [ ] **Step 1: Create the namespace manifest**

Create `apps/keda/namespace.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: keda
```

- [ ] **Step 2: Create the install runbook**

Create `apps/keda/README.md`:

```markdown
# KEDA

Event-driven autoscaler. Used by `apps/pageindex-mcp/worker-scaledobject.yaml`
to scale the PageIndex worker 1↔2 on arq queue depth.

## Install (one-time, cluster-scoped)

    kubectl apply -f apps/keda/namespace.yaml
    helm repo add kedacore https://kedacore.github.io/charts
    helm repo update
    helm install keda kedacore/keda --namespace keda --version 2.x

## Verify

    kubectl get pods -n keda
    kubectl get crd | grep keda.sh   # scaledobjects.keda.sh must exist

## Uninstall

    helm uninstall keda -n keda
```

- [ ] **Step 3: Run the install**

Run the commands from the README. (If your environment requires the user to run cluster-mutating commands, hand these to the user via a `!` command and wait for confirmation.)
Expected: `kubectl get crd | grep keda.sh` lists `scaledobjects.keda.sh`; KEDA operator pod is `Running`.

- [ ] **Step 4: Commit**

```bash
cd /root/hetzner-deployment-service
git add apps/keda/namespace.yaml apps/keda/README.md
git commit -m "feat: add KEDA install runbook and namespace"
```

---

## Task 9: ScaledObject for the worker (1 ↔ 2)

**Files:**
- Create: `hetzner-deployment-service/apps/pageindex-mcp/worker-scaledobject.yaml`
- Modify: `hetzner-deployment-service/apps/pageindex-mcp/worker-deployment.yaml`

- [ ] **Step 1: Create the ScaledObject**

Create `apps/pageindex-mcp/worker-scaledobject.yaml`:

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: pageindex-mcp-worker
  namespace: pageindex-mcp
spec:
  scaleTargetRef:
    name: pageindex-mcp-worker
  minReplicaCount: 1
  maxReplicaCount: 2
  # Slow scale-down so a brief queue dip doesn't kill the 2nd pod mid-batch.
  cooldownPeriod: 120
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleDown:
          stabilizationWindowSeconds: 120
  triggers:
    - type: prometheus
      metricType: AverageValue
      metadata:
        serverAddress: http://prometheus.infra.svc.cluster.local:9090
        # threshold=1 → desiredReplicas = ceil(queue_depth / 1), capped at
        # maxReplicaCount=2. So the 2nd pod is added once ≥2 jobs are waiting.
        threshold: "1"
        query: pageindex_arq_queue_depth
```

- [ ] **Step 2: Remove the static replica count from the worker Deployment**

In `apps/pageindex-mcp/worker-deployment.yaml`, delete the line `replicas: 1` under `spec:` and add a comment in its place so a future reader knows KEDA owns it:

```yaml
spec:
  # replicas is owned by the KEDA ScaledObject (worker-scaledobject.yaml): 1↔2.
  strategy:
```

(Leaving `replicas: 1` is harmless on first apply but KEDA then manages it; removing it avoids GitOps fighting the HPA on every reconcile.)

- [ ] **Step 3: Apply both**

Run:
```bash
kubectl apply -f apps/pageindex-mcp/worker-deployment.yaml
kubectl apply -f apps/pageindex-mcp/worker-scaledobject.yaml
kubectl get scaledobject -n pageindex-mcp
kubectl get hpa -n pageindex-mcp   # KEDA creates a managed HPA
```
Expected: `ScaledObject` `READY=True`, `ACTIVE` reflects queue state; a `keda-hpa-pageindex-mcp-worker` HPA exists.

- [ ] **Step 4: Commit**

```bash
cd /root/hetzner-deployment-service
git add apps/pageindex-mcp/worker-scaledobject.yaml apps/pageindex-mcp/worker-deployment.yaml
git commit -m "feat: autoscale pageindex worker 1-2 via KEDA on arq queue depth"
```

---

## Task 10: In-cluster validation

**Files:** none (validation).

- [ ] **Step 1: Confirm the gauge is scraped**

Run:
```bash
kubectl exec -n infra deploy/prometheus -- \
  wget -qO- 'http://localhost:9090/api/v1/query?query=pageindex_arq_queue_depth'
```
Expected: a JSON result with a value for `pageindex_arq_queue_depth` (0 when idle). If the series is missing, confirm the server pod restarted with the new image and `/metrics` shows the gauge.

- [ ] **Step 2: Validate scale-up on queue depth**

Upload ≥3 documents in one batch (so ≥2 sit queued while 1 runs), then:
```bash
watch -n2 'kubectl get pods -n pageindex-mcp -l app=pageindex-mcp-worker'
```
Expected: a 2nd worker pod appears within ~1 HPA cycle while queue depth ≥ 2; it disappears after the queue drains + `cooldownPeriod`.

- [ ] **Step 3: Validate memory serialization (no OOM)**

While 2 pods are up and processing table-dense PDFs, watch node memory:
```bash
kubectl top nodes
kubectl get events -n pageindex-mcp --field-selector reason=Evicted
kubectl exec -n pageindex-mcp deploy/pageindex-mcp-worker -- \
  sh -c 'cat /sys/fs/cgroup/memory.events 2>/dev/null | grep oom || echo no-oom'
```
Expected: no `Evicted` events, no `oom_kill` increment. Under genuine pressure the 2nd job logs `admission wait` and serializes rather than OOMing. Compare against the floor in the worker logs:
```bash
kubectl logs -n pageindex-mcp -l app=pageindex-mcp-worker --tail=200 | grep -i admission
```

- [ ] **Step 4: Record results**

Note observed peak node memory with 2 concurrent light jobs vs. the serialized heavy case in the spec/PR description. If 2 light jobs already approach the floor, raise `MEM_ADMISSION_FLOOR_BYTES` via the ConfigMap and re-roll the worker.

---

## Appendix A: Redis reservation-counter fallback (only if Task 1 fails)

If the worker pod does NOT see node-level `/proc/meminfo`, replace the *availability reader* only — the lock, wait loop, wiring, and KEDA tasks are unchanged. Swap `read_meminfo_available_bytes()` for a budget-vs-reservations model:

- Config: `NODE_MEM_BUDGET_BYTES` (e.g. node total minus a reserved baseline, ~5.0Gi), `JOB_MEM_RESERVATION_BYTES` (~1.9Gi).
- A Redis integer key `pageindex:mem_reserved`. `wait_for_memory` becomes: under the lock, `reserved = INCRBY(JOB_MEM_RESERVATION_BYTES)`; if `reserved <= NODE_MEM_BUDGET_BYTES` proceed, else `DECRBY` back and wait/retry. The job must `DECRBY` its reservation in a `finally` after the subprocess returns (add the release in `process_document_job`'s `finally`).
- `_has_headroom` is then `reserved_after_increment <= budget`. Tests mirror Task 5 but assert on the counter rather than a mocked meminfo value. Reservations are deterministic and cross-pod, so they don't depend on the container's meminfo view.

This is the spec's documented fallback (Risk #1); implement it ONLY if Task 1's verification shows the cgroup-limited view.

---

## Self-Review Notes

- **Spec coverage:** Unit 1 (gauge + scrape loop = Tasks 2–3; ScaledObject + KEDA = Tasks 8–9). Unit 2 (meminfo reader + headroom = Task 4; lock + bounded wait + fail-open = Task 5; wiring before `_run_converter_subprocess` = Task 6). Config env vars = Task 7. Error handling / fail-open = covered by tests in Tasks 4–5. Testing ≥80% = Task 7 Step 1. Risk #1 (/proc view) = Task 1 + Appendix A. Risk #3 (flapping) = ScaledObject cooldown/stabilization. Risk #4 (double-heavy fail-open) = `wait_for_memory` returns False + log; arq retry recovers a killed job (existing behavior).
- **Type consistency:** `wait_for_memory(redis) -> bool`, `read_meminfo_available_bytes(path=...) -> int | None`, `_has_headroom(available, floor) -> bool`, `read_queue_depth(redis) -> int`, `queue_depth_scrape_loop(redis, interval)`, `_lifespan_with_scrape(app, _inner)` — names used identically across tasks.
- **No placeholders:** every code/command step is concrete.
