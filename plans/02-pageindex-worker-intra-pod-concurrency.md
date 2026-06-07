# Plan 02 — Intra-pod concurrency for PageIndex worker (`max_jobs > 1`)

**Status:** STUB. Do not execute until plan 01 (subprocess isolation) is merged and a real scaling pressure exists that horizontal replicas can't satisfy.

**Prerequisite:** plan 01 phases 1–6 complete and Phase 5 PR soaked 24 h clean.

**When to revisit:** Redis queue depth grows faster than `replicas × (cold_start + median_convert_time)` and horizontal scaling has hit a node-count or cost ceiling. Until then, scale by replica count with `max_jobs=1`.

---

## Goal

Allow a single arq worker pod to process `N > 1` PDF conversions concurrently using `N` parallel subprocesses, while keeping the parent at its post-plan-01 ~250 MiB baseline and preserving per-job observability.

## Why this is a separate plan

Plan 01's subprocess model assumes `max_jobs=1`. Three things break when concurrency rises:

1. **Per-pod memory budget changes shape.** Steady-state floor stays at parent's ~250 MiB, but peak rises to `~250 MiB + N × 1.9 GiB`. Pod `resources.limits.memory` must be raised in lockstep with `max_jobs`.
2. **`getrusage(RUSAGE_CHILDREN).ru_maxrss` stops being per-job.** It aggregates the max across **all** waited-on children since the parent started, so under concurrency the Prometheus gauge from plan 01 Phase 3 silently becomes "max child since worker boot" instead of "this job's peak."
3. **Tmpfile collision risk** if any code path uses a fixed tmp path.

## Phase 0 — Re-verify after plan 01

- [ ] Confirm `RUSAGE_CHILDREN` aggregation behavior on the production kernel (write a 5-line script that spawns 2 children with different RSS and reads the field).
- [ ] Confirm `psutil>=5.9` is acceptable as a new dependency, or use direct `/proc/<pid>/status` parsing.
- [ ] Re-read plan 01's `_run_converter_subprocess` (Phase 3) to lock the exact integration point.

## Phase 1 — Per-child RSS sampling (TDD)

Replace the single `getrusage` call with a sampling loop scoped to one child PID:

- Background task per subprocess: every 250 ms read `/proc/<child_pid>/status` `VmRSS` line (or `psutil.Process(pid).memory_info().rss`), track max in a local variable; stop when the child exits.
- Metric becomes a histogram (`pageindex_converter_child_peak_rss_kib`) labelled by `outcome={done,oom,timeout,child_failed}` instead of a gauge.
- Tests: monkeypatch the sampler with a generator yielding rising RSS values; assert the recorded peak equals the max seen before exit.

## Phase 2 — Unique tmp namespace per job

- Replace any fixed/predictable tmp path with `tempfile.mkdtemp(prefix=f"pageindex-{job_id}-")`.
- CLI signature from plan 01 already takes input/output paths from the parent, so the change is parent-side only.
- Test: run two `_run_converter_subprocess` coroutines concurrently in fakeredis-backed test; assert distinct tmp dirs and no clobbering.

## Phase 3 — `max_jobs` knob + pod sizing formula

- Promote `MAX_JOBS = 1` from a hard-coded constant to an env-driven setting (`PAGEINDEX_MAX_JOBS`, default 1). Document the formula: `pod_memory_limit_gib = 0.25 + max_jobs * 2.0` (rounded up to nearest 0.5 Gi).
- WorkerSettings reads the env at import time.
- Tests: `WorkerSettings.max_jobs` reflects env override.

## Phase 4 — Load test

- docker-compose with `PAGEINDEX_MAX_JOBS=2`, pod-equivalent limit 4.5 Gi.
- Push 10 PDFs in quick succession. Confirm: two children alive simultaneously, parent stays ≤ 350 MiB, per-child peaks each ≤ 2 GiB, no `ResourceWarning`, all jobs reach terminal status.
- Compare wall-clock throughput vs the same load on 2 × `max_jobs=1` replicas. Pick whichever wins on `throughput / memory_budget`.

## Phase 5 — k8s manifest update (only if Phase 4 wins)

- In `apps/pageindex-mcp/worker-deployment.yaml`: set `env.PAGEINDEX_MAX_JOBS` and bump `resources.limits.memory` per the formula above.
- HPA: queue-depth target unchanged; node fit may force fewer replicas at higher per-pod size — verify on a single 8 GiB node that you can still schedule what you need.

## Open questions to answer in Phase 0

- Does `arq` serialize cron jobs against the `max_jobs` pool? (Plan 01 Phase 0 noted "cron jobs count against the same slot pool" — confirm this doesn't starve the reaper when `max_jobs=2` and both slots are busy with 5-min Docling runs.)
- Worth a connection-pool cap on `httpx`/Azure-OpenAI clients used by PageIndex if those run in the parent under B-wide? Unknown until plan 01 Phase 1 picks the boundary.

## Anti-patterns to ban (carry from plan 01)

All plan 01 Phase 0 bans still apply. Additionally:
- No background-thread RSS samplers — use `asyncio.create_task` so cancellation works.
- No silent fallback to `RUSAGE_CHILDREN` if `/proc/<pid>/status` reads fail — fail loudly so the metric stays trustworthy.
