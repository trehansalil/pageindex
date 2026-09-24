<!-- Space: CITRA -->

<!-- Title: Regression Watchdog — Run 19 -->

<!-- Folder: Audits -->

# Regression Watchdog — Run 19

## Summary

- **Audit pair**: Run 19 vs Run 18
- **Branch**: feat/pdf-inspector-shadow-pilot
- **Date**: 2026-08-10
- **Commit range**: 6484f1f..HEAD (staged uncommitted changes) (0 commits)
- **Regressions**: 3 (14 pipeline, 0 judge-shift)
- **Stalls**: 11
- **Verdict**: NEEDS_RFC

## Regression Triage

| # | Document                                                                                                                      | Change            | Domain                                             | Suspect Commit                                                                                                                                | Hypothesis                                                                                                                                                                                             | RFC Coverage                                             | Action                                                                                                                                                             |
| - | ----------------------------------------------------------------------------------------------------------------------------- | ----------------- | -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1 | uae_numbers_english_page_16_17_landscape - Copy.pdf                                                                           | MARGINAL -> FAIL  | converters/client (landscape extraction + routing) | staged (uncommitted) — RFC-035 D2: landscape rasterize-rotate-reextract + routing interaction in converters.py and client.py                 | RFC-035 D2 landscape rasterize-rotate-reextract produces scrambled OCR read-order; routing forces ok=False on landscape_fallback_picture, re-routes to flat-mixed yielding 71 bare kv-singleton blocks | RFC-035 D2 — covered_landed, fix did not hold           | Amend RFC-035 D2: suppress flat-mixed fallback when OCR quality is below threshold; consider preserving original extraction when rasterize-rotate degrades quality |
| 2 | uae_numbers_english_page_16_17_portrait - Copy.pdf                                                                            | PASS -> MARGINAL  | converters/client (landscape extraction + routing) | staged (uncommitted) — RFC-035 D2: _repair_docling_tables prev_was_separator tracking + landscape probe geometric heuristic in converters.py | D0 prev_was_separator change alters table-row collapse logic and/or D2 landscape probe width>height heuristic misfires on near-square pages, producing 89% singleton kv fragmentation                  | RFC-035 D0/D2 — covered_landed, fix did not hold        | Amend RFC-035 D0/D2: tighten landscape probe geometric threshold; audit prev_was_separator impact on chart-data documents                                          |
| 3 | اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf | MARGINAL -> ERROR | storage (write barrier)                            | staged (uncommitted) — RFC-034 D18: write barrier (_confirm_write_visible) with 4-attempt retry + backoff in storage.py                      | _confirm_write_visible adds up to 8.8s total backoff (dual call sites), pushing Arabic document job completion past the scoring window                                                                 | RFC-034 D18 — covered_landed, fix caused new regression | Amend RFC-034 D18: reduce write-barrier max backoff or make it non-blocking; ensure scoring window accounts for barrier latency                                    |

## Stall Triage

| #  | Document                                                                                                                                                                                                                     | Verdict  | Domain                                | Blocking RFC                                  | Task Status                                                                       | Action                                                                       |
| -- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | ------------------------------------- | --------------------------------------------- | --------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| 1  | FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE - Copy.pdf                                                                                                                                                          | MARGINAL | helpers (tree splitter / hierarchy)   | RFC-034 D16 (landed)                          | complete — fix did not fully resolve deep legal hierarchy                        | Needs new RFC for depth-adequate heading detection in deep legal hierarchies |
| 2  | Haftpflicht-Allgemeine-Bedingungen.pdf.pdf                                                                                                                                                                                   | MARGINAL | helpers (tree splitter / hierarchy)   | uncovered                                     | N/A                                                                               | Needs new RFC for depth-adequacy scoring proportional to document complexity |
| 3  | cabinet_resolution_no_21_of_2020_concerning_service_fees_and_administrative_fines_in_the_ministry_of_human_resources_and_emiratisation (1) - Copy.pdf                                                                        | MARGINAL | helpers (tree splitter / hierarchy)   | uncovered                                     | N/A                                                                               | Needs new RFC for Arabic government resolution heading detection             |
| 4  | image pie chart about labor distribution in january 2025 - Copy.jpg                                                                                                                                                          | MARGINAL | converters (OCR / picture enrichment) | RFC-034 D19 (landed)                          | complete — fix did not hold for chart imagery                                    | Amend RFC-034 D19 or file new RFC for Tesseract chart OCR accuracy           |
| 5  | قرار مجلس الوزراء رقم (1) لسنة 2022 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل.pdf   | MARGINAL | helpers (tree splitter / hierarchy)   | uncovered                                     | N/A                                                                               | Needs new RFC for Arabic structural marker recognition in legal documents    |
| 6  | قرار مجلس الوزراء رقم (106) لسنة 2022 بشأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (9) لسنة 2022 بشأن عمال الخدمة المساعدة.pdf | MARGINAL | helpers/converters (OCR)              | RFC-034 D21 (landed)                          | complete — garble-gate fix landed but Arabic heading detection remains uncovered | Needs new RFC for Arabic heading detection (garble-gate alone insufficient)  |
| 7  | مرسوم بقانون اتحادي رقم (13) لسنة 2022 بشان التأمين ضد التعطل عن العمل - Copy.pdf                                                                                          | MARGINAL | helpers (tree splitter / hierarchy)   | RFC-034 D20/D21 (landed)                      | complete — fixes did not resolve heading detection gap                           | Needs new RFC for short Arabic decree heading detection                      |
| 8  | وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات حكومة أبوظبي حول برنامج مهارات المهن الحرجية - Copy.pdf                    | ERROR    | helpers (garble gate / RTL)           | RFC-036 D3 (flat-fallback routing)            | N/A                                                                               | **Corrected:** Garble gate is functional — document is rejected by `rtl_reversal` in the terminal-raise list (client.py:1992) BEFORE reaching the garble gate. RFC-036 D3 adds flat-fallback routing so the garble gate can run; it correctly detects and rejects the numeric-junk text. Both paths produce garbled output; ERROR verdict is correct per Hard Rule 5.           |
| 9  | world-stats-pocketbook-2023.pdf                                                                                                                                                                                              | ERROR    | storage (persistence)                 | uncovered                                     | N/A                                                                               | Investigate upstream processing failure; file RFC if systemic                |
| 10 | GHV-TKV-Tarif.pdf                                                                                                                                                                                                            | MARGINAL | converters (picture enrichment)       | uncovered (deferred per RFC-034 C6)           | N/A                                                                               | Deferred — icon/checkmark semantic enrichment low priority                  |
| 11 | Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf                                                                                                                                                                      | MARGINAL | converters (picture enrichment)       | uncovered (deferred per RFC-035 out-of-scope) | N/A                                                                               | Deferred — same root cause as GHV-TKV-Tarif                                 |

## Live Verification (RFC-025 D4)

### اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf

| Field          | Value                                |
| -------------- | ------------------------------------ |
| doc_id         | d58be46f-8bd4-4cda-a166-809e92be66fa |
| Stored verdict | PASS (cat_b_promoted)                |
| Audit verdict  | ERROR                                |
| Prior verdict  | MARGINAL                             |
| Node count     | 218                                  |
| Depth          | 0                                    |
| Chars          | 18,411                               |

> **Divergence**: Live meta.json shows verdict=PASS (cat_b_promoted), pipeline_version=4, flat_char_count=28202, processed_at=2026-08-10T06:31:28Z. The audit scored ERROR because at score time no artifacts existed in MinIO — the job completed late (06:31 vs 06:26-06:28 cohort). The stored PASS is the pipeline gate verdict; the audit ERROR reflects score-time absence. This is a timing/race divergence, not a fabrication.

### uae_numbers_english_page_16_17_landscape - Copy.pdf

| Field          | Value                                |
| -------------- | ------------------------------------ |
| doc_id         | f7dc8381-4290-4042-8c7b-07353fc42f68 |
| Stored verdict | PASS (image_enrichment_promoted)     |
| Audit verdict  | FAIL                                 |
| Prior verdict  | MARGINAL                             |
| Node count     | 78                                   |
| Depth          | 0                                    |
| Chars          | 748                                  |

> **Divergence**: Live meta.json shows verdict=PASS (image_enrichment_promoted), pipeline_version=4, flat_char_count=748. The audit judge scored FAIL because the 78 flat blocks are 71 unusable kv singletons with scrambled OCR read-order — the enrichment promotion claim is unsupported by actual content quality. This is a gate-vs-judge divergence: the automated pipeline gate promotes to PASS on the presence of image enrichment metadata, but the LLM judge correctly identifies the content as structurally unusable.

### uae_numbers_english_page_16_17_portrait - Copy.pdf

| Field          | Value                                |
| -------------- | ------------------------------------ |
| doc_id         | bfd765eb-8bb9-4188-b5f8-b1491610d34b |
| Stored verdict | PASS (image_enrichment_promoted)     |
| Audit verdict  | MARGINAL                             |
| Prior verdict  | PASS                                 |
| Node count     | 80                                   |
| Depth          | 0                                    |
| Chars          | 764                                  |

> **Divergence**: Live meta.json shows verdict=PASS (image_enrichment_promoted), pipeline_version=4, flat_char_count=764. The audit judge downgraded to MARGINAL due to 89% singleton kv fragmentation (71/80 blocks are bare chart axis labels/values). Same gate-vs-judge divergence pattern as landscape sibling: pipeline gate promotes on enrichment metadata presence, but content is poorly structured. Previously scored PASS in Run 18 when the chart data was less fragmented.

## Recommended Next Steps

### Immediate (self-inflicted regressions from staged changes)

- [ ] **Amend RFC-035 D2** — landscape rasterize-rotate-reextract produces unusable OCR; add quality gate that falls back to original extraction when OCR read-order is worse than input. Affects: `uae_numbers_english_page_16_17_landscape - Copy.pdf`
- [ ] **Amend RFC-035 D0/D2** — prev_was_separator and/or landscape probe geometric heuristic regresses portrait chart documents; tighten threshold or exclude chart-class documents. Affects: `uae_numbers_english_page_16_17_portrait - Copy.pdf`
- [ ] **Amend RFC-034 D18** — write barrier backoff too aggressive for slow-processing documents; reduce max total delay or make non-blocking with async confirmation. Affects: `اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf`
- [ ] **Fix gate-vs-judge divergence** — `image_enrichment_promoted` and `cat_b_promoted` pipeline gate verdicts are contradicted by LLM judge scoring in 3/3 verified documents. `classify_verdict` promotion logic needs content-quality validation, not just metadata-presence checks.

### New RFC required (uncovered stalls)

- [ ] **File new RFC for Arabic heading detection** — 5 Arabic legal/government documents stall on flat/collapsed hierarchy due to missing structural marker recognition. Covers: cabinet resolution, labor law regulation, domestic workers regulation, marsoom 13, ward 597.
- [ ] **File new RFC for depth-adequate splitting** — 2 German/English documents stall on depth-2-too-shallow. Covers: Haftpflicht-Allgemeine-Bedingungen, FEDERAL LAW NO (3) OF 1987.

### Deferred (explicitly out-of-scope)

- [ ] Icon/checkmark semantic enrichment (GHV-TKV-Tarif, Unfallversicherung) — deferred per RFC-034 C6
- [ ] world-stats-pocketbook-2023.pdf processing failure — investigate root cause before filing RFC

### Scorecard

| Metric                 | Value                              |
| ---------------------- | ---------------------------------- |
| Total documents        | 25                                 |
| PASS                   | 9                                  |
| MARGINAL               | 12                                 |
| FAIL                   | 1                                  |
| ERROR                  | 3                                  |
| Net change from Run 18 | -3 (3 regressions, 0 improvements) |
