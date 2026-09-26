<!-- Space: CITRA -->
<!-- Title: Implementation Plan: Pipeline Acceleration & Raw Output Persistence -->
<!-- Folder: Tasks -->

---
id: "tasks-rfc050-pipeline-acceleration-raw-export"
title: "Tasks: Pipeline Acceleration & Raw Output Persistence"
type: tasks
status: draft
date: "2026-09-24"
tags:
  - tasks
  - pipeline
  - performance
  - storage
aliases:
  - "tasks-rfc050-pipeline-acceleration-raw-export"
governs:
  - "[[RFC-050]]"
---

# Implementation Plan: Pipeline Acceleration & Raw Output Persistence

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [[RFC-050]] |
| Design Document | [[design-rfc050-pipeline-acceleration-raw-export]] |
| PRD / Requirements | [[PRD]] |

## Overview

**Current contract (2026-09-25, Iter 11 — amended, D8 cascade enhancement: two-stage native detection + neighbor padding + detection_method; corrected post-review, 2026-09-24.)** Implements RFC-050 across five waves (including Wave 2b), ~20h total: (1) stage-timing instrumentation and a pre-change baseline; (2) safety and correctness work that must land before concurrency rises — cgroup-aware admission gate (D1, working-set-based, not raw usage), service-aware `MAX_JOBS` default keyed on `config.docling_offload_configured()` (D2), a per-file dedup lock now living in the **parent** (`worker/subprocess_mgr.py`'s `_run_converter_subprocess`, not `index()`) around `_run_converter_child` (D7, new), reject reason/defects in quarantine metadata (D3b, new), and the 30-day quarantine TTL (D6); (3) pointing the worker at the in-cluster `services/docling-service` pod, enabling `MAX_JOBS=2`, and running the G1 validation gate; (4) raw output persistence for persisted documents (D3, slimmed), a doc-name-recovery fix for the `.extracted.md` sidecar-only case, and `get_document(doc_id, include="raw")`. Property tests cover 1, 2, 3, 3a (now trivial), 3b, 6, 7, 8. D4 (CLI replay) and D5 (recovery optimization) are **deferred** — their task lists are kept, unmodified in substance, under **Deferred (Iter 9)** at the bottom, for a follow-up RFC to pick up. **Open item (post-review, 2026-09-24):** `uv run arq ...` run directly loads `.env`, which in this dev checkout points `DOCLING_SERVICE_URL` at Scaleway — an operator-discipline gap (use `make up`, which stays local via `.env.active`), not a code defect; see RFC-050 Risks.

<details><summary>Amendment history (Iter 1-9 pre-review, collapsed)</summary>

Iterations 1-8 built a 5-wave, 10-task-group, 37-41h plan (admission gate + concurrency, raw output + a state sidecar for replay, a CLI dry-run replay behind a fail-closed write-barrier overlay, recovery method optimization, corpus validation). See RFC-050's own amendment history for the per-decision evolution. The initial Iter 9 regeneration (pre-review) placed the D7 dedup lock inside `index()`, keyed cgroup headroom on raw `memory.current`/`usage_in_bytes`, and keyed service-mode purely on `DOCLING_SERVICE_URL` being set — all superseded by the post-review fix above.

</details>

## Tasks

- [ ] 1. Wave 1 — Stage Timing + Pre-Change Baseline

  - [x] 1.5 Stage timing split for G1 attribution (implemented 2026-09-24, uncommitted)
    - **As implemented:** `converters_cli` returns `stage_timings` in its final stdout JSON; `worker/subprocess_mgr.py` reads them and observes histogram `pageindex_stage_duration_seconds` with a `stage` label (`extraction`, `tree_build`, `recovery` — non-overlapping), added to `metrics/definitions.py` next to `UPLOAD_DURATION` (end-to-end) and `LLM_DURATION` (per LLM call). No `persist` stage.
    - One `stage_duration` decision record per stage per doc, so Task 9.1/9.2 can read them from a run without Prometheus
    - `pageindex_stage_duration_seconds` added to `FROZEN_SURFACE["metrics"]` in `scripts/gates/source_invariants.py` in the same commit
    - Unit test (`tests/test_worker.py`): one fake ingest records one observation per stage; the emitting call site and the decision record land together
    - **Open item (not done; deferred to Phase 3 with the infra, user decision 2026-09-24):** the worker's `/metrics` isn't scraped and the Redis metric bridge carries scalars only, so this histogram is invisible on `/metrics` — the `stage_duration` log events, parsed by `make g1-timings`, are the source for G1 attribution until the Phase 3 worker scrape lands
    - _Requirements: RFC-050 G1_

  - [ ] 9.1 Pre-RFC baseline timing measurement
    - Runs immediately after Task 1.5, before any Wave 2/3 change lands — the G1 protocol's baseline arm
    - **Precondition — server resize first (infra decision 2026-09-25):** the current server is resized in place (no second node) **before** this baseline arm runs, so the baseline and post (Task 9.2) arms run on the same hardware. Record the node's allocatable memory with the result; if it differs between the two arms, the G1 comparison is void and the baseline must be re-run
    - **Protocol (the G1 protocol, shared with Task 9.2):** ingest via `make ingest` (worker path, `scripts/remote_ingest_test.py` → `POST /upload/files`); local Docling (no `DOCLING_SERVICE_URL`), `MAX_JOBS=1`; KEDA pinned to 1 worker replica; one fixed `.env.active`, recorded with the result; 2 runs, take the median
    - Record per-document and total wall-clock time and the Task 1.5 per-stage split
    - _Requirements: RFC-050 G1_

  - [x] 2. Checkpoint — Wave 1
    - Verify the stage-timing histogram and `decision()` record work end-to-end on one document
    - Verify the baseline numbers are recorded and reproducible (2 runs, median taken)
    - Ask the user if questions arise before proceeding.

- [x] 3. Wave 2 — Safety Before Concurrency (D1, D2, D7, D3b, D6)

  - [x] 1.1 Cgroup-aware `available` + `MEM_ADMISSION_FLOOR_SERVICE_BYTES` (D1) (implemented 2026-09-24, uncommitted; **corrected post-review, 2026-09-24**)
    - Add `_cgroup_headroom()` to `memory_admission.py`: cgroup v2 `memory.max − working_set` when `memory.max` is numeric; v1 fallback `memory.limit_in_bytes − working_set`; `None` if neither is readable. `working_set = current − inactive_file` (v2: `inactive_file` from `memory.stat`; v1: `total_inactive_file` from the same file) — matching kubelet's headroom calculation instead of subtracting raw `memory.current`/`usage_in_bytes`; falls back to the raw-usage subtraction when the stat can't be parsed
    - Add `_available_bytes()`: `min(host MemAvailable, cgroup_headroom)` when the cgroup read succeeds, else the host value alone (today's behavior)
    - Add module constant `MEM_ADMISSION_FLOOR_SERVICE_BYTES` (default 800 MiB, `os.getenv` override) beside `MEM_ADMISSION_FLOOR_BYTES` (`memory_admission.py:23`)
    - _Requirements: RFC-050 R1_

  - [x] 1.2 Make `wait_for_memory` route-aware and cgroup-aware (implemented 2026-09-24, uncommitted; **corrected post-review, 2026-09-24**)
    - `wait_for_memory` (`memory_admission.py:72-99`) gains a `floor: int | None` keyword, passed to `_has_headroom` at `:86` (unchanged function); `available` is now `_available_bytes()` instead of the host-only read at `:35-46`
    - Caller in `process_document_job` (`worker/job.py:184`) passes `floor=MEM_ADMISSION_FLOOR_SERVICE_BYTES` when `config.docling_offload_configured()` is true (`DOCLING_SERVICE_URL` set AND `docling` importable), not on `settings.docling_service_url` alone
    - Log the active threshold and mode (local/service) at first call; warn at startup when `DOCLING_SERVICE_URL` is set but offload isn't configured; if memory cannot be read, the gate still fails open (unchanged)
    - _Requirements: RFC-050 R1_

  - [x] 1.3 Service-aware `resolve_max_jobs` default (D2) (implemented 2026-09-24, uncommitted; **corrected post-review, 2026-09-24**)
    - `resolve_max_jobs()` (`worker/lifecycle.py:31-50`) stays pure: add a `remote: bool` parameter, computed at the call site from `config.docling_offload_configured()` (not `settings.docling_service_url` alone); default to 2 when `remote` is true and `PAGEINDEX_WORKER_MAX_JOBS` is unset
    - `MAX_JOBS_CEILING` (4) already enforced — log a warning if clamped
    - **Not done (R2 AC4 deferred to Phase 3 with the worker `/metrics` scrape, user decision 2026-09-24):** no `pageindex_worker_max_jobs` Prometheus gauge was added
    - This ships the logic only — `DOCLING_SERVICE_URL` is not yet pointed at the in-cluster service until Wave 3, so the default stays 1 in practice until then **in profiles where offload is unconfigured** (`make up` / `.env.active` local). A bare `uv run arq ...` loads `.env`, whose Scaleway `DOCLING_SERVICE_URL` makes `config.docling_offload_configured()` true, so it selects 2 before Wave 3 (RFC-050 Risk 7 — use `make up`). Per-upload staging is unaffected by any of this — it is always present for arq jobs
    - _Requirements: RFC-050 R2_

  - [x] 1.4 Tests for 1.1-1.3 (implemented 2026-09-24, uncommitted)
    - `wait_for_memory` threshold selection with mocked `DOCLING_SERVICE_URL` (set/unset); cgroup v2, v1-fallback, and neither-readable cases
    - `resolve_max_jobs`: service → 2, local → 1, explicit override wins, ceiling clamps with a warning
    - Bump `tests/TEST_BUDGET.baseline` if needed
    - _Requirements: RFC-050 R1, R2 — Properties 1, 6_

  - [x] 1.6 (New, Iter 9) Per-file dedup lock (D7) (implemented 2026-09-24, uncommitted; **corrected post-review, 2026-09-24**)
    - **As implemented (post-review):** new module `storage/ingest_lock.py`, keyed on `sha256(basename(abspath(pdf_path)))` (not content sha256 — the hash cache is filename-keyed). Redis key `pageindex:ingest-lock:<sha256(basename(abspath(pdf_path)))>`, `SET NX PX <token>`. **Lives in the parent**: `worker/subprocess_mgr.py`'s `_run_converter_subprocess` acquires it around `_run_converter_child` and releases it in the parent's own `finally`, so a killed child (OOM/timeout) no longer strands it. `index()` no longer takes the lock; both `job.py` (arq) and `preprocess_client.py` are covered as callers of `_run_converter_subprocess`.
    - TTL = `MAX_EFFECTIVE_TIMEOUT + kill grace + 60s` (upper bound — the parent can't know the child's effective timeout before the handshake). Wait = `min(INGEST_LOCK_MAX_WAIT_S, min(CHILD_TIMEOUT, MAX_EFFECTIVE_TIMEOUT) / 2, deadline_left / 2)` (`INGEST_LOCK_MAX_WAIT_S` default 900s). **Corrected post-review (PR #26):** on expiry while the lock is still held, the waiter never proceeds unlocked — the arq job is requeued (arq retry/defer) and `preprocess_client.py` skips the file with a logged error. A cancellation during acquire does a token compare-and-delete then re-raises. The arq deadline is recomputed after the converter handshake.
    - Fail-open if Redis is down, bounded by finite socket/connect timeouts — the one case where G5 does not hold. Caveats: the wait counts against the arq `JOB_TIMEOUT` budget; a **parent** (worker pod) death strands the lock until TTL expiry (no external reaper). The `ingest_dedup_lock` decision point lives in a new `_WORKER_POINTS` table.
    - _Requirements: RFC-050 R6 — Property 7_

  - [x] 1.7 (New, Iter 9) Tests for the dedup lock (implemented 2026-09-24, uncommitted)
    - Two concurrent ingests of identical bytes → exactly one `doc_id` minted and persisted, the other dedup-skips
    - Single ingest, no contention → unchanged behavior
    - Redis unreachable → fail-open, proceeds unlocked
    - Wait budget expires while held → arq requeue / `preprocess_client` skip, never unlocked (post-review; extend existing tests in-body — test budget frozen)
    - _Requirements: RFC-050 R6 — Property 7_

  - [x] 3.6 30-day quarantine TTL (D6) (implemented 2026-09-24, uncommitted)
    - Add an idempotent `ensure_quarantine_lifecycle(client, bucket)` in `storage/documents.py` (the only file allowed to hold the `quarantine/` literal, `check_quarantine_prefix_confined`): reads `get_bucket_lifecycle()`, adds or updates a rule with a fixed ID, prefix filter `quarantine/`, 30-day `Expiration`, keeps every other rule, writes it back
    - Call it once at worker startup (`worker/lifecycle.py`, beside existing bucket setup) and from a `make preflight` step; a failure is logged and never blocks startup
    - Unit test (`tests/test_quarantine.py`, mocked client): one rule with 30 days after one application; a second application changes nothing; an unrelated pre-existing rule survives
    - _Requirements: RFC-050 R3 (AC6), D6 — Property 3b_

  - [x] 3.7 (New, Iter 9) Reject reason + defects in quarantine meta (D3b) (implemented 2026-09-24, uncommitted)
    - Extend `save_quarantine` (`storage/documents.py:888-936`) to accept `reject_reason: str` and `defects: list[str]`, and write them into `quarantine/<sha256>.meta.json` alongside the existing `filenames` field
    - Call at both existing reject points — the `flat_garble_unrecovered` branch in `_persist_flat_result` (`client/indexer.py:1924-1933`) and `case (False, Route.REJECT)` in `index()` (`:2762-2787`), for every reject reason, not only garbling
    - No new object, no new erasure key — the existing quarantine erasure and the D6 TTL already cover `.meta.json` whole
    - _Requirements: RFC-050 D3b — Property 8_

  - [x] 3.8 (New, Iter 9) Tests for D3b (implemented 2026-09-24, uncommitted)
    - Force a `Route.REJECT` (non-garbling reason) and a `flat_garble_unrecovered` document → `quarantine/<sha256>.meta.json` carries `reject_reason` and `defects`
    - A forced write failure is logged and the document is still rejected
    - _Requirements: RFC-050 D3b — Property 8_

  - [x] 1.8 (New, Iter 9) Job-level deadline for the child timeout (implemented 2026-09-24, uncommitted)
    - **Defect:** the ingest-lock wait (D7) and the memory-admission wait consumed arq's `JOB_TIMEOUT` budget before the child started, so the child timeout could fire *after* arq had already cancelled the job
    - **Fix:** `worker/job.py` computes `deadline = start + JOB_TIMEOUT − CHILD_GRACE_SECONDS` and passes it down; the child timeout is clamped to what remains, and the ingest-lock wait is capped at `remaining / 2`. `preprocess_client.py` passes no deadline (unchanged behaviour)
    - _Requirements: RFC-050 R1, R6_

  - [x] 4. Checkpoint — Wave 2
    - Run `make test PYTEST_ARGS="tests/test_worker.py tests/test_quarantine.py -q" TEST_MEM_MAX=1500M`
    - Verify the dedup lock, TTL rule and D3b fields all pass their unit tests
    - Ask the user if questions arise before proceeding.

- [ ] 4b. Wave 2b — Selective TableFormer (D8, Added: 2026-09-25)

  - [x] 10.1 Table detection heuristic in preclassify (~1.5h) (implemented 2026-09-25, uncommitted; **amended 2026-09-25 Iter 11 — cascade enhancement pending**)
    - Add `_page_has_ruled_table(page, min_h=3, min_v=3) -> bool` (private) and `detect_pages_with_tables(pdf_path: str) -> set[int] | None` (module-level) to `converters/preclassify.py`
    - `detect_pages_with_tables` opens the PDF with `fitz`, iterates pages, calls `_page_has_ruled_table` on each, returns the set of 0-indexed page numbers with tables; returns `None` when `allow_agpl_fallback=False`, `fitz` is not importable, or `TABLEFORMER_SKIP_ENABLED` env var is `"0"`/`"false"`/`"no"`
    - Add `pages_with_tables: set[int] | None = None` field to `PreClassification` dataclass; update `to_dict()` to serialize as `list[int]` when not `None` (omit when `None`); update `from_dict()` to deserialize back to `set[int] | None`
    - Call `detect_pages_with_tables(pdf_path)` from `preclassify_document()` after the existing pdf-inspector block, assigning to `pre_class.pages_with_tables`; wrap in `try/except Exception` so a failure logs a warning and leaves the field as `None`
    - _Requirements: RFC-050 R7 (AC1, AC6, AC7, AC8, AC9)_

  - [x] 10.1a Cascade enhancement: column-alignment detection (~1h) (Added: 2026-09-25 Iter 11, implemented 2026-09-25, uncommitted)
    - Add `_page_has_column_alignment(page, min_columns=2, min_blocks_per_col=3, quantize_px=12) -> bool` to `converters/preclassify.py`: extracts text blocks via `page.get_text("blocks")`, quantizes x-coordinates, counts positions with ≥`min_blocks_per_col` blocks; returns `True` when ≥`min_columns` such columns exist
    - Update `detect_pages_with_tables()` to call `_page_has_column_alignment(page)` as a second stage alongside `_page_has_ruled_table(page)` — a page is positive if **either** signal fires
    - Wrap `_page_has_column_alignment` call in its own `try/except` so a failure in text-block extraction falls back to vector-geometry-only detection, not `None`
    - _Requirements: RFC-050 R7 (AC1, AC3, AC9)_

  - [x] 10.1b Cascade enhancement: neighbor-page padding (~0.5h) (Added: 2026-09-25 Iter 11, implemented 2026-09-25, uncommitted)
    - Add `_add_neighbor_padding(pages: set[int], page_count: int) -> set[int]` to `converters/preclassify.py`: for every page in the input set, include pages at index N-1 and N+1, clamped to `[0, page_count-1]`
    - Call from `detect_pages_with_tables()` after both detection stages, before returning
    - _Requirements: RFC-050 R7 (AC2)_

  - [x] 10.1c Cascade enhancement: detection_method field + observability (~0.5h) (Added: 2026-09-25 Iter 11, implemented 2026-09-25, uncommitted)
    - Add `detection_method: str | None = None` field to `PreClassification` dataclass
    - Set it in `detect_pages_with_tables()` based on which signals fired per page: `"vector"`, `"column_alignment"`, `"vector+column_alignment"`, `"neighbor_pad"`, or `None` when detection unavailable
    - Update `to_dict()` / `from_dict()` to serialize/deserialize the field
    - Thread through handshake JSON alongside `pages_with_tables` (rides along in `pre_classification` dict)
    - _Requirements: RFC-050 R7 (AC10)_

  - [x] 10.2 Thread `do_table_structure` through the converter stack (~1h) (implemented 2026-09-25, uncommitted)
    - `_build_pdf_pipeline_options()`: add `do_table_structure: bool = True` parameter; when `False`, set `opts.do_table_structure = False` instead of `True` (skip the `table_structure_options.mode` assignment too)
    - `_docling_converter()`: add `do_table_structure: bool = True` parameter; include `"no_tables" if not do_table_structure else ""` as the 8th element of the cache key tuple; pass `do_table_structure` through to `_build_pdf_pipeline_options()`
    - `pdf_to_markdown_docling()` (`pipeline.py`): add `do_table_structure: bool = True` parameter; pass it to `_docling_converter()`; compute the value at call time from `pages_with_tables` (if not `None` and empty, pass `False`)
    - `_pdf_to_markdown_docling_chunked()` (`docling_conv.py`): add `pages_with_tables: set[int] | None = None` parameter; for each chunk, compute `chunk_has_tables = pages_with_tables is None or bool(pages_with_tables & set(range(start, end)))`; pass `do_table_structure=chunk_has_tables` to `_run_docling_chunk_with_timeout()` and thence to `pdf_to_markdown_docling()`
    - `_run_docling_chunk_with_timeout()` and `_docling_chunk_worker()`: thread `do_table_structure` through
    - _Requirements: RFC-050 R7 (AC2, AC3)_

  - [x] 10.3 Thread through the remote/service path (~0.5h) (implemented 2026-09-25, uncommitted)
    - `PdfConvertRequest` (`services/docling-service/app.py`): add `do_table_structure: bool = True` field
    - `convert_pdf` endpoint: pass `req.do_table_structure` through to the `pdf_to_markdown_docling()` call
    - `_remote_pdf_to_markdown` (`client/remote.py`): accept and include `do_table_structure` in the JSON payload
    - The caller in `_convert_to_tree` (`client/indexer.py`) computes `do_table_structure` from the handshake's `pre_classification.pages_with_tables` and passes it to `_remote_pdf_to_markdown`
    - _Requirements: RFC-050 R7 (AC2, AC3)_

  - [x] 10.4 Tests for selective TableFormer (~1h) (implemented 2026-09-25, uncommitted; **amended 2026-09-25 Iter 11 — cascade tests pending**)
    - Unit test: `_page_has_ruled_table` with a synthetic page containing horizontal/vertical lines returns `True`; a blank page returns `False`
    - Unit test: `detect_pages_with_tables` returns `None` when `allow_agpl_fallback=False`; returns `None` when `TABLEFORMER_SKIP_ENABLED=0`; returns `set()` for a text-only PDF; returns `{0, 2}` for a PDF with tables on pages 0 and 2
    - Unit test: `_build_pdf_pipeline_options(do_table_structure=False)` returns options with `opts.do_table_structure == False`
    - Unit test: `_docling_converter(do_table_structure=False)` uses a different cache key than `_docling_converter(do_table_structure=True)` (two distinct converter instances)
    - Unit test: `PreClassification.to_dict()` round-trips `pages_with_tables` correctly (set → list → set)
    - Integration test (corpus validation): ingest a known table-bearing PDF and a known text-only PDF; verify the table-bearing one produces identical markdown with and without the optimization; verify the text-only one skips TableFormer (check log for the decision record)
    - _Requirements: RFC-050 R7 — Property 9_

  - [x] 10.4a Tests for cascade enhancements (~1h) (Added: 2026-09-25 Iter 11, implemented 2026-09-25, uncommitted)
    - Unit test: `_page_has_column_alignment` with a synthetic page with ≥2 columns of aligned text blocks → `True`; with a single-column prose page → `False`; with multi-column prose that lacks row regularity → correctly distinguishes from table
    - Unit test: `_add_neighbor_padding({2, 5}, page_count=10)` → `{1, 2, 3, 4, 5, 6}`; `{0}, page_count=3` → `{0, 1}` (lower clamp); `{2}, page_count=3` → `{1, 2}` (upper clamp); empty set → empty set
    - Unit test: `detect_pages_with_tables` returns pages detected by column-alignment even when no ruled lines present (borderless table coverage)
    - Unit test: `detect_pages_with_tables` includes neighbor pages of a positive page (padding coverage)
    - Unit test: `PreClassification.to_dict()` round-trips `detection_method` correctly; `detection_method=None` when detection unavailable
    - Unit test: column-alignment exception falls back to vector-geometry-only, not `None` (graceful degradation)
    - Unit test: three-tier confidence — ambiguous page (weak column alignment) treated as positive
    - _Requirements: RFC-050 R7 (AC1, AC2, AC3, AC9, AC10) — Property 9_

  - [ ] 4c. Checkpoint — Wave 2b
    - Run `make test` to verify no regressions
    - Verify the `TABLEFORMER_SKIP_ENABLED=0` kill switch restores original behavior
    - Ask the user if questions arise before proceeding

- [ ] 5. Wave 3 — In-Cluster Docling Service + MAX_JOBS=2 + G1 Validation

  - [ ] 5.1 (New, Iter 9) Point the worker at the in-cluster docling service
    - Set `DOCLING_SERVICE_URL` to `services/docling-service`'s in-cluster address in the relevant `.env.active` profile (`make env-remote` / `make env`)
    - Confirm `services/docling-service` (custom FastAPI, warm models, `POST /convert/pdf`) is reachable from the worker pod before running G1; Scaleway remote Docling is not used
    - With the env var set and `PAGEINDEX_WORKER_MAX_JOBS` unset, confirm Task 1.3's `resolve_max_jobs` now returns 2 and Task 1.2's admission floor is `MEM_ADMISSION_FLOOR_SERVICE_BYTES`
    - Pod sizing (worker + docling-service limits/requests on the 7.6 GiB node): follow `docs/PARALLEL_INGESTION.md` § "Deployment sizing (RFC-050)" and apply `docs/infra/hetzner-deployment-service-rfc050.patch` to the deployment repo
    - Watch the G1 runs for LLM 429s: `make g1-timings` counts 429/retry lines (see the deferred LLM concurrency cap in Notes)
    - _Requirements: RFC-050 G1, R1, R2 (user decision)_

  - [ ] 9.2 Post-RFC-050 timing measurement (G1 gate)
    - Same G1 protocol as Task 9.1: `make ingest`, KEDA pinned to 1 replica, the same `.env.active` except `DOCLING_SERVICE_URL` and `MAX_JOBS`, 2 runs, median — on the **same (already resized) hardware** as the baseline arm; confirm the recorded node allocatable matches Task 9.1's
    - Before this arm runs with `MAX_JOBS=2`: resolve the docling-service concurrency open item (service-side cap, or limit sized for N concurrent conversions — `docs/PARALLEL_INGESTION.md` § Deployment sizing)
    - In-cluster docling service active, `MAX_JOBS=2`
    - Compare against the Task 9.1 baseline median: gate is **≥30% wall-clock reduction**; report the per-stage change from Task 1.5
    - _Requirements: RFC-050 G1_

  - [ ] 6. Checkpoint — Wave 3
    - Verify the ≥30% G1 gate is met; if not, stop and reassess before Wave 4 (which is independent and can still land, but the throughput goal would be unmet)
    - Watch total node memory during the run (docling-service pod + worker), not just worker RSS — the 7.6 GiB single node hosts both
    - Ask the user if questions arise before proceeding.

- [x] 7. Wave 4 — Raw Output Persistence + get_document (D3)

  - [x] 3.1 Wire `save_raw` into the two persist methods only (implemented in f31f386 on the RFC-050 branch; **not deployed** — live image sha-721cfd1 is master, so the 2026-09-25 pocketbook ingest wrote no sidecar)
    - In `_persist_tree_result` (`client/indexer.py:2407`) and `_persist_flat_result` (`:2226`), after the existing `save_raw(doc_id, filename, file_bytes)`, add `save_raw(doc_id, f"{filename}.extracted.md", state.md_content.encode("utf-8"))`, guarded by `if state.md_content is not None` and wrapped in `try/except Exception` → `logger.warning(..., exc_info=True)` so a failed sidecar upload never fails an otherwise successful ingest (best-effort; already in code at both sites)
    - Replace `save_raw`'s inline `.pdf`-or-octet-stream ternary (`storage/documents.py:838-854`) with a suffix map: `.pdf` → `application/pdf`, `.md` → `text/markdown`, default `application/octet-stream`
    - No reject-side write — rejected documents get D3b's reason/defects (Task 3.7) instead, never an `.extracted.md`
    - _Requirements: RFC-050 R3 (AC1) — Property 2_

  - [x] 3.2 Verify erasure cascade covers raw output (implemented in f31f386 on the RFC-050 branch; **not deployed** — live image sha-721cfd1 is master, so the 2026-09-25 pocketbook ingest wrote no sidecar); covered by the `uploads/` prefix delete + `test_delete_doc_recovers_doc_name_past_extracted_md_sidecar`
    - Confirm `_ERASURE_MANIFEST`'s `uploads/` prefix delete (`storage/documents.py:383-415`) already covers `*.extracted.md` — no new manifest entry needed
    - Integration test: ingest doc → verify `*.extracted.md` exists → `delete_doc` → verify it is removed
    - **(New, post-review 2026-09-24)** Fix the doc-name recovery helper to strip the `.extracted.md` suffix (in addition to existing known suffixes), covering the sidecar-only case where `uploads/<doc_id>/` holds only `<filename>.extracted.md` and the original upload is already gone or never persisted
    - _Requirements: RFC-050 R3 (AC3) — Property 3_

  - [x] 3.3 Add `load_raw` to the storage layer (implemented in f31f386 on the RFC-050 branch; **not deployed** — live image sha-721cfd1 is master, so the 2026-09-25 pocketbook ingest wrote no sidecar); **named `load_extracted_md`**, not `load_raw`
    - `load_raw(doc_id: str) -> bytes | None` in `storage/documents.py`, listing `uploads/<doc_id>/*.extracted.md`
    - `doc_id` only — never reads `quarantine/`; no discovery function
    - Import directly from `storage.documents`; do not add to the storage package's frozen `__all__`
    - _Requirements: RFC-050 R3 (AC4) — Property 3a_

  - [x] 3.4 Add `include="raw"` to `get_document` (implemented in f31f386 on the RFC-050 branch; **not deployed** — live image sha-721cfd1 is master, so the 2026-09-25 pocketbook ingest wrote no sidecar); **deviation:** a missing sidecar returns `raw_markdown: null` + note, not a not-found error (unknown `doc_id` still returns not-found); DESIGN.md updated
    - Extend the existing `get_document(doc_id: str)` MCP tool (`tools/documents.py:277`) with an optional `include: str | None = None` parameter; `include="raw"` calls `storage.documents.load_raw(doc_id)` and returns the decoded markdown, raising a descriptive not-found error otherwise; default behavior (`include=None`) is unchanged
    - No sixth tool — `FROZEN_SURFACE["tools"]` is unchanged
    - Update `DESIGN.md` §MCP Tool Contracts to document the new parameter
    - _Requirements: RFC-050 R3 (AC4), G2 — Property 3a_

  - [x] 3.5 Tests for raw output persistence (done 2026-09-26: unit tests for both persist sites, None-guard, content type, `load_extracted_md` and `include="raw"`; integration `test_raw_markdown_round_trip_persist_serve_erase` in `tests/test_client.py` — real `_persist_tree_result` → `save_raw` → `load_extracted_md` → `get_document(include="raw")` → `delete_doc` against an in-memory MinIO, zero objects left; budget baseline 978 → 979)
    - Unit test: `save_raw` called with correct arguments at both persist sites; content-type map (`.md` → `text/markdown`)
    - Unit test: `load_raw` returns stored content / `None` when missing
    - Integration test: full ingest → `.extracted.md` exists in MinIO → `delete_doc` → zero artifacts remain
    - `None`-guard test: ingest a `.txt` and a forced all-converters-failed PDF → no `.extracted.md`, no exception
    - Unit test: `get_document(doc_id, include="raw")` returns the markdown; a rejected sha256 or unknown `doc_id` returns not-found and never reads `quarantine/`
    - _Requirements: RFC-050 R3 — Properties 2, 3, 3a_

  - [x] 8. Checkpoint — Wave 4 (2026-09-26: `make test` on test_storage/test_quarantine/test_client green; **live smoke passed 2026-09-26** on image sha-2184e7e: pocketbook re-ingested via `POST /upload/files` (job 6d2972bd, 375.8 s, Mac docling-service) → doc_id 6ab474c3-9db6-4283-b72d-7c60628cb1af; through the registered MCP tools (6 listed, no import errors) `get_document(include="raw")` returned a 2,035,016-char `raw_markdown`, plain `get_document` has no `raw_markdown` key, `include="xml"` → invalid-include error, unknown doc_id → not-found)
    - Run `make test PYTEST_ARGS="tests/test_storage.py tests/test_quarantine.py -q" TEST_MEM_MAX=1500M`
    - Verify `get_document(..., include="raw")` works via a smoke call; verify MCP tool registration has no import errors
    - Ask the user if questions arise before proceeding.

- [ ] 9. Final Checkpoint
  - Run `make test` (full suite, foreground, `TEST_MEM_MAX` set) and verify zero regressions
  - Verify the G1 gate (Task 9.2) — ≥30% median wall-clock reduction
  - Verify the quarantine lifecycle rule is present on the remote bucket (Task 3.6)
  - Verify every **persisted** corpus document has accessible raw output via `get_document(doc_id, include="raw")` (non-null `raw_markdown`), and that every **rejected** input stays not-found (no `doc_id`; no `.extracted.md` anywhere) per Tasks 3.1/3.5
  - Ask the user if questions arise before proceeding.

## Deferred (Iter 9)

**Cut from this RFC's execution (MINIMAL scope, user decision).** Kept below, unmodified in substance from Iter 8, so a follow-up RFC can pick up the analysis rather than re-deriving it. Do not execute these under this RFC.

- [ ] D. Deferred — D4: CLI Dry-Run Replay (was Wave 3, Tasks 5.1-5.3, Checkpoint 6)

  Depends on the state sidecar (also deferred — no consumer without D4). Follow-up option: replace the fail-closed in-memory read-your-writes overlay with a scratch MinIO bucket + scratch Redis db (~3h instead of ~17.5-19.5h). Task shape carried over from Iter 8: CLI entry (`--from-raw --doc-id | --sha256`, `--no-recovery`, `--no-text`, `--allow-non-zdr`); the replay seam in `index()` / `_convert_to_tree` (state restore, dedup bypass, no-original handling); the write barrier; cache readers + the `check_quarantine_reader_cli_only` HR5 gate; the recovery on/off switch, report and structural diff; the full test list (CLI parsing, cache hit/miss, LLM-stubbed integration, Property 4a spies, HR2/HR3 safeguards, exit codes). See RFC-050 §D4 and design §3 (both kept, collapsed) for the full Iter 1-8 spec.

- [ ] E. Deferred — D5: Recovery Method Optimization (was Wave 4, Tasks 7.1-7.3, Checkpoint 8)

  No quantified target; Task 1.5's stage-timing histogram (kept, Wave 1) gives a follow-up RFC real `stage="recovery"` data to profile against. Task shape carried over: profile each `RecoveryMixin` method (`client/recovery.py`) under representative documents; optimize the 2-3 slowest without restructuring the sequential GateSpec dispatch; equivalence tests (Property 5) confirming optimized output matches baseline.

- [ ] F. Phase 6 — Backup policy (operator work, not in code; user decision 2026-09-24)

  Back up only MinIO `uploads/` (sources — everything else is re-derivable by re-ingest) and the Postgres registry; nightly; 30-day retention. `delete_doc` / `erase_quarantine` record erased `doc_id`/sha256 in an erasure ledger, replayed against any restore before it is served; the 30-day retention bounds how long an erased document survives in a backup (HR2). See ARCHITECTURE.md § Compliance and DESIGN.md § Erasure.

## Notes

- **Packaging (2026-09-24):** RFC-051 lands first on branch `ICR-97-rfc51-codebase-trimming-audit-archive`; RFC-050 is stacked on it as `ICR-97-rfc50-pipeline-acceleration-raw-export`. Confluence sync is intentionally not done for either.
- **Deferred open item — LLM concurrency cap (user decision 2026-09-24):** per-node summaries in `pageindex` (an installed site-packages dependency, not vendored) already run concurrently — `page_index_md.py` uses `asyncio.gather` with no semaphore — so bursts double at `MAX_JOBS=2`. Revisit only if G1 runs show 429s (`make g1-timings` counts 429/retry lines); a fix means a fork change or a client-side limiter.
- **(Iter 9)** The task list is regenerated around the Iter 9 four-wave sequencing; task numbers 1.1-1.5, 3.1-3.6, 9.1-9.2 are kept from Iter 8 for continuity with RFC-050 and the design doc's prose references. New tasks (1.6, 1.7, 3.7, 3.8, 5.1) use free numbers in their wave's neighborhood.
- **(Iter 9)** D4's Tasks 5.2a-5.2d, 5.3 and D5's Tasks 7.1-7.3 are not renumbered — they are cut wholesale and kept only as a shape summary under Deferred, not as executable checkboxes, to keep this file short (shrink is the point of Iter 9).
- `save_raw` already exists in `storage/documents.py:838-854` — Task 3.1 wires it at two sites only (not four; the reject-side pair and the state sidecar are cut with D4).
- `_ERASURE_MANIFEST` already covers the `uploads/` prefix — Task 3.2 verifies this, adds nothing.
- Every commit, not only the last of a wave, must pass `scripts/gates/test_budget.sh`. Bump `tests/TEST_BUDGET.baseline`, with a justification, only when the collected count leaves the ±15 band. Whichever of RFC-050 and RFC-051 lands second rebases on the other's baseline.
- `tests/test_source_invariants.py` is touched by both this RFC (Tasks 1.5's `FROZEN_SURFACE["metrics"]` entry) and RFC-051 D1 — rebase on whichever lands first.
- New `decision()` events (Tasks 1.5, 3.7) go into `obs/decision_points.py`'s Python tables, or the YAML once RFC-051 D4 has landed.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 1, "tasks": ["1.5", "9.1", "2"], "label": "Stage timing + pre-change baseline (local Docling, MAX_JOBS=1)" },
    { "id": 2, "tasks": ["1.1", "1.2", "1.3", "1.4", "1.6", "1.7", "3.6", "3.7", "3.8", "4"], "label": "Safety before concurrency: cgroup-aware admission gate (D1), service-aware MAX_JOBS default (D2, not yet enabled), dedup lock (D7), reject metadata (D3b), quarantine TTL (D6)" },
    { "id": "2b", "tasks": ["10.1", "10.1a", "10.1b", "10.1c", "10.2", "10.3", "10.4", "10.4a", "4c"], "label": "Selective TableFormer: two-stage cascade table detection (D8, Iter 11 — vector-geometry + column-alignment + neighbor padding + detection_method), parameter threading (local + remote), tests" },
    { "id": 3, "tasks": ["5.1", "9.2", "6"], "label": "Point at in-cluster docling service, enable MAX_JOBS=2, run the G1 gate (≥30%)" },
    { "id": 4, "tasks": ["3.1", "3.2", "3.3", "3.4", "3.5", "8"], "label": "Raw output persistence (2 sites only) + get_document(include=\"raw\")" },
    { "id": 5, "tasks": ["9"], "label": "Final checkpoint" }
  ],
  "deferred": {
    "D4": ["5.1 (old)", "5.2a", "5.2b", "5.2c", "5.2d", "5.3", "6 (old)"],
    "D5": ["7.1", "7.2", "7.3", "8 (old)"],
    "note": "Old Wave-3/Wave-4 task numbers from Iter 8 are not reused in this wave graph to avoid collision with the new Task 5.1 (in-cluster docling wiring) and the renumbered checkpoints."
  }
}
```
