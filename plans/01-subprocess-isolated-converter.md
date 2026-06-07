# Plan 01 — Subprocess-isolated Docling conversion

## Decision (Phase 1)

### RSS measurements (Linux KiB / MiB)

| Probe | RSS (KiB) | RSS (MiB) |
|---|---|---|
| Baseline (`python -c "import resource; print(...)"`) | 42,636 | 41.6 |
| After `from pageindex_mcp.converters import pdf_to_markdown_docling` | 42,636 | 41.6 |
| After `from pageindex_mcp.client import CustomPageIndexClient` | 197,196 | 192.6 |

Derived deltas:
- Docling-only delta: **0 KiB / 0 MiB** (Docling is lazy-imported inside `pdf_to_markdown_docling`; importing the function symbol costs nothing at import time)
- Client import delta: **154,560 KiB / 150.9 MiB** (dominated by `pageindex.PageIndexClient` → `litellm` → ~104 MiB, plus `openai`, `httpx`, `pydantic` stack)
- Docling as % of client delta: **0 %**

### Chosen boundary: B-wide

The decision rule says "if Docling-only import ≥ 80 % of the client-import delta, go B-narrow; else B-wide." Docling contributes **0 %** of the delta, so the rule clearly dictates **B-wide**.

The culprit is the `PageIndexClient` parent class imported at `client.py` line 14 (`from pageindex import PageIndexClient`) and the `from .converters import pdf_markdown_converters` at line 18, which together drag in `litellm` (103.9 MiB alone) plus `openai`, `httpx`, and `pydantic` on every parent-process import. `pdf_to_markdown_docling` itself is not imported at module top-level — it is returned lazily by `pdf_markdown_converters()` at call time. By contrast, if the parent imports `CustomPageIndexClient` it pays the ~151 MiB penalty before any job arrives; that immediately violates the ≤ 350 MiB steady-state target once Docling model weights (~1.9 GiB) also load in the child and some allocator memory leaks back. With B-wide the parent never imports `CustomPageIndexClient` at all — it only touches Redis/MinIO/staging — so it stays at the baseline ~42 MiB plus asyncio/arq overhead.

### CLI entry point for Phase 2

**B-wide** — the subprocess must run the entire `CustomPageIndexClient.index()` call.

**Contract reconciliation (Phase 2 pre-work):** The original Phase 2 section of this plan assumed a B-narrow shape with `<input_pdf_path> <output_md_path>` — writing markdown to a file. Reading `src/pageindex_mcp/client.py` and `src/pageindex_mcp/worker.py` lines 45-117 confirms that `CustomPageIndexClient.index(local_path)` returns a plain `str` (the 8-char `doc_id`) and **persists the processed document to MinIO itself** via `save_doc`, `save_doc_meta`, and `save_raw`. There is no markdown output file to write — MinIO is the output. The call site in `worker.py` is simply `doc_id = await client.index(local_path)`. Therefore the B-wide CLI contract needs no output path argument; the `doc_id` is returned in the stdout JSON and MinIO persistence is handled inside `client.index()`.

Reconciled CLI contract:

```
python -m pageindex_mcp.converters_cli <input_pdf_path>
```

- No output file argument. `client.index()` handles all persistence (MinIO).
- Stdout: exactly one JSON line at exit:
  - success: `{"ok": true, "doc_id": "...", "peak_rss_kib": <int>, "duration_ms": <int>}`
  - failure: `{"ok": false, "error": "<ExceptionClassName>", "message": "..."}`
- Exit code: 0 on success, 1 on handled exception, signal-default on crash.
- Reads `DOCLING_NUM_THREADS`, `DOCLING_DO_OCR`, `DOCLING_ARTIFACTS_PATH` from env (inherited).
- Does NOT import `redis`, `arq`, `pageindex_mcp.worker`, or `pageindex_mcp.cache`.

The CLI module (`src/pageindex_mcp/converters_cli.py`) will instantiate `CustomPageIndexClient`, call `await client.index(input_pdf_path)`, and emit the `doc_id` as part of the stdout JSON line. It must **not** import `redis`, `arq`, or `pageindex_mcp.worker`.

---

**Goal:** keep the arq parent worker at ~250 MiB resident indefinitely by running every PDF conversion in a fresh child process. When the child exits, the kernel reclaims Docling model weights, PyTorch caches, and glibc arenas — no in-process residual.

**Source of truth for "why":** prior session observations #128 (peak ~1.9 GiB, thread caps don't help), #130 (max_jobs=1 + reaper already shipped), #120 (worker idles at 301 MiB → ~1.5 GiB after a job today).

**Non-goals:** changing Docling pipeline settings, changing the Redis status schema, changing k8s manifests beyond a memory-limit reduction at the end.

---

## Phase 0 — Verified facts (do not re-derive in implementation phases)

### Code surface (from /root/pageindex_deployment)

| Item | Location | Exact value / signature |
|---|---|---|
| arq handler | `src/pageindex_mcp/worker.py:45-117` | `async def process_document_job(ctx: dict, staging_key: str, job_id: str) -> str` |
| Constants | `src/pageindex_mcp/worker.py:27-39` | `JOB_TTL=86_400`, `JOB_TIMEOUT=900`, `MAX_JOBS=1`, `REAP_GRACE=120`, `DLQ_KEY="pageindex:dlq"` |
| Cron reaper | `src/pageindex_mcp/worker.py:119-169` | `async def reap_stale_jobs(ctx: dict) -> None`; cutoff = `JOB_TIMEOUT + REAP_GRACE` (1020 s) |
| WorkerSettings | `src/pageindex_mcp/worker.py:179-196` | `functions=[process_document_job]`, `max_jobs=1`, `job_timeout=900`, `cron_jobs=[cron(reap_stale_jobs, second=0, run_at_startup=True, unique=True, max_tries=1, timeout=30)]` |
| Docling entry | `src/pageindex_mcp/converters.py:409-499` | **Synchronous**: `def pdf_to_markdown_docling(pdf_path: str) -> str` |
| Pipeline opts | `src/pageindex_mcp/converters.py:360-407` | `_build_pdf_pipeline_options()`; `num_threads` from `DOCLING_NUM_THREADS` (default 1); `TableFormerMode.ACCURATE`; CPU |
| Status helpers | `src/pageindex_mcp/cache.py:30-42` | `await job_status_set(job_id, mapping)`, `await job_status_get(job_id)`; key format `pageindex:job:{job_id}`; TTL `JOB_TTL` |
| Call site of Docling | Inside `CustomPageIndexClient.index(local_path)` — invoked from `process_document_job`, **not** directly | The subprocess boundary should wrap this whole call (see Phase 1 spike) |

Hash fields written by worker: `status` (`processing`/`done`/`error`), `processing_started_at` (epoch str, line 71), `doc_id`, `error`, `reason`, `reaped_at`.

### Test surface

- `pytest-asyncio` with `asyncio_mode = "auto"`.
- `tests/test_worker.py` — uses `AsyncMock()` for redis; patches `CustomPageIndexClient`, `download_staging`, `delete_staging`, `shutil`. Does **not** load Docling.
- `tests/test_worker_resiliency.py` — uses `fakeredis.aioredis.FakeRedis(decode_responses=True)`; imports `JOB_TIMEOUT`, `REAP_GRACE`, `WorkerSettings`, `process_document_job`, `reap_stale_jobs`.
- `tests/test_converters_footprint.py` — imports `_build_pdf_pipeline_options` and Docling enums; pure introspection, no model load.

### Allowed APIs (cited)

| Need | API | Source |
|---|---|---|
| Spawn child | `asyncio.create_subprocess_exec(program, *args, stdin, stdout, stderr, **kwds)` | https://docs.python.org/3/library/asyncio-subprocess.html#asyncio.create_subprocess_exec |
| Read PIPE safely | `await proc.communicate()` (never naked `proc.stdout.read()`/`wait()` with PIPE) | Same page, *Process.wait* note |
| Bounded wait | `async with asyncio.timeout(T): ...` (preferred over `wait_for`) | https://docs.python.org/3/library/asyncio-task.html#asyncio.timeout |
| Process group | pass `start_new_session=True` to `create_subprocess_exec`; **never** `preexec_fn=os.setsid` (deprecated, thread-unsafe) | https://docs.python.org/3/library/subprocess.html#subprocess.Popen |
| Signal the group | `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)` then SIGKILL after grace | https://docs.python.org/3/library/os.html#os.killpg |
| Graceful → forceful | `proc.terminate()` (SIGTERM) → `await asyncio.wait_for(proc.wait(), grace)` → `proc.kill()` (SIGKILL) → `await proc.wait()` | Same asyncio-subprocess page |
| OOM detection | `proc.returncode == -signal.SIGKILL` (i.e. `-9`) — **negative** for signals from `create_subprocess_exec`; shell-style `137` only via `shell=True` | https://docs.python.org/3/library/asyncio-subprocess.html#asyncio.subprocess.Process.returncode |
| Child peak RSS | `resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss` after `await proc.wait()`; **Linux units = KiB**, multiply by 1024 for bytes | https://docs.python.org/3/library/resource.html#resource.getrusage + getrusage(2) man |
| arq cancellation | `task.cancel()` raises `CancelledError` in the handler at `job_timeout`; the handler must wrap subprocess await in `try/except (asyncio.CancelledError, TimeoutError):` and run kill in `finally` | https://arq-docs.helpmanual.io/#retrying-jobs-and-cancellation (shutdown documented; `job_timeout` inferred from same mechanism) |

### Anti-patterns banned

- `preexec_fn=os.setsid` — deprecated; thread-unsafe.
- `shell=True` — breaks signal returncode convention (turns -9 into 137) and is a security hazard.
- `os.fork()` directly — corrupts the asyncio loop / selectors.
- `await proc.wait()` while PIPEs are unread — documented deadlock.
- Relying on `__del__` for child cleanup — emits `ResourceWarning`, leaves zombies.
- Inventing arq hooks not in the docs (`functions`, `cron_jobs`, `on_startup`, `on_shutdown`, `on_job_start`, `on_job_end`, `after_job_end` are the documented ones — use only these).

### Open question — resolve in Phase 1 spike, not later

`pdf_to_markdown_docling` is invoked **inside** `CustomPageIndexClient.index(local_path)`, not directly from `process_document_job`. The subprocess boundary therefore has two viable shapes:

- **B-narrow:** subprocess wraps only `pdf_to_markdown_docling`. Parent still loads PageIndex itself.
- **B-wide:** subprocess wraps the whole `CustomPageIndexClient.index()` call. Parent only does Redis/MinIO/staging.

Phase 1 must read `CustomPageIndexClient.index()` and decide. Acceptance: pick whichever keeps the **parent steady-state RSS ≤ 350 MiB** under load. If PageIndex itself loads heavy models (Azure-OpenAI clients are HTTP, so unlikely — but verify), choose B-wide.

---

## Phase 1 — Spike: pick the subprocess boundary

**Goal:** decide between B-narrow and B-wide based on what `CustomPageIndexClient.index()` actually loads. No production code changes.

**Tasks:**
1. Read `src/pageindex_mcp/client.py` end-to-end. Specifically locate `CustomPageIndexClient.index()`; enumerate what it imports at module top-level vs inside the method (heavy ML deps tell you what the parent would otherwise hold).
2. Run a quick RSS probe: `python -c "from pageindex_mcp.client import CustomPageIndexClient; import resource; print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)"` — record RSS just from import.
3. Repeat with `from pageindex_mcp.converters import pdf_to_markdown_docling` — record.
4. Decision rule:
   - If `pdf_to_markdown_docling` import alone is the dominant cost (≥80 % of the delta), go **B-narrow**.
   - Otherwise go **B-wide**.

**Deliverable:** one paragraph at the top of `plans/01-subprocess-isolated-converter.md` ("Decision: B-narrow because …") plus an updated CLI signature for Phase 2.

**Verification:** the chosen boundary is justified by a measured number in the plan, not by assumption.

---

## Phase 2 — CLI entrypoint (TDD)

**Goal:** ship `src/pageindex_mcp/converters_cli.py`, callable as `python -m pageindex_mcp.converters_cli`, that runs the boundary chosen in Phase 1 and exits.

**Contract (CLI):** *(reconciled to B-wide — see Phase 1 Decision)*

```
python -m pageindex_mcp.converters_cli <input_pdf_path>
```

- Reads `<input_pdf_path>` (must exist).
- Runs `CustomPageIndexClient.index()` end-to-end inside the child: conversion, tree build, MinIO persistence. No output file is produced — the persisted artifact is the `doc_id` returned from `client.index()`.
- Emits **exactly one JSON line on stdout** at exit: `{"ok": true, "doc_id": "...", "peak_rss_kib": N, "duration_ms": M}` (success) or `{"ok": false, "error": "<exception class>", "message": "..."}` (failure).
- Exit code: `0` on success, `1` on any handled exception, signal-default on crash (so `-9` on OOM).
- Reads `DOCLING_NUM_THREADS`, `DOCLING_DO_OCR`, `DOCLING_ARTIFACTS_PATH` from env (inherits from parent — no new env contract).
- No imports from worker.py, cache.py, or redis (CLI must be runnable standalone for local debugging).

**RED tests in `tests/test_converters_cli.py`:** *(B-wide — no output file)*
1. Happy path: tiny PDF fixture → CLI prints `{"ok": true, "doc_id": ..., ...}` on stdout, exit 0. Use `subprocess.run([sys.executable, "-m", "pageindex_mcp.converters_cli", in_path], capture_output=True, timeout=120)` with `client.index` monkeypatched in a shim.
2. Missing input: nonexistent input path → exit 1, stdout JSON `{"ok": false, "error": "FileNotFoundError", ...}`.
3. `client.index` raises `RuntimeError("...empty...")` → exit 1, stdout JSON `{"ok": false, "error": "RuntimeError", ...}`.
4. JSON structural test: stdout is exactly one line, parseable, contains `doc_id` (str), `peak_rss_kib` (int ≥ 0) and `duration_ms` (int ≥ 0) on success.
5. Stdout-pollution guard: even when `client.index` emits stray `print()`/log noise, stdout must contain exactly one JSON line.

**Anti-pattern guards:**
- `grep -n "shell=True" tests/test_converters_cli.py src/pageindex_mcp/converters_cli.py` must return nothing.
- The CLI module must not `import` `redis`, `arq`, or `pageindex_mcp.worker` (grep check).
- Stdout must be a single line of JSON — no log spam to stdout; Docling logs go to **stderr**.

**Verification checklist:**
- [ ] 5/5 new tests RED before code, GREEN after.
- [ ] Existing 105-test suite still green; no regressions.
- [ ] `python -m pageindex_mcp.converters_cli` exits cleanly with `--help`-style banner when args missing.

---

## Phase 3 — Parent: replace in-process call with subprocess (TDD)

**Contract reconciliation (Phase 3 pre-work):** The Phase 3 sketch below was written assuming B-narrow (it passes `out_path` and reads markdown from a file post-conversion). The chosen boundary is **B-wide** (see "Decision (Phase 1)" above) and the committed CLI contract from Phase 2 takes only `<input_pdf_path>` and returns `doc_id` in the stdout JSON — MinIO persistence happens inside the child. Therefore `_run_converter_subprocess(pdf_path)` takes only the input path; the parent extracts `doc_id` from the returned JSON dict and uses it directly (no markdown file read, no post-conversion PageIndex call). The Redis hash schema (`status`, `processing_started_at`, `doc_id` on success, `error`/`reason` on failure) is unchanged.

**Goal:** `process_document_job` spawns the CLI as a child, awaits with `JOB_TIMEOUT`, handles every documented exit mode, and writes the same Redis hash fields the in-process version does. **Existing Redis schema is unchanged.**

**Concrete shape (sketch — implementation phase must verify against Phase 0 facts):**

```python
async def _run_converter_subprocess(pdf_path: str) -> dict:
    """Returns parsed stdout JSON; raises ConverterChildError on any failure."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pageindex_mcp.converters_cli", pdf_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,                     # NOT preexec_fn
        env=os.environ.copy(),
    )
    try:
        async with asyncio.timeout(JOB_TIMEOUT):
            stdout, stderr = await proc.communicate()
    except (TimeoutError, asyncio.CancelledError):
        await _kill_group(proc, grace=10)
        raise
    if proc.returncode == 0:
        stdout_lines = stdout.splitlines()
        if not stdout_lines:
            # Defensive: child exited 0 but emitted no JSON line — treat as a
            # handled child failure rather than letting splitlines()[-1] raise
            # IndexError, which would bypass the Redis status write and leave
            # the job stuck at status=processing.
            raise ConverterChildError(0, "child exited 0 but produced no stdout JSON")
        result = json.loads(stdout_lines[-1])
        # Per-job peak from the child's own RUSAGE_SELF (parent's RUSAGE_CHILDREN
        # is a process-lifetime high-water mark and would report stale peaks).
        CONVERTER_PEAK_RSS_KIB.set(int(result.get("peak_rss_kib") or 0))
        return result  # caller pulls doc_id from result["doc_id"]
    if proc.returncode == -signal.SIGKILL:
        raise ConverterOOMError(stderr.decode(errors="replace")[-2000:])
    raise ConverterChildError(proc.returncode, stderr.decode(errors="replace")[-2000:])
```

Where `_kill_group` sends SIGTERM to the process group, awaits up to `grace` seconds, then SIGKILL, then `await proc.wait()`.

**Handler integration in `process_document_job`:**
- Before conversion: write `status=processing` + `processing_started_at` (already done — keep it).
- Call `_run_converter_subprocess(local_path)`; on success read `doc_id` directly from `result["doc_id"]`. No post-conversion read of an output file — the child has already persisted the document via `CustomPageIndexClient.index()` (B-wide).
- On `ConverterOOMError`: set `status=error`, `reason="converter_oom"`, `error=<tail of stderr>`. Re-raise so arq's retry logic + DLQ still apply.
- On `ConverterChildError`: set `status=error`, `reason="converter_child_failed"`, `error=<...>`.
- On `TimeoutError/CancelledError`: set `status=error`, `reason="converter_timeout"`. Re-raise.

**RED tests in `tests/test_worker_resiliency.py` (extend) and `tests/test_worker_subprocess.py` (new):**
1. Happy path: monkeypatch `_run_converter_subprocess` to return a canned success dict; `process_document_job` writes `status=done` to fakeredis and returns `doc_id`.
2. OOM path: monkeypatch to raise `ConverterOOMError`; assert fakeredis hash has `status="error"`, `reason="converter_oom"`, and a non-empty `error` field; assert the exception is re-raised so arq sees the failure.
3. Timeout path: monkeypatch the subprocess wrapper to raise `TimeoutError`; assert `reason="converter_timeout"` and re-raise.
4. Generic child failure: monkeypatch to raise `ConverterChildError(returncode=2, stderr="boom")`; assert `reason="converter_child_failed"` and `error` contains `"boom"`.
5. Cron reaper still works after the change: pre-seed fakeredis with a `processing` hash older than cutoff, run `reap_stale_jobs`, assert flip to `status=error`, `reason="worker_terminated"` (unchanged).
6. **Real subprocess smoke test** (skip-if `DOCLING_INTEGRATION` env not set): spawn the actual CLI against a tiny fixture PDF, confirm `_run_converter_subprocess` returns sensible JSON. Marked `@pytest.mark.integration`; not run in CI by default.

**Prometheus:**
- New gauge `pageindex_converter_child_peak_rss_kib` (set after each subprocess.wait).
- New counter `pageindex_converter_child_oom_total`.
- New counter `pageindex_converter_child_timeout_total`.
- Register in `src/pageindex_mcp/metrics.py` matching existing patterns (read it first; do not invent a `MetricRegistry`).

**Anti-pattern guards (grep):**
- `grep -n "preexec_fn" src/pageindex_mcp/worker.py` → empty.
- `grep -n "shell=True" src/pageindex_mcp/worker.py` → empty.
- `grep -n "os.fork" src/pageindex_mcp/worker.py` → empty.
- `grep -n "proc.wait()" src/pageindex_mcp/worker.py` → only inside the kill helper, never before `communicate()`.

**Verification checklist:**
- [ ] 6/6 new tests pass; 105 existing tests still pass.
- [ ] No `ResourceWarning: subprocess N is still running` in pytest output (run `pytest -W error::ResourceWarning`).
- [ ] `mypy src/pageindex_mcp/worker.py` (if configured) clean.
- [ ] Reaper test from `tests/test_worker_resiliency.py` is unchanged and still passing — the reaper is the backstop, not the primary path now.

---

## Phase 4 — Local validation (manual, no code changes)

**Goal:** prove the parent steady-state RSS stays low across many real conversions.

1. Build the worker image locally (`docker build -t pageindex-worker-local .`).
2. Run with a host-mounted MinIO/Redis (existing `docker-compose.yml`).
3. Push 5 real PDFs through (`AKB.pdf`, `AVB-PHV-Basis.pdf`, 3 others). Use the existing upload flow.
4. Sample parent RSS every 2 s with `ps -o pid,rss,cmd -p <arq_pid>` (or `cat /proc/<pid>/status | grep VmRSS`); record min/max/last across the 5-job run.
5. Acceptance: parent **last RSS ≤ 350 MiB** after 5 conversions (current behavior: ≥ 1500 MiB).
6. Child peak RSS (from getrusage / Prometheus gauge) should match the pre-existing ~1.9 GiB measurement (#128). If radically different, stop and investigate before proceeding.

**Deliverable:** a short results table appended to this plan file under "Phase 4 results".

---

## Phase 5 — Reduce k8s memory limit (cross-repo)

**Only if Phase 4 acceptance is met.**

In `/root/hetzner-deployment-service/apps/pageindex-mcp/worker-deployment.yaml`:
- Set `resources.limits.memory: 2Gi` (down from 2.5Gi).
- Keep `resources.requests.memory` at its current value.
- Commit on a branch `feat/pageindex-worker-mem-2gi`, open PR referencing this plan and Phase 4 measurements.

**Verification:**
- `kubectl rollout status deploy/pageindex-mcp-worker -n pageindex-mcp` after merge.
- 24 h observation window: no OOMKills (`kubectl get events -n pageindex-mcp | grep -i oom` empty).
- `kubectl top pod` shows parent ≤ 400 MiB between jobs.

**Rollback:** revert the limit to 2.5Gi on the same PR if any OOMKill appears within 24 h.

**Scaling note (read before changing replica count or `max_jobs`):**

- **Horizontal scaling (more replicas, `max_jobs=1` each) is the supported path** for this plan. Each pod sized at the 2 Gi limit above. Reaper is replica-safe via `cron(..., unique=True)`. Recommended HPA signal: Redis queue depth via KEDA's `redis-streams` / `redis-list` scaler. No code changes required to add replicas.
- **Vertical scaling within a pod (`max_jobs > 1`) is out of scope for this plan.** It requires: (a) raising the pod memory limit to `~250 MiB + max_jobs × 1.9 GiB`, (b) replacing `getrusage(RUSAGE_CHILDREN).ru_maxrss` with per-child sampling (the rusage value aggregates across all reaped children and stops being per-job under concurrency), (c) per-job unique tmp dirs (`tempfile.mkdtemp(prefix=job_id)`). See `plans/02-pageindex-worker-intra-pod-concurrency.md`.

---

## Phase 6 — Final verification

- [ ] `pytest -q` green; new subprocess + CLI tests included.
- [ ] `pytest -q -m integration` green when `DOCLING_INTEGRATION=1` and a real fixture PDF is present.
- [ ] Grep audit (anti-pattern guards from Phases 2 + 3) all empty.
- [ ] Memory probe results recorded in this plan file (Phase 4).
- [ ] Cross-repo PR merged and 24 h soak clean (Phase 5).
- [ ] Update prior memory observation: parent steady-state floor verified ≤ 350 MiB.

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| arq's `job_timeout` cancellation racing with our `asyncio.timeout` → double-cancel | `try/except (CancelledError, TimeoutError):` covers both; kill in `finally` is idempotent (already terminated → `proc.kill()` no-ops on dead pid). |
| Child stdout exceeds PIPE buffer → deadlock | Use `proc.communicate()`. Docling status JSON is one line; stderr can be larger but `communicate()` drains both. |
| `ru_maxrss` units differ on macOS dev / Linux prod | Linux production is the only target; assume KiB. Document in metric description. |
| `start_new_session=True` not supported on Windows | Production is Linux; CI may run on Linux too. Guard with `sys.platform != "win32"` if devs hit it locally. |
| PageIndex (in `CustomPageIndexClient`) silently loading large weights → B-narrow doesn't help | Phase 1 spike measures this *before* committing. |
| Cron reaper falsely flips a job whose subprocess is mid-conversion if `JOB_TIMEOUT` is hit | Reaper cutoff = `JOB_TIMEOUT + REAP_GRACE` (1020 s). The parent always writes a terminal status before the reaper window — unchanged from today. |
