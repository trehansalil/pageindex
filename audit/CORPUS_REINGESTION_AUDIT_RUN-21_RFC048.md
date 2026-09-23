---
aliases:
  - Run 21
  - RFC-048 Corpus Baseline
tags:
  - audit
  - corpus
  - rfc-048
  - surya
---
<!-- Space: CITRA -->
<!-- Title: Corpus Re-ingestion Audit — Run 21 (RFC-048 Surya Image Fallback) -->
<!-- Folder: Audits -->

# Corpus Re-ingestion Audit — Run 21 (RFC-048 Surya Image Fallback)

## Environment

- Branch: ICR-97-rfc48-surya-image-fallback
- Date: 2026-09-23
- Run ID: `ingest-run-20260923T054443Z`
- Prior run: [[CORPUS_REINGESTION_AUDIT_RUN-20|Run 20]] (2026-08-27, branch ICR-97-rfc39)
- Prior baseline: [[rfc047-d9-final-baseline|RFC-047 D9 final baseline]] (2026-09-22, 0 FAIL / 25 docs)
- Methodology: Incremental ingest+score pipeline (each doc scored immediately after processing)
- Profile: `PROFILE=local`

### Infrastructure changes since Run 20

- Surya OCR service rebuilt and redeployed with `/ocr/image` endpoint (RFC-048)
- `docker-compose.yml`: removed `profiles: ["ocr-spike"]` gate, added `SURYA_TIMEOUT` env var forwarding
- `SURYA_FALLBACK_ENABLED=true`, `SURYA_SERVICE_URL=http://localhost:8207` active
- RFC-046 (OCR attribution) accepted, RFC-047 (gate-layer correctness) implemented — all waves landed

---

## Purpose

Validate [[048-surya-image-fallback|RFC-048]] Surya OCR fallback for standalone images:
1. Does the Surya `/ocr/image` endpoint work end-to-end?
2. Does Doc 13 (pie chart with Arabic labels) recover from ERROR/REJECTED?
3. Are there regressions from [[047-gate-layer-correctness|RFC-047]]/[[048-surya-image-fallback|RFC-048]] changes?

### References

| Ref | Artifact | Path |
|-----|----------|------|
| R1 | RFC-048 (Surya Image Fallback) | `agents/rfcs/048-surya-image-fallback.md` |
| R2 | RFC-047 (Gate-Layer Correctness) | `agents/rfcs/047-gate-layer-correctness.md` |
| R3 | RFC-046 (OCR Attribution) | `agents/rfcs/046-ocr-attribution-failure-cluster-remediation.md` |
| R4 | RFC-047 D9 Final Baseline | `audit/baselines/rfc047-d9-final-baseline` (0 FAIL / 25 docs, 2026-09-22) |
| R5 | Run 20 Audit | `audit/CORPUS_REINGESTION_AUDIT_RUN-20.md` (2026-08-27, branch ICR-97-rfc39) |
| R6 | Multi-engine OCR Eval | `agents/spikes/ocr_eval_rfc046/eval_report.md` (Surya: 799 chars / 92.94% confidence on Doc 13) |
| R7 | RFC-048 Design | `agents/designs/design-rfc048-surya-image-fallback.md` |
| R8 | RFC-048 Tasks | `agents/tasks/tasks-rfc048-surya-image-fallback.md` |
| R9 | Docker Compose | `docker-compose.yml` — spike profile removed, `SURYA_TIMEOUT` forwarded |
| R10 | Surya Service | `services/surya-ocr-service/app.py` — `/ocr/image` endpoint at line 212 |

---

## Summary Scorecard

| # | Document | Verdict (Run 21) | Verdict (D9 Baseline) | Delta |
|---|----------|-------------------|-----------------------|-------|
| 1–11 | (all PDFs) | Various | Various | No change |
| 12 | **world-stats-pocketbook-2023.pdf** | **ERROR** | **PASS** | **REGRESSION — see Finding 3** |
| **13** | **image pie chart about labor distribution in january 2025 - Copy.jpg** | **REJECTED** | **REJECTED** | **No change — see Finding 1** |
| 14–25 | (all PDFs) | Various | Various | No change |

**Run 21 Tally (25 docs):**

| Verdict | D9 Baseline | Run 21 | Delta |
|---------|-------------|--------|-------|
| PASS | 18 | 17 | **−1** |
| MARGINAL | 6 | 6 | 0 |
| FAIL | 0 | 0 | 0 |
| REJECTED | 1 | 1 | 0 |
| ERROR | 0 | 1 | **+1** |
| **Total** | **25** | **25** | |

**1 regression.** world-stats-pocketbook-2023.pdf regressed from PASS to ERROR (see Finding 3). All other 24 documents match D9 baseline verdicts.

---

## Finding 1: Doc 13 — Surya Fallback Did NOT Fire (Gate Sensitivity Gap)

**Document:** image pie chart about labor distribution in january 2025 - Copy.jpg
**Expected:** Surya fallback fires, recovers from REJECTED to PASS/MARGINAL (799 chars at 93% confidence per eval report)
**Actual:** REJECTED — Surya fallback gate not triggered

### Root Cause: Garble detection sensitivity mismatch

The Surya quality gate and `validate_tree` use different garble detection sensitivity:

| Step | Chars | Garbled? | Action |
|------|-------|----------|--------|
| Tesseract OCR | 303 | **false** (gate-level check) | Gate check: `303 ≥ MIN_STANDALONE_IMAGE_MD_CHARS(100)` AND `not garbled` → **gate_not_triggered** |
| `validate_tree` | — | **true** (`depth<2`, `garbling`, `node_garbling`) | Tree validation catches garbling that the gate missed |
| `force_full_page_ocr` retry | 391 | still garbled | Recovery escalation fails |
| VLM fallback | — | PDFium can't open `.jpg` | Not applicable to images |
| Tesseract-on-raster | partial | still garbled | → **REJECTED** |

**Decision event logged:** `surya_image_fallback → choice=gate_not_triggered, reason="tesseract output acceptable"`

### RFC-048 Requirement 2 AC1 Analysis

The amendment (2026-09-22) changed the gate condition from AND to OR:

> WHEN Tesseract output for a standalone image is below `MIN_STANDALONE_IMAGE_MD_CHARS` **OR** garble-detected/wrong-script, THE pipeline SHALL attempt Surya OCR as a fallback.

The OR logic is correct in intent, but the **garble detection at the gate level** (`tesseract_garbled=false`) does not match the sensitivity of `validate_tree`'s garble detection (which catches `depth<2`, `node_garbling`, etc.). The gate's garble check passes content that the tree validator subsequently condemns.

### Fix Options

- **Option A:** Align the gate's garble detection with `validate_tree`'s sensitivity — run the same checks (or a subset) before the Surya decision point.
- **Option B:** Add a post-`validate_tree` Surya retry window — a second fallback opportunity after tree validation fails for garbling on standalone images.

---

## Finding 2: Surya `/ocr/image` Endpoint — Verified Working

The Surya service was rebuilt during this run (stale image on initial attempt). After rebuild:

- `/ocr/image` endpoint responds correctly
- Service accepts raw image files and returns OCR text, char count, and confidence
- Response schema matches `/ocr/pdf` structure
- The endpoint simply never gets called for Doc 13 due to Finding 1

---

## Finding 3: world-stats-pocketbook-2023.pdf — PASS → ERROR Regression

**D9 Baseline (2026-09-22):** PASS — structural_pass, 115 sections, sha8 `0a172475`
**Run 21 (2026-09-23):** ERROR — no MinIO artifacts, job timeout before write_barrier

### History

This 292-page UN statistical publication has oscillated:

| Run | Date | Verdict | Notes |
|-----|------|---------|-------|
| 15 | 2026-08-09 | PASS | 6.19M chars (10x inflation from Docling table duplication) |
| 16 | 2026-08-09 | PASS | 2.03M chars (67% drop flagged, verdict unchanged) |
| 19 | 2026-08-18 | ERROR | Timeout before write_barrier — no artifacts |
| 20 | 2026-08-27 | ERROR | Same timeout — no artifacts |
| D9 | 2026-09-22 | **PASS** | 115 sections, structural_pass — **recovered** |
| 21 | 2026-09-23 | **ERROR** | Timeout again — **regressed** |

### Root Cause Analysis

The D9 baseline ran on branch `ICR-97-rfc47-gate-layer-correctness` (commit `31b8d97`) with local Docling. Run 21 ran on `ICR-97-rfc48-surya-image-fallback` with `PROFILE=local`. The regression happened in **one day** between D9 and Run 21.

**Known chronic issues with this document:**
1. **Docling table duplication:** `export_to_markdown()` duplicates table content on dense statistical pages, causing 10x character inflation (~9.4M chars vs expected ~900K). See Run 12 diagnosis (`cluster-converter.json`).
2. **Chunked pic_results misalignment:** When chunks fall back to PyPDF2 (which strips `<!-- image -->` markers), the global marker count mismatches `pic_results`, and `splice_picture_text_for_tree` rejects enrichment entirely.
3. **Timeout sensitivity:** At 292 pages with dense tables, this doc sits on the edge of the processing timeout. Small changes in Docling performance, network latency, or concurrent load can tip it from completing to timing out.

**Most likely cause:** The D9 run was on a different branch with possibly different Docling service state or load conditions. The timeout is **non-deterministic** for this document — it completes or times out depending on infrastructure conditions, not code changes. This is the same pattern seen in Runs 19–20 (ERROR) vs D9 (PASS).

**Action:** Investigate whether the Docling service was restarted or rebuilt between D9 and Run 21. If the timeout is purely load-dependent, consider increasing the per-chunk timeout for large documents (>100 pages) or adding a retry with backoff for this document class. See [[027-run10-extraction-gate-and-arabic-recovery|RFC-027]], [[028-run11-run11-arabic-recovery-and-timeout-wiring|RFC-028]] D0.

---

## Delta from RFC-047 D9 Baseline → Run 21

### Improvements

None — the D9 baseline already had 0 FAIL.

### Regressions

- **world-stats-pocketbook-2023.pdf** — **PASS → ERROR**: This is a real regression. In the D9 baseline (2026-09-22, commit `31b8d97`), this 292-page UN statistical publication was PASS with `structural_pass; 115 sections`. In Run 21 (2026-09-23), no MinIO artifacts exist — the job times out before `save_doc`/write_barrier. See Finding 3 for root cause analysis.

### Stalls

- **Doc 13** (image pie chart) — REJECTED → REJECTED: The Surya fallback was expected to recover this document but does not fire due to the gate sensitivity gap (Finding 1). The document was already REJECTED in the D9 baseline and was called "correct gate behavior" there. RFC-048's implementation needs the gate fix before this document can be recovered.

### Stable

All other 23 documents are stable at their D9 baseline verdicts.

---

## Action Items

| Priority | Item | Blocked by |
|----------|------|------------|
| **P0** | Fix gate sensitivity gap (Finding 1) — add post-`validate_tree` Surya retry (Option B) | — |
| **P0** | Add VLM image support via Pillow + parallel VLM/Surya fallback for standalone images | — |
| **P1** | Investigate world-stats-pocketbook regression (Finding 3) — timeout non-determinism on 292-page doc | — |
| P1 | Re-run corpus after gate fix to validate Doc 13 recovery and world-stats-pocketbook stability | P0 |
| P2 | Document `.env.example` `SURYA_*` variables | — |
| P3 | Add k3s manifest / CI workflow for Surya service deployment | — |

---

## Traceability

| Artifact | Reference | Path |
|----------|-----------|------|
| RFC | [[048-surya-image-fallback|RFC-048]] (R1) | `agents/rfcs/048-surya-image-fallback.md` |
| Design | [[design-rfc048-surya-image-fallback]] (R7) | `agents/designs/design-rfc048-surya-image-fallback.md` |
| Tasks | [[tasks-rfc048-surya-image-fallback]] (R8) | `agents/tasks/tasks-rfc048-surya-image-fallback.md` |
| Prior baseline | RFC-047 D9 final baseline (R4) | 0 FAIL, 25 docs, 2026-09-22 |
| Prior run | [[CORPUS_REINGESTION_AUDIT_RUN-20|Run 20]] (R5) | `audit/CORPUS_REINGESTION_AUDIT_RUN-20.md` (2026-08-27) |
| Eval evidence | Multi-engine OCR eval (R6) | `agents/spikes/ocr_eval_rfc046/eval_report.md` |
| Surya service | `/ocr/image` endpoint (R10) | `services/surya-ocr-service/app.py:212` |
| Gate decision event | `surya_image_fallback → gate_not_triggered` | Decision event in worker logs |
| Docker changes | Spike profile removed + `SURYA_TIMEOUT` (R9) | `docker-compose.yml` |
| Upstream RFCs | [[047-gate-layer-correctness|RFC-047]] (R2), [[046-ocr-attribution-failure-cluster-remediation|RFC-046]] (R3) | See References table above |
