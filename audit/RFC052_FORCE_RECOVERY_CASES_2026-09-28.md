# RFC-052 force-recovery case census (task 9.8, 2026-09-28)

R9 AC7: tabulate every forced conversion's recovery context (document, why
force was set, first-pass per-chunk OCR/TableFormer decisions, whether the
recovered garble sat on TableFormer pages) before any policy acts on it.

## Source and window

- Loki, in-cluster Service `infra/loki` (`10.43.94.207:3100`). The Tailscale
  gateway is push-only and refuses queries.
- Retention is **3 days**; the oldest retained worker line is
  2026-09-25 13:21 UTC.
- The `docling_force_recovery` record exists only from P4 on. P4 went live on
  the worker at **2026-09-28 04:48 UTC** (image `sha-c80280c`, now
  `sha-49f3b07`); the Mac docling-service ran P4 code from the same day.
- Streams searched: every namespace except `infra`, plus the Mac's pushed
  `{host="mac", service="docling-service"}` stream.

## Result: 0 cases

| Query | Window | Hits |
|---|---|---|
| `docling_force_recovery` (worker, docling-service, Mac) | retention | **0** |
| worker conversions (distinct `job_id` with decision records) | since P4 | 4 (the 9.1 capture runs, pocketbook) |
| worker `force_full_page_ocr_decision` | since P4 | 0 |
| garbling escalation to `force_full_page_ocr` | since P4 | 0 |

No conversion since P4 took the force path, so there is nothing to tabulate
and the first-pass join on `job_id` has no rows.

## Pre-P4 context (not a census row)

One garbling escalation falls inside retention but before P4, so no
`docling_force_recovery` record could exist for it:

| When (UTC) | job_id | doc (name sha8) | Trigger | Outcome |
|---|---|---|---|---|
| 2026-09-27 00:54 | `a3698898-7fe5-4eb6-868a-75a420a93f8c` | `41533f9d` | tree garbling -> `force_full_page_ocr`, langs `eng`+`deu` | recovery converter child killed after timeout |

Four pre-P4 `force_full_page_ocr_decision` records in the window are all
`not_forced` (`no force trigger`).

## What this means

- The census cannot yet say whether recovered garble lands on TableFormer
  pages: the path has not run since the record was added.
- Loki keeps 3 days, so a census has to be re-run within 3 days of any
  forced conversion, or the retention raised, before the R9 AC7 question can
  be answered from logs.
- No policy may act on force-recovery context until real cases exist (R9 AC7).
