# PageIndex E2E Corpus Report

**Date:** 2026-07-10
**Server:** `localhost:8201` (VS Code debugger)
**Branch:** `feat/scaling-pageindex`
**Total documents:** 62 (27 German + 35 mixed Arabic/English/German)
**Pipeline errors:** 0 — all 62 completed with status `done`

## Verdict Summary

| Corpus                    | Pass         | Marginal     | Fail         | Pass Rate       |
| ------------------------- | ------------ | ------------ | ------------ | --------------- |
| German`issue/data` (27) | 22           | 5            | 0            | 81.5%           |
| Mixed`issue/data2` (35) | 13           | 9            | 13           | 37.1%           |
| **Total**           | **35** | **14** | **13** | **56.5%** |

## Storage Reference

- **MinIO bucket:** `pageindex`
- **Tree docs:** `processed/<doc_id>.json`
- **Flat docs:** `processed/<doc_id>.flat.json`
- **Metadata sidecar:** `processed/<doc_id>.meta.json`
- **Raw uploads:** `uploads/<doc_id>/*`
- **Redis cache key:** `pageindex:doc:<doc_id>`

---

## German Corpus — `issue/data` (27 files, 0 fails)

| #  | File                                                                    | doc_id       | job_id                                   | Verdict        | Doc Type | MinIO Path                       | Metrics                            | Notes                                           |
| -- | ----------------------------------------------------------------------- | ------------ | ---------------------------------------- | -------------- | -------- | -------------------------------- | ---------------------------------- | ----------------------------------------------- |
| 1  | `AKB.pdf.pdf`                                                         | `c7daf6a1` | `f2c2b224-da25-458e-bf55-a116eea81301` | **PASS** | tree     | `processed/c7daf6a1.json`      | 405 nodes, depth 4, max_leaf 24.5k | Clean, well-structured                          |
| 2  | `AVB-PHV-Basis.pdf.pdf`                                               | `416384b8` | `09fd424e-57af-4cdc-a9c0-e1e9bfa063bd` | **PASS** | tree     | `processed/416384b8.json`      | 287 nodes, depth 5, max_leaf 24.5k | Clean                                           |
| 3  | `AVB-PHV-Komfort.pdf.pdf`                                             | `caf26a8b` | `9ce99f73-c1d7-42f2-bef7-b0ad8aaeeb6e` | **PASS** | tree     | `processed/caf26a8b.json`      | 301 nodes, depth 4                 | Clean                                           |
| 4  | `AVB-PHV-Premium.pdf.pdf`                                             | `fca365ae` | `adb60b2b-c8f0-4684-bcfd-191e15898f45` | **PASS** | tree     | `processed/fca365ae.json`      | 320 nodes, depth 4                 | Clean                                           |
| 5  | `Downloadbereich Dokumente - GHV VERSICHERUNG.pdf`                    | `2ddf5adf` | `46bd6742-6479-4ac2-981d-73762598747e` | **PASS** | tree     | `processed/2ddf5adf.json`      | 20 nodes, depth 3                  | Catalog page                                    |
| 6  | `GHV-TKV-Tarif.pdf`                                                   | `d526b12e` | `1e31a468-4296-4353-b1f1-eaa6b4a84d18` | MARGINAL       | flat     | `processed/d526b12e.flat.json` | flat_mixed                         | Table extraction gap: column structure degraded |
| 7  | `Haftpflicht-Allgemeine-Bedingungen.pdf.pdf`                          | `fb43f1a8` | `cab05ff2-8bf5-439f-9676-6fe9116b5b6a` | MARGINAL       | tree     | `processed/fb43f1a8.json`      | 129 nodes, depth 1                 | Flat hierarchy — depth 1 despite 129 nodes     |
| 8  | `Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf`               | `499820a1` | `d79a298f-ba69-4a0a-8c04-fd92386668be` | MARGINAL       | tree     | `processed/499820a1.json`      | 33 nodes, depth 2, max_leaf 13.1k  | Flat hierarchy                                  |
| 9  | `Hunde-Kranken-Besondere-Bedingungen-2024-002.pdf.pdf`                | `952f3324` | `31c04e21-79bf-4fc3-a9d4-8f7f7860f324` | **PASS** | flat     | `processed/952f3324.flat.json` | 21 blocks                          | IPID, clean                                     |
| 10 | `Hunde-OP-Besondere-Bedingungen-2024-002.pdf.pdf`                     | `681d2dcc` | `48dbaafb-83b8-4903-9e79-754ea7ecd694` | **PASS** | flat     | `processed/681d2dcc.flat.json` | 22 blocks                          | IPID, clean                                     |
| 11 | `Hundehalter-Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf` | `1bb886ae` | `8246ccbc-623e-4e65-b22d-3d7dab843313` | **PASS** | tree     | `processed/1bb886ae.json`      | 3 nodes, depth 2                   | Benefits sheet                                  |
| 12 | `Hundehalterhaftpflicht-Besondere-Bedingungen.pdf.pdf`                | `f62ffe38` | `a8b0006e-108b-4145-805f-8164a54bbe50` | **PASS** | tree     | `processed/f62ffe38.json`      | 128 nodes, depth 1                 | Minor title truncation                          |
| 13 | `Hundeleben-Allgemeine-Bedingungen.pdf.pdf`                           | `8ad9e2b6` | `fd443abe-26de-4662-bb42-43a432338a42` | **PASS** | tree     | `processed/8ad9e2b6.json`      | 68 nodes, depth 1                  | Clean                                           |
| 14 | `Katzen-Kranken-Besondere-Bedingungen-2024-002.pdf.pdf`               | `4ffb3191` | `be6fe3b7-ba6b-4d32-bf0f-be6eae2a3e5b` | **PASS** | flat     | `processed/4ffb3191.flat.json` | 21 blocks                          | IPID, clean                                     |
| 15 | `Katzen-OP-Besondere-Bedingungen-2024-002.pdf.pdf`                    | `45d07251` | `2c314fb3-54e2-4268-8a05-7ef02f0ac778` | **PASS** | tree     | `processed/45d07251.json`      | 22 nodes, depth 2                  | IPID, clean                                     |
| 16 | `Kundeninformation-GHV-2025-001.pdf.pdf`                              | `23102933` | `68fbc4d5-30fb-4068-9345-8bb1bd840003` | **PASS** | tree     | `processed/23102933.json`      | 8 nodes, depth 2                   | Info sheet                                      |
| 17 | `Meutenversicherung-Besondere-Bedingungen-2024-001.pdf.pdf`           | `a0f71859` | `0c7c28ca-6e0b-4038-9fb2-39275a9f8898` | **PASS** | flat     | `processed/a0f71859.flat.json` | 21 blocks                          | IPID, clean                                     |
| 18 | `Pferde-Kranken-Besondere-Bedingungen-2025-002.pdf.pdf`               | `c5fc32c6` | `ccc2d171-e1d5-4126-85fc-abacd608f651` | **PASS** | flat     | `processed/c5fc32c6.flat.json` | 26 blocks                          | Clean                                           |
| 19 | `Pferde-OP-Besondere-Bedingungen-2025-002.pdf.pdf`                    | `d09334b3` | `5f04c7b9-c6d3-4aed-8edb-2e1d08fabbe2` | **PASS** | flat     | `processed/d09334b3.flat.json` | 23 blocks                          | Clean                                           |
| 20 | `Pferdehalterhaftpflicht-Besondere-Bedingungen.pdf.pdf`               | `25e4273c` | `8a16a359-fcae-49bc-a263-60ccbad33e64` | **PASS** | tree     | `processed/25e4273c.json`      | 134 nodes, depth 2                 | Clean                                           |
| 21 | `Reitlehrer - Bereiter - Kutschfahrlehrer.pdf`                        | `02f4447f` | `729aa976-b822-4387-bdd9-da66ccd8dc5f` | **PASS** | flat     | `processed/02f4447f.flat.json` | 12 blocks, flat_mixed              | Clean                                           |
| 22 | `Reitlehrer - Bereiter.pdf`                                           | `2fa6b729` | `8163e4b7-ebdf-4fe5-bb7b-4f6af9515c3f` | **PASS** | flat     | `processed/2fa6b729.flat.json` | 8 blocks, flat_mixed               | Clean                                           |
| 23 | `Reitlehrer - Schäden am Berittpferd.pdf`                            | `a6ed79d0` | `ec64e9bd-ea79-4241-854c-87212d44fccf` | MARGINAL       | tree     | `processed/a6ed79d0.json`      | 7 nodes, depth 1                   | Fragment, limited content                       |
| 24 | `Reiter-Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf`      | `7da16bd8` | `bb4386e1-2454-49de-9e6d-1644f41331f9` | **PASS** | flat     | `processed/7da16bd8.flat.json` | 56 blocks, flat_mixed              | Benefits sheet                                  |
| 25 | `Tarifblatt-Privat.pdf`                                               | `367b57d2` | `d71e3ec3-f511-4aef-966d-f7a25d07c9cd` | **PASS** | tree     | `processed/367b57d2.json`      | 4 nodes, depth 2                   | Tables clean                                    |
| 26 | `Tier-OP-Kranken-Allgemeine-Bedingungen-2025-001.pdf.pdf`             | `cbd653d5` | `e094a695-c4ec-4562-a005-ebfa4b50bb2e` | **PASS** | tree     | `processed/cbd653d5.json`      | 82 nodes, depth 1                  | Medical terms clean                             |
| 27 | `Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf`             | `45c9e0b4` | `ed28c467-3fe3-4053-b1f3-f2e09da8c5d1` | MARGINAL       | flat     | `processed/45c9e0b4.flat.json` | 78 blocks, flat_mixed, 63 images   | Table extraction gap                            |

---

## Mixed Corpus — `issue/data2` (35 files)

### PASS (13 files)

| #  | File                                                       | doc_id       | job_id                                   | Doc Type | MinIO Path                       | Metrics                           | Notes                      |
| -- | ---------------------------------------------------------- | ------------ | ---------------------------------------- | -------- | -------------------------------- | --------------------------------- | -------------------------- |
| 1  | `32-305_Antrag Internationaler Führerschein - Copy.pdf` | `d674c5cd` | `1a07d53c-ace6-48ae-bbdf-c27ac0660ad4` | tree     | `processed/d674c5cd.json`      | 10 nodes, depth 1                 | German form, clean         |
| 2  | `Amendment of Service Fees...PDF`                        | `57e08886` | `3a912da2-4033-480c-b32b-fe4d1003c9f6` | tree     | `processed/57e08886.json`      | 12 nodes, depth 1                 | English legal, clean       |
| 3  | `Cabinet Resolution_...Decree-Law No. 33 - Copy.pdf`     | `d15c2d11` | `28231f63-1395-42b5-a2bb-84aec78056c7` | tree     | `processed/d15c2d11.json`      | 78 nodes, depth 2                 | English, clean             |
| 4  | `Economic Activities - Copy.pdf`                         | `e4b7c5e1` | `1d057666-2837-4ba8-b800-cb0712d3de5a` | flat     | `processed/e4b7c5e1.flat.json` | flat_mixed                        | ISIC table labels restored |
| 5  | `Federal Decree-Law No. (13)...(4) - Copy.pdf`           | `6c9e5386` | `63ac2da8-3a94-4219-b732-6a8d6970716f` | tree     | `processed/6c9e5386.json`      | 28 nodes, depth 2                 | English, clean             |
| 6  | `Federal Decree-Law No. (13)...Copy.pdf`                 | `182b775a` | `21fc034d-fbfd-4714-8f27-0a8b7b5ddbe7` | tree     | `processed/182b775a.json`      | 28 nodes, depth 2                 | Consistent with copy 1     |
| 7  | `federal_decree_law_no_13...Copy.pdf`                    | `1418abad` | `3b4b5752-37a5-421e-938f-76b85c2b499b` | tree     | `processed/1418abad.json`      | 28 nodes, depth 2                 | Consistent 3rd copy        |
| 8  | `General_Terms_of_Services...Copy.pdf`                   | `25261369` | `2f00d59f-16ac-4661-96b7-552afdc81216` | tree     | `processed/25261369.json`      | 22 nodes, depth 2, max_leaf 14.8k | Long articles legitimate   |
| 9  | `Ministerial Resolution No. (620)...Copy.pdf`            | `0d62cd91` | `411ebe25-6802-4993-905e-0a8e7f08495c` | tree     | `processed/0d62cd91.json`      | 38 nodes, depth 3                 | Clean                      |
| 10 | `NAS GN Network - September 2024 - Copy.xlsx`            | `77764cad` | `740513bf-4808-4758-9a65-7db3b86187a3` | flat     | `processed/77764cad.flat.json` | 145 blocks, flat_mixed, 1.47MB    | Spreadsheet, clean         |
| 11 | `PDF with Texture background example - Copy.pdf`         | `9239f9b4` | `d669f9c0-ecb4-4196-b1d9-fe359a131b89` | flat     | `processed/9239f9b4.flat.json` | 3 blocks, flat_prose              | Minimal, correct           |
| 12 | `cabinet_resolution_no_37...Copy.pdf`                    | `38e980f0` | `2d33de1e-77ac-484b-858c-63c0b1265f59` | tree     | `processed/38e980f0.json`      | 12 nodes, depth 2                 | Amendment, clean           |
| 13 | `wcms_660002 - Copy.pdf`                                 | `11bcd9d9` | `c8d17342-88d1-4cd2-afbe-b4e259a5b50c` | tree     | `processed/11bcd9d9.json`      | 27 nodes, depth 2                 | ILO document, clean        |

### PASS — Arabic

| #  | File                                                                                                    | doc_id       | job_id                                   | Doc Type | MinIO Path                  | Metrics                                  | Notes                                                  |
| -- | ------------------------------------------------------------------------------------------------------- | ------------ | ---------------------------------------- | -------- | --------------------------- | ---------------------------------------- | ------------------------------------------------------ |
| 14 | `اتفاقية الامم المتحدة بشأن البيع الدولي للبضائع - Copy.pdf` | `4ab2fd8f` | `66fd7c83-28c6-4490-ab28-95af2aa37adc` | tree     | `processed/4ab2fd8f.json` | 153 nodes, depth 3, max_leaf 20.4k (ToC) | 100/101 CISG articles individually split, clean Arabic |

### MARGINAL (9 files)

| # | File                                                                     | doc_id       | job_id                                   | Doc Type | MinIO Path                       | Metrics                                        | Failure Reason                                                       | What to Look For                                                                                                                                        |
| - | ------------------------------------------------------------------------ | ------------ | ---------------------------------------- | -------- | -------------------------------- | ---------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 | `FEDERAL LAW NO (3)...PENAL CODE - Copy.pdf`                           | `2030e34d` | `17434e33-9012-4a33-a4db-4356eccacabe` | tree     | `processed/2030e34d.json`      | 20 nodes, depth 2,**max_leaf 236,413**   | Tail-blob: 429 articles trapped in single leaf                       | Inline`Article (N)` markers at positions 457/458 not line-anchored — Latin regex misses parenthesized form                                           |
| 2 | `Federal Decree-Law No. (47)...Copy.pdf`                               | `14f41037` | `5e4dd60c-44ae-4f69-a2bc-ec13a990e830` | tree     | `processed/14f41037.json`      | 13 nodes, depth 2,**max_leaf 11,171**    | Tail-blob: multiple articles merged                                  | Same inline article marker issue as Penal Code                                                                                                          |
| 3 | `Ministerial Resolution No279...Copy.pdf`                              | `8eab19d7` | `9e13e8cd-9db6-4dac-8bc1-4e1dcaf17d27` | tree     | `processed/8eab19d7.json`      | 18 nodes, depth 1                              | Tab artifacts in extracted text                                      | Tab characters (`\t`) interspersed in body text                                                                                                       |
| 4 | `cabinet_resolution_no_21...(1) - Copy.pdf`                            | `1d682268` | `4055e9d5-6b79-41b3-8a4c-b7af5f8f93ab` | tree     | `processed/1d682268.json`      | 22 nodes, depth 2,**max_leaf 42,697**    | Tail-blob: Articles 5-9 + fee schedules merged into single node      | Disputed: Haiku called PASS (fee schedule), Sonnet called FAIL (found`ARTICLE` headers inside node)                                                   |
| 5 | `cabinet_resolution_no_96...Copy.pdf`                                  | `4806d4bd` | `49df611b-c414-419e-8d3e-29aa1e6ff54e` | tree     | `processed/4806d4bd.json`      | 23 nodes, depth 2,**max_leaf 21,245**    | Tail-blob: Articles 5-16 merged                                      | Same inline article marker pattern                                                                                                                      |
| 6 | `federal_decree_law_no_33...Copy.pdf`                                  | `3d0eb173` | `c33ac387-658e-4c62-8282-9227129bfc95` | tree     | `processed/3d0eb173.json`      | 93 nodes, depth 3,**max_leaf 17,311**    | Tail-blob: 3 articles merged into 17k leaf                           | English version — Arabic version (مرسوم 33) has this fixed                                                                                        |
| 7 | `world-stats-pocketbook-2023.pdf`                                      | `de697353` | `77c63345-0585-4673-b67e-2e83035be757` | flat     | `processed/de697353.flat.json` | 2602 blocks, flat_mixed, 204k chars, 20 images | Column structure degraded in per-country summary tables              | Multiple sub-fields concatenated into single header strings; headers duplicated as row[0]; data present but needs string parsing                        |
| 8 | `سياسة حوكمة و إدارة البيانات - Copy.pdf`      | `70607efb` | `8aa999fe-b5cd-4e7d-bb50-84a59919a2a4` | tree     | `processed/70607efb.json`      | 23 nodes, depth 3, max_leaf 2,423              | Arabic diacritics artifacts + RTL table corruption                   | Combining diacritics (ي, ُ, َ, ّ) appear as standalone characters; approval table name/title fields reversed                                        |
| 9 | `مرسوم بقانون اتحادي رقم (33) لسنة 2021...pdf` | `b87e897e` | `31e7aa97-7598-4401-b532-1e0dd76a6c35` | tree     | `processed/b87e897e.json`      | 125 nodes, depth 4, max_leaf 6,447             | **Tail-blob FIXED** (114k→6.4k). OCR text corruption persists | في→# substitution (~166 occurrences); garbled titles (التعريغفنات, الأضطنتاف); Articles 44-45 likely merged into المادة 43 |

### FAIL (13 files)

#### Category 1: OCR Escalation Never Fires (7 files)

These documents are scanned/image-only. The text extraction produces only `<!-- image -->` placeholders. `escalated_ocr` stays `False`, so OCR never runs. The pipeline silently persists hollow artifacts as "success" with `content_class=flat_prose`.

| # | File                                                                            | doc_id       | job_id                                   | Doc Type | MinIO Path                       | Metrics                                           | What to Look For                                       |
| - | ------------------------------------------------------------------------------- | ------------ | ---------------------------------------- | -------- | -------------------------------- | ------------------------------------------------- | ------------------------------------------------------ |
| 1 | `MOU MOHRE & Nafis...pdf`                                                     | `95cf0d76` | `e55fef8b-deed-4a7a-a5ec-ab02e56cc215` | flat     | `processed/95cf0d76.flat.json` | flat_prose, ALL blocks =`<!-- image -->`        | Scanned Arabic MOU. Zero text.`escalated_ocr=False`. |
| 2 | `image pie chart...Copy.jpg`                                                  | `6d075b7f` | `0499437c-bf1b-4ea3-925c-12dd03cae264` | flat     | `processed/6d075b7f.flat.json` | flat_prose, zero chart data                       | JPG image — only title extracted, pie chart data lost |
| 3 | `uae_numbers_english...landscape - Copy.pdf`                                  | `55410100` | `d0ae16c8-4e81-467a-921b-8a18fbe8b15c` | flat     | `processed/55410100.flat.json` | flat_prose, 7/9 image placeholders                | Infographic-style PDF with tables rendered as images   |
| 4 | `uae_numbers_english...portrait - Copy.pdf`                                   | `349799a7` | `3bf4b7d2-2a7a-48e7-8728-0cef8174830e` | flat     | `processed/349799a7.flat.json` | flat_mixed, 4/7 image placeholders                | Same doc in portrait orientation                       |
| 5 | `اتفاقية مستوى الخدمة...موقعة من الطرفين.pdf` | `b2e83c23` | `5123c3d9-25f8-4957-886a-5490766ef850` | flat     | `processed/b2e83c23.flat.json` | flat_prose, 45 blocks ALL images, 630 chars total | Scanned Arabic SLA. 100% image placeholders.           |
| 6 | `قرار مجلس الوزراء رقم (1) لسنة 2022...pdf`             | `e33a5fa5` | `195b8edc-2d8a-4e91-85bb-7f359dcc75a7` | flat     | `processed/e33a5fa5.flat.json` | flat_prose, 21 blocks ALL images, 294 chars       | Scanned Arabic Cabinet Resolution. Zero text.          |
| 7 | `قرار مجلس الوزراء رقم (106) لسنة 2022...pdf`           | `bd3ab676` | `d195d8bb-8471-4c31-93cc-46f7af819410` | flat     | `processed/bd3ab676.flat.json` | flat_prose, 15 blocks ALL images, 210 chars       | Scanned Arabic Cabinet Resolution. Zero text.          |

**Gap:** No mechanism detects that a flat document consists entirely of image placeholders. `validate_tree` only runs on tree docs. Flat docs bypass quality checks entirely. The `content_class` classifier labels 100%-image docs as `flat_prose` (misleading). Hard Rule #5 ("Never silently persist a low-quality tree") does not cover flat docs.

**What to fix:** Add an image-ratio check in the flat-doc path. If `image_placeholders / total_blocks > threshold` (e.g. 0.8), escalate to OCR or reject as `low_quality_flat`.

#### Category 2: Garble-Gate Bypass (3 files)

These documents have corrupt text layers that pass `validate_tree` because it only checks structural metrics (depth, node_count), not text integrity. The corrupt text is persisted as a "success".

| # | File                                                                            | doc_id       | job_id                                   | Doc Type | MinIO Path                       | Metrics                                         | What to Look For                                                                                                                                                                                                                                      |
| - | ------------------------------------------------------------------------------- | ------------ | ---------------------------------------- | -------- | -------------------------------- | ----------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 | `وارد رقم 597...Copy.pdf`                                              | `4f37b2e3` | `d94b2aca-fee9-4c26-a496-ff94eafc1cef` | flat     | `processed/4f37b2e3.flat.json` | flat_mixed, 2235 blocks, 38.8k chars, 45 images | **91.3% digits.** Pattern `1651001429` repeated 3,481 times. Zero Arabic characters. Numeric-junk text layer accepted without question.                                                                                                       |
| 2 | `القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf` | `b1e42755` | `668b9ae7-eee9-421c-ab6a-c451e98f4a6b` | tree     | `processed/b1e42755.json`      | 99 nodes, depth 3, max_leaf 3,426               | **Systemic mojibake.** Embedded font with missing/wrong ToUnicode CMap. Article titles render as `E<ì^¹]` instead of Arabic المادة. Body text equally garbled. Structure is correct (99 nodes, proper nesting) but zero usable text.  |
| 3 | `مرسوم بقانون اتحادي رقم (13)...Copy.pdf`                 | `b1a72fb2` | `75e65cbe-be65-4ca4-a80e-62b72aaf034e` | tree     | `processed/b1a72fb2.json`      | 14 nodes, depth 2, max_leaf 3,107               | **Latin mojibake persists.** "Oleg" instead of نهيان (ruler's name); "ee ونه الصا شن ا لأ 1 Nest" = pure OCR garbage. Only 3/22 articles present — 19 articles missing entirely. Known prior test case — mojibake NOT fixed. |

**Gap:** `validate_tree` checks `depth >= 2` and `node_count >= 3` but never inspects text content. A document with perfect hierarchy but zero readable text passes. The garble-gate needs a text-integrity check: e.g. Unicode script distribution (>90% digits = suspect), entropy analysis, or known-junk-pattern detection.

**What to fix:** Add a text-content quality check to `validate_tree`: (1) digit-ratio threshold, (2) Latin-in-Arabic detection, (3) repeated-substring detection for numeric junk patterns like `1651001429`.

#### Category 3: Tail-Blob + Encoding (2 files)

| # | File                                              | doc_id       | job_id                                   | Doc Type | MinIO Path                  | Metrics                                      | What to Look For                                                                                                                                                                                                                                                                        |
| - | ------------------------------------------------- | ------------ | ---------------------------------------- | -------- | --------------------------- | -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 | `cabinet_resolution_no_21...(1) (1) - Copy.pdf` | `144fbaaf` | `9a766acf-e746-48f0-a55d-a470319b7766` | tree     | `processed/144fbaaf.json` | 22 nodes, depth 2,**max_leaf 42,697**  | Tail-blob: Articles 5-9 + fee schedules merged.`ARTICLE` headers found embedded inside single node proving merge.                                                                                                                                                                     |
| 2 | `ﺣﻘﻮق اﻹﻧﺴﺎن - Copy.pdf`              | `ae02da49` | `a5fc6349-6793-4710-8499-ba063eef37c5` | tree     | `processed/ae02da49.json` | 34 nodes, depth 4,**max_leaf 319,975** | **Largest tail-blob in corpus (319k chars).** 90.1% of document text trapped in 2 blobs (319k + 137k ToC). Presentation-form Arabic (U+FE70-FEFF) throughout — Fix-1 Arabic regex only matches logical-form المادة (U+0600-06FF). Text IS human-readable despite encoding. |

**Gap:** The splitter's Arabic article regex uses logical-form Arabic characters (U+0600-06FF). Documents encoded in presentation-form Arabic (U+FE70-FEFF) — where ﺍﳌـﺎﺩﺓ is used instead of المادة — completely bypass article splitting. This is a distinct bug from the Latin inline-marker issue.

**What to fix:** Either (1) NFKC-normalize text before regex matching (presentation-form → logical-form), or (2) add presentation-form variants to the Arabic article regex.

---

## Systemic Gaps Identified

### Gap 1: OCR Escalation Never Fires (BLOCKER — 7 FAILs)

**Symptom:** Scanned/image-only documents produce `<!-- image -->` placeholders. `escalated_ocr` remains `False`. Zero text recovery.

**Root cause:** The OCR escalation gate (`_wire_ocr_escalation` in worker.py) checks for garbled text, not for absent text. When the PDF extractor returns only image placeholders, there's nothing garbled to detect — just nothing at all.

**Affected doc_ids:** `95cf0d76`, `6d075b7f`, `55410100`, `349799a7`, `b2e83c23`, `e33a5fa5`, `bd3ab676`

**Fix direction:** Add image-placeholder-ratio detection before the flat-doc persistence path. If `image_blocks / total_blocks > 0.8`, escalate to Tesseract OCR or reject.

### Gap 2: Garble-Gate Text Integrity (BLOCKER — 3 FAILs)

**Symptom:** Documents with corrupt text layers (numeric junk, mojibake from embedded fonts) pass `validate_tree` and persist as success.

**Root cause:** `validate_tree` only checks structural metrics: `depth >= 2`, `node_count >= 3`. It never reads the actual text content.

**Affected doc_ids:** `4f37b2e3` (91% digits), `b1e42755` (font mojibake), `b1a72fb2` (Latin mojibake)

**Fix direction:** Add text-quality heuristics to validation: digit-ratio threshold, Unicode script consistency check, repeated-substring detection.

### Gap 3: Tail-Blob Splitter — Latin Inline Markers (WARNING — 5 MARGINALs, 1 FAIL)

**Symptom:** Long documents with inline `Article (N)` markers produce massive unsplit leaf nodes (11k–236k chars).

**Root cause:** Two issues in `split_oversized_leaf_nodes`:

1. Latin regex lacks parenthesized article form — matches `Article 9` but not `Article (9)`
2. Regex is line-anchored (`^`) but article markers are inline (positions 457/458 in Penal Code)

**Affected doc_ids:** `2030e34d` (236k), `14f41037` (11k), `3d0eb173` (17k), `144fbaaf` (42k), `4806d4bd` (21k), `1d682268` (42k)

**Fix direction:** Remove line-anchor requirement; add parenthesized variant `Article\s*\(\d+\)` to Latin regex.

### Gap 4: Presentation-Form Arabic (WARNING — 1 FAIL)

**Symptom:** ﺣﻘﻮق اﻹﻧﺴﺎن has 319k tail-blob because Arabic article markers use presentation-form characters (U+FE70-FEFF) instead of logical-form (U+0600-06FF).

**Root cause:** Arabic article regex only matches logical-form المادة. Presentation-form ﺍﳌـﺎﺩﺓ is character-by-character different.

**Affected doc_ids:** `ae02da49` (319k blob)

**Fix direction:** NFKC-normalize text before regex matching, or add presentation-form variants to regex.

### Gap 5: Arabic OCR Quality (WARNING — 2 MARGINALs)

**Symptom:** Even when OCR fires, Arabic text shows character-level corruption: في→#, garbled article titles.

**Root cause:** Tessdata language selection or model quality. مرسوم 33 OCR produces ~166 في→# substitutions. مرسوم 13 has "Oleg" and "Nest" Latin artifacts in otherwise-Arabic text.

**Affected doc_ids:** `b87e897e` (في→# x166), `b1a72fb2` ("Oleg", "Nest")

**Fix direction:** Verify `.tessdata/` provisioning includes Arabic trained data (`ara.traineddata`); test with `configs/` directory present (known prerequisite from Fix-1 tessdata pre-bake).

### Gap 6: Table Column Structure (WARNING — 3 MARGINALs)

**Symptom:** Complex tables have degraded column structure — headers and values concatenated into single strings.

**Affected doc_ids:** `de697353` (World Stats), `d526b12e` (GHV-TKV-Tarif), `45c9e0b4` (Unfallversicherung)

**Fix direction:** Improve table parsing in `helpers.py` to preserve column boundaries. Low priority — data is present, just requires parsing.

---

## Positive Findings

1. **German corpus regression-free** — 0/27 fails. All text-layer PDFs extract cleanly. Flat-doc success route (RFC-004) correctly handles IPID/catalog documents.
2. **مرسوم 33 tail-blob FIXED** — max_leaf 114k → 6.4k. 72/74 articles individually split. Proves Arabic article markers work when text is logical-form Unicode.
3. **UN Convention (CISG) Arabic excellent** — 100/101 articles split, clean Unicode, proper CISG hierarchy (Part > Chapter > Section > Article). Best Arabic result in corpus.
4. **English legal documents consistent** — Decree-Law 13 produced identical results across 3 copies. No non-determinism.
5. **Pipeline stability** — Zero errors, zero timeouts, zero crashes across 62 documents. Max processing time 750.9s (World Stats Pocketbook 2023).
6. **Format diversity handled** — PDF, XLSX, JPG all accepted. XLSX (NAS GN Network, 1.47MB) processed cleanly.

---

## Full Document Reference Table

| doc_id       | Source File                                     | Corpus | job_id       | Status | content_class | MinIO Tree                  | MinIO Flat                       | Redis Key                  | Elapsed |
| ------------ | ----------------------------------------------- | ------ | ------------ | ------ | ------------- | --------------------------- | -------------------------------- | -------------------------- | ------- |
| `c7daf6a1` | AKB.pdf.pdf                                     | data   | `f2c2b224` | done   | —            | `processed/c7daf6a1.json` | —                               | `pageindex:doc:c7daf6a1` | 60.1s   |
| `416384b8` | AVB-PHV-Basis.pdf.pdf                           | data   | `09fd424e` | done   | —            | `processed/416384b8.json` | —                               | `pageindex:doc:416384b8` | 70.1s   |
| `caf26a8b` | AVB-PHV-Komfort.pdf.pdf                         | data   | `9ce99f73` | done   | —            | `processed/caf26a8b.json` | —                               | `pageindex:doc:caf26a8b` | 70.1s   |
| `fca365ae` | AVB-PHV-Premium.pdf.pdf                         | data   | `adb60b2b` | done   | —            | `processed/fca365ae.json` | —                               | `pageindex:doc:fca365ae` | 65.1s   |
| `2ddf5adf` | Downloadbereich...GHV.pdf                       | data   | `46bd6742` | done   | —            | `processed/2ddf5adf.json` | —                               | `pageindex:doc:2ddf5adf` | 25.0s   |
| `d526b12e` | GHV-TKV-Tarif.pdf                               | data   | `1e31a468` | done   | flat_mixed    | —                          | `processed/d526b12e.flat.json` | `pageindex:doc:d526b12e` | 25.0s   |
| `fb43f1a8` | Haftpflicht-Allgemeine.pdf                      | data   | `cab05ff2` | done   | —            | `processed/fb43f1a8.json` | —                               | `pageindex:doc:fb43f1a8` | 30.0s   |
| `499820a1` | Haftpflicht-Besondere.pdf                       | data   | `d79a298f` | done   | —            | `processed/499820a1.json` | —                               | `pageindex:doc:499820a1` | 45.1s   |
| `952f3324` | Hunde-Kranken.pdf                               | data   | `31c04e21` | done   | —            | —                          | `processed/952f3324.flat.json` | `pageindex:doc:952f3324` | 25.0s   |
| `681d2dcc` | Hunde-OP.pdf                                    | data   | `48dbaafb` | done   | —            | —                          | `processed/681d2dcc.flat.json` | `pageindex:doc:681d2dcc` | 25.0s   |
| `1bb886ae` | Hundehalter-Unfall.pdf                          | data   | `8246ccbc` | done   | —            | `processed/1bb886ae.json` | —                               | `pageindex:doc:1bb886ae` | 20.0s   |
| `f62ffe38` | Hundehalterhaftpflicht.pdf                      | data   | `a8b0006e` | done   | —            | `processed/f62ffe38.json` | —                               | `pageindex:doc:f62ffe38` | 35.0s   |
| `8ad9e2b6` | Hundeleben-Allgemeine.pdf                       | data   | `fd443abe` | done   | —            | `processed/8ad9e2b6.json` | —                               | `pageindex:doc:8ad9e2b6` | 25.0s   |
| `4ffb3191` | Katzen-Kranken.pdf                              | data   | `be6fe3b7` | done   | —            | —                          | `processed/4ffb3191.flat.json` | `pageindex:doc:4ffb3191` | 25.0s   |
| `45d07251` | Katzen-OP.pdf                                   | data   | `2c314fb3` | done   | —            | `processed/45d07251.json` | —                               | `pageindex:doc:45d07251` | 25.0s   |
| `23102933` | Kundeninformation.pdf                           | data   | `68fbc4d5` | done   | —            | `processed/23102933.json` | —                               | `pageindex:doc:23102933` | 25.0s   |
| `a0f71859` | Meutenversicherung.pdf                          | data   | `0c7c28ca` | done   | —            | —                          | `processed/a0f71859.flat.json` | `pageindex:doc:a0f71859` | 20.0s   |
| `c5fc32c6` | Pferde-Kranken.pdf                              | data   | `ccc2d171` | done   | —            | —                          | `processed/c5fc32c6.flat.json` | `pageindex:doc:c5fc32c6` | 30.0s   |
| `d09334b3` | Pferde-OP.pdf                                   | data   | `5f04c7b9` | done   | —            | —                          | `processed/d09334b3.flat.json` | `pageindex:doc:d09334b3` | 30.0s   |
| `25e4273c` | Pferdehalterhaftpflicht.pdf                     | data   | `8a16a359` | done   | —            | `processed/25e4273c.json` | —                               | `pageindex:doc:25e4273c` | 35.0s   |
| `02f4447f` | Reitlehrer-Bereiter-Kutsch.pdf                  | data   | `729aa976` | done   | flat_mixed    | —                          | `processed/02f4447f.flat.json` | `pageindex:doc:02f4447f` | 10.0s   |
| `2fa6b729` | Reitlehrer-Bereiter.pdf                         | data   | `8163e4b7` | done   | flat_mixed    | —                          | `processed/2fa6b729.flat.json` | `pageindex:doc:2fa6b729` | 10.0s   |
| `a6ed79d0` | Reitlehrer-Schäden.pdf                         | data   | `ec64e9bd` | done   | —            | `processed/a6ed79d0.json` | —                               | `pageindex:doc:a6ed79d0` | 20.0s   |
| `7da16bd8` | Reiter-Unfallversicherung.pdf                   | data   | `bb4386e1` | done   | flat_mixed    | —                          | `processed/7da16bd8.flat.json` | `pageindex:doc:7da16bd8` | 25.0s   |
| `367b57d2` | Tarifblatt-Privat.pdf                           | data   | `d71e3ec3` | done   | —            | `processed/367b57d2.json` | —                               | `pageindex:doc:367b57d2` | 20.0s   |
| `cbd653d5` | Tier-OP-Kranken-Allgemeine.pdf                  | data   | `e094a695` | done   | —            | `processed/cbd653d5.json` | —                               | `pageindex:doc:cbd653d5` | 25.0s   |
| `45c9e0b4` | Unfallversicherung.pdf                          | data   | `ed28c467` | done   | flat_mixed    | —                          | `processed/45c9e0b4.flat.json` | `pageindex:doc:45c9e0b4` | 25.0s   |
| `d674c5cd` | Antrag Führerschein.pdf                        | data2  | `1a07d53c` | done   | —            | `processed/d674c5cd.json` | —                               | `pageindex:doc:d674c5cd` | 20.0s   |
| `57e08886` | Amendment Service Fees.PDF                      | data2  | `3a912da2` | done   | —            | `processed/57e08886.json` | —                               | `pageindex:doc:57e08886` | 25.0s   |
| `d15c2d11` | Cabinet Res Decree-Law 33.pdf                   | data2  | `28231f63` | done   | —            | `processed/d15c2d11.json` | —                               | `pageindex:doc:d15c2d11` | 40.0s   |
| `e4b7c5e1` | Economic Activities.pdf                         | data2  | `1d057666` | done   | flat_mixed    | —                          | `processed/e4b7c5e1.flat.json` | `pageindex:doc:e4b7c5e1` | 25.0s   |
| `2030e34d` | PENAL CODE.pdf                                  | data2  | `17434e33` | done   | —            | `processed/2030e34d.json` | —                               | `pageindex:doc:2030e34d` | 10.0s   |
| `6c9e5386` | Decree-Law 13 (4).pdf                           | data2  | `63ac2da8` | done   | —            | `processed/6c9e5386.json` | —                               | `pageindex:doc:6c9e5386` | 25.0s   |
| `182b775a` | Decree-Law 13 (Copy).pdf                        | data2  | `21fc034d` | done   | —            | `processed/182b775a.json` | —                               | `pageindex:doc:182b775a` | 25.0s   |
| `14f41037` | Decree-Law 47.pdf                               | data2  | `5e4dd60c` | done   | —            | `processed/14f41037.json` | —                               | `pageindex:doc:14f41037` | 25.0s   |
| `25261369` | General Terms of Services.pdf                   | data2  | `2f00d59f` | done   | —            | `processed/25261369.json` | —                               | `pageindex:doc:25261369` | 35.0s   |
| `95cf0d76` | MOU MOHRE.pdf                                   | data2  | `e55fef8b` | done   | flat_prose    | —                          | `processed/95cf0d76.flat.json` | `pageindex:doc:95cf0d76` | 20.0s   |
| `0d62cd91` | Ministerial Res 620.pdf                         | data2  | `411ebe25` | done   | —            | `processed/0d62cd91.json` | —                               | `pageindex:doc:0d62cd91` | 30.0s   |
| `8eab19d7` | Ministerial Res 279.pdf                         | data2  | `9e13e8cd` | done   | —            | `processed/8eab19d7.json` | —                               | `pageindex:doc:8eab19d7` | 35.0s   |
| `77764cad` | NAS GN Network.xlsx                             | data2  | `740513bf` | done   | flat_mixed    | —                          | `processed/77764cad.flat.json` | `pageindex:doc:77764cad` | 40.0s   |
| `9239f9b4` | PDF Texture background.pdf                      | data2  | `d669f9c0` | done   | flat_prose    | —                          | `processed/9239f9b4.flat.json` | `pageindex:doc:9239f9b4` | 20.0s   |
| `144fbaaf` | cabinet_res_21 (1)(1).pdf                       | data2  | `9a766acf` | done   | —            | `processed/144fbaaf.json` | —                               | `pageindex:doc:144fbaaf` | 30.0s   |
| `1d682268` | cabinet_res_21 (1).pdf                          | data2  | `4055e9d5` | done   | —            | `processed/1d682268.json` | —                               | `pageindex:doc:1d682268` | 30.0s   |
| `38e980f0` | cabinet_res_37.pdf                              | data2  | `2d33de1e` | done   | —            | `processed/38e980f0.json` | —                               | `pageindex:doc:38e980f0` | 25.0s   |
| `4806d4bd` | cabinet_res_96.pdf                              | data2  | `49df611b` | done   | —            | `processed/4806d4bd.json` | —                               | `pageindex:doc:4806d4bd` | 30.0s   |
| `1418abad` | decree_law_13.pdf                               | data2  | `3b4b5752` | done   | —            | `processed/1418abad.json` | —                               | `pageindex:doc:1418abad` | 25.0s   |
| `3d0eb173` | decree_law_33.pdf                               | data2  | `c33ac387` | done   | —            | `processed/3d0eb173.json` | —                               | `pageindex:doc:3d0eb173` | 40.0s   |
| `6d075b7f` | pie chart.jpg                                   | data2  | `0499437c` | done   | flat_prose    | —                          | `processed/6d075b7f.flat.json` | `pageindex:doc:6d075b7f` | 25.0s   |
| `55410100` | uae_numbers_landscape.pdf                       | data2  | `d0ae16c8` | done   | flat_prose    | —                          | `processed/55410100.flat.json` | `pageindex:doc:55410100` | 20.0s   |
| `349799a7` | uae_numbers_portrait.pdf                        | data2  | `3bf4b7d2` | done   | flat_mixed    | —                          | `processed/349799a7.flat.json` | `pageindex:doc:349799a7` | 20.0s   |
| `11bcd9d9` | wcms_660002.pdf                                 | data2  | `c8d17342` | done   | —            | `processed/11bcd9d9.json` | —                               | `pageindex:doc:11bcd9d9` | 25.0s   |
| `de697353` | world-stats-pocketbook.pdf                      | data2  | `77c63345` | done   | flat_mixed    | —                          | `processed/de697353.flat.json` | `pageindex:doc:de697353` | 750.9s  |
| `4ab2fd8f` | اتفاقية الامم المتحدة.pdf    | data2  | `66fd7c83` | done   | —            | `processed/4ab2fd8f.json` | —                               | `pageindex:doc:4ab2fd8f` | 45.0s   |
| `b2e83c23` | اتفاقية مستوى الخدمة.pdf      | data2  | `5123c3d9` | done   | flat_prose    | —                          | `processed/b2e83c23.flat.json` | `pageindex:doc:b2e83c23` | 25.0s   |
| `b1e42755` | القرار التنظيمي.pdf               | data2  | `668b9ae7` | done   | —            | `processed/b1e42755.json` | —                               | `pageindex:doc:b1e42755` | 40.0s   |
| `70607efb` | سياسة حوكمة البيانات.pdf      | data2  | `8aa999fe` | done   | —            | `processed/70607efb.json` | —                               | `pageindex:doc:70607efb` | 30.0s   |
| `e33a5fa5` | قرار مجلس الوزراء رقم 1.pdf   | data2  | `195b8edc` | done   | flat_prose    | —                          | `processed/e33a5fa5.flat.json` | `pageindex:doc:e33a5fa5` | 20.0s   |
| `bd3ab676` | قرار مجلس الوزراء رقم 106.pdf | data2  | `d195d8bb` | done   | flat_prose    | —                          | `processed/bd3ab676.flat.json` | `pageindex:doc:bd3ab676` | 25.0s   |
| `b1a72fb2` | مرسوم 13 (Arabic).pdf                      | data2  | `75e65cbe` | done   | —            | `processed/b1a72fb2.json` | —                               | `pageindex:doc:b1a72fb2` | 10.0s   |
| `b87e897e` | مرسوم 33 (Arabic).pdf                      | data2  | `31e7aa97` | done   | —            | `processed/b87e897e.json` | —                               | `pageindex:doc:b87e897e` | 170.2s  |
| `4f37b2e3` | وارد 597.pdf                                | data2  | `d94b2aca` | done   | flat_mixed    | —                          | `processed/4f37b2e3.flat.json` | `pageindex:doc:4f37b2e3` | 50.1s   |
| `ae02da49` | ﺣﻘﻮق اﻹﻧﺴﺎن.pdf                       | data2  | `a5fc6349` | done   | —            | `processed/ae02da49.json` | —                               | `pageindex:doc:ae02da49` | 10.0s   |
