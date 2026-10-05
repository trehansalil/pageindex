<!-- Space: CITRA -->
<!-- Title: RFC-052: Observable, Page-Class-Aware, Capacity-Weighted Conversion -->
<!-- Folder: RFCs -->

---
id: "RFC-052"
title: "Observable, Page-Class-Aware, Capacity-Weighted Conversion"
type: rfc
status: draft
date: "2026-09-26"
plan-impact: "yes"
tags:
  - rfc
  - performance
  - observability
  - docling
  - scaling
aliases:
  - "RFC-052"
  - "Capacity-Weighted Conversion"
governs:
  - "[[design-rfc052-observable-capacity-weighted-conversion]]"
  - "[[tasks-rfc052-observable-capacity-weighted-conversion]]"
supersedes: []
---

## Context

The goal of this RFC is to convert one large document as fast as possible, as an experimental showcase. Batch throughput comes later. Docling conversion dominates end-to-end time. On the 292-page pocketbook through the Mac:

| Stage | Time |
|---|---|
| Extraction (Docling) | ~431 s |
| Tree build | ~18 s |
| Recovery | ~5 s |
| **Total** | **~467 s** |

Three read-only investigations (2026-09-26) found the following.

**F1. The table skip (RFC-050 R7) shipped but never activates.**
- The worker image lacks the optional `pdf-inspector` extra (`pyproject.toml:72`; `Dockerfile` runs a plain `uv sync --frozen --no-dev`). The running worker pod raises `ModuleNotFoundError: pdf_inspector`. That leaves `pdf_type` null, so `detect_pages_with_tables` (`converters/preclassify.py:425-480`) is gated off (`:501`).
- Even with the package installed, `_page_has_ruled_table` (`preclassify.py:364-391`) raises `AttributeError`. On PyMuPDF 1.27.2.2, `get_cdrawings()` returns plain tuples, not objects. The call is outside the per-page `try`, so the whole detection returns `(None, None)` and logs only at debug level. It failed on 289 of 292 pocketbook pages.
- Both failures fall back to `None`, which means TableFormer stays on everywhere.
- In all six pocketbook runs on 2026-09-25, Loki shows `preclassify_document pdf_type: null`. The Mac ran TableFormer on all 27 chunks.

**F2. The pocketbook is table-heavy, so skipping by page class alone does not make it fast.**

PyMuPDF census:

| Measure | Pages |
|---|---|
| Real tables (`find_tables()`) | 262 / 292 |
| No table and no image | 29 (pp. 0-11, 274-291) |
| Vector drawings | 289 |

- The column-alignment detector flags 264 pages, so it would switch TableFormer on almost everywhere on normal documents too.
- TableFormer runs only on regions the layout model labels as tables. Skipping it on table-free chunks saves mainly model load and setup: about 1% here.
- The cost that remains is the layout model on every page, plus TableFormer **ACCURATE** (hardcoded, `converters/docling_conv.py:95`) on the 262 table pages.
- OCR is inconsistent across backends:
  - `DOCLING_DO_OCR` defaults to `0` (`docling_conv.py:83`), but the Mac's `run.sh` sets it to `1`.
  - Docling's non-forced OCR only covers bitmap regions, so the cost of OCR on pure-text pages is expected to be small but is **unmeasured**.

**F3. Logs are mostly in Loki already, but not usable per job.**

Promtail → Loki (72 h retention) → Grafana runs in `infra`. Server, worker and node-controller streams are ingested, and `{service="pageindex-mcp-worker"} |= "job_id"` already correlates.

Defects:
- `container` is relabelled to the pod name.
- The json stage maps `name` and `timestamp`, while our envelope emits `logger` and `ts`, so `logger` is never set and timestamps are not parsed.
- About half of the worker lines are arq plain-text duplicates.
- Most server lines are `GET /metrics` access logs.
- The controller's `tick` container is missing.
- docling-1 is never scraped: promtail does not tolerate the `dedicated=docling:NoSchedule` taint.
- The Mac is never scraped: no agent is installed, and Loki is ClusterIP-only.
- docling-service logs plain text with no job correlation, no per-chunk timing, no timestamps (Mac), and no table or OCR flags.

**F4. Splitting one document across machines can be built, but is limited by memory, not code.**
- The service accepts only a whole-PDF presigned URL (`services/docling-service/app.py:97-101`).
- `/health` returns `{"status":"ok"}` only.
- `available_memory_bytes()` returns **total** memory, not free (`converters/docling_resources.py:95-113`).
- A single chunk failure other than a timeout 500s the request, and the worker retries the **whole document** (`client/indexer.py:377-433`).
- **Portfolio** (4 vCPU, 7.6 GB):
  - MemAvailable was ~1.9 GiB, with 4.2 GB of swap in use and a load average of ~5.9 at the time of measurement.
  - One Docling process peaks at 1.3-1.45 GiB; a `docling-service-local` plan peaks at ~3.5 GiB.
  - At best portfolio contributes 1 process at ~40-60 s/page per process, about 2-3% of pages.
  - It is the host that OOM-killed cluster pods on 2026-09-17.

### User decisions (2026-09-26)

- **UD1. Backends.**
  - **Remote:** exactly one active remote at a time: the **Mac if present, else docling-1**. The existing `docling-node-controller` rule stays.
  - **Mac + docling-1 together** is delivered as a later phase (P5), not now.
  - **No Docling on portfolio.** `docling-local` is dropped from this RFC (user decision, 2026-09-27). The only backends are the Mac, docling-1, or both together in P5.
- **UD2. OCR by page.** A page that has a text layer, no images and no tables gets **no OCR**.
- **UD3. Delivery.** One RFC delivered as stacked PRs. Branches share the prefix `ICR-97-rfc52-` with a distinct slug per phase (see [Implementation Plan](#implementation-plan)).
- **UD4. TableFormer mode.** FAST vs ACCURATE is benchmarked in this RFC. The default changes only if quality holds.

### Relationship to Prior RFCs

- [[RFC-050]] R7 (selective table structure): this RFC **repairs** R7 (F1) and extends its per-chunk granularity into page-class chunking (R3).
- [[RFC-050]] D1/D2 (offload-aware admission, `MAX_JOBS`): unchanged. With no Docling on portfolio, no shard needs a `pageindex:admission` reservation.
- [[RFC-027]] D7 (chunking loses the outline and re-levels headings at joins): every new join R3 and R5 add carries the same cost. It is measured in R4/R6.
- [[RFC-046]] / HR5 (OCR attribution, garbling): R3's OCR skip must not remove the `force_full_page_ocr` escalation path (`client/recovery.py:453-483`).
- [[RFC-051]] and the `docling-node-controller`: the controller keeps choosing the active remote. This RFC does not change that choice.

## Goals

- **G1 (observability):** For one `job_id`, Grafana shows every log line across MCP server, worker, converter child, node controller, docling-1 and the Mac. This includes per-chunk page range, backend, table flag, OCR flag, duration and peak RSS.
- **G2 (page-class correctness):** Every text-based PDF gets a working per-page classification: text layer, images, tables. TableFormer and OCR are enabled per chunk from it, so that a text-layer page with no image and no table runs neither (UD2).
- **G3 (measured levers):** Measure seconds per page for OCR on/off (page-class) and TableFormer FAST vs ACCURATE on the pocketbook plus two contrast documents. Adopt a default only where the quality gates hold.
- **G4 (capacity-weighted split, phased):** Every backend reports its live capacity (P3). In P5, when the Mac and docling-1 are both up, one document's chunks are split between them, weighted by each backend's **live** free memory, effective CPU and measured speed. The algorithm is written for N backends so that later horizontal scaling needs configuration, not code.
- **G5 (no OOM):** No Docling conversion runs on portfolio. Each backend clamps its process count by its own free memory (R5 AC2), so a busy backend refuses work rather than running out of memory.

## Non-Goals

- **NG1:** Running the Mac and docling-1 concurrently **before P5**. Until P5 ships, only one remote is active (UD1).
- **NG7:** Docling on portfolio (`docling-local`). Dropped 2026-09-27. Portfolio's 4 cores and about 2 GB free memory are left to the cluster; the worker's R7 table capture is the only extraction work there.
- **NG2:** Resizing portfolio or adding nodes. The split must be correct at today's size and pick up capacity automatically later.
- **NG3:** Replacing promtail with Alloy in-cluster, or changing Loki storage or retention. Promtail 3.0 stays. The Mac runs no log agent; docling-service pushes its own logs to Loki (D2).
- **NG4:** Batch or multi-document throughput and KEDA changes. KEDA stays paused.
- **NG5:** A visual table detector (RFC-050 D9/R8). R2 uses PyMuPDF `find_tables()` plus fixed heuristics only.
- **NG6:** Routing PII documents to the Mac. HR3 still holds (R5 AC7).

## Glossary

| Term | Definition |
|---|---|
| Active remote | The single off-portfolio Docling backend the node controller routes `docling-active` to: the Mac, else docling-1. |
| docling-local | *Dropped (NG7).* `docling-service-local` Deployment on portfolio, replicas 0. Not a backend in this RFC. |
| Backend | Any docling-service instance that reports `/capacity`. |
| Page class | Per-page tuple `(has_text_layer, has_images, has_tables)` from R2. |
| Shard | A contiguous page range sent to one backend in one request. The backend may sub-chunk it internally (`plan_docling`). |
| Safe procs | `min(effective_cpus, floor((free_mem − reserve) / (WORKER_BASE_BYTES + chunk_pages·PER_PAGE_BYTES)))`. |
| Coordinator | Code in the worker's converter child that splits, dispatches, retries and merges shards. |

## Requirements

### Requirement 1: Unified, correlated logs in Grafana

**User story:** As an operator, I want to type one `job_id` into Grafana and see the whole conversion across every machine, so I can tell where the time went.

#### Acceptance Criteria

1. Promtail relabelling SHALL set `container` from `__meta_kubernetes_pod_container_name` and `pod` from the pod name. The json stage SHALL map `logger: logger` and parse `ts` with a `timestamp` stage.
2. `job_id`, `doc_id`, `doc_sha8` and `run_id` SHALL be extracted as **structured metadata**, not labels. Of the fields extracted from the log line, only `level` and `kind` SHALL become labels, and each SHALL have no more than about 50 values per day. Kubernetes discovery labels (`namespace`, `service`, `container`, `pod`, `host`, `stream`) are exempt: `pod` takes about 1,440 values per day because the per-minute `infra` cron creates a new pod each run, which is the same stream count as before (the old `container` label held the pod name).
3. Promtail SHALL drop gunicorn access lines for `/metrics` and `/health`, and arq's duplicate plain-text console lines, either at source or in the pipeline.
4. Promtail SHALL tolerate `dedicated=docling:NoSchedule`, so that it runs on docling-1 and pushes to `loki.infra:3100` over the private network.
5. The `docling-node-controller` `tick` container's output SHALL reach Loki. Line counts SHALL be non-zero over one hour of ticks.
6. `client/remote.py` SHALL send `X-Job-Id`, `X-Doc-Sha8`, `X-Run-Id` and `X-Shard` headers. docling-service SHALL bind them into the log context and emit the `obs` JSON envelope (`src/pageindex_mcp/obs/formatter.py`).
7. docling-service SHALL log one `docling_chunk` line per chunk, with these fields:
   - `page_start`, `page_end`, `backend`;
   - `do_table_structure`, `do_ocr`, `tableformer_mode`;
   - `duration_s`, `peak_rss_bytes`, `outcome`.
8. Preclassify SHALL log the page-class summary at INFO: counts per class, `detection_method`, and the table and OCR page sets as compact ranges.
9. The Mac docling-service SHALL push its own logs to Loki in-process (`PAGEINDEX_LOKI_PUSH_URL`), labelled `host=mac`, `service=docling-service`, with the correlation IDs as structured metadata. The push target SHALL be reachable **only** over `tailscale0` and SHALL expose only the push and ready endpoints. `service.log` SHALL be rotated in-process. After a one-time `install.sh`, no step on the Mac or on portfolio SHALL be manual: the Mac updates itself from `master` when idle, and infra changes apply on merge.
10. A provisioned Grafana dashboard "PageIndex Logs" SHALL have a `$job_id` variable and panels for:
    - the full log stream;
    - a per-chunk timeline table (from `docling_chunk`);
    - error lines.
11. The extra RAM on portfolio SHALL be ≤ 150 MiB, measured.

### Requirement 2: Correct per-page classification

**User story:** As the pipeline, I need to know per page whether it has a text layer, images or tables, so that I only pay for the models the page needs.

#### Acceptance Criteria

1. `pdf-inspector` (the `pdf-inspection` extra) SHALL be installed in the worker and docling-service images. Alternatively, detection SHALL no longer depend on it. The choice is recorded in the design.
2. `_page_has_ruled_table` SHALL handle the tuple form of `get_cdrawings()` items, and its per-page body SHALL sit inside the per-page `try`. One bad page SHALL mark only that page positive (the safe default) and SHALL NOT abort the whole detection.
3. Table detection SHALL use PyMuPDF `find_tables()` as the primary signal, with ruled lines and column alignment as secondary signals. The column-alignment signal SHALL be tightened so that it no longer marks more than about 110% of the `find_tables()` positive pages on the pocketbook census. The ±1 neighbour padding stays.
4. Image detection SHALL mark a page when a raster image covers ≥ `PAGECLASS_IMAGE_AREA_MIN` (default 2%) of the page area.
5. Text-layer detection SHALL mark a page as having text when it has ≥ `PAGECLASS_TEXT_MIN_CHARS` (default 50) extractable characters and passes the existing garble screen.
6. The result SHALL be `PreClassification.page_classes`: one entry per page, carried through the handshake JSON and the remote request body as run-length ranges.
7. A detection failure SHALL fall back to "everything on" (tables and OCR) for the affected pages, and SHALL log at **WARNING**.
8. The pocketbook census SHALL be reproducible by a committed script: `scripts/page_class_census.py`.

### Requirement 3: Page-class-aware chunking (TableFormer and OCR per chunk)

**User story:** As the pipeline, I want chunks to be uniform in which models they need, so that the per-chunk switches actually skip work.

#### Acceptance Criteria

1. Chunking SHALL cut the page sequence into **contiguous runs of equal needs**, where needs are `needs_tables = has_tables` and `needs_ocr = not has_text_layer or has_images or has_tables` (UD2). Runs are then split to ≤ `MAX_CHUNK_PAGES`.
2. A run shorter than `MIN_CHUNK_PAGES` SHALL be absorbed into a neighbour whose needs already **cover** its own. If no neighbour covers it, it SHALL be absorbed into a neighbour anyway only when the number of neighbour pages that would gain a model is ≤ `MIN_CHUNK_PAGES` (fewer upgraded pages wins; tie → left); otherwise it stays a chunk of its own. The merged chunk takes the **union** of needs, which is safe because it never removes a model a page needs.
   > **Amendment (2026-09-27, P2 build).** The original wording ("absorbed into a neighbour", union of needs) collapsed the real pocketbook (292 pages) to one all-on run: a 4-page table run was merged into the 13-page no-model run at pages 275–287, then cascaded, so 0 pages skipped any model. The covering-neighbour rule keeps pages 275–287 model-free; the ≤ `MIN_CHUNK_PAGES` upgrade bound stops isolated table or image pages among text from each becoming a 1-page chunk (each chunk is a child that reloads the Docling models).
3. Each chunk SHALL build its pipeline with `do_table_structure = chunk.needs_tables` and `do_ocr = chunk.needs_ocr`. `DOCLING_DO_OCR` becomes a global override: `0` = page-class driven (new default), `1` = force on, `off` = force off. The Mac's `run.sh` value SHALL be aligned.
4. `force_full_page_ocr` (the recovery and HR5 escalation) SHALL still force OCR on every page it covers, whatever the page class.
5. The merge SHALL stay in page order. Picture `page` fields and table-page indices SHALL be rebased per chunk, as today.
6. Kill switch: `PAGECLASS_CHUNKING=0` restores today's chunking (uniform chunks, table flag only).

### Requirement 4: Measured TableFormer and OCR levers

**User story:** As the owner of the showcase, I want numbers, not guesses, before changing model defaults.

#### Acceptance Criteria

1. `DOCLING_TABLEFORMER_MODE` (`accurate` | `fast`, default `accurate`) SHALL replace the hardcoded ACCURATE.
2. A benchmark SHALL run on the active remote with a fixed plan. It covers:
   - **Documents:**
     - the pocketbook;
     - one scanned or image-heavy PDF;
     - one Arabic or garble-prone PDF from the corpus.
   - **Arms:**
     - baseline: today's settings;
     - R3 on;
     - R3 plus FAST.
   - Each arm runs 2× and the median is reported.
3. For each arm the benchmark SHALL report:
   - seconds of extraction and seconds per page;
   - the `validate_tree` verdict;
   - garble-screen results;
   - a table-cell diff against the baseline arm (cell-count delta and changed-cell ratio on the 262 pocketbook table pages).
4. FAST becomes the default only if the changed-cell ratio is ≤ 2%, no verdict gets worse, and garble does not increase. Otherwise it stays opt-in.
   > **Amendment (2026-09-27, task 5.6 decision).** Benchmark run on docling-1 (cpx62, 16 cores, image 8c3870d); report `audit/RFC052_CONVERSION_BENCH_2026-09-27.md`. FAST fails AC4 on every document. Its changed-cell ratio vs R3 is 73.24% (pocketbook), 11.72% (scanned) and 18.01% (Arabic), against a 2% limit. The baseline-vs-baseline noise floor is 0.00%, so the differences are real. **Decision (user, 2026-09-27): keep R3 as the default and FAST opt-in.** The defaults are unchanged:
   > - `PAGECLASS_CHUNKING=1`;
   > - `DOCLING_DO_OCR=0` (page-class driven);
   > - `DOCLING_TABLEFORMER_MODE=accurate`.
   >
   > R3 matched the baseline's tables cell for cell, and every verdict in every arm was PASS with no garbling. R3 saved only 2% on the pocketbook (742 s vs 758 s), because 275 of its 292 pages need OCR and 273 need tables. It saved nothing on the all-OCR scanned and Arabic documents; its gains are expected on text-dominant documents such as the T&C corpus. FAST was 2.4× faster on the pocketbook (314 s). Revisiting it needs a hand check of the changed tables, since AC4 measures difference, not correctness.
5. Results SHALL be written to `audit/RFC052_CONVERSION_BENCH_<date>.md`.

### Requirement 5: Capacity reporting (P3) and a Mac + docling-1 split (P5)

**User story:** As the pipeline, I want to give each backend as much of one document as it can safely convert, and never more.

**Phasing (2026-09-27):**
- **P3:** AC1–AC3, AC5 and AC8. Capacity reporting, the free-memory clamp and the page-range API, all used by the single active remote.
- **P5:** AC4, AC6, AC7 and AC10, plus R6. The split across the Mac and docling-1 running together.

#### Acceptance Criteria

1. docling-service SHALL expose `GET /capacity` (bearer-authed) with these fields:
   - `effective_cpus`: P-cores + 0.5·E-cores on macOS, cgroup quota on Linux;
   - `free_mem_bytes`: the minimum of cgroup (`memory.max − memory.current`) and host MemAvailable on Linux; `vm_stat` free + inactive + speculative on macOS;
   - `reserve_bytes`, `safe_procs`, `chunk_pages`;
   - `busy_slots` / `max_slots`;
   - `spp_ewma`: seconds per page per process, an exponentially weighted moving average over recent chunks;
   - `build_sha`.
2. `available_memory_bytes()` SHALL keep its current meaning (total, for plan sizing). The planner SHALL additionally clamp its worker count by `safe_procs` computed from **free** memory.
3. `PdfConvertRequest` SHALL accept an optional `page_start` / `page_end`: 0-based and inclusive, the convention of the `docling_chunk` records. Both are required or neither; an invalid range is a 422. The service slices with fitz and rebases the page classes to the slice. Picture pages come back in the slice's own numbering, and the P5 merge adds `page_start`. Omitting the range keeps today's whole-document behaviour.
4. The coordinator SHALL live in the worker's converter child (next to `_remote_pdf_to_markdown`). It SHALL query `/capacity` on the **named** Services of every backend that is up: `docling-service-mac` and docling-1's `docling-service`. The node controller's backend choice is read from a ConfigMap.

   It SHALL compute `rate_b = safe_procs_b / spp_b` and give each backend a contiguous initial allocation in proportion to its rate, aligned to R3 chunk boundaries. The remainder goes on a shared tail queue that backends pull from as their slots free (tail stealing).
5. **No Docling on portfolio (NG7):**
   - The coordinator SHALL NOT dispatch to any backend scheduled on portfolio.
   - The `docling-service-local` Deployment and the `docling-node.sh local on` path SHALL be removed in the infra companion PR.
   - A backend whose `safe_procs < 1` gets zero chunks.
6. **Failure handling:**
   - A failed or timed-out shard SHALL be retried once, on the other backend where one exists. Only then does the whole conversion fail.
   - All shards share the single `DOCLING_SERVICE_TIMEOUT_S` deadline.
   - A shard whose backend reports `busy_slots == max_slots` SHALL NOT be queued behind it.
7. **HR3:** a backend is eligible for a document only if its routing policy allows it.
   - The Mac is **never** eligible when `PII_CORPUS=true`.
   - docling-1 (in-cluster) SHALL be eligible for PII documents, replacing today's blanket block (the HR3 gate in `_remote_pdf_to_markdown`; the old `:126-131` citation drifted).
8. **Build skew:** a backend whose `build_sha` differs from the coordinator's expected SHA SHALL be excluded, with a WARNING.
9. *Removed 2026-09-27 (docling-local dropped, NG7).*
10. Kill switch: `DOCLING_SPLIT_ENABLED=0` (default for the first deploy) sends the whole document to `docling-active`, as today.

> **Proposed amendments (2026-10-04, P5 build; awaiting the user's approval at checkpoint 12).**
> - **A-P5-1 (AC4, live backend set):** the coordinator does not read a ConfigMap. It probes `/capacity` on every backend in `DOCLING_SPLIT_BACKENDS` (default `mac=http://docling-service-mac:8090,node=http://docling-service:8080`). A backend that refuses the connection is absent for 60 s. With docling-1 down its Service has no endpoints, so the probe fails in milliseconds. The controller's job (task 11.1) is only to keep docling-1 up beside the Mac (`DOCLING_SPLIT_KEEP_NODE=1`). No new cross-repo contract or RBAC is needed.
> - **A-P5-2 (AC8, expected SHA):** the expected build is `DOCLING_EXPECTED_BUILD_SHA` when set, else the `build_sha` of the backend `docling-active` routes to (Redis `docling:backend` target), else the fastest backend's. The worker's own `BUILD_SHA` cannot serve: the worker image is rebuilt on every merge, docling-service only when its code changes. On 2026-10-04 the worker ran 44d5c13 and the Mac 16b3690, so a worker-SHA rule would exclude every backend. The effect is that all shards of one document come from one build.
> - **A-P5-3 (allocation edge cases):** with exactly one eligible backend, the whole document goes to it unsliced (no extra joins). Below `DOCLING_SPLIT_MIN_PAGES` (default 20), or when nothing is eligible, the call takes today's `docling-active` path unchanged.
> - **A-P5-4 (AC7, HR3 rule):** under `PII_CORPUS=true` a backend is eligible only if all three hold. First, neither its configured name nor the identity it reports in `/capacity` is `mac` (case-insensitive). Second, it is listed in `DOCLING_SPLIT_PII_BACKENDS`. Third, its URL is a cluster-internal Service address: a bare name, `*.svc` or `*.svc.cluster.local`. This per-backend rule replaces `require_zdr_compliance` for split calls, because that allow-list covers LLM APIs only and would block docling-1 too. The unsplit `docling-active` path keeps the blanket block.
> - **A-P5-5 (AC1-AC5, scheduling; proposed 2026-10-04 after the 11.5 parity run, approved by the user the same day; implemented, parity re-run pending):** replace up-front per-backend shares with a shared queue of small chunks. 11.5 measured split on at 1189 s against 619 s off: docling-1 ran its 55-page shard as one process (`PARALLEL_MIN_PAGES` = 60 makes any request of <= 60 pages single-process), at ~17.7 s/page on 16 cores, while the Mac sat idle for ~10 min. The rule becomes:
>   1. The coordinator cuts the document into page-class-aligned chunks targeting 5-20 pages (a page-class boundary can leave a shorter one), about two per slot across the backends, and keeps them in one queue. `DOCLING_SPLIT_INITIAL_FRAC` is removed.
>   2. Each eligible backend keeps up to `min(safe_procs, slice_slots)` chunks in flight. docling-service runs up to `slice_slots` (`DOCLING_SLICE_SLOTS`) page ranges of <= 20 pages at once, each as one process with `cpus // slice_slots` threads, so the `PARALLEL_MIN_PAGES` single-pass rule never applies to a slice. `max_slots` is unchanged: the running chunks hold every whole-conversion slot as a group, and `/capacity` clamps `slice_slots` to what free memory fits for a 20-page slice.
>   3. A backend that finishes a chunk takes the next one, so the split follows measured speed and the `spp_ewma` prior no longer sizes anyone's share.
>   4. When the queue is empty and a backend is idle, it also runs a copy of the oldest in-flight chunk; the first result wins and the other is cancelled.
>   5. The coordinator gives every chunk one presigned URL, renewed after 10 minutes, and docling-service keeps each downloaded PDF per URL (the full signed URL, so MinIO still authorises each one). Chunks sharing a URL cost one download per backend; a renewal costs one more.
>
>   Parity (R6) is unchanged: verdict equal, node_count within ±3%, join heading shifts logged. Expected (not measured): ~270 s of conversion for the pocketbook against ~400 s for the Mac alone.

### Requirement 6: Split quality parity

**User story:** As the owner of the tree quality bar (HR5), I need splitting to never lower quality silently.

#### Acceptance Criteria

1. With the split on, the pocketbook SHALL produce the same verdict as with it off.
2. The node count SHALL stay within ±3%, and heading-level changes SHALL be limited to shard joins, each counted and logged.
3. `validate_tree()` still runs before `save_doc` (HR5). A split never bypasses it.

### Requirement 7: Parallel table capture on portfolio, persisted, never discarded (accepted 2026-09-27, P4)

**User story:** As the owner of the corpus, I want every table PyMuPDF finds to be kept as searchable data, found using the cores the worker actually has, without risking an OOM.

Measured on 2026-09-27:
- `find_tables()` costs 0.5–0.84 s/page and peaks at about 87 MiB per process.
- The worker pod is limited to 2 CPU / 1536 Mi and was using 273 Mi.
- Portfolio has 4 cores (load average 4–5) and about 2 GB available.
- On pocketbook pages 101–130 the default `lines` strategy found only the ruled header box (6×2, top 7% of the page). The unruled indicator tables were missed.

#### Acceptance Criteria

1. `find_tables()` SHALL run in a process pool, not threads: PyMuPDF documents are not thread-safe, and the work holds the GIL. Each process SHALL open the PDF itself and take a contiguous page range.
2. Pool size SHALL be `min(cgroup CPU quota, floor((cgroup free − TABLES_RESERVE_BYTES) / TABLES_PROC_BYTES), ceil(pages / TABLES_MIN_PAGES_PER_PROC))`. The size is re-computed per document and is ≥ 1.
   - `TABLES_PROC_BYTES` defaults to 256 MiB (about 3× the measured peak). **Amended 2026-10-05 (11.7 re-run):** 192 MiB, with `TABLES_RESERVE_BYTES` 128 MiB (was 512). Once capture ran to its deadline (A-P5-5 follow-up), 256 + 512 held it to one process in the 1536 MiB worker pod (~614 MiB free beside the converter child and arq). That made capture the job's critical path at +27% wall time, against item 3's 5%. 192 MiB is 1.5× the highest capture RSS since seen (129 MiB, pocketbook), and two processes at that cap still leave ~200 MiB of the pod.
   - A process that exceeds it is killed. Its pages are marked `capture_failed`, and the document still converts.
3. Capture SHALL run **concurrently with remote conversion**, not ahead of it, and SHALL NOT block dispatch. **Amended 2026-09-27 (user):** R9's signals are computed by docling-service per chunk, so the veto set is empty. The job's median wall time SHALL NOT grow by more than 5% on the pocketbook.
4. Every table SHALL be persisted to `processed/<doc_id>.tables.json` with these fields:
   - `table_id`, `page`, `bbox`, `rows`, `cols`;
   - `header`, `cells` (text per cell), `markdown`;
   - `source` (`pymupdf_find_tables` or `docling_tableformer`) and `strategy`;
   - `coverage`, the share of the page's text characters inside the table;
   - `node_id`, the tree node whose page range contains it;
   - a nearby caption, if any, and a generated `description` (R8).
5. Where TableFormer also produced a table on the same page, **both** SHALL be kept, linked by overlapping bbox. Neither SHALL be dropped.
6. **HR2:** `processed/*.tables.json` SHALL join `_ERASURE_MANIFEST` and be purged by `delete_doc`. No dedicated erasure test (user decision, 2026-09-27).
7. **HR4:** the tables are the output of AGPL PyMuPDF. **User decision, 2026-09-27:** they are served through search and the MCP surface now; the legal review is deferred and tracked as an open item, not a gate.
8. Persisted table cells SHALL be usable directly by retrieval answers (RAG), so a table question is answered from stored cells without re-extracting the page at query time.

### Requirement 8: Tables are part of search (accepted 2026-09-27, P4)

**User story:** As an MCP client, I want a question answered by a table to find that table the same way a question answered by a section finds the tree node.

#### Acceptance Criteria

1. Each table SHALL appear in the slim search tree as a child of its `node_id`, with type `table`, a title (caption or header row) and a `description` of no more than about 30 tokens.
2. Descriptions SHALL be generated like node summaries, through the same LLM tier and routing as the tree. **HR3:** PII corpora go only through the ZDR tier.
3. The table nodes SHALL add no more than `TABLES_SEARCH_TOKEN_BUDGET` tokens (default 8k) to the search prompt. Over budget, low-coverage tables are dropped from the *search view* only, never from storage.
4. The page-content tool SHALL return a table's `markdown` when a search selects it.
5. A table-backed question set (at least 10 pocketbook questions) SHALL not answer worse than without table nodes.

### Requirement 9: Signal-driven TableFormer and OCR bypass (accepted 2026-09-27, P4)

**User story:** As the pipeline, I want the tables and text I have already extracted to switch off TableFormer, OCR or both on pages where they add nothing.

#### Acceptance Criteria

1. **Skip OCR** on a page that has a clean text layer (R2 AC5), no raster image ≥ `PAGECLASS_IMAGE_AREA_MIN`, and, if it has tables, clean non-empty `find_tables()` cell text. This replaces D5's rule that table pages keep OCR.
2. **Skip TableFormer** on a page with zero `find_tables()` tables and no ruled or column-alignment signal. This is the existing R3 path.
3. ~~**Replace TableFormer with the `find_tables()` grid** on a page only when the table is ruled.~~ **Dropped 2026-09-28 (user)** after task 9.7: with the page-level amendment below it fired on real ruled tables and changed 13.33% (Unfall) and 20.10% (GHV) of cells against TableFormer, against a 2% gate. The code, `TABLES_TRUST_BYPASS` and `TABLES_TRUST_COVERAGE` are removed.
4. **Skip both** OCR and TableFormer when AC1 and AC2 hold for a whole R3 chunk.
5. Each bypass SHALL be logged per chunk in the `docling_chunk` record (`bypass: ocr|tableformer|both`), and each SHALL have its own kill switch.
6. `force_full_page_ocr` (HR5 recovery) SHALL override the OCR bypass (AC1). **Amended 2026-09-27 (user):** it does not re-enable R3's no-table TableFormer skip (AC2).
7. Every forced conversion SHALL record its recovery context: the document, why force was set, and the first pass's per-chunk OCR/TableFormer decisions and TableFormer pages. The recovery request carries the prior pass. The cases SHALL be measured and tabulated before any policy acts on that context (user, 2026-09-27).

> **Amendment (2026-09-28, user, task 9.7).** AC1 and AC3 were judged per table, so one small table vetoed a page that qualifies as a whole. Across `doc_store` AC3 fired on 0 pages, and AC1 on none of Unfall's 3 pages: each carries a 9-cell header box 22% filled holding 3% of the text, beside a table that holds 72-87%. Both criteria now judge the page's tables together:
> - **AC1:** the tables' non-empty cell share is cell-weighted across the page (≥ `TABLES_OCR_BYPASS_MIN_FILLED`), and every table *with text* passes the garble screen; an empty ruled box is not screened.
> - **AC3:** every table is ruled, and the union of the table bboxes holds ≥ `TABLES_TRUST_COVERAGE` of the page's text; the no-alignment-outside condition is unchanged.
>
> Blast radius (local census, 2026-09-28): Unfall AC1 0→2 of 3 pages, AC3 0→3; GHV AC3 0→1 (AC1 still fails the garble screen); Haftpflicht, Reitlehrer and the pocketbook unchanged. Both switches stay off by default; the 9.7 grid bench is the gate.
>
> **AC3 dropped (2026-09-28, user).** The 9.7 bench on the amended rule failed (see AC3 above), so grid replacement is removed; the AC1 amendment stands.

## Decision Summary

| ID | Decision | Rationale |
|---|---|---|
| D1 | Keep promtail and Loki; fix relabels; structured metadata for IDs | Stack already works and costs ~185 Mi; IDs as labels would blow up cardinality. |
| D2 | Mac docling-service pushes logs in-process to a push-only nginx gateway bound to portfolio's Tailscale IP (`100.120.146.20:3100`); the Mac self-updates from `master` when idle; infra auto-applies on push | User wants no manual steps (2026-09-26). Binding to the Tailscale address avoids host iptables and NodePorts entirely; Loki has no auth, so the gateway allows only `POST /loki/api/v1/push` and `GET /ready`. Replaces the earlier Alloy + NodePort + iptables design. |
| D3 | Correlate via HTTP headers into docling-service's `obs` envelope | Reuses the existing schema v1; no new tracing system. |
| D4 | `find_tables()` as the primary table signal; tighten column alignment | The census shows column alignment at 264/292 is too loose to be useful. |
| D5 | OCR need = no text layer, or images, or tables (UD2) | Follows the user's rule. Conservative: table pages keep OCR until R4 shows it is safe to drop. |
| D6 | Page-class run-length chunking with union absorption | Per-page model switches don't exist in a single Docling call; making each chunk uniform in its needs gives page-level effect at chunk granularity. |
| D7 | TableFormer mode becomes config; default changes only on R4 evidence | Speed vs table fidelity is a quality decision (UD4).  **Resolved 2026-09-27:** R4 benchmark failed FAST on all three documents (11.7–73.2% changed cells vs a 2% bar); `accurate` stays the default and R3 stays on (R4 AC4 amendment). |
| D8 | Coordinator in the worker's converter child | It already has presigning, the HR3 gate and the retry policy; a Mac coordinator would create a single point of failure and a callback path into the cluster. |
| D9 | Proportional initial split plus tail stealing | A static split can't absorb a slow shard; pure stealing of small chunks wastes the Mac's 14-process parallelism. |
| D10 | No Docling on portfolio; `docling-local` dropped (2026-09-27) | Portfolio has 4 cores and about 2 GB free, and would get zero chunks almost always; the gating it needed was the RFC's main OOM risk. |
| D11 | One remote at a time until P5; Mac + docling-1 together in P5 | UD1; the N-backend algorithm makes P5 a configuration change on top of P3. |
| D12 | Stacked PRs `ICR-97-rfc52-<slug>` | UD3; matches RFC-050/051. |
| D13 (accepted 2026-09-27) | Keep every `find_tables()` result as searchable data; run it in a memory-sized process pool in the worker, overlapped with remote conversion | User, 2026-09-27: extracted table data must not be discarded. Processes because PyMuPDF is not thread-safe; overlap because conversion (about 800 s) dwarfs capture (about 125 s with 2 processes). |
| D14 (accepted 2026-09-27) | Tables become child nodes of the search tree, with short descriptions and a token budget | Reuses the one-call tree search and adds no second index; the budget protects search latency (about 96k tokens today). |
| D15 (accepted 2026-09-27; grid replacement dropped 2026-09-28) | OCR and TableFormer bypass from `find_tables()` signals; grid replacement off until benchmarked, then dropped when it failed the 2% gate | TableFormer is about 93% of conversion time (28.7 of 28.9 s/page, 1 thread); the `lines` strategy missed the unruled pocketbook tables, so replacing TableFormer needs evidence. |

## Implementation Plan

### Sequencing (stacked PRs)

| Phase | Branch | Scope | Depends on |
|---|---|---|---|
| P0 | `ICR-97-rfc52-log-correlation` | R1: promtail fixes, headers, JSON logging in docling-service, chunk logs, dashboard, docling-1 toleration; then `ICR-97-rfc52-log-shipping-automation`: Loki Tailscale gateway, infra auto-apply, Mac in-process push and auto-updater | — |
| P1 | `ICR-97-rfc52-page-class-detection` | R2: detector repair, `find_tables()`, image and text-layer signals, census script, images get the extra | P0 (to observe it) |
| P2 | `ICR-97-rfc52-page-class-chunking` | R3 plus R4: run-length chunking, OCR by page class, TableFormer mode config, benchmark | P1 |
| P3 | `ICR-97-rfc52-capacity-split` | R5 AC1–3, 5, 8: `/capacity`, free-memory clamp, page-range API, build-skew check, docling-local removal (infra) | P2 |
| P4 | `ICR-97-rfc52-table-capture` | R7 + R8 + R9: parallel table capture, `tables.json` persistence and erasure, table nodes in search, signal-driven bypasses | P2 (benchmark harness), P1 |
| P5 (later) | `ICR-97-rfc52-mac-docling1-split` | R5 AC4, 6, 7, 10 plus R6: Mac + docling-1 together, coordinator, tail stealing, retry and re-route, HR3 eligibility, parity check | P3 |

P0 ships first because every later acceptance criterion is verified through its logs. P3 ships with `DOCLING_SPLIT_ENABLED=0` and is switched on only after the R6 parity run.

### Effort Estimate

| Phase | Effort |
|---|---|
| P0 | ~1 day (half of it cluster config and Mac operator steps) |
| P1 | ~1 day |
| P2 | ~1.5 days (the benchmark runtime dominates) |
| P3 | ~3 days |

## Test Strategy

- **Unit tests** (`make test` only, bounded, foreground):
  - relabel and pipeline config snapshot;
  - tuple-form `get_cdrawings` fixture;
  - page-class run-length and absorption properties: union never drops a need; order is preserved;
  - allocation math: zero-proc backends get nothing, allocations sum to N, and chunk boundaries are respected;
  - HR3 eligibility table;
  - shard retry and re-route.
- **Integration:** a stub `/capacity` plus a range-convert server that exercises the coordinator's stealing and deadline logic without Docling.
- **Live:**
  - the R4 benchmark;
  - the R6 parity run on the pocketbook;
  - a forced-low-memory run on the active remote showing the planner clamps its process count (no OOM events in `kubectl get events`), and a `docling_chunk` backend histogram with no portfolio backend.

## Risks

| Risk | Mitigation |
|---|---|
| Conversion steals memory from cluster pods (2026-09-17 class) | Removed at source: no Docling on portfolio (NG7, D10). R7 table capture runs in the worker's own cgroup with a per-process memory cap. |
| More joins cause heading re-levelling and outline loss (RFC-027 D7) | R6 parity bar; shard boundaries align to page-class chunk boundaries (no extra joins beyond R3) |
| Page-class OCR skip hides a corrupt text layer | Garble screen in the text-layer test (R2 AC5); HR5 `force_full_page_ocr` escalation unchanged (R3 AC4) |
| FAST TableFormer degrades tables silently | R4 AC4 gate; default unchanged without evidence |
| Loki exposed beyond the tailnet, or its query/delete API exposed on it | Gateway listens only on the Tailscale address and forwards only push and ready (403 otherwise); Tailscale ACLs |
| Loki keeps doc identifiers after a DSR delete (HR2) | `delete_doc` step 8 `loki_logs` files in-cluster `/loki/api/v1/delete` requests for `doc_id`, `doc_sha8` and `doc_name_sha8` (`PAGEINDEX_LOKI_URL`); 72 h retention is the backstop; unset URL → `partial_purge` |
| Loki disk: host at 83% | 72 h retention kept; drop noise (R1 AC3); alert at 90% |
| The Mac's `BLOCK_PRIVATE_URLS=1` needs public presigned URLs | Unchanged from today's path; the coordinator presigns per shard with the same client |

## Consequences

- Until P5, one document goes to one active remote (the Mac, else docling-1). From P5, it can use the Mac and docling-1 together, and later nodes join by configuration.
- The measurable speed-up for the showcase is expected to come from P2 (OCR by page class, possibly FAST TableFormer), not from P3, on today's hardware. This RFC states that openly rather than promise a split speed-up.
- Every conversion becomes explainable per chunk in Grafana.

## Related Findings (tracked, out of scope)

- `expected_script` is sent by `client/remote.py:134-140` but silently dropped by `PdfConvertRequest`.
- All six pocketbook runs on 2026-09-25 failed with `RemoteProtocolError`, `ReadTimeout` or a converter timeout, then fell through to a pymupdf4llm fallback that is not installed.
- One Mac request failed with `unexpected keyword argument 'do_table_structure'`. The build-skew check covers only one URL; R5 AC8 widens it.
- `memory_admission` fails open after 120 s.

## Traceability

| Requirement | Design | Tasks |
|---|---|---|
| R1 | [[design-rfc052-observable-capacity-weighted-conversion#log-pipeline]] | P0 (1.x) |
| R2 | [[design-rfc052-observable-capacity-weighted-conversion#page-classifier]] | P1 (2.x) |
| R3, R4 | [[design-rfc052-observable-capacity-weighted-conversion#page-class-chunker]] | P2 (3.x) |
| R5, R6 | [[design-rfc052-observable-capacity-weighted-conversion#split-coordinator]] | P3 (4.x) |
