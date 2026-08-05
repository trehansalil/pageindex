<!-- Space: CITRA -->
<!-- Title: Corpus Re-ingestion Audit — Run 9 -->
<!-- Folder: Audits -->

# Corpus Re-ingestion Audit — Run 9

## Environment

- Branch: feat(run-7)-diagnosis_n_implementation
- Date: 2026-07-31
- Prior run: /Users/saliltrehan/Documents/Python_n_R/Personal/pageindex/audit/CORPUS_REINGESTION_AUDIT_RUN-8.md
- Methodology: Incremental ingest+score pipeline (each doc scored immediately after processing)

> **RFC-025 D4 pre-publish verification — MANDATORY CORRECTION APPLIED.**
> The raw Run 9 harness output supplied to this audit reported **all 24 documents as `ERROR` with null node_count/chars**. Live re-pull of `processed/{doc_id}.meta.json` from MinIO (2026-07-31, all `processed_at` timestamps 08:26–08:33 UTC today) **refutes that data**: every doc_id in the batch except one has a persisted meta with a real verdict. The harness `ERROR` defaults were a scoring-stage artifact — the per-document key_findings were raw ingestion transcripts (`status: success`, doc_id assigned), not tree-quality judgments, and the harness defaulted every unscored record to `ERROR`. Per the D4 rule, **every figure below is re-derived from the live MinIO store**, not from the harness payload. The single genuine ERROR is `world-stats-pocketbook-2023.pdf`, which has no meta/tree object in MinIO at all (run never completed).

---

## Summary Scorecard

| # | Document | Doc Class | Verdict | Key Finding |
|---|---|---|---|---|
| 1 | FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE  - Copy.pdf | legal_document | PASS | Live MinIO: 606 nodes / 220,268 chars / depth-2, `max_leaf_ratio` 0.0083, empty verdict_reason. Matches Run 8 ground truth exactly — RFC-005 splitter output preserved. Harness `ERROR` was a scoring-stage default, not a real failure. |
| 2 | Federal Decree-Law No. (47) of 2021 - Copy.pdf | legal_labor | PASS | Live MinIO: 69 nodes / 13,376 chars / depth-1, verdict PASS. Note chars dropped 24.3k -> 13.4k vs Run 8 while node count held at 69; verdict improved MARGINAL -> PASS, but the ~45% char reduction warrants a spot-check of section coverage. |
| 3 | GHV-TKV-Tarif.pdf | flat_mixed | MARGINAL | Live MinIO: 5 nodes / 13,022 chars / depth-1, verdict_reason `leaf_concentration=0.39` (`max_leaf_ratio` 0.3853). Same 5-node/13k tree that PASSed Run 8 now trips the leaf-concentration gate — verdict-threshold change (RFC-025 hysteresis), not a content change. |
| 4 | Haftpflicht-Allgemeine-Bedingungen.pdf.pdf | insurance_tnc | PASS | Live MinIO: 132 nodes / 56,610 chars / depth-1, verdict PASS with empty reason. Identical node/char shape to Run 8's FAIL (81/132 garbled) — either the garble fix landed and cleaned detection, or the garble check no longer feeds the verdict. Requires garble-ratio spot-check before trusting this PASS. |
| 5 | Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf | insurance_tnc | PASS | Live MinIO: 34 nodes / 138,556 chars / depth-1, verdict PASS. Same tree as Run 8's MARGINAL (depth-2 under-segmentation); verdict promoted without structural change — consistent with RFC-025 threshold/hysteresis update. |
| 6 | MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf | flat_prose | MARGINAL | Live MinIO: flat.json with **0 blocks / 0 chars**, verdict_reason `node_count=0`. Content is still totally lost (known image-OCR-never-fires defect), but the verdict is now MARGINAL instead of Run 8's FAIL — a zero-content doc surfacing as MARGINAL is a verdict-gate softening that hides real content loss. |
| 7 | Ministerial Resolution No279 of 2022 Monitoring Mechanisms of Emiratisation Rates in the Private Sec - Copy.pdf | legal/regulatory | PASS | Live MinIO: 28 nodes / 10,207 chars / depth-1, verdict PASS (re-pulled twice to confirm). Recovered from Run 8's MARGINAL depth-collapse finding; node count back to Run 7 level (28). |
| 8 | Reitlehrer - Schäden am Berittpferd.pdf | insurance/legal | PASS | Live MinIO: 10 nodes / 4,082 chars / depth-1, `max_leaf_ratio` 0.2571 — byte-identical profile to the Run 8 ground-truth-verified PASS. Stable. |
| 9 | Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf | flat_mixed | PASS | Live MinIO: flat.json, 21 blocks but only **492 chars** (Run 8: 7,471 chars), verdict PASS via `image_enrichment_promoted`. Verdict label held but text content dropped ~15x — the promotion path is masking a content regression. Investigate. |
| 10 | cabinet_resolution_no_21_of_2020_concerning_service_fees_and_administrative_fines_in_the_ministry_of_human_resources_and_emiratisation (1) - Copy.pdf | legal/regulatory | PASS | Live MinIO: 43 nodes / 53,334 chars / depth-2, verdict PASS with empty reason. Same shape as Run 8's FAIL (19/43 Latin-gibberish garbled). Either the expected_script garble fix landed, or garbling no longer gates the verdict — spot-check required. |
| 11 | cabinet_resolution_no_96_of_2023_regarding_an_alternative_end_of_service_benefits_system - Copy.pdf | legal/administrative | PASS | Live MinIO: 108 nodes / 29,110 chars / depth-2 — identical to Run 8 PASS. Stable. |
| 12 | federal_decree_law_no_33_of_2021_regarding_the_regulation_of_employment_relationship_and_its_amendments - Copy.pdf | legal | PASS | Live MinIO: 502 nodes / 110,938 chars / depth-3 — identical to Run 8 PASS. Stable. |
| 13 | image pie chart about labor distribution in january 2025 - Copy.jpg | flat_mixed | PASS | Live MinIO: flat.json, 12 blocks / 978 chars, verdict PASS via `image_enrichment_promoted` (Run 8: MARGINAL, 1,072 chars). Char count roughly held; chart-wedge-semantics confirmation still outstanding (known gap), so the PASS rests on the promotion flag, not verified chart content. |
| 14 | uae_numbers_english_page_16_17_landscape - Copy.pdf | flat_mixed | MARGINAL | Live MinIO: flat.json, 76 blocks / **748 chars** (unchanged from Run 8 FAIL), verdict_reason `depth=1`. Severe content loss (10x+ below 2-page baseline) persists for the third straight run; only the verdict label moved (FAIL -> MARGINAL), the underlying numeric-table fragmentation is unfixed. |
| 15 | uae_numbers_english_page_16_17_portrait - Copy.pdf | flat_mixed | MARGINAL | Live MinIO: flat.json, 76 blocks / **764 chars** (unchanged from Run 8 FAIL), verdict_reason `depth=1`. Same persistent content-loss pathology as the landscape variant; verdict softening only. |
| 16 | world-stats-pocketbook-2023.pdf | unknown | ERROR | **Genuine ERROR — the only one in the batch.** No `processed/*.meta.json`, tree, or flat object exists in MinIO for this document; harness transcript shows the run left mid-flight ("Status: In Progress", monitor task watching). The 6.1MB file never completed within the run window. |
| 17 | اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf | flat_prose | MARGINAL | Live MinIO: flat.json with **0 blocks / 0 chars**, verdict_reason `node_count=0` (4,906MB ingestion peak). Total content loss persists (image-OCR-never-fires defect, per ocr-image-block-conflation memory); FAIL -> MARGINAL is verdict softening on a zero-content doc, not recovery. |
| 18 | القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf | flat_prose | PASS | **New in Run 9** (no Run 8 baseline). Live MinIO: flat.json, only **2 blocks / 123 chars**, yet verdict PASS via `image_enrichment_promoted` (`max_leaf_ratio` 0.5348). A 123-char PASS on a multi-page regulatory decision is implausible — the promotion path is bypassing the content-volume gate. |
| 19 | سياسة حوكمة و إدارة البيانات - Copy.pdf | flat_prose | PASS | Live MinIO: 24 nodes / 20,330 chars / depth-3 — identical to Run 8 PASS. Stable. |
| 20 | قرار مجلس الوزراء رقم (1) لسنة 2022 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل.pdf | flat_prose | MARGINAL | Live MinIO: flat.json with **0 blocks / 0 chars**, verdict_reason `node_count=0`. The Run 8 hard ERROR (parse crash) no longer occurs — ingestion completes and persists an artifact — but zero content was extracted, so this is a crash-to-empty conversion, not a recovery. |
| 21 | قرار مجلس الوزراء رقم (106) لسنة 2022 بشأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (9) لسنة 2022 بشأن أعمال الخدمة المساعدة.pdf | flat_prose | MARGINAL | Live MinIO: flat.json with **0 blocks / 0 chars**, verdict_reason `node_count=0`. Same pattern as #20: Run 8 ERROR crash converted to a persisted-but-empty artifact. |
| 22 | مرسوم بقانون اتحادي رقم (13) لسنة 2022 بشان التأمين ضد التعطل عن العمل - Copy.pdf | flat_prose | PASS | Live MinIO: flat.json, **2 blocks / 38 chars**, yet verdict PASS via `image_enrichment_promoted` (`max_leaf_ratio` 0.6333). Run 8 scored 60 chars as FAIL; Run 9 has *less* content and a PASS. This is the clearest evidence the image-enrichment promotion path bypasses the quality gate — violates Hard Rule 5 in spirit. |
| 23 | مرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل وتعديلاته.pdf | flat_mixed | PASS | Live MinIO (doc_id `2a819bc4-8f90-4d58-a2e5-6a76b47c18fa`, processed 08:33 UTC): flat.json with **884 blocks / 95,351 chars**, verdict PASS via `cat_b_promoted`. Genuine recovery from Run 8's 0-node/0-char ERROR — the harness transcript ("now monitoring") simply predated completion. Content is back near Run 7 levels (172k tree -> 95k flat). |
| 24 | وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات حكومة أبوظبي حول برنامج مهارات المهن الحرفية - Copy.pdf | flat_mixed | MARGINAL | Live MinIO: flat.json, 592 blocks / 62,836 chars, verdict_reason `garbling(ratio=1.00)`. The garble gate now correctly flags the numeric-junk text layer (the Run 8 "garble-gate hole" is at least detected), but the doc still surfaces as MARGINAL with fully-garbled content rather than escalating to OCR. |

**Run 9 Tally (24/25 audited):** 15 PASS, 8 MARGINAL, 0 FAIL, 1 ERROR
*(Verdicts re-derived from live MinIO `processed/{doc_id}.meta.json` per RFC-025 D4; the harness-supplied 0/0/0/24 tally is rejected as a scoring-stage artifact. `حقوق الإنسان - Copy.pdf` — Run 8 FAIL — was absent from the Run 9 batch and not re-tested.)*

---

## Delta from Prior Run -> Run 9

### Improvements

- **Federal Decree-Law No. (47) of 2021 - Copy.pdf** (MARGINAL -> PASS): Verdict recovered; 69 nodes held. Caveat: chars dropped 24.3k -> 13.4k, so the promotion coincides with a char reduction — verify coverage before crediting the fix.
- **Haftpflicht-Allgemeine-Bedingungen.pdf.pdf** (FAIL -> PASS): 132 nodes / 56.6k chars unchanged; Run 8's 61%-garbled finding no longer gates the verdict. Either the expected_script garble fix landed or garbling was decoupled from the verdict — spot-check garble ratio to confirm which.
- **Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf** (MARGINAL -> PASS): Same 34-node/138.6k tree; depth-2 no longer penalized (RFC-025 threshold/hysteresis change).
- **Ministerial Resolution No279 of 2022** (MARGINAL -> PASS): 20 -> 28 nodes, 9.1k -> 10.2k chars; Run 8 depth-collapse recovered.
- **cabinet_resolution_no_21_of_2020** (FAIL -> PASS): 43 nodes / 53.3k chars unchanged; Run 8's 44% Latin-gibberish CMap-garble finding no longer flagged. Same caveat as Haftpflicht-Allgemeine — confirm the garble fix rather than the gate.
- **image pie chart about labor distribution in january 2025 - Copy.jpg** (MARGINAL -> PASS): 978 chars via `image_enrichment_promoted`; chart-wedge semantics still unverified (known gap), so this is a soft improvement.
- **مرسوم بقانون اتحادي رقم (33) لسنة 2021 ... وتعديلاته.pdf** (ERROR -> PASS): **Genuine recovery.** Run 8: 0 nodes / 0 chars total extraction collapse. Run 9: 884 blocks / 95,351 chars persisted (`cat_b_promoted`). The Arabic-CMap crash on this doc is fixed.

### Structural Improvements

- **Arabic CMap crash class eliminated**: All three Run 8 hard-ERROR Arabic docs (قرار 1/2022, قرار 106/2022, مرسوم 33/2021) now complete ingestion and persist artifacts. One (مرسوم 33) recovered real content (95k chars); the other two persist empty (0 chars) — the crash is fixed, the extraction for those two is not.
- **Garble gate now fires on وارد 597**: the previously documented garble-gate hole (numeric-junk text layer never flagged) is now detected (`garbling(ratio=1.00)` in verdict_reason). Detection landed; OCR escalation on detection has not.
- **Scoring-harness defect identified (meta-finding)**: the Run 9 report harness defaulted every unscored doc to ERROR while ingestion transcripts showed success. Confirmed against live MinIO — the judge/scoring stage never consumed the persisted metas. The incremental ingest+score pipeline's score step must fail loudly, not emit default-ERROR rows.

### Regressions

- **GHV-TKV-Tarif.pdf** (PASS -> MARGINAL): Identical 5-node / 13,022-char / depth-1 tree as Run 8's PASS now yields `leaf_concentration=0.39`. Pure verdict-threshold regression from the RFC-025 hysteresis/threshold change, not a content regression.
- **Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf** (PASS -> PASS, content regression): verdict label stable but extracted text collapsed 7,471 -> 492 chars, held at PASS only by `image_enrichment_promoted`. The promotion flag is masking a ~15x content loss.
- **world-stats-pocketbook-2023.pdf** (MARGINAL -> ERROR): No artifact in MinIO at all; the 6.1MB file never finished processing within the run window. Legitimate ERROR (timeout/incomplete), distinct from the harness-artifact ERRORs.

### Stalls

- **MOU MOHRE & Nafis (1).pdf** (FAIL -> MARGINAL, still 0 chars): zero-content persists (image-OCR-never-fires defect); only the verdict label softened. Not a recovery.
- **اتفاقية مستوى الخدمة ... موقعة من الطرفين.pdf** (FAIL -> MARGINAL, still 0 chars): same zero-content stall behind a softened verdict.
- **قرار مجلس الوزراء رقم (1) لسنة 2022 ....pdf** (ERROR -> MARGINAL, 0 chars): crash fixed, extraction still yields nothing — crash-to-empty conversion, content stall continues.
- **قرار مجلس الوزراء رقم (106) لسنة 2022 ....pdf** (ERROR -> MARGINAL, 0 chars): identical crash-to-empty pattern.
- **uae_numbers_english_page_16_17_landscape - Copy.pdf** (FAIL -> MARGINAL): 748 chars unchanged across Runs 7/8/9; numeric-table fragmentation unresolved, verdict softening only.
- **uae_numbers_english_page_16_17_portrait - Copy.pdf** (FAIL -> MARGINAL): 764 chars unchanged; same stall.
- **مرسوم بقانون اتحادي رقم (13) لسنة 2022 ....pdf** (FAIL -> PASS, 38 chars): *worse* content than Run 8 (60 -> 38 chars) with a *better* verdict via `image_enrichment_promoted` — recorded as a stall-with-gate-bypass, not an improvement.
- **وارد رقم 597 ....pdf** (FAIL/MARGINAL -> MARGINAL): garbling now detected (ratio=1.00) but content remains fully garbled at ~62.8k chars; OCR escalation still absent.

### Stable (No Change)

- **FEDERAL LAW NO (3) OF 1987 ... PENAL CODE - Copy.pdf** (PASS -> PASS): 606 nodes / 220.3k chars, RFC-005 splitter output preserved.
- **Reitlehrer - Schäden am Berittpferd.pdf** (PASS -> PASS): 10 nodes / 4,082 chars, byte-identical to Run 8 ground truth.
- **cabinet_resolution_no_96_of_2023 ....pdf** (PASS -> PASS): 108 nodes / 29,110 chars, identical.
- **federal_decree_law_no_33_of_2021 ....pdf** (PASS -> PASS): 502 nodes / 110,938 chars, identical.
- **سياسة حوكمة و إدارة البيانات - Copy.pdf** (PASS -> PASS): 24 nodes / 20,330 chars, identical.

### Not Comparable

- **القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf**: new in Run 9, no Run 8 baseline. PASS at 123 chars is itself suspect (see below).
- **حقوق الإنسان - Copy.pdf**: Run 8 FAIL (severe content loss); absent from the Run 9 batch, not re-tested.

---

## Regressions Requiring Investigation

| Category | Document(s) | Severity | Root Cause Pattern |
|---|---|---|---|
| Scoring-harness default-ERROR | All 24 batch records | Critical (process) | The judge/score stage of the incremental ingest+score pipeline never ran (or never consumed MinIO metas); the report harness silently emitted verdict=ERROR with null metrics for every doc. Score-stage failure must be loud and must never fabricate verdicts. |
| Quality-gate bypass via `image_enrichment_promoted` | مرسوم (13) 2022 (38 chars, PASS), القرار التنظيمي (123 chars, PASS), Unfallversicherung (492 chars, PASS) | Critical | The image-enrichment promotion path assigns PASS regardless of extracted text volume — near-zero-content docs persist as PASS, contravening Hard Rule 5 (never silently persist a low-quality tree). Gate must retain a minimum-content floor even when promoted. |
| Zero-content persisted as MARGINAL | MOU MOHRE, اتفاقية SLA, قرار 1/2022, قرار 106/2022 | High | Docs with `node_count=0` / 0 chars now persist with MARGINAL verdicts instead of FAIL/ERROR. Crash-to-empty conversion plus verdict softening hides total content loss (image-OCR-never-fires defect and Arabic extraction gaps remain unfixed underneath). |
| Leaf-concentration threshold flap | GHV-TKV-Tarif.pdf | Medium | Identical tree flipped PASS -> MARGINAL on `leaf_concentration=0.39`; RFC-025 hysteresis/threshold retune penalizes legitimately-flat short tariff docs. |
| Incomplete large-file run | world-stats-pocketbook-2023.pdf | Medium | 6.1MB file produced no MinIO artifact; run window/timeout insufficient. Needs re-run with completion monitoring before Run 10. |
| Garble detected but not escalated | وارد 597 | Medium | Garble gate now flags ratio=1.00, but the pipeline persists the fully-garbled text as MARGINAL instead of escalating to OCR re-extraction. Detection landed; the recovery hook is missing. |
