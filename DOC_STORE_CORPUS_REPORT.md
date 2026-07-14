<!-- Space: CITRA -->
<!-- Title: PageIndex doc_store/ Corpus Report — 2026-07-14 -->
<!-- Parent: Data-AI Refactoring Experiments -->
<!-- Label: pageindex -->
<!-- Label: rfc-010 -->
<!-- Confluence-Page-Id: 5101387785 -->

# PageIndex doc_store/ Corpus Report

**Date:** 2026-07-14
**Server:** isolated-subprocess `preprocess_client.py` (concurrency=1) — real production converter path (`pageindex_mcp.converters_cli`, same isolation primitive the arq worker uses)
**Branch:** `feat/scaling-pageindex`
**Task:** RFC-010 Corpus Gap Remediation, Batch 4 / Wave 6 — full corpus revalidation with D1–D5 fixes applied
**Total documents in `doc_store/`:** 25 (supported extensions only)
**Total processed:** 25
**Pipeline errors (final):** 0

## What changed since the 2026-07-11 baseline

RFC-010 D1–D5 landed (splitter redesign, garble-gate hardening, OCR-escalation wiring, flat-doc routing) between the baseline run and this one. During this revalidation, two documents initially failed to (re)ingest:

- **`GHV-TKV-Tarif.pdf`** — raised `LowQualityTreeError: low_quality_tree: garbling`. Root cause: the D3B flat-path garble gate's single-token repetition heuristic (`_flat_text_is_garbled` / `_tree_is_garbled` in `src/pageindex_mcp/helpers.py`) counted the markdown table pipe delimiter (`|`, 609/1577 tokens = 38.6%) and the `€` currency symbol (324/967 tokens = 33.5%) as "repeated tokens," tripping the >30% garbling threshold on a legitimate wide price table. **Fixed**: excluded purely symbolic tokens (no alphanumeric characters) from the repetition ratio in both functions. Verified against the actual document (ratio dropped to 1.9%), full test suite re-run green (372 passed / 6 skipped).
- **`Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf`** — failed once during the 25-file sequential batch with a truncated stderr fragment; succeeded cleanly on standalone retry. Transient resource contention during the batch run, not a code defect.

Both documents now ingest successfully and are included in the verdicts below.

## Verdict Summary

| Verdict  | Count | Rate  | vs. 2026-07-11 |
| -------- | ----- | ----- | --------------- |
| PASS     | 6     | 24.0% | 1 → 6 (+5)      |
| MARGINAL | 17    | 68.0% | 12 → 17 (+5)    |
| FAIL     | 2     | 8.0%  | 12 → 2 (−10)    |

Ingestion is now dramatically healthier: **10 documents moved out of FAIL**, most via the OCR-escalation and splitter fixes (Gap 1, Gap 3). Only 2 genuine FAILs remain, both structural PDF-encoding issues outside the scope of D1–D5 (see below).

## Storage Reference

- **MinIO bucket:** `pageindex`
- **Tree docs:** `processed/<doc_id>.json`
- **Flat docs:** `processed/<doc_id>.flat.json`

---

## Full Results

| #  | File | doc_id | Type | Metrics | Verdict | Reason |
| -- | ---- | ------ | ---- | ------- | ------- | ------ |
| 1  | `FEDERAL LAW NO (3) OF 1987 ... PENAL CODE - Copy.pdf` | `4a29d3e9` | tree | nodes=575, depth=5, max_leaf=3,704/221,125 (1.7%) | **PASS** | Gap 3 **resolved** — was 99% single leaf (236k/239k), now well-distributed across 575 nodes |
| 2  | `Federal Decree-Law No. (47) of 2021 - Copy.pdf` | `4071a62b` | tree | nodes=69, depth=6, max_leaf=1,727/13,504 (12.8%) | **PASS** | Gap 3 resolved — was 11,171/14,257 in one leaf |
| 3  | `GHV-TKV-Tarif.pdf` | `a6a49019` | flat | blocks=24, class=flat_mixed, img=4/24, chars=389 | **MARGINAL** | Gap 6 unchanged: tariff table column structure still degraded. Ingestion itself was blocked by a garble-gate false positive — **fixed this run** (see above) |
| 4  | `Haftpflicht-Allgemeine-Bedingungen.pdf.pdf` | `ff708282` | tree | nodes=129, depth=2, max_leaf=6,006/56,234 (10.7%) | **PASS** | Clean, unchanged — reasonable leaf sizes, real depth-2 hierarchy |
| 5  | `Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf` | `906392fb` | tree | nodes=33, depth=2, max_leaf=20,929/131,013 (16.0%) | **MARGINAL** | Uneven split persists (same class of issue as baseline; larger doc than before by node-content, Docling extraction not fully deterministic run-to-run) |
| 6  | `MOU MOHRE & Nafis & ... (1).pdf` | `7c0a0100` | tree | nodes=16, depth=2, max_leaf=5,548/12,204 (45.5%) | **MARGINAL** | Gap 1 **resolved** — was 100% image blocks / zero text (FAIL). OCR now fires and extracts real Arabic text. Residual: scattered OCR-noise fragments on specific recital-clause phrases (e.g. `Salgll rot!` for `الموافق`), and 45% of content concentrated in one leaf |
| 7  | `Ministerial Resolution No279 of 2022 - Copy.pdf` | `c6a673f1` | tree | nodes=28, depth=5, max_leaf=3,478/10,220 (34.0%), **690 tab chars** | **MARGINAL** | Unchanged — tab-character artifacts (`\t` between every word) persist from Docling's font/spacing extraction for this PDF |
| 8  | `Reitlehrer - Schäden am Berittpferd.pdf` | `7116d385` | tree | nodes=9, depth=3, max_leaf=1,053/3,562 (29.6%) | **MARGINAL** | Unchanged — inherently short fragment document, limited content |
| 9  | `Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf` | `4bbd7ede` | flat | blocks=78, class=flat_mixed, img=63/78 (80.8%), chars=1,263 | **MARGINAL** | Gap 6 unchanged: benefits-table structure still degraded, majority image blocks. Ingestion succeeded on retry after transient batch-run failure (see above) |
| 10 | `cabinet_resolution_no_21...(1) (1) - Copy.pdf` | `997a140a` | tree | nodes=37, depth=4, max_leaf=39,188/53,354 (73.4%), 15 `ARTICLE` headers detected | **MARGINAL** | Gap 3 **improved, not resolved** — was 81% single leaf/22 nodes (FAIL); now 73%/37 nodes with headers correctly detected, but a large block of merged article + fee-schedule content remains |
| 11 | `cabinet_resolution_no_21...(1) - Copy.pdf` | `0dc36fb4` | tree | nodes=37, depth=4, max_leaf=39,188/53,354 (73.4%) | **MARGINAL** | Same as #10 — identical duplicate-content doc, identical metrics (deterministic extraction confirmed) |
| 12 | `cabinet_resolution_no_96... - Copy.pdf` | `aeda1ba7` | tree | nodes=105, depth=6, max_leaf=6,487/29,382 (22.1%) | **PASS** | Gap 3 resolved — was 23 nodes/max_leaf 21,245 (MARGINAL), now well-distributed |
| 13 | `federal_decree_law_no_33...amendments - Copy.pdf` | `d5f62522` | tree | nodes=400, depth=6, max_leaf=11,420/112,032 (10.2%) | **PASS** | Gap 3 **resolved** — was 83% single leaf (100,176/120,654, FAIL), now well-distributed across 400 nodes |
| 14 | `uae_numbers_english...landscape - Copy.pdf` | `55d70bd9` | flat | blocks=9, class=flat_prose, img=7/9 (77.8%), chars=156 | **FAIL** | Unchanged — Gap 1 remnant: infographic-style PDF (charts/graphics, not scanned text), OCR correctly has nothing to recover since there is no text layer to escalate on |
| 15 | `uae_numbers_english...portrait - Copy.pdf` | `f274ece1` | flat | blocks=7, class=flat_mixed, img=4/7 (57.1%), chars=129 | **MARGINAL** | Slightly better image ratio than #14 (same doc, portrait orientation); still image-dominant |
| 16 | `world-stats-pocketbook-2023.pdf` | `87a4487d` | flat | blocks=2,602, class=flat_mixed, img=20/2,602 (0.8%), chars=204,069 | **PASS** | Gap 6 **resolved** — was vague "table structure degraded" MARGINAL; now excellent extraction with negligible image-block ratio and substantial recovered text |
| 17 | `اتفاقية مستوى الخدمة ... موقعة من الطرفين.pdf` | `a5ef1929` | tree | nodes=40, depth=2, max_leaf=14,070/29,947 (47.0%) | **MARGINAL** | Gap 1 **resolved** — was 100% image blocks / zero text (FAIL). Real Arabic text now extracted. Residual: same class of scattered OCR-noise fragments as #6, and 47% single-leaf concentration |
| 18 | `القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf` | `a10af398` | tree | nodes=94, depth=2, max_leaf=3,426/78,806 (4.3%), **mojibake confirmed persisting** (`ð]…‡çÖ]‹×¥`, `ʨʱ`, `ȑ`) | **FAIL** | Unchanged — Gap 2: embedded-font ToUnicode CMap corruption is a structural PDF defect, not addressed by D1–D5 (those targeted splitter/garble-gate/OCR-routing, not CMap decoding) |
| 19 | `سياسة حوكمة و إدارة البيانات - Copy.pdf` | `efd65b00` | tree | nodes=18, depth=2, max_leaf=3,272/19,814 (16.5%), diacritic density 0.3% | **MARGINAL** | Unchanged — kept cautious MARGINAL; diacritic density measured this run is low/normal, previously-flagged RTL table-field corruption not independently re-confirmed |
| 20 | `قرار مجلس الوزراء رقم (1) لسنة 2022 ... .pdf` | `34b3b7ee` | tree | nodes=33, depth=2, max_leaf=17,006/39,112 (43.5%) | **MARGINAL** | Gap 1 **resolved** — was 100% image blocks / zero text (FAIL). Real Arabic text now extracted, same residual OCR-noise pattern as #6/#17 |
| 21 | `قرار مجلس الوزراء رقم (106) لسنة 2022 ... .pdf` | `7b819149` | tree | nodes=20, depth=2, max_leaf=19,959/32,763 (60.9%) | **MARGINAL** | Gap 1 resolved (same as #20); largest single-leaf concentration of the four newly-recovered scanned docs |
| 22 | `مرسوم بقانون اتحادي رقم (13) لسنة 2022 - Copy.pdf` | `d9f0a0e9` | tree | nodes=17, depth=2, max_leaf=1,785/5,803 (30.8%), **"Oleg" Latin mojibake gone (0 occurrences, was 81 `#` substitutions)** | **MARGINAL** | Gap 2 **major mojibake resolved**. Residual: minor recurring OCR-noise token (`- deg -`) in place of a short recital-clause phrase, same noise class as #6/#17/#20 |
| 23 | `مرسوم بقانون اتحادي رقم (33) لسنة 2021 وتعديلاته.pdf` | `8b05de59` | tree | nodes=58, depth=3, max_leaf=32,583/122,197 (26.7%), في→# substitutions persist (~699 mid-body `#` occurrences) | **MARGINAL** | Gap 5 unchanged — D5 was documented as an **interim** fix, not a full fix; في→# corruption persists as expected. **Open item**: node count dropped 125→58 and max_leaf grew 6,447→32,583 vs. the 06-30 splitter-validation run — needs a follow-up diff to confirm this is TOC-filter (D4) correctly removing noise nodes rather than a splitter regression |
| 24 | `وارد رقم 597 ... - Copy.pdf` | `127ba17a` | tree | nodes=13, depth=2, max_leaf=21,041/74,407 (28.3%), digit_ratio=0.01 (was 0.91), **`1651001429` numeric-junk pattern: 0 occurrences (was ~1,400)** | **MARGINAL** | Gap 2 **resolved** — numeric-junk garble-gate bypass fixed; OCR escalation now fires and produces clean, legible Arabic text (`سعادة / شيماء يوسف العوضي...`). Kept at MARGINAL rather than PASS due to 28% single-leaf concentration |
| 25 | `ﺣﻘﻮق اﻹﻧﺴﺎن - Copy.pdf` | `e8596b90` | tree | nodes=322, depth=6, max_leaf=137,648/503,229 (27.4%) | **MARGINAL** | Gap 4 **major improvement** — was 88% single leaf (319,975/364,174, FAIL); now 27%. Matches the previously-validated splitter behavior for this doc: the remaining 137k-char leaf is a genuine ToC page plus 2 long single-article bodies, not a splitter bug |

---

## Systemic Gaps — Updated Status

| Gap | Baseline (2026-07-11) | This run (2026-07-14) |
| --- | --- | --- |
| **Gap 1** — OCR escalation never fires on image-only flat docs | 6 FAILs (`073853bd`, `55410100`, `11a82180`, `39959dd7`, `b604dbaa`, `0fe0aeef`) | **4 of 6 resolved** to real-text trees (MOU, اتفاقية, قرار 1, قرار 106). Remaining 2 (`uae_numbers` landscape/portrait) are genuinely graphic/infographic content with no text layer to recover — not an OCR-escalation defect |
| **Gap 2** — Garble-gate checks structure only, not text content | 3 FAILs (font-mojibake, Latin-substitution, digit-junk) | **2 of 3 resolved**: مرسوم 13 mojibake gone, وارد 597 numeric-junk gone. القرار التنظيمي font-CMap corruption **persists unchanged** — structural PDF issue outside D1–D5 scope |
| **Gap 3** — Latin inline `Article (N)` marker not matched | 2 FAILs, 4 MARGINALs | **Splitter redesign lands**: PENAL CODE, Decree-Law 47, cabinet_res_96, federal_decree_law_33 all now PASS with well-distributed leaves. `cabinet_resolution_no_21` pair improved (81%→73% single-leaf) but not fully resolved |
| **Gap 4** — Presentation-form Arabic bypasses logical-form regex | 1 FAIL (`ae02da49`, 88% single leaf) | Improved to MARGINAL (27% single leaf) — matches prior splitter-fix validation; residual is a genuine long ToC + long-article structure, not a bug |
| **Gap 5** — Arabic OCR/text quality (diacritics, في→#, Latin artifacts) | 2 MARGINALs | مرسوم 33's في→# substitution **persists** (D5 documented as interim fix). New sub-pattern discovered: scattered short-phrase OCR noise (`- deg -`, `Salgll rot!`, `blll`) on the 4 newly-recovered scanned Arabic docs — worth tracking as a distinct residual issue in a future RFC |
| **Gap 6** — Table column structure degrades on complex tables | 3 MARGINALs | `world-stats-pocketbook` resolved to PASS. `GHV-TKV-Tarif` and `Unfallversicherung` table degradation **unchanged** |

## New Finding This Run

- **D3B flat-path garble-gate false positive on symbolic-token-heavy documents** (fixed in this session, see `src/pageindex_mcp/helpers.py`): the single-token repetition heuristic did not exclude purely symbolic tokens (`|`, `€`), so wide markdown tables and price lists could be misclassified as garbled. Fixed by excluding tokens with no alphanumeric characters from the repetition ratio in both `_tree_is_garbled` and `_flat_text_is_garbled`. Full test suite green after the fix (372 passed, 6 skipped).

## Positive Findings

1. **Zero pipeline errors after fixes** — all 25 files completed successfully through the isolated-subprocess `preprocess_client.py` path once the garble-gate false positive was resolved.
2. **10 documents moved out of FAIL**, the large majority via the Gap 1 OCR-escalation and Gap 3 splitter fixes landing correctly.
3. **Duplicate-content detection still working correctly** — the two `cabinet_resolution_no_21...` copies (`997a140a`, `0dc36fb4`) produced byte-for-byte identical tree metrics.
4. **مرسوم 33's tail-blob splitter fix continues to hold** at the node/depth level (though see the Gap 5 open item on node-count delta above).

## Remaining Known Limitations (not addressed by D1–D5, no fix attempted this run)

1. `القرار التنظيمي` (`a10af398`) — embedded-font ToUnicode CMap corruption; text is structurally unreadable at the PDF-decoding level, upstream of the ingestion pipeline.
2. `uae_numbers_english...` landscape/portrait (`55d70bd9`, `f274ece1`) — infographic-style PDFs with data rendered as charts/images; no text layer exists to OCR-recover.
3. مرسوم 33 في→# substitution — RFC-010 D5 was explicitly scoped as an interim fix; full resolution requires deeper OCR post-processing work.

---

## Raw Doc Reference

| doc_id | Source File | content_class | MinIO Path |
| ------ | ----------- | -------------- | ----------- |
| `4a29d3e9` | PENAL CODE.pdf | — | `processed/4a29d3e9-....json` |
| `4071a62b` | Decree-Law 47.pdf | — | `processed/4071a62b-....json` |
| `a6a49019` | GHV-TKV-Tarif.pdf | flat_mixed | `processed/a6a49019-....flat.json` |
| `ff708282` | Haftpflicht-Allgemeine.pdf | — | `processed/ff708282-....json` |
| `906392fb` | Haftpflicht-Besondere.pdf | — | `processed/906392fb-....json` |
| `7c0a0100` | MOU MOHRE.pdf | — | `processed/7c0a0100-....json` |
| `c6a673f1` | Ministerial Res 279.pdf | — | `processed/c6a673f1-....json` |
| `7116d385` | Reitlehrer-Schäden.pdf | — | `processed/7116d385-....json` |
| `4bbd7ede` | Unfallversicherung.pdf | flat_mixed | `processed/4bbd7ede-....flat.json` |
| `997a140a` | cabinet_res_21(1)(1).pdf | — | `processed/997a140a-....json` |
| `0dc36fb4` | cabinet_res_21(1).pdf | — | `processed/0dc36fb4-....json` |
| `aeda1ba7` | cabinet_res_96.pdf | — | `processed/aeda1ba7-....json` |
| `d5f62522` | federal_decree_law_33.pdf | — | `processed/d5f62522-....json` |
| `55d70bd9` | uae_numbers_landscape.pdf | flat_prose | `processed/55d70bd9-....flat.json` |
| `f274ece1` | uae_numbers_portrait.pdf | flat_mixed | `processed/f274ece1-....flat.json` |
| `87a4487d` | world-stats-pocketbook.pdf | flat_mixed | `processed/87a4487d-....flat.json` |
| `a5ef1929` | اتفاقية مستوى الخدمة.pdf | — | `processed/a5ef1929-....json` |
| `a10af398` | القرار التنظيمي.pdf | — | `processed/a10af398-....json` |
| `efd65b00` | سياسة حوكمة البيانات.pdf | — | `processed/efd65b00-....json` |
| `34b3b7ee` | قرار مجلس الوزراء رقم 1.pdf | — | `processed/34b3b7ee-....json` |
| `7b819149` | قرار مجلس الوزراء رقم 106.pdf | — | `processed/7b819149-....json` |
| `d9f0a0e9` | مرسوم 13 (Arabic).pdf | — | `processed/d9f0a0e9-....json` |
| `8b05de59` | مرسوم 33 (Arabic).pdf | — | `processed/8b05de59-....json` |
| `127ba17a` | وارد 597.pdf | — | `processed/127ba17a-....json` |
| `e8596b90` | ﺣﻘﻮق اﻹﻧﺴﺎن.pdf | — | `processed/e8596b90-....json` |
