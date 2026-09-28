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
                      │                          │  table capture (R7, pool) │
                      │                          │  coordinator (R5) ──┐         │
                      │          promtail DS ──► Loki ──► Grafana      │ /capacity│
                      │          (no Docling on portfolio — NG7)       │ /convert │
                      └────────────────────────────────────────────────┼──────────┘
                                                                        │
                         until P5: exactly one active remote (node controller); P5: both
                         ├─ Mac (Tailscale, public presigned URL)  — in-process push ─► loki-tailscale-gateway (100.120.146.20:3100, push-only) ─► Loki
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

**Mac.** No log agent. When `PAGEINDEX_LOKI_PUSH_URL` is set (only the Mac's `run.sh` sets it), `obs.configure()` adds `LokiPushHandler` (`obs/loki.py`): a bounded, drop-oldest queue drained by a daemon thread in batches of 500 lines or 2 s to `http://100.120.146.20:3100/loki/api/v1/push`. Labels are `host`, `service`, `level`, `kind`; `job_id`, `doc_id`, `doc_sha8`, `run_id` go as structured metadata. Chunk children install their own handler. `service.log` rotates in-process (10 MB × 5). A launchd updater (`macos/update.sh`, every 300 s) fast-forwards to `origin/master` and re-runs `install.sh` when relevant paths changed, `/health` reports `in_flight == 0`, and the tree is clean.

**Loki reachability.** `apps/infra/loki-tailscale-gateway.yaml` (infra repo): an `nginx-unprivileged` Deployment on portfolio's host network, listening only on the Tailscale address `100.120.146.20:3100` (so it is never on the public interface; no NodePort, no iptables). It forwards `POST /loki/api/v1/push` and `GET /ready` to `loki.infra.svc.cluster.local:3100` and returns 403 for everything else, since Loki has no auth. A push to `main` touching `apps/infra/**` applies it; no operator step.

**Erasure (HR2).** The shipped lines carry `doc_id` / `doc_sha8` / `doc_name_sha8`, both in the JSON line and as structured metadata, so Loki is a derived store. `delete_doc` ends with step 8, `loki_logs` (`storage/documents.py::_erase_loki_logs` → `obs/loki.py::request_log_deletion`). It POSTs one `/loki/api/v1/delete` request per known identifier, `{service=~".+"} |= "<needle>"`, over the last `PAGEINDEX_LOKI_ERASURE_LOOKBACK_H` hours (default 168). The target is the in-cluster Loki at `PAGEINDEX_LOKI_URL`, never the gateway, which stays push-only. The Mac lines have no `doc_id`, so the `doc_sha8` request is what reaches them. Loki 3.0 in `infra` already runs `retention_enabled: true` + `delete_request_store: filesystem`, so `deletion_mode` is `filter-and-delete`. Queries hide the lines on acceptance, the compactor removes them after 24 h, and `retention_period: 72h` is the backstop. With the URL unset the step is not reached, which gives `partial_purge=True`. A refused request lands in `errors`. `PAGEINDEX_LOKI_URL` must be set on the pageindex-mcp server (and worker) env in the infra repo.

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
- Result 2026-09-27 (`audit/RFC052_CONVERSION_BENCH_2026-09-27.md`): FAST fails R4 AC4; the defaults stay page-class chunking on, `DOCLING_DO_OCR=0`, and TableFormer `accurate` (RFC R4 AC4 amendment).

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
- **`spp_ewma` with no samples:** a configured prior per backend (`DOCLING_SPP_PRIOR`: Mac 19, cpx62 40).
- **P3 use (one active remote):** the service's own planner clamps its process count by `safe_procs`. The worker logs the `/capacity` snapshot per job. Allocation below is P5 only.

### Allocation (P5: Mac + docling-1 together)

Backends are `docling-service-mac` and docling-1's `docling-service`. Nothing scheduled on portfolio is ever a candidate (NG7).

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

**Per-shard check:** before each shard, re-read that backend's `/capacity`; `safe_procs < 1` or `busy_slots == max_slots` puts the shard back in the queue. A backend that refuses connections is marked absent (cached 60 s).

**docling-local:** dropped (RFC NG7, D10). The infra companion to P3 deletes the `docling-service-local` Deployment and the `docling-node.sh local on` path.

### Failure, deadline and merge

- **Deadline:** one shared deadline `T = DOCLING_SERVICE_TIMEOUT_S`. The per-request httpx timeout is `T − elapsed`.
- **Retry:** a failed shard is retried once on another eligible backend. If none exists, it is retried once on the same backend when the remaining time allows; otherwise the conversion fails, with the same error class as today, so the worker's existing retry semantics stay unchanged.
- **Merge:** concatenate the shard markdown by `page_start`, rebase `pic["page"]`, and count the heading-level discontinuities at joins (`split_join_heading_shifts`, logged).

**Active-remote resolution.** Read the endpoints of `docling-active` through the in-cluster API (the worker ServiceAccount needs `get` on `endpoints`). Alternatively the controller writes the choice to a ConfigMap key, `docling-active-backend=mac|docling-1`. Prefer the ConfigMap: the RBAC is smaller and it is already the controller's source of truth.

## P4 Decisions (user, 2026-09-27)

| ID | Decision |
|---|---|
| P4-1 | The R9 bypass signals are computed **in docling-service, per chunk** (service-local `find_tables()` on the chunk's P1 table pages, ~3% of chunk time). The worker's capture never gates dispatch, and the R7 veto set is empty. |
| P4-2 | Keep the worker's 1536 Mi limit. Use a 512 Mi reserve, 256 Mi per capture process, and 2 capture slots per pod. Measure the worker cgroup's `memory.peak` on the 9.1 live run, and raise the limit to 2 Gi only if the peak is above 1.3 Gi. |
| P4-3 | Table descriptions use `PAGEINDEX_FILTER_MODEL` (the cheaper model, same endpoint and HR3 gate), with 600 LLM descriptions per document at most. The rest get deterministic ones. |
| P4-4 | `force_full_page_ocr` disables the OCR bypass (and, until it was dropped, grid replacement), but not R3's no-table TableFormer skip. Every forced run records its **recovery context**: which documents took the path, and what the first pass did per chunk. These cases are measured (task 9.8) before any further policy change. |
| P4-5 | Recommended defaults adopted, each described in the sections below:<br>• PyMuPDF↔TableFormer links use containment (intersection ÷ smaller area) ≥ 0.5.<br>• Node spans come from service `heading_pages`, falling back to a title search.<br>• Tree route only; flat documents get no `tables.json`.<br>• Capture scans every page, cheap positives first.<br>• The `text` strategy runs only on column-alignment pages and never drives grid replacement.<br>• The search budget counts only tokens *added* over today's view.<br>• The ≤ 5% bound is judged on median arq job wall time over 2 runs per arm. |
| P4-8 | (Repair cycle 1) An explicit operator `do_ocr_policy="force_on"` outranks the R9 AC1 OCR bypass, below force. `docling_force_recovery` gains a `recovery_trigger` label and a computed `garbled_tableformer_overlap`; `doc_id` is dropped from the record (the service has none). |
| P4-9 | (Repair cycle 2) `recovery_trigger` is a **closed vocabulary** (`hr5_garble`/`hr5_low_content`/`hr5_image_dominant`); it sets `choice`/`force_reason` to the closed `hr5_recovery` category, never to the label itself. `garbled_tableformer_overlap` is `null`, with a companion `garbled_pages_known: bool`, when no first-pass chunk carried `garbled_pages` -- unknown is not zero. The resolved `do_ocr_policy` must be threaded all the way into `_apply_chunk_bypass` (chunk child, `_decide_chunk_bypass`, and the direct route), not just the first `do_ocr` resolution, or a per-request `force_on` is silently lost to the bypass. `prior_pass` pages are whole-document (an HR5 recovery request is itself unsliced); the worker rebases the shard-relative `applied.chunks[*]` it forwards verbatim (including `grid_replace`) by that shard's `page_start`. |

## Table Capture

**Where it runs.** Capture runs in the worker's converter child (`converters_cli` → `CustomPageIndexClient._convert_to_tree`, `client/indexer.py`).
- **Start:** right after the handshake, before `_remote_pdf_to_markdown`.
- **Join:** after conversion returns. Dispatch never waits on capture (P4-1).
- **Gate (HR4, explicit):** capture starts only when the conversion is on the **remote PDF route** *and* `pipeline_config.allow_agpl_fallback` is on -- both conditions are checked directly in `client/indexer.py`, not inferred from `state.pdf_page_count` alone (a page count could in principle be known without AGPL fallback being allowed). Local-docling and non-PDF routes never start capture and never write `processed/<id>.tables.json` (P10).

```
converter child
 ├─ preclassify (P1, sync)                  ──► handshake
 ├─ cap = tables.capture.start(pdf, classes) ──► spawn processes (own slots)
 ├─ _remote_pdf_convert(...)                ──► docling-active (never waits on cap)
 │      cap done (~125 s) ⟶ tables.describe.run(cap.tables)   (LLM, overlapped)
 ├─ md → tree → validate_tree → finalize_gate_and_route        (HR5, unchanged)
 ├─ cap.join(grace=TABLES_JOIN_GRACE_S)
 └─ _persist_tree_result: anchor(tree, tables, table_results)
                          → insert table nodes (post-gate) → save_doc → save_tables → save_doc_meta
```

**Interfaces** (new package `src/pageindex_mcp/tables/`):

```python
# tables/capture.py
def start(pdf_path: str, *, page_count: int, page_classes: list[PageClass] | None,
          deadline_monotonic: float) -> CaptureHandle
class CaptureHandle:
    async def join(self, grace_s: float) -> CaptureResult   # never raises; kills stragglers
@dataclass(frozen=True)
class CaptureResult:
    tables: list[TableRecord]; failed: list[tuple[int, int, str]]  # [start, end, reason]
    procs: int; duration_s: float; peak_rss_bytes: int
```

**Pool sizing** (per document, at `start`):

```
cpu   = floor(available_cpus())                                              # docling_resources.py
mem   = floor((free_memory_bytes() − TABLES_RESERVE_BYTES) / TABLES_PROC_BYTES)
pages = ceil(N / TABLES_MIN_PAGES_PER_PROC)
procs = max(1, min(cpu, mem, pages, slots_free))
```

- **Pod-wide slots:** `TABLES_POD_SLOTS` (default `floor(available_cpus())`) is enforced with `fcntl` lock files `/tmp/pageindex-tables-slot-<i>.lock`. Two concurrent jobs share one cgroup, and without the slots they could start 4 × 256 Mi at once.
- **No free slot:** `start` waits for a slot up to the deadline. Dispatch never waits.

**Per-process memory kill: RSS watchdog, not RLIMIT_AS.**
- **Why not RLIMIT_AS or RLIMIT_DATA:** they cap virtual or data address space, which libmupdf and the glibc malloc arenas (64 MiB reserved per thread arena) over-reserve. A 256 MiB cap would raise `MemoryError` on memory that is never touched.
- **Watchdog:** a supervisor thread reads `/proc/<pid>/statm` every `TABLES_RSS_POLL_S` and SIGKILLs any process over `TABLES_PROC_BYTES`.
- **Backstop:** each capture process writes `1000` to its own `oom_score_adj`, so a spike that outruns the poll kills a capture process and not the converter child or arq (the 2026-09-17 outage, CLAUDE.md).
- **Process model:**
  - Processes use `multiprocessing.get_context("spawn").Process`, one per page range, following `_run_docling_chunk_with_timeout`.
  - Each installs `_install_parent_death_safeguard`.
  - `ProcessPoolExecutor` is not used: one killed worker breaks the whole pool with `BrokenProcessPool` and loses which task died.

**Ranges and `capture_failed`.**
- Pages are split into `procs` contiguous ranges. Each process opens the PDF itself (R7 AC1).
- Each process streams one JSON line per finished page, so a kill loses only unfinished pages. Those go to `failed` as `[start, end, "rss_limit" | "deadline" | "crash" | "no_slot"]`.
- Pages inside a range are scanned with P1 cheap positives first.
- Capture failure is never a job error.

**Strategies.**
- `lines` runs on every page.
- `text` runs only on pages with the P1 `column_alignment` signal where no `lines` table covers ≥ 50% of the aligned region. This targets the unruled pocketbook indicator tables.
- Each record keeps its `strategy`.

**Wall-time bound (R7 AC3).** Capture (~125 s at 2 processes) and the descriptions finish inside the ~742–788 s pocketbook conversion.
- **Bound:** median arq job wall time (`process_document_job` phase entry to DONE) with `TABLES_CAPTURE=1` ≤ 1.05 × the median with `0`, over 2 runs per arm on the same remote. Add a third pair if the spread exceeds 5%.
- **Tail:** `TABLES_JOIN_GRACE_S` bounds it.

**`processed/<doc_id>.tables.json`** (schema v1):
- `page` is 0-based, as in `docling_chunk`.
- `bbox` is in PDF points, top-left origin.

```json
{
  "schema_version": 1, "doc_id": "…", "page_count": 292,
  "capture": {"procs": 2, "duration_s": 131.4, "peak_rss_bytes": 91226112, "pymupdf": "1.27.2.2",
              "strategies": ["lines", "text"], "capture_failed": [[288, 291, "deadline"]]},
  "tables": [
    {"table_id": "p0104-t0", "page": 104, "page_label": "105", "bbox": [36.0, 92.5, 559.3, 411.8],
     "rows": 14, "cols": 5, "header": ["Indicator", "2019", "2020", "2021", "2022"],
     "cells": [["Indicator", "2019", "2020", "2021", "2022"], ["GDP (US$ bn)", "18.9", "20.1", "14.3", "14.6"]],
     "markdown": "| Indicator | 2019 | … |", "source": "pymupdf_find_tables", "strategy": "text",
     "coverage": 0.71, "node_id": "0042", "tree_node_id": "0042_seg1", "node_id_method": "heading_page",
     "caption": "Table 3. Economic indicators", "title": "Table 3. Economic indicators",
     "description": "GDP, inflation and trade indicators for 2019–2022.", "description_source": "llm",
     "links": [{"table_id": "p0104-f0", "overlap": 0.93}]},
    {"table_id": "p0104-f0", "page": 104, "source": "docling_tableformer", "strategy": null, "…": "…",
     "links": [{"table_id": "p0104-t0", "overlap": 0.93}]}
  ]
}
```

Field rules:
- `table_id` = `p{page:04d}-t{k}` (PyMuPDF) or `-f{k}` (TableFormer), numbered in reading order.
- `coverage` = the page's word characters whose word-box centre lies inside the bbox, ÷ all the page's characters.
- `caption` = the nearest text block within 40 pt above or below the bbox that matches `^(Table|Tabelle|Tab\.)`, else null.
- `title` = the caption, else the header cells joined by ` | ` (≤ 80 chars), else `Table p.{page_label}`.
- `cells` is row-major text, with `None` written as `""`.

**TableFormer source (new service field).**
- **Response field:** `PdfConvertResponse` gains `table_results: list[TableResultOut]` (`page`, `bbox` top-left, `rows`, `cols`, `cells`, `markdown`). It also gains `heading_pages: list[[str, int]]`.
- **Built from:** `DoclingDocument.tables` / heading `prov`. Verify the attribute names against the installed Docling at build time.
- **Rebasing:** entries are rebased per chunk like `pic["page"]`, and by `page_start` on a P3 slice.
- **Worker side:** the worker reads the new fields through a new `_remote_pdf_convert` returning a dataclass. `_remote_pdf_to_markdown` keeps its `(md, pics)` signature. **Correction (P4-7):** `client/recovery.py` is NOT untouched -- it DOES need a kwarg/context to forward `prior_pass` (and `recovery_trigger`) on an HR5 recovery request; the earlier "untouched" claim here was wrong. That wiring is wave 2 couple B's, not P4 wave 1.
- **Older remote service:** a remote service without the field yields PyMuPDF-only records with `links=[]` -- this is the only "records without TableFormer" case, since capture (per the HR4 gate above) never runs on a local-docling or non-PDF route in the first place.

**Linking (R7 AC5).** Two tables on the same page link when `area(A∩B) / min(area A, area B) ≥ TABLES_LINK_MIN_OVERLAP` (0.5). This is containment, not IoU: the pocketbook's `lines` result is a header box inside TableFormer's table. Links are many-to-many and symmetric, and both records are always kept.

**`node_id` assignment** (`tables/anchor.py`, after the gate). Docling-route tree nodes carry no page ranges, so spans are computed:
1. **Heading pages.** Taken from the service's `heading_pages` (the only route capture runs on, per the HR4 gate above). When that comes back empty -- an older service, or a document with no headings the service resolved -- a forward-only search of each node title over the normalized per-page PyMuPDF text stands in instead; this is a fallback within the same remote route, not a different route.
2. **Node spans.** A resolved node's span runs from its heading page to the heading page of the next resolved node at the same or a shallower depth (inclusive: that next heading can sit mid-page), else to the last page.
3. **Assignment.** A table gets the deepest resolved node whose span contains its page (`node_id_method="heading_page"`); a tie on a shared page is broken by y position among the deepest candidates only. Otherwise the nearest preceding resolved node, or -- for a table that falls before every heading -- the first resolved node (`"nearest"`).
4. **`tree_node_id`.** The existing `_seg` child (`helpers/tree_split.py`) whose pipe-table cell tokens match the record's cells (Jaccard ≥ 0.5), else a new `_t<k>` child.

**Tree insertion** (after `validate_tree`/`compute_verdict`, before `save_doc`):
- A matched `_seg` node gains `type: "table"`, `table_id`, `description`, and `start_index = end_index = page + 1`. Its title and text are unchanged.
- An unmatched PyMuPDF table becomes a new child `{node_id: "<node>_t<k>", type: "table", title, description, table_id, start_index, end_index, text: markdown}`.
- `node_count` in `.meta.json` is computed before insertion.

**HR2.** `save_tables(doc_id, payload)` writes `processed/{doc_id}.tables.json` in `storage/documents.py`, following `save_doc`.
- **Manifest step:** `ErasureStep(name="processed_tables_json", step=2, required=False)` goes right after `processed_flat_json`. It calls `_remove_object_idempotent` and is added to `_PREFIX_TO_ERASURE_STEPS["processed/"]`.
- **Tests:** the exact-set assertions in `tests/test_storage.py` are updated. There is no new erasure test (user decision).
- **Order:** the step runs before `redis_cache`, and no new Redis key is added.
- **Never quarantined:** tables are written only by `_persist_tree_result`, never under `quarantine/` (HR5).

**Retrieval (R7 AC8).** A table node's `text` is its markdown. `_search_one_doc` already returns the matched nodes' `text` (`helpers/rag.py`), so a table question is answered from stored cells with no re-extraction.

**HR4.** The records are PyMuPDF (AGPL) output served over MCP, and capture is a new use of it. The legal review is deferred (user, 2026-09-27) and tracked as an open item in DESIGN.md.

**Knobs** (worker, `tables/settings.py`):

| Env | Default | Meaning |
|---|---|---|
| `TABLES_CAPTURE` | `1` | Master switch; `0` = today's behaviour |
| `TABLES_PROC_BYTES` | `268435456` | RSS kill limit per process (≈3× the 87 MiB peak) |
| `TABLES_RESERVE_BYTES` | `536870912` | Cgroup headroom kept for the tree build and a second job |
| `TABLES_MIN_PAGES_PER_PROC` | `30` | Page bound on the pool |
| `TABLES_POD_SLOTS` | `floor(available_cpus())` | Capture processes across concurrent jobs |
| `TABLES_RSS_POLL_S` | `0.25` | Watchdog period |
| `TABLES_JOIN_GRACE_S` | `30` | Max wait for capture after conversion |
| `TABLES_STRATEGIES` | `lines,text` | `text` only on column-alignment pages |
| `TABLES_LINK_MIN_OVERLAP` | `0.5` | Containment threshold |

**Failure modes.**

| Failure | Result |
|---|---|
| RSS kill, crash, deadline or no slot | Those pages go to `capture_failed`; the job continues |
| `tables.json` write error | WARNING plus metric; the tree still persists |
| Service without `table_results` | PyMuPDF-only records, `links=[]` |
| Heading resolution fails | `node_id_method="nearest"` |

## Tables in Search

**Search view** (`tables/search_view.py`, replacing `_strip_text` in `_search_one_doc`):

```python
def build_search_view(structure: list, *, budget_tokens: int, tables_on: bool) -> tuple[list, dict]
```

- **Rendering:**
  - Non-table nodes render exactly as today.
  - A table entry renders as `{"node_id", "type": "table", "title", "description"}`.
  - A `_seg` node without `type` renders exactly as today.
- **Prompt:** one extra line: "Nodes with type=table are tables; select them for questions answered by figures in a table."

**Descriptions** (`tables/describe.py`, run inside capture's overlap window):

```python
async def describe(tables: list[TableRecord], *, model: str) -> dict[str, str]  # table_id → ≤30-token text
```

- **Batching:**
  - 25 tables per call, 4 calls in flight.
  - Input per table: title, caption, header and the first 3 rows, ≤ 400 chars.
  - Output: JSON `{table_id: description}`.
- **Model and routing (P4-3, HR3):** `TABLES_DESC_MODEL` defaults to `PAGEINDEX_FILTER_MODEL`. Calls go through `_llm_with_retry`, so `require_zdr_compliance` applies.
- **Fallback:** when a call is blocked, fails, or the table is over `TABLES_DESC_MAX_PER_DOC`, it gets a deterministic header-row description, `description_source="fallback"`. The cap is spent on the highest-coverage tables first.
- **Scope:** only PyMuPDF records get LLM descriptions. A linked TableFormer twin copies its partner's.
- **HR3 note:** PII corpora must also run with `LANGFUSE_TRACE_CONTENT=false`.
- **Bounded wait (P4 follow-up):** `PendingTables.finalize` (`tables/anchor.py`) awaits the describe task with `asyncio.wait_for`, not unboundedly -- a slow LLM call must never push the converter child past its job deadline and lose the whole tree (capture failure is never a job error). The caller (`_persist_tree_result`, `client/indexer.py`) is the one that knows the job deadline: it passes `finalize(..., job_deadline_monotonic=child_deadline_monotonic())`, keeping `tables/` free of a `client/` import. `_descriptions()` takes the smaller of `TABLES_DESC_DEADLINE_S` and (when a job deadline was passed) that deadline minus `anchor._DESC_DEADLINE_MARGIN_S` (10 s, reserved for `finalize`'s own anchoring work and the persist writes after it); with no job deadline (e.g. under test) only `TABLES_DESC_DEADLINE_S` applies. On timeout the describe task is cancelled and every record gets its deterministic fallback description (`anchor.fallback_description`), logged as a WARNING with counts only (HR3: no table text in the log).

**Token budget (R8 AC3).**
- **Counting:** tiktoken `o200k_base` (made a direct dependency), else chars/4.
- **Budget:** tokens(view with tables) − tokens(view with `tables_on=False`) ≤ `TABLES_SEARCH_TOKEN_BUDGET`. Today's `_seg` entries (~15k tokens) are not counted.
- **Drop order:**
  1. new `_t<k>` entries, lowest `coverage` first;
  2. then the added `description`/`type` of enriched `_seg` entries, which fall back to their current form.
- Storage is never touched. Drops are logged as a `tables_search_budget` decision.

**`get_page_content` (R8 AC4).**
- Table nodes carry `start_index = end_index = page + 1`, so `_extract_page_hits` returns them for their page, with `text` = markdown. Hits gain `type` and `table_id`.
- The MCP surface stays 5 tools.
- DESIGN.md is corrected to the real `find_relevant_documents` shape `{query, sources, content}`.
- **Separate follow-up:** Docling-route nodes otherwise carry no `start_index`/`end_index`, so `get_page_content` matches only page 0 on them.

**Server knobs:** `TABLES_IN_SEARCH` (`1`; `0` = today's `_strip_text` view), `TABLES_SEARCH_TOKEN_BUDGET` (`8000`).
**Worker knobs:** `TABLES_DESC_ENABLED` (`1`), `TABLES_DESC_MODEL` (`PAGEINDEX_FILTER_MODEL`), `TABLES_DESC_BATCH` (`25`), `TABLES_DESC_CONCURRENCY` (`4`), `TABLES_DESC_MAX_PER_DOC` (`600`), `TABLES_DESC_DEADLINE_S` (`60`, only used when no converter-child deadline is known).

**Evaluation (9.5, R8 AC5).**
- **Questions:** `evals/table_questions/pocketbook.yaml`, ≥ 10 entries of `{id, question, answer, answer_page, table_hint}`. The pocketbook is public statistics, so no PII.
- **Runner:** `scripts/table_search_eval.py` runs `_search_one_doc` twice per arm (`TABLES_IN_SEARCH` 0/1, temperature 0).
- **Scoring:** a hit is the normalized answer contained in the returned content.
- **Pass:** hits(on) ≥ hits(off), with at most 1 per-question regression.
- **Output:** `audit/RFC052_TABLE_SEARCH_EVAL_<date>.md`, including prompt tokens per arm.

## Signal-driven Bypass

**Where the decision runs (P4-1).** In docling-service, inside each chunk child before its pipeline is built (`_docling_chunk_worker`, and the direct route in `converters/pipeline.py`). It is computed from the chunk's own pages.

```python
# converters/table_bypass.py
def decide_bypass(pdf_path: str, pages: range, classes: list[PageClass] | None, *,
                  force_ocr: bool, switches: BypassSwitches) -> ChunkBypass
@dataclass(frozen=True)
class ChunkBypass:
    do_ocr: bool; do_table_structure: bool
    bypass: Literal["none", "ocr", "tableformer", "both"]; reasons: tuple[str, ...]
```

| Rule | Page test | Signal source |
|---|---|---|
| AC1 skip OCR | clean text layer (P1 garble screen) **and** image fraction < `PAGECLASS_IMAGE_AREA_MIN` **and** (no table **or** the page's `find_tables()` tables together have ≥ `TABLES_OCR_BYPASS_MIN_FILLED` non-empty cells, cell-weighted, and every table with text is clean) *(amended 2026-09-28)* | `page_classes` from the request, plus service-local `find_tables()` on the chunk's table pages |
| AC2 skip TableFormer | P1 `has_tables` false | Today's R3 path |
| AC3 grid replace | **Dropped 2026-09-28 (user):** changed 13-20% of cells against TableFormer in the 9.7 bench | — |
| AC4 chunk | OCR off only if AC1 holds on every page; TableFormer off only if AC2 holds on every page | — |

**Label semantics (P4-7 clarification).** `bypass` (`ChunkBypass.bypass`) is the coarse label a reader groups by; `reasons` is the finer AC trail, and the two do not always align 1:1:
- `bypass="tableformer"` is R3's own AC2 no-table skip (the only TableFormer bypass since AC3 was dropped).
- `bypass="ocr"` is reported only when the bypass removed OCR the page-class decision would have run; a chunk whose OCR page-class policy already turned off shows `tableformer` even with `ac1` in its reasons.
- `reasons` may contain `ac1_clean_text_layer` even when the resulting `bypass` label is `"none"`: AC1 only removes OCR the P3 decision would otherwise have run, so a chunk that never needed OCR in the first place still records the AC1 finding for the 9.8 census without showing `bypass="ocr"`.

**AC1 "clean" definition.** A page counts as having a clean text layer when the P1 garble screen passes on the JOINED text of all of a table's non-empty cells (not judged per cell). A per-cell garble check misfires on numeric-heavy cells (a lone `"14.3"` or `"–"` looks like low-entropy garble in isolation), so numeric-heavy tables must fail AC1 safe -- i.e. an all-numeric or mostly-numeric table never satisfies "clean" on cell content alone and keeps OCR on for that page.

- **Cost:** service-local `find_tables()` takes ~0.8 s/page on a chunk's table pages only, ≈3% of chunk time, in parallel across chunks.
- **Plans unchanged:** the R3 chunk plan is not changed, so there are no new joins and R6 parity holds.

**Precedence (P4-4, amended P4-8, repair cycle 2 finding 1).** `_resolve_force_ocr` decides force.
- **Under force:** `do_ocr=True`, `bypass ∉ {ocr, both}`.
- **Not overridden:** R3's no-table TableFormer skip (AC2).
- **Orchestrator decision (P4-8):** below force, an explicit operator kill-switch (`do_ocr_policy="force_on"`, i.e. `DOCLING_DO_OCR=1`) outranks the R9 AC1 signal-driven OCR bypass. A human-set "OCR is always on" must not be silently defeated by a heuristic that thinks a page looks clean. Full order: `force_full_page_ocr` → policy `force_on` (**request** `do_ocr_policy` if given, else the `DOCLING_DO_OCR` env -- the existing request-over-env rule) → `bypass_ocr` → the rest of the policy (`page_class` defers to the chunk's `needs_ocr`).
- **Single decision point:** `_resolve_do_ocr` stays the only place OCR is decided, and takes `bypass_ocr` as an input below force and below an explicit `force_on`.
- **Correction (repair cycle 2, QA finding 1):** the resolved policy must be threaded all the way to where the bypass is applied, not just to the first `do_ocr` resolution: `_apply_chunk_bypass` and `_decide_chunk_bypass` both take a `policy` param now, `_docling_chunk_worker`/`_run_docling_chunk_with_timeout` forward it into the chunk child as `do_ocr_policy`, and the in-process direct route in `pipeline.py` passes its own `do_ocr_policy` into `_decide_chunk_bypass` the same way. Without this, a per-request `force_on` was silently lost at the one place (the chunk's own bypass decision) that actually reads `DOCLING_DO_OCR` from the env.

**Recovery context (P4-4, amended P4-8, corrected P4-9 repair cycle 2).** Every conversion with force on emits a `docling_force_recovery` decision record per document:
- `doc_sha8` and route (no `doc_id`: the docling-service has none, so this record identifies documents by `doc_sha8` + `job_id`, not `doc_id`);
- `choice`/`force_reason` stay the **closed** inferred category -- `request`, `env`, or `hr5_recovery` -- exactly as before P4-8. A valid `recovery_trigger` (below) sets `choice`/`force_reason` to `hr5_recovery`, same as `prior_pass` presence already did; it never becomes the label itself (repair cycle 2, QA finding 5 -- the earlier P4-8 draft of this section, which said the trigger "sets force_reason/choice", was wrong and is corrected here);
- `recovery_trigger` (P4-8, **closed vocabulary**, repair cycle 2 finding 5): one of `hr5_garble`, `hr5_low_content`, `hr5_image_dominant` -- one per `client/recovery.py` HR5 path (`_recover_garble_ocr`, `_recover_low_content_ocr`, `_recover_image_dominant_ocr`). Sanitized to `[a-z0-9_]`, ≤ 40 chars, AND checked against the closed set; anything else (including a merely well-formed but unrecognised label such as a stray `hr5_rtl`) is dropped to `None` rather than passed through. Lives only in this attr;
- for each chunk of the **first pass**: `do_ocr`, `do_table_structure`, `bypass`, `bypass_reasons`, the pages TableFormer produced tables on (`tableformer_pages`, from `table_results`), and optionally `garbled_pages` (P4-8) -- see "`garbled_pages` source" below for exactly where these pages come from and when they are omitted;
- `garbled_pages_known` (repair cycle 2, QA finding 2): `true` only when at least one first-pass chunk record actually carried a `garbled_pages` key **whose value is a list** (present-but-invalid, e.g. `None`, does not count as known -- the sanitizer drops the key entirely in that case rather than injecting `[]`). `low_content`/`image_dominant` triggers normally have no garble detector run and so omit it.

**`garbled_pages` source (wave 2 couple B, orchestrator decision).** `garbled_pages` is produced by the **worker**, not the service, and is wired as part of couple B's `client/recovery.py` work, not P4 wave 1:
- The worker takes the tree nodes that the HR5 garble detector flagged -- `helpers/garble.py`'s `detect_garble`/`_garble_check_nodes`, as already consumed by the recovery gate's `_eligible_garble` -- and maps each flagged node to the pages it covers using the first pass's `heading_pages` (node title → 0-based page; the same anchoring the table capture path already uses for a table node's `node_id` "heading_page" method). A node's page span runs from its own heading page up to (but not including) the next heading's page, i.e. `[heading_pages[i], heading_pages[i+1] - 1]`; the last node in document order spans to `page_count - 1`.
- The resulting set is **whole-document, 0-based** (matching `prior_pass`'s own page frame -- see "Wire list and page frame" above), computed once per recovery request and then filtered per `prior_pass` entry down to that entry's own `[page_start, page_end]` before being attached to that entry's `garbled_pages`.
- **Omission, not `[]`:** when `heading_pages` is empty (no headings resolved for the first pass) or no flagged node anchors to a page via that mapping, the worker omits `garbled_pages` entirely for that request -- this is what makes `garbled_pages_known=false` (unknown), never a known-empty list. This is also the only source: no earlier draft's mention of a "P1 text-layer garble screen" as the source applies here -- P1's screen feeds page **classification** (AC1), not the HR5 recovery's `garbled_pages`.
- **No `applied.chunks` at all (a local chain, a non-service fallback conversion, or an older service that predates R9 chunk bypass records) means the worker OMITS `prior_pass` altogether** -- `None`, never `[]` -- since there is nothing to attribute per-chunk context to.
- **First-pass forces are attributed by a join, not inline:** the inspector / pre-garble probe in `client/indexer.py` that forces the *first* pass records `choice="request"` there, with no `prior_pass` of its own (there is no prior pass yet). Task 9.8's census attributes that force to the worker's own `docling_force_recovery` decision record for the same document by joining on `job_id` -- listed explicitly as part of task 9.8's query, not a separate mechanism.
- **State carried to recovery:** the worker keeps the first pass's `applied.chunks` on its extraction state from the first pass through to any later recovery pass; each `_recover_*` method in `client/recovery.py` (`_recover_garble_ocr`, `_recover_low_content_ocr`, `_recover_image_dominant_ocr`) passes its own `recovery_trigger` into `_execute_ocr_retry`, which is where `prior_pass` (built from that retained `applied.chunks`, rebased per "Wire list and page frame" above) and `recovery_trigger` are both forwarded on the recovery request -- couple B's wiring job, not P4 wave 1's.
- `garbled_tableformer_overlap` (P4-8): count of pages that are in both some chunk's `garbled_pages` and some chunk's `tableformer_pages` -- directly answers "did the recovered garble involve TableFormer pages" without a manual join in Loki. **`null` (not `0`) when `garbled_pages_known` is `false`** -- omitted must never read as "zero overlap".

**Wire list and page frame (repair cycle 2, QA findings 3-4).** The worker forwards `applied.chunks[*]` **verbatim** as `prior_pass` entries: `page_start`, `page_end`, `do_ocr`, `do_table_structure`, `bypass`, `bypass_reasons`, `tableformer_pages`, plus an optional `garbled_pages` it adds itself. `prior_pass` pages are **0-based whole-document pages**, not slice-relative: an HR5 recovery request is itself unsliced (no `page_start`/`page_end` of its own), so there is no slice for them to be relative to. `applied.chunks[*]` WAS shard-relative to the earlier (possibly `page_start`-sliced, P3 R5 AC3) request that produced it -- the worker is the one that rebases those pages by that shard's own `page_start` before assembling this later request's `prior_pass`. (`page_classes`/`pages_with_tables`, by contrast, stay slice-relative to *this* request's own slice when one is given -- see "TableFormer source" above; only `prior_pass`, carried on an always-unsliced recovery request, is whole-document.) The sanitizer drops any other key, including a `grid_replace` from a build before AC3 was dropped.

The recovery request also carries a top-level `recovery_trigger` so the service can see why this is a forced pass, in addition to which pages TableFormer covered and which were garbled, before re-OCRing them. Every field is sanitized on the way into the record (HR3): unknown `bypass`/reason tags and non-numeric page fields are dropped rather than logged verbatim, and chunk/page lists are capped (512 chunks, 2000 pages).
- **P4 scope:** only recorded and logged, never acted on.
- **Measurement:** task 9.8 queries Loki for these records and tabulates the cases in `audit/RFC052_FORCE_RECOVERY_CASES_<date>.md`:
  - which documents took the path (`doc_sha8` + `job_id`);
  - why (`force_reason`/`recovery_trigger`);
  - how many first-pass chunks had OCR or TableFormer bypassed (filtering `bypass_reasons` for `ac3_trusted_grid` vs `ac2_no_table` where the distinction matters -- see "Label semantics" above);
  - whether the recovered garble involved TableFormer pages, filtering on `garbled_pages_known` first (`garbled_tableformer_overlap`, no manual join needed when known).
- Any policy that uses the prior pass needs that evidence first.

**9.8 census caveats.**
- **Local-docling recovery is silent to this census:** the `local_docling` dispatch branch of `_execute_ocr_retry` (used when `state.use_remote` is false) calls `pdf_to_markdown_docling` directly and forwards neither `prior_pass` nor `recovery_trigger` -- those are only ever attached to a `_remote_pdf_convert` call. A document recovered on the local chain therefore never emits a `docling_force_recovery` record with recovery context for that pass, and the 9.8 query must not assume every HR5-triggered retry is visible in Loki this way.
- **Mac-backend records depend on log shipping:** when the remote route is served by the Mac backend (RFC-050/051 split), its `docling_force_recovery` and `docling_chunk` decisions reach Loki only through the automated Mac log-shipping path, not a direct write -- if that shipping job is down or lagging, the census under-counts Mac-served documents without any other signal that it did.

**Logging.** `emit_docling_chunk` gains `bypass` and `bypass_reasons`, which the child returns to the parent record. `applied` echoes the switch values.

**Kill switches** (service env: docling-1 deployment and the Mac `run.sh`; readers in `config.py`):

| Env | Default | Meaning |
|---|---|---|
| `TABLES_OCR_BYPASS` | `0` → `1` after the 9.6 parity run | AC1 |
| `TABLEFORMER_SKIP_ENABLED` | existing | AC2 (reused) |
| `TABLES_OCR_BYPASS_MIN_FILLED` | `0.5` | AC1 non-empty cell share, cell-weighted across the page's tables |

**9.7 benchmark** (`scripts/conversion_bench.py`):
- **Arms:** `r3_ocr_bypass` (the 9.6 parity run) and `r3_trust`.
- **Pages compared:** only those the bypass covered (`applied.chunks[*].bypass`), with `r3` re-run on the same pages via the P3 `page_start`/`page_end` API.
- **Trust gate:** `changed_cell_ratio ≤ 0.02` on every document, no verdict worse, garble within tolerance.
- **OCR-bypass gate:** the same, plus changed text ≤ 2% on the covered pages.
- **Documents:** the pocketbook plus a ruled-table T&C PDF.
- **Output:** `audit/RFC052_GRID_BENCH_<date>.md`.
- **Guards kept:** `refuse_portfolio` and `require_hr3`.

## Correctness Properties

- **P1 (coverage):** the shards' page ranges partition `[0, N)`.
- **P2 (need monotonicity):** no page is converted with fewer models than its class needs.
- **P3 (OOM safety):** no Docling conversion is dispatched to portfolio, and no backend starts more processes than its free-memory `safe_procs`.
- **P4 (HR3):** no PII document reaches a non-cluster backend.
- **P5 (fallback):** with split disabled or no eligible backend, behaviour is byte-identical to today's `docling-active` path.
- **P6 (capture never gates dispatch):** the remote conversion is issued before any `CaptureHandle` result is awaited.
- **P7 (pool bound):** `1 ≤ procs ≤ min(cpu, mem, pages, slots_free)` once a slot is held, and the capture processes across a pod never exceed `TABLES_POD_SLOTS`.
- **P8 (memory kill):** a capture process whose RSS exceeds `TABLES_PROC_BYTES` is dead within 2 × `TABLES_RSS_POLL_S`, its unfinished pages are `capture_failed`, and the converted markdown is unaffected.
- **P9 (page partition):** every page in `[0, N)` is either scanned or in exactly one `capture_failed` range.
- **P10 (persist iff tree):** `processed/<id>.tables.json` exists iff `processed/<id>.json` was persisted. Nothing table-related is written on REJECT or under `quarantine/`.
- **P11 (gate invariance):** the `validate_tree` verdict, `node_count` and `compute_verdict` are identical with `TABLES_CAPTURE` 0 and 1.
- **P12 (HR2):** `validate_erasure_manifest()` passes, and `delete_doc` removes `processed/<id>.tables.json` before `redis_cache`.
- **P13 (node_id totality):** every record's `node_id` names an existing non-table node. `tree_node_id` is required (non-null) for every PyMuPDF record and for a TableFormer record that is either linked to an inserted PyMuPDF twin or matched a `_seg` of its own; an unlinked, unmatched TableFormer record has `tree_node_id=null`.
- **P14 (never drop):** records from both sources are kept, and links are symmetric.
- **P15 (budget):** added search-view tokens ≤ `TABLES_SEARCH_TOKEN_BUDGET`, and drops follow ascending coverage. Storage is unchanged by the budget, and with `TABLES_IN_SEARCH=0` the view equals `_strip_text`.
- **P16 (HR3):** with `PII_CORPUS=true` and a non-ZDR endpoint, description generation makes zero egress calls and every description is `fallback`.
- **P17 (need safety):** OCR is bypassed only when AC1 holds on every page of the chunk, and TableFormer only when AC2 does.
- **P18 (force precedence):** `force_full_page_ocr` ⇒ `do_ocr=True`, `bypass ∉ {ocr, both}`, and a `docling_force_recovery` record is emitted with the first pass's per-chunk context.
- **P19 (kill switches):** with every P4 switch off, each chunk's `(do_ocr, do_table_structure)` equals the P3 decision.
- **P20 (logging):** every `docling_chunk` record carries `bypass ∈ {none, ocr, tableformer, both}`.
