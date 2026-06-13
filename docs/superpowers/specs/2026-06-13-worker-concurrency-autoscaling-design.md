# PageIndex Worker — Conditional 2-Pod Autoscaling

**Date:** 2026-06-13
**Status:** Approved (design)
**Repos touched:** `pageindex_deployment` (worker code + metric), `hetzner-deployment-service` (KEDA + manifests)

## Goal

Process posted document batches across **up to 2 worker pods** when the node has
memory headroom, while keeping the Docling extraction pipeline **byte-for-byte
unchanged** and guaranteeing **no OOM / eviction** on the current node.

Non-goal: 3-way concurrency, and any change to extraction quality. Both were
explicitly ruled out (see Decisions).

## Background & hard constraint

- Single k3s node `portfolio`: **7.6Gi total**, ~2.4Gi steady-state available
  (excluding the transient interactive session measured during design).
- Each Docling job peaks **~1.9Gi** worst case (table-dense), ~0.9Gi light
  (measured; recorded in prior OOM investigation).
- Worker today: **1 replica, arq `max_jobs=1`** → exactly one job at a time.
  Pod limit 2500Mi / request 512Mi (Burstable).
- **The wall:** two worst-case jobs = ~3.8Gi simultaneous > available. Per-pod
  cgroup limits do NOT manufacture physical RAM; the failure mode is node-level
  pressure eviction (Burstable pods evicted first). Reclaiming the hr-chatbot
  cotenant frees only ~0.27Gi (its actual usage) — not load-bearing.

Therefore the 2nd concurrent slot must be **conditional on real headroom**, not
guaranteed. On light batches this yields true 2× throughput; on heavy batches it
safely serializes.

## Architecture

Two independent units, each testable in isolation:

### Unit 1 — Autoscaler (infra)
- **KEDA** (cluster-scoped operator) installed via Helm into a `keda` namespace.
- A **queue-depth gauge** `pageindex_arq_queue_depth` published by `upload_app`
  (it already holds the arq pool and already exposes a Prometheus endpoint) via a
  periodic `ZCARD arq:queue`.
- A **`ScaledObject`** targeting the `pageindex-mcp-worker` Deployment:
  - `minReplicas: 1`, `maxReplicas: 2`
  - Trigger: KEDA **Prometheus scaler** querying `pageindex_arq_queue_depth`,
    scale the 2nd pod up at depth ≥ 2, scale down when it drains
    (with KEDA's default cooldown to avoid flapping).

### Unit 2 — Memory-admission gate (app code)
- Each pod keeps arq **`max_jobs=1`** → 2 pods = at most 2 concurrent jobs.
- New gate invoked in `process_document_job` **immediately before**
  `_run_converter_subprocess`:
  1. Acquire a short Redis lock (`pageindex:admission`) so two pods don't both
     pass the check on the same free memory (TTL ~ a few seconds, auto-released).
  2. Read node **`MemAvailable`** from `/proc/meminfo`.
  3. If `MemAvailable >= MEM_ADMISSION_FLOOR_BYTES` (default ~2.2Gi: one ~1.9Gi
     job + margin), release lock and proceed.
  4. Else release lock, sleep a bounded backoff, and re-check (up to a cap).
     The job is **not failed** — it simply waits its turn, then runs.
- **Self-regulating:** while the other pod runs a heavy job, `MemAvailable` is
  depressed → this pod waits. While the other runs a light job, `MemAvailable`
  stays high → this pod proceeds in parallel.

### Data flow
```
upload endpoint --enqueue--> arq:queue (Redis sorted set)
        |                          |
        |                    ZCARD (periodic) --> pageindex_arq_queue_depth gauge
        |                          |
        |                    Prometheus <-- KEDA Prometheus scaler --> ScaledObject
        |                          |                                       |
        |                          |                              scale worker 1<->2
        v                          v
   worker pod(s): process_document_job
        -> [admission gate: lock + /proc/meminfo MemAvailable >= FLOOR?]
             yes -> _run_converter_subprocess (child, Docling, ~1.9Gi peak)
             no  -> bounded wait, re-check
```

## Configuration (env)
- `MEM_ADMISSION_FLOOR_BYTES` (default ≈ 2.2Gi) — gate threshold.
- `MEM_ADMISSION_MAX_WAIT_S` / backoff — bounds how long a job waits before it
  proceeds anyway (fail-open after cap so a job is never stuck forever; the
  per-pod 2500Mi cgroup limit remains the last-resort containment).
- `ARQ_QUEUE_DEPTH_SCRAPE_INTERVAL_S` — gauge refresh cadence.
- Worker Deployment: `replicas` managed by KEDA; resources unchanged
  (limit 2500Mi / request 512Mi).

## Error handling
- Gate lock acquisition failure → treat as "not safe", wait/retry (never crash).
- `/proc/meminfo` unreadable/unexpected → log once, **fail-open** to current
  behavior (proceed) so we never regress below today's working single-job flow.
- KEDA/Prometheus unavailable → Deployment stays at `minReplicas: 1`; system
  degrades to exactly today's behavior. No data loss.

## Testing (TDD, ≥80%)
- **Unit:** gate decision logic — MemAvailable above/below floor; meminfo parse;
  lock held vs free; fail-open on unreadable meminfo; wait-cap fail-open.
- **Unit:** queue-depth gauge — ZCARD result maps to gauge value; scrape loop.
- **Integration:** two queued jobs serialize under simulated low MemAvailable and
  parallelize under high (monkeypatch the meminfo reader); arq `max_jobs=1`
  honored per pod.
- **In-cluster:** `ScaledObject` scales 1→2 on synthetic queue depth and back.

## Risks
1. **`/proc/meminfo` view in-container.** On k3s a container typically sees node
   meminfo; must verify at plan time. If a pod sees a cgroup-limited view, the
   gate signal is wrong → fallback: a Redis in-flight reservation counter
   (each job reserves worst-case ~1.9Gi against a configured node budget).
2. **KEDA operator dependency** — new cluster-scoped component; widely used,
   acceptable.
3. **Flapping** between 1 and 2 replicas — mitigated by KEDA cooldown / stabilization.
4. **Gate fail-open after wait cap** could, in a pathological double-heavy batch,
   allow two heavy jobs → reverts to the known OOM risk for that rare case; arq
   retry recovers the killed job. Tunable via the wait cap.

## Decisions (from brainstorming)
- Keep Docling config unchanged (declined pymupdf4llm flip) — quality preserved.
- Cap concurrency at **2**, not 3.
- Conditional 2nd slot via **live `/proc/meminfo` MemAvailable** gate.
- Autoscaler: **KEDA + Prometheus scaler** (not HPA/adapter, not fixed 2 replicas).
- hr-chatbot left running (~0.27Gi reclaim not worth degrading a prod service).
