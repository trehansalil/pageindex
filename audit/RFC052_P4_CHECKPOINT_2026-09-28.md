# RFC-052 P4 checkpoint (task 10, 2026-09-28)

P4: table capture (R7), tables in search (R8), signal-driven OCR/TableFormer
bypass (R9). Code merged in #39 (P4), #40 (tables-sidecar listing and
erasure fix) and #41 (grid bench fix, R9 AC1/AC3 amendment). Every live gate
below was run on the Mac docling-service; the capture runs went worker -> Mac.

## Summary

| Task | Feature | Gate | Result | Switch (default) |
|---|---|---|---|---|
| 9.1 | Table capture in the worker | median wall time <= 1.05x, `memory.peak` <= 1536Mi | **FAIL**: 1.123x (454.7 s -> 510.5 s); memory 1.15 / 1.19 GB PASS | `TABLES_CAPTURE=0` |
| 9.5 | Table nodes in search | on >= off, <= 1 regression | **FAIL**: 5/13 vs 6/13, 1 regression, +7,885 prompt tokens | `TABLES_DESC_ENABLED=0` |
| 9.6 | OCR bypass (AC1) | changed cells <= 2% | **PASS** on quality: 0.00% of 72,113 cells over 270 pages; **no time saved** | `TABLES_OCR_BYPASS=0` |
| 9.7 | Grid replacement (AC3) | changed cells <= 2% on covered pages | **FAIL**: Unfall 13.33%, GHV 20.10% | `TABLES_TRUST_BYPASS=0` |
| 9.8 | Force-recovery census | tabulate cases | **0 cases** since P4 went live (`RFC052_FORCE_RECOVERY_CASES_2026-09-28.md`) | -- |

Production behaviour is unchanged by P4: every new switch is off.

## 9.1 Table capture

Pocketbook (292 pages), 2 runs per arm through the worker to the Mac.

| Arm | Median wall time | Worker `memory.peak` |
|---|---|---|
| capture off | 454.7 s | 1.15 GB |
| capture on | 510.5 s | 1.19 GB |

Ratio 1.123 against a 1.05 gate. Capture is meant to overlap remote
conversion; the ~56 s it adds says it does not overlap enough. Not profiled.

## 9.5 Tables in search

`scripts/table_search_eval.py`, 13 pocketbook questions on doc `9cc52a11`,
two runs per arm (a hit needs both). Off 6/13, on 5/13. Regression:
`latin-america-pop-density-2023`. Seven questions miss in both arms, most of
them region-level economic indicators. Not diagnosed.

## 9.6 OCR bypass

| Document | Pages the bypass covered | Changed cells | Wall time off -> on |
|---|---|---|---|
| Pocketbook | 270 of 292 (256 with tables) | 0.00% | 372.6 s -> 376.2 s (controlled pair) |
| Unfall | 0 (page 2 tables are 32% filled; one 3-page chunk) | -- | -- |
| GHV | 0 (numeric price tables fail the garble screen, by design) | -- | -- |

A first uncontrolled pocketbook pair read 304.5 s -> 377.3 s; the rerun pair
above is the one to quote, and run-to-run spread on the Mac is ~70 s. Docling
runs OCR only on bitmap regions, so skipping it on text-layer pages saves
almost nothing.

Chunks 275-287 report `bypass="tableformer"` with reasons `ac1` + `ac2`.
That is correct: `bypass="ocr"` is reported only when the bypass removed OCR
the page-class decision would have run, and page-class policy had already
turned OCR off on those pages.

## 9.7 Grid replacement

As first written, AC3 required every ruled table on a page to hold >= 50% of
its text alone and fired on 0 pages of `doc_store`. #41 amended AC1 and AC3
to judge a page's tables together (cell-weighted fill; union coverage). With
that, recorded runs on the Mac (branch `9eb0803`):

| Document | Pages grid-replaced | Changed cells | Wall time off -> trust on |
|---|---|---|---|
| Unfall | 3 of 3 | 13.33% | 13.3 s -> 8.2 s |
| GHV | 1 of 1 | 20.10% | 13.4 s -> 7.4 s |
| Pocketbook | 0 (ruled boxes hold 7-25% of each page; aligned text outside) | NOT MEASURED | -- |

Single runs; the timings are indicative only. The differences are real
structure errors: the `find_tables()` grid merges row labels into value cells,
leaves the rest of the row empty, and drops spanned-header repeats so values
shift columns. The quality verdict was the same with the bypass on and off
(these one-to-three-page documents fail the tree gate on depth/node count
either way).

## Open decisions for the user

1. **R9 AC3 (grid replacement):** drop it, or keep it switched off as a
   measured negative result. It is fast (~40% less time on small ruled
   documents) but changes 13-20% of cells.
2. **9.1 / 9.5:** diagnose capture overhead and the table-search miss, or
   leave capture and table search off and move to P5.
3. **7.6** (forced-low-memory run) is still open from P3.
4. **HR4:** the table scan and grid use PyMuPDF (AGPL); the legal review stays
   deferred.
