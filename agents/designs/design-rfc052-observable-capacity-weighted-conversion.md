<!-- Space: CITRA -->
<!-- Title: Design Document: Observable, Page-Class-Aware, Capacity-Weighted Conversion -->
<!-- Folder: Designs -->

---
id: "design-rfc052-observable-capacity-weighted-conversion"
title: "Design: Observable, Page-Class-Aware, Capacity-Weighted Conversion"
type: design
status: draft
date: "2026-09-26"
tags:
  - design
  - performance
  - observability
  - docling
  - scaling
aliases:
  - "design-rfc052-observable-capacity-weighted-conversion"
governs:
  - "[[RFC-052]]"
---

# Design Document: Observable, Page-Class-Aware, Capacity-Weighted Conversion

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [[RFC-052]] |
| Tasks | [[tasks-rfc052-observable-capacity-weighted-conversion]] |
| Architecture Doc | [[ARCHITECTURE]] |

## Topology

```
                      ┌────────────── portfolio (k3s) ───────────────────────────┐
 POST /upload ──► MCP server ──► arq worker ──► converter child                   │
                      │                          │  preclassify (R2)             │
                      │                          │  chunker (R3)                 │
                      │                          │  coordinator (R5) ──┐         │
                      │                          │                     │         │
                      │          docling-service-local (opportunistic) ◄┤ /capacity│
                      │          promtail DS ──► Loki ──► Grafana      │ /convert │
                      └────────────────────────────────────────────────┼──────────┘
                                                                        │
                         active remote (exactly one, chosen by node controller)
                         ├─ Mac (Tailscale, public presigned URL)  — Alloy ─► Loki NodePort (tailscale0 only)
                         └─ docling-1 (cpx62, private net)         — promtail DS (tolerates taint)
```

## Log Pipeline

**Promtail `configmap.yaml` changes** (`/root/hetzner-deployment-service/apps/infra/`):

```yaml
relabel_configs:
  - source_labels: [__meta_kubernetes_pod_container_name]
    target_label: container
  - source_labels: [__meta_kubernetes_pod_name]
    target_label: pod
pipeline_stages:
  - cri: {}
  - drop: { expression: '"(GET|HEAD) /(metrics|health)' }
  - drop: { expression: '^\d{2}:\d{2}:\d{2}: ' }      # arq console duplicates
  - json: { expressions: { level: level, logger: logger, ts: ts, kind: kind,
                           job_id: job_id, doc_id: doc_id, doc_sha8: doc_sha8, run_id: run_id } }
  - timestamp: { source: ts, format: RFC3339Nano, action_on_failure: skip }
  - labels: { level: , kind: }
  - structured_metadata: { job_id: , doc_id: , doc_sha8: , run_id: }
```

Adding the daemonset toleration `{key: dedicated, operator: Equal, value: docling, effect: NoSchedule}` puts a promtail on docling-1. Its memory comes out of docling-1's RAM.

**Mac.** Alloy is installed with `brew install grafana/grafana/alloy` and runs as a launchd agent. Its `loki.source.file` tails `~/docling-service/logs/service.log`, and `loki.write` pushes to `http://100.120.146.20:<nodeport>/loki/api/v1/push`. Rotation uses newsyslog (`/etc/newsyslog.d/docling.conf`, 10 MB × 5).

**Loki reachability.**
- A `loki-tailscale` Service of type NodePort.
- A host iptables rule that ACCEPTs that port on `tailscale0` and DROPs it on every other interface. The public Hetzner firewall does not list the port. This is an operator step; Claude does not mutate hcloud.

**Correlation headers.** The client sends:

```
X-Job-Id, X-Doc-Sha8, X-Run-Id, X-Shard: "<i>/<n>:<start>-<end>"
```

docling-service applies these headers in a middleware through `obs.bind_log_context(...)`, and installs `obs.formatter.JsonFormatter` on the root logger and on uvicorn's loggers. The package is already importable there.

**`docling_chunk` record**, written as `kind=decision` in the `obs` envelope:

```json
{"event":"docling_chunk","job_id":"…","shard":"1/3:0-139","chunk":"4/14",
 "page_start":40,"page_end":49,"backend":"mac","do_table_structure":true,"do_ocr":false,
 "tableformer_mode":"accurate","duration_s":61.2,"peak_rss_bytes":1450000000,"outcome":"ok"}
```

**Dashboard.**
- Variable: `$job_id` (textbox).
- Panels:
  - **Logs:** `{namespace=~"pageindex-mcp|infra"} | job_id="$job_id"` plus `{host="mac"} | json | job_id="$job_id"`;
  - **Chunk timeline:** `… |= "docling_chunk" | json` → table;
  - **Errors:** `| level=~"ERROR|WARNING"`.

## Page Classifier

```python
@dataclass(frozen=True)
class PageClass:
    has_text_layer: bool
    has_images: bool
    has_tables: bool

    @property
    def needs_tables(self) -> bool: return self.has_tables
    @property
    def needs_ocr(self) -> bool:   # UD2
        return (not self.has_text_layer) or self.has_images or self.has_tables
```

**Per page, each signal wrapped in its own `try`.** A failed signal is set to **True** for that page, the safe default, and logged at WARNING.

| Signal | Test |
|---|---|
| Text layer | `len(page.get_text("text").strip()) >= PAGECLASS_TEXT_MIN_CHARS`, then the existing garble screen |
| Images | `sum(area(r) for r in page.get_image_info())` ÷ page area ≥ `PAGECLASS_IMAGE_AREA_MIN` |
| Tables | `page.find_tables()` has ≥ 1 table, **or** ruled lines, **or** strict column alignment; then ±1 neighbour padding |

- **Ruled-line repair:** index tuple items (`kind, *pts` / `rect` as a 4-tuple) instead of attribute access.
- **Strict column alignment:** ≥ 3 aligned x-positions, each with ≥ 4 blocks, and aligned across ≥ 3 rows. The threshold is tuned on the census until the RFC R2 AC3 bound holds.
- **`pdf-inspector`:** add the `pdf-inspection` extra to the worker and docling-service images (`uv sync --frozen --no-dev --extra pdf-inspection`). The classifier itself also no longer needs `pdf_type == "text_based"`: a page with no text layer simply classifies as `needs_ocr`.
- **Cost:** `find_tables()` is ~0.9 s/page on portfolio (the census took 269 s for 292 pages). That is too slow to run in the worker child.
  - **Mitigation:** run `find_tables()` only on pages where a cheap signal (ruled lines or loose column alignment) fired. Cheap-negative pages are trusted as no-table.
  - **Budget:** ≤ 30 s for the pocketbook, measured in P1. If that is exceeded, `find_tables()` moves into the backend (`/convert` classifies its own shard).

**Wire format** (handshake JSON and request body), as run-length ranges:
`"page_classes": [[0, 11, "T--"], [12, 273, "T-t"], …]`. The flags are `T`/`-` for text, `i`/`-` for images, and `t`/`-` for tables.

## Page-Class Chunker

```
runs   = run_length(pages, key=(needs_tables, needs_ocr))
runs   = absorb(runs, min=MIN_CHUNK_PAGES)          # merge short run into the neighbour with the
                                                    # same key if any, else the larger neighbour; key := OR
chunks = [c for r in runs for c in split(r, max=plan.pages_per_chunk)]
```

- **Invariants** (property-tested):
  - every page is covered exactly once, in order;
  - `chunk.needs_x ≥ page.needs_x` for every page in the chunk;
  - no chunk exceeds `MAX_CHUNK_PAGES`;
  - the number of chunks is at most `ceil(N/MIN) + |runs|`.
- **Per-chunk options:** `_build_pdf_pipeline_options(do_table_structure=c.needs_tables, do_ocr=c.needs_ocr or force_ocr, tableformer_mode=env)`.
- **Global OCR override:** `DOCLING_DO_OCR` in {`0`: page-class driven; `1`: force on; `off`: force off}.
- **Expected pocketbook shape:** 12 front pages and 18 back pages without tables form 2 chunks with no tables and no OCR. The 262 table pages form ⌈262/11⌉ ≈ 24 chunks with tables and OCR. Gains are small here (RFC F2). They are large on text-dominant documents such as the T&C corpus, and R4 measures both.

**Benchmark harness** (`scripts/conversion_bench.py`):
- Drives `/convert` directly on the active remote, with each arm set by per-request overrides (`tableformer_mode`, `pageclass_chunking`, `do_ocr_policy`).
- Runs each arm twice; reports medians, verdict, garble and table-cell diff.
- Writes the audit markdown.
- Never runs on portfolio.

## Split Coordinator

### `/capacity`

```json
{"backend":"mac","build_sha":"…","effective_cpus":12.0,"total_mem_bytes":68719476736,
 "free_mem_bytes":41000000000,"reserve_bytes":4294967296,"chunk_pages":11,
 "per_proc_peak_bytes":1504000000,"safe_procs":12,"busy_slots":0,"max_slots":1,
 "spp_ewma":19.2,"spp_samples":27}
```

- `safe_procs = min(floor(effective_cpus), floor((free_mem − reserve) / per_proc_peak))`, where `per_proc_peak = WORKER_BASE_BYTES + chunk_pages·PER_PAGE_BYTES`.
- **Linux `free_mem`:** `min(cgroup memory.max − memory.current, /proc/meminfo MemAvailable)`. The host MemAvailable is visible in the pod through `/proc/meminfo`. Because the node is 158% overcommitted, the cgroup limit alone is not enough.
- **docling-local reserve:** `max(reserve, PORTFOLIO_RESERVE_BYTES=1.5 GiB)`.
- **`spp_ewma` with no samples:** a configured prior per backend (`DOCLING_SPP_PRIOR`: Mac 19, cpx62 40, portfolio 60).

### Allocation

```
eligible = [b for b in backends if hr3_ok(b, doc) and b.build_sha == expected and b.safe_procs >= 1]
if not eligible: fall back to docling-active whole-document (today's path)
rate_b   = b.safe_procs / b.spp_ewma
alloc_b  = N · rate_b / Σ rate                    # pages
initial  = contiguous prefix/suffix per backend, snapped to R3 chunk boundaries,
           covering ≈ 80% of N in proportion to alloc_b
tail     = remaining chunks → shared queue; each backend pulls one shard of
           ≤ safe_procs chunks when its previous shard returns
```

- **Placement:** the fastest backend gets the front of the document and the others take contiguous blocks after it. This keeps joins at run boundaries.
- **Shard size:** at most `safe_procs × chunk_pages` pages, so a backend's internal plan fills its processes in one wave.

**Local gating (RFC R5 AC5),** before each local shard:
1. Re-read `/capacity`; `safe_procs < 1` puts the shard back in the queue.
2. `admission.reserve(bytes=per_proc_peak × procs, ttl=deadline)`; failure puts the shard back in the queue.
3. Dispatch.
4. Release the reservation in `finally`.

**docling-local pod:**
- `resources.limits.memory` = the planned peak for `safe_procs_max=1` (≈ 1.9 GiB with the parent);
- `requests.memory` = idle RSS;
- low `priorityClassName`;
- replicas 1 only if the measured idle RSS is ≤ 400 MiB. Otherwise replicas 0, and the coordinator gets a connection-refused and marks it absent (cached 60 s).

### Failure, deadline and merge

- **Deadline:** one shared deadline `T = DOCLING_SERVICE_TIMEOUT_S`. The per-request httpx timeout is `T − elapsed`.
- **Retry:** a failed shard is retried once on another eligible backend. If none exists, it is retried once on the same backend when the remaining time allows; otherwise the conversion fails, with the same error class as today, so the worker's existing retry semantics stay unchanged.
- **Merge:** concatenate the shard markdown by `page_start`, rebase `pic["page"]`, and count the heading-level discontinuities at joins (`split_join_heading_shifts`, logged).

**Active-remote resolution.** Read the endpoints of `docling-active` through the in-cluster API (the worker ServiceAccount needs `get` on `endpoints`). Alternatively the controller writes the choice to a ConfigMap key, `docling-active-backend=mac|docling-1`. Prefer the ConfigMap: the RBAC is smaller and it is already the controller's source of truth.

## Correctness Properties

- **P1 (coverage):** the shards' page ranges partition `[0, N)`.
- **P2 (need monotonicity):** no page is converted with fewer models than its class needs.
- **P3 (OOM safety):** docling-local never starts a shard when `MemAvailable − planned_peak < PORTFOLIO_RESERVE_BYTES`.
- **P4 (HR3):** no PII document reaches a non-cluster backend.
- **P5 (fallback):** with split disabled or no eligible backend, behaviour is byte-identical to today's `docling-active` path.
