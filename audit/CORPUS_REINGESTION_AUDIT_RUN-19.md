<!-- Space: CITRA -->
<!-- Title: Corpus Re-ingestion Audit — Run 19 -->
<!-- Folder: Audits -->

# Corpus Re-ingestion Audit — Run 19

## Environment

- Branch: feat/pdf-inspector-shadow-pilot
- Date: 2026-08-10
- Prior run: /Users/saliltrehan/Documents/Python_n_R/Personal/pageindex/audit/CORPUS_REINGESTION_AUDIT_RUN-18.md
- Methodology: Incremental ingest+score pipeline (each doc scored immediately after processing)

---

## Pre-publish verification (RFC-025 D4)

Before publication, every per-document verdict/char/node figure below was re-pulled from the live MinIO store (`processed/{doc_id}.meta.json` plus `processed/{doc_id}.json` / `.flat.json`; 23 meta artifacts, processed 2026-08-10 06:26–06:31 UTC). The following dispatched figures diverged from live state and were re-derived from the actual store before writing:

| Doc | Dispatched figure | Live-store figure (used below) |
|---|---|---|
| Haftpflicht-Besondere-Bedingungen-2024-001_01 | 133k chars | 138,175 chars (`total_tree_chars`) |
| cabinet_resolution_no_21_of_2020 | depth 1, 37 sibling nodes | 45 nodes, depth 3 (measured from tree) |
| cabinet_resolution_no_96_of_2023 | 26k chars, depth 2 | 43,043 chars (`total_tree_chars`), depth 3 (measured) |
| القرار التنظيمي لوزارة الاقتصاد1 (2) | 47k chars | 48,387 chars (`total_tree_chars`) |
| مرسوم بقانون اتحادي رقم (33) لسنة 2021 | depth 2; 63 nodes (delta section) | 234 nodes, depth 4, 106,230 chars (measured from tree) |
| ﺣﻘﻮق اﻹﻧﺴﺎن | 394k chars | 420,100 chars (`total_tree_chars`) |
| uae_numbers_english_page_16_17_landscape | 71 kv singletons | 78 flat blocks live (71 singletons plausible; 748 chars confirmed) |

**Material divergence — اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد:** the dispatched ERROR verdict states no artifacts exist (`processed/pending.meta.json` NoSuchKey on all retries). Live MinIO now contains `processed/d58be46f-8bd4-4cda-a166-809e92be66fa.meta.json` + `.flat.json` (processed_at 2026-08-10T06:31:28Z — the latest timestamp in the store, several minutes after every other doc), 28,202 chars, `flat_mixed`, stored verdict PASS. The artifact evidently landed **after** the scoring pass ran. The ERROR verdict is retained below as the score-time truth, but this document should be re-scored in Run 20 — see the Regressions note.

Confirmed absent from `processed/` (full inventory scan): `world-stats-pocketbook-2023.pdf` and `وارد رقم 597 …` — consistent with their ERROR verdicts.

---

## Summary Scorecard

| # | Document | Doc Class | Verdict | Key Finding |
|---|---|---|---|---|
| 1 | FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE  - Copy.pdf | unknown | MARGINAL | Depth 2 is far too shallow for a penal code with 595 nodes; legal hierarchy (Book/Chapter/Section/Article) collapsed to near-flat structure despite good content volume (246k chars). |
| 2 | Federal Decree-Law No. (47) of 2021 - Copy.pdf | legal/statute | MARGINAL | Out-of-order leave sub-clauses (nodes 64-69 displaced after Article 13 instead of under Article 9) and shallow depth-2 hierarchy for a 13-article statute degrade structural fidelity but content is present. |
| 3 | GHV-TKV-Tarif.pdf | tariff_rate_sheet | MARGINAL | Single-page rate table with adequate char extraction (6720) but 3/4 image markers are bare decorative icons (animal silhouettes/logo) without enrichment — no real content loss. |
| 4 | Haftpflicht-Allgemeine-Bedingungen.pdf.pdf | unknown | MARGINAL | Depth 2 is too shallow for a German insurance general-conditions document; legal clause hierarchy is flattened despite adequate node count and character coverage. |
| 5 | Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf | unknown | PASS | German liability special conditions document with solid structure (76 nodes, depth 3) and substantial content (138,175 chars); no quality issues detected. |
| 6 | MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf | unknown | PASS | Bilingual Arabic/English MOU recovered to baseline (~13.5k chars, 140 nodes); flat tree appropriate for MOU document type; no garbling detected. |
| 7 | Ministerial Resolution No279 of 2022 Monitoring Mechanisms of Emiratisation Rates in the Private Sec - Copy.pdf | unclassified | PASS | Well-structured short legal resolution with 28 nodes at depth 2; no content loss or garbling; cosmetic tab-in-headings artifact is non-impactful. |
| 8 | Reitlehrer - Schäden am Berittpferd.pdf | german_insurance_tc | PASS | Single-page German liability insurance T&C for riding instructors; 3260 chars and 10 nodes faithfully capture all 5 sections with correct structure and no content loss. |
| 9 | Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf | flat_mixed | MARGINAL | Table-dominant benefits overview with acceptable flat structure but 95% unenriched image markers (63 markers vs 3 enrichments); empty table cells are intentional plan-tier structure, not data loss. |
| 10 | cabinet_resolution_no_21_of_2020_concerning_service_fees_and_administrative_fines_in_the_ministry_of_human_resources_and_emiratisation (1) - Copy.pdf | legal_regulation | MARGINAL | Shallow tree collapses Article/sub-clause hierarchy of a multi-article legal resolution with fee/fine schedule tables, degrading structural queryability despite full content extraction (20,721 chars, 0 garble). [Live store shows 45 nodes at depth 3, not the dispatched depth-1/37-node flat tree — flatness severity not reproduced against live state; verdict retained pending re-score.] |
| 11 | cabinet_resolution_no_96_of_2023_regarding_an_alternative_end_of_service_benefits_system - Copy.pdf | legal | PASS | 16-page UAE Cabinet Resolution (English translation) with 11+ articles cleanly extracted; 108 nodes at depth 3 is reasonable for article-level hierarchy, 43,043 chars (~2690/page) within expected range, no content loss or garbling. |
| 12 | federal_decree_law_no_33_of_2021_regarding_the_regulation_of_employment_relationship_and_its_amendments - Copy.pdf | unknown | PASS | Well-structured UAE employment law extraction: 505 nodes, depth 4, 179K chars, zero garble; 7 decorative image markers without enrichment are acceptable for legal stamps/seals. |
| 13 | image pie chart about labor distribution in january 2025 - Copy.jpg | image_standalone | MARGINAL | OCR garbling corrupts key data points (truncated numerals, Arabic misreads like ذكق for ذكور) but chart structure and general meaning are recoverable. |
| 14 | uae_numbers_english_page_16_17_landscape - Copy.pdf | flat_mixed | FAIL | Chart data fragmented into 71 unusable kv singletons (of 78 flat blocks live) with scrambled OCR read-order and phantom enrichment promotion claim unsupported by actual content. |
| 15 | uae_numbers_english_page_16_17_portrait - Copy.pdf | flat_mixed | MARGINAL | 89% singleton kv fragmentation (71/80 blocks are bare chart axis labels/values) yields captured but poorly structured output for this chart-heavy 2-page doc |
| 16 | world-stats-pocketbook-2023.pdf | unknown | ERROR | No processed artifacts exist in MinIO for this document. meta.json (processed/pending.meta.json) and tree.json returned NoSuchKey on all attempts (3 retries with 5s backoff, as required). A full inventory scan of the processed/ prefix contains no entry for "world-stats-pocketbook-2023.pdf" or any filename matching "world" or "stats" — confirming the ingestion job never completed and never persisted any derivative (no tree, no flat fallback, no meta). This is consistent with the reported processing_status=timeout: the job died/hung before the write_barrier that produces processed/<doc_id>.meta.json, so there is no ground truth to score against. [Absence re-confirmed at publish time against live store.] |
| 17 | اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf | unknown | ERROR | At score time, no meta.json or tree JSON existed in MinIO for this document — doc_id "pending" is a placeholder, never a real assigned ID, and processed/pending.meta.json returned NoSuchKey on all 3 retries (5s apart), consistent with the reported "timeout" processing_status. **Pre-publish verification divergence:** live MinIO now holds processed/d58be46f-8bd4-4cda-a166-809e92be66fa.meta.json + .flat.json (processed_at 06:31:28Z, 28,202 chars, flat_mixed, stored PASS) — the job completed late, after the scoring pass. ERROR retained as score-time truth; re-score in Run 20. |
| 18 | القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf | unknown | PASS | Arabic regulatory document with 48,387 chars across 66 nodes at depth 4; structure and content volume are consistent with a legal decree, no garbling or programmatic issues detected. |
| 19 | سياسة حوكمة و إدارة البيانات - Copy.pdf | unknown | PASS | Arabic data governance policy with clean extraction: 24 nodes/depth-3 is adequate for a policy doc, 18k chars consistent with ~6 pages, zero garble. |
| 20 | قرار مجلس الوزراء رقم (1) لسنة 2022 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل.pdf | unknown | MARGINAL | Complete hierarchy collapse (depth=1) on a multi-chapter legal regulation document; 308 nodes all flat under root, 0 structural markers detected despite expected articles/chapters/sections. |
| 21 | قرار مجلس الوزراء رقم (106) لسنة 2022 بشأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (9) لسنة 2022 بشأن عمال الخدمة المساعدة.pdf | flat_mixed | MARGINAL | 15-page scanned Arabic legal regulation (zero text layer) with complete structural collapse: depth 0 flat tree for a document that should have chapter/article hierarchy, and below-average char density (~1690 chars/page) suggesting partial OCR content loss. |
| 22 | مرسوم بقانون اتحادي رقم (13) لسنة 2022 بشان التأمين ضد التعطل عن العمل - Copy.pdf | unknown | MARGINAL | Structural collapse (0 nodes, depth 1) for a 4-page/8-article UAE decree; content intact with 0 garble and correct Arabic order, but flat tree with article mis-nesting degrades legal-document queryability. |
| 23 | مرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل وتعديلاته.pdf | unknown | MARGINAL | Tree is structurally collapsed relative to expectation for a 100-page legal statute, and ~1062 chars/page is well below the 2000-4000 expected range, indicating significant content loss. [Live store measures 234 nodes at depth 4 (106,230 chars), not the dispatched depth-2 — structural collapse is less severe than dispatched, but the content-density concern stands; verdict retained pending re-score.] |
| 24 | وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات حكومة أبوظبي حول برنامج مهارات المهن الحرفية - Copy.pdf | unknown | ERROR | No processed artifacts exist in MinIO for this document under doc_id 'unknown' or under any doc_id in the full inventory (23 processed docs total, none matching this filename or the '597' fragment). meta.json fetch returned NoSuchKey on all 3 retry attempts (5s apart). This confirms the reported 'timeout' status: the ingestion job never completed to the point of writing processed/*.meta.json or processed/*.json to MinIO — there is no tree, no meta, nothing to score. [Absence re-confirmed at publish time against live store.] |
| 25 | ﺣﻘﻮق اﻹﻧﺴﺎن - Copy.pdf | text_based | PASS | Large Arabic human rights doc with healthy 352-node/depth-5 tree and 420k chars; zero garble and no programmatic issues confirm clean extraction. |

**Run 19 Tally (25/25 audited):** 9 PASS, 12 MARGINAL, 1 FAIL, 3 ERROR

---

## Delta from Prior Run -> Run 19

### Improvements

- **Federal Decree-Law No. (47) of 2021 - Copy.pdf** — FAIL -> MARGINAL: Run 18 flagged severe over-segmentation into heading-only body-less fragments (88% of nodes) plus a 40% meta-vs-tree char discrepancy (22,186 vs 13,351) — a genuine content-loss defect. Run 19 shows full content present (22,186 chars matches meta exactly, live-verified) with only pre-existing shallow depth-2 hierarchy and a leave-sub-clause mis-ordering issue remaining. The content-loss regression from Run 18 appears fixed; only the older structural-flatness issue persists.
- **Reitlehrer - Schäden am Berittpferd.pdf** — MARGINAL -> PASS: Run 18 flagged one unenriched image marker (likely a logo) missing content_class metadata as a MARGINAL-worthy gap. Run 19 reports all 5 sections faithfully captured with correct structure and no content loss — the marker-enrichment nit no longer blocks a PASS.
- **سياسة حوكمة و إدارة البيانات - Copy.pdf** — FAIL -> PASS: Run 18 (per its Stalls section) reported persistent RTL-reversal / Arabic single-letter fragment garbling across runs 10-18 (79-100% of nodes) under judge scoring, even though the automated/stored verdict was already PASS at that time (a known gate-vs-judge divergence). Run 19 reports zero garble detected with node/char counts essentially unchanged (24 nodes/18,185 chars vs 24/18,287). Treat this with caution: it may reflect the judge converging on the pipeline's actual (unchanged) output rather than a genuine pipeline fix, since the underlying artifact barely moved.

### Structural improvements

- **مرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل وتعديلاته.pdf** — Verdict held at MARGINAL in both runs (persistent hierarchy collapse for this 100-page statute), but content volume appears to have grown substantially: Run 18's Stalls section cited roughly 6,022 body chars as persisting across prior runs, whereas Run 19's live store measures 106,230 chars across 234 nodes at depth 4 (~1,062 chars/page). If this comparison is accurate, it represents a large content-recovery improvement masked by an unchanged verdict, since the article-hierarchy-collapse defect that keeps the doc at MARGINAL is unrelated to raw content volume. Note: the Run 18 source file was heavily compressed/garbled in places, so the '6,022' figure should be re-verified against the live Run 18 meta.json before treating this as confirmed rather than a parsing artifact. (Dispatched "63 nodes" corrected to 234 per live tree measurement at publish time.)

### Regressions

- **uae_numbers_english_page_16_17_landscape - Copy.pdf** — MARGINAL -> FAIL: Chart data extraction quality dropped from a merely thin/lossy extraction to an unusable, structurally scrambled one between runs. Hypothesis: Run 18 already flagged content-loss concern from thin extraction (748 chars over 2 pages, byte-identical in the live store this run) but still passed as MARGINAL. Run 19 shows the chart data now fragmented into 71 unusable kv singletons (of 78 flat blocks) with scrambled OCR read-order plus a phantom enrichment-promotion claim (`verdict_reason: image_enrichment_promoted` in live meta) unsupported by actual content. This looks like a worsening of chart/table splitting for landscape-oriented pages, plausibly touched by the RFC-035 'table-meta-landscape-fixes' work (design doc agents/designs/design-rfc035-run18-table-meta-landscape-fixes.md, tests tests/test_rfc035_d2_landscape.py) landing between Run 18 and Run 19 without fully resolving the underlying fragmentation, or introducing a new splitting defect specific to landscape pages.
- **uae_numbers_english_page_16_17_portrait - Copy.pdf** — PASS -> MARGINAL: A document explicitly stable through Run 18 (correctly captured as flat_mixed with 4 charts) now shows the same singleton-fragmentation failure mode as its landscape sibling. Hypothesis: this document was explicitly listed as stable PASS->PASS through Run 18 (4 economic-sector charts correctly captured as flat_mixed). Run 19 now shows 89% singleton kv fragmentation (71/80 blocks are bare chart axis labels/values, live-verified 80 blocks) — the same failure mode newly afflicting its landscape sibling. Given both orientation variants of the same source chart regressed together, the most likely cause is a shared table/chart-block splitting change (again plausibly the RFC-035 landscape-table fix set, or a related change in converters.py/helpers.py per the working-tree diff) that altered chart-block segmentation logic used by both page orientations, degrading a previously-working extraction path.
- **اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf** — MARGINAL -> ERROR: A document with live, scoreable artifacts and growing node count in Run 18 had zero processed artifacts in MinIO at score time in Run 19. Hypothesis: Run 18 scored this SLA/legal document MARGINAL with node count more than doubling since Run 16 (99 -> 225 nodes) and content intact (~27k chars) — a live, scoreable artifact. At Run 19 score time no processed artifacts were found: doc_id stuck at the 'pending' placeholder and processed/pending.meta.json returning NoSuchKey on all retries, consistent with a reported 'timeout' status where the job died before the write barrier. This suggests a job-completion/persistence regression (possibly interacting with the RFC-034 D18 write-barrier change, tests/test_rfc034_d18_write_barrier.py, which is in the current uncommitted diff) that now causes this document's ingestion job to fail to complete and persist, where it previously succeeded (if imperfectly). **Pre-publish verification caveat:** the artifact did eventually land (processed_at 06:31:28Z, 28,202 chars, flat_mixed, stored PASS) minutes after the rest of the corpus — so the regression may be a severe slowdown / late completion past the scorer's timeout window rather than a hard persistence failure. Re-score in Run 20 before treating as a hard ERROR.

### Regressions requiring investigation

| Doc | From -> To | Prime suspect | Evidence to pull |
|---|---|---|---|
| uae_numbers_english_page_16_17_landscape - Copy.pdf | MARGINAL -> FAIL | RFC-035 landscape/table-splitting changes (design-rfc035-run18-table-meta-landscape-fixes.md, tests/test_rfc035_d2_landscape.py) | Diff chart-block segmentation in converters.py/helpers.py against Run 18 commit; re-run splitter on this PDF; verify `image_enrichment_promoted` verdict_reason against actual enrichment payloads |
| uae_numbers_english_page_16_17_portrait - Copy.pdf | PASS -> MARGINAL | Same shared chart/table-block segmentation change as landscape sibling (both orientation variants regressed together) | Compare Run 18 vs Run 19 flat.json block lists for this doc_id; bisect converters.py/helpers.py working-tree diff |
| اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf | MARGINAL -> ERROR (score-time) | RFC-034 D18 write-barrier change (tests/test_rfc034_d18_write_barrier.py) delaying job completion past the scorer's polling window | Worker logs for doc_id d58be46f timing; job duration vs other docs (06:31 vs 06:26-06:28 cohort); re-score late-landed artifact in Run 20 |

### Stalls

- **FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE - Copy.pdf** — MARGINAL -> MARGINAL: Same shallow depth-2 legal hierarchy collapse (Book/Chapter/Section/Article flattened) flagged in both Run 18 and Run 19, despite adequate content volume.
- **Haftpflicht-Allgemeine-Bedingungen.pdf.pdf** — MARGINAL -> MARGINAL: Identical node/char counts between runs (136 nodes / 77,024 chars, live-verified) and the same depth-2-too-shallow structural complaint for a German insurance general-conditions document; no change in either direction.
- **cabinet_resolution_no_21_of_2020_concerning_service_fees_and_administrative_fines_in_the_ministry_of_human_resources_and_emiratisation (1) - Copy.pdf** — MARGINAL -> MARGINAL: Hierarchy-flattening issue persists (Run 18: flattened fee/fine schedule tables with duplicated headers; Run 19: shallow hierarchy — live store measures 45 nodes at depth 3, see D4 divergence table). Symptom description shifted slightly but the doc remains MARGINAL for the same underlying structural-queryability degradation, with content fully extracted in both runs (20,721 chars in Run 19).
- **image pie chart about labor distribution in january 2025 - Copy.jpg** — MARGINAL -> MARGINAL: Same OCR garbling of chart data points persists across both runs; chart structure/general meaning remains recoverable but numeric/text garbling is unresolved.
- **قرار مجلس الوزراء رقم (1) لسنة 2022 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل.pdf** — MARGINAL -> MARGINAL: Flat-hierarchy collapse for this multi-chapter labor-law regulation has persisted across Run 16, 18, and 19 without resolution (Run 19: depth=1, 308 nodes all flat under root — 308 flat blocks live-verified, 0 structural markers).
- **قرار مجلس الوزراء رقم (106) لسنة 2022 بشأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (9) لسنة 2022 بشأن عمال الخدمة المساعدة.pdf** — MARGINAL -> MARGINAL: Structural collapse on this scanned Arabic regulation persists unresolved (Run 19 adds a new note of below-average OCR char density suggesting possible partial content loss, but the verdict and core flat-hierarchy defect are unchanged).
- **مرسوم بقانون اتحادي رقم (13) لسنة 2022 بشان التأمين ضد التعطل عن العمل - Copy.pdf** — MARGINAL -> MARGINAL: Flat/collapsed tree (0-node depth-1 structure) for this 4-page/8-article decree persists; content and Arabic order remain intact in both runs, only the hierarchy defect is unresolved.
- **وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات حكومة أبوظبي حول برنامج مهارات المهن الحرفية - Copy.pdf** — ERROR -> ERROR: Already ERROR in Run 18 (low_quality_tree / rtl_reversal rejection at the tree-quality gate); Run 19 confirms no processed artifacts exist anywhere in MinIO for this document. The document remains fully unrecoverable across both runs.
- **world-stats-pocketbook-2023.pdf** — missing/no-artifacts -> ERROR: Run 18 noted this document was entirely absent from the processed store (not scoreable, despite being PASS in Run 16). Run 19 confirms the same underlying condition: no processed artifacts exist in MinIO after a full inventory scan. This is a continuation of the same unresolved persistence gap, not a new event.
- **GHV-TKV-Tarif.pdf** — unknown -> MARGINAL: This document does not appear in the Run 18 Summary Scorecard excerpt provided (no verdict, node, or char figures found), so no Run18->Run19 delta can be computed. Flagging for visibility rather than as a scored stall.
- **Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf** — unknown -> MARGINAL: This document does not appear in the Run 18 Summary Scorecard excerpt provided, so no Run18->Run19 delta can be computed. Flagging for visibility rather than as a scored stall.

### Stable

- Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf (PASS -> PASS)
- MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf (PASS -> PASS)
- Ministerial Resolution No279 of 2022 Monitoring Mechanisms of Emiratisation Rates in the Private Sec - Copy.pdf (PASS -> PASS)
- cabinet_resolution_no_96_of_2023_regarding_an_alternative_end_of_service_benefits_system - Copy.pdf (PASS -> PASS)
- federal_decree_law_no_33_of_2021_regarding_the_regulation_of_employment_relationship_and_its_amendments - Copy.pdf (PASS -> PASS)
- القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf (PASS -> PASS)
- ﺣﻘﻮق اﻹﻧﺴﺎن - Copy.pdf (PASS -> PASS)
