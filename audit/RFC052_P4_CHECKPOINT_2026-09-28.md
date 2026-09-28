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
| 9.7 | Grid replacement (AC3) | changed cells <= 2% on covered pages | **FAIL**: Unfall 13.33%, GHV 20.10%; **AC3 dropped**, code removed | -- |
| 7.6 | Planner memory clamp (P3) | clamps under forced low memory, no OOM | **PASS**: 12 -> 3 processes; `safe_procs=0` -> 1 process; 0 OOM events | `DOCLING_RESERVE_BYTES` |
| 9.8 | Force-recovery census | tabulate cases | **0 cases** since P4 went live (`RFC052_FORCE_RECOVERY_CASES_2026-09-28.md`) | -- |

Production behaviour is unchanged by P4: every new switch is off.

## 9.1 Table capture

Pocketbook (292 pages), 2 runs per arm through the worker to the Mac.

| Arm | Median wall time | Worker `memory.peak` |
|---|---|---|
| capture off | 454.7 s | 1.15 GB |
| capture on | 510.5 s | 1.19 GB |

Ratio 1.123 against a 1.05 gate.

**Diagnosis** (worker Loki timeline, the on-arm sidecar in MinIO, per-page
timing on this host):

- The extra ~56 s is ~31 s in convert+join and ~22 s after it. Conversion
  itself took the same ~377 s in both arms; the +31 s is the full
  `TABLES_JOIN_GRACE_S` (30 s) spent waiting for a capture that could not
  finish.
- The pool ran **1 process** (not 2), took 406.9 s and still hit its
  deadline: `capture_failed` covers pages 3-7 and 75-291, so ~70 of 292 pages
  were scanned, at ~5.8 s each. Peak RSS 120 MB; no RSS kills.
- Per page, `find_tables()` is only ~1-2 s CPU. The rest is
  `table.to_markdown()` on the large text-strategy tables (3.0 s and 6.9 s for
  one table each; `extract()` on the same tables is 0.08-0.24 s). ~270 table
  pages x ~5 s is ~1,300 CPU-s, which cannot fit inside a ~377 s conversion.
  The design's "~125 s at 2 procs" estimate is ~6x low.
- The +22 s after the join fits `PendingTables.finalize` (anchoring, merging
  391 records, uploading a 12 MB sidecar that stores cells and markdown).
- The memory term of the pool size (`(free - 512Mi) / 256Mi`) gave 1 process
  on the 2-CPU worker (inferred; the pool is sized silently).

Fix options, most confident first: build markdown from the extracted cells
instead of `to_markdown()` (measured ~70-85% less CPU per page); await the
join in `finalize` so capture also overlaps the ~55 s tree build; slim the
sidecar; size the pool by CPU once per-page cost is down; log the capture
outcome from the worker.

## 9.5 Tables in search

`scripts/table_search_eval.py`, 13 pocketbook questions on doc `9cc52a11`,
two runs per arm (a hit needs both). Off 6/13, on 5/13. Regression:
`latin-america-pop-density-2023`. Seven questions miss in both arms, most of
them region-level economic indicators.

**Diagnosis** (search views rebuilt offline from the stored tree and
sidecar; the rebuild reproduces the +7,885 tokens; which nodes the LLM picked
is inferred, since the eval kept no selection logs):

- **Most misses are tree-side and hit both arms.** Region profiles are split
  into `_seg` children and the parent node's own `text` is empty; selecting
  the parent returns nothing because a node returns only its own text. This
  accounts for World, Americas, Northern America and Southern Europe. Docling
  also merged Jordan into Japan and Palau into Pakistan (lost headings).
- **Table anchoring collapsed:** 214 of 391 tables (pages 38-177) anchor to
  `Australia and New Zealand`; World's tables sit under Explanatory notes,
  Latin America's under Northern America. Suspected cause: non-table nodes
  with `start_index=None` defeat heading-page resolution.
- **No LLM descriptions:** all 391 are `fallback` ("Table with columns:
  <header>"), often garbled and never naming the region; the cause of the
  fallback is not logged. Capture hit its deadline on pages 75-291 (see
  9.1), so those pages have TableFormer tables only.
- **Budget drops by coverage alone:** 71 of 105 new table entries were cut,
  including the only copy of Africa's population table.
- The one regression (latam density) most likely comes from the new
  "type=table" prompt line pulling the pick to the Caribbean table.

Fix options, most confident first: expand an empty parent to its
descendants' text in search (lifts both arms); fix anchoring; make
descriptions name the page's region and log why the LLM path fell back;
dedupe the budget so it never drops the only copy; soften the table prompt
line.

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

## 9.7 Grid replacement (dropped)

**Dropped 2026-09-28 by the user.** The code, the `TABLES_TRUST_*` switches
and the bench `grid` subcommand are removed; RFC-052 R9 AC3 is struck. The
measurement below is kept as the reason.

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

## 7.6 Forced-low-memory run (P3 carry-over)

On the Mac (`4a282de`), `DOCLING_RESERVE_BYTES` forced in the service
launcher and checked through `/capacity`, pocketbook page slices:

| Run | Reserve | Pages | Planner log | Wall time |
|---|---|---|---|---|
| A | 21.1 GiB (27 GiB free) | 120 | `docling plan clamp: 12 -> 3 processes by free memory (27065 MiB free, safe_procs=3)` | 196.8 s |
| B | 64 GiB | 70 | `safe_procs=0 (26963 MiB free, 65536 MiB reserve, 1472 MiB per process); converting with 1 process anyway` | 185.7 s |

Cluster OOM events 0 before / 0 after. The reserve was restored to the
768 MiB default afterwards (`/capacity` confirms).

## Test flake fixed

`test_9_1_capture_pool_bound_page_partition_and_rss_kill` failed
intermittently: its real 2-process capture ran under the 256 MiB
`TABLES_PROC_BYTES` default, and a spawned child's baseline RSS on a loaded
host reached 271 MB and was killed. That step now runs with 1 GiB; the kill
itself is still tested at 64 MiB.

## Open decisions for the user

1. **9.1 / 9.5:** diagnosed above, not fixed. Build the capture and search
   fixes (a new task set), or leave capture and table search off and move to
   P5.
2. **HR4:** the table scan and grid use PyMuPDF (AGPL); the legal review stays
   deferred.
