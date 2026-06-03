# PageIndex MCP — Worker Deploy Rollout-Timeout Diagnosis

**Date:** 2026-06-03
**Symptom:** GitHub Actions "Deploy to Hetzner" job fails at step *"Update image tag (rolling deploy) — pageindex-mcp"* with:

```
deployment.apps/pageindex-mcp image updated
deployment.apps/pageindex-mcp-worker image updated
Waiting for deployment "pageindex-mcp" rollout to finish: 0 of 1 updated replicas are available...
deployment "pageindex-mcp" successfully rolled out
Waiting for deployment "pageindex-mcp-worker" rollout to finish: 0 of 1 updated replicas are available...
error: timed out waiting for the condition
Error: Process completed with exit code 1.
```

**Repos involved:**
- App: `/Users/saliltrehan/Documents/Python_n_R/Personal/pageindex` (FastMCP + arq worker, Python 3.12)
- Deploy: `/Users/saliltrehan/Documents/Python_n_R/Personal/hetzner-deployment-service` (k8s manifests under `apps/pageindex-mcp/`, workflow `.github/workflows/deploy.yml`)

---

## TL;DR — Root Cause (CONFIRMED by cluster snapshot)

**The worker's updated pod is `Pending` (unschedulable) and never gets onto a node, so the rollout waits the full 300s and exits 1. It is a capacity / scheduling problem — NOT a container crash, NOT Redis, NOT OOM.**

A cluster snapshot of namespace `pageindex-mcp` showed:

| Pod | Age | IP | Status | Restarts |
|---|---|---|---|---|
| `pageindex-mcp-7c49b644fd-d8dpv` (server) | 11m | 10.42.0.133 | **Succeeded** | 0 |
| `pageindex-mcp-7c49b644fd-nn2xh` (server, new) | 119s | — | **Pending** | 0 |
| `pageindex-mcp-worker-7b997cbfdf-… ` (worker, new) | 117s | — | **Pending** | 0 |
| `pageindex-mcp-worker-7b997cbfdf-… ` (worker) | 10m | 10.42.0.139 | **Succeeded** | 0 |
| `pod-cleanup-…` (CronJob) | 3m | — | Succeeded | 0 |

Key reads:
- **`RESTARTS = 0` everywhere** → nothing is crash-looping. (A crash would show climbing restarts + `CrashLoopBackOff`/`OOMKilled`.)
- **New pods are `Pending` with no IP** → never scheduled to a node → their containers never started → cannot be crashing on Redis or OOMing.
- **Pod CIDR `10.42.0.0/24`** → single-node **k3s** cluster hosting all apps.

---

## Why the worker fails the rollout but the server doesn't

The decisive asymmetry is the **rollout strategy**, not anything in the code:

| | Server `pageindex-mcp` | Worker `pageindex-mcp-worker` |
|---|---|---|
| Command | `gunicorn … server:app` | `arq pageindex_mcp.worker.WorkerSettings` |
| Strategy | `maxSurge:0, maxUnavailable:1` | `maxSurge:1, maxUnavailable:0` |
| Rollout behavior | Terminates old pod **first**, frees its resource request, then schedules new one in the freed slot → fits on a tight node | Must schedule a **2nd** worker pod *before* retiring the old one → needs room for two at once → on a full node the surge pod stays `Pending` → timeout |
| Readiness probe | `tcpSocket:8201` | **none** (so a stuck pod = blind 300s timeout) |

This explains the original failure (server rolled out, worker timed out) far better than the earlier crash/Redis theory.

---

## Likely capacity cause (needs one `describe` to confirm)

Single k3s node hosts three apps. Summed **requests** (the scheduler bin-packs on *requests*, not limits), from the manifests:

| App | CPU req | Mem req |
|---|---|---|
| airline-hr-chatbot (6 workloads) | 550m | 640Mi |
| neonatal-care (5 workloads, incl. its own Redis) | 650m | 1472Mi |
| pageindex server + worker | 400m | 512Mi |
| **App subtotal** | **1.6 vCPU** | **~2.56 GiB** |
| + k3s system pods (traefik/coredns/metrics) | ~0.25 vCPU | ~0.4 GiB |
| **≈ committed** | **~1.85 vCPU** | **~3 GiB** |

On a **2 vCPU / 4 GB** Hetzner node (allocatable ≈ 1.9 vCPU after overhead), the worker's surge pod (+200m / +256Mi) tips CPU (and nearly memory) **past allocatable** → `FailedScheduling: Insufficient cpu/memory` → `Pending`.

The old pods sitting in terminal **`Succeeded`** phase (abnormal for a Deployment, which forces `restartPolicy: Always`) strongly suggests the **node restarted / drained / hit node-level pressure ~11 min before the snapshot** — which is also why *both* new pods (server + worker) were `Pending` at once, vs. only the worker in the original failure.

---

## Confirm on the live cluster

> The Hetzner k3s cluster is reachable only via the CI `KUBECONFIG_B64` secret — not from the local kubeconfig (local contexts are docker-desktop / minikube / ihe-demo-wise-k8s, none of which is the target).

```bash
# 1. THE answer — why the new pods won't schedule:
kubectl -n pageindex-mcp describe pod pageindex-mcp-worker-7b997cbfdf-<id> | sed -n '/Events:/,$p'
#   expect: FailedScheduling  "0/1 nodes are available: 1 Insufficient cpu/memory"
#       or: "had untolerated taint {node.kubernetes.io/memory-pressure|disk-pressure}"

# 2. Node capacity & health:
kubectl describe node | grep -A12 'Allocated resources'      # requests vs allocatable
kubectl describe node | grep -A6 'Conditions\|Taints'        # MemoryPressure/DiskPressure/Ready, taints
kubectl top nodes                                            # actual usage

# 3. Why the old pods are "Succeeded" (node shutdown? evicted?):
kubectl -n pageindex-mcp describe pod pageindex-mcp-7c49b644fd-d8dpv | sed -n '/Status:/,/Events:/p'
kubectl -n pageindex-mcp get events --sort-by=.lastTimestamp | tail -40
```

---

## Fix

1. **Surgical (fixes the worker rollout timeout):** in `apps/pageindex-mcp/worker-deployment.yaml`, set the worker strategy to match the server so the rollout frees the old slot before scheduling the new pod:
   ```yaml
   spec:
     strategy:
       type: RollingUpdate
       rollingUpdate:
         maxSurge: 0
         maxUnavailable: 1
   ```
2. **Legibility:** add an exec readiness/startup probe so a stuck worker shows a clear status instead of a blind 300s timeout (arq has no HTTP port):
   ```yaml
   startupProbe:
     exec: { command: ["sh","-c","pgrep -f 'pageindex_mcp.worker.WorkerSettings' >/dev/null"] }
     initialDelaySeconds: 10
     periodSeconds: 5
     failureThreshold: 30
   ```
3. **Root (if genuinely over-committed):** scale up the Hetzner node, or trim requests across the three apps.

### Correction to an earlier (superseded) recommendation
An earlier diagnosis suggested raising the worker **memory limit to ~3Gi** for Docling. On a ~4 GB single node that is **dangerous**:
- Do **not** raise the worker memory **request** — it makes scheduling (the actual current failure) worse.
- Do **not** uncap the limit toward 3Gi on this node — a Docling job spiking ~3 GB alongside neonatal-care + airline can trigger a **node-level OOM/reboot** (a candidate for the `Succeeded` terminal pods).
- The Docling worker realistically needs a node **materially larger** than the one currently shared by three apps.

---

## Ruled out / corrected

- **arq Redis-connect crash at boot** — refuted: `RESTARTS=0` and `Pending` (container never ran). Also `neonatal-care-redis` *is* deployed (confirmed in `apps/neonatal-care/`), so the cross-namespace `REDIS_URL=redis://neonatal-care-redis.neonatal-care:6379/1` target exists.
- **OOMKill (512Mi limit vs torch/Docling)** — not the rollout cause: all torch/docling imports are function-local (no module-level heavy import), so an idle worker fits 512Mi. It remains a **real job-time risk** once scheduling is fixed and a real PDF runs — but separate from this timeout.
- **`b7f8f2c` refactor import crash** — refuted: the server imports the same `client.py` / `converters.py` / `helpers.py` and booted fine.

---

## Open items

- [ ] Run the `describe pod` / `describe node` commands above to confirm `Insufficient cpu` vs `Insufficient memory` vs a pressure taint.
- [ ] Apply worker strategy `maxSurge:0/maxUnavailable:1`.
- [ ] Decide node sizing — required before the Docling worker can process real PDFs without OOM risk.
- [ ] (Optional) add the exec readiness probe for legible failures.
