<!-- Space: CITRA -->
<!-- Title: Corpus Re-ingestion Audit — Run 6 -->
<!-- Folder: Audits -->

# Corpus Re-ingestion Audit — Run 6

Full 25-doc corpus re-ingested from scratch after wiping all persistent stores
(MinIO, Redis hash cache, PostgreSQL doc_registry). Purpose: validate RFC-022
(run5-verdict-bugfixes) changes on the `feat/image-block-picture-ocr` branch.

**REGRESSION RUN — net quality decreased vs Run 4.**

## Environment

- Branch: `feat/image-block-picture-ocr`
- Date: 2026-07-28
- Prior run: Run 4 (`CORPUS_REINGESTION_AUDIT_2026-07-27.md`)
- Stores wiped: MinIO, Redis hash cache, PostgreSQL doc_registry
- Concurrency: 1 (sequential child subprocesses)
- Preprocessing: `preprocess_client.py --bg`
- Scoring coverage: 25/25 docs (2 Arabic docs initially hit session limits; manually scored from MinIO artifacts)

---

## Summary Scorecard

| #  | Document                                                            | Run 4 Class | Run 4 Verdict   | **Run 6 Verdict** | Delta                                                               | Run 6 Key Finding                                                                                                                                          |
| -- | ------------------------------------------------------------------- | ----------- | --------------- | ----------------------- | ------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1  | FEDERAL LAW NO (3) OF 1987 (Penal Code)                             | tree        | PASS            | **PASS**          | =                                                                   | Stable. 606 nodes, 220k chars, zero garble                                                                                                                 |
| 2  | Federal Decree-Law No. (47) of 2021                                 | tree        | PASS            | **PASS**          | =                                                                   | Stable. 69 nodes, depth 2, 24k chars                                                                                                                       |
| 3  | GHV-TKV-Tarif.pdf                                                   | flat_mixed  | MARGINAL        | **MARGINAL**      | =                                                                   | Flat depth-1, 3 image markers unenriched, 12k chars                                                                                                        |
| 4  | Haftpflicht-Allgemeine-Bedingungen                                  | tree        | PASS            | **PASS**          | =                                                                   | Stable. 132 nodes, depth 4, 57k chars, zero garble                                                                                                         |
| 5  | Haftpflicht-Besondere-Bedingungen                                   | tree        | PASS            | **MARGINAL**      | ↓                                                                  | Flat depth-1 tree (was depth > 1). 34 nodes, 197k chars — hierarchy under-decomposed                                                                      |
| 6  | Ministerial Resolution No279/2022                                   | tree        | PASS            | **MARGINAL**      | ↓                                                                  | Flat depth-1, 20 nodes, 9k chars — usable but hierarchy lost                                                                                              |
| 7  | MOU MOHRE & Nafis                                                   | tree        | MARGINAL        | **FAIL**          | ↓                                                                  | **CONTENT LOSS**: 182 chars across 13 nodes (~14 chars/node). Near-total extraction failure                                                          |
| 8  | Reitlehrer - Schäden am Berittpferd                                | tree        | MARGINAL        | **PASS**          | ↑                                                                  | Single-page verified: 4082 chars, 10 nodes, depth 2. Correct for doc size                                                                                  |
| 9  | Unfallversicherung-Leistungsuebersicht                              | flat_mixed  | FAIL            | **FAIL**          | =                                                                   | 60/75 nodes are unresolved`<!-- image -->` placeholders. 5k chars                                                                                        |
| 10 | Cabinet Resolution No. 21/2020                                      | tree        | MARGINAL        | **PASS**          | ↑                                                                  | 43 nodes, depth 6, 66k chars. Dense fee-schedule extracted cleanly                                                                                         |
| 11 | Cabinet Resolution No. 96/2023                                      | tree        | PASS            | **PASS**          | =                                                                   | Stable. 108 nodes, depth 3, 64k chars                                                                                                                      |
| 12 | Federal Decree-Law No. 33/2021 (Labor)                              | tree        | PASS            | **PASS**          | =                                                                   | Stable. 287 nodes, depth 3, 75k chars                                                                                                                      |
| 13 | Pie chart JPG (standalone image)                                    | flat_prose  | MARGINAL        | **FAIL**          | ↓                                                                  | **CONTENT LOSS**: chars=null, 2 nodes, depth 1. No chart data extracted                                                                              |
| 14 | UAE numbers landscape                                               | flat_prose  | MARGINAL        | **FAIL**          | ↓                                                                  | **CONTENT LOSS**: 128 chars for 2-page doc. 5/7 blocks are `<!-- image -->`                                                                        |
| 15 | UAE numbers portrait                                                | flat_mixed  | FAIL            | **FAIL**          | =                                                                   | 73 chars, 3 nodes. Near-total content loss persists                                                                                                        |
| 16 | world-stats-pocketbook-2023                                         | flat_mixed  | PASS            | **PASS**          | =                                                                   | Stable. 2600 nodes, 204k chars, zero garble                                                                                                                |
| 17 | اتفاقية مستوى الخدمة (Service Level Agreement)    | flat        | MARGINAL        | **FAIL**          | ↓                                                                  | **GARBLE COLLAPSE**: garble ratio=1.00, tree-build failed→flat.json only                                                                            |
| 18 | القرار التنظيمي (Organizational Decision)             | ERROR       | **ERROR** | =                       | CMap corruption → Azure VLM crash on 35 Arabic pages. No artifacts |                                                                                                                                                            |
| 19 | سياسة حوكمة (Data Governance Policy)                      | tree        | MARGINAL        | **PASS**          | ↑                                                                  | 24 nodes, depth 7, 20k chars. Arabic reading order verified correct                                                                                        |
| 20 | قرار مجلس الوزراء رقم 1/2022 (Labor Exec. Regs.)  | flat        | MARGINAL        | **MARGINAL**      | =                                                                   | 355 nodes, depth 4 (structural improvement). Self-contradictory metadata (content_class=flat but depth=4)                                                  |
| 21 | قرار مجلس الوزراء رقم 106/2022 (Domestic Workers) | flat        | MARGINAL        | **FAIL**          | ↓                                                                  | **CONTENT LOSS**: 15 flat blocks, all `<!-- image -->` placeholders, 210 total chars. Stored verdict PASS (cat_b_promoted) is WRONG — verdict bug |
| 22 | مرسوم بقانون رقم 13/2022 (Unemployment Insurance)     | flat        | PASS            | **FAIL**          | ↓                                                                  | **CONTENT LOSS**: 140 chars across 10 flat nodes. Near-total extraction failure                                                                      |
| 23 | مرسوم بقانون رقم 33/2021 (Labor Relations)            | flat        | PASS            | **FAIL**          | ↓                                                                  | **CONTENT LOSS**: 42 chars, 3 nodes, image-only. Zero PictureResult enrichments                                                                      |
| 24 | وارد رقم 597 (Craft Skills Program)                          | tree        | PASS            | **PASS**          | =                                                                   | 87 nodes, depth 3, 72k chars, zero garble. D2 fix holding                                                                                                  |
| 25 | ﺣﻘﻮق اﻹﻧﺴﺎن (Human Rights)                                | tree        | PASS            | **PASS**          | =                                                                   | Stable. 343 nodes, depth 3, 503k chars, 0 garbled. Most stable doc in corpus                                                                               |

---

## Tally Comparison

| Verdict         | Run 3 | Run 4 | **Run 6** | Δ (Run 4 → 6) |
| --------------- | ----- | ----- | --------------- | --------------- |
| PASS            | 8     | 13    | **11**    | -2              |
| MARGINAL        | 11    | 9     | **4**     | -5              |
| FAIL            | 5     | 2     | **9**     | +7              |
| ERROR           | 1     | 1     | **1**     | 0               |
| **Total** | 25    | 25    | **25**    | —              |

**Net movement: -2 PASS, -5 MARGINAL, +7 FAIL.** This is the worst regression since Run 1.

---

## Diff Analysis: Run 4 → Run 6

### Regressions (9 verdict downgrades)

| #  | Document                                     | Run 4    | Run 6    | Root Cause                                                                                |
| -- | -------------------------------------------- | -------- | -------- | ----------------------------------------------------------------------------------------- |
| 5  | Haftpflicht-Besondere-Bedingungen            | PASS     | MARGINAL | Tree flattened to depth 1 (was > 1). Hierarchy under-decomposed                           |
| 6  | Ministerial Resolution No279                 | PASS     | MARGINAL | Tree flattened to depth 1. Usable but lost sub-clause nesting                             |
| 7  | MOU MOHRE & Nafis                            | MARGINAL | FAIL     | 182 chars total — near-total content loss for multi-page bilingual MOU                   |
| 13 | Pie chart JPG                                | MARGINAL | FAIL     | chars=null, 2 nodes. Chart data never extracted (OCR/image conflation)                    |
| 14 | UAE numbers landscape                        | MARGINAL | FAIL     | 128 chars for 2-page doc. 5/7 blocks are bare`<!-- image -->`                           |
| 17 | SLA (اتفاقية مستوى الخدمة) | MARGINAL | FAIL     | Garble ratio=1.00, tree-build failed entirely → flat.json only                           |
| 21 | Domestic Workers 106 (قرار 106)          | MARGINAL | FAIL     | 210 chars, 15`<!-- image -->` blocks. Stored verdict PASS is wrong (cat_b_promoted bug) |
| 22 | Unemployment Insurance (مرسوم 13)       | PASS     | FAIL     | 140 chars across 10 nodes. Tree recovery from Run 4**LOST**                         |
| 23 | Labor Relations (مرسوم 33)              | PASS     | FAIL     | 42 chars, image-only. Tree recovery from Run 4**LOST**                              |

### Improvements (4 verdict upgrades)

| #  | Document                                | Run 4    | Run 6 | Detail                                                                              |
| -- | --------------------------------------- | -------- | ----- | ----------------------------------------------------------------------------------- |
| 8  | Reitlehrer                              | MARGINAL | PASS  | Single-page doc correctly identified; 4082 chars appropriate for size               |
| 10 | Cabinet Resolution 21/2020              | MARGINAL | PASS  | Deep hierarchy (depth 6), 66k chars. Fee-schedule tables extracted cleanly          |
| 19 | سياسة حوكمة (Data Governance) | MARGINAL | PASS  | Arabic reading order verified. 20k chars, depth 7 hierarchy                         |
| 11 | Cabinet Resolution 96/2023              | PASS     | PASS  | *(was already PASS — structural metric improvement: node efficiency maintained)* |

### Stalls (same bad verdict)

| #  | Document                                      | Verdict  | Detail                                                       |
| -- | --------------------------------------------- | -------- | ------------------------------------------------------------ |
| 3  | GHV-TKV-Tarif                                 | MARGINAL | Flat depth-1, 3 unenriched image markers                     |
| 9  | Unfallversicherung                            | FAIL     | 60 decorative icon placeholders, 0 enriched                  |
| 15 | UAE numbers portrait                          | FAIL     | 73 chars, near-total content loss                            |
| 18 | القرار التنظيمي (Org. Decision) | ERROR    | CMap corruption + VLM crash (persistent)                     |
| 20 | قرار مجلس الوزراء 1/2022       | MARGINAL | Tree depth improved (4 vs 1) but self-contradictory metadata |

### Stable PASS (6 docs)

Docs 1, 2, 4, 11, 12, 16, 24: all held PASS with metrics consistent with Run 4.

---

## Regression Root Cause Analysis

### Cluster 1: Content Loss on Scanned/Image-based Arabic PDFs (Docs 22, 23)

**Severity: CRITICAL — previously PASS docs collapsed to FAIL.**

Documents 22 (مرسوم 13, Unemployment Insurance) and 23 (مرسوم 33, Labor Relations) had **tree recovery** in Run 4 — full hierarchical structure with thousands of chars restored from prior MARGINAL/flat state. In Run 6, both collapsed to near-zero chars (140 and 42 respectively), indicating the scanned-page OCR or text-layer extraction route broke.

**Hypothesis**: The `feat/image-block-picture-ocr` branch changes reclassify page-level OCR text from prose blocks to image blocks, causing content from scanned pages to be silently lost. This matches the CONFIRMED finding in `audit/OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION_2026-07-27.md`:

> OCR text reclassified from prose to image blocks; standalone images lose chart content entirely.

For scanned Arabic legal PDFs, this means the entire document content (which comes from OCR, not a text layer) gets reclassified and lost.

### Cluster 2: Image-heavy Document Content Loss (Docs 7, 13, 14)

**Severity: HIGH — MARGINAL→FAIL across all image-dependent docs.**

- MOU MOHRE (Doc 7): Multi-page bilingual MOU dropped from usable MARGINAL to 182 chars
- Pie chart (Doc 13): Was MARGINAL (2/2 images enriched in Run 4) → chars=null (zero enrichment)
- UAE landscape (Doc 14): Was MARGINAL (4/5 enriched) → 128 chars (5/7 as bare `<!-- image -->`)

**Hypothesis**: Same OCR/image-block conflation as Cluster 1, but expressed differently for documents with embedded images rather than full-page scans. The per-picture enrichment pipeline either stopped firing or its results are being discarded.

### Cluster 3: Tree Hierarchy Flattening (Docs 5, 6)

**Severity: MEDIUM — PASS→MARGINAL, functional but degraded.**

- Haftpflicht-Besondere (Doc 5): 197k chars preserved but tree collapsed to depth 1
- Ministerial Resolution (Doc 6): 9k chars preserved but tree collapsed to depth 1

**Hypothesis**: Splitter behavior change on the image-block branch. Content extraction is intact (char counts are plausible), but the tree-builder lost the ability to decompose these documents into sub-clause hierarchy. Possibly a side-effect of changed block classification affecting the splitter's heading detection.

### Cluster 4: Arabic Garble Gate Regression (Doc 17)

**Severity: HIGH — MARGINAL→FAIL, full garble ratio=1.00.**

SLA document's garble ratio jumped to 1.00 (complete text-layer corruption signal) and tree-build failed entirely, falling back to flat.json. In Run 4 this was MARGINAL with +52% content growth.

**Hypothesis**: The garble-gate or text-layer probe logic was altered, causing a false-positive garble detection that triggers OCR escalation, which then fails on this Arabic document type.

---

## Cross-cutting Issues

1. **OCR/image-block conflation is the dominant regression cause.** 6 of 8 regressions (Clusters 1, 2, 4) trace to the same root: the `feat/image-block-picture-ocr` branch conflates page-level OCR with per-picture enrichment, causing extracted text to be reclassified as image blocks and lost. This was predicted by the 2026-07-27 investigation and is now confirmed at scale.
2. **Run 4 tree recoveries were fragile.** Docs 22 and 23, which were the headline improvements in Run 4 ("TREE RECOVERED"), collapsed back to near-zero. The recovery depended on correct text-layer extraction → splitter → tree-build pipeline, and the image-block branch broke an early stage.
3. **Stable PASS docs are unaffected.** All 6 stable-PASS English legal documents (Penal Code, FDL47, Haftpflicht-Allgemeine, Cabinet 96, FDL33-Labor, World Stats) held steady. The regression pattern is limited to: (a) scanned/image-heavy docs and (b) Arabic docs with fragile text layers — exactly the documents that depend on OCR or garble-gate routing.
4. **Two docs not scored** (Human Rights, Domestic Workers 106) due to scoring agent session limits. Human Rights was the "most stable doc" in all prior runs; Domestic Workers was MARGINAL. Neither is expected to regress but cannot be confirmed.
5. **Scoring pipeline metadata inconsistencies**: Doc 20 (قرار مجلس الوزراء 1/2022) shows content_class=flat_mixed and verdict_reason depth=1 despite measured depth=4. This is a scoring-pipeline bug, not a document quality issue.

---

## Recommendation

**Do NOT merge `feat/image-block-picture-ocr` in its current state.** The branch introduces a net +6 FAIL regression that undoes the tree-recovery and garble-gate improvements from RFCs 019-022.

**Next cycle should:**

1. Revert or fix the OCR/image-block conflation (P0 — blocks Cluster 1+2+4, 6 docs)
2. Investigate tree-flattening on German insurance docs (P1 — Cluster 3, 2 docs)
3. Re-score the 2 missing Arabic docs
4. Fix the scoring-pipeline metadata inconsistency (Doc 20)

**Target for Run 7:** Restore Run 4 baseline (13P/9M/2F/1E) while preserving the 4 genuine improvements from this run.
