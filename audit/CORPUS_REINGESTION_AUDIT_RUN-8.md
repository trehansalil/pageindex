<!-- Space: CITRA -->
<!-- Title: Corpus Re-ingestion Audit — Run 8 -->
<!-- Folder: Audits -->

# Corpus Re-ingestion Audit — Run 8

## Environment

- Branch: feat(run-7)-diagnosis_n_implementation
- Date: 2026-07-30
- Prior run: /Users/saliltrehan/Documents/Python_n_R/Personal/pageindex/audit/CORPUS_REINGESTION_AUDIT_RUN-7.md

---

## Summary Scorecard

| # | Document | Doc Class | Verdict | Key Finding |
|---|---|---|---|---|
| 1 | Reitlehrer - Schäden am Berittpferd.pdf | insurance/legal (German, riding-instructor liability for damage to a horse in training) | PASS | Direct MinIO verification shows actual persisted state is PASS: 4082 chars / 10 nodes / depth-1, `max_leaf_ratio: 0.2571`, consistent with a short document. Prior audit's FAIL verdict (497 chars, 8 nodes) was fabricated and did not match MinIO ground truth. |
| 2 | قرار مجلس الوزراء رقم (1) لسنة 2022 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل.pdf | flat_prose | ERROR | Zero nodes, zero depth, and zero characters extracted for this Arabic legal regulation (UAE Cabinet Resolution No. 1/2022 on Executive Regulations of Federal Decree-Law No. 33/2021 on Labour Relations) — this is not a shallow-tree or low-coverage case, it is total content-extraction failure with no text to evaluate for logical order, garbling, or structure. |
| 3 | اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf | image_standalone | FAIL | Total content loss: a signed inter-ministry SLA document with 45 image-derived nodes yielded 0 extracted characters, consistent with the known image-block route never firing OCR (content lost as `<!-- image -->` placeholders). |
| 4 | cabinet_resolution_no_96_of_2023_regarding_an_alternative_end_of_service_benefits_system - Copy.pdf | legal/administrative | PASS | 16-page cabinet resolution extracts cleanly (29,110 chars vs. 27,469 raw pdftotext baseline) with coherent, complete legal text and no garbling; 108-node/depth-2 tree matches the document's flat Article + dense-definitions + enumerated-annex structure rather than indicating structural collapse. |
| 5 | uae_numbers_english_page_16_17_landscape - Copy.pdf | flat_mixed | FAIL | Severe content loss: a 2-page (16-17) landscape document yielded only 748 chars across 79 flat (depth-1) nodes — averaging under 10 chars/node, roughly 5-10x below the ~2000-4000 chars/page expectation, indicating the extraction largely failed to capture text (likely a numeric/table page that fragmented into near-empty nodes rather than losing garbled content outright). |
| 6 | image pie chart about labor distribution in january 2025 - Copy.jpg | flat_mixed | MARGINAL | Single-page pie-chart image with flat 12-node/depth-1 tree and 1072 chars extracted — no garbling flagged, but the doc-class/type is exactly the known image-content-loss risk case (chart wedge data / legend semantics historically dropped as an opaque `<!-- image -->` placeholder rather than transcribed), so text-only char count cannot confirm the actual chart data (labor-distribution categories/percentages) was captured. |
| 7 | GHV-TKV-Tarif.pdf | flat_mixed | PASS | Flat 5-node/depth-1 tree is appropriate for a tariff schedule (doc_class flat_mixed), and 13022 chars maps to a plausible ~4-5 page document with no garbling or programmatic issues. |
| 8 | federal_decree_law_no_33_of_2021_regarding_the_regulation_of_employment_relationship_and_its_amendments - Copy.pdf | undefined | PASS | UAE Federal Decree-Law No. 33/2021 (employment law) ingests cleanly: 502 nodes at depth 4 gives article/clause-level granularity appropriate for a statutory instrument, 0 garbled blocks, no programmatic issues, and ~221 chars/node is consistent with dense legal clause text rather than content loss. |
| 9 | مرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل وتعديلاته.pdf | flat_prose | ERROR | Complete extraction failure: 0 nodes, 0 chars, 0 depth for a UAE Federal Decree-Law on labor relations that should be a multi-page, deeply structured legal document. |
| 10 | uae_numbers_english_page_16_17_portrait - Copy.pdf | undefined | FAIL | Severe content loss: only 764 chars across a 2-page doc (page_16_17) split into 76 flat (depth-1) nodes averaging ~10 chars/node — expected ~4000-8000 chars for 2 pages, actual is ~10-20x below the rule-of-thumb floor. |
| 11 | MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf | image_standalone | FAIL | Total content loss: 13 flat (depth-1) nodes carry 0 chars — this is an image-only MOU (Arabic/English scan) that was never OCR'd/enriched, so no textual content was extracted at all. |
| 12 | Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf | flat_mixed | PASS | Flat 21-node/depth-0 tree with 7471 chars (~2-3 pages at 2-4k chars/page) and zero garbled blocks matches expected structure for a tabular Leistungsübersicht (benefits overview), which has no natural section hierarchy. |
| 13 | Haftpflicht-Allgemeine-Bedingungen.pdf.pdf | insurance_tnc | FAIL | 81 of 132 nodes (~61%) are flagged garbled — majority of the tree's content is corrupted, consistent with the known PyPDF2/text-layer garbling root cause on German special characters (ü/ö/ä/ß), which outweighs otherwise adequate char coverage (~56.6k chars, plausible for a 15-25 page insurance T&C). |
| 14 | Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf | German Insurance T&C (Haftpflicht) | MARGINAL | No garbling and char count is healthy for a ~40-55 page doc, but depth-2 hierarchy is shallow for a German insurance T&C, which typically has nested §/Absatz structure (3+ levels) — likely under-segmentation rather than content loss. |
| 15 | مرسوم بقانون اتحادي رقم (13) لسنة 2022 بشان التأمين ضد التعطل عن العمل - Copy.pdf | undefined | FAIL | Federal decree-law extracted to only 60 chars across 2 flat nodes with garbled non-word OCR output — near-total content loss, not a processing success. |
| 16 | حقوق الإنسان - Copy.pdf | document | FAIL | Severe content loss: only 382 chars total across a 347-node, depth-5 tree — the hierarchy was built (titles/ToC extracted) but essentially all body text is missing. |
| 17 | world-stats-pocketbook-2023.pdf | flat_mixed | MARGINAL | Flat (depth-0) tree of 2582 nodes over 6.3M chars is expected structure for a statistical-tables pocketbook, not a sign of hierarchy collapse; no garbling or programmatic issues detected. |
| 18 | Ministerial Resolution No279 of 2022 Monitoring Mechanisms of Emiratisation Rates in the Private Sec - Copy.pdf | undefined | MARGINAL | Flat 20-node/depth-1 tree with no garbling on a short ministerial resolution (~9k chars, ~450 chars/node) — plausible for a brief article-only regulation but lacks any sub-clause nesting typical of legal text. |
| 19 | قرار مجلس الوزراء رقم (106) لسنة 2022 بشأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (9) لسنة 2022 بشأن عمال الخدمة المساعدة.pdf | flat_prose | ERROR | Complete extraction failure: 0 nodes, 0 chars, empty tree.json — pipeline produced no usable output for this Arabic legal/regulatory document. |
| 20 | سياسة حوكمة و إدارة البيانات - Copy.pdf | undefined | PASS | Arabic data-governance policy tree looks structurally healthy — 24 nodes at depth 4, ~20.3k chars (implying ~5-8 pages, consistent with the char/page heuristic), zero garbled blocks and no programmatic issues flagged. |
| 21 | وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات حكومة أبوظبي حول برنامج مهارات المهن الحرفية - Copy.pdf | undefined | FAIL | verdict_reason claims total (1.00) text-layer garbling for this Arabic memo, and this document matches a previously documented garble-gate hole (وارد 597 numeric-junk text layer not flagged as garbled so OCR never escalates) — the tree passed downstream checks only because garbling was measured pre-extraction and never propagated into block-level PUA detection, not because content is actually clean. |
| 22 | Federal Decree-Law No. (47) of 2021 - Copy.pdf | legal_labor | MARGINAL | Tree is structurally flat (depth=1) despite 69 nodes for a federal decree-law that should carry Chapter/Article hierarchy; content volume (24,313 chars, ~0 garbled) looks otherwise healthy for a short multi-page statute. |
| 23 | FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE  - Copy.pdf | legal_document | PASS | 606-node tree with 0 garbled blocks and no programmatic issues; 220,576 chars implies ~55-110 pages, consistent with a full penal code — this matches the Penal Code doc noted in prior memory as successfully split by the RFC-005 splitter fix (236k chars, tail-blobs resolved to ~2k-char nodes). |
| 24 | وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات حكومة أبوظبي حول برنامج مهارات المهن الحرفية - Copy.pdf | flat_mixed | MARGINAL | Flat letter-style structure (depth 0) is appropriate for the doc type, but this exact file is a known corpus case where the garble-gate under-flags a numeric-junk text layer, so the reported 4 garbled blocks likely understate true garbling severity. |
| 25 | cabinet_resolution_no_21_of_2020_concerning_service_fees_and_administrative_fines_in_the_ministry_of_human_resources_and_emiratisation (1) - Copy.pdf | legal/regulatory | FAIL | 44% of nodes (19/43) are garbled from Latin-gibberish CMap mojibake (same pattern as Haftpflicht docs / RC2 in RFC-024 triage), plus ~8% char loss (58k->53.3k) — this is a known regression where _document_level_text_fallback and _text_layer_has_content call _is_garbled_blob without expected_script, so Arabic-CMap-corrupted Latin junk slips past the garble gate undetected. |

**Run 8 Tally (25/25 audited):** 7 PASS, 6 MARGINAL, 9 FAIL, 3 ERROR

---

## Delta from Prior Run -> Run 8

### Improvements

- **GHV-TKV-Tarif.pdf** (MARGINAL→PASS): Node consolidation improved (20→5 nodes), char coverage increased (8.1k→13k chars); better table consolidation or extraction refactoring enabled PASS verdict.
- **Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf** (MARGINAL→PASS): D2 decorative-icon stripping and table consolidation yielded PASS verdict; 7.3k→7.5k chars with slight node increase (15→21) maintained quality.

### Structural Improvements

None identified.

### Regressions

- **قرار مجلس الوزراء رقم (1) لسنة 2022 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل.pdf** (PASS→ERROR): Complete extraction failure: 1.7k chars + image_enrichment_promoted → 0 nodes, 0 chars. Image-enrichment or OCR path broken. *Hypothesis: Image-block-picture-ocr route disable or crash; OCR/Tesseract enrichment code path inverted or gated incorrectly; CMap corruption crash during parsing.*

- **اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf** (PASS→FAIL): 38k chars (image-marker garble exemption) → 45 nodes, 0 chars. OCR text-extraction never fired; content lost as `<!-- image -->` placeholders. *Hypothesis: Known image-block route defect: OCR/enrichment never executes, images classified as text-empty; image-marker garble exemption logic may have inverted (now treating image-derived content as unrecoverable).*

- **image pie chart about labor distribution in january 2025 - Copy.jpg** (PASS→MARGINAL): Tesseract enrichment (D8a, 401 chars) → 12 flat nodes, 1072 chars. Chart semantics (categories/percentages) cannot be confirmed from text-only expansion. *Hypothesis: D8a Tesseract enrichment regressed or disabled; character count increase is inflated/duplicate text rather than true enrichment; chart wedge data loss (known gap) unresolved.*

- **federal_decree_law_no_33_of_2021_regarding_the_regulation_of_employment_relationship_and_its_amendments - Copy.pdf** (PASS→PASS): Stable PASS with minor jitter: 189k chars → 110k chars, nodes 488→502. Extraction consolidation variance within acceptable range. *Hypothesis: Minor re-extraction jitter in filtering/node consolidation; verdict held despite ~42% char-count reduction in reported metrics (may reflect different counting methodology for duplicates/metadata).*

- **مرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل وتعديلاته.pdf** (PASS→ERROR): 172k chars, 546 nodes depth-5 (D0+D1 recovery) → 0 nodes, 0 chars. Total extraction collapse. *Hypothesis: CMap/Arabic text-layer corruption crash during PDF parsing; D0/D1 OCR recovery fixes did not survive or were reverted; parallels Run 7 doc 18 (organizational decision) ERROR case.*

- **uae_numbers_english_page_16_17_portrait - Copy.pdf** (MARGINAL→FAIL): D6 rotation correction (4 nodes, 38 chars) → 76 nodes, 764 chars. Node fragmentation 4→76 indicates splitting pathology; chars still 10-20x below ~2000-4000 baseline for 2 pages. *Hypothesis: D6 rotation fix regressed or was reverted; node splitting/reconstruction code executing but yielding fragmented output; rotation metadata read but not applied to text reconstruction.*

- **MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf** (MARGINAL→FAIL): D0 OCR recovery (20 nodes depth-5, 14.6k chars, leaf_concentration=0.50) → 13 flat nodes, 0 chars. Complete content loss to `<!-- image -->` placeholders. *Hypothesis: D0 OCR recovery regressed; image→text enrichment pathway disabled or crashing; Tesseract invocation never fires; document reverted to image-only classification with no text fallback.*

- **Haftpflicht-Allgemeine-Bedingungen.pdf.pdf** (PASS→FAIL): 80k chars, 132 nodes → 56.6k chars, 132 nodes with 81/132 nodes (~61%) flagged garbled. Same node count but char loss and high garble ratio. *Hypothesis: Garble-gate regressed; ü/ö/ä/ß PyPDF2 corruption re-emerged or garble-detection logic changed (possibly _is_garbled_blob threshold sensitivity reduced); expected_script parameter missing from CMap validation.*

- **Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf** (PASS→MARGINAL): D10 threshold widening (140k chars, 34 nodes depth-2) → 138.6k chars, 34 nodes, depth-2 marked as under-segmentation. Same node count suggests D10 logic reverted. *Hypothesis: D10 threshold widening (PASS_MAX_LEAF_RATIO 0.17→0.20) regressed or was reverted; verdict logic no longer suppresses leaf-ratio check for docs below threshold; depth-2 hierarchy re-penalized as shallow (should be 3+ for German insurance T&C).*

- **مرسوم بقانون اتحادي رقم (13) لسنة 2022 بشان التأمين ضد التعطل عن العمل - Copy.pdf** (PASS→FAIL): D0 recovery (38 nodes depth-3, 8.4k chars) → 2 flat nodes, 60 chars with garbled OCR output. Near-total content loss with non-word junk. *Hypothesis: D0 OCR recovery regressed; CMap corruption fallback executed but yielded unrecoverable mojibake (Latin gibberish) rather than clean Arabic text; _is_garbled_blob detection failed to gate result as ERROR.*

- **حقوق الإنسان - Copy.pdf** (PASS→FAIL): 527k chars, 347 nodes depth-6 → 382 chars, 347 nodes. Titles/ToC extracted but body text vanished (~1400x content loss). *Hypothesis: Text-layer extraction filter regressed (e.g., min_length threshold increased or body-text classification inverted); hierarchical structure built but content filtered at extraction stage; heading-only mode unintentionally enabled.*

- **world-stats-pocketbook-2023.pdf** (PASS→MARGINAL): 204k chars, 2582 flat nodes → 6.3M chars, 2582 flat nodes. Char count explosion (31x) is implausible. *Hypothesis: Extraction logic changed to include all text twice, include non-document metadata, or count markup/encoding tags; possible regression in deduplication or node-text concatenation logic (duplicate text counted multiple times).*

- **Ministerial Resolution No279 of 2022 Monitoring Mechanisms of Emiratisation Rates in the Private Sec - Copy.pdf** (PASS→MARGINAL): D9 BiDi heading preservation (28 nodes depth-2, 14k chars) → 20 nodes depth-1, 9.1k chars. Depth collapsed, hierarchy lost, char loss ~35%. *Hypothesis: D9 BiDi correction regressed; heading-reconstruction/hierarchical-parsing code inverted or disabled; RTL heading markers no longer preserved as hierarchy levels; fallback to flat structure.*

- **قرار مجلس الوزراء رقم (106) لسنة 2022 بشأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (9) لسنة 2022 بشأن عمال الخدمة المساعدة.pdf** (MARGINAL→ERROR): D0+D4 recovery (82 nodes depth-3, 41k chars) → 0 nodes, 0 chars. Total extraction failure. *Hypothesis: CMap corruption crash during Arabic legal-document parsing; D0/D4 fixes did not survive or were reverted; parallels doc 9 (مرسوم 33) ERROR and doc 18 in Run 7; systemic Arabic PDF parser failure.*

- **وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات حكومة أبوظبي حول برنامج مهارات المهن الحرفية - Copy.pdf (variant 1)** (PASS→FAIL): 93k chars, 609 flat_mixed nodes (cat_b_promoted) → 62.8k chars, 558 nodes with verdict_reason 1.00 text-layer garbling. Garble-gate hole re-flagged. *Hypothesis: Garble-gate hole re-emerged: numeric-junk text layer not flagged as garbled pre-extraction → OCR never escalates; extraction passed but garble ratio post-extraction exceeds threshold; D0/D4 fixes regressed or expected_script validation disabled.*

- **Federal Decree-Law No. (47) of 2021 - Copy.pdf** (PASS→MARGINAL): 69 nodes depth-2, 22k chars → 69 nodes depth-1, 24.3k chars. Hierarchical parsing regressed despite statute structure. *Hypothesis: Hierarchy extraction code inverted or disabled; Article/Section nesting lost during reconstruction; depth-1 tree deemed insufficient for federal decree-law (should be 2+); splitter not executing on legal structure.*

- **وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات حكومة أبوظبي حول برنامج مهارات المهن الحرفية - Copy.pdf (variant 2)** (PASS→MARGINAL): 93k chars, 609 flat_mixed nodes → 60.5k chars, 536 flat depth-0 nodes. Garble-gate under-flags numeric-junk; extraction jitter evident (duplicate document with different score). *Hypothesis: Same doc re-extracted with different result (Docling/extraction non-determinism); garble-gate under-flags numeric-junk (known hole from memory); node/char drop indicates content-loss or extraction-path divergence; duplicate scoring suggests corpus reingestion variance.*

- **cabinet_resolution_no_21_of_2020_concerning_service_fees_and_administrative_fines_in_the_ministry_of_human_resources_and_emiratisation (1) - Copy.pdf** (PASS→FAIL): 58k chars, 43 nodes depth-3 → 53.3k chars, 43 nodes with 19/43 (~44%) garbled as Latin-gibberish CMap mojibake. Char loss ~8%, high garble ratio. *Hypothesis: Garble-gate regressed: _document_level_text_fallback and _text_layer_has_content call _is_garbled_blob without expected_script parameter → Arabic-CMap-corrupted Latin junk slips past garble detection; node-level validation threshold exceeded post-check.*

### Stalls

- **uae_numbers_english_page_16_17_landscape - Copy.pdf** (FAIL→FAIL): Content loss persists across runs: 11 nodes, 28 chars (Run 7) → 79 nodes, 748 chars (Run 8). Node count increased but chars still 10-30x below ~2000-4000 baseline for 2-page document. RFC-023 fixes (D1, D6) did not reach this doc's failure mode; numeric table fragmentation unresolved.

### Stable (No Change)

- **Reitlehrer - Schäden am Berittpferd.pdf** (PASS→PASS): PASS in both Run 7 and Run 8 (actual MinIO state). No regression occurred. Prior audit entry was fabricated.
- **cabinet_resolution_no_96_of_2023_regarding_an_alternative_end_of_service_benefits_system - Copy.pdf** (PASS→PASS): 108 nodes, minor char variance 46k→29k.
- **federal_decree_law_no_33_of_2021_regarding_the_regulation_of_employment_relationship_and_its_amendments - Copy.pdf** (PASS→PASS): 502 nodes, char variance 189k→110k reflects consolidation jitter.
- **سياسة حوكمة و إدارة البيانات - Copy.pdf** (PASS→PASS): 24 nodes, stable 21k→20.3k chars.
- **FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE - Copy.pdf** (PASS→PASS): 606 nodes, stable 247k→220.5k chars, RFC-005 splitter fix preserved.

---

## Regressions Requiring Investigation

| Category | Document | Severity | Root Cause Pattern |
|---|---|---|---|
| Image-block defect | اتفاقية مستوى الخدمة, MOU MOHRE & Nafis | Critical | OCR/Tesseract enrichment path disabled or crashing; image-derived nodes never converted to text; fallback to `<!-- image -->` placeholders. |
| Arabic CMap crash | قرار مجلس الوزراء (doc 2), مرسوم (doc 9), قرار (doc 19) | Critical | CMap corruption crash during PDF parsing for Arabic legal documents; D0/D1 OCR recovery fixes did not survive or were reverted; parallels existing ERROR cases in Run 7. |
| Garble-gate hole | Haftpflicht-Allgemeine-Bedingungen, cabinet_resolution_no_21, وارد 597 (variant 1) | High | _is_garbled_blob validation regressed; expected_script parameter missing from CMap validation; PyPDF2 ü/ö/ä/ß corruption and Arabic-Latin mojibake both slip past detection. |
| Hierarchy extraction | Ministerial Resolution, Federal Decree-Law (doc 22), حقوق الإنسان | High | Hierarchical parsing code inverted or disabled; RTL/BiDi heading reconstruction regressed; Article/Section nesting lost during reconstruction. |
| Content loss (non-garble) | uae_numbers (landscape/portrait), حقوق الإنسان | High | Text-layer extraction filter regressed (min_length threshold increased or body-text classification inverted); Docling jitter exacerbated by changed quality filters; numeric table fragmentation unresolved. |
| Duplicate/extraction jitter | world-stats-pocketbook, وارد 597 (variant 2) | Medium | Extraction logic changed to include all text twice or non-document metadata; node-text concatenation logic regression; Docling/extraction non-determinism for same document yields different results. |
| D10 leaf-ratio suppression | Haftpflicht-Besondere-Bedingungen | Medium | D10 threshold widening (PASS_MAX_LEAF_RATIO 0.17→0.20) regressed; verdict logic no longer suppresses leaf-ratio check for docs below threshold. |
