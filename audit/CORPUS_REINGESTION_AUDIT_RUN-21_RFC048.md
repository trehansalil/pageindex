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

Validate RFC-048 Surya OCR fallback for standalone images:
1. Does the Surya `/ocr/image` endpoint work end-to-end?
2. Does Doc 13 (pie chart with Arabic labels) recover from ERROR/REJECTED?
3. Are there regressions from RFC-047/048 changes?

---

## Summary Scorecard

| # | Document | Verdict (Run 21) | Verdict (D9 Baseline) | Delta |
|---|----------|-------------------|-----------------------|-------|
| 1–11 | (all PDFs) | Various | Various | No change |
| 12 | world-stats-pocketbook-2023.pdf | ERROR | ERROR | Stable |
| **13** | **image pie chart about labor distribution in january 2025 - Copy.jpg** | **REJECTED** | **REJECTED** | **No change — see Finding 1** |
| 14–25 | (all PDFs) | Various | Various | No change |

**Run 21 Tally (25 docs):** Matches RFC-047 D9 baseline exactly.

| Verdict | D9 Baseline | Run 21 | Delta |
|---------|-------------|--------|-------|
| Done (non-error) | 24 | 24 | 0 |
| Error/Rejected | 1 | 1 | 0 |

**No regressions.** All 24 PDF documents produce identical verdicts to the D9 baseline.

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

## Delta from RFC-047 D9 Baseline → Run 21

### Improvements

None — the D9 baseline already had 0 FAIL.

### Regressions

None — all 24 successful documents match baseline verdicts exactly.

### Stalls

- **Doc 13** (image pie chart) — REJECTED → REJECTED: The Surya fallback was expected to recover this document but does not fire due to the gate sensitivity gap (Finding 1). The document was already REJECTED in the D9 baseline and was called "correct gate behavior" there. RFC-048's implementation needs the gate fix before this document can be recovered.
- **world-stats-pocketbook-2023.pdf** — ERROR → ERROR: No MinIO artifacts exist; job times out before `save_doc`. Unchanged from all prior runs.

### Stable

All other 23 documents are stable at their D9 baseline verdicts.

---

## Action Items

| Priority | Item | Blocked by |
|----------|------|------------|
| **P0** | Fix gate sensitivity gap (Finding 1) — align Surya gate garble check with `validate_tree` sensitivity, OR add post-validation Surya retry | — |
| P1 | Re-run corpus after gate fix to validate Doc 13 recovery | P0 |
| P2 | Document `.env.example` `SURYA_*` variables | — |
| P3 | Add k3s manifest / CI workflow for Surya service deployment | — |

---

## Traceability

| Artifact | Reference |
|----------|-----------|
| RFC | [[048-surya-image-fallback|RFC-048]] |
| Prior baseline | [[rfc047-d9-final-baseline|RFC-047 D9 final baseline]] (0 FAIL, 25 docs, 2026-09-22) |
| Prior run | [[CORPUS_REINGESTION_AUDIT_RUN-20|Run 20]] (2026-08-27) |
| Eval evidence | `agents/spikes/ocr_eval_rfc046/eval_report.md` (Surya: 799 chars / 92.94% confidence on Doc 13) |
| Gate decision event | `surya_image_fallback → gate_not_triggered` |
| Docker changes | `docker-compose.yml` — spike profile removed, `SURYA_TIMEOUT` forwarded |
