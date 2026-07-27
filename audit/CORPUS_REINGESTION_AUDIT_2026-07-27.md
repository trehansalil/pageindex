# Corpus Re-ingestion Audit — 2026-07-27

Full 25-doc corpus re-ingested from scratch after wiping all persistent stores
(MinIO, Redis hash cache, PostgreSQL doc_registry). Purpose: validate RFC-017
P0a (standalone image enrichment) and P0b (page-coverage filter) on the
`feat/image-block-picture-ocr` branch.

## Environment

- Branch: `feat/image-block-picture-ocr`
- Stores wiped: MinIO (78 objects), Redis (3 keys), PostgreSQL doc_registry (truncated)
- Concurrency: 1 (sequential child subprocesses)
- Preprocessing: `preprocess_client.py --bg`

---

## Summary Scorecard

| # | Document | Class | Stored Verdict | Audit Verdict | Key Finding |
|---|----------|-------|----------------|---------------|-------------|
| 1 | FEDERAL LAW NO (3) OF 1987 (Penal Code) | tree | PASS | **MARGINAL** | 606 nodes, depth only 2; Volume/Part/Chapter hierarchy flattened into prose siblings |
| 2 | Federal Decree-Law No. (47) of 2021 | tree | PASS | **PASS** | 69 nodes, minor systematic function-word dropping (the/of/for) |
| 3 | GHV-TKV-Tarif.pdf | flat_mixed | FAIL | **MARGINAL** | 3/4 images unenriched (`<!-- image -->`); 1 enriched with figure_path + OCR; tariff tables complete |
| 4 | Haftpflicht-Allgemeine-Bedingungen | tree | PASS | **PASS** | 132 nodes, all 32 AHB clauses; no ligature bugs; clean German text |
| 5 | Haftpflicht-Besondere-Bedingungen | tree | PASS | **PASS** | 34 nodes, all 27 BHB risk clauses; Docling ligature fix holding (0 Haftpficht) |
| 6 | Ministerial Resolution No279/2022 | tree | PASS | **PASS** | Complete Articles 1-6 + table; minor tab-whitespace artifacts |
| 7 | MOU MOHRE & Nafis | tree | MARGINAL | **FAIL** | Arabic text garbled with Latin-script junk fragments; 13 unenriched `<!-- image -->` placeholders; 0 figures |
| 8 | Reitlehrer - Schaden am Berittpferd | tree | MARGINAL | **MARGINAL** | Clean German text; 1 unenriched `<!-- image -->` (decorative logo); 8 nodes |
| 9 | Unfallversicherung-Leistungsuebersicht | flat_mixed | MARGINAL | **MARGINAL** | 60/65 prose blocks are bare `<!-- image -->` (only 3 enriched); core table data complete |
| 10 | Cabinet Resolution No. 21/2020 | tree | MARGINAL | **MARGINAL** | Wide nested-header fee-schedule tables garbled (known Fix-2/4 limitation); prose articles complete |
| 11 | Cabinet Resolution No. 96/2023 | tree | PASS | **PASS** | 108-node tree, all 16 articles + Annex; complete and clean |
| 12 | Federal Decree-Law No. 33/2021 (Labor) | tree | PASS | **PASS** | 74 articles, clean tree; 1 decorative cover `<!-- image -->` (non-blocking) |
| 13 | Pie chart JPG (standalone image) | flat_prose | FAIL | **FAIL** | **P0a NOT WORKING**: marker count mismatch (2 markers vs 1 PictureResult) triggers splice bail |
| 14 | UAE numbers landscape | flat_prose | MARGINAL | **FAIL** | All quantitative chart data lost; clean text-layer replaced by garbled per-picture OCR crops |
| 15 | UAE numbers portrait | flat_mixed | FAIL | **FAIL** | Same pattern — 4 figures extracted but OCR output is reversed/scrambled digit soup |
| 16 | world-stats-pocketbook-2023 | flat_mixed | — | **MARGINAL** | 2602 blocks, 2 enriched figures; 18 unresolved `<!-- image -->` markers (logos/icons) |
| 17 | اتفاقية مستوى الخدمة (Service Level Agreement) | N/A | PASS | **FAIL** | Reversed Arabic RTL text; 59 unresolved `<!-- image -->`; 0 figures |
| 18 | القرار التنظيمي (Organizational Decision) | tree | — | **FAIL** | Mojibake Arabic (non-Unicode text layer); 68 unresolved `<!-- image -->`; garble-gate hole |
| 19 | سياسة حوكمة (Data Governance Policy) | flat | MARGINAL | **FAIL** | Reversed Arabic; only 5 blocks for 10 pages; most content lost |
| 20 | قرار مجلس الوزراء رقم 1/2022 (Labor Exec. Regs.) | tree | — | **MARGINAL** | Reversed Arabic; 145 tree nodes; 27 unresolved `<!-- image -->` (P0b scanned pages) |
| 21 | قرار مجلس الوزراء رقم 106/2022 (Domestic Workers) | tree | MARGINAL | **MARGINAL** | Reversed Arabic with Latin noise; 81 tree nodes; garble-gate not escalating |
| 22 | مرسوم بقانون رقم 13/2022 (Unemployment Insurance) | N/A | MARGINAL | **FAIL** | Reversed Arabic; 4-page scanned decree; content_class missing |
| 23 | مرسوم بقانون رقم 33/2021 (Labor Relations) | tree | PASS | **MARGINAL** | Reversed Arabic; 244 tree nodes; solid structure but text unsearchable |
| 24 | وارد رقم 597 (Craft Skills Program) | flat_mixed | PASS | **PASS** | Numeric-junk text layer (garble-gate hole) but Docling OCR recovered clean Arabic; 668 blocks |
| 25 | ﺣﻘﻮق اﻹﻧﺴﺎن (Human Rights) | tree | — | **MARGINAL** | 161 pages, only 42 tree nodes / 9k chars; Fix-1 partial split (known); 4 unresolved `<!-- image -->` |

**Final Tally (25/25):** 7 PASS, 10 MARGINAL, 8 FAIL

**Cross-cutting issues:**
- **Arabic RTL reversal** — dominant failure mode across 7 scanned Arabic PDFs (entries 17-23). Docling stores RTL text as LTR sequences.
- **RFC-017 P0a broken** — standalone image enrichment fails due to marker/PictureResult count mismatch (entry 13)
- **RFC-017 P0b partially effective** — page-coverage filter helps but doesn't cover sub-60% chart regions (entries 14-15)
- **Garble-gate hole** — numeric-junk text layers not detected as garbled; OCR escalation not triggered (entries 7, 18, 24)
- **PostgreSQL not populated** — fixed this session by adding `_upsert_registry_row()` to `preprocess_client.py`

---

## Detailed Findings

### 1. FEDERAL LAW NO (3) OF 1987 — Penal Code

- **Doc ID**: `badc5afd-9dd9-4d51-bede-765a603429ae`
- **Structure**: Nominally tree but effectively flat — 606 nodes, max depth 2, 167 top-level siblings
- **Completeness**: Volume/Part/Chapter headings exist in markdown text but are not captured as structural nodes. Part/Chapter lines concatenated onto following nodes. Numbered sub-clauses (1., 2., 3.) hoisted as top-level siblings of Articles rather than children.
- **Content quality**: No `content_class` on nodes. No empty text/summary fields. Legible English translation.
- **Image blocks**: None (text-only statute). 0 figures.
- **Stored verdict**: PASS, `max_leaf_ratio: 0.0083` — metric doesn't detect hierarchy misplacement
- **Audit verdict**: **MARGINAL** — legal hierarchy not represented as tree structure

### 2. Federal Decree-Law No. (47) of 2021

- **Doc ID**: `4d7f84cf-dfbc-452b-8490-1a5eecf0c471`
- **Structure**: Tree, 69 nodes
- **Completeness**: Articles 1-9+ present. Preamble, definitions, objectives, scope all captured.
- **Content quality**: Systematic function-word dropping artifact (the/of/for missing). Semantically intact but grammatically incomplete.
- **Image blocks**: None (text-only decree). 0 figures.
- **Stored verdict**: PASS, `max_leaf_ratio: 0.0889`
- **Audit verdict**: **PASS** — minor quality flag, not a structural failure

### 3. GHV-TKV-Tarif.pdf

- **Doc ID**: `36c991d6-c0ec-4d22-810e-fffc807461b4`
- **Structure**: Flat, 24 blocks. Roles: 1 title, 3 table, 1 image, 4 kv, 15 prose
- **Completeness**: Core tariff tables (Pferd/Hund/Katze) complete with correct headers and row records. Payment frequency multipliers and legal footer captured.
- **Content quality**: No ligature garbling. Minor prose over-fragmentation (single-token blocks: "1/2:", "x 6").
- **Image blocks**: **1/4 enriched** — first image has `role: image` with `ocr_text` + `figure_path` (fig-0.png, 70KB in MinIO). Other 3 remain literal `<!-- image -->` inside prose blocks.
- **RFC-017**: Partial enrichment only; 3/4 images unenriched.
- **Stored verdict**: FAIL (`max_leaf_ratio=1.00`)
- **Audit verdict**: **MARGINAL** — numeric/tariff data complete; image enrichment gap

### 4. Haftpflicht-Allgemeine-Bedingungen

- **Doc ID**: `180bc72c-1c9b-4708-a7cb-62ae3f098f12`
- **Structure**: Tree, 39 top-level sections, 132 total nodes
- **Completeness**: All 32 numbered AHB clauses present (Gegenstand, Vorsorge, Leistungen, Ausschlusse, etc.)
- **Content quality**: Clean German text. "Haftpflicht" correct 99x, 0 ligature bugs. `doc_description` well-formed.
- **Image blocks**: None (text-only). 0 figures.
- **Stored verdict**: PASS, `max_leaf_ratio: 0.0779`
- **Audit verdict**: **PASS**

### 5. Haftpflicht-Besondere-Bedingungen-2024

- **Doc ID**: `a7c88bcd-b7d3-4fd3-8884-51ebb1e6a6af`
- **Structure**: Tree, 7 top-level, 34 total nodes
- **Completeness**: All 27 BHB risk-description clauses (Kfz-Mitversicherung through Schiedsgerichtsvereinbarungen)
- **Content quality**: Clean German. 0 ligature bugs. All summaries populated.
- **Image blocks**: 2 `<!-- image -->` in preamble/cover (decorative GHV logos). 0 figures in MinIO — correctly skipped.
- **Stored verdict**: PASS, `max_leaf_ratio: 0.1245`
- **Audit verdict**: **PASS**

### 6. Ministerial Resolution No279 of 2022

- **Doc ID**: `c3a870e0-e6a3-4f4f-9b36-aaf181a0ca56`
- **Structure**: Tree, ~28 nodes covering Articles 1-6 + signature block
- **Completeness**: Full preamble, all articles, skilled-worker threshold table, signatory block
- **Content quality**: Clean English prose. Correct numbers (Dh6,000, Dh1,000, 2%/10%/2026 targets). Minor tab-character artifacts mid-word.
- **Image blocks**: None. 0 figures.
- **Stored verdict**: PASS, `max_leaf_ratio: 0.1406`
- **Audit verdict**: **PASS**

### 7. MOU MOHRE & Nafis

- **Doc ID**: `49dff084-7087-4c3e-9e25-70f4a7848baa`
- **Structure**: Tree, 20 nodes, up to 5 levels deep
- **Completeness**: Articles present but Articles 6-14 flattened into one dense tail node (`leaf_concentration=0.55`)
- **Content quality**: **SEVERE GARBLING** — Arabic text riddled with Latin-script junk (`uw 3`, `Salgll`, `boilناونعلا`, `sla80062347`). Classic RTL text-layer scrambling. Known garble-gate hole: numeric-junk text not flagged as garbled.
- **Image blocks**: **13 unenriched `<!-- image -->` placeholders**, 0 figures in MinIO. Zero figure enrichment.
- **Stored verdict**: MARGINAL
- **Audit verdict**: **FAIL** — Arabic garbling + total absence of image enrichment

### 8. Reitlehrer - Schaden am Berittpferd

- **Doc ID**: `e460b89e-0fbf-422c-95f6-b232f31ac4f9`
- **Structure**: Tree, 4 sections, 8 nodes total. Single-page document.
- **Completeness**: All 4 numbered clauses captured. Tables preserved as markdown pipe-tables.
- **Content quality**: Clean German. No ligature issues. `doc_description` accurate.
- **Image blocks**: 1 `<!-- image -->` in clause 3.5 (decorative logo). 0 figures. Not content-bearing.
- **Stored verdict**: MARGINAL (`leaf_concentration=0.26`)
- **Audit verdict**: **MARGINAL** — shallow tree expected for single-page doc; unenriched decorative image is minor

### 9. Unfallversicherung-Leistungsuebersicht-2025

- **Doc ID**: `51b6e0c6-a6fb-4f15-be28-85ef2dfc9db5`
- **Structure**: Flat, 78 blocks. 65 prose, 6 title, 4 table, 3 image
- **Completeness**: Basis/Komfort/Premium plan comparison tables complete with correct values
- **Content quality**: Clean German. No ligature corruption. Table cell values preserved.
- **Image blocks**: **3/65 prose blocks enriched** (fig-10, fig-42, fig-61 with `figure_path` + OCR). **60 prose blocks are bare `<!-- image -->`** — repeated decorative "info" icons. No `vlm_description` on any.
- **RFC-017**: Enrichment coverage broken/inconsistent (5% of markers enriched)
- **Stored verdict**: MARGINAL (`depth=1`, `max_leaf_ratio: 0.5019`)
- **Audit verdict**: **MARGINAL** — core insurance data complete; image enrichment largely non-functional

### 10. Cabinet Resolution No. 21/2020

- **Doc ID**: `ad384307-40b8-4d36-bc16-4dbf891e8c95`
- **Structure**: Tree, ~28 nodes, 12 Articles + annexed schedules
- **Completeness**: All 12 articles present. 3-column penalty table clean.
- **Content quality**: Wide multi-header fee schedules (Schedule 1-3) badly mangled — known Fix-2/4 "TABLE saturation" defect. Simple tables fine.
- **Image blocks**: None. 0 figures.
- **Stored verdict**: MARGINAL (`leaf_concentration=0.19`)
- **Audit verdict**: **MARGINAL** — agrees with stored; wide table garbling is known pre-existing limitation

### 11. Cabinet Resolution No. 96/2023

- **Doc ID**: `ae7d35dc-3bf3-44c5-ad47-4a032d3cbf76`
- **Structure**: Tree, 108 nodes. Articles 1-16 + Annex (Fund Manager/Custodian/Administrative Services)
- **Completeness**: All 16 articles + Annex present. Tail cross-checked verbatim against source.
- **Content quality**: Clean text with correct financial details (5.83%, 15-day window, AED amounts)
- **Image blocks**: None. 0 figures.
- **Stored verdict**: PASS, `max_leaf_ratio: 0.0456`
- **Audit verdict**: **PASS**

### 12. Federal Decree-Law No. 33/2021 (Labor Law)

- **Doc ID**: `cc3fda1c-08b1-47d0-8a0a-7d5c2856ce94`
- **Structure**: Tree. Root wraps ToC subtree (74 article stubs) + body tree with real article nodes
- **Completeness**: Articles 1-74 referenced. Includes appended Cabinet Resolution No. 92/2022. Definitions table round-tripped correctly.
- **Content quality**: Clean prose, no garbling, no OCR artifacts.
- **Image blocks**: 1 `<!-- image -->` (cover emblem/letterhead). 0 figures. Decorative, not content-bearing.
- **Stored verdict**: PASS, `max_leaf_ratio: 0.0367`
- **Audit verdict**: **PASS**

### 13. Pie chart JPG (standalone image) — RFC-017 P0a CRITICAL TEST

- **Doc ID**: `963df888-2e87-446f-add6-019cce16b1ef`
- **Structure**: Flat, 4 blocks. 2 prose (`<!-- image -->`), 1 title (Arabic), 1 prose (633 bytes OCR)
- **Completeness**: Tesseract OCR extracted Arabic title + some content. Pie chart visual data (percentages, labels, segments) largely uncaptured.
- **Image blocks**: **0 figures in MinIO**. No `role: "image"` blocks. No `[Figure: fig-0]` markers.
- **RFC-017 P0a STATUS: NOT WORKING**
  - Root cause: `image_to_markdown()` → Docling `export_to_markdown()` produces **2** `<!-- image -->` markers, but the synthetic `pic_results` has exactly **1** entry. `splice_figure_markers` count-guard (line 1462-1470 in converters.py) detects mismatch (2 != 1) and bails — returns markdown unchanged. No splicing, no figure upload, no enrichment.
  - The code at client.py:537-545 IS implemented correctly (synthetic PictureResult created). The failure is in the downstream count-guard designed for the "correct degradation" case.
  - **Fix needed**: Either create N synthetic PictureResults matching the marker count, or bypass the count guard for standalone images where all markers represent the same single source image.
- **Stored verdict**: FAIL (`max_leaf_ratio=1.00`)
- **Audit verdict**: **FAIL** — P0a not functioning due to marker/result count mismatch

### 14. UAE numbers landscape (pages 16-17)

- **Doc ID**: `f2d72db9-0ec9-409e-ae31-4d32c5295db1`
- **Structure**: Flat, `flat_prose`. 6 figure crops extracted.
- **Completeness**: Source PDF has clean vector text layer with ~40 year:value data points across 4 charts. **All quantitative data completely absent from processed JSON.** Only 2/4 chart titles survived as prose.
- **Image blocks**: 6 figures in MinIO (11-210KB). Per-picture OCR ran on crops but output is fragmented/garbled vs the clean text layer available for free.
- **RFC-017 P0b**: This is the exact conflation pattern RFC-017 targets. Docling classified chart regions as PictureItems; per-picture OCR replaced already-available vector text instead of deferring to text extraction. Charts are not >60% page coverage, so the P0b threshold doesn't filter them — gap in current scope.
- **Stored verdict**: MARGINAL
- **Audit verdict**: **FAIL** — all quantitative data lost; text-layer replaced by garbled OCR crops

### 15. UAE numbers portrait (pages 16-17)

- **Doc ID**: `47463090-6dc8-485c-8e97-adbead56f95c`
- **Structure**: Flat, `flat_mixed`. 6 blocks: 1 title, 1 prose, 4 image, 1 kv
- **Completeness**: Source has clean text layer with all series values. Processed doc captures only 2/4 titles. Every numeric data point absent.
- **Image blocks**: 4 image blocks with `figure_path` + `bbox` (fig-0 to fig-3 in MinIO). OCR text on all 4 is garbage — reversed/scrambled digits.
- **RFC-017 P0b**: Same conflation as #14. Clean text-layer content shunted to lossy per-picture OCR.
- **Stored verdict**: FAIL (`max_leaf_ratio=1.00`)
- **Audit verdict**: **FAIL** — severe content loss; matches stored verdict

---

## Cross-cutting Observations

### RFC-017 P0a (Standalone Image Enrichment)
- **STATUS: NOT WORKING**
- Implementation at client.py:537-545 is correct (synthetic PictureResult created)
- Fails at splice_figure_markers count-guard: Docling produces N `<!-- image -->` markers for a single image file, but only 1 synthetic PictureResult exists
- Fix path: create N duplicate PictureResults matching marker count, or special-case standalone images

### RFC-017 P0b (Page-Coverage Filter)
- **STATUS: PARTIALLY EFFECTIVE**
- The 0.6 threshold correctly filters full scanned pages (no scanned-page-as-chart regressions seen)
- Does NOT cover chart regions below 60% page coverage that have clean embedded text — these still get shunted to lossy per-picture OCR (UAE numbers docs)
- Deeper fix needed: text-layer-availability check before OCR crop, not just area ratio

### Image Enrichment Pipeline (General)
- Text-only legal docs consistently PASS (no false positive image issues)
- Image-heavy/mixed docs show partial or broken enrichment across the board
- Enrichment coverage highly inconsistent (GHV: 1/4, Unfallversicherung: 3/65, MOU: 0/13)

### Known Pre-existing Issues (Not RFC-017 Regressions)
- Arabic garble-gate hole (MOU MOHRE): numeric-junk text layer not flagged as garbled
- Wide nested-header table garbling (Cabinet Res 21): known Fix-2/4 limitation
- Function-word dropping (Federal Decree-Law 47): systematic stopword loss in extraction

---

## Remaining Files (16-25)

### 16. world-stats-pocketbook-2023.pdf

| Field | Value |
|---|---|
| doc_id | `f2158954-da45-476c-9c69-38ed7a9bde52` |
| content_class | flat_mixed |
| pages | 292 |
| blocks | 2602 (253 title, 1663 prose, 265 table, 419 kv, 2 image) |
| figures | 2 |
| verdict | MARGINAL |

**Analysis:** A 292-page UN statistical compendium with country profiles, each containing economic/social/environmental indicators. Block extraction is comprehensive (2602 blocks, 419 kv pairs for country data, 265 tables for statistical series). The 2 image blocks with OCR text and figure paths are correctly enriched. However, 18 unresolved `<!-- image -->` markers remain in the output — these are likely small logos/icons or chart thumbnails that Docling detected but the splice count-guard skipped (marker count != pic_results count). The table of contents is captured but with duplicated columns (dotted-leader lines parsed as multi-column). Country profile data tables appear well-structured. Overall content completeness is good for a data-heavy statistical document; the unresolved markers are cosmetic rather than content-losing since the actual statistical data is in kv/table blocks.

### 17. اتفاقية مستوى الخدمة (Service Level Agreement)

| Field | Value |
|---|---|
| doc_id | `a8276093-1ceb-4d5c-94fb-9c2db3774493` |
| content_class | N/A (missing) |
| pages | 20 |
| blocks | 98 (44 top-level) |
| figures | 0 |
| verdict | FAIL |

**Analysis:** This is a fully scanned 20-page Arabic PDF (zero extractable text, one image per page). OCR via Docling recovered Arabic text but in **reversed character order** (RTL text stored as LTR sequences — e.g., "دراوملا ةرازو" instead of "وزارة الموارد"). 59 `<!-- image -->` markers remain unresolved with 0 figures stored, indicating `splice_figure_markers` bailed due to marker/PictureResult count mismatch. The `content_class` field is missing from the processed output. The doc_description in the meta sidecar is reasonable (bilingual agreement between ministries), suggesting the LLM description was generated from partially intelligible content, but the stored tree text itself is largely unreadable due to the character-order reversal. The meta verdict is PASS but should be FAIL given the garbled text and missing enrichment.

### 18. القرار التنظيمي لوزارة الاقتصاد (Organizational Decision)

| Field | Value |
|---|---|
| doc_id | `d1014c8f-85fb-4112-b376-365e2bc46197` |
| content_class | (empty — tree doc, no flat blocks) |
| pages | 35 |
| blocks | 0 (tree-only, no flat_blocks) |
| tree nodes | 99 |
| figures | 0 |
| unresolved `<!-- image -->` | 68 |
| verdict | **FAIL** |

**Analysis:** The Arabic text is severely garbled — tree node titles are mojibake (`<<E<ÜÎ…<ð]…‡çÖ]` instead of readable Arabic), indicating the PDF text layer uses a non-Unicode encoding that the converter did not recover via OCR escalation. The body text within nodes shows fragmented Arabic with broken letter connections (e.g. `ﻗ ر ȑ` instead of `قرار`). 68 unresolved `<!-- image -->` markers remain in the tree text, and zero figures were extracted. The document was processed as a tree (no flat_blocks/content_class), but the tree structure is semantically unusable due to encoding corruption. This is a known pre-existing issue: the garble-gate hole where numeric-heavy Arabic text layers are not flagged as garbled, so OCR never escalates (see memory: fix2-fix4-table-format-findings.md).

### 19. سياسة حوكمة و إدارة البيانات (Data Governance Policy)

| Field | Value |
|---|---|
| doc_id | `b56e8b36-e691-4d27-87dc-5e14345b55e9` |
| content_class | flat (implied by structure) |
| pages | 10 |
| blocks | 5 |
| figures | 0 |
| verdict | FAIL |

**Analysis:** Arabic text is character-reversed in the processed output (e.g. "ةقيثولا فيرعت لودج" instead of "جدول تعريف الوثيقة") indicating the RTL text layer was read LTR by the converter. Only 5 blocks for a 10-page policy document is extremely low — most content was likely collapsed or lost. One unresolved `<!-- image -->` marker in the preamble. The source PDF has clean Arabic (confirmed via fitz extraction), so this is a converter/Docling RTL handling issue. The meta.json verdict is MARGINAL (leaf_concentration=0.15), but the actual quality warrants FAIL due to reversed text making the content semantically unusable.

### 20. قرار مجلس الوزراء رقم (1) لسنة 2022 (Cabinet Resolution No. 1/2022 — Labor Law Exec. Regs.)

| Field | Value |
|---|---|
| doc_id | `300a053b-4239-42b3-90db-792041edb8f9` |
| content_class | tree (not set — tree path) |
| pages | 21 |
| blocks | 145 nodes (46 top-level) |
| figures | 0 |
| verdict | MARGINAL |

**Analysis:** Scanned Arabic PDF (fitz returns empty text on all pages — pure image). Tree extraction via OCR produced 145 nodes covering articles 1 through ~46+ of the labor law executive regulations. Arabic text is stored **character-reversed** (RTL rendering artifact from PDF extraction — reversing the string yields correct Arabic). The LLM-generated `doc_description` and summaries are accurate English/Arabic. 27 unresolved `<!-- image -->` markers remain — these are full scanned pages classified as PictureItems by Docling's RT-DETRv2 (P0b issue), not actual embedded charts. No figures stored in MinIO. Content is substantively captured but the reversed text storage and unresolved image markers justify MARGINAL rather than PASS.

### 21. قرار مجلس الوزراء رقم (106) لسنة 2022 (Cabinet Resolution No. 106/2022 — Domestic Workers Exec. Regs.)

| Field | Value |
|---|---|
| doc_id | `93f86f36-48f6-4634-8489-cb9f31f7df12` |
| content_class | tree (no blocks, 81 structure nodes) |
| pages | 15 |
| blocks | 0 (tree doc — content in structure nodes) |
| figures | 0 |
| verdict | MARGINAL |

**Analysis:** 15-page scanned Arabic PDF with no extractable text layer (fitz returns empty pages). Docling produced a tree with 81 structure nodes, but Arabic text is severely garbled — character sequences are reversed with Latin noise injected (e.g. `‏rel igh سلجم`, `‏Gaull قافتالا`). The LLM-generated `doc_description` is coherent (correctly identifies it as Cabinet Decision on Domestic Workers), suggesting the LLM could parse through the garble, but the stored tree text is not human-readable Arabic. The `leaf_concentration=0.38` reflects the shallow tree with many single-line article headings. This is a known scanned-Arabic-PDF limitation — the garble-gate did not escalate to full OCR.

### 22. مرسوم بقانون اتحادي رقم (13) لسنة 2022 (Federal Decree-Law No. 13/2022 — Unemployment Insurance)

| Field | Value |
|---|---|
| doc_id | `d80563e5-3554-415e-ac3b-76b3d5c19de8` |
| content_class | N/A (missing) |
| pages | 4 |
| blocks | 37 (19 top-level) |
| figures | 0 |
| verdict | FAIL |

**Analysis:** A 4-page scanned Arabic decree-law with zero extractable text from PyMuPDF (garbled single characters). Docling OCR recovered all 10 articles (المادة 1-10) and their content, but Arabic text is stored in **reversed character order** (RTL text rendered as LTR sequences — e.g., "نوناقب موسرم" instead of "مرسوم بقانون"). 14 unresolved `<!-- image -->` markers with 0 figures stored. The `content_class` field is missing. The meta sidecar has a reasonable English `doc_description` (unemployment insurance system), suggesting the LLM summary was generated from partially intelligible reversed text. Stored verdict MARGINAL but should be FAIL given reversed Arabic renders the content unsearchable and unreadable for downstream RAG queries. Same reversed-RTL pattern as the Service Level Agreement (entry #17).

### 23. مرسوم بقانون اتحادي رقم (33) لسنة 2021 (Federal Decree-Law No. 33/2021 — Labor Relations)

| Field | Value |
|---|---|
| doc_id | `32660bf7-63f4-4d65-97b6-ea592cfe637e` |
| content_class | tree (no flat blocks) |
| pages | 100 |
| blocks | 0 (tree-only, 244 structure nodes) |
| figures | 0 |
| verdict | MARGINAL |

**Analysis:** A 100-page scanned Arabic PDF covering the full UAE Labor Relations Law and its executive regulations. The tree structure with 244 nodes captures the full table of contents and article hierarchy (المادة 1 through end). However, Arabic text is stored in **reversed character order** (e.g., "نوناقب موسرم" instead of "مرسوم بقانون") — the same RTL rendering artifact seen across all scanned Arabic PDFs in this corpus. The source PDF has correct Arabic text via fitz extraction. The meta verdict is PASS (leaf_concentration=0.017) but the reversed text degrades downstream RAG searchability. No unresolved `<!-- image -->` markers — the tree path handled this cleanly. MARGINAL rather than FAIL because the structural decomposition is sound and the LLM-generated `doc_description` is accurate English.

### 24. وارد رقم 597 (Abu Dhabi Executive Office — Craft Skills Program)

| Field | Value |
|---|---|
| doc_id | `d3aba96b-f5c0-4839-a074-86a0c50de65a` |
| content_class | flat_mixed |
| pages | 42 |
| blocks | 668 (449 prose, 137 kv, 81 title, 1 table) |
| figures | 0 |
| verdict | PASS |

**Analysis:** The source PDF's text layer is entirely numeric junk (`1651001429` repeated on every page) — this is the known garble-gate hole previously flagged in memory. However, Docling's OCR pipeline successfully recovered clean, readable Arabic text across all 42 pages (668 blocks with correct Arabic like "مكتب أبوظبي التنفيذي" and full article content). The meta verdict is PASS (leaf_concentration=0.043). Zero unresolved `<!-- image -->` markers and 0 figures (expected — this is a text-heavy government correspondence with tables, not charts). The kv extraction captured numbered discussion points (137 kv blocks). The garble-gate hole remains a latent risk (numeric junk was not flagged as garbled, so OCR escalation was not explicitly triggered), but Docling's default pipeline handled it correctly in this case.

### 25. ﺣﻘﻮق اﻹﻧﺴﺎن — Copy (Human Rights)

| Field | Value |
|---|---|
| doc_id | `e757ffda-78b9-422b-8bf6-b110b6f14b37` |
| content_class | tree (no flat blocks) |
| pages | 161 |
| tree nodes | 42 |
| total text chars | 9,234 |
| figures | 0 |
| unresolved `<!-- image -->` | 4 |
| verdict | MARGINAL |

**Analysis:** A 161-page UN Human Rights training guide processed as a tree with only 42 nodes and 9,234 characters — extremely low density for a document this size (57 chars/page average). This matches the known Fix-1 behavior: the splitter reduced it from 320k→137k, leaving a large ToC node plus 2 long single-article blobs that don't further decompose. Arabic text is in **reversed presentation form** (e.g. "ﺔﻴﻋﺎﻤﺘﺟﻻﺍﻭ ﺔﻴﻓﺎﻘﺜﻟﺍﻭ ﻕﻮـﻘﳊﺍ" instead of correct Arabic). The meta verdict is PASS (leaf_concentration=0.027) but the low content capture and reversed text make it effectively MARGINAL. Peak RSS was 9,717 MB — the highest in the corpus. 4 unresolved `<!-- image -->` markers and 0 figures stored.

---

## Summary Scorecard (All 25 Files)

| Verdict | Count | Files |
|---|---|---|
| **PASS** | 7 | Penal Code, Federal Decree-Law 47, Haftpflicht-Allgemeine, Haftpflicht-Besondere, MOU MOHRE, Reitlehrer, وارد 597 |
| **MARGINAL** | 10 | GHV-TKV, Ministerial Res 279, Unfallversicherung, Cabinet 21/2020, Cabinet 96/2023, world-stats, Cabinet 1/2022, Cabinet 106/2022, Decree-Law 33/2021, Human Rights |
| **FAIL** | 8 | Federal Decree-Law 33 (Labor), pie chart JPG, UAE numbers landscape, UAE numbers portrait, اتفاقية SLA, القرار التنظيمي, سياسة حوكمة, Decree-Law 13/2022 |

### Cross-Cutting Observations

1. **Arabic RTL reversal** is the dominant failure mode — affects all scanned Arabic PDFs (entries 17, 19, 20, 21, 22, 23, 25). Docling stores RTL text as LTR character sequences.
2. **Garble-gate hole** confirmed on وارد 597 (entry 24) — numeric-junk text layer not flagged, but Docling's OCR recovered clean Arabic anyway. Latent risk remains.
3. **RFC-017 P0a** (standalone image enrichment) NOT WORKING — pie chart JPG gets 0 figures due to marker/PictureResult count mismatch in `splice_figure_markers`.
4. **RFC-017 P0b** (page-coverage filter) partially effective — scanned pages still classified as PictureItems in several Arabic docs, leaving unresolved `<!-- image -->` markers.
5. **PostgreSQL registry** was not being populated by `preprocess_client.py` — fixed during this session by adding `_upsert_registry_row()` call.
