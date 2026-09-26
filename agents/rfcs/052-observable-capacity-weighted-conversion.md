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
  - **Remote:** exactly one active remote at a time: the **Mac if present, else docling-1**. They never run together; the existing `docling-node-controller` rule stays. Running both is deferred to a later horizontal-scaling experiment.
  - **Local:** `docling-local` on portfolio is a **third, opportunistic** backend. It takes chunks **only when it has measured free memory** and otherwise gets nothing. No OOM risk is acceptable.
- **UD2. OCR by page.** A page that has a text layer, no images and no tables gets **no OCR**.
- **UD3. Delivery.** One RFC delivered as stacked PRs. Branches share the prefix `ICR-97-rfc52-` with a distinct slug per phase (see [Implementation Plan](#implementation-plan)).
- **UD4. TableFormer mode.** FAST vs ACCURATE is benchmarked in this RFC. The default changes only if quality holds.

### Relationship to Prior RFCs

- [[RFC-050]] R7 (selective table structure): this RFC **repairs** R7 (F1) and extends its per-chunk granularity into page-class chunking (R3).
- [[RFC-050]] D1/D2 (offload-aware admission, `MAX_JOBS`): R5's local shards must hold the `pageindex:admission` reservation so the gate and the local backend don't double-book memory.
- [[RFC-027]] D7 (chunking loses the outline and re-levels headings at joins): every new join R3 and R5 add carries the same cost. It is measured in R4/R6.
- [[RFC-046]] / HR5 (OCR attribution, garbling): R3's OCR skip must not remove the `force_full_page_ocr` escalation path (`client/recovery.py:453-483`).
- [[RFC-051]] and the `docling-node-controller`: the controller keeps choosing the active remote. This RFC does not change that choice.

## Goals

- **G1 (observability):** For one `job_id`, Grafana shows every log line across MCP server, worker, converter child, node controller, docling-1 and the Mac. This includes per-chunk page range, backend, table flag, OCR flag, duration and peak RSS.
- **G2 (page-class correctness):** Every text-based PDF gets a working per-page classification: text layer, images, tables. TableFormer and OCR are enabled per chunk from it, so that a text-layer page with no image and no table runs neither (UD2).
- **G3 (measured levers):** Measure seconds per page for OCR on/off (page-class) and TableFormer FAST vs ACCURATE on the pocketbook plus two contrast documents. Adopt a default only where the quality gates hold.
- **G4 (capacity-weighted split):** One document's chunks are split between the active remote (Mac or docling-1) and `docling-local`, weighted by each backend's **live** free memory, effective CPU and measured speed. `docling-local` gets zero chunks whenever its safe budget is below one process. The algorithm is written for N backends so that later horizontal scaling needs configuration, not code.
- **G5 (no OOM):** No step in this RFC can push portfolio below its reserved headroom. A local chunk is refused, never attempted, when the budget is insufficient.

## Non-Goals

- **NG1:** Running the Mac and docling-1 concurrently. Deferred to a horizontal-scaling experiment (UD1).
- **NG2:** Resizing portfolio or adding nodes. The split must be correct at today's size and pick up capacity automatically later.
- **NG3:** Replacing promtail with Alloy in-cluster, or changing Loki storage or retention. Promtail 3.0 stays; the Mac uses Alloy only because promtail is not packaged for launchd.
- **NG4:** Batch or multi-document throughput and KEDA changes. KEDA stays paused.
- **NG5:** A visual table detector (RFC-050 D9/R8). R2 uses PyMuPDF `find_tables()` plus fixed heuristics only.
- **NG6:** Routing PII documents to the Mac. HR3 still holds (R5 AC7).

## Glossary

| Term | Definition |
|---|---|
| Active remote | The single off-portfolio Docling backend the node controller routes `docling-active` to: the Mac, else docling-1. |
| docling-local | `docling-service-local` Deployment pinned to portfolio (today replicas 0, limits 2 CPU / 3584 Mi). |
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
2. `job_id`, `doc_id`, `doc_sha8` and `run_id` SHALL be extracted as **structured metadata**, not labels. Only `level`, `kind` and `service` SHALL be labels. No label SHALL have more than about 50 values per day.
3. Promtail SHALL drop gunicorn access lines for `/metrics` and `/health`, and arq's duplicate plain-text console lines, either at source or in the pipeline.
4. Promtail SHALL tolerate `dedicated=docling:NoSchedule`, so that it runs on docling-1 and pushes to `loki.infra:3100` over the private network.
5. The `docling-node-controller` `tick` container's output SHALL reach Loki. Line counts SHALL be non-zero over one hour of ticks.
6. `client/remote.py` SHALL send `X-Job-Id`, `X-Doc-Sha8`, `X-Run-Id` and `X-Shard` headers. docling-service SHALL bind them into the log context and emit the `obs` JSON envelope (`src/pageindex_mcp/obs/formatter.py`).
7. docling-service SHALL log one `docling_chunk` line per chunk, with these fields:
   - `page_start`, `page_end`, `backend`;
   - `do_table_structure`, `do_ocr`, `tableformer_mode`;
   - `duration_s`, `peak_rss_bytes`, `outcome`.
8. Preclassify SHALL log the page-class summary at INFO: counts per class, `detection_method`, and the table and OCR page sets as compact ranges.
9. The Mac SHALL ship `~/docling-service/logs/service.log` to Loki through Grafana Alloy (launchd), labelled `host=mac`, `service=docling-service`. The push target SHALL be reachable **only** over `tailscale0`. `service.log` SHALL be rotated with newsyslog.
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
2. A run shorter than `MIN_CHUNK_PAGES` SHALL be absorbed into a neighbour. The merged chunk takes the **union** of needs, which is safe because it never removes a model a page needs.
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
5. Results SHALL be written to `audit/RFC052_CONVERSION_BENCH_<date>.md`.

### Requirement 5: Capacity reporting and a capacity-weighted, OOM-safe split

**User story:** As the pipeline, I want to give each backend as much of one document as it can safely convert, and never more.

#### Acceptance Criteria

1. docling-service SHALL expose `GET /capacity` (bearer-authed) with these fields:
   - `effective_cpus`: P-cores + 0.5·E-cores on macOS, cgroup quota on Linux;
   - `free_mem_bytes`: the minimum of cgroup (`memory.max − memory.current`) and host MemAvailable on Linux; `vm_stat` free + inactive + speculative on macOS;
   - `reserve_bytes`, `safe_procs`, `chunk_pages`;
   - `busy_slots` / `max_slots`;
   - `spp_ewma`: seconds per page per process, an exponentially weighted moving average over recent chunks;
   - `build_sha`.
2. `available_memory_bytes()` SHALL keep its current meaning (total, for plan sizing). The planner SHALL additionally clamp its worker count by `safe_procs` computed from **free** memory.
3. `PdfConvertRequest` SHALL accept an optional `page_start` / `page_end`. The service slices with fitz and rebases pictures and page classes. Omitting the range keeps today's whole-document behaviour.
4. The coordinator SHALL live in the worker's converter child (next to `_remote_pdf_to_markdown`). It SHALL query `/capacity` on the **named** Services:
   - the active remote, resolved from `docling-active`'s current endpoint;
   - `docling-service-local`.

   It SHALL compute `rate_b = safe_procs_b / spp_b` and give each backend a contiguous initial allocation in proportion to its rate, aligned to R3 chunk boundaries. The remainder goes on a shared tail queue that backends pull from as their slots free (tail stealing).
5. **OOM safety for docling-local:**
   - (a) A zero-chunk allocation is the normal outcome when `safe_procs < 1`.
   - (b) `/capacity` is re-read before **every** local shard; a failing check returns the shard to the queue for the remote.
   - (c) A local shard SHALL hold a `pageindex:admission` reservation for its planned peak for its whole lifetime.
   - (d) The docling-local pod's memory limit SHALL equal its planned peak, so an overrun kills the pod, not the host.
   - (e) `PORTFOLIO_RESERVE_BYTES` (default 1.5 GiB) of host MemAvailable is never budgeted.
6. **Failure handling:**
   - A failed or timed-out shard SHALL be retried once, on the other backend where one exists. Only then does the whole conversion fail.
   - All shards share the single `DOCLING_SERVICE_TIMEOUT_S` deadline.
   - A shard whose backend reports `busy_slots == max_slots` SHALL NOT be queued behind it.
7. **HR3:** a backend is eligible for a document only if its routing policy allows it.
   - The Mac is **never** eligible when `PII_CORPUS=true`.
   - In-cluster backends (docling-1, docling-local) SHALL be eligible for PII documents, replacing today's blanket block at `client/remote.py:126-131`.
8. **Build skew:** a backend whose `build_sha` differs from the coordinator's expected SHA SHALL be excluded, with a WARNING.
9. `docling-local` SHALL be kept at **replicas 1 only when idle RSS ≤ 400 MiB**, measured in Phase 3. Otherwise it stays at replicas 0, and the coordinator treats it as absent.

   `docling-node.sh local on`'s 16 GB guard SHALL be replaced by the `/capacity` check.
10. Kill switch: `DOCLING_SPLIT_ENABLED=0` (default for the first deploy) sends the whole document to `docling-active`, as today.

### Requirement 6: Split quality parity

**User story:** As the owner of the tree quality bar (HR5), I need splitting to never lower quality silently.

#### Acceptance Criteria

1. With the split on, the pocketbook SHALL produce the same verdict as with it off.
2. The node count SHALL stay within ±3%, and heading-level changes SHALL be limited to shard joins, each counted and logged.
3. `validate_tree()` still runs before `save_doc` (HR5). A split never bypasses it.

## Decision Summary

| ID | Decision | Rationale |
|---|---|---|
| D1 | Keep promtail and Loki; fix relabels; structured metadata for IDs | Stack already works and costs ~185 Mi; IDs as labels would blow up cardinality. |
| D2 | Mac ships logs with Alloy over Tailscale to a Loki NodePort limited to `tailscale0` | Loki has no auth. The Hetzner firewall stays closed publicly; firewall and host changes are operator steps, and Claude does not run hcloud mutations. |
| D3 | Correlate via HTTP headers into docling-service's `obs` envelope | Reuses the existing schema v1; no new tracing system. |
| D4 | `find_tables()` as the primary table signal; tighten column alignment | The census shows column alignment at 264/292 is too loose to be useful. |
| D5 | OCR need = no text layer, or images, or tables (UD2) | Follows the user's rule. Conservative: table pages keep OCR until R4 shows it is safe to drop. |
| D6 | Page-class run-length chunking with union absorption | Per-page model switches don't exist in a single Docling call; making each chunk uniform in its needs gives page-level effect at chunk granularity. |
| D7 | TableFormer mode becomes config; default changes only on R4 evidence | Speed vs table fidelity is a quality decision (UD4). |
| D8 | Coordinator in the worker's converter child | It already has presigning, the HR3 gate and the retry policy; a Mac coordinator would create a single point of failure and a callback path into the cluster. |
| D9 | Proportional initial split plus tail stealing | A static split can't absorb a slow shard; pure stealing of small chunks wastes the Mac's 14-process parallelism. |
| D10 | docling-local strictly opportunistic, fails closed | UD1 and G5: zero chunks is the normal outcome on today's portfolio. |
| D11 | One remote at a time; no Mac + docling-1 concurrency | UD1; the N-backend algorithm makes it a configuration change later. |
| D12 | Stacked PRs `ICR-97-rfc52-<slug>` | UD3; matches RFC-050/051. |

## Implementation Plan

### Sequencing (stacked PRs)

| Phase | Branch | Scope | Depends on |
|---|---|---|---|
| P0 | `ICR-97-rfc52-log-correlation` | R1: promtail fixes, headers, JSON logging in docling-service, chunk logs, dashboard, docling-1 toleration, Mac Alloy (operator) | — |
| P1 | `ICR-97-rfc52-page-class-detection` | R2: detector repair, `find_tables()`, image and text-layer signals, census script, images get the extra | P0 (to observe it) |
| P2 | `ICR-97-rfc52-page-class-chunking` | R3 plus R4: run-length chunking, OCR by page class, TableFormer mode config, benchmark | P1 |
| P3 | `ICR-97-rfc52-capacity-split` | R5 plus R6: `/capacity`, page-range API, coordinator, docling-local gating, parity check | P2 |

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
  - a forced-low-memory run showing docling-local is refused (a `docling_chunk` backend histogram with 0 local shards, and no OOM events in `kubectl get events`).

## Risks

| Risk | Mitigation |
|---|---|
| docling-local steals memory from cluster pods (2026-09-17 class) | R5 AC5 (a)-(e); zero-chunk default; pod limit = planned peak; kill switch |
| More joins cause heading re-levelling and outline loss (RFC-027 D7) | R6 parity bar; shard boundaries align to page-class chunk boundaries (no extra joins beyond R3) |
| Page-class OCR skip hides a corrupt text layer | Garble screen in the text-layer test (R2 AC5); HR5 `force_full_page_ocr` escalation unchanged (R3 AC4) |
| FAST TableFormer degrades tables silently | R4 AC4 gate; default unchanged without evidence |
| Loki on a Tailscale NodePort leaks if bound publicly | Interface-bound rule plus Tailscale ACL; verified with an external probe in P0 |
| Loki disk: host at 83% | 72 h retention kept; drop noise (R1 AC3); alert at 90% |
| The Mac's `BLOCK_PRIVATE_URLS=1` needs public presigned URLs | Unchanged from today's path; the coordinator presigns per shard with the same client |

## Consequences

- One document can use every backend that reports safe capacity. Today that means effectively the active remote alone, because docling-local will rarely qualify. Its value is the mechanism, which later nodes join by configuration.
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
