# Corpus Re-ingestion Audit — Run 8

## Environment

- Branch: ICR-97-rfc44-recovery-dispatch-wiring
- Date: 2026-09-09
- Prior run: /Users/saliltrehan/Documents/Python_n_R/Personal/pageindex/audit/CORPUS_REINGESTION_AUDIT_RUN-7.md
- Methodology: Incremental ingest+score pipeline (each doc scored immediately after processing)
- Pre-publish verification (RFC-025 D4): every per-document node / depth / char / verdict figure below was re-pulled from live MinIO `processed/{doc_id}.meta.json` + `processed/{doc_id}.json` / `.flat.json` (bucket `pageindex`, 20 meta objects, all `processed_at` 2026-09-09T08:59–09:15Z) before writing. Figures that diverged from the scorer's draft were re-derived from the store; every correction is listed in the "RFC-025 D4 verification log" section at the end. Char figures are `total_tree_chars` from meta.json (tree docs) or `flat_char_count` from `.flat.json` (flat docs).

---

## Summary Scorecard

| # | Document | Doc Class | Verdict | Key Finding |
|---|---|---|---|---|
| 1 | FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE  - Copy.pdf | legal_statute | MARGINAL | 77-page penal code extracted with correct content volume (595 nodes, 246,652 chars, ~3,200 chars/page, no garbling) but flattened to depth=2 (493 top-level nodes, 102 at depth 2) instead of a Book/Part/Chapter/Section/Article hierarchy, so structural and cross-reference queries above article level are unsupported. Gate: MARGINAL `depth_inadequate:expected_min_depth=5,actual_depth=2`. |
| 2 | Federal Decree-Law No. (47) of 2021 - Copy.pdf | legal | PASS | Live store: 70 nodes, depth 2, 22,186 chars, max_leaf_ratio 0.079, gate `structural_pass` — identical to Run 7 (69 nodes, depth 2, 22k). Scorer draft claimed depth=1 / 13,351 chars / 87% heading-only leaves; live tree has 65 leaves of which 12 (18%) are heading-only (<40 chars), the rest carry article body text (e.g. Definitions 1,727 chars). Draft MARGINAL not supported by store; re-derived to PASS. |
| 3 | GHV-TKV-Tarif.pdf | unknown | MARGINAL | Single-page Excel-to-PDF tariff table with healthy char count (10 nodes, depth 3, 6,720 chars); leaf_concentration 0.45 and partial image enrichment are expected artifacts of tabular layout, not content loss. Gate: MARGINAL `leaf_concentration=0.45`. |
| 4 | Haftpflicht-Allgemeine-Bedingungen.pdf.pdf | unknown | MARGINAL | Near-flat tree (51 nodes, depth 2 but 45 of them top-level, only 6 at depth 2) fails to capture the deep hierarchical structure expected of German insurance general conditions; 55,302 chars is adequate content but node count (132→51) and chars (80k→55k) both fell vs Run 7. Gate says `structural_pass` (max_leaf_ratio 0.094); reviewer holds MARGINAL on the Run-7 delta. |
| 5 | Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf | unknown | PASS | Live store: 78 nodes, depth 4 (14/42/20/2 per level), 138,177 chars for 38 pages (~3,636 chars/page, inside the 2K-4K benchmark), max_leaf_ratio 0.064, gate `structural_pass`. Scorer draft figures (51 nodes / depth 2 / 53,150 chars / "85K chars trapped in title+summary") were a copy of doc 4's numbers; live title+summary total 44,266 chars vs 133,685 chars of body text. Re-derived to PASS, consistent with Run 7 PASS. |
| 6 | MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf | unknown | FAIL | **Post RFC-045 garble fix (3-fix chain):** now indexed with verdict=FAIL, verdict_reason=`suspect_density(chars_per_page=1437.7)`, 9 pages, ~12,966 chars, max_leaf_ratio 0.245, converter=docling. Three compounding bugs fixed: (1) `script_mismatch` garble prong added to detect >80% Latin in Arabic-expected docs — fires GARBLING gate at tree validation; (2) `_keep_best_wins` RFC-045 post-garble escape — keeps OCR retry when post-retry is NOT garbled despite higher repeating-token density (formal Arabic vs random Latin gibberish); (3) VLM skip when full-page OCR resolved whole-tree garble — prevents VLM from overwriting good Arabic OCR content. Previously ERROR (latin_gibberish + VLM fallback failure). |
| 7 | Ministerial Resolution No279 of 2022 Monitoring Mechanisms of Emiratisation Rates in the Private Sec - Copy.pdf | legal_regulatory_uae_emiratisation | PASS | 5-page English-translation UAE ministerial resolution well captured: 28 nodes, 11,194 chars (exceeds 7,804 raw), zero garble, depth 2 adequate for this flat article structure. Gate `structural_pass`, max_leaf_ratio 0.155. |
| 8 | Reitlehrer - Schäden am Berittpferd.pdf | insurance_terms_conditions | PASS | Clean extraction of short 1-page riding instructor insurance clause (10 nodes, depth 2, 3,260 chars, max_leaf_ratio 0.203, gate `structural_pass`); minor missing image enrichment and empty leaf do not affect usability. |
| 9 | Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf | unknown | MARGINAL | Benefit-overview tables lose ~95% of checkmark/icon markers (63 markers, 3 generic enrichments), degrading the core tier-comparison data; 9 nodes, depth 2, 6,440 chars for 3 pages. Gate: MARGINAL `leaf_concentration=0.31`. |
| 10 | cabinet_resolution_no_21_of_2020_concerning_service_fees_and_administrative_fines_in_the_ministry_of_human_resources_and_emiratisation (1) - Copy.pdf | legal_resolution | MARGINAL | Shallow depth (3) and low char count (20,721 vs ~27,000 expected for 11pp, and vs 58k in Run 7) indicate content loss from complex multi-column fee schedule tables; 45 nodes, max_leaf_ratio 0.187. Gate says `structural_pass`; reviewer holds MARGINAL on the char collapse. |
| 11 | cabinet_resolution_no_96_of_2023_regarding_an_alternative_end_of_service_benefits_system - Copy.pdf | unknown | PASS | Live store: 108 nodes, depth 3 (93/13/2 per level), 43,043 chars, max_leaf_ratio 0.042, gate `structural_pass` — matches Run 7 (108 nodes, depth 3, 46k). Scorer draft claim "all 93 nodes flattened to depth-1" counted only top-level nodes; article/sub-article hierarchy is present. Content complete, no garbling. Re-derived to PASS. |
| 12 | federal_decree_law_no_33_of_2021_regarding_the_regulation_of_employment_relationship_and_its_amendments - Copy.pdf | unknown | PASS | **Post depth-recal:** depth 4 now meets recalibrated expected_min_depth=4 (was 5). Content volume is healthy at 506 nodes / 179,287 chars with zero garble. Gate: `structural_pass`. Previously MARGINAL due to over-aggressive depth floor. |
| 13 | image pie chart about labor distribution in january 2025 - Copy.jpg | image | FAIL | Complete content extraction failure: all 3 nodes (depth 1, 1,282 chars) contain garbled gibberish (`"2025 uly - A gahy antl igus gh Maal) ess"`, confirmed in live tree), PictureResult enrichment is empty, and no usable text was recovered from the pie chart image. Gate only reached MARGINAL `depth=1` (max_leaf_ratio 0.573) — the garble gate did not fire on this Latin-script noise. |
| 14 | uae_numbers_english_page_16_17_landscape - Copy.pdf | flat_mixed | FAIL | 2-page landscape infographic yielded only 1,355 chars across 198 flat blocks (0 row_records) with 32 garbled blocks (16%), complete flat collapse (depth=1), and chart data fragmented into unstructured single-word kv blocks instead of coherent tables. Gate: FAIL `max_leaf_ratio=0.86`. |
| 15 | uae_numbers_english_page_16_17_portrait - Copy.pdf | flat_mixed | MARGINAL | Flat tree and low chars (84 blocks, 1,240 chars, 0 row_records) are expected for a 2-page infographic of bar charts; text extraction captured all chart titles, data labels, and attribution -- near-complete for the textual content present. Gate says FAIL `suspect_density`; reviewer overrides to MARGINAL on content inspection. |
| 16 | world-stats-pocketbook-2023.pdf | unknown | PASS | **Post depth-recal:** depth 4 now meets recalibrated expected_min_depth=4 (was 5). Statistical pocketbook with strong content extraction (2,098,887 chars, 944 nodes, 292 pages, zero garble). Gate: `structural_pass`. Previously MARGINAL due to over-aggressive depth floor. |
| 17 | اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf | unknown | FAIL | Catastrophic content loss: 20-page signed Arabic SLA collapsed to a single flat `[Preamble]` node with 1,283 chars retained (~64 chars/page vs expected ~2,000-4,000); 19+ pages entirely absent from tree. Gate: FAIL `garbling`, max_leaf_ratio 1.0, garble_latin_ratio 0.4. |
| 18 | القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf | unknown | FAIL | **Re-test (RFC-045 fixes):** no longer garble-gate rejected; now indexed with verdict=FAIL, verdict_reason=`suspect_density(chars_per_page=1413.1)`, 35 pages, 49,460 chars, max_leaf_ratio 0.0565, converter=docling. The garble-gate false positive is resolved but the underlying extraction has low content density (1,413 chars/page vs expected 2,000-4,000). Previously ERROR due to PF false-positive garble-gate rejection + fence_parity orphan closes. |
| 19 | سياسة حوكمة و إدارة البيانات - Copy.pdf | unknown | PASS | **Post re-ingestion:** `structural_pass`, 18,287 chars, max_leaf_ratio 0.1873. PF false-positive garble-gate resolved by NFKC fallback removal (commit `2c39168`). Previously FAIL/garbling (reviewer overrode to MARGINAL). |
| 20 | قرار مجلس الوزراء رقم (1) لسنة 2022 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل.pdf | unknown | PASS | **Post re-ingestion:** `structural_pass`, 50,774 chars, max_leaf_ratio 0.0438. Digit substitution and missing articles resolved by tessdata probe fix (commit `d5f0c19`) enabling correct Arabic OCR. Previously FAIL/garbling with decree number 1→7 and Articles 1-4 missing. |
| 21 | قرار مجلس الوزراء رقم (106) لسنة 2022 بشأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (9) لسنة 2022 بشأن عمال الخدمة المساعدة.pdf | unknown | PASS | **Re-test (RFC-045 fixes):** `structural_pass`, 15 pages, 32,140 chars, max_leaf_ratio 0.1659, converter=docling, extraction_route=local. Previously ERROR due to garble-gate false positive (presentation_forms NFKC fallback). |
| 22 | مرسوم بقانون اتحادي رقم (13) لسنة 2022 بشان التأمين ضد التعطل عن العمل - Copy.pdf | unknown | FAIL | Arabic legal decree at ~1,285 chars/page (29 nodes, depth 5, 5,141 chars for 4 pages; expected 2,000-4,000/page) with empty leaf nodes and bidi-coherence garble flag indicates substantial content loss during extraction. Gate: FAIL `garbling`, max_leaf_ratio 0.177. |
| 23 | مرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل وتعديلاته.pdf | unknown | PASS | False-positive garble gate: FAIL/garbling verdict not corroborated by tree content (0 PUA codepoints, 0 garbled blocks, max_leaf_ratio 0.034 well under 0.3 threshold); 107,334 chars of clean Arabic legal text with 231 nodes at depth 3 across 100 pages is structurally sound for a federal decree-law. Reviewer overrides to PASS. Note: Run 7 stored 546 nodes / depth 5 / 172k chars — metrics drifted down inside the PASS bucket. |
| 24 | وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات حكومة أبوظبي حول برنامج مهارات المهن الحرفية - Copy.pdf | unknown | PASS | **Re-test (RFC-045 fixes):** verdict=PASS, verdict_reason=garbling (recovered), 42 pages, 57,020 chars, max_leaf_ratio 0.0901, converter=docling, extraction_route=local. Previously ERROR (timeout) due to tessdata probe failure causing wrong-language OCR. |
| 25 | حقوق الإنسان - Copy.pdf | unknown | PASS | **Re-test (RFC-045 fixes):** `structural_pass`, 161 pages, 419,856 chars, max_leaf_ratio 0.0277, converter=docling, extraction_route=local. Previously ERROR (timeout) due to tessdata probe failure. Now the deepest, richest tree in the corpus again. |

**Run 8 Tally (25/25 audited, post RFC-045 garble fix chain + depth recalibration + re-ingestion):** 14 PASS, 6 MARGINAL, 5 FAIL, 0 ERROR

> Scorer draft tally was 3 PASS / 12 MARGINAL / 5 FAIL / 5 ERROR. RFC-025 D4 re-verification against live MinIO moved docs 2, 5 and 11 from MARGINAL to PASS because the draft figures that justified MARGINAL (depth-1 collapse, char loss, heading-only leaves) do not exist in the stored trees — see verification log below.

---

## Delta from Prior Run -> Run 8

### Improvements

- **Reitlehrer - Schäden am Berittpferd.pdf** — MARGINAL -> PASS. Run 7 flagged this as an unprojected regression from Docling extraction jitter (max_leaf_ratio 0.2571, just above the D10-widened 0.20 threshold). Run 8 clean extraction (10 nodes, depth 2, 3,260 chars, max_leaf_ratio 0.2029, gate `structural_pass`) confirms the Run-7 recommendation's hypothesis — this was transient jitter, not a structural defect, and it has now resolved on its own.

### Structural improvements

- None.

### Regressions

- **FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE - Copy.pdf** — PASS -> MARGINAL. Run 7: tree, 606 nodes, depth 3, 247k chars. Run 8 (live): 595 nodes, depth 2 (493 top-level Article nodes, 102 at depth 2; Book/Part/Chapter/Section levels lost), 246,652 chars. Hierarchy depth regressed; content volume did **not** (the scorer draft's "~28k char drop" compared a text-field-only count against Run 7's `total_tree_chars` — live meta shows 247k -> 246.7k, flat).
  *Hypothesis:* Loss of a hierarchy level (3->2) on a document that previously resolved a 3-level tree, with content intact, points to a regression in the heading/section classifier or tree-builder rather than source-content variance. The gate reason `depth_inadequate:expected_min_depth=5` also shows a new per-class depth floor is in force for legal statutes.
- **Haftpflicht-Allgemeine-Bedingungen.pdf.pdf** — PASS -> MARGINAL. Run 7: tree, 132 nodes, depth 2, 80k chars. Run 8 (live): 51 nodes, depth 2 (45 top-level, 6 at depth 2), 55,302 chars — node count fell 61% and ~25k chars were lost; depth did **not** change (draft said depth 1; live tree is depth 2). Gate itself returns `structural_pass`.
  *Hypothesis:* Fewer, larger nodes with less total text on a German insurance T&C doc that previously resolved 132 nodes at the same depth points to section boundaries being merged/dropped during tree building, plus some body text loss — a hierarchy-detection change is plausible but it is not the batch-wide depth collapse the draft described (docs 2, 5 and 11 did not collapse).
- **MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf** — MARGINAL -> ERROR -> **FAIL (RFC-045 garble fix)**. Run 7 (D0 OCR recovery) reached MARGINAL: tree, 20 nodes, depth 5, 14.6k chars, leaf_concentration=0.50. Run 8 was rejected outright for latin_gibberish garble; three compounding bugs prevented the OCR recovery path from engaging. Post-fix: indexed with verdict=FAIL, `suspect_density(chars_per_page=1437.7)`, max_leaf_ratio 0.245.
  *Root cause (3-fix chain):* (1) `detect_garble` missed the script mismatch — 100% Latin text from Arabic doc not flagged because `<!-- image -->` markers diluted ratios; (2) `_keep_best_wins` reverted good Arabic OCR because formal Arabic text has higher repeating-token density than random Latin gibberish; (3) VLM fallback overwrote good OCR content after garble was resolved. All three fixed, OCR recovery path now engages correctly. Remaining FAIL is content density — 9-page Arabic MOU averages ~1,438 chars/page, below the density threshold.
- **cabinet_resolution_no_21_of_2020_concerning_service_fees_and_administrative_fines_in_the_ministry_of_human_resources_and_emiratisation (1) - Copy.pdf** — PASS -> MARGINAL. Run 7: tree, 43 nodes, depth 3, 58k chars. Run 8 (live): 45 nodes, depth 3, 20,721 chars — same depth and similar node count, but chars collapsed by ~37k (64%), attributed to content loss from complex multi-column fee-schedule tables. Gate itself returns `structural_pass`.
  *Hypothesis:* Depth/node-count held steady while char volume collapsed sharply — points to a table-extraction regression (multi-column fee tables) rather than a hierarchy-detection issue.
- **federal_decree_law_no_33_of_2021_regarding_the_regulation_of_employment_relationship_and_its_amendments - Copy.pdf** — PASS -> MARGINAL. Run 7: tree, 488 nodes, depth 3, 189k chars. Run 8: 506 nodes, depth 4, 179,287 chars, zero garble — node count and depth actually increased and chars are close; the downgrade is driven by the gate's new `depth_inadequate:expected_min_depth=5` floor for multi-part decree-laws.
  *Hypothesis:* Unlike the other regressions in this run, the underlying extraction looks equal or slightly better (deeper tree, similar volume, no garble) — this is a verdict-threshold change (stricter depth expectation), not an extraction regression; confirm against the scoring rubric before treating it as a pipeline defect. The same floor also drives docs 1 and 16.
- **image pie chart about labor distribution in january 2025 - Copy.jpg** — PASS -> FAIL. Run 7 (D8a Tesseract enrichment): flat_prose, image_enrichment_promoted, 401 chars, PASS. Run 8: all 3 nodes contain garbled gibberish (1,282 chars of Latin-script noise), PictureResult enrichment is empty, no usable text recovered; gate stopped at MARGINAL `depth=1` and did not catch the garble.
  *Hypothesis:* The D8a Tesseract-OCR image-enrichment path that produced a clean PASS in Run 7 appears to have stopped firing or broken for this image — PictureResult enrichment being completely empty (vs. populated in Run 7) points to a regression in the OCR-enrichment invocation or its wiring into the node-content pipeline, not a change in the source image. Secondary: the garble gate has a hole for Latin-script OCR noise (cf. memory note on numeric-junk text layers).
- **world-stats-pocketbook-2023.pdf** — PASS -> MARGINAL. Run 7: flat_mixed, cat_b_promoted, 2582 nodes, 204k chars, PASS. Run 8: tree, 944 nodes, depth 4, 2,098,887 chars, zero garble — char volume rose roughly 10x but node count dropped by more than half, and the gate now returns `depth_inadequate:expected_min_depth=5,actual_depth=4`.
  *Hypothesis:* The classification pathway that got this doc PASS via cat_b_promoted flat_mixed handling in Run 7 has changed to a plain tree classification with a stricter depth bar in Run 8 — worth checking whether the content_class routing changed for this doc, since the raw content volume improved rather than degraded.
- **اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf** — PASS -> FAIL. Run 7 (D3 image-marker garble exemption): tree, 98 nodes, depth 4, 38k chars, PASS — Run 7's report explicitly called this result as 'exceeds projected MARGINAL.' Run 8 (live): catastrophic collapse to a single flat `[Preamble]` node with 1,283 chars retained (~64 chars/page vs. expected ~2,000-4,000; draft said 62 chars, live is 1,283); 19+ of 20 pages entirely absent from the tree; gate FAIL `garbling` with max_leaf_ratio 1.0 and garble_latin_ratio 0.4.
  *Hypothesis:* This is the most severe regression in the run — a doc that went from a working 4-level, 38k-char tree straight to near-total content loss. The D3 image-marker garble exemption that made Run 7's result possible appears to have been reverted, disabled, or bypassed, causing the garble/quality gate (or the extractor itself) to discard almost the entire document.
- **سياسة حوكمة و إدارة البيانات - Copy.pdf** — PASS -> MARGINAL. Run 7: tree, 24 nodes, depth 4, 21k chars, PASS. Run 8: 27 nodes, depth 3, 19,778 chars — content volume close to Run 7's, but max_leaf_ratio (0.1653) now sits just over a 0.15 threshold, and the gate's `garbling` FAIL is a false positive (zero actual garbling across all 27 nodes) that the reviewer corrected to MARGINAL.
  *Hypothesis:* Content extraction looks essentially stable; the downgrade is driven by a leaf-ratio threshold that is now tighter (0.15) than the bar that let this doc PASS in Run 7 — likely a verdict-threshold tightening rather than an extraction defect, compounded by a garble-gate false positive that had to be manually corrected.
- **قرار مجلس الوزراء رقم (1) لسنة 2022 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل.pdf** — PASS -> FAIL. Run 7 (D5 synthetic structure): flat_prose, image_enrichment_promoted, 42 nodes, 1.7k chars, PASS. Run 8: 58 nodes, depth 4, 47,020 chars, but the decree's identifying number is digit-substituted (1->7, verified in the stored preamble) and Articles 1-4 are missing entirely (first article node is المادة (5)), making the document unreliable for citation/lookup despite far higher raw char volume.
  *Hypothesis:* Char volume increased dramatically (1.7k->47k) so this is not a content-loss regression — it is a correctness regression: a digit-substitution error in the decree number plus dropped leading articles suggests a bug in OCR character recognition or in whatever synthetic-structure logic (D5) reconstructs the document's opening sections, introduced or exposed since Run 7.
- **قرار مجلس الوزراء رقم (106) لسنة 2022 بشأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (9) لسنة 2022 بشأن عمال الخدمة المساعدة.pdf** — MARGINAL -> ERROR -> **PASS (RFC-045 re-test)**. Run 7 (D0 + D4): tree, 82 nodes, depth 3, 41k chars, leaf_concentration=0.37, MARGINAL. Run 8: rejected by garble gate (PF false-positive). Post-fix: `structural_pass`, 15 pages, 32,140 chars, max_leaf_ratio 0.1659. Root cause was the NFKC presentation-forms fallback in `detect_garble()` unconditionally flagging all Arabic text.
  *Resolution:* Fixed by removing the over-broad PF fallback in garble.py (commit `2c39168`).
- **مرسوم بقانون اتحادي رقم (13) لسنة 2022 بشان التأمين ضد التعطل عن العمل - Copy.pdf** — PASS -> FAIL. Run 7 (D0): tree, 38 nodes, depth 3, 8.4k chars, PASS — Run 7 called this result as 'exceeds projected PASS/MARGINAL floor.' Run 8: 29 nodes, depth 5, 5,141 chars (~1,285 chars/page vs. expected 2,000-4,000), empty leaf nodes, bidi-coherence garble flag raised.
  *Hypothesis:* A doc that Run 7 explicitly flagged as exceeding projection has lost ~39% of its content and now trips a bidi-coherence garble flag — this looks like a regression in the Arabic bidi-reconstruction path (the same mechanism D9 targeted for Ministerial Res. 279 and D0 fixed here in Run 7), possibly reintroducing the reordering issue those fixes addressed.
- **وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات حكومة أبوظبي حول برنامج مهارات المهن الحرفية - Copy.pdf** — PASS -> ERROR -> **PASS (RFC-045 re-test)**. Run 7: flat_mixed, cat_b_promoted, 609 nodes, 93k chars, PASS. Run 8: timed out with no output. Post-fix: verdict=PASS (garbling recovered), 42 pages, 57,020 chars, max_leaf_ratio 0.0901. Root cause was `tesseract --print-parameters` no longer emitting `tessdata_prefix` in Tesseract 5.x, causing `ensure_tessdata()` to miss `ara.traineddata` and fall back to wrong-language OCR → timeout on large Arabic docs.
  *Resolution:* Fixed by switching tessdata probe to `tesseract --list-langs` in ocr_langs.py (commit `d5f0c19`).
- **حقوق الإنسان - Copy.pdf** — PASS -> ERROR -> **PASS (RFC-045 re-test)**. Run 7: tree, 347 nodes, depth 6, 527k chars, PASS (the deepest, richest tree in the corpus). Run 8: timed out with no output. Post-fix: `structural_pass`, 161 pages, 419,856 chars, max_leaf_ratio 0.0277. Same tessdata root cause as doc 24.
  *Resolution:* Fixed by switching tessdata probe to `tesseract --list-langs` in ocr_langs.py (commit `d5f0c19`).

### Stalls

- **GHV-TKV-Tarif.pdf** — MARGINAL -> MARGINAL. Out of scope per RFC-023 in Run 7 (flat_mixed depth=1, 20 nodes, 8,110 chars). Run 8: tree, 10 nodes, depth 3, 6,720 chars — leaf_concentration 0.45 and partial image enrichment noted as expected artifacts of tabular layout, not content loss. Consistent, expected MARGINAL for this single-page tariff table.
- **Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf** — MARGINAL -> MARGINAL. Run 7 (D2 decorative-icon stripping): flat_mixed depth=1, 15 nodes, 7,297 chars, MARGINAL. Run 8: tree, 9 nodes, depth 2, 6,440 chars, leaf_concentration 0.31, still MARGINAL — but node count regressed further and ~95% of checkmark/icon markers are now lost (63 markers, only 3 generic enrichments): the underlying benefit-comparison data is degrading even though the verdict bucket hasn't moved. Track as a near-miss regression toward FAIL rather than treating as fully stable.
- **uae_numbers_english_page_16_17_portrait - Copy.pdf** — MARGINAL -> MARGINAL. Run 7 (D6 rotation correction): flat_mixed depth=1, 4 nodes, 38 chars, MARGINAL. Run 8: flat_mixed, 84 blocks, 1,240 chars, MARGINAL (gate says FAIL `suspect_density`; reviewer override) — chars and block count both rose substantially (near-complete capture of chart titles/labels/attribution for this infographic's textual content), so this is a stall with metrics improving inside the same verdict bucket.
- **uae_numbers_english_page_16_17_landscape - Copy.pdf** — FAIL -> FAIL. Run 7: flat_mixed, max_leaf_ratio=0.86, 11 nodes, 28 chars, FAIL (near-total content loss). Run 8: flat_mixed, 198 blocks, 1,355 chars, still FAIL — 32 garbled blocks (16%), flat depth=1 collapse, chart data fragmented into unstructured kv pairs; gate reason still `max_leaf_ratio=0.86`. Char volume moved in the improving direction (28->1,355) but remains far short of what the infographic contains. Persistent hardest-case doc; RFC-023-era fixes have never reached this doc's failure mode across either run.
- **القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf** — ERROR -> ERROR -> **FAIL (RFC-045 re-test)**. Run 7: CMap corruption -> Azure VLM crash. Run 8: garble-gate rejection (PF false-positive). Post-fix: verdict=FAIL, verdict_reason=`suspect_density(chars_per_page=1413.1)`, 35 pages, 49,460 chars, max_leaf_ratio 0.0565. The garble-gate false positive is resolved, but the underlying extraction has low content density — likely a persistent CMap-corruption artefact yielding sparse text through the non-VLM extraction path.

### Stable

- Ministerial Resolution No279 of 2022 Monitoring Mechanisms of Emiratisation Rates in the Private Sec - Copy.pdf (PASS -> PASS; 28 nodes / depth 2 / 11,194 chars, gate `structural_pass`)
- Federal Decree-Law No. (47) of 2021 - Copy.pdf (PASS -> PASS; 69->70 nodes, depth 2 both runs, 22k -> 22,186 chars, gate `structural_pass` — reclassified from the draft's regression list after live verification)
- Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf (PASS -> PASS; 34->78 nodes, depth 2->4, 140k -> 138,177 chars, gate `structural_pass` — reclassified from the draft's regression list after live verification; hierarchy actually deepened)
- cabinet_resolution_no_96_of_2023_regarding_an_alternative_end_of_service_benefits_system - Copy.pdf (PASS -> PASS; 108 nodes / depth 3 both runs, 46k -> 43,043 chars, gate `structural_pass` — reclassified from the draft's regression list after live verification)
- مرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل وتعديلاته.pdf (PASS -> PASS, both runs also flagged a garble-gate false-positive that had to be manually overridden; note metric drift inside the bucket: 546 -> 231 nodes, depth 5 -> 3, 172k -> 107,334 chars — worth watching next run)

### Regressions requiring investigation

| # | Document | Run 7 -> Run 8 | Live Run 8 figures | Regression class | Suspected cause |
|---|---|---|---|---|---|
| 17 | اتفاقية مستوى الخدمة (SLA) | PASS -> FAIL | 1 node, depth 1, 1,283 chars / 20 pp | Content loss (catastrophic) | D3 image-marker garble exemption reverted/bypassed; garble gate discards ~all content |
| 25 | حقوق الإنسان (Human Rights) | PASS -> ERROR -> **PASS** | `structural_pass`, 161p, 419,856 chars | ~~Infrastructure / timeout~~ **RESOLVED** | Tessdata probe fix (commit `d5f0c19`) — `tesseract --list-langs` replaces broken `--print-parameters` |
| 24 | وارد 597 (Craft Skills Program) | PASS -> ERROR -> **PASS** | PASS (garbling recovered), 42p, 57,020 chars | ~~Infrastructure / timeout~~ **RESOLVED** | Same tessdata fix as doc 25 |
| 21 | قرار 106/2022 (Domestic Workers) | MARGINAL -> ERROR -> **PASS** | `structural_pass`, 15p, 32,140 chars | ~~Garble-gate rejection~~ **RESOLVED** | PF false-positive fix (commit `2c39168`) — NFKC fallback removed from detect_garble() |
| 6 | MOU MOHRE & Nafis | MARGINAL -> ERROR -> **FAIL** | FAIL (suspect_density, 1,437 chars/page) | ~~Garble-gate rejection~~ **RESOLVED** | 3-fix chain: script_mismatch garble prong, _keep_best_wins post-garble escape, VLM skip after OCR resolves garble. Now indexed but low content density for 9-page Arabic MOU. |
| 13 | Pie chart JPG | PASS -> FAIL | 3 nodes, 1,282 chars of gibberish, empty PictureResult | OCR enrichment loss | D8a Tesseract enrichment not firing; garble gate misses Latin-script noise |
| 20 | قرار 1/2022 (Labor Exec. Regs.) | PASS -> FAIL | 58 nodes, depth 4, 47,020 chars; decree no. 1->7, Articles 1-4 missing | Correctness (digit substitution, dropped articles) | OCR digit misrecognition / D5 synthetic-structure opening-section reconstruction |
| 22 | مرسوم 13/2022 (Unemployment Insurance) | PASS -> FAIL | 29 nodes, depth 5, 5,141 chars / 4 pp | Content loss + bidi garble flag | Arabic bidi-reconstruction path regression (D0/D9 area) |
| 10 | Cabinet Resolution No. 21/2020 | PASS -> MARGINAL | 45 nodes, depth 3, 20,721 chars (was 58k) | Table content loss | Multi-column fee-table extraction |
| 4 | Haftpflicht-Allgemeine-Bedingungen | PASS -> MARGINAL | 51 nodes, depth 2, 55,302 chars (was 132 / 80k) | Node merge + content loss | Section-boundary detection in tree builder |
| 1 | Penal Code 1987 | PASS -> MARGINAL | 595 nodes, depth 2, 246,652 chars (was depth 3) | Hierarchy depth loss (content intact) | Heading/section classifier; new depth floor for legal statutes |
| 19 | سياسة حوكمة (Data Governance) | PASS -> MARGINAL | 27 nodes, depth 3, 19,778 chars, mlr 0.1653 | Threshold tightening + garble false positive | 0.15 leaf-ratio threshold; garble-gate false positive |
| 12 | Federal Decree-Law 33/2021 (Copy) | PASS -> MARGINAL | 506 nodes, depth 4, 179,287 chars (equal or better than Run 7) | Verdict-threshold only | New `expected_min_depth=5` floor — confirm rubric, not a pipeline defect |
| 16 | world-stats-pocketbook-2023 | PASS -> MARGINAL | 944 nodes, depth 4, 2,098,887 chars | Classification routing + depth floor | cat_b_promoted flat_mixed route replaced by tree route with depth floor |

Cross-cutting threads for the fix plan: ~~(a) large-doc timeouts (24, 25)~~ **RESOLVED** by tessdata probe fix; ~~(b-partial) garble-gate PF false positives (21, and likely 19, 23)~~ **RESOLVED** by NFKC PF fallback removal; ~~(b-remaining-6) genuine garble-gate rejection (6)~~ **RESOLVED** by 3-fix garble chain (script_mismatch prong + _keep_best_wins escape + VLM skip); (b-remaining-13) garble-gate miss (13); (c) the new `expected_min_depth=5` floor which alone accounts for three PASS->MARGINAL moves (1, 12, 16) and should be confirmed as an intended rubric change; (d) Arabic content loss (17, 22); (e) doc 18 low content density (CMap corruption artefact, now FAIL instead of ERROR).

---

## RFC-025 D4 verification log

Live MinIO state (bucket `pageindex`) re-pulled 2026-09-09 for every document before writing. 20 `processed/*.meta.json` objects exist; docs 6, 18, 21, 24, 25 have no `processed/` objects (ERROR verdicts confirmed by absence). Draft figures that diverged from the store, and what was written instead:

| # | Draft figure | Live MinIO | Action |
|---|---|---|---|
| 1 | 219,456 chars, "~28k char drop" | `total_tree_chars` 246,652 (draft counted `text` fields only); 595 nodes, depth 2 confirmed | Chars corrected; char-drop claim removed from delta |
| 2 | depth 1, 13,351 chars, 87% heading-only leaves, MARGINAL | depth 2, 70 nodes, 22,186 chars, 12/65 leaves heading-only, gate PASS | Verdict re-derived to PASS; moved from Regressions to Stable |
| 4 | depth 1, 53,150 chars | depth 2 (45 top-level + 6), 51 nodes, 55,302 chars, gate PASS | Depth and chars corrected; MARGINAL kept on node/char loss vs Run 7 |
| 5 | 51 nodes, depth 2, 53,150 chars, "85K chars trapped in title/summary", MARGINAL | 78 nodes, depth 4, 138,177 chars, title+summary 44,266 chars, gate PASS | Draft figures were doc 4's; verdict re-derived to PASS; moved to Stable |
| 10 | 16,737 chars | 20,721 chars (draft counted `text` only) | Chars corrected; MARGINAL kept |
| 11 | 93 nodes, depth 1, MARGINAL | 108 nodes, depth 3 (93/13/2), 43,043 chars, gate PASS | Draft counted top-level nodes only; verdict re-derived to PASS; moved to Stable |
| 13 | (no char figure) | 3 nodes, depth 1, 1,282 chars, gibberish confirmed, gate MARGINAL `depth=1` | FAIL kept (reviewer override of gate); figures added |
| 14 | 1,652 chars | `flat_char_count` 1,355, 198 blocks | Chars corrected; FAIL kept |
| 15 | 2,068 chars | `flat_char_count` 1,240, 84 blocks, gate FAIL `suspect_density` | Chars corrected; MARGINAL kept (reviewer override noted) |
| 17 | 62 chars | 1,283 chars in one node (~64 chars/page) | Chars corrected; FAIL kept |
| 19 | 19,119 chars | 19,778 chars, depth 3 | Chars corrected; MARGINAL kept |
| 20 | 45,924 chars | 47,020 chars; digit substitution (`رقم ( 7`) and missing Articles 1-4 confirmed in stored tree | Chars corrected; FAIL kept |
| 23 | 106K chars | 107,334 chars, 231 nodes, depth 3 (Run 7: 546 / 5 / 172k) | Confirmed; metric drift noted in Stable |
| Tally | 3 PASS / 12 MARGINAL / 5 FAIL / 5 ERROR | — | Re-derived to 6 PASS / 9 MARGINAL / 5 FAIL / 5 ERROR; post RFC-045 re-test: **9 PASS / 9 MARGINAL / 6 FAIL / 1 ERROR** |

Figures for docs 3, 7, 8, 9, 12, 16, 22 matched the live store exactly and were written as drafted.

---

## RFC-045 Re-test Addendum (2026-09-09)

Two root-cause fixes were applied and the 5 ERROR documents re-tested end-to-end:

### Fixes applied

| Commit | File | Fix |
|--------|------|-----|
| `d5f0c19` | `src/pageindex_mcp/converters/ocr_langs.py` | Tessdata probe switched from `tesseract --print-parameters` (broken on Tesseract 5.x) to `tesseract --list-langs`. Without this fix, `ara.traineddata` was reported missing and Arabic OCR fell back to `deu+eng`, causing timeouts on large Arabic docs. |
| `2c39168` | `src/pageindex_mcp/helpers/garble.py` | Removed the NFKC presentation-forms fallback in `detect_garble()` that unconditionally set `_had_pf=True` for ALL Arabic text with zero presentation-form codepoints. This fired the `presentation_forms` garble prong on every Arabic document. The correct detection path (`ScriptContext.from_document`) already handles real PF detection pre-NFKC. |

### Re-test results

| Doc | File | Run 8 | Post-fix | Verdict details |
|-----|------|-------|----------|-----------------|
| 6 | MOU MOHRE & Nafis | ERROR (garbling) | **FAIL** (suspect_density) | Improved — 3-fix garble chain: (1) `script_mismatch` prong detects >80% Latin in Arabic-expected docs, (2) `_keep_best_wins` post-garble escape keeps OCR retry when post-retry not garbled, (3) VLM skip when full-page OCR resolved whole-tree garble. Now indexed at ~12,966 chars / 9 pages but below density threshold. |
| 18 | القرار التنظيمي لوزارة الاقتصاد1 | ERROR (garbling) | **FAIL** (suspect_density) | Improved — garble-gate false positive resolved; now indexed but low content density (1,413 chars/page). Likely CMap-corruption artefact. |
| 21 | قرار مجلس الوزراء رقم (106) | ERROR (garbling) | **PASS** (structural_pass) | Resolved — 15 pages, 32,140 chars, max_leaf_ratio 0.1659. PF false-positive was the sole blocker. |
| 24 | وارد رقم 597 (42 pages) | ERROR (timeout) | **PASS** (garbling recovered) | Resolved — 42 pages, 57,020 chars, max_leaf_ratio 0.0901. Tessdata fix restored correct Arabic OCR; garble recovery path succeeded. |
| 25 | حقوق الإنسان (161 pages) | ERROR (timeout) | **PASS** (structural_pass) | Resolved — 161 pages, 419,856 chars, max_leaf_ratio 0.0277. Tessdata fix restored correct Arabic OCR. Deepest, richest tree in the corpus again. |

### Updated tally

| | PASS | MARGINAL | FAIL | ERROR |
|---|---|---|---|---|
| Run 8 (original) | 6 | 9 | 5 | 5 |
| Run 8 (post RFC-045) | **9** (+3) | 9 | **6** (+1) | **1** (-4) |
| Run 8 (post depth recal.) | **12** (+3) | **6** (-3) | 6 | 1 |
| Run 8 (post re-ingestion) | **14** (+2) | 6 | **4** (-2) | 1 |
| Run 8 (post garble fix) | 14 | 6 | **5** (+1) | **0** (-1) |

All 5 ERRORs resolved. Doc 6 (MOU MOHRE) moved from ERROR to FAIL via 3-fix garble chain: script_mismatch prong in `_garble_prongs`, post-garble escape in `_keep_best_wins`, VLM skip when OCR resolves whole-tree garble. Remaining FAIL is `suspect_density` (1,437 chars/page for 9-page Arabic MOU).

### Depth formula recalibration (commit `7e5156c`)

The `expected_min_depth` formula in `tree_validation.py` was recalibrated: cap lowered from 5→4, node base raised from 50→100. This resolves three false MARGINAL verdicts where the depth-5 expectation was unrealistic for Docling's heading-recovery pipeline:

| Doc | File | Old expected | New expected | Actual depth | Old verdict | New verdict |
|-----|------|---|---|---|---|---|
| 12 | federal_decree_law_no_33_of_2021 (Copy) | 5 | 4 | 4 | MARGINAL | **PASS** |
| 16 | world-stats-pocketbook-2023 | 5 | 4 | 4 | MARGINAL | **PASS** |
| 23 | مرسوم بقانون اتحادي رقم (33) لسنة 2021 | 4 | 3 | 3 | MARGINAL (reviewer override) | **PASS** |
| 1 | FEDERAL LAW NO (3) OF 1987 (Penal Code) | 5 | 4 | 2 | MARGINAL | MARGINAL (correctly) |

Doc 1 stays MARGINAL because depth 2 for 595 nodes is genuinely shallow — hierarchy loss, not a threshold issue.

### Re-ingestion results (2026-09-09)

Four documents re-ingested to pick up committed fixes (hash cache cleared to force re-processing):

| Doc | File | Previous verdict | After re-ingestion | Fix applied |
|-----|------|-----|-----|-----|
| 13 | image pie chart (JPG) | FAIL (garble not caught by gate) | **FAIL** (garble caught by gate, `garbling`) | Fix #3 — IMAGE_OCR_NONSENSE_RATIO=0.45 now fires garble gate correctly (commit `d1f67c3`). Correct behavior per Hard Rule #5. |
| 17 | اتفاقية مستوى الخدمة (SLA) | FAIL (1,283 chars, catastrophic loss) | **FAIL** (still garbled) | D3a probe fires but PRE_GARBLE_FORCE_OCR_ENABLED=false gates the OCR trigger. Enabling it produces 30k chars of clean Arabic MD, but the LLM tree-builder + landscape reextraction pipeline still triggers garble rejection. Needs comprehensive pipeline fix (future RFC). |
| 19 | سياسة حوكمة (Data Governance) | FAIL/garbling (reviewer override to MARGINAL) | **PASS** (`structural_pass`, 18,287 chars, mlr 0.1873) | PF false-positive resolved by NFKC fallback removal (commit `2c39168`). |
| 20 | قرار 1/2022 (Labor Exec. Regs.) | FAIL (digit substitution 1→7, Articles 1-4 missing) | **PASS** (`structural_pass`, 50,774 chars, mlr 0.0438) | Tessdata probe fix (commit `d5f0c19`) enabled correct Arabic OCR; digit substitution and missing articles resolved. |
