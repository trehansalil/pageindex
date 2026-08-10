<!-- Space: CITRA -->
<!-- Title: Corpus Re-ingestion Audit — Run 18 -->
<!-- Folder: Audits -->

# Corpus Re-ingestion Audit — Run 18

## Environment

- Branch: feat/pdf-inspector-shadow-pilot
- Date: 2026-08-09
- Prior run: audit/CORPUS_REINGESTION_AUDIT_RUN-17.md
- Methodology: Incremental ingest+score pipeline (each doc scored immediately after processing)

---

## Pre-publish verification (RFC-025 D4)

Before publication, every per-document verdict/char/node figure below was re-pulled from the live MinIO store (`processed/{doc_id}.meta.json` plus `processed/{doc_id}.json` / `.flat.json`, 23 artifacts, processed 2026-08-09 11:50–12:03 UTC). Seven dispatched figures diverged from live state and were re-derived from the actual store before writing:

| Doc | Dispatched figure | Live-store figure (used below) |
|---|---|---|
| القرار التنظيمي لوزارة الاقتصاد1 (2) | 52K chars | 48,457 chars (`total_tree_chars`) |
| ﺣﻘﻮق اﻹﻧﺴﺎن | 419k chars | 420,100 chars (`total_tree_chars`) |
| cabinet_resolution_no_96_of_2023 | depth 4 | depth 3 (measured from tree) |
| Haftpflicht-Allgemeine-Bedingungen | 76k chars | 77,024 chars (`total_tree_chars`) |
| image pie chart … .jpg | 536 chars | 489 chars (`flat_char_count`) |
| Reitlehrer - Schäden am Berittpferd | 9 nodes | 10 nodes (measured from tree) |
| uae_numbers_english_page_16_17_landscape | 872 chars | 748 chars (`flat_char_count`, byte-identical to prior runs) |

`وارد رقم 597` has no stored artifacts (confirmed absent from `processed/`), consistent with its gate-rejection ERROR. `world-stats-pocketbook-2023.pdf` is also absent from the store (see Stalls).

---

## Summary Scorecard

| # | Document | Doc Class | Verdict | Key Finding |
|---|---|---|---|---|
| 1 | اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf | unknown | MARGINAL | Depth-1 flat tree is structurally inadequate for an SLA/legal document that should have deep article/clause hierarchy; content volume (27,452 chars, 225 nodes) appears intact. |
| 2 | سياسة حوكمة و إدارة البيانات - Copy.pdf | governance-policy | FAIL | Persistent RTL reversal and Arabic single-letter fragment garbling across runs 10-15 (79-100% of nodes affected) renders tree unusable for RAG; automated gate blind spot stores PASS despite repeated judge-side FAIL. |
| 3 | قرار مجلس الوزراء رقم (1) لسنة 2022 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل.pdf | flat_mixed | MARGINAL | Depth-0 flat tree for a deeply hierarchical UAE labor law executive regulation; content present (38,323 chars, 0 garble) but structural hierarchy completely lost |
| 4 | قرار مجلس الوزراء رقم (106) لسنة 2022 بشأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (9) لسنة 2022 بشأن عمال الخدمة المساعدة.pdf | flat_mixed | MARGINAL | Legal regulation document is completely flat (depth 1, 0 markers) despite having 158 nodes; article/chapter hierarchy not captured, content extraction intact at 25,195 chars. |
| 5 | مرسوم بقانون اتحادي رقم (13) لسنة 2022 بشان التأمين ضد التعطل عن العمل - Copy.pdf | unknown | MARGINAL | Short 4-page/8-article UAE decree content fully captured with correct Arabic logical order and 0 garble, but Articles 3/4/5 are mis-nested under Article 2 — a hierarchy defect that degrades legal-document queryability. |
| 6 | مرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل وتعديلاته.pdf | unknown | MARGINAL | 65 of 74 articles (11-74) mis-nested under single shallow parent 'المادة (15)' breaks hierarchical navigation; content itself is intact at 106,547 chars with 0 garbled blocks |
| 7 | القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf | arabic_regulatory_document | PASS | Healthy Arabic regulatory tree (66 nodes, depth 4, 48,457 chars, 0 garble) but 0 structural markers detected — marker regexes may lack Arabic patterns (مادة/فصل/باب). |
| 8 | ﺣﻘﻮق اﻹﻧﺴﺎن - Copy.pdf | unknown | PASS | 352-node depth-5 tree with 420k chars is well-structured for an Arabic Human Rights legal document; single garbled block is a confirmed false positive on Latin-heuristic substring. |
| 9 | وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات حكومة أبوظبي حول برنامج مهارات المهن الحرفية - Copy.pdf | unknown | ERROR | Processing failed: low_quality_tree: rtl_reversal - Arabic document rejected due to RTL text reversal in tree structure. Tree quality gate validation failed before indexing. |
| 10 | cabinet_resolution_no_21_of_2020_concerning_service_fees_and_administrative_fines_in_the_ministry_of_human_resources_and_emiratisation (1) - Copy.pdf | legal_regulation | MARGINAL | Fee/fine schedule tables (Schedules 1-5) have flattened multi-row headers with duplicated column labels, degrading queryability of this document's core tabular content. |
| 11 | cabinet_resolution_no_96_of_2023_regarding_an_alternative_end_of_service_benefits_system - Copy.pdf | unknown | PASS | 16-page UAE Cabinet Resolution (English translation) with 108 nodes at depth 3; clean extraction, no garble, ~2690 chars/page well within expected range for legal text. |
| 12 | Federal Decree-Law No. (47) of 2021 - Copy.pdf | legal decree-law / labor law statute, English | FAIL | Over-segmentation collapsed 13-page legal statute into flat heading-only fragments (88% of nodes are body-less headings, depth=2, ~1027 chars/page vs expected ~2000+), with 40% chars discrepancy between meta.json (22,186) and actual tree content (13,351). |
| 13 | FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE  - Copy.pdf | unknown | MARGINAL | Flat tree (depth=2, 493 top-level nodes) collapses the Penal Code's Book/Part/Chapter/Article hierarchy into sibling nodes, losing structural semantics despite full content extraction (219,456 body chars, 0 garble). |
| 14 | federal_decree_law_no_33_of_2021_regarding_the_regulation_of_employment_relationship_and_its_amendments - Copy.pdf | legal_statute | PASS | Well-structured legal statute with 505 nodes, no garbling, and reasonable depth; 7 unenriched image markers are decorative and chars discrepancy is aggregation-basis difference, not content loss. |
| 15 | GHV-TKV-Tarif.pdf | insurance_tarif | MARGINAL | Small pet insurance tariff (Pferd/Hund/Katze) with reasonable 10-node/depth-3 tree but 3/4 image markers unenriched and minor leaf imbalance across animal branches |
| 16 | Haftpflicht-Allgemeine-Bedingungen.pdf.pdf | unknown | MARGINAL | Depth 2 is too shallow for a deeply structured German insurance legal document; hierarchy not adequately captured despite good content volume (77,024 chars, 136 nodes). |
| 17 | Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf | unknown | PASS | Good content extraction (3,636 chars/page) with no garbling; depth 3 is shallow for a legal document but structure is adequate with 76 nodes across 38 pages. |
| 18 | image pie chart about labor distribution in january 2025 - Copy.jpg | image_standalone | MARGINAL | Pie chart image captured via OCR (489 chars) but lacks PictureResult chart enrichment, zero bounding box, and truncated numeric labels — raw text partially usable but semantic chart structure lost. |
| 19 | Ministerial Resolution No279 of 2022 Monitoring Mechanisms of Emiratisation Rates in the Private Sec - Copy.pdf | document | PASS | Short ministerial resolution (28 nodes, 11,194 chars, depth 2) with clean extraction; tab-spacing artifact is cosmetic only and does not affect usability. |
| 20 | MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf | flat_mixed | PASS | Bilingual Arabic/English MOU with 13,440 chars across 137 flat nodes; content recovered to run-12 baseline after fence-stripping regression fix; no garbling detected. |
| 21 | Reitlehrer - Schäden am Berittpferd.pdf | unknown | MARGINAL | Single-page German addendum with reasonable tree (10 nodes, depth 2, 3,260 chars) but 1 unenriched image marker (likely logo) and missing content_class metadata |
| 22 | uae_numbers_english_page_16_17_landscape - Copy.pdf | unknown | MARGINAL | 748 chars across 2 landscape pages (~374/page) indicates likely content loss from tabular/numerical data extraction failure |
| 23 | uae_numbers_english_page_16_17_portrait - Copy.pdf | flat_mixed | PASS | Chart-heavy 2-page infographic with 4 economic sector charts correctly captured as flat_mixed; 4 picture results cover all chart areas, low prose char count (764) is structurally expected for this visual document type. |
| 24 | Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf | German accident-insurance benefit comparison leaflet | FAIL | Benefit-comparison table checkmark icons entirely lost (63 image markers, 3 identical boilerplate enrichments), making tier inclusion/exclusion data — the document's core content — unrecoverable from the tree. |

**Run 18 Tally (24/24 audited):** 8 PASS, 12 MARGINAL, 3 FAIL, 1 ERROR

---

## Delta from Prior Run -> Run 18

### Improvements

- **قرار مجلس الوزراء رقم (106) لسنة 2022 بشأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (9) لسنة 2022 بشأن عمال الخدمة المساعدة.pdf** — FAIL -> MARGINAL: Run 16 found 40% Latin-mojibake nodes with a total garble-gate miss (0 detected) and noise enrichments. Run 18 reports 0 markers/legal regulation flat but content intact at 25,195 chars with no garble complaint — the mojibake defect appears resolved, leaving only the pre-existing flat-hierarchy issue.
- **مرسوم بقانون اتحادي رقم (13) لسنة 2022 بشان التأمين ضد التعطل عن العمل - Copy.pdf** — FAIL -> MARGINAL: Run 16 found 36% Latin-letter OCR garbage embedded in Arabic text and a garbled ratifying-authority name. Run 18 reports 0 garble and correct Arabic logical order across all 4 pages/8 articles; only remaining defect is Articles 3/4/5 mis-nested under Article 2 (a hierarchy issue, not a content-quality one).
- **القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf** — MARGINAL -> PASS: Run 16 flagged 49% of image markers (34/69) unenriched. Run 18 reports a healthy 66-node/depth-4/48,457-char tree with 0 garble; the image-enrichment gap is no longer the blocking issue (only a marker-regex/structural-tagging nit remains).
- **cabinet_resolution_no_96_of_2023_regarding_an_alternative_end_of_service_benefits_system - Copy.pdf** — ERROR -> PASS: Run 16's persistence-timing race (artifacts not yet visible in MinIO at scoring time) is resolved in Run 18 — 108 nodes/depth 3/43,043 chars, clean extraction, no garble, consistent with the live-verified shape already observed at Run 16 publish time.
- **image pie chart about labor distribution in january 2025 - Copy.jpg** — FAIL -> MARGINAL: Run 16 found the enrichment route had replaced real chart-digit OCR with boilerplate placeholder text (complete content loss). Run 18 shows 489 chars of OCR'd pie-chart text recovered, though PictureResult chart enrichment, bounding boxes, and full numeric labels are still missing.
- **MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf** — MARGINAL -> PASS: Run 18 explicitly states content was recovered to the run-12 baseline after a fence-stripping regression fix — 13,440 chars/137 nodes with no garbling, resolving Run 16's under-segmentation and garbled-Latin-OCR-remnant findings.

### Structural improvements

- **اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf** — Verdict held at MARGINAL, but node count more than doubled (99 nodes in Run 16 -> 225 nodes in Run 18) with content volume intact (~27-32k chars in both runs) — a granularity improvement, though the depth-1/depth-0 flatness that keeps it MARGINAL was not resolved and Run 18's key finding actually describes a depth-1 (flatter) tree than Run 16's depth-3, so this should be read as a mixed signal (better segmentation, no better hierarchy) rather than an unqualified structural win.

### Regressions

- **cabinet_resolution_no_21_of_2020_concerning_service_fees_and_administrative_fines_in_the_ministry_of_human_resources_and_emiratisation (1) - Copy.pdf** — PASS -> MARGINAL: Run 18 finds fee/fine schedule tables (Schedules 1-5) now have flattened multi-row headers with duplicated column labels, degrading queryability of the document's core tabular content — Run 16 reported this same doc as a clean 45-node/depth-3/20,721-char extraction with no such defect.
- **Federal Decree-Law No. (47) of 2021 - Copy.pdf** — MARGINAL -> FAIL: Run 16 flagged a flat depth-2 tree (65/70 top-level) as the sole defect with clean text. Run 18 finds severe over-segmentation into heading-only fragments (88% of nodes are body-less headings) plus a 40% chars discrepancy between meta.json and the actual tree content — a genuine content-loss regression on top of the pre-existing flatness.
- **Haftpflicht-Allgemeine-Bedingungen.pdf.pdf** — PASS -> MARGINAL: Run 16's PASS was itself flagged as a judge-severity reclassification (watermark false-positive on the sole garbled block) rather than a structural fix — the underlying depth-2 tree was never actually deepened. Run 18 re-flags that same depth-2 shallowness as MARGINAL-worthy for this deeply structured German insurance document.
- **Reitlehrer - Schäden am Berittpferd.pdf** — PASS -> MARGINAL: Run 16 scored this single-page German addendum PASS with only a non-critical logo image marker unenriched (10 nodes, byte-stable chars). Run 18 downgrades to MARGINAL for the same unenriched image marker plus a newly noted 'missing content_class metadata' field (confirmed absent in the live meta.json).
- **Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf** — MARGINAL -> FAIL: Run 16 already flagged 60/63 (95%) unenriched image markers for this checkmark-based benefit-comparison table. Run 18 finds the situation worse: all 63 image markers unenriched with only 3 identical boilerplate enrichments, making the tier inclusion/exclusion data — the document's core content — entirely unrecoverable.
- **وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات حكومة أبوظبي حول برنامج مهارات المهن الحرفية - Copy.pdf** — MARGINAL -> ERROR: Run 16 scored this MARGINAL against a known garble-gate blind spot (garbled_blocks=0 despite a visibly garbled text layer) that let the doc through with a stored PASS/judged MARGINAL split. Run 18 shows the document now fails outright at ingestion: low_quality_tree / rtl_reversal, rejected by the tree-quality gate before indexing.

### Regressions requiring investigation

| Document | Delta | Hypothesis |
|---|---|---|
| cabinet_resolution_no_21_of_2020… (1) - Copy.pdf | PASS -> MARGINAL | A table-repair/header-flattening regression in the multi-row-header handling path (recent table-separator work, per commit c62ef80 'add pre-redeploy baseline probe for processed JSON files') likely altered how nested schedule headers are collapsed for this specific fee-table-heavy document. |
| Federal Decree-Law No. (47) of 2021 - Copy.pdf | MARGINAL -> FAIL | A splitter change between Run 16 and Run 18 appears to have started stripping body text from headings for this document, consistent with the RFC-034 D16/D17 ToC-strip-guard and bilingual-block-merge changes touching splitting/merging logic; the meta-vs-tree char mismatch (22,186 vs 13,351, live-verified) points to a write/aggregation-path defect rather than pure OCR quality. |
| Haftpflicht-Allgemeine-Bedingungen.pdf.pdf | PASS -> MARGINAL | Not a new pipeline defect — most likely a reversion to stricter depth-adequacy scoring criteria between runs, re-surfacing a structural gap that Run 16's PASS had merely reclassified rather than fixed. |
| Reitlehrer - Schäden am Berittpferd.pdf | PASS -> MARGINAL | A metadata regression (content_class field dropped/unpopulated) appears to have been introduced between runs and is now being scored against, rather than any content-extraction change — node count (10 in both runs, live-verified) and chars (3,260 vs 2,768 depending on aggregation basis) are close enough to rule out a content-loss cause. |
| Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf | MARGINAL -> FAIL | This matches the same enrichment-route defect diagnosed for the pie-chart image in Run 16 (boilerplate placeholder text replacing real OCR/enrichment content) — likely a shared enrichment-promotion or PictureResult pipeline regression affecting image-heavy/icon-based documents broadly, worsening here rather than being fixed as it was for the pie chart. |
| وارد رقم 597 … - Copy.pdf | MARGINAL -> ERROR | The gate appears to have been tightened for RTL-reversal detection (consistent with D16-D21 hardening work in RFC-034), and for this specific document the tightened gate now correctly rejects content it previously let through — turning a silent-garble MARGINAL into a hard, blocking ERROR. This may be gate correctness catching up with the doc's genuine RTL corruption rather than a new regression, but it removes the document from the corpus entirely and needs confirmation the rejection is not itself a false positive. |

### Stalls

- **سياسة حوكمة و إدارة البيانات - Copy.pdf** — FAIL: Persistent RTL reversal and Arabic single-letter fragment garbling (79-100% of nodes) continues unresolved; node/char counts nearly identical to Run 16 (24 nodes/18,287 ttc -> 24 nodes/18,185 chars). Automated gate PASS/judge FAIL divergence also persists (live stored verdict is PASS).
- **قرار مجلس الوزراء رقم (1) لسنة 2022 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل.pdf** — MARGINAL: Flat hierarchy for a multi-chapter legal document persists (Run 16 depth-2/114/149 top-level; Run 18 reports depth-0 flat — structural flatness did not improve and may have worsened). Node count grew 149->317 but the core structural-collapse complaint is unchanged.
- **مرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل وتعديلاته.pdf** — MARGINAL: Article mis-nesting persists across runs: Run 16 found effective depth-3 shallowness for a 100-page decree; Run 18 finds 65 of 74 articles (11-74) mis-nested under a single shallow parent. Node/char counts are close (241/103,694 -> 234/106,547), content intact, hierarchy defect unresolved.
- **FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE  - Copy.pdf** — MARGINAL: Same depth-2 flat-tree defect in both runs (Run 16: 493/595 top-level; Run 18: same 595-node depth-2 tree), collapsing Book/Part/Chapter/Article hierarchy despite full content extraction in both runs (246,652 ttc vs 219,456 body chars, 0 garble).
- **GHV-TKV-Tarif.pdf** — MARGINAL: Same 10-node tree with borderline leaf balance and unenriched decorative image markers in both runs; chars essentially flat (6,022 body chars in both runs).
- **uae_numbers_english_page_16_17_landscape - Copy.pdf** — MARGINAL: Chart/tabular content-loss concern persists across runs (Run 16: semantically scrambled OCR losing year-to-value relationships, 748 chars byte-identical across 4 prior runs; Run 18: 748 chars across 2 pages, still byte-identical and still flagged as likely content loss from tabular extraction failure).
- **world-stats-pocketbook-2023.pdf** — MISSING (was PASS in Run 16): Document is absent from the Run 18 score set entirely — not audited this run, so no verdict delta can be computed. Confirmed absent from the live `processed/` store at publish time. Given Run 15/16's history of persistence-timing races and flat-vs-tree reshaping for this exact doc, its absence should be investigated rather than assumed benign before Run 19.

### Stable

- ﺣﻘﻮق اﻹﻧﺴﺎن - Copy.pdf (PASS -> PASS)
- federal_decree_law_no_33_of_2021_regarding_the_regulation_of_employment_relationship_and_its_amendments - Copy.pdf (PASS -> PASS)
- Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf (PASS -> PASS)
- Ministerial Resolution No279 of 2022 Monitoring Mechanisms of Emiratisation Rates in the Private Sec - Copy.pdf (PASS -> PASS)
- uae_numbers_english_page_16_17_portrait - Copy.pdf (PASS -> PASS)
