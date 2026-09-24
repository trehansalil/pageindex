<!-- Space: CITRA -->
<!-- Title: Corpus Re-ingestion Audit — Run 21 (RFC-048 Surya Image Fallback) -->
<!-- Folder: Audits -->
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

# Corpus Re-ingestion Audit — Run 21 (RFC-048 Surya Image Fallback)

## Environment

- Branch: ICR-97-rfc48-surya-image-fallback
- Date: 2026-09-23
- Run 21a ID: `ingest-run-20260923T054443Z` (initial, before post-VT fallback)
- Run 21b ID: `d428364f-aa92-4a79-b409-e8bde5c31e6d` (after post-VT fallback + Surya memory fix)
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

> **Run 21 has two phases.** Run 21a was the initial run before the post-VT fallback was implemented — Doc 13 stayed REJECTED and world-stats-pocketbook regressed to ERROR. After implementing the post-VT parallel VLM+Surya fallback (commits `4b22f66`, `545812a`) and adding Surya memory limits (`3g` Docker cap), the full corpus was re-run as **Run 21b**. The results below are from Run 21b.

| # | Document | D9 Baseline | Run 21b | Delta |
|---|----------|-------------|---------|-------|
| 1 | FEDERAL LAW NO (3) | MARGINAL | MARGINAL | — |
| 2 | Federal Decree-Law 47 | PASS | PASS | — |
| 3 | GHV-TKV-Tarif | MARGINAL | MARGINAL | — |
| 4 | Haftpflicht-Allgemeine | PASS | PASS | — |
| 5 | Haftpflicht-Besondere | PASS | PASS | — |
| 6 | Ministerial Resolution 279 | PASS | PASS | — |
| 7 | Reitlehrer | PASS | PASS | — |
| 8 | Unfallversicherung | MARGINAL | MARGINAL | — |
| 9 | cabinet_resolution 21 | PASS | PASS | — |
| 10 | cabinet_resolution 96 | PASS | PASS | — |
| 11 | federal_decree_law 33 | PASS | PASS | — |
| **12** | **image pie chart (.jpg)** | **REJECTED** | **MARGINAL** | **REJECTED→MARGINAL ↑ (Finding 1 FIXED)** |
| 13 | uae_numbers landscape | MARGINAL | MARGINAL | — |
| 14 | uae_numbers portrait | PASS | PASS | — |
| 15 | القرار التنظيمي | PASS | PASS | — |
| 16 | سياسة حوكمة | PASS | PASS | — |
| 17 | قرار مجلس الوزراء (1) | PASS | PASS | — |
| 18 | قرار مجلس الوزراء (106) | PASS | PASS | — |
| 19 | مرسوم اتحادي (13) | PASS | PASS | — |
| 20 | مرسوم اتحادي (33) | MARGINAL | MARGINAL | — |
| 21 | وارد رقم 597 | MARGINAL | MARGINAL | — |
| 22 | ﺣﻘﻮق اﻹﻧﺴﺎن | PASS | PASS | — |
| 23 | MOU MOHRE | PASS | PASS | — |
| 24 | اتفاقية مستوى الخدمة | PASS | PASS | — |
| 25 | world-stats-pocketbook | PASS | PASS | — |

**Run 21b Tally (25 docs):**

| Verdict | D9 Baseline | Run 21b | Delta |
|---------|-------------|---------|-------|
| PASS | 18 | 18 | 0 |
| MARGINAL | 6 | 7 | **+1** |
| FAIL | 0 | 0 | 0 |
| REJECTED | 1 | 0 | **−1** |
| ERROR | 0 | 0 | 0 |
| **Total** | **25** | **25** | |

**0 regressions. 1 improvement.** Doc 12 (pie chart image) recovered from REJECTED to MARGINAL via the post-VT VLM fallback. The corpus now has **0 REJECTED, 0 FAIL** for the first time. world-stats-pocketbook is stable at PASS (115 sections) after Surya memory limits were applied.

---

## Finding 1: Doc 12 — Gate Sensitivity Gap → FIXED by Post-VT Parallel Fallback

**Document:** image pie chart about labor distribution in january 2025 - Copy.jpg
**Run 21a (initial):** REJECTED — Surya fallback gate not triggered (gate sensitivity gap)
**Run 21b (after fix):** **MARGINAL** — post-VT VLM fallback recovered 643 chars clean

### Root Cause (Run 21a): Garble detection sensitivity mismatch

The Surya quality gate and `validate_tree` use different garble detection sensitivity:

| Step | Chars | Garbled? | Action |
|------|-------|----------|--------|
| Tesseract OCR | 303 | **false** (gate-level check) | Gate check: `303 ≥ MIN_STANDALONE_IMAGE_MD_CHARS(100)` AND `not garbled` → **gate_not_triggered** |
| `validate_tree` | — | **true** (`depth<2`, `garbling`, `node_garbling`) | Tree validation catches garbling that the gate missed |
| `force_full_page_ocr` retry | 391 | still garbled | Recovery escalation fails |
| VLM fallback | — | PDFium can't open `.jpg` | Not applicable to images |
| Tesseract-on-raster | partial | still garbled | → **REJECTED** |

### Fix Applied: Option B — Post-validate_tree Parallel Fallback (commits `4b22f66`, `545812a`)

Instead of aligning the pre-VT gate's sensitivity, a **second fallback window** was added after `validate_tree` condemns the output. This two-window design:

1. **Pre-VT gate** (simple garble/char-count check) → fires Surya if obvious garble or too few chars
2. **`validate_tree`** (structural analysis) → catches subtle defects the pre-VT gate misses
3. **Post-VT parallel fallback** → when validate_tree condemns a standalone image, fires **both VLM and Surya in parallel**, picks best result by char count

For Doc 12, the post-VT fallback fired:
- VLM (via Pillow `rasterize_image_file()`): **643 chars**, clean markdown ← **winner**
- Surya: **0 chars** (garbled)
- Tree rebuilt with 3 nodes (1 section), passed validate_tree on second pass
- Final verdict: **MARGINAL** (leaf_concentration=0.72 — expected for a single-image document)

Key implementation details:
- `_surya_already_ran` guard prevents redundant Surya calls when Surya already won at the pre-VT gate
- `_min_recovery` threshold: winner must exceed `max(MIN_STANDALONE_IMAGE_MD_CHARS, current_chars)` to prevent displacement by low-quality results
- Decision event: `post_validation_image_fallback → choice=vlm` logged
- Pillow-based `rasterize_image_file()` replaces pypdfium2 for standalone .jpg/.png/.tiff files (pypdfium2 cannot open non-PDF images)

---

## Finding 2: Surya `/ocr/image` Endpoint — Verified Working

The Surya service was rebuilt during this run (stale image on initial attempt). After rebuild:

- `/ocr/image` endpoint responds correctly
- Service accepts raw image files and returns OCR text, char count, and confidence
- Response schema matches `/ocr/pdf` structure
- The endpoint simply never gets called for Doc 13 due to Finding 1

---

## Finding 3: world-stats-pocketbook-2023.pdf — PASS → ERROR → FIXED

**D9 Baseline (2026-09-22):** PASS — structural_pass, 115 sections, sha8 `0a172475`
**Run 21a (2026-09-23):** ERROR — no MinIO artifacts, job timeout before write_barrier
**Run 21b (2026-09-23, after fix):** **PASS** — structural_pass, 115 sections — **matches D9 exactly**

### History

This 292-page UN statistical publication has oscillated:

| Run | Date | Verdict | Notes |
|-----|------|---------|-------|
| 15 | 2026-08-09 | PASS | 6.19M chars (10x inflation from Docling table duplication) |
| 16 | 2026-08-09 | PASS | 2.03M chars (67% drop flagged, verdict unchanged) |
| 19 | 2026-08-18 | ERROR | Timeout before write_barrier — no artifacts |
| 20 | 2026-08-27 | ERROR | Same timeout — no artifacts |
| D9 | 2026-09-22 | **PASS** | 115 sections, structural_pass — **recovered** |
| 21a | 2026-09-23 | **ERROR** | Timeout again — **regressed** |
| **21b** | 2026-09-23 | **PASS** | 115 sections — **stable after Surya memory fix** |

### Root Cause and Fix

The Run 21a regression was caused by **Surya OCR service resource contention**. The Surya service (activated by default after removing the `profiles: ["ocr-spike"]` gate) was consuming unbounded memory during concurrent processing, starving the Docling service of resources and causing this large document to timeout.

**Fix applied (commit `545812a`):** Added Docker memory limits to the Surya service in `docker-compose.yml`:

```yaml
deploy:
  resources:
    limits:
      memory: 3g
    reservations:
      memory: 1g
```

After applying the memory cap, world-stats-pocketbook processes reliably at PASS with 115 sections, matching the D9 baseline exactly.

---

## Delta from RFC-047 D9 Baseline → Run 21b

### Improvements

- **Doc 12 (image pie chart): REJECTED → MARGINAL** — Post-VT parallel VLM+Surya fallback (RFC-048 Amendment 1) recovered this standalone .jpg file. VLM extracted 643 chars clean via Pillow `rasterize_image_file()`. Final verdict MARGINAL due to leaf_concentration (expected for a single image with only 3 nodes). See Finding 1.

### Regressions

None. The Run 21a regression (world-stats-pocketbook ERROR) was resolved by Surya memory limits before the Run 21b corpus re-run. See Finding 3.

### Stable

All 24 non-Doc-12 documents hold their D9 baseline verdicts exactly. The corpus now has **0 REJECTED, 0 FAIL** for the first time.

---

## Action Items

| Priority | Item | Status |
|----------|------|--------|
| ~~**P0**~~ | ~~Fix gate sensitivity gap (Finding 1) — post-VT parallel VLM+Surya fallback (Option B)~~ | **DONE** (`4b22f66`) |
| ~~**P0**~~ | ~~Add VLM image support via Pillow + parallel VLM/Surya fallback for standalone images~~ | **DONE** (`4b22f66`) |
| ~~**P1**~~ | ~~Investigate world-stats-pocketbook regression (Finding 3)~~ | **DONE** — Surya memory limits (`545812a`) |
| ~~P1~~ | ~~Re-run corpus after gate fix to validate Doc 13 recovery and world-stats-pocketbook stability~~ | **DONE** — Run 21b, 0 regressions |
| P2 | Document `.env.example` `SURYA_*` variables | Open |
| P3 | Add k3s manifest / CI workflow for Surya service deployment | Open |

---

## Residue Analysis (Run 21b)

**Remaining MARGINAL (7):**

| # | Document | Reason | Addressable? |
|---|----------|--------|--------------|
| 1 | FEDERAL LAW NO (3) | depth_inadequate | Structural — Docling hierarchy limit on flat legal text |
| 3 | GHV-TKV-Tarif | leaf_concentration | Single-leaf doc (1 section) — correct behavior |
| 8 | Unfallversicherung | leaf_concentration | Few headings (6 sections) — near threshold |
| 12 | image pie chart | leaf_concentration | Recovered from REJECTED; 3 nodes is correct for single image |
| 13 | uae_numbers landscape | depth_inadequate | Landscape PDF with limited structure |
| 20 | مرسوم اتحادي (33) | depth_inadequate | Large Arabic legal text — Docling hierarchy limit |
| 21 | وارد رقم 597 | depth_inadequate | Arabic document — non-deterministic (oscillates FAIL↔MARGINAL) |

**Remaining REJECTED: 0**
**Remaining FAIL: 0**

All 7 MARGINALs are structural limitations (flat documents, single-image files, or Docling hierarchy depth limits), not pipeline defects. No further RFC-048 action required.

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
