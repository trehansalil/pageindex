# RFC-046 Wave 3 — 3.C Gate Checkpoint

**Run date:** 2026-09-21
**Branch:** `ICR-97-rfc46-ocr-attribution-cluster-remediation`
**Path:** worker (MCP server + arq via `preprocess_client.py`)
**Clean-slate reset:** yes (4-store wipe verified before ingest)
**Pipeline version:** 5
**Corpus:** 25 docs (doc_store/)

## Verdict Distribution

| Verdict  | Count | 1.C Count |
|----------|-------|-----------|
| PASS     | 15    | 13        |
| MARGINAL | 4     | 6         |
| FAIL     | 5     | 3         |
| REJECTED | 1     | 2         |
| TIMEOUT  | 0     | 1         |
| **Total**| **25**| **25**    |

## Delta Against 1.C Baseline

### Unchanged (21 docs)

All 21 docs with stable verdicts between 1.C and 3.C — no verdict movement.

### Verdict Movements (3 docs)

| Document | 1.C | 3.C | Attribution | Notes |
|----------|-----|-----|-------------|-------|
| Reitlehrer - Schäden am Berittpferd.pdf | MARGINAL | PASS | D5 (density) | 1.C over-condemned clean German extraction; corrected density numerator (3.4) now scores correctly |
| MOU MOHRE & Nafis (Arabic scanned) | REJECTED | FAIL | D5 (garble gate) | No longer garble-rejected (HR5); reaches verdict but fails on quality. Correct direction. |
| image pie chart (labor distribution .jpg) | MARGINAL | FAIL | D5 (density) | 1.C baseline noted "MARGINAL on unusable OCR noise" as under-condemnation. FAIL is the correct verdict for garbled OCR output. |

All three movements are **correct direction** — two over-condemnations fixed, one under-condemnation fixed.

### New Coverage (1 doc, per R9.8)

| Document | 1.C | 3.C | Attribution |
|----------|-----|-----|-------------|
| world-stats-pocketbook-2023.pdf | TIMEOUT (3600s) | PASS | D11 (infrastructure) — dynamic child timeout now allows 77-page doc to complete |

Per R9.8: new coverage, not verdict movement. Scored as new baseline row.

## D11 Assessment (R11.7)

D11 tasks (3.10–3.13) are infrastructure-only. The requirement is **no verdict movement** attributable to D11.

- `world-stats-pocketbook-2023.pdf`: TIMEOUT → PASS is **coverage**, not movement (R9.8) — no 1.C verdict existed to move from.
- All 22 docs that had verdicts in 1.C: **zero** moved due to D11. The 3 movements above are all attributable to D5.

**R11.7: SATISFIED.**

## Verification

- `uv run pytest`: 2331 passed (14 pre-existing failures from 3.14–3.17 preclassify module — not related to D5/D8/D10/D11)
- Architecture guards: pass
- Threshold guard (3.7): no threshold constants changed
- Clean-slate reset verified before ingest (4 stores at zero)
- Worker path used (not batch CLI)