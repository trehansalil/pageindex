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
| `MAX_JOBS` (default 1, ceiling 4) | `worker.py`, `resolve_max_jobs()` | per worker process |
| `MEM_ADMISSION_FLOOR_BYTES` ≈ 2.2 GiB | `memory_admission.py:22`, gate at `worker.py:372-375` | cross-process, Redis lock `pageindex:admission` |

Multiple worker **processes** are already safe and already deployed — KEDA
scales replicas 1↔2 (`hetzner-deployment-service/apps/pageindex-mcp/worker-scaledobject.yaml`).
Cross-process safety comes from arq's `unique=True` cron dedup (`worker.py:689-691`)
and the admission lock. No per-document locking serializes distinct documents.

## 4. The opportunity: the memory floor is stale for remote Docling

`_run_converter_subprocess` (`worker.py:219-249`) always spawns the converter
child. But when `DOCLING_SERVICE_URL` is set, `client.py:762-790` takes the
`_remote_pdf_to_markdown` branch and **never invokes the local converter**, so
the lazy Docling/PyTorch import at `converters.py:1058` is never reached.

The child's peak RSS on that path is dominated by PyMuPDF text extraction and
tree building — materially below the ~1.9 GiB figure baked into
`MEM_ADMISSION_FLOOR_BYTES` and the k8s pod-memory comments. Nothing in the code
distinguishes the two routes for admission purposes.

Remote Docling is the current default (`PI_DOCLING=remote`), so the deployment
is being throttled by a number derived for a path it no longer takes.

## 5. Recommended order

1. Add `--file` (repeatable) — cheap, unblocks arbitrary cross-folder sets.
2. Make the admission floor route-aware: a lower floor when
   `DOCLING_SERVICE_URL` is set. Measure the remote-path child's actual peak RSS
   first — do not guess.
3. Raise `maxReplicaCount` in the KEDA ScaledObject, rather than raising
   `MAX_JOBS`. That preserves the existing horizontal-scaling design; raising
   `MAX_JOBS` would let two heavy children run inside one pod with no
   intra-pod admission control.

Steps 2 and 3 change production memory behaviour and need an explicit decision.
