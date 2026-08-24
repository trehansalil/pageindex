<!-- Space: CITRA -->
<!-- Title: Regression Watchdog — Run 16 -->
<!-- Folder: Audits -->

# Regression Watchdog — Run 16

## Summary

- **Audit pair**: Run 16 vs Run 15
- **Branch**: feat/pdf-inspector-shadow-pilot
- **Dates**: Run 15 2026-08-06 / Run 16 2026-08-09
- **Commit range**: a52a1f9..932d634 (4 pipeline-touching commits, 791 lines changed across 8 files)
- **Run 15 tally**: 11 PASS, 12 MARGINAL, 1 FAIL, 1 ERROR
- **Run 16 tally**: 9 PASS, 11 MARGINAL, 4 FAIL, 1 ERROR
- **Regressions**: 6 (4 pipeline, 1 persistence-race, 1 judge-shift)
- **Stalls**: 7
- **Verdict**: **NEEDS_AMENDMENT**

### Key Commits Between Runs

| SHA | Message | Files Changed |
|-----|---------|--------------|
| 932d634 | feat(rfc-034): implement RFC-034 run15-reconciliation-remediation | client.py, config.py, converters.py, helpers.py, metrics.py, storage.py (399+/74-) |
| f344d6f | feat(rfc-undefined): implement RFC-undefined undefined | client.py, converters.py, helpers.py, storage.py (383+/48-) |
| daefd11 | feat(rfc-032): activate PDF inspector Tier-1 pre-classification | client.py |
| a52a1f9 | feat(rfc-032): implement RFC-032 pdf-inspector-tier1-activation | client.py |

---

## Regression Triage

| # | Document | Change | Domain | Suspect Commit | Hypothesis | RFC Coverage | Action |
|---|----------|--------|--------|----------------|------------|-------------|--------|
| R1 | FEDERAL LAW NO (3) 1987 (Penal Code) | PASS → MARGINAL | Splitter | 932d634 (RFC-034 D11: `_strip_toc_heading_nodes`) | New ToC-stripping function removed nodes that were structural, flattening depth 3→2; 493/595 nodes now top-level | **covered_landed** (D11 complete) — fix didn't hold; over-strips on long legal statutes | Amend RFC-034 D11: add depth-guard so ToC stripping preserves structural hierarchy on docs >100 nodes |
| R2 | MOU MOHRE & Nafis | PASS → MARGINAL | Converter / block-merging | 932d634 + f344d6f (converters.py changes: `_repair_docling_tables`, re-normalization) | Block count collapsed 134→20 nodes, chars dropped 13,422→12,344; 11/13 unenriched image markers; Arabic/English bilingual MOU hit by re-normalization or block-merging change | **uncovered** | New RFC decision needed: investigate block-merging regression on bilingual docs |
| R3 | cabinet_resolution_no_96 | MARGINAL → ERROR | Persistence-timing | 932d634 (storage.py changes) | 2nd consecutive persistence-timing race (different doc each time); score attempt races write; artifacts exist at publish time (108/depth 3/26,091 chars, stored PASS) | **covered_landed** (RFC-033 D3 retry logic) — fix didn't hold | Amend RFC-033 D3: current retry insufficient; add write-visibility barrier (read-after-write consistency check) before scoring |
| R4 | image pie chart (jpg) | MARGINAL → FAIL | Picture OCR / enrichment | f344d6f (converters.py: enrichment path changes) | Enrichment route now produces 1,203 chars boilerplate placeholder text instead of 489 chars real OCR digits; `image_enrichment_partial(ratio=0.50)` — enrichment fires but replaces real content | **uncovered** | New RFC decision needed: enrichment promotion path discards real OCR content for placeholder text |
| R5 | قرار مجلس الوزراء رقم (106) | MARGINAL → FAIL | Garble gate | 932d634 (helpers.py: garble/bidi changes) | 40% nodes now show Latin mojibake in Arabic text; garble gate detects 0 (total miss); stored PASS contradicts FAIL; gate changes from RFC-034 D6/D7 may have narrowed detection window | **covered_pending** (RFC-033 D2 Part B: BIDI_COHERENCE_ENFORCE promotion — task 9.1 gate not passed) | Land D2 Part B; additionally investigate whether RFC-034 D6 (Arabic line widening) introduced detection blind spot |
| R6 | مرسوم بقانون اتحادي رقم (13) | PASS → FAIL | Garble gate + depth | 932d634 (helpers.py) | 36% nodes contain Latin-letter OCR garbage; stored PASS verdict incorrect; garble gate reports 0; depth regressed 4→2. Primarily a scoring correction exposing pre-existing garble-gate blind spot, but depth regression is new | **covered_pending** (RFC-033 D2 Part B for garble; depth regression **uncovered**) | Land D2 Part B; add depth-regression investigation for this doc |

### Pipeline vs Judge Classification

| Type | Count | Documents |
|------|-------|-----------|
| Pipeline regression | 4 | Penal Code (R1), MOU (R2), image pie chart (R4), مرسوم 13 depth (R6 partial) |
| Persistence race | 1 | cabinet_resolution_no_96 (R3) |
| Judge-shift / scoring correction | 1 | قرار 106 (R5), مرسوم 13 garble (R6 partial) |

---

## Stall Triage

| # | Document | Verdict | Domain | Blocking RFC | Task Status | Action |
|---|----------|---------|--------|-------------|-------------|--------|
| S1 | Federal Decree-Law 47 | MARGINAL | Hierarchy collapse | RFC-033 D4 (article regex) | Landed | Hierarchy-collapse persists despite D4; needs splitter structural fix beyond regex |
| S2 | GHV-TKV-Tarif | MARGINAL | Table segmentation | RFC-033 D6 (table on primary path) | Not implemented (no D6 in RFC-033) | `_segment_table_nodes` still not on primary tree-build path |
| S3 | Haftpflicht-Allgemeine | MARGINAL → PASS | Judge reclassification | RFC-033 D5 (depth-2 flatness) | Pending | Judge-side upgrade only (watermark reclassification); structural depth-2 unchanged |
| S4 | Unfallversicherung | MARGINAL | Table/image enrichment | — | — | 95% image markers unenriched; table-dense benefit-comparison data lost |
| S5 | سياسة حوكمة | FAIL | Garble gate (single-letter) | RFC-033 D2 Part B | Pending (task 9.1 gate) | Stored PASS vs audit FAIL; 67% blocks garbled |
| S6 | قرار رقم (1) | MARGINAL | Hierarchy collapse | — | — | Flat depth-1→2; 114/149 nodes top-level + 19% garbled nodes new |
| S7 | وارد 597 | MARGINAL | Garble gate + content mismatch | — | — | garble_blocks=0 despite garbled text; 20% char regression; content-filename mismatch persists |

---

## Live Verification

MinIO is reachable but doc_id lookup not available in this session. Relying on audit publish-time verification (RFC-025 D4 protocol) which re-pulled all figures from live MinIO before writing.

| Document | Audit-reported | Live at publish time | Divergence |
|----------|---------------|---------------------|------------|
| Penal Code (R1) | MARGINAL (depth 2, 493/595 top-level) | 595 nodes / depth 2 / 246,652 ttc | **None** — audit matches live |
| MOU (R2) | MARGINAL (20 nodes, 12,344 chars) | 20 nodes / 12,344 body chars | **None** — audit matches live |
| cabinet_resolution_no_96 (R3) | ERROR (scoring miss) | 108 nodes / depth 3 / 26,091 chars, stored PASS | **Stored-vs-audit divergence**: stored PASS but could not be scored → persistence-timing race confirmed |

---

## RFC Coverage Summary

| Coverage Status | Count | Details |
|-----------------|-------|---------|
| **covered_landed** (fix didn't hold) | 2 | R1 (RFC-034 D11 ToC stripping), R3 (RFC-033 D3 retry logic) |
| **covered_pending** | 2 | R5, R6 garble (RFC-033 D2 Part B — task 9.1 gate blocking) |
| **uncovered** | 3 | R2 (MOU block-merging), R4 (image enrichment content loss), R6 depth regression |

---

## Recommended Next Steps

- [ ] **Amend RFC-034 D11** (`_strip_toc_heading_nodes`): Add depth-guard or node-count threshold — currently over-strips long legal statutes (Penal Code 606→595 nodes but depth 3→2). This is the highest-confidence pipeline regression (new function directly correlated to symptom).
- [ ] **Amend RFC-033 D3**: Persistence-timing race recurred on a different doc (2nd consecutive run). Current retry logic insufficient — need a write-visibility barrier (e.g., read-after-write check with backoff) before scoring proceeds.
- [ ] **New RFC decision**: MOU block-merging regression (134→20 nodes) likely caused by re-normalization safety net (RFC-034 D3) or block-merging changes in converters.py. Investigate `_repair_docling_tables` and NFKC re-normalization on bilingual Arabic/English content.
- [ ] **New RFC decision**: Image enrichment content loss (pie chart MARGINAL→FAIL). Enrichment promotion path now produces placeholder text instead of real OCR data. Investigate `_enrich_pictures` changes in converters.py commit f344d6f.
- [ ] **Unblock RFC-033 D2 Part B** (task 9.1 gate): BIDI_COHERENCE_ENFORCE promotion is the fix for R5 (قرار 106) and R6 (مرسوم 13) garble-gate blind spots, plus stall S5 (سياسة حوكمة). Gate task 9.1 (scoped re-ingest + remeasure) must run first.
- [ ] **Investigate depth regression** on مرسوم 13 (R6): depth 4→2 is separate from the garble-gate issue and not covered by any RFC.
