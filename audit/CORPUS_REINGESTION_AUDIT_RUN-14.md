<!-- Space: CITRA -->
<!-- Title: Corpus Re-ingestion Audit — Run 14 -->
<!-- Folder: Audits -->

# Corpus Re-ingestion Audit — Run 14

## Environment

- Branch: feat(run-7)-diagnosis_n_implementation
- Date: 2026-08-05
- Prior run: audit/CORPUS_REINGESTION_AUDIT_RUN-13.md
- Methodology: Incremental ingest+score pipeline (each doc scored immediately after processing)

---

## Pre-publish Verification (RFC-025 D4)

Every per-document figure below was re-pulled from the live MinIO `pageindex` bucket (`processed/{doc_id}.meta.json` + `processed/{doc_id}.json` / `processed/{doc_id}.flat.json`) before writing. Live inventory: **25 persisted docs** — all 25 audited docs are now present (Run 13's 4 ERROR docs, including وارد 597, persisted this run; all processed 2026-08-05). Corrections applied where the source findings diverged from live state:

> 1. **قرار مجلس الوزراء رقم (106)**: the claimed 47,670 chars / 316 nodes is refuted — those are قرار رقم (1)'s live figures. Live flat store for قرار 106 reads **26,146 chars / 160 blocks** (`flat_char_count` exact). Still a dramatic recovery from Run 13's 2,022 chars / 11 nodes; corrected in Scorecard and Improvements.
> 2. **uae_numbers portrait**: the claimed char rise 764 → 1,176 is refuted — live `flat_char_count` reads **764**, byte-identical to Runs 12 and 13; block count stable at 80. Corrected in Structural improvements (this same claim was refuted in Run 13's verification as well).
> 3. **federal_decree_law_no_33_of_2021**: claimed 182k chars re-derives live as **103,081 body-text chars** (193,035 including node summaries + prefix summaries). 502 nodes / depth 4 confirmed exact. Figures corrected in Scorecard.
> 4. **مرسوم بقانون اتحادي رقم (33) لسنة 2021**: claimed 106,631 chars reads **102,018** live (158,813 with summaries); chars_per_node re-derives to **~444** (102,018 / 230), not 463. 230 nodes / depth 3 confirmed. Corrected in Scorecard.
> 5. **وارد رقم 597**: claimed 62,327 chars reads **62,207** live body text (85,861 with summaries); 121 nodes / depth 7 confirmed. The Run 9 garbled-text-layer comparison (62,836 chars) stands. Corrected in Scorecard.
> 6. **Haftpflicht-Besondere**: claimed 175k chars decomposes exactly as body text (133,688) + summaries = **175,638**; body text alone is 133,688. Figure retained with derivation noted. 67 nodes / depth 3 confirmed.
> 7. **cabinet_resolution_no_96_of_2023**: the claimed depth drop 3 → 2 is **refuted** — the live tree reads 108 nodes / **depth 3** / 85 top-level nodes, and 26,111 chars, matching Run 13's live-verified shape exactly. The 85/108 top-level flatness is real but is not new this run; the PASS → MARGINAL downgrade is judge-severity-side, not a structural change. Noted in Regressions.
> 8. **قرار مجلس الوزراء رقم (1)**: 47,670 chars / 316 flat blocks confirmed live. The "0 nodes / 0 markers" finding refers to the hierarchical tree: no `processed/{doc_id}.json` tree exists — only the flat depth-1 store (stored verdict_reason `depth=1`). Content is present; hierarchy is not.
> 9. **ﺣﻘﻮق اﻹﻧﺴﺎن**: 347 nodes / depth 6 / **394,717** chars confirmed live (≈395K as claimed).
> 10. **Exact-match confirmations**: Penal Code 606 nodes / 220,268 chars / depth 3; Decree-Law 47 depth 2 (69 nodes); GHV-TKV 6,033 chars / 4 nodes / depth 2; Haftpflicht-Allgemeine 53,145 chars / 132 nodes / depth 2; MOU 13,541 chars / 134 blocks; Min. Res. 279 7,782 chars / 28 nodes / depth 2; Reitlehrer 2,768 chars / 10 nodes; cab. res. 21 16,748 chars / 43 nodes / depth 3; pie chart 489 chars (byte-identical to Runs 11–13); landscape 748 chars / 78 blocks (byte-identical chars to Runs 12–13); world-stats 6,193,594 chars / 2,591 blocks; SLA 27,801 chars / 219 blocks; القرار التنظيمي 46,675 chars / 90 nodes / depth 5; سياسة حوكمة 24 nodes / depth 4; مرسوم 13 5,934 chars / 70 blocks.
> 11. **Stored vs audit verdicts**: live stored gate verdicts diverge from judge verdicts on many docs — **سياسة حوكمة** and **وارد 597** stored PASS vs judged FAIL; **ﺣﻘﻮق اﻹﻧﺴﺎن**, **cab. res. 96**, **Haftpflicht-Allgemeine**, **Reitlehrer**, **Decree-Law 47**, **مرسوم 13**, **مرسوم 33**, **قرار 106**, **uae_numbers portrait/landscape** stored PASS vs judged MARGINAL; conversely **SLA** and **MOU** stored MARGINAL `garbling(ratio=1.00)` vs judged PASS (confirming the garble-gate false-positive findings). The gate remains blind to the defect classes the judge flags (RTL reversal, hierarchy collapse, enrichment loss) while over-firing on clean Arabic.
> 12. **Depth convention**: narrative depth figures count tree levels (root level = 1). Flat stores are described as depth 1 per the source finding's convention.

---

## Summary Scorecard

| # | Document | Doc Class | Verdict | Key Finding |
|---|---|---|---|---|
| 1 | FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE  - Copy.pdf | unknown | PASS | 77-page UAE Penal Code (English) cleanly extracted: 606 nodes, 220k chars (~2860/page), zero garble, adequate article-level granularity at depth 3. |
| 2 | Federal Decree-Law No. (47) of 2021 - Copy.pdf | unknown | MARGINAL | Reading order defect displaces Article 9 leave sub-clauses after Article 13; depth=2 is shallow for a multi-article legal statute, though content retention is good at ~90%. |
| 3 | GHV-TKV-Tarif.pdf | tariff_table | MARGINAL | Single-page tariff table with adequate char count (6033) but flat tree (4 nodes, depth 2) because _segment_table_nodes is not wired into the primary tree-build path. |
| 4 | Haftpflicht-Allgemeine-Bedingungen.pdf.pdf | unknown | MARGINAL | Depth 2 is too shallow for a structured German insurance T&C document (expected 3-4+); content volume (53k chars, 132 nodes) is adequate but tree hierarchy collapses the legal structure. |
| 5 | Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf | unknown | PASS | 38-page German insurance special conditions extracted cleanly: 175k chars incl. summaries (133,688 body text, ~3.5k/page), 67 nodes at depth 3, no garble, 3 benign decorative image markers. |
| 6 | MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf | flat_mixed | PASS | MARGINAL verdict is a false positive from _MIXED_SCRIPT_RE matching Arabic punctuation as mojibake; 13.5k chars OCR-extracted from 9 scanned pages with 134 clean nodes is acceptable quality for a bilingual MOU. |
| 7 | Ministerial Resolution No279 of 2022 Monitoring Mechanisms of Emiratisation Rates in the Private Sec - Copy.pdf | unknown | PASS | Clean 5-page English ministerial resolution with 99.7% char coverage (7782 chars); 28-node tree at depth 2 is slightly shallow for a 6-article legal text but acceptable given document brevity. |
| 8 | Reitlehrer - Schäden am Berittpferd.pdf | unknown | MARGINAL | 32% char loss (4082->2768) from RFC-029 D3 fence/HR stripping regression; tree structure stable at 10 nodes but content was stripped from flat extraction |
| 9 | Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf | flat_mixed | MARGINAL | Flat depth-1 tree is correct for this 3-page tri-tier benefit comparison table, but 3/3 image blocks missing OCR (likely checkmark/icon data) means tier-inclusion info is lost |
| 10 | cabinet_resolution_no_21_of_2020_concerning_service_fees_and_administrative_fines_in_the_ministry_of_human_resources_and_emiratisation (1) - Copy.pdf | unknown | PASS | 11-page English UAE legal resolution fully extracted (16,748 chars vs 14,342 raw baseline, 117%); 43 nodes at depth 3 adequate for cabinet resolution structure; tab-delimited text is cosmetic only. |
| 11 | cabinet_resolution_no_96_of_2023_regarding_an_alternative_end_of_service_benefits_system - Copy.pdf | unknown | MARGINAL | 16-page English legal resolution has 93% character retention but severely flat tree (85/108 nodes top-level; live depth reads 3, unchanged from Run 13) where Article-clause hierarchy should produce depth 4-5, degrading legal clause retrieval. |
| 12 | federal_decree_law_no_33_of_2021_regarding_the_regulation_of_employment_relationship_and_its_amendments - Copy.pdf | unknown | PASS | UAE labor law with 502 nodes/depth-4 hierarchy and 103k body-text chars (193k incl. summaries, live-verified) is well-structured; 0 garbled blocks confirms clean bilingual extraction; 8 unenriched image markers are decorative headers, not content loss. |
| 13 | image pie chart about labor distribution in january 2025 - Copy.jpg | flat_mixed | MARGINAL | Single pie-chart image: OCR captured 489 chars of labels/numbers but chart segment data is not machine-readable (no PictureResult enrichment); 2 garbled blocks from Arabic OCR artifacts. |
| 14 | uae_numbers_english_page_16_17_landscape - Copy.pdf | unknown | MARGINAL | 2-page landscape infographic yields 748 chars with 71 unpaired kv fragments and lossy OCR (truncated numbers, stray tokens); chart data survives only as unstructured bare values. |
| 15 | uae_numbers_english_page_16_17_portrait - Copy.pdf | flat_mixed | MARGINAL | Chart data shattered into 71/80 single-token kv blocks (years/numbers) with no table consolidation, making structured queries unreliable despite content being present. |
| 16 | world-stats-pocketbook-2023.pdf | flat_mixed | PASS | Flat tree appropriate for statistical pocketbook; 6.2M chars is inflated (~25k/page vs typical 2-4k) suggesting verbose table extraction, but no content loss or garbling. |
| 17 | اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf | flat_mixed | PASS | Garble gate false positive (stored ratio 1.00 vs recomputed 0.067) wrongly penalized clean Arabic SLA; 7/219 flagged blocks are legitimate bilingual fragments, not corruption. |
| 18 | القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf | arabic_legal_regulatory_decree | PASS | 35-page Arabic legal decree: 90 nodes at depth 5 with 46k chars is structurally sound; page-1 header garbling (legacy font) missed by garble gate but body text is largely intact. |
| 19 | سياسة حوكمة و إدارة البيانات - Copy.pdf | Arabic RTL policy/governance document | FAIL | 87.5% of nodes contain garbled orphan-character lines and nearly all section titles are bidi-reversed, making the tree structurally misleading despite reasonable depth/char count -- false-PASS from garble-gate blind spot confirmed. |
| 20 | قرار مجلس الوزراء رقم (1) لسنة 2022 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل.pdf | flat_mixed | MARGINAL | 21-page scanned Arabic legal document: OCR extracted 47k chars (reasonable) but tree has 0 nodes/0 markers — complete structural collapse makes it un-queryable by tree-reasoning engine |
| 21 | قرار مجلس الوزراء رقم (106) لسنة 2022 بشأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (9) لسنة 2022 بشأن عمال الخدمة المساعدة.pdf | flat_mixed | MARGINAL | Structured UAE legal document (articles with numbered clauses and lettered sub-clauses) collapsed to flat depth=1 tree; content extracted cleanly (26,146 chars / 160 blocks live-verified, 0 garble) but hierarchy entirely lost; meta/tree verdict and char-count desync confirms pipeline bug. |
| 22 | مرسوم بقانون اتحادي رقم (13) لسنة 2022 بشان التأمين ضد التعطل عن العمل - Copy.pdf | flat_mixed | MARGINAL | 4-page scanned Arabic legal decree has flat tree (depth 1) where article hierarchy is expected, and 5934 chars (~1484/page) is thin; raw text layer is fully garbled but garble_blocks=0 suggests OCR replaced it or garble gate missed it. |
| 23 | مرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل وتعديلاته.pdf | unknown | MARGINAL | Arabic UAE Labour Law content complete (102,018 chars live-verified, 0 garble) but depth=3 is too shallow for a multi-chapter/70+ article legal decree; chars_per_node ~444 borderline below 500 threshold; node count dropped from 883 to 230 across runs suggesting over-consolidation losing legal hierarchy. |
| 24 | وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات حكومة أبوظبي حول برنامج مهارات المهن الحرفية - Copy.pdf | unknown | FAIL | Garble gate hole: 62,207 chars (live-verified) matches Run 9 garbled text-layer (62,836 chars, ratio=1.00) but garbled_blocks=0 indicates gate is not firing; zero usable Arabic body text across all prior runs confirms extraction failure persists. |
| 25 | ﺣﻘﻮق اﻹﻧﺴﺎن - Copy.pdf | tree | MARGINAL | Good tree structure (347 nodes, depth 6) but 42% garble rate and reversed Arabic headings make navigation broken; content volume intact at 395K chars. |

**Run 14 Tally (25/25 audited):** 9 PASS, 14 MARGINAL, 2 FAIL, 0 ERROR

---

## Delta from Prior Run -> Run 14

### Improvements (9)

- **FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE  - Copy.pdf** — ERROR (low_content_density, chars_per_node=408.2) -> PASS: low_content_density gate no longer trips; 606 nodes / 220k chars extracted cleanly with zero garble and adequate article-level granularity.
- **MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf** — FAIL (89% content loss, garble-gate misfire on clean Arabic) -> PASS: The Run13 garble-gate false positive (ratio=1.00 with 0 actual garbled sequences) is now recognized as a false positive rather than driving a FAIL; content volume restored to 13.5k chars / 134 nodes.
- **federal_decree_law_no_33_of_2021_regarding_the_regulation_of_employment_relationship_and_its_amendments - Copy.pdf** — ERROR (low_content_density, chars_per_node=54.3) -> PASS: low_content_density gate no longer trips; now produces a well-structured 502-node / depth-4 tree with 103k body-text chars (193k incl. summaries) and 0 garbled blocks.
- **image pie chart about labor distribution in january 2025 - Copy.jpg** — FAIL (RTL garbling undetected, chart values unstructured, likely number truncation) -> MARGINAL: OCR now captures 489 chars of labels/numbers as usable text; upgraded from unreliable-output FAIL to MARGINAL, though chart segment data is still not machine-readable and 2 garbled Arabic OCR artifacts remain.
- **اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf** — FAIL (total extraction failure, 0 content, silently-empty tree) -> PASS: Pipeline now produces 219 nodes / 27,801 chars; the prior garble-gate false positive (stored ratio 1.00 vs recomputed 0.067) that was wrongly penalizing this clean Arabic SLA is corrected.
- **قرار مجلس الوزراء رقم (106) لسنة 2022 بشأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (9) لسنة 2022 بشأن عمال الخدمة المساعدة.pdf** — FAIL (~95% content loss, 2,022 chars / 11 nodes, truncated extraction) -> MARGINAL: Content volume recovered dramatically to 26,146 chars / 160 blocks (live-verified; 0 garble); tree still collapses to flat depth=1, losing article/sub-clause hierarchy, so it lands at MARGINAL rather than PASS.
- **مرسوم بقانون اتحادي رقم (13) لسنة 2022 بشان التأمين ضد التعطل عن العمل - Copy.pdf** — FAIL (complete extraction failure, 0 nodes / 0 chars) -> MARGINAL: Extraction now recovers 70 nodes / 5,934 chars instead of nothing, though the tree remains flat (depth 1) and content is thin for a 4-page decree.
- **مرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل وتعديلاته.pdf** — ERROR (low_content_density, chars_per_node=459.4) -> MARGINAL: low_content_density gate no longer trips; 102,018 chars / 230 nodes extracted with 0 garble, though depth=3 is still shallow for a multi-chapter 70+ article decree and node count dropped from a prior 883, suggesting over-consolidation.
- **وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات حكومة أبوظبي حول برنامج مهارات المهن الحرفية - Copy.pdf** — ERROR (never persisted — pipeline timed out, no meta.json written) -> FAIL: Pipeline now completes and persists output (121 nodes / 62,207 chars, live-verified) instead of timing out silently, but quality is still FAIL: the same known Run 9 garbled text-layer content (62,836 chars, ratio=1.00) is present and the garble gate still fails to fire (garbled_blocks=0).

### Structural improvements (1)

- **uae_numbers_english_page_16_17_portrait - Copy.pdf** — Verdict held at MARGINAL. The claimed ~54% char growth (764 -> 1,176) is **refuted by live state**: `flat_char_count` reads 764, byte-identical to Runs 12 and 13, with block count stable at 80. Core fragmentation issue (chart data shattered into single-token kv blocks, 71/80 unpaired) persists — no measurable structural or content movement this run.

### Regressions (4)

- **cabinet_resolution_no_96_of_2023_regarding_an_alternative_end_of_service_benefits_system - Copy.pdf** — PASS (108 nodes, depth 3, 93% char retention, no garbling) -> MARGINAL: Verdict downgraded despite stable char count (26,111 live-verified) because of hierarchy flatness (85/108 nodes top-level), degrading legal clause retrieval. *Verification caveat: the claimed depth drop 3 -> 2 is refuted — the live tree reads depth 3, shape-identical to Run 13's live-verified store. The downgrade is judge-severity-side (flatness now scored more harshly), not a structural change; the tree-consolidation hypothesis (RFC-029/030 splitter or chars_per_node threshold side effect) is therefore unsupported for this document.*
- **سياسة حوكمة و إدارة البيانات - Copy.pdf** — MARGINAL (23/24 node titles bidi-reversed, body text correct) -> FAIL: The garble/reversal defect that was title-only in Run 13 is now reported as pervasive across node bodies (87.5% of the 24 live nodes contain garbled orphan-character lines). Hypothesis: an RTL/bidi normalization regression (or a garble-gate scope change that now also flags genuine new corruption) introduced alongside the RFC-029/030 Arabic-handling changes — needs a direct byte-level diff against the Run 13 stored tree to confirm whether body text actually got worse or the judge is scoring the same defect more strictly. Stored gate verdict remains PASS — the blind spot persists.
- **ﺣﻘﻮق اﻹﻧﺴﺎن - Copy.pdf** — PASS (347 nodes, depth 6, 420k chars, 0 garble) -> MARGINAL: Newly-detected garble (42%) and reversed Arabic headings undermine navigation despite intact content volume (394,717 chars, 347 nodes / depth 6 live-verified — tree shape essentially unchanged from Run 13). Hypothesis: either a regression in RTL/bidi handling for this large Arabic document introduced by recent RFC-029/030 changes, or a garble-gate/judge sensitivity change that now correctly surfaces a defect the Run 13 pass missed (consistent with the garble-gate blind-spot pattern seen at doc #24). Needs direct verification against the stored tree to distinguish a real extraction regression from a detection-only change.
- **Reitlehrer - Schäden am Berittpferd.pdf** — MARGINAL -> MARGINAL (verdict-stable content regression): 10 nodes / 2,768 chars (live-verified) vs prior 4,082 — a 32% char loss explicitly attributed to the RFC-029 D3 fence/HR stripping regression, which appears to be over-stripping legitimate content on this short flat document. Flagged separately since verdict-only tracking would hide this.

### Regressions requiring investigation

| Document | Change | Live-verified state | Investigation lead |
|---|---|---|---|
| cabinet_resolution_no_96_of_2023 … - Copy.pdf | PASS -> MARGINAL | 108 nodes / depth 3 / 85 top-level / 26,111 chars — shape identical to Run 13 | Confirm judge-side severity shift vs any real consolidation; claimed depth 3 -> 2 drop refuted by live tree |
| سياسة حوكمة و إدارة البيانات - Copy.pdf | MARGINAL -> FAIL | 24 nodes / depth 4 / 17,575 chars; stored gate verdict PASS | Byte-level diff of node bodies vs Run 13 tree: did garble spread from titles into bodies, or is scoring stricter? RFC-029/030 RTL changes prime suspect |
| ﺣﻘﻮق اﻹﻧﺴﺎن - Copy.pdf | PASS -> MARGINAL | 347 nodes / depth 6 / 394,717 chars — shape unchanged | Distinguish real RTL/bidi extraction regression from detection-only change (42% garble on a doc that scored 0 in Run 13) |
| Reitlehrer - Schäden am Berittpferd.pdf | MARGINAL -> MARGINAL (chars 4,082 -> 2,768) | 10 nodes / 2,768 chars live | RFC-029 D3 fence/HR stripping over-strips legitimate content on short flat docs |

### Stalls (6)

- **Federal Decree-Law No. (47) of 2021 - Copy.pdf** — MARGINAL -> MARGINAL: Same unresolved defects: Article 9 sub-clause reading-order displacement and shallow depth=2 tree for a multi-article statute persist unchanged.
- **GHV-TKV-Tarif.pdf** — MARGINAL -> MARGINAL: Same unresolved defect: _segment_table_nodes still not wired into the primary tree-build path, so the tariff table remains a single flat node (4 nodes, depth 2) despite adequate char count.
- **Haftpflicht-Allgemeine-Bedingungen.pdf.pdf** — MARGINAL -> MARGINAL: Same unresolved defect: depth-2 tree remains too shallow for this 32-clause German AHB; content volume stable (~53k chars/132 nodes) but hierarchy still collapsed.
- **Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf** — MARGINAL -> MARGINAL: Same unresolved defect: image/table cells (checkmarks/icons) still missing OCR, losing tier-inclusion data in the benefit-comparison table.
- **uae_numbers_english_page_16_17_landscape - Copy.pdf** — MARGINAL -> MARGINAL: Char count byte-identical to prior run (748, live-verified) with the same core defect: zero picture/chart enrichment and lossy, fragmented OCR of chart values.
- **قرار مجلس الوزراء رقم (1) لسنة 2022 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل.pdf** — MARGINAL -> MARGINAL: Still un-queryable by the tree-reasoning engine, but the failure mode shifted: Run 13 was content-loss dominant (14.8k chars, ~69% below baseline) whereas Run 14 recovered content volume (47,670 chars live-verified, close to the ~48k baseline) but now shows complete structural collapse (no hierarchical tree persisted — flat depth-1 store of 316 blocks only, 0 tree nodes/markers).

### Stable (5)

- **Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf** — PASS -> PASS
- **Ministerial Resolution No279 of 2022 Monitoring Mechanisms of Emiratisation Rates in the Private Sec - Copy.pdf** — PASS -> PASS
- **cabinet_resolution_no_21_of_2020_concerning_service_fees_and_administrative_fines_in_the_ministry_of_human_resources_and_emiratisation (1) - Copy.pdf** — PASS -> PASS
- **world-stats-pocketbook-2023.pdf** — PASS -> PASS
- **القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf** — PASS -> PASS
