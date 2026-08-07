<!-- Space: CITRA -->
<!-- Title: Corpus Re-ingestion Audit — Run 15 -->
<!-- Folder: Audits -->

# Corpus Re-ingestion Audit — Run 15

## Environment

- Branch: feat/pdf-inspector-shadow-pilot
- Date: 2026-08-06
- Prior run: /Users/saliltrehan/Documents/Python_n_R/Personal/pageindex/audit/CORPUS_REINGESTION_AUDIT_RUN-14.md
- Methodology: Incremental ingest+score pipeline (each doc scored immediately after processing)

---

## Pre-publish Verification (RFC-025 D4)

Every per-document figure below was re-pulled from the live MinIO `pageindex` bucket (`processed/{doc_id}.meta.json` + `processed/{doc_id}.json` / `processed/{doc_id}.flat.json`) before writing. Live inventory: **25 persisted docs**, all re-processed 2026-08-06 between 11:02 and 11:19 UTC. Corrections applied where the source findings diverged from live state:

> 1. **ﺣﻘﻮق اﻹﻧﺴﺎن**: the claimed 70+ nodes / depth 13 / 111,902 chars is **refuted** — the live tree reads **347 nodes / depth 6 / 394,717 chars**, byte-identical in shape to Run 14. No 72% content shrink occurred. Moreover, direct inspection of the live tree shows node titles are **still bidi-reversed** (largest node titled `تايوتحملا` = المحتويات reversed, 18,636 chars; `ةصالخلا` = الخلاصة reversed), contradicting the judged "0 garble / reversed headings gone" claim. The PASS upgrade is figure-corrected in the Scorecard and flagged as suspect in Improvements.
> 2. **cabinet_resolution_no_96_of_2023**: the claimed "Article 5 contains 21,245 of 26,111 chars (81%) in a single unsplit leaf" is **refuted** — the live tree's largest node is *Article 1 Definitions* at **3,488 chars (13%)**; total 26,111 chars / 108 nodes / depth 3 confirmed exact. The real live defect is unchanged hierarchy flatness (**85/108 nodes top-level**, identical to Runs 13–14). Key finding re-derived from store.
> 3. **Federal Decree-Law No. (47) of 2021**: the claimed "Articles 3-13 concatenated into one massive node (0012)" is **refuted** — the live largest node is *Definitions* at **1,727 chars**; no concatenated blob exists. Live shape: 69 nodes / depth 2 / 13,376 chars / 54 top-level. The real defect remains the shallow depth-2, majority-top-level flatness carried from Run 14. Key finding re-derived from store.
> 4. **القرار التنظيمي لوزارة الاقتصاد1 (2)**: the "no artifacts in MinIO" state observed at scoring time is **no longer true at publish time** — live store now holds meta + tree: **83 nodes / depth 5 / 48,586 chars, stored verdict PASS, processed_at 2026-08-06T11:05:56Z**. The NoSuchKey during scoring was transient (score attempted before/around the write), pointing to a persistence-timing race rather than a lost write. ERROR verdict retained (the doc genuinely could not be scored this run) with the live state noted.
> 5. **قرار مجلس الوزراء رقم (106)**: the claimed 217 sibling nodes / 27,740 block-sum are **refuted** — those figures belong to the SLA doc (217 blocks / 27,740 meta chars live). قرار 106 live reads **158 flat blocks, meta `flat_char_count` 39,671 vs block text sum 20,634** — the accounting gap is **~48%**, larger than the claimed 30%. Corrected in Scorecard and Structural improvements.
> 6. **مرسوم بقانون اتحادي رقم (33) لسنة 2021 (Arabic)**: claimed 159K chars decomposes as **101,627 body-text chars** (159K including node summaries — same accounting as Run 14's 102,018 body / 158,813 incl. summaries). 236 nodes / depth 4 confirmed exact. Body content is essentially unchanged vs Run 14; the real deltas are depth 3 → 4 and nodes 230 → 236. The artifact-swap hypothesis vs the English sibling is **refuted**: both docs exist live with distinct shapes (Arabic 236/101.6k, English 502/103,081 — the English shape identical to Run 14's).
> 7. **image pie chart**: claimed 536 chars reads **489** live (`flat_char_count`, 6 blocks) — byte-identical to Runs 11–14; the claimed 489 → 536 growth is refuted. Stored verdict_reason is `image_enrichment_promoted_below_char_floor`, indicating the enrichment path fired but stayed below the char floor.
> 8. **uae_numbers landscape**: claimed 872 chars reads **748** live (78 blocks) — byte-identical to Runs 12–14. Stored verdict flipped to PASS (`image_enrichment_promoted`) vs judged MARGINAL.
> 9. **uae_numbers portrait**: claimed 1,176 chars reads **764** live (80 blocks) — byte-identical to Runs 12–14; this same 764 → 1,176 growth claim is now refuted for the **third consecutive run**. The enrichment side of the claim is, however, supported: stored verdict_reason is `image_enrichment_promoted`.
> 10. **Unfallversicherung**: live flat store reshaped this run — **21 blocks, meta `flat_char_count` 7,171** (block text sum 492; table blocks carry cell payloads outside `text`). Stored verdict MARGINAL `depth=1`, consistent with the judged verdict.
> 11. **Exact-match confirmations**: Penal Code 606 nodes / 220,268 chars / depth 3; fed. decree-law 33 (EN) 502 nodes / depth 4 / 103,081 body chars / 286 top-level; Haftpflicht-Besondere 133,688 body chars / 67 nodes / depth 3; Haftpflicht-Allgemeine 53,145 chars / 132 nodes / depth 2; GHV-TKV 6,033 chars / 4 nodes / depth 2 (stored MARGINAL `leaf_concentration=0.65`); Min. Res. 279 7,782 chars / 28 nodes / depth 2; cab. res. 21 16,748 chars / 43 nodes / depth 3; Reitlehrer 2,768 chars / 10 nodes; MOU 13,422 meta chars / 134 blocks; world-stats 6,193,594 meta chars / 2,591 blocks; SLA 27,740 meta chars / 217 blocks (stored MARGINAL `garbling(ratio=1.00)` — the false-positive is live-confirmed); قرار رقم (1) 48,006 chars / 339 blocks (stored `depth=1`); سياسة حوكمة 24 nodes / depth 4 / 17,575 chars; مرسوم 13 23 nodes / depth 4 / 5,620 chars; وارد 597 108 nodes / depth 6 / 77,836 chars.
> 12. **Stored vs audit verdicts**: the gate still diverges from the judge — **سياسة حوكمة** stored PASS vs judged FAIL; **وارد 597**, **ﺣﻘﻮق اﻹﻧﺴﺎن**, **مرسوم 33 (AR)**, **مرسوم 13**, **القرار التنظيمي** stored PASS vs judged MARGINAL/PASS/ERROR variously; **SLA** stored MARGINAL `garbling(ratio=1.00)` on clean Arabic (the recurring false positive); **landscape/portrait** stored PASS `image_enrichment_promoted` vs judged MARGINAL/PASS. The gate remains blind to RTL reversal and hierarchy collapse while over-firing on clean mixed-script Arabic.
> 13. **Depth convention**: narrative depth figures count tree levels (root level = 1). Flat stores are described as depth 1 per the source finding's convention.

---

## Summary Scorecard

| # | Document | Doc Class | Verdict | Key Finding |
|---|---|---|---|---|
| 1 | اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf | flat_mixed | MARGINAL | Garble gate false-positive (ratio=1.00, live-confirmed in stored verdict_reason) on clean Arabic text with legitimate mixed-script tokens; flat tree (217 blocks / 27,740 chars live) acceptable for short 19-page SLA but limits retrieval granularity |
| 2 | سياسة حوكمة و إدارة البيانات - Copy.pdf | unknown | FAIL | 79% of nodes contain Arabic single-letter fragment garbling (non-PUA, undetected by gate — stored verdict remains PASS), combined with RTL word-order reversal and structural undercounting (24 nodes / depth 4 live-verified for a 7-section policy with sub-clauses) — document is unusable for RAG |
| 3 | قرار مجلس الوزراء رقم (1) لسنة 2022 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل.pdf | flat_mixed | MARGINAL | UAE labor regulation (30+ page legal doc) collapsed to depth=1 flat tree; 339 blocks / 48,006 chars (live-verified exact) suggest content is present but hierarchical chapter/article structure is lost |
| 4 | قرار مجلس الوزراء رقم (106) لسنة 2022 بشأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (9) لسنة 2022 بشأن عمال الخدمة المساعدة.pdf | flat_mixed | MARGINAL | Flat tree (depth=1) collapses 17-article legal hierarchy into **158** sibling blocks (live-verified; claimed 217 belonged to the SLA doc); content extraction is clean but a **~48%** char-count mismatch (39,671 meta vs 20,634 live block-sum) signals an accounting gap larger than initially reported. |
| 5 | مرسوم بقانون اتحادي رقم (13) لسنة 2022 بشان التأمين ضد التعطل عن العمل - Copy.pdf | unknown | PASS | Short 4-page/8-article UAE decree fully captured with correct Arabic logical order; 23 nodes at depth 4 (5,620 chars live) is appropriate for this compact legal instrument. |
| 6 | مرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل وتعديلاته.pdf | unknown | PASS | UAE labor decree-law well-structured: 236 nodes at depth 4 with 101,627 body-text chars (~159K incl. node summaries, live-verified) and zero garbled blocks; meta-schema gaps are tooling issues only |
| 7 | القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf | flat_prose | ERROR | At scoring time neither meta.json nor tree/flat JSON was retrievable for this doc_id (both scripts/minio_helper.py calls returned S3 NoSuchKey and a broader processed/ listing was empty), so the doc could not be scored despite a reported "success" processing status. **Publish-time verification: artifacts now exist live** (83 nodes / depth 5 / 48,586 chars, stored PASS, processed_at 2026-08-06T11:05:56Z) — the miss was a transient persistence-timing race, not a lost write. |
| 8 | ﺣﻘﻮق اﻹﻧﺴﺎن - Copy.pdf | human-rights | PASS | Arabic human-rights doc judged clean this run, but live figures correct the claim: **347 nodes / depth 6 / 394,717 chars** (not 70+/depth 13/112k), shape-identical to Run 14, and live node titles are **still bidi-reversed** (e.g. `تايوتحملا`) — the 0-garble PASS is suspect (see Improvements caveat). Also a suspected duplicate of the already-audited human-rights corpus document. |
| 9 | وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات حكومة أبوظبي حول برنامج مهارات المهن الحرفية - Copy.pdf | not_available | MARGINAL | Content-filename mismatch confirmed: filename references craftwork skills program but tree content is 100% anti-commercial-fraud regulation (Decree-Law 42/2023); tree structure itself is well-formed (108 nodes / depth 6 / 77,836 chars live-verified, 0 garble) but document identity is unreliable. |
| 10 | cabinet_resolution_no_21_of_2020_concerning_service_fees_and_administrative_fines_in_the_ministry_of_human_resources_and_emiratisation (1) - Copy.pdf | unknown | PASS | 11-page UAE legal resolution fully extracted with all 12 articles and 6 annexed fee schedules; 43 nodes at depth 3 (16,748 chars live) adequate for this article-level structure; minor table cell scrambling in complex multi-column schedules is non-blocking. |
| 11 | cabinet_resolution_no_96_of_2023_regarding_an_alternative_end_of_service_benefits_system - Copy.pdf | legal-resolution | MARGINAL | Claimed Article-5 21,245-char blob is **refuted by live tree** (largest node: Article 1 Definitions, 3,488 chars / 13%); the live defect is unchanged hierarchy flatness — **85/108 nodes top-level** at depth 3 (26,111 chars), identical to Runs 13–14, degrading sub-article retrievability. |
| 12 | Federal Decree-Law No. (47) of 2021 - Copy.pdf | unknown | MARGINAL | Claimed Articles 3-13 concatenation into one massive node (0012) is **refuted by live tree** (largest node 1,727 chars); the live defect is the persistent shallow depth-2 tree with 54/69 nodes top-level and thin 13,376 total chars, flattening the legal hierarchy needed for article-level RAG queries. |
| 13 | FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE  - Copy.pdf | unknown | PASS | 77-page UAE Penal Code well-captured: 606 nodes, 220,268 chars (live-verified), no garbling; depth 3 is slightly shallow for a multi-level legal code but content coverage is complete. |
| 14 | federal_decree_law_no_33_of_2021_regarding_the_regulation_of_employment_relationship_and_its_amendments - Copy.pdf | legal-statute | MARGINAL | 58-page UAE employment law has full text (103,081 body chars live-verified, no loss vs 97k raw) but severely flat tree: 286/502 nodes top-level (exact live match), max depth 4, ToC misparsed into ~130 heading nodes, sub-clauses not nested under parent Articles. |
| 15 | GHV-TKV-Tarif.pdf | tariff-table | MARGINAL | Single-page pet insurance tariff table: 4-node tree correctly maps 3 sections (Pferd/Hund/Katze), but 6,033 chars (byte-identical to Run 14; stored `leaf_concentration=0.65`) likely under-captures the dense multi-column pricing tables. |
| 16 | Haftpflicht-Allgemeine-Bedingungen.pdf.pdf | unknown | MARGINAL | 98% char coverage (53,145/54k live-verified) but depth-2 tree is too shallow for a 16-page legal conditions document; preamble has undetected vertical-text garbling and 3 images lack enrichment. |
| 17 | Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf | unknown | PASS | Strong extraction: 133,688 body chars across 67 nodes at depth 3 (live-verified) with zero garble, appropriate for a German liability special conditions document. |
| 18 | image pie chart about labor distribution in january 2025 - Copy.jpg | flat_mixed | MARGINAL | Pie chart image yielded **489** chars of OCR text (live `flat_char_count`; claimed 536 refuted — byte-identical to Runs 11–14) with no usable picture enrichment above the char floor (stored `image_enrichment_promoted_below_char_floor`), losing the visual proportional data that is the chart's core value. |
| 19 | Ministerial Resolution No279 of 2022 Monitoring Mechanisms of Emiratisation Rates in the Private Sec - Copy.pdf | unknown | PASS | 5-page English UAE legal resolution fully captured (7,782 chars live vs 7,625 reference); depth-2 tree with 28 nodes appropriate for flat article structure; zero garble. |
| 20 | MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf | flat_mixed | PASS | Bilingual Arabic/English MOU with 13,422 chars (live meta) across 134 flat blocks; content intact and consistent with historical baseline (13.5k), no garbling detected. |
| 21 | Reitlehrer - Schäden am Berittpferd.pdf | insurance_rider | PASS | Single-page German insurance rider fully captured; 2,768 chars and 10 nodes (live-verified) appropriate for 1-page addendum; missing image enrichment is just the GHV company logo, not substantive content. |
| 22 | uae_numbers_english_page_16_17_landscape - Copy.pdf | flat_mixed | MARGINAL | Chart data extracted as fragmented kv blocks (78 blocks live, ~71 unpaired) instead of coherent table/series structure; **748** chars (live; claimed 872 refuted — byte-identical to Runs 12–14) across 2-page spread is thin but consistent with numeric chart source |
| 23 | uae_numbers_english_page_16_17_portrait - Copy.pdf | flat_mixed | PASS | 2-page chart infographic with 80 blocks and picture-enrichment promotion (stored `image_enrichment_promoted`); low char count (**764** live; claimed 1,176 refuted — byte-identical to Runs 12–14) is expected for a visually dense infographic with minimal prose text. |
| 24 | Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf | flat_mixed | MARGINAL | Flat tree acceptable for a benefits-overview flyer, but high empty-cell ratios (up to 0.75) suggest table extraction gaps in this table-dense comparison sheet. Live store reshaped this run: 21 blocks / 7,171 meta chars (stored MARGINAL `depth=1`). |
| 25 | world-stats-pocketbook-2023.pdf | flat_mixed | PASS | Statistical pocketbook with massive char count (6,193,594 live meta) and 2,591 flat blocks; flat tree acceptable for dense tabular country-stats content, zero garbling, one minor missing image enrichment. |

**Run 15 Tally (25/25 audited):** 11 PASS, 12 MARGINAL, 1 FAIL, 1 ERROR

---

## Delta from Prior Run -> Run 15

### Improvements (6)

- **Reitlehrer - Schäden am Berittpferd.pdf** — MARGINAL -> PASS: Char count unchanged from Run 14's regressed value (2,768 live-verified, same RFC-029 D3 stripping loss vs original 4,082) but judge re-classified missing image enrichment as non-substantive (GHV company logo only) rather than content loss — a scoring-severity change, not a pipeline fix.
- **uae_numbers_english_page_16_17_portrait - Copy.pdf** — MARGINAL -> PASS: Claimed structural gain is **half-refuted by live state**: char count did NOT grow 764 -> 1,176 (`flat_char_count` reads 764, byte-identical to Runs 12–14; this exact growth claim is now refuted for the third consecutive run). The enrichment half is supported — stored verdict_reason is now `image_enrichment_promoted` (was unpaired-kv fragmentation with no enrichment in Run 14) — which is the substantive movement behind the upgrade.
- **مرسوم بقانون اتحادي رقم (13) لسنة 2022 بشان التأمين ضد التعطل عن العمل - Copy.pdf** — MARGINAL -> PASS: Tree depth recovered from flat depth-1 (Run 14) to depth-4 with 23 nodes (live-verified) correctly reflecting the 8-article decree structure; the prior scanned-Arabic garbled-text-layer issue also no longer appears. Live body chars 5,620 (vs 5,934 in Run 14 — essentially stable).
- **مرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل وتعديلاته.pdf** — MARGINAL -> PASS: The claimed char jump ~102k -> 159,304 is an **accounting artifact, not growth**: live body text reads 101,627 chars (159K only when node summaries are included — the same decomposition Run 14 measured as 102,018 body / 158,813 incl. summaries). The genuine deltas are depth 3 -> 4 and nodes 230 -> 236 with 0 garble. The artifact-swap hypothesis vs the English sibling is **refuted by live state**: both docs exist with distinct shapes (Arabic 236 nodes / 101.6k body; English 502 nodes / 103,081 body, identical to its own Run 14 shape) — no swap occurred.
- **وارد رقم 597 من مكتب أبوظبي التنفيذي ... - Copy.pdf** — FAIL -> MARGINAL: The Run 14 garble-gate hole (62k chars of garbled Arabic text-layer, garbled_blocks=0) is gone and the doc now yields a clean 108-node / depth-6 / 77,836-char tree (live-verified; Run 14 was 121 nodes / depth 7 / 62,207 chars — a genuinely different artifact shape). However the extracted content is now entirely about anti-commercial-fraud regulation (Decree-Law 42/2023), unrelated to the craftwork-skills-program filename — the FAIL -> MARGINAL move looks like a content-identity/document-swap artifact rather than a genuine extraction fix for this specific source PDF.
- **ﺣﻘﻮق اﻹﻧﺴﺎن - Copy.pdf** — MARGINAL -> PASS: Judge reports reversed/garbled Arabic headings (42% garble rate in Run 14) gone with 0 garble. **Live verification contradicts both halves of the claimed evidence**: (a) the claimed 347 -> 70 node drop and 394,717 -> 111,902 char shrink did NOT happen — the live tree reads exactly 347 nodes / depth 6 / 394,717 chars, shape-identical to Run 14; (b) live node titles are still bidi-reversed (`تايوتحملا`, `ةصالخلا`). The upgrade therefore appears to be a judge-side re-read (possibly of a different/duplicate artifact, as Run 15 itself flags) rather than any pipeline fix — treat this PASS as unconfirmed.

### Structural improvements (2)

- **image pie chart about labor distribution in january 2025 - Copy.jpg** — Verdict held at MARGINAL. The claimed OCR growth 489 -> 536 chars is **refuted by live state**: `flat_char_count` reads 489, byte-identical to Runs 11–14 (6 blocks). Stored verdict_reason `image_enrichment_promoted_below_char_floor` shows the enrichment path now fires but stays below the char floor — the chart's proportional-data loss remains unresolved with no measurable content movement this run.
- **قرار مجلس الوزراء رقم (106) لسنة 2022 ... عمال الخدمة المساعدة.pdf** — Meta chars grew 26,146 -> 39,671 (+52%, live-verified) between runs, but the tree remains flat depth-1 (verdict held at MARGINAL, stored `depth=1`). Live block figures correct the claim: **158 blocks (not 217) with block text sum 20,634 (not 27,740** — those figures belong to the SLA doc), so the meta-vs-block accounting gap is **~48%**, larger than the reported 30%: the volume gain came with a bigger bookkeeping discrepancy than initially stated.

### Regressions (3)

- **federal_decree_law_no_33_of_2021_regarding_the_regulation_of_employment_relationship_and_its_amendments - Copy.pdf** — PASS -> MARGINAL: Verdict downgraded for a legal-hierarchy-flatness defect. Hypothesis (supported by live state): underlying shape is identical to the state Run 14 explicitly scored PASS (502 nodes, depth 4, 103,081 body chars, 286/502 top-level — all live-verified exact, same misparsed ToC). No structural or content change occurred between runs — this is a judge/verdict-classification severity change (flat-tree-with-hierarchy-loss now scored MARGINAL instead of PASS), the same pattern already seen with cabinet_resolution_no_96 in Run 13 -> 14.
- **اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf** — PASS -> MARGINAL: Garble-gate false-positive (ratio=1.00) reappeared, downgrading the verdict despite legitimate clean Arabic content. Hypothesis: the exact garble-gate false-positive from Run 13 (ratio=1.00 on clean mixed-script Arabic) reappeared in Run 15 (live stored verdict_reason confirms `garbling(ratio=1.00)`) after Run 14 had corrected/overridden it to a recomputed ratio of 0.067 and scored PASS. Suggests either non-determinism in the garble-gate scoring or a regression that reintroduced the false trigger, combined with this run's judge not applying the same override Run 14 did.
- **القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf** — PASS -> ERROR: No processed artifacts were retrievable from MinIO for this doc_id at scoring time despite a reported successful processing status, so it could not be scored this run. Hypothesis (revised by publish-time verification): the artifacts **do exist live now** — meta + tree with 83 nodes / depth 5 / 48,586 chars, stored PASS, processed_at 2026-08-06T11:05:56Z — so this was a **transient persistence-timing race** (scoring read raced the write) rather than a persistence/write-path loss. Content-wise the live tree is close to Run 14's PASS state (90 -> 83 nodes, 46,675 -> 48,586 chars, depth 5 unchanged); the doc needs a re-score, not a re-ingest.

### Regressions requiring investigation

| Document | Change | Live-verified state | Investigation lead |
|---|---|---|---|
| federal_decree_law_no_33_of_2021 … - Copy.pdf | PASS -> MARGINAL | 502 nodes / depth 4 / 103,081 body chars / 286 top-level — identical to Run 14's PASS state | Confirm judge-side severity shift (flatness scored more harshly); no pipeline change implicated |
| اتفاقية مستوى الخدمة … .pdf | PASS -> MARGINAL | 217 blocks / 27,740 meta chars; stored verdict_reason `garbling(ratio=1.00)` live | Garble-gate non-determinism or reintroduced false trigger on clean mixed-script Arabic; Run 14's recomputed ratio was 0.067 |
| القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf | PASS -> ERROR | Artifacts exist live: 83 nodes / depth 5 / 48,586 chars, stored PASS, processed 2026-08-06T11:05:56Z | Persistence-timing race between worker write and scorer read; verify read-after-write ordering in the incremental ingest+score pipeline, then re-score |

### Stalls (8)

- **Federal Decree-Law No. (47) of 2021 - Copy.pdf** — MARGINAL -> MARGINAL: Same structural collapse class persists. The Run 15 claim of Articles 3-13 concatenated into one massive node is refuted by the live tree (largest node 1,727 chars), but the underlying hierarchy-collapse defect is real and unchanged: depth 2 with 54/69 nodes top-level (Run 14 flagged the same shallow depth-2 plus reading-order displacement). Unresolved.
- **GHV-TKV-Tarif.pdf** — MARGINAL -> MARGINAL: Byte-identical: 6,033 chars / 4 nodes in both runs (live-verified). _segment_table_nodes still not wired into the primary tree-build path, so the tariff table stays a single flat node (stored `leaf_concentration=0.65`).
- **Haftpflicht-Allgemeine-Bedingungen.pdf.pdf** — MARGINAL -> MARGINAL: Same depth-2 tree, same 53,145 chars / 132 nodes as Run 14 (live-verified). 32-clause AHB document still lacks the hierarchy depth its legal structure needs.
- **Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf** — MARGINAL -> MARGINAL: Same core defect (table cell / checkmark data not captured), now described as high empty-cell ratios (up to 0.75) rather than missing OCR on image blocks — same benefit-comparison-table extraction gap. Live store reshaped to 21 blocks / 7,171 meta chars this run without a verdict change.
- **cabinet_resolution_no_96_of_2023_regarding_an_alternative_end_of_service_benefits_system - Copy.pdf** — MARGINAL -> MARGINAL: Same hierarchy flatness — live tree reads 85/108 nodes top-level at depth 3 / 26,111 chars, identical to Runs 13–14 (the Run 15 Article-5-single-blob description is refuted by the live tree; the flatness finding stands). Unresolved across three runs.
- **uae_numbers_english_page_16_17_landscape - Copy.pdf** — MARGINAL -> MARGINAL: Chart-data fragmentation into single-token kv blocks persists (78 blocks live, ~71 unpaired in both runs) with char count byte-identical at 748 (claimed 872 refuted). Stored verdict flipped to PASS `image_enrichment_promoted` but the judge still finds no coherent table/series structure.
- **سياسة حوكمة و إدارة البيانات - Copy.pdf** — FAIL -> FAIL: Garbling persists but the specific signature shifted: Run 14 found 87.5% of nodes with bidi-reversed section titles; Run 15 finds 79% with Arabic single-letter fragment garbling (non-PUA) plus RTL word-order reversal. Same root RTL/Arabic-garbling class the gate remains blind to (stored verdict still PASS live), still FAIL. Live shape unchanged: 24 nodes / depth 4 / 17,575 chars.
- **قرار مجلس الوزراء رقم (1) لسنة 2022 ... تنظيم علاقات العمل.pdf** — MARGINAL -> MARGINAL: Same flat depth-1 structural collapse: Run 14 had 316 flat blocks / 0 tree nodes at 47,670 chars; Run 15 has 339 blocks at depth 1 with 48,006 chars (live-verified exact). Content volume is stable but hierarchical article/chapter structure is still lost.

### Stable (6)

- **FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE  - Copy.pdf** — PASS -> PASS (606 nodes / 220,268 chars / depth 3, live-verified identical)
- **Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf** — PASS -> PASS (67 nodes / depth 3 / 133,688 body chars, live-verified identical)
- **MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf** — PASS -> PASS (134 blocks / 13,422 meta chars live; Run 14: 13,541)
- **Ministerial Resolution No279 of 2022 Monitoring Mechanisms of Emiratisation Rates in the Private Sec - Copy.pdf** — PASS -> PASS (28 nodes / depth 2 / 7,782 chars, live-verified identical)
- **cabinet_resolution_no_21_of_2020_concerning_service_fees_and_administrative_fines_in_the_ministry_of_human_resources_and_emiratisation (1) - Copy.pdf** — PASS -> PASS (43 nodes / depth 3 / 16,748 chars, live-verified identical)
- **world-stats-pocketbook-2023.pdf** — PASS -> PASS (2,591 blocks / 6,193,594 meta chars, live-verified identical)

---

## Finding ID Index

> **Added 2026-08-06 by `audit/RECONCILIATION_REPORT.md`.** This audit was written without finding IDs; the `A33-*` identifiers were introduced by the reconciliation pass so findings can be referenced stably across runs and mapped to RFC decisions. The index is a labeling layer over the sections above — it introduces no new findings and restates no measurements.

| ID | Sev | Finding | Where in this doc | RFC-033 mapping |
|---|---|---|---|---|
| **A33-C4a** | critical | Garble-gate false positive: `_garble_ratio` full-text tautology + `_flatten_tree_text` missing separator | Regressions; Scorecard (SLA) | **D1** — closes on D1 delivery |
| **A33-C4b** | critical | Verdict gate blind to RTL reversal: reversed Arabic headings neither detected nor corrected | Regressions requiring investigation | **D2 Part A** — stays open until the heading-reversal guard lands and the scoped re-ingest confirms clean headings |
| **A33-S1** | important | Hierarchy-collapse defects persist across runs (compound, 8 docs) | Stalls (8) | D4, D5, D2, D8, OoS [10b] |
| **A33-S2** | important | `GHV-TKV-Tarif.pdf` tariff table stalled flat — `_segment_table_nodes` not on the primary tree-build path | Stalls (8); Scorecard row 15 | **D6** |
| **A33-R1** | important | `federal_decree_law_no_33` PASS → MARGINAL (judge-side severity shift) | Regressions (3); Scorecard row 14 | **D0** |
| **A33-R2** | important | SLA PASS → MARGINAL — garble-gate false positive reappeared | Regressions (3) | **D1** |
| **A33-I1** | important | Persistence-timing race: القرار التنظيمي scoring miss | Regressions requiring investigation | **D3** |
| **A33-I2** | important | Char-accounting gap in قرار مجلس الوزراء رقم (106) | Pre-publish Verification (line 24) | OoS [9] — measurement artifact, not a pipeline defect |
| **A33-C1** | important | حقوق الإنسان node shrinkage / bidi-reversal claim | Improvements (6) | **D2 Part A** — root cause is pipeline-induced (see reconciliation C-3) |
| **A33-C2** | important | `cabinet_resolution_no_96` Article-5 blob claim | Stalls (8) | **D4** |
| **A33-C3** | important | FDL No. (47) Articles 3–13 concatenation claim | Stalls (8) | **D4** |
| **A33-C5** | important | وارد 597 FAIL→MARGINAL is a content-identity/document-swap artifact | Improvements (6); Scorecard row 9 | OoS [10a] — source-file data quality |
| **A33-I3** | informational | No artifact-swap between Arabic and English sibling docs | Improvements (6) | — (observational; close as no-defect) |
| **A33-I4** | informational | Image-enrichment promotion below char floor ineffective | Stalls (8) | **D7** |

### Uncovered sub-items

These sit inside findings counted as covered above; no RFC decision addresses them. See `audit/RECONCILIATION_REPORT.md` § Orphaned Audit Findings.

| Sub-item | Parent | Why it is uncovered |
|---|---|---|
| **Reitlehrer ~32% char-stripping loss** (2,768 vs original 4,082) | Improvements (6), line 74 | Live content-loss regression from landed RFC-029 D3, **masked by a PASS verdict** — the doc improved only because the judge reclassified the missing image as a non-substantive logo. Highest-priority uncovered item. |
| **Haftpflicht-Allgemeine** vertical-text garbling + 3 unenriched images | A33-S1, Scorecard row 16 | D5 covers the depth-2 flatness only. |
| **FDL-33 ToC misparsed into ~130 heading nodes** | A33-R1, Scorecard row 14 | D0 covers only the verdict regression; the structural misparse survives D0. |
| **SLA doc depth-1 flatness** | A33-S1 | Appears in RFC-033 only under D1 (garble false positive), never for structure. |
| **A33-I2 residual: char-sum methodology** | A33-I2 | Audit tooling, not `src/`. |

### A33-C4 split note

A33-C4 was originally a single CRITICAL finding mapped to D1 + D2, which made it closable on work addressing only half of it. Split into **C4a** (garble gate, closes on D1) and **C4b** (RTL reversal, closes on D2 Part A) per the 2026-08-06 reconciliation decision. Any cross-run reference to a bare "A33-C4" should be read as **C4a + C4b**.
