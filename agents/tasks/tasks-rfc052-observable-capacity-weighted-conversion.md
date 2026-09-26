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

Four stacked PRs, each cut from the previous one:

| PR | Branch |
|---|---|
| P0 | `ICR-97-rfc52-log-correlation` |
| P1 | `ICR-97-rfc52-page-class-detection` |
| P2 | `ICR-97-rfc52-page-class-chunking` |
| P3 | `ICR-97-rfc52-capacity-split` |

Infra manifests live in `/root/hetzner-deployment-service` and ship as a companion PR per phase. Tests use `make test` only.

## Tasks

- [ ] 1. P0: Log correlation (`ICR-97-rfc52-log-correlation`)
  - [ ] 1.1 Promtail config: fix the container/pod relabels, the json map (`logger`, `ts`), and the timestamp stage. Add structured metadata for the IDs and drop stages for `/metrics`, `/health` and arq duplicates. _R1 AC1-3_
  - [ ] 1.2 Promtail DaemonSet: add the docling taint toleration, then verify a stream from docling-1 while it is up. _R1 AC4_
  - [ ] 1.3 Node controller: make the `tick` container log to stdout, not only a file or stderr that is lost. Verify it in Loki. _R1 AC5_
  - [ ] 1.4 Stop the duplicate arq console handler at source (worker logging setup). _R1 AC3_
  - [ ] 1.5 Send the `X-Job-Id`/`X-Doc-Sha8`/`X-Run-Id`/`X-Shard` headers from `client/remote.py`. _R1 AC6_
  - [ ] 1.6 docling-service: header middleware plus `bind_log_context`, and `JsonFormatter` on the root and uvicorn loggers. _R1 AC6_
  - [ ] 1.7 Write a `docling_chunk` record per chunk in `converters/docling_conv.py`'s chunked path, and one for the single-shot path. Peak RSS comes from the child's `resource.getrusage`. _R1 AC7_
  - [ ] 1.8 Preclassify: log the page-set summary at INFO, including the method. Raise the detection-failure log from debug to WARNING. _R1 AC8, R2 AC7_
  - [ ] 1.9 **Operator:** add the Loki Tailscale NodePort Service, the iptables rule limiting it to `tailscale0`, and the external probe proving it is closed publicly. _R1 AC9, D2_
  - [ ] 1.10 **Operator (Mac):** Alloy launchd agent, newsyslog rotation, and redeploy the Mac service with JSON logging. _R1 AC9_
  - [ ] 1.11 Provision the "PageIndex Logs" dashboard JSON: the `$job_id` variable plus log, chunk-timeline and error panels. _R1 AC10_
  - [ ] 1.12 Measure the extra RAM on portfolio (≤ 150 MiB), and check that one pocketbook `job_id` renders end to end. _R1 AC11, G1_
- [ ] 2. Checkpoint P0: `make test`, open the PR, and ask the user before continuing.

- [ ] 3. P1: Page-class detection (`ICR-97-rfc52-page-class-detection`)
  - [ ] 3.1 Add the `pdf-inspection` extra to the worker and docling-service Dockerfiles. Confirm `import pdf_inspector` in the pod. _R2 AC1_
  - [ ] 3.2 Repair `_page_has_ruled_table` for tuple items, add per-page `try` with a safe-positive default, and a fixture test with real tuple output. _R2 AC2_
  - [ ] 3.3 Add the `PageClass` dataclass, the text-layer and image signals, and `find_tables()` gated by cheap signals. Tighten column alignment. _R2 AC3-5_
  - [ ] 3.4 Add `PreClassification.page_classes` and carry the run-length wire format through the handshake JSON and the `PdfConvertRequest` body. _R2 AC6_
  - [ ] 3.5 Commit `scripts/page_class_census.py`. Check that the pocketbook census meets R2 AC3's bound and that classification time is ≤ 30 s. _R2 AC3, AC8_
- [ ] 4. Checkpoint P1: `make test`, open the PR, and ask the user.

- [ ] 5. P2: Page-class chunking and levers (`ICR-97-rfc52-page-class-chunking`)
  - [ ] 5.1 Chunker: run-length, absorb, split, with property tests P1/P2. _R3 AC1-2_
  - [ ] 5.2 Per-chunk `do_table_structure`/`do_ocr`, the `DOCLING_DO_OCR` override semantics, and alignment of the Mac `run.sh`. _R3 AC3_
  - [ ] 5.3 Keep the `force_full_page_ocr` escalation: add a test that recovery still forces OCR on text-layer pages. _R3 AC4_
  - [ ] 5.4 Add the `DOCLING_TABLEFORMER_MODE` config and the kill switch `PAGECLASS_CHUNKING`. _R3 AC6, R4 AC1_
  - [ ] 5.5 Write `scripts/conversion_bench.py`, run the 3 docs × 3 arms × 2 benchmark on the active remote, and write `audit/RFC052_CONVERSION_BENCH_<date>.md`. _R4 AC2-5_
  - [ ] 5.6 Choose the defaults from the benchmark (FAST only if it passes the R4 AC4 gate). Record the decision in the RFC as an amendment.
- [ ] 6. Checkpoint P2: `make test`, open the PR, and ask the user.

- [ ] 7. P3: Capacity split (`ICR-97-rfc52-capacity-split`)
  - [ ] 7.1 docling-service `GET /capacity`: free memory on Linux and macOS, `safe_procs`, slots, the `spp_ewma` tracker, `build_sha`. _R5 AC1_
  - [ ] 7.2 The planner clamps its worker count by free-memory `safe_procs`. _R5 AC2_
  - [ ] 7.3 Add `page_start`/`page_end` to `PdfConvertRequest`: slice, rebase pictures and page classes. _R5 AC3_
  - [ ] 7.4 The node controller writes `docling-active-backend` to a ConfigMap. The coordinator resolves the named Services from it. _R5 AC4_
  - [ ] 7.5 Coordinator: eligibility (HR3, build SHA), proportional initial allocation, tail stealing, the shared deadline, retry and re-route, merge, and join heading-shift count. _R5 AC4, AC6-8_
  - [ ] 7.6 Local gating: re-read `/capacity` per shard, hold the admission reservation, apply `PORTFOLIO_RESERVE_BYTES`. Set the docling-local manifest's limit to the planned peak and give it a low priority class. _R5 AC5_
  - [ ] 7.7 Measure docling-local idle RSS and decide replicas 1 or 0. Replace the 16 GB guard in `docling-node.sh`. _R5 AC9_
  - [ ] 7.8 HR3: exempt in-cluster backends for PII documents and never allow the Mac. Test the eligibility table. _R5 AC7_
  - [ ] 7.9 Add a stub-backend integration test for stealing, the deadline, and re-route after a failure. _Test strategy_
  - [ ] 7.10 Deploy with `DOCLING_SPLIT_ENABLED=0`. Run a parity check on the pocketbook with split off and on, plus a forced-low-memory run showing 0 local shards and no OOM events. _R5 AC10, R6_
- [ ] 8. Checkpoint P3: `make test`, open the PR, and get the user's go-ahead before switching on `DOCLING_SPLIT_ENABLED=1`.

## Notes

- Operator steps (1.9, 1.10) touch host iptables and the user's Mac. Claude prepares scripts; the user runs anything on hcloud.
- Commits carry no attribution lines. Never `git add -A`.
