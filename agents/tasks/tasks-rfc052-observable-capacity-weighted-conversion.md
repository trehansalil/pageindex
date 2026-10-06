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
  - Report: `audit/RFC052_P4_CHECKPOINT_2026-09-28.md`. Decision 1 (9.1/9.5) was answered by #43/#44 and the 2026-10-04 decision above. Decision 2 (HR4 legal review) stays deferred as an open item; it does not block P5.

- [ ] 11. P5 (later): Mac + docling-1 together (`ICR-97-rfc52-mac-docling1-split`)
  - [x] 11.1 The node controller keeps docling-1 up alongside the Mac when split is on (`DOCLING_SPLIT_KEEP_NODE`); the coordinator finds live backends by probing the named Services' `/capacity` (amendment A-P5-1, replacing the original ConfigMap plan). _R5 AC4_ **Built 2026-10-04 (infra branch `rfc52-p5-split-keep-node`, 560fbc5):** `DOCLING_SPLIT_KEEP_NODE=1` starts docling-1 beside a healthy Mac when jobs wait (daily cap and idle reaper still apply); routing stays on the Mac. No ConfigMap: see amendment A-P5-1 in the RFC.
  - [x] 11.2 Coordinator: eligibility (HR3, build SHA), proportional initial allocation, tail stealing, per-shard `/capacity` check, the shared deadline, retry and re-route, merge, and join heading-shift count. _R5 AC4, AC6_ `client/split.py` (97e3794); `_remote_pdf_convert` hands unsliced calls to it when `DOCLING_SPLIT_ENABLED=1`.
  - [x] 11.3 HR3: docling-1 eligible for PII documents, the Mac never. Test the eligibility table. _R5 AC7_ `TestSplitCoordinator::test_hr3_eligibility_table_and_build_match`.
  - [x] 11.4 Add a stub-backend integration test for stealing, the deadline, and re-route after a failure. _Test strategy_ `TestSplitCoordinator::test_stub_backends_steal_reroute_and_share_the_deadline` (8a1d13f).
  - [x] 11.5 Deploy with `DOCLING_SPLIT_ENABLED=0`. Run a parity check on the pocketbook with split off and on. _R5 AC10, R6_
    - **Result 2026-10-04: quality PASS, speed FAIL.** Worker and both backends on `d77eb13`.
      | Arm | Wall time | Verdict | node_count | Join heading shifts |
      |---|---|---|---|---|
      | split off (Mac only) | 619 s | PASS | 773 | -- |
      | split on (Mac + docling-1 cpx62) | 1189 s | PASS | 774 (+0.13%) | 0 |
    - The split step took 974 s: Mac 237 pages in 2 shards (done ~16:05), docling-1 55 pages in 1 shard (done 16:15:57); 0 retries, 0 reroutes. The Mac sat idle for ~10 min.
    - **Cause:** docling-service ran the 55-page range as **one process** (`docling plan: workers=1, threads_per_worker=16, pages_per_chunk=55, safe_procs=9`). `_plan_from_total` runs any request of <= `PARALLEL_MIN_PAGES` (60) pages as a single process, and Docling barely scales with threads: ~17.7 s/page. The coordinator also sized docling-1's share from the 40 s/page prior (no samples) and cannot take over an in-flight shard. Fix proposed as A-P5-5.
    - First attempt blocked: Hetzner retired the datacenter API, so every docling-1 start failed (`jq: invalid JSON text passed to --argjson`). Fixed in infra #22 (`ed23625`): stock now read from `server-type` `locations[].available`.
    - Unrelated, seen in the same run: 2,440 Azure `AuthenticationError` (invalid subscription key) from the tree-build LLM calls. The tree still passed; needs its own look.
    - Cleanup: both switches unset, docling-1 deleted, test docs `07a03e14` and `045bc5d2` erased (no errors, no partial purge, no MinIO residue), hash entry restored, kept doc `a3779d3d` untouched.
  - [x] 11.6 Implement A-P5-5: shared chunk queue with per-backend `chunk_limit`, tail copies, one presigned URL per document (`client/split.py`); `plan_slice`, slice slots held as a group, shared download (docling-service). Tests: `test_docling_capacity_client.py` (pull/limit, copy, build roll, re-route, second failure, deadline), `test_docling_capacity.py` (shared download, HR2 eviction, group slot). _A-P5-5_
  - [ ] 11.7 Re-run the 11.5 parity check on the A-P5-5 build (pocketbook, split off vs on; verdict, node_count ±3%, wall time below split off). _R6, A-P5-5_
    - **Result 2026-10-05: speed PASS, verdict PASS, node_count FAIL (+4.66%).** Worker and both backends on `e2aad90` (Mac 12 slice slots, docling-1 cpx62 15).
      | Arm | Wall time | Verdict | node_count | Walked tree nodes |
      |---|---|---|---|---|
      | split off (Mac only) | 556 s | PASS | 773 | 1000 |
      | split on (Mac + docling-1) | 497 s (-11%) | PASS | 809 (+4.66%) | 1005 (+0.5%) |
    - Split step: 288 s, 50 shards of 6 pages, Mac 199 pages / 34 shards, node 93 pages / 16 shards; 19 tail copies, 2 copy wins, 0 retries, 0 reroutes, 0 join heading shifts. The conversion went from 974 s (11.5) to 288 s; the rest of the wall time is tree build.
    - **Cause of the node_count gap (diffed 2026-10-05): not the split.** Docling output is identical across arms: 265 `docling_tableformer` tables in both, and the pages that differ came from both backends (shards 26-27 docling-1, 28-29 Mac). The gap is the worker-side PyMuPDF `find_tables` capture in `tables.json`, which runs alongside conversion and stops at a deadline: `capture_failed` `[169, 291, "deadline"]` (off, 387 s) vs `[150, 291, "deadline"]` (on, 320 s), 0-based. The faster arm captured 19 fewer pages (151-169), i.e. 2 x 19 = 38 fewer `pymupdf_find_tables` tables (157 -> 138 per strategy), and the tree on those pages has more heading nodes and fewer table nodes. Real loss from that: a few values held only in the PyMuPDF tables (e.g. Maldives "Rufiyaa (MVR)" p169, a capital-city population "537.6" p164). Some "only in off" text is just the cell-split duplicate variant ("date 16 \| December 196 \| 3").
    - **Pre-existing, affects both arms:** table capture covers ~150-170 of 292 pages in either mode and its coverage tracks wall time, so `node_count` is not reproducible across runs of different speed. The ±3% node_count bar measures this, not split parity. Fixed on `ICR-97-rfc52-table-capture-budget`: the join waits for capture to finish (child deadline minus `TABLES_DEADLINE_MARGIN_S`), pages are queued in 30-page ranges and the pool grows once the converter child frees memory. Re-run 11.7 on that build: node_count should then match, at some wall-time cost if capture outlasts the tree build. Table page attribution is also off by ±1 page in both arms (Kenya, PDF p153: off puts it at 151 plus a duplicate at 153, on at 152).
    - **Re-run 2026-10-05 on 38ce68b (PR #47 merged): still FAIL.** Both backends on 38ce68b (a first split attempt with docling-1 still on e2aad90 was excluded by the build-sha gate and ran `single_backend` on the Mac; discarded).

      | Arm | Wall | Verdict | node_count | Walked | Capture |
      |---|---|---|---|---|---|
      | split off | 708 s | PASS | 773 | 1141 | 1 proc, 633 s, 0 failed, 789 tables / 265 pages |
      | split on (mac:194 + node:98 pages, 50 shards, 19 copies) | 692 s | PASS | 809 (+4.66%) | 1172 | 1 proc, 618 s, 0 failed, 789 tables / 265 pages |

    - **The capture diagnosis above was wrong for node_count.** Capture is now identical in both arms, yet node_count is 809 vs 773, the same +36 as the first run. The tree diff shows the gap is in the split's markdown: with 6-page shards Docling assigns different heading levels (front matter and region headings one level up) and promotes some country names to headings (Angola, British Virgin Islands). `split_join_heading_shifts` is 0, so the join does not reconcile it. This is a split-parity defect, not a capture one.
    - **New wall-time regression from PR #47:** capture now runs to completion but never grows past 1 process (~620 s), so it is the critical path: split off 556 → 708 s (+27%), split on 497 → 692 s. Split saves only 2%. Cause: worker limit 1536 MiB; the converter child (~630 MB) and arq (~280 MB) leave ~460-690 MiB free, below `TABLES_RESERVE_BYTES` (512 MiB) + `TABLES_PROC_BYTES` (256 MiB), so `_room` never admits a second process. Likely breaches R7 AC3 (≤5%), but unverified: AC3 compares same-remote capture-on/off medians, and the 556/497 s references come from an earlier run whose capture stopped at conversion + 30 s, not from a capture-off control. Run that control before calling AC3.
    - Test docs `1d459539` and `e9c3f54b` erased after the diff. Worker log and chunk records: scratchpad `parity/`.
    - Cleanup done: both switches unset, docling-1 deleted, hash entry unchanged (`0a172475…`), kept doc `a3779d3d` untouched.
    - **Root cause of the heading gap (2026-10-05): each Docling call levels its own headings.** The style path of docling-hierarchical-pdf (used when the PDF has no outline, as here) clusters `first_cell.rect.height`, which is glyph-ink height: "Angola" (descender) ranks above "Albania" although both are 14 pt. `headings._relevel_headings` then lifts each call's shallowest heading to H1. Different chunking gives different levels, and that affects the unsplit arm too, since the service runs it as ~30-page chunks. On the Mac, pages 24-53 as one call vs five 6-page shards gave the same 27 headings on the same pages, with only the levels differing (shard 30-35 all H1, the same headings H2 in the single call).
    - **Fix (`ICR-97-rfc52-split-heading-levels`, `converters/joined_headings.py`):** after any join of two or more Docling calls (the worker after a split or a service chunked result, and the service's own chunked join), heading levels are re-derived document-wide from the PDF's font size and bold (pypdfium2, HR4), with headings placed by `heading_pages`. It skips documents where more than 30% of headings are numbered (containment levels win there), documents where fewer than 80% of headings can be placed, and documents with fewer than two headings. Decision event `joined_heading_relevel`. Verified offline: pages 24-53 give identical levels for the one-call and shard joins. The full 49-shard join has 255 headings, raw 171 at H1 / 81 at H2 / 3 at H3; after the relevel all 225 region/country headings are H2, and the other 30 (cover, contents, explanatory notes, indicator legends) take levels 1 and 3-6 by font. The unsplit full-document side could not be reproduced on the Mac: requests of ≥29 pages hang there (`busy_slots` stuck at 1, `spp_samples` 152→153 in an hour) while 6-page requests succeed. That is a separate Mac service fault. 11.7 stays open until the parity rerun on the merged build.
    - **Mac hang root cause (2026-10-05, `ICR-97-rfc52-docling-preempt-cancel-leak`): a leaked slice slot, not slow conversion.** `convert_pdf` stopped its queued-time `_preempt_while_queued` loop with `task.cancel()` and then awaited it. That loop spends most of its time in starlette's `Request.is_disconnected()`, whose self-cancelling anyio `CancelScope` swallows a cancel that lands inside it (anyio 4.13 / starlette 1.0; reproduced 8-9 lost cancels in 20 trials). One of the 49 concurrent shards hung after admission: 48 of 49 got a plan, and its 11:40 cancel logged no exit. `_slice_active` stayed at 1, so the slice group never gave the whole-document slot back (`busy_slots` 1 = `_convert_slots.held`, `busy_slice_slots` 1 = `_slice_active`), and every request over `SLICE_MAX_PAGES` queued until its client gave up. The 500s were MinIO 403s on the experiment's expired presigned URL. Fix: stop the loop through an `asyncio.Event`, and end `_watch_client` on `conv.finished`. The regression test fails (rather than hangs) on the old code.
  - [ ] 11.8 Merge PR #50 (preempt loop stopped by an event, watcher ends on `conv.finished`). Redeploy docling-service on the Mac (`macos/update.sh`) and re-bake the docling-1 snapshot. _A-P5-6, design P24_
  - [x] 11.9 Slot conservation and wedge state in docling-service:
    - `conv.admitted_at`, plus `leaked_slots` and `overdue_s` in `/capacity`;
    - a background `wedged_since` check;
    - `/health` 503 `wedged` after `DOCLING_WEDGE_GRACE_S`;
    - `docling_service_wedged` decision logged (amended 2026-10-06: not registered in `obs/decision_points.py`, which indexes `pageindex_mcp` modules only; no docling-service event is registered there; the 11.13 alert keys on the Loki `event` label, which does not need the registry);
    - self-exit after `DOCLING_WEDGE_RESTART_S` (0 = report only).
    - Tests: a leaked slice slot (admitted, never started) reads `leaked_slots` 1 and turns `/health` 503 after the grace; an overdue conversion does the same; a normal admission inside the grace stays 200; the restart path calls the exit hook once.
    - _A-P5-6 AC1-3, design P21_
    - **As built (2026-10-06, PR #50):**
      - Admission is tracked in two steps: `conv.slot_at` when `_admit_unless_cancelled` wins a slot, and `conv.admitted_at` once the preempt loop has stopped. Both are cleared on release.
      - `leaked_slots` = held slots beyond what the holders explain (whole-document holders + `/convert/image` holders + `MAX_CONCURRENT` while a slice group runs), plus `_slice_active` beyond the slice holders, plus holders still without `admitted_at` after `ADMIT_STALL_S` (10 s).
      - That last term is what catches the 2026-10-05 hang: the request held its slot alive and never got past admission. Counting it from `slot_at` alone would have read 0.
      - `_wedge_monitor` is stopped by an event (P24).
      - `/health` also reports `leaked_slots`, `overdue_s` and `wedged_s` when healthy.
      - The restart path goes through a replaceable `_exit` hook.
      - **Deviation:** `docling_service_wedged` is not in `obs/decision_points.py`. No docling-service event is registered there (`docling_request_cancelled` neither), since the registry indexes `pageindex_mcp` modules only.
      - Also added for 11.13: `POST /debug/leak-slot`, which is 404 unless `DOCLING_DEBUG_FAULTS=1`.
  - [x] 11.10 Coordinator per-shard time limit (`DOCLING_SPLIT_SHARD_MIN_S`, `DOCLING_SPLIT_SHARD_FACTOR`), backend demotion after 2 failed shards, and no chunks to a backend with `leaked_slots > 0`. Stub-backend tests:
    - a stuck shard is retried on the other backend within the limit;
    - with one backend live, a stuck shard is retried on the same backend after the limit, not at the shared deadline;
    - a backend with two 5xx shards gets no further chunks.
    - _A-P5-6 AC4-5, design P22_
    - **As built:**
      - The time limit is applied as `read_timeout_s = min(remaining, limit)`.
      - A chunk failing within `_LIMIT_SLACK_S` (10 s) of a binding limit counts as `shard_timeouts`. The service's X-Deadline 499 lands about 5 s before the read timeout.
      - Demotion counts every non-`DoclingUnavailable` failure, including a copy that lost while the other copy still ran.
      - A backend reporting `leaked_slots > 0` is excluded at probe time (`docling_split_backend` choice `wedged`) and by `_still_accepts` before a retry or copy.
      - Test: `test_shard_time_limit_demotion_wedged_backend_and_reuse`, which also covers 11.11.
  - [x] 11.11 Keep finished shards across conversion retries: `_shard_results` in the converter child, keyed by staging key, build, range and options digest. Test: a split whose first attempt fails on one shard re-dispatches only that shard on the retry (`shards_reused` = n-1). _A-P5-6 AC6, design P23_
  - [x] 11.12 Download errors return 502 `download failed (<status>)`, not 500. Test with an expired-URL 403 stub. _A-P5-6 AC7_
  - [ ] 11.13 Grafana alert on `docling_service_wedged` (infra companion). Live fault test on the Mac and docling-1:
    - force a leaked slot with a debug-only fault switch, never on in production;
    - check that `/health` turns 503 within the grace, that the controller routes `docling-active` to docling-1, and that the service restarts after `DOCLING_WEDGE_RESTART_S`;
    - in a split run, kill one backend mid-document and record the reroutes, demotion and wall time.
    - _A-P5-6_
- [ ] 12. Checkpoint P5: `make test`, open the PR, and get the user's go-ahead before switching on `DOCLING_SPLIT_ENABLED=1`. The go-ahead covers A-P5-1..6; 11.7 (parity) and 11.13 (fault test) must pass first.

## Notes

- No operator steps remain for 1.9 and 1.10 beyond a one-time `install.sh` on the Mac. Claude still never runs hcloud mutations.
- Commits carry no attribution lines. Never `git add -A`.
