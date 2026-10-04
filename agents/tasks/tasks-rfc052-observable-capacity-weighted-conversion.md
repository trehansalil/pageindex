<!-- Space: CITRA -->
<!-- Title: Implementation Plan: Observable, Page-Class-Aware, Capacity-Weighted Conversion -->
<!-- Folder: Tasks -->

---
id: "tasks-rfc052-observable-capacity-weighted-conversion"
title: "Tasks: Observable, Page-Class-Aware, Capacity-Weighted Conversion"
type: tasks
status: draft
date: "2026-09-26"
tags:
  - tasks
  - performance
  - observability
  - docling
aliases:
  - "tasks-rfc052-observable-capacity-weighted-conversion"
governs:
  - "[[RFC-052]]"
---

# Implementation Plan: Observable, Page-Class-Aware, Capacity-Weighted Conversion

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC(s) | [[RFC-052]] |
| Design Document | [[design-rfc052-observable-capacity-weighted-conversion]] |

## Overview

Six stacked PRs, each cut from the previous one (P4 and P5 added 2026-09-27; P5 is a later phase):

| PR | Branch |
|---|---|
| P0 | `ICR-97-rfc52-log-correlation` |
| P1 | `ICR-97-rfc52-page-class-detection` |
| P2 | `ICR-97-rfc52-page-class-chunking` |
| P3 | `ICR-97-rfc52-capacity-split` |
| P4 | `ICR-97-rfc52-table-capture` |
| P5 (later) | `ICR-97-rfc52-mac-docling1-split` |

Infra manifests live in `/root/hetzner-deployment-service` and ship as a companion PR per phase. Tests use `make test` only.

## Tasks

- [x] 1. P0: Log correlation (`ICR-97-rfc52-log-correlation`)
  - [x] 1.1 Promtail config: fix the container/pod relabels, the json map (`logger`, `ts`), and the timestamp stage. Add structured metadata for the IDs and drop stages for `/metrics`, `/health` and arq duplicates. _R1 AC1-3_
  - [x] 1.2 Promtail DaemonSet: add the docling taint toleration, then verify a stream from docling-1 while it is up. _R1 AC4_
  - [x] 1.3 Node controller: make the `tick` container log to stdout, not only a file or stderr that is lost. Verify it in Loki. _R1 AC5_
  - [x] 1.4 Stop the duplicate arq console handler at source (worker logging setup). _R1 AC3_
  - [x] 1.5 Send the `X-Job-Id`/`X-Doc-Sha8`/`X-Run-Id`/`X-Shard` headers from `client/remote.py`. _R1 AC6_
  - [x] 1.6 docling-service: header middleware plus `bind_log_context`, and `JsonFormatter` on the root and uvicorn loggers. _R1 AC6_
  - [x] 1.7 Write a `docling_chunk` record per chunk in `converters/docling_conv.py`'s chunked path, and one for the single-shot path. Peak RSS comes from the child's `resource.getrusage`. _R1 AC7_
  - [x] 1.8 Preclassify: log the page-set summary at INFO, including the method. Raise the detection-failure log from debug to WARNING. _R1 AC8, R2 AC7_
  - [x] 1.9 Loki reachable from the Mac with no operator step: push-only nginx gateway bound to portfolio's Tailscale IP, and infra auto-applied on push (`ICR-97-rfc52-log-shipping-automation`). _R1 AC9, D2_
  - [x] 1.10 Mac log shipping with no operator step: in-process `LokiPushHandler`, in-process rotation, and a launchd auto-updater; one-time `install.sh` bootstrap (`ICR-97-rfc52-log-shipping-automation`). _R1 AC9_
  - [x] 1.11 Provision the "PageIndex Logs" dashboard JSON: the `$job_id` variable plus log, chunk-timeline and error panels. _R1 AC10_
  - [x] 1.12 Measure the extra RAM on portfolio (≤ 150 MiB), and check that one pocketbook `job_id` renders end to end. _R1 AC11, G1_
  - Status 2026-09-27 (1.2 and 1.12 closed): infra PR #11 (daemonset headroom) is live. The new docling-1 sized docling-service to requests `15750m/28595Mi` and limits `16000m/28979Mi`, and promtail `promtail-d5n4t` ran on docling-1. Stores were wiped (MinIO `pageindex`, Redis db1, `doc_registry`), then the pocketbook was re-ingested: job `079e80b3-f20e-4699-98c3-5a2853184f3f`, done in 903 s, all 30 chunks converted on docling-1 (01:16:51 to 01:30:44). Loki under that `job_id`: docling-service 845 decision + 607 log lines, all 30 `docling_chunk` records; worker 51 decision + 266 log + phase entry/exit. The "PageIndex Logs" dashboard needs the `job_id` box filled in. Gap: docling library lines from the chunk child processes (for example the tesseract OSD error) carry no `job_id`. Two earlier attempts failed because of a cold-start defect: the job fell back to the legacy LLM path while docling-1 booted, and orphaned remote OCR kept running after the worker restart. It is logged in the docling-service backlog, with a fix plan, as a separate PR.
  - Status 2026-09-27 (1.12 live run on docling-1): pocketbook job `cad64281-68da-4a2a-b8bd-60d0b1081301` done in 788 s, tree 2.5 MB. In Loki under that `job_id`: worker 50 decision + 262 log + phase entry/exit lines. docling-service tags every line, `docling_chunk n/30` included, with the job ID (checked with kubectl logs). Those lines are missing from Loki because 1.2 is blocked: promtail on docling-1 stays Pending (Insufficient cpu), since `docling-node.sh` sizes docling-service to the whole node. The fix is infra PR #11, not merged. RAM: Loki, promtail and Grafana have existed since 2026-06, and the only new pod is the gateway (2 MiB). Loki went from 68 to 115 MiB during the job, so the added RAM is about 50 MiB (AC11 met). Counting Loki + promtail + gateway in full, the peak was 168 MiB. 1.3 is verified: controller tick lines are in Loki. 1.12 stays open until docling-1's lines show up in Loki.
  - Status 2026-09-26: 1.2 and 1.3 are done in config and await live verification in 1.12. 1.9 and 1.10 were first prepared as operator scripts, then replaced by automation on `ICR-97-rfc52-log-shipping-automation` (user asked for no manual steps).
- [x] 2. Checkpoint P0: `make test`, open the PR, and ask the user before continuing.

- [x] 3. P1: Page-class detection (`ICR-97-rfc52-page-class-detection`)
  - [x] 3.1 Add the `pdf-inspection` extra to the worker and docling-service Dockerfiles. Confirm `import pdf_inspector` in the pod. _R2 AC1_
  - [x] 3.2 Repair `_page_has_ruled_table` for tuple items, add per-page `try` with a safe-positive default, and a fixture test with real tuple output. _R2 AC2_
  - [x] 3.3 Add the `PageClass` dataclass, the text-layer and image signals, and `find_tables()` gated by cheap signals. Tighten column alignment. _R2 AC3-5_
  - [x] 3.4 Add `PreClassification.page_classes` and carry the run-length wire format through the handshake JSON and the `PdfConvertRequest` body. _R2 AC6_
  - [x] 3.5 Commit `scripts/page_class_census.py`. Check that the pocketbook census meets R2 AC3's bound and that classification time is ≤ 30 s. _R2 AC3, AC8_
  - Status 2026-09-27 (`ICR-97-rfc52-page-class-detection`): 3.1 `import pdf_inspector` verified in a local `--extra pdf-inspection` venv (pdf-inspector 0.2.6, MIT); the in-pod check follows the image build. 3.2 tuple items read, per-page try, real `get_cdrawings()` fixture. 3.3 classifier runs one `get_text("dict")` parse per page; column alignment is now line-based (≥ 3 columns × ≥ 4 lines, ≥ 3 shared rows); classification no longer needs `pdf_type`. 3.4 `page_classes` in the handshake and the `/convert/pdf` body (optional; absent or malformed = none). 3.5 pocketbook census: column alignment 263 vs `find_tables()` 262 (1.004, AC3 met), 0 table pages missed after padding, classification 18.0 s (≤ 30 s). **Deviation:** `find_tables()` does not run in the worker: gated on cheap positives it would still check ~264 pocketbook pages at ~0.8 s each (~210-250 s), far over the 30 s budget. Per the design's fallback it belongs in the backend; that step is not built here and awaits a decision.
- [x] 4. Checkpoint P1: `make test`, open the PR, and ask the user.

- [x] 5. P2: Page-class chunking and levers (`ICR-97-rfc52-page-class-chunking`)
  - [x] 5.1 Chunker: run-length, absorb, split, with property tests P1/P2. _R3 AC1-2_
  - [x] 5.2 Per-chunk `do_table_structure`/`do_ocr`, the `DOCLING_DO_OCR` override semantics, and alignment of the Mac `run.sh`. _R3 AC3_
  - [x] 5.3 Keep the `force_full_page_ocr` escalation: add a test that recovery still forces OCR on text-layer pages. _R3 AC4_
  - [x] 5.4 Add the `DOCLING_TABLEFORMER_MODE` config and the kill switch `PAGECLASS_CHUNKING`. _R3 AC6, R4 AC1_
  - [x] 5.5 Write `scripts/conversion_bench.py` (run 2026-09-27 on docling-1; PR #37), run the 3 docs × 3 arms × 2 benchmark on the active remote, and write `audit/RFC052_CONVERSION_BENCH_<date>.md`. _R4 AC2-5_
  - [x] 5.6 Choose the defaults from the benchmark (FAST only if it passes the R4 AC4 gate). Record the decision in the RFC as an amendment.
  - Status 2026-09-27: FAST failed the R4 AC4 gate on all three documents (73.24% / 11.72% / 18.01% changed cells vs R3; noise floor 0.00%). The user chose to keep R3 as the default and FAST opt-in; the defaults are unchanged. Recorded as the R4 AC4 amendment and on D7.
- [x] 6. Checkpoint P2: `make test`, open the PR, and ask the user.
  - Status 2026-09-27: P0 (#30, #32), P1 (#34) and P2 (#36) merged; `make test` on master + the 5.6 decision: 1000 passed, 9 skipped.

- [x] 7. P3: Capacity reporting (`ICR-97-rfc52-capacity-split`). Scope cut 2026-09-27: docling-local dropped; the split moves to P5.
  - [x] 7.1 docling-service `GET /capacity`: free memory on Linux and macOS, `safe_procs`, slots, the `spp_ewma` tracker, `build_sha`. _R5 AC1_
  - [x] 7.2 The planner clamps its worker count by free-memory `safe_procs`. _R5 AC2_
  - [x] 7.3 Add `page_start`/`page_end` to `PdfConvertRequest`: slice, rebase page classes (pictures are rebased by the P5 merge). _R5 AC3_
  - [x] 7.4 Build-skew check: the worker compares the active remote's `build_sha` and logs a WARNING on mismatch. _R5 AC8_
  - [x] 7.5 Infra companion: delete the `docling-service-local` Deployment and the `docling-node.sh local on` path. _R5 AC5, NG7_ Infra branch `rfc52-drop-docling-local`; the deploy workflow deletes the orphaned Deployment.
  - [x] 7.6 Forced-low-memory run on the active remote: the planner clamps, no OOM events. _Test strategy_ **Run 2026-09-28 on the Mac (`4a282de`), with `DOCLING_RESERVE_BYTES` forced in the service launcher:** 120 pocketbook pages at a 21.1 GiB reserve clamped 12 -> 3 processes (196.8 s); 70 pages at a 64 GiB reserve hit `safe_procs=0` and converted with 1 process (185.7 s). OOM events 0 before / 0 after; reserve restored to 768 MiB.
- [x] 8. Checkpoint P3: `make test`, open the PR, and ask the user.

- [x] 9. P4 (accepted 2026-09-27; design: Table Capture, Tables in Search, Signal-driven Bypass): Table capture, search and bypass (`ICR-97-rfc52-table-capture`)
  - [x] 9.1 Process-pool `find_tables()` in the worker: pool sized by cgroup CPU and free memory, per-process memory kill, `capture_failed` pages. Overlap it with remote conversion; it never blocks dispatch. Live: median wall time ≤ 1.05× over 2 runs per arm, and the worker cgroup's `memory.peak`. _R7 AC1-3_ **Measured 2026-09-28: FAIL**, 1.123x (454.7 s -> 510.5 s); memory 1.15/1.19 GB within limit. `TABLES_CAPTURE` stays 0. **Re-measured 2026-09-29 after #43: PASS**, 1.011x (519.7 s -> 525.5 s), memory.peak 1.28 GB; capture still hits its deadline on pages ~165-291 (1 proc).
  - [x] 9.2 Persist `processed/<doc_id>.tables.json` (bbox, cells, markdown, source, coverage, `node_id`, caption). Keep TableFormer tables alongside and link them by bbox. Add it to `_ERASURE_MANIFEST` (no dedicated erasure test, per user). _R7 AC4-6, HR2_
  - [x] 9.3 Serve stored table cells directly to retrieval answers. HR4 legal review deferred by the user (2026-09-27); track it as an open item. _R7 AC7-8_
  - [x] 9.4 Table descriptions through the tree's LLM tier (HR3), table child nodes in the slim tree under a token budget, and the page-content tool returning the markdown. Descriptions use `PAGEINDEX_FILTER_MODEL`, capped at 600 per document. _R8 AC1-4_
  - [x] 9.5 Table question set (≥ 10 pocketbook questions): search with vs without table nodes. _R8 AC5_ **Measured 2026-09-28: FAIL**, 5/13 vs 6/13, 1 regression, +7.9k tokens. `TABLES_DESC_ENABLED` stays 0. **Re-measured 2026-09-29 after #43: PASS**, 9/13 vs 9/13, 1 regression (latin-america-pop-density), +7.7k tokens.
  - [x] 9.6 OCR bypass on clean text-layer table pages; TableFormer skip on pages with no table; `bypass` field in `docling_chunk`; per-bypass kill switches; signals computed per chunk in docling-service; `force_full_page_ocr` overrides the OCR bypass and emits `docling_force_recovery` with the first pass's per-chunk context. _R9 AC1-2, 4-7_
  - [x] 9.7 Benchmark grid replacement (`find_tables` vs TableFormer, changed-cell ratio). Enable `TABLES_TRUST_BYPASS` only at ≤ 2%. _R9 AC3_ **Measured 2026-09-28 (after the #41 AC1/AC3 amendment): FAIL**, Unfall 13.33%, GHV 20.10%. **AC3 dropped 2026-09-28 (user):** grid replacement, the `TABLES_TRUST_*` switches and the bench `grid` subcommand are removed.
  - [x] 9.8 Force-recovery case census: query Loki for `docling_force_recovery`, then tabulate which documents took the path, why, their first-pass OCR/TableFormer bypasses, and whether the recovered garble was on TableFormer pages (via `garbled_pages_known`/`garbled_tableformer_overlap`; `garbled_pages` itself is the worker's mapping of `helpers/garble.py`'s flagged tree nodes to pages through `heading_pages` -- see the design's "`garbled_pages` source" -- omitted, not `[]`, when unanchored). Also join a first-pass force (recorded by the `client/indexer.py` inspector/pre-garble probe with `choice="request"` and no `prior_pass` of its own) to this same document's `docling_force_recovery` record on `job_id`, since the first pass's own force is otherwise invisible to this census. Write `audit/RFC052_FORCE_RECOVERY_CASES_<date>.md`. _R9 AC7_ **Done 2026-09-28: 0 cases** since P4 went live (Loki keeps 3 days).
- [x] 10. Checkpoint P4: `make test`, open the PR, and ask the user. PR #42 (report), fixes #43, re-run #44. **Decision 2026-10-04 (user):** keep production as is: `TABLES_CAPTURE=1` (infra #18), `TABLES_DESC_ENABLED=0`; move to P5.
  - Report: `audit/RFC052_P4_CHECKPOINT_2026-09-28.md`. Awaiting the user's decisions listed there.

- [ ] 11. P5 (later): Mac + docling-1 together (`ICR-97-rfc52-mac-docling1-split`)
  - [ ] 11.1 The node controller keeps docling-1 up alongside the Mac when split is on, and writes the live backend set to a ConfigMap. The coordinator resolves the named Services from it. _R5 AC4_
  - [ ] 11.2 Coordinator: eligibility (HR3, build SHA), proportional initial allocation, tail stealing, per-shard `/capacity` check, the shared deadline, retry and re-route, merge, and join heading-shift count. _R5 AC4, AC6_
  - [ ] 11.3 HR3: docling-1 eligible for PII documents, the Mac never. Test the eligibility table. _R5 AC7_
  - [ ] 11.4 Add a stub-backend integration test for stealing, the deadline, and re-route after a failure. _Test strategy_
  - [ ] 11.5 Deploy with `DOCLING_SPLIT_ENABLED=0`. Run a parity check on the pocketbook with split off and on. _R5 AC10, R6_
- [ ] 12. Checkpoint P5: `make test`, open the PR, and get the user's go-ahead before switching on `DOCLING_SPLIT_ENABLED=1`.

## Notes

- No operator steps remain for 1.9 and 1.10 beyond a one-time `install.sh` on the Mac. Claude still never runs hcloud mutations.
- Commits carry no attribution lines. Never `git add -A`.
