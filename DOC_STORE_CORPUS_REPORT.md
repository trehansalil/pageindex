# PageIndex doc_store/ Corpus Report

**Date:** 2026-07-11
**Server:** `localhost:8201` (VS Code debugger — MCP server + arq worker attached)
**Branch:** `feat/scaling-pageindex`
**Ingestion path:** `POST /upload/files` → arq → `converters_cli` (real production path, not the isolated-subprocess `preprocess_client.py` route)
**Total documents submitted:** 26 (`doc_store/`)
**Skipped (unsupported extension):** 1 (`image pie chart...jpg` — `.jpg` not in `_SUPPORTED`)
**Total processed:** 25
**Pipeline errors:** 0 — all 25 completed with status `done`

## Verdict Summary

| Verdict  | Count | Rate  |
| -------- | ----- | ----- |
| PASS     | 1     | 4.0%  |
| MARGINAL | 12    | 48.0% |
| FAIL     | 12    | 48.0% |

This corpus is dominated by the same Arabic/scanned/legal-document failure modes already tracked in `E2E_CORPUS_REPORT.md` — 21/25 files here overlap by content with that 62-file corpus. No new gap classes were found; this run reconfirms the 6 systemic gaps below against fresh doc_ids (this run used the live debugger-attached server, not the isolated preprocess path).

## Storage Reference

- **MinIO bucket:** `pageindex`
- **Tree docs:** `processed/<doc_id>.json`
- **Flat docs:** `processed/<doc_id>.flat.json`
- **Redis cache key:** `pageindex:doc:<doc_id>`

---

## Full Results

| #  | File                                                                                      | doc_id       | Type | Metrics                                                                                                                                                          | Verdict            | Reason                                                                                                          |
| -- | ----------------------------------------------------------------------------------------- | ------------ | ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------ | --------------------------------------------------------------------------------------------------------------- |
| 1  | `FEDERAL LAW NO (3) OF 1987 ... PENAL CODE - Copy.pdf`                                  | `2030e34d` | tree | nodes=20, depth=2, max_leaf=**236,413**/239,139                                                                                                            | **MARGINAL** | Gap 3: inline`Article (N)` markers unsplit — 99% of doc trapped in one leaf                                  |
| 2  | `Federal Decree-Law No. (47) of 2021 - Copy.pdf`                                        | `14f41037` | tree | nodes=13, depth=2, max_leaf=11,171/14,257                                                                                                                        | **MARGINAL** | Gap 3: same inline-marker tail-blob pattern                                                                     |
| 3  | `GHV-TKV-Tarif.pdf`                                                                     | `18286d95` | flat | blocks=24, class=flat_mixed, img=4/24, total=389                                                                                                                 | **MARGINAL** | Gap 6: tariff table column structure degraded                                                                   |
| 4  | `Haftpflicht-Allgemeine-Bedingungen.pdf.pdf`                                            | `05ea7b35` | tree | nodes=129, depth=2, max_leaf=6,006/55,051                                                                                                                        | **PASS**     | Clean; reasonable leaf sizes, real depth-2 hierarchy                                                            |
| 5  | `Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf`                                 | `499820a1` | tree | nodes=33, depth=2, max_leaf=13,112/96,454                                                                                                                        | **MARGINAL** | Flat hierarchy — 33 nodes but large, unevenly split leaves                                                     |
| 6  | `MOU MOHRE & Nafis & ... (1).pdf`                                                       | `073853bd` | flat | blocks=13, class=flat_prose,**img=13/13 (100%)**                                                                                                           | **FAIL**     | Gap 1: scanned Arabic MOU, zero text, OCR never escalates                                                       |
| 7  | `Ministerial Resolution No279 ... - Copy.pdf`                                           | `1f2c0c52` | tree | nodes=18, depth=2, max_leaf=5,752/9,982,**657 tab chars**                                                                                                  | **MARGINAL** | Tab-character artifacts interspersed in body text                                                               |
| 8  | `Reitlehrer - Schäden am Berittpferd.pdf`                                              | `6de45e87` | tree | nodes=7, depth=2, max_leaf=1,885/3,531                                                                                                                           | **MARGINAL** | Fragment — very limited extracted content                                                                      |
| 9  | `Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf`                               | `45c9e0b4` | flat | blocks=78, class=flat_mixed, img=63/78                                                                                                                           | **MARGINAL** | Gap 6: benefits-table structure degraded, majority image blocks                                                 |
| 10 | `cabinet_resolution_no_21...(1) (1) - Copy.pdf`                                         | `144fbaaf` | tree | nodes=22, depth=2, max_leaf=42,697/52,752,**12 embedded "ARTICLE" headers**                                                                                | **FAIL**     | Gap 3: Articles 5–9 + fee schedules merged into one leaf — headers found embedded inside node prove the merge |
| 11 | `cabinet_resolution_no_21...(1) - Copy.pdf`                                             | `1d682268` | tree | nodes=22, depth=2, max_leaf=42,697/52,752,**12 embedded "ARTICLE" headers**                                                                                | **FAIL**     | Same as#10 — identical tail-blob (duplicate source content)                                                    |
| 12 | `cabinet_resolution_no_96... - Copy.pdf`                                                | `4806d4bd` | tree | nodes=23, depth=2, max_leaf=21,245                                                                                                                               | **MARGINAL** | Gap 3: Articles 5–16 merged, same inline-marker pattern                                                        |
| 13 | `federal_decree_law_no_33...amendments - Copy.pdf`                                      | `2a7e0ebe` | tree | nodes=24, depth=2, max_leaf=**100,176**/120,654 (83% of doc in one leaf)                                                                                   | **FAIL**     | Gap 3, severe: near-total collapse into a single leaf                                                           |
| 14 | `uae_numbers_english...landscape - Copy.pdf`                                            | `55410100` | flat | blocks unknown, 7/9 image placeholders                                                                                                                           | **FAIL**     | Gap 1: infographic-style PDF, tables rendered as images, OCR never fires                                        |
| 15 | `uae_numbers_english...portrait - Copy.pdf`                                             | `11a82180` | flat | class=flat_mixed, 4/7 image placeholders                                                                                                                         | **FAIL**     | Gap 1: same doc, portrait orientation                                                                           |
| 16 | `world-stats-pocketbook-2023.pdf`                                                       | `621512a9` | flat | class=flat_mixed                                                                                                                                                 | **MARGINAL** | Gap 6: per-country summary table columns degraded                                                               |
| 17 | `اتفاقية مستوى الخدمة ... موقعة من الطرفين.pdf`         | `39959dd7` | flat | blocks=21, class=flat_prose,**img=21/21 (100%)**                                                                                                           | **FAIL**     | Gap 1: scanned Arabic SLA, zero text                                                                            |
| 18 | `القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf`           | `2c90ef0d` | tree | total=72,386 chars,**confirmed systemic font mojibake** (`Vð]…‡çÖ]‹×¥`, `ʨʱ`, `ȑ`)                                                        | **FAIL**     | Gap 2: embedded-font ToUnicode CMap corrupt — structure intact, text unreadable                                |
| 19 | `سياسة حوكمة و إدارة البيانات - Copy.pdf`                       | `70607efb` | tree | nodes=23, depth=3, max_leaf=2,423                                                                                                                                | **MARGINAL** | Gap 5: Arabic diacritics artifacts / RTL table field corruption                                                 |
| 20 | `قرار مجلس الوزراء رقم (1) لسنة 2022 ... .pdf`                    | `b604dbaa` | flat | blocks=21, class=flat_prose,**img=21/21 (100%)**                                                                                                           | **FAIL**     | Gap 1: scanned Arabic Cabinet Resolution, zero text                                                             |
| 21 | `قرار مجلس الوزراء رقم (106) لسنة 2022 ... .pdf`                  | `0fe0aeef` | flat | blocks=15, class=flat_prose,**img=15/15 (100%)**                                                                                                           | **FAIL**     | Gap 1: same as#20                                                                                               |
| 22 | `مرسوم بقانون اتحادي رقم (13) لسنة 2022 - Copy.pdf`             | `b1a72fb2` | tree | nodes=14, depth=2, total=5,452, confirmed**"Oleg" Latin mojibake**, 81`#` substitutions                                                                        | **FAIL**     | Gap 2: garble-gate bypass — mojibake persists (known prior test case, unfixed)                                 |
| 23 | `مرسوم بقانون اتحادي رقم (33) لسنة 2021 وتعديلاته.pdf` | `b87e897e` | tree | nodes=125, depth=4, max_leaf=6,447/119,316, confirmed**2,859× `في→#` substitution**                                                                  | **MARGINAL** | Gap 5: tail-blob previously fixed, but OCR text corruption persists                                             |
| 24 | `وارد رقم 597 ... - Copy.pdf`                                                    | `4f37b2e3` | flat | blocks=2,235, class=flat_mixed,**digit_ratio=0.91**, `1651001429`×1,400                                                                                 | **FAIL**     | Gap 2: garble-gate bypass — numeric-junk text layer accepted as valid                                          |
| 25 | `ﺣﻘﻮق اﻹﻧﺴﺎن - Copy.pdf`                                                      | `ae02da49` | tree | nodes=34, depth=4, max_leaf=**319,975**/364,174 (88% of doc in one leaf), confirmed **245,714 presentation-form vs 7,201 logical-form Arabic chars** | **FAIL**     | Gap 4: presentation-form Arabic (U+FE70–FEFF) bypasses the logical-form article regex entirely                 |

---

## Systemic Gaps Reconfirmed (all previously identified, none new)

| Gap                                                                                                     | Symptom                                                                                | Docs hit this run                                                                                         |
| ------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| **Gap 1** — OCR escalation never fires on image-only flat docs                                   | `escalated_ocr=False`, 100% `<!-- image -->` blocks persisted as "success"         | `073853bd`, `55410100`, `11a82180`, `39959dd7`, `b604dbaa`, `0fe0aeef` (6 FAILs)              |
| **Gap 2** — Garble-gate checks structure only, not text content                                  | Font-mojibake, digit-junk, and Latin-substitution text pass`validate_tree` untouched | `2c90ef0d`, `b1a72fb2`, `4f37b2e3` (3 FAILs)                                                        |
| **Gap 3** — Latin inline `Article (N)` marker not matched (line-anchored regex, no paren form) | Massive unsplit leaves (11k–236k chars)                                               | `2030e34d`, `14f41037`, `144fbaaf`, `1d682268`, `4806d4bd`, `2a7e0ebe` (2 FAILs, 4 MARGINALs) |
| **Gap 4** — Presentation-form Arabic (U+FE70–FEFF) bypasses logical-form regex                  | 319k-char tail-blob, article splitting never triggers                                  | `ae02da49` (1 FAIL)                                                                                     |
| **Gap 5** — Arabic OCR/text quality (diacritics, في→#, Latin artifacts)                       | Structure correct, text partially corrupted                                            | `70607efb`, `b87e897e` (2 MARGINALs)                                                                  |
| **Gap 6** — Table column structure degrades on complex tables<br />                              | Headers/values concatenated, columns lost                                              | `18286d95`, `45c9e0b4`, `621512a9` (3 MARGINALs)                                                    |

No fix work was done in this run — this was a read-only ingestion pass to exercise the live debugger-attached server/worker. All gaps above are pre-existing and tracked against the same root causes documented in `E2E_CORPUS_REPORT.md`.

## Positive Findings

1. **Zero pipeline errors** — all 25 supported files completed with `status=done` through the real HTTP→arq→worker path (not the isolated-subprocess preprocess route).
2. `Haftpflicht-Allgemeine-Bedingungen.pdf.pdf` is a clean PASS — proper depth-2 hierarchy, no garbling, no tail-blob.
3. **مرسوم 33's tail-blob fix holds** — 125 nodes / depth 4 / max_leaf 6.4k (vs. a document this size collapsing to one blob), confirming the splitter fix from prior work is still effective; only the في→# OCR substitution remains as a separate, known issue.
4. **Duplicate-content detection working correctly** — the two `cabinet_resolution_no_21...` copies (`144fbaaf`, `1d682268`) produced byte-for-byte identical tree metrics, confirming deterministic extraction.

---

## Raw Doc Reference

| doc_id       | Source File                                     | job_id       | content_class | MinIO Path                       |
| ------------ | ----------------------------------------------- | ------------ | ------------- | -------------------------------- |
| `2030e34d` | PENAL CODE.pdf                                  | `dceaa3e9` | —            | `processed/2030e34d.json`      |
| `14f41037` | Decree-Law 47.pdf                               | `75029ef3` | —            | `processed/14f41037.json`      |
| `18286d95` | GHV-TKV-Tarif.pdf                               | `b8cd5f1f` | flat_mixed    | `processed/18286d95.flat.json` |
| `05ea7b35` | Haftpflicht-Allgemeine.pdf                      | `4a214554` | —            | `processed/05ea7b35.json`      |
| `499820a1` | Haftpflicht-Besondere.pdf                       | `19e32182` | —            | `processed/499820a1.json`      |
| `073853bd` | MOU MOHRE.pdf                                   | `11486428` | flat_prose    | `processed/073853bd.flat.json` |
| `1f2c0c52` | Ministerial Res 279.pdf                         | `c542ecd1` | —            | `processed/1f2c0c52.json`      |
| `6de45e87` | Reitlehrer-Schäden.pdf                         | `e8fdd1dc` | —            | `processed/6de45e87.json`      |
| `45c9e0b4` | Unfallversicherung.pdf                          | `96dfcbde` | flat_mixed    | `processed/45c9e0b4.flat.json` |
| `144fbaaf` | cabinet_res_21(1)(1).pdf                        | —           | —            | `processed/144fbaaf.json`      |
| `1d682268` | cabinet_res_21(1).pdf                           | —           | —            | `processed/1d682268.json`      |
| `4806d4bd` | cabinet_res_96.pdf                              | —           | —            | `processed/4806d4bd.json`      |
| `2a7e0ebe` | federal_decree_law_33.pdf                       | —           | —            | `processed/2a7e0ebe.json`      |
| `55410100` | uae_numbers_landscape.pdf                       | —           | flat          | `processed/55410100.flat.json` |
| `11a82180` | uae_numbers_portrait.pdf                        | —           | flat_mixed    | `processed/11a82180.flat.json` |
| `621512a9` | world-stats-pocketbook.pdf                      | —           | flat_mixed    | `processed/621512a9.flat.json` |
| `39959dd7` | اتفاقية مستوى الخدمة.pdf      | —           | flat_prose    | `processed/39959dd7.flat.json` |
| `2c90ef0d` | القرار التنظيمي.pdf               | —           | —            | `processed/2c90ef0d.json`      |
| `70607efb` | سياسة حوكمة البيانات.pdf      | —           | —            | `processed/70607efb.json`      |
| `b604dbaa` | قرار مجلس الوزراء رقم 1.pdf   | —           | flat_prose    | `processed/b604dbaa.flat.json` |
| `0fe0aeef` | قرار مجلس الوزراء رقم 106.pdf | —           | flat_prose    | `processed/0fe0aeef.flat.json` |
| `b1a72fb2` | مرسوم 13 (Arabic).pdf                      | —           | —            | `processed/b1a72fb2.json`      |
| `b87e897e` | مرسوم 33 (Arabic).pdf                      | —           | —            | `processed/b87e897e.json`      |
| `4f37b2e3` | وارد 597.pdf                                | —           | flat_mixed    | `processed/4f37b2e3.flat.json` |
| `ae02da49` | ﺣﻘﻮق اﻹﻧﺴﺎن.pdf                       | —           | —            | `processed/ae02da49.json`      |
