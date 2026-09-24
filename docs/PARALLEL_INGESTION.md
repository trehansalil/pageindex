# File-by-file parallel ingestion — what exists, what limits it

Findings from a two-agent parallel analysis of `scripts/remote_ingest_test.py`,
`src/pageindex_mcp/worker.py` and `src/pageindex_mcp/upload_app.py`. Every claim
is anchored to a file:line.

## Short answer

Per-file parallelism **already exists end to end** on the client. Explicit
per-file *paths* do not — you can only point at one directory or one MinIO
prefix. Adding them is small. But neither change buys throughput on its own,
because the worker executes **one job at a time**.

## 1. The client is already fully per-file

`run_pipeline` (`scripts/remote_ingest_test.py:688-708`) builds one `DocResult`
per document, throttles with a single `asyncio.Semaphore(concurrency)`, and
launches one coroutine per document through `asyncio.gather`.

- one `POST /upload/files` per file — `submit_one:589-606`
- independent polling per job, with its own backoff and deadline — `poll_one:623-685`
- `--concurrency` (default 2) — `:87`, `:922`

Nothing in this path inspects a document's parent directory. `SourceDoc`
(`:518-531`) carries `name`, `origin`, `size_bytes` and a `read()` callable, so
a list assembled from several unrelated folders would flow through
`run_pipeline` / `submit_one` / `poll_one` **unchanged**.

## 2. What is missing: explicit paths

`build_parser` (`:891-948`) offers `--dir` (a single `Path`, not repeatable) and
`--prefix` (a single MinIO prefix). `--include` / `--exclude` are globs applied
*within* that one root — they cannot reach across folders.

`discover_local` (`:533-548`) is single-directory and non-recursive.

Adding a repeatable `--file` needs three small pieces:

1. `discover_paths(paths: list[Path]) -> list[SourceDoc]` — ~15 lines mirroring
   the per-file body of `discover_local`.
2. `src.add_argument("--file", action="append", type=Path, default=[], …)`.
3. A branch in `main()` (`:981-987`), which currently assumes exactly one source.

No changes to `submit_one`, `poll_one`, `run_pipeline`, `DocResult`, the
semaphore, the retry logic, or the server handler.

One soft edge: two files with the same basename in different folders produce
ambiguous rows in the report table, which is keyed on `name`.

## 3. The real ceiling: `MAX_JOBS` (default 1)

`src/pageindex_mcp/worker.py`, `MAX_JOBS` — defaults to 1 and is applied as
`WorkerSettings.max_jobs`. It is **no longer hardcoded**: `resolve_max_jobs()`
reads `PAGEINDEX_WORKER_MAX_JOBS` and clamps it to `[1, MAX_JOBS_CEILING]`
(ceiling 4), so an unset, invalid, or absurd value still lands on a safe number
rather than crashing startup or stacking enough jobs to OOM the worker.

The reason for the default of 1 is peak RSS: a local Docling index can peak at
multiple GiB, and stacking two would risk an OOM kill on a memory-tight node.
That is a *memory* safeguard, not an arq or I/O limitation — which is exactly
why the override is safe against **remote** Docling, where conversion happens
off-box and the worker is I/O-bound. See
[ENV_PROFILES.md](ENV_PROFILES.md#variables-the-toggles-do-not-set) before
raising it; do not raise it against `DOCLING=local`.

Symbol names rather than line numbers throughout this section: the earlier
`:68`/`:688` citations were already stale by the time the override shipped.

Two independent gates exist:

| Control | Where | Scope |
|---|---|---|
| `MAX_JOBS` (default 1, or 2 when `config.docling_offload_configured()` is true and the env var is unset; ceiling 4) | `worker/lifecycle.py`, `resolve_max_jobs()` | per worker process |
| `MEM_ADMISSION_FLOOR_BYTES` ≈ 2.2 GiB, or `MEM_ADMISSION_FLOOR_SERVICE_BYTES` = 800 MiB when `config.docling_offload_configured()` is true | `memory_admission.py`, gate in `wait_for_memory()` | cross-process, Redis lock `pageindex:admission` |

Multiple worker **processes** are already safe and already deployed — KEDA
scales replicas 1↔2 (`hetzner-deployment-service/apps/pageindex-mcp/worker-scaledobject.yaml`).
Cross-process safety comes from arq's `unique=True` cron dedup (`worker.py:689-691`)
and the admission lock. No per-document locking serializes distinct documents.

## 4. RFC-050 D1/D2: the admission floor and MAX_JOBS are now route-aware

`_run_converter_subprocess` (`worker.py:219-249`) always spawns the converter
child. But when `DOCLING_SERVICE_URL` is set (the in-cluster Docling service
pod — `services/docling-service`, not the retired Scaleway remote endpoint),
`client.py`'s `_remote_pdf_to_markdown` branch **never invokes the local
converter**, so the lazy Docling/PyTorch import at `converters.py:1058` is
never reached. The child's peak RSS on that path is dominated by PyMuPDF text
extraction and tree building — materially below the ~1.9 GiB figure baked
into `MEM_ADMISSION_FLOOR_BYTES`.

As of RFC-050 D1/D2 this is no longer stale: `memory_admission.py` uses
`MEM_ADMISSION_FLOOR_SERVICE_BYTES` (800 MiB default) instead of
`MEM_ADMISSION_FLOOR_BYTES`, and `resolve_max_jobs()` (`worker/lifecycle.py`)
defaults `MAX_JOBS` to 2 instead of 1, whenever
`config.docling_offload_configured()` is true — **corrected post-review,
2026-09-24**: this is `DOCLING_SERVICE_URL` set *and* `docling` importable
(the indexer's docling converter entry exists), not `DOCLING_SERVICE_URL`
alone, since a set-but-unusable URL should not silently raise concurrency. An
explicit `PAGEINDEX_WORKER_MAX_JOBS` still wins, and the worker warns at
startup when `DOCLING_SERVICE_URL` is set but offload isn't configured.
Per-upload staging is orthogonal to this and stays present for every arq job
regardless.

The gate also now factors in the pod's cgroup memory limit (v2/v1), taking
`min(host MemAvailable, cgroup headroom)` rather than only the host-wide
reading. **Corrected post-review, 2026-09-24:** `cgroup headroom` is
`limit − working_set`, where `working_set = current − inactive_file` (v2
reads `inactive_file` from `memory.stat`; v1 reads `total_inactive_file` from
the same file) — mirroring kubelet's own headroom calculation rather than
subtracting raw `memory.current`/`usage_in_bytes`, which would overcount
reclaimable page cache as pressure. It falls back to the raw-usage
subtraction when the stat can't be parsed — see
[ENV_PROFILES.md](ENV_PROFILES.md#pageindex_worker_max_jobs--only-raise-it-when-docling-is-offloaded).

## 5. Recommended order

1. Add `--file` (repeatable) — cheap, unblocks arbitrary cross-folder sets.
2. ~~Make the admission floor route-aware~~ — done (RFC-050 D1/D2, §4 above).
   Still open: measure the in-cluster-service-path child's actual peak RSS
   first — do not guess.
3. Raise `maxReplicaCount` in the KEDA ScaledObject, rather than raising
   `MAX_JOBS`. That preserves the existing horizontal-scaling design; raising
   `MAX_JOBS` would let two heavy children run inside one pod with no
   intra-pod admission control.

Steps 2 and 3 change production memory behaviour and need an explicit decision.

## Deployment sizing (RFC-050)

The in-cluster docling-service pod moves Docling's RSS off the worker, not off
the node: both land on the same single k3s node (`portfolio`, 7.6 GB RAM,
allocatable 7,937,228 Ki ≈ 7.57 GiB), next to redis, postgres and minio.

| Pod | Replicas | Memory request | Memory limit | Concurrency knob |
|---|---|---|---|---|
| `docling-service` (`services/docling-service`, uvicorn `--workers 1`, :8080) | 1 | ~2.5Gi | ~3.5Gi | one conversion at a time; ~2 GB peak RSS per its README |
| `pageindex-mcp-worker` | KEDA 1↔2 (`maxReplicaCount: 2`) | ~1Gi | ~1.5Gi | `PAGEINDEX_WORKER_MAX_JOBS=2` — the default once `config.docling_offload_configured()` is true (§4) |
| worker admission floor | — | — | — | `MEM_ADMISSION_FLOOR_SERVICE_BYTES` = 800 MiB (838860800) |

Fit check against the node's scheduled requests on 2026-09-24 (2,904 Mi,
including today's 512 Mi worker request): replacing that with 2 × 1 Gi
workers and adding 2.5 Gi for docling-service gives ≈ 7,000 Mi of requests,
≈ 90 % of allocatable. A second KEDA replica still schedules, but with little
room left, so anything else added to the node can leave it `Pending`. Limits
are overcommitted (already 154 % today) — so the cgroup-aware admission gate
(§4) is what holds a pod inside its limit, not the scheduler.

Two figures are **assumptions, not measurements**. The 1.5 Gi worker limit
assumes the service-path child peaks well under the 1.9–3.1 GiB local-Docling
child — measure it (§5 step 2) before relying on `MAX_JOBS=2` inside that
limit. The G1 run should watch total node memory, not only worker RSS
(RFC-050 Risk 4). Read stage timings from worker logs with
`make g1-timings LOGS="baseline=a.log baseline=b.log post=c.log post=d.log"`
(`scripts/g1_stage_timings.py`). The Prometheus scrape is deferred to Phase 3.

The manifests are **not in this repo**: they live in the separate
`hetzner-deployment-service` repo (`apps/pageindex-mcp/…`, see §3). The change
is on its branch `feature/pageindex-docling-service`: a `docling-service`
Deployment + Service, the configmap fix, worker resources, and a
`docling-service-image-updated` deploy route that
`.github/workflows/build-push-docling-service.yml` here dispatches after
publishing `ghcr.io/trehansalil/docling-service`. Merge it only **after** the
G1 baseline arm is recorded: anything on that repo's `main` goes live on the
next image dispatch. The earlier sketch,
[infra/hetzner-deployment-service-rfc050.patch](infra/hetzner-deployment-service-rfc050.patch),
is kept for the sizing rationale. As of 2026-09-24
the live `pageindex-mcp-config` still sets `PAGEINDEX_WORKER_MAX_JOBS="10"`.
That explicit value wins over the D2 default, and the ceiling clamps it to 4.
It also still points `DOCLING_SERVICE_URL` at Scaleway. The patch corrects
both.
