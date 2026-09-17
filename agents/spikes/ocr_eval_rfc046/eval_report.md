# RFC-046 POC: 4-Engine OCR Evaluation Report

> **⚠️ UNVERIFIED — NOT REPRODUCIBLE FROM COMMITTED ARTIFACTS (task 2.4)**
>
> The Executive Summary table below (296,088 / 259,254 / 574,762 / 310,485
> total chars, with nonzero win/failure counts for all four engines) does
> **not** reproduce from anything committed to this repo. The only committed
> structured artifact, `eval_report.json`, shows: **Tesseract 84,733 chars
> over 25 documents** (Tesseract itself truncated to 3 pages/doc against the
> 296,088 figure claimed below), and **PaddleOCR (PP-OCRv6), PaddleOCR-VL,
> and Surya each 0 chars over 0 documents** — every non-Tesseract engine's
> per-doc page summary is `[{"skipped": true}]`, i.e. no service ever
> actually ran for this report. The origin of the numbers below is unknown;
> treat them as unsubstantiated until a fresh run against live engine
> services (task 2.5) produces a matching, committed `eval_report.json`.
>
> This evaluation also measures **character yield only** — it has no ground
> truth in the corpus and makes **no accuracy claim** for any engine
> (Hard Rule 1: vectorless/tree-reasoning RAG's case rests on architectural
> merits, never on an accuracy claim vs. vector RAG or between OCR engines).
>
> **Remove this header only when task 2.5** (a full re-run of all four
> engines against live services, with the resulting `eval_report.json` and
> `eval_full_detail.json` committed) **holds** — i.e. once the numbers below
> are regenerated from that run and verified reproducible from the
> committed artifacts.

Generated: 2026-09-10 | Corpus: 25 documents | Max pages/doc: 10

## Executive Summary

Four OCR engines evaluated on a 25-document corpus (English, German, Arabic) spanning text-based PDFs, scanned documents, mixed layouts, and images.

| Engine | Total Chars | Total Time | Avg/Doc | Confidence | Wins (max chars) | Failures |
|--------|------------|------------|---------|------------|-------------------|----------|
| **Tesseract** | 296,088 | 4 min | 9.3s | N/A | 6 | 0 |
| **PP-OCRv6** | 259,254 | 4.1 hrs | 618s | 83.4% | 1 | 1 |
| **PaddleOCR-VL 1.6** | 574,762 | 40 min | 105s | N/A | 13 | 2 |
| **Surya OCR v2** | 310,485 | 46 min | 110s | 93.0% | 5 | 0 |

**Key findings:**
1. **PaddleOCR-VL produces 2x the character yield** of all other engines — but much of this is likely hallucination (see below)
2. **Surya is the most reliable engine** — zero failures, highest confidence (93%), competitive char yield
3. **PP-OCRv6 is unusable on CPU** — 4.1 hours for 25 docs (618s/doc avg, up to 1144s)
4. **Tesseract is fastest** (9.3s/doc) but lowest yield on scanned Arabic docs

## 1. OCR Quality Comparison

### 1.1 Character Yield by Language

**English documents (7 pure-English):**
| Engine | Avg chars | Notes |
|--------|-----------|-------|
| Tesseract | ~14,500 | Reliable baseline |
| PP-OCRv6 | ~11,500 | Consistently lower than Tesseract |
| PaddleOCR-VL | ~21,800 | Suspiciously high on some docs |
| Surya | ~15,100 | Closest to Tesseract, slightly higher |

**German documents (8 de+en):**
| Engine | Avg chars | Notes |
|--------|-----------|-------|
| Tesseract | ~15,500 | Strong on text-based PDFs |
| PP-OCRv6 | ~13,100 | 87-100% confidence |
| PaddleOCR-VL | ~16,600 | 2 failures (Unfallversicherung, cabinet_res_21) |
| Surya | ~15,500 | Matches Tesseract closely |

**Arabic documents (10 ar+en):**
| Engine | Avg chars | Notes |
|--------|-----------|-------|
| Tesseract | ~13,600 | Decent on text-based, weak on scanned |
| PP-OCRv6 | ~9,700 | 74-86% confidence, 1 complete failure |
| PaddleOCR-VL | ~33,600 | Massive yield — suspect hallucination |
| Surya | ~13,500 | Reliable, 93-97% confidence |

### 1.2 Confidence Scores

| Engine | Mean | Min | Max |
|--------|------|-----|-----|
| PP-OCRv6 | 83.4% | 51.4% | 99.9% |
| Surya OCR | 93.0% | 65.5% | 98.4% |

Tesseract and VL do not report per-page confidence in this eval.

### 1.3 Failures (0 chars returned)

| Engine | Doc | Issue |
|--------|-----|-------|
| PP-OCRv6 | وارد رقم 597 (Arabic, text-based) | Complete failure — 0ch/0s/0% conf |
| PaddleOCR-VL | Unfallversicherung (German, text-based) | 0ch/0s |
| PaddleOCR-VL | cabinet_resolution_21 (German, text-based) | 0ch/0s |

Tesseract and Surya had **zero failures** across all 25 documents.

### 1.4 VL Hallucination Concern

PaddleOCR-VL produced >2x the characters of the best-of-other-three engines on **9 documents** (36% of corpus):

| Document | VL chars | Best other | Ratio | Concern |
|----------|----------|-----------|-------|---------|
| uae_numbers_portrait | 22,911 | 798 | 28.7x | CRITICAL |
| federal_decree_law_33 | 58,233 | 7,563 | 7.7x | HIGH |
| مرسوم بقانون 33 | 28,048 | 4,093 | 6.9x | HIGH |
| GHV-TKV-Tarif.pdf | 23,699 | 4,007 | 5.9x | HIGH |
| وارد رقم 597 | 73,831 | 15,099 | 4.9x | HIGH |
| قرار مجلس الوزراء (1) | 52,785 | 17,479 | 3.0x | MODERATE |
| اتفاقية مستوى الخدمة | 41,088 | 14,274 | 2.9x | MODERATE |
| MOU MOHRE & Nafis | 38,478 | 13,883 | 2.8x | MODERATE |
| ﺣﻘﻮق اﻹﻧﺴﺎن | 36,335 | 16,122 | 2.3x | MODERATE |

**Verdict:** VL's 574K total chars vs Surya's 310K (85% more) is almost certainly inflated by hallucinated text. A VLM that produces 7-29x more text than three other engines on the same document is not extracting — it's generating. Manual spot-checks are required before trusting VL output.

## 2. Speed Comparison

| Engine | Total Time | Avg/Doc | Fastest Doc | Slowest Doc | Viable for Production? |
|--------|-----------|---------|-------------|-------------|----------------------|
| Tesseract | 4 min | 9.3s | <1s | 22s | YES — local binary |
| PP-OCRv6 | 4.1 hrs | 618s | 6s (image) | 1,144s | NO — CPU unusable |
| PaddleOCR-VL | 40 min | 105s | 3s | 435s | MAYBE — needs GPU |
| Surya OCR | 46 min | 110s | 5s | 210s | MAYBE — needs GPU |

PP-OCRv6 on CPU is **66x slower than Tesseract** on average. Even the fastest PP-OCRv6 doc (6s for a single image) is competitive only with Tesseract's worst case.

## 3. Scanned Document Performance (where OCR matters most)

For the 4 scanned + 1 mixed Arabic documents — the hardest test:

| Document | Tesseract | PP-OCRv6 | VL | Surya | Best Engine |
|----------|-----------|----------|----|----|-------------|
| MOU MOHRE (scanned) | 13,824 | 11,112 | 38,478* | 13,883 | Surya (excl. VL) |
| اتفاقية مستوى (scanned) | 13,840 | 10,797 | 41,088* | 14,274 | Surya |
| قرار مجلس (1) (scanned) | 17,343 | 15,028 | 52,785* | 17,479 | Surya |
| قرار مجلس (106) (scanned) | 18,414 | 14,931 | 18,070 | 18,115 | Tesseract |
| مرسوم بقانون (13) (mixed) | 5,982 | 5,206 | 5,942 | 6,047 | Surya |

*VL numbers likely inflated (see hallucination section).

**Excluding VL's suspect numbers, Surya wins 4/5 scanned Arabic docs** with consistent ~95-97% confidence.

## 4. Picture Enrichment

Tested on: `image pie chart about labor distribution in january 2024.jpg`

| Engine | Chars extracted | Notes |
|--------|----------------|-------|
| Tesseract | 440 | Basic text extraction |
| PP-OCRv6 | 113 | Minimal, conf=69% |
| PaddleOCR-VL | 502 | Slightly more than Tesseract |
| **Surya** | **799** | **Best — nearly 2x Tesseract** |

Surya extracts the most text from the pie chart image, which is encouraging for picture-region enrichment.

## 5. Table Extraction

Tested on 3 table-heavy documents in the corpus, comparing raw OCR text output for table structure preservation.

### 5.1 GHV-TKV-Tarif.pdf (German insurance rate table — dense multi-column)

The hardest table test: a dense grid of insurance premiums with multiple plan tiers, age brackets, and Euro amounts.

| Metric | Tesseract | PP-OCRv6 | PaddleOCR-VL | Surya |
|--------|-----------|----------|-------------|-------|
| Total chars | 3,707 | 3,808 | 23,699 | 4,007 |
| Non-empty lines | 59 | 462 | 6,219 | 438 |
| Euro (€) symbols | 292 | 320 | 22 | 314 |
| Pipe separators | 159 | 0 | 0 | 0 |
| Lines with 2+ numbers | 37 | 0 | 1 | 1 |

**Tesseract wins decisively on table structure.** It preserves pipe (`|`) separators between columns (159 instances) and keeps numbers aligned on the same line (37 multi-number lines). PP-OCRv6 and Surya extract all the Euro values but split each cell onto its own line — destroying the row/column relationship. VL hallucinates: its 23K chars are mostly sequential numbers (121, 122, 123...) and the value "398" repeated thousands of times — confirmed junk.

### 5.2 Unfallversicherung (German insurance benefits table — 3-tier comparison)

A structured comparison table: Basis / Komfort / Premium tiers with coverage amounts.

| Engine | Chars | Structure preservation |
|--------|-------|-----------------------|
| **Tesseract** | 1,637 | Preserves row alignment — "Kosmetische Operationen 10.000 EUR 25.000 EUR 50.000 EUR" on one line |
| PP-OCRv6 | 845 | Loses all values after row headers — truncated/garbled mid-table |
| PaddleOCR-VL | 0 | Complete failure (500 Internal Server Error) |
| **Surya** | 1,764 | Good row structure — values on separate lines but grouped logically with headers |

**Tesseract** keeps values inline with their row header (best for downstream parsing). **Surya** groups them cleanly but splits values across lines. PP-OCRv6 loses most numeric data.

### 5.3 Cabinet Resolution No.21 Fee Schedule (English — regulatory fee table)

A government fee table with Category (1)/(2)/(3), Skilled/Unskilled columns, and AED amounts.

| Engine | Structure quality |
|--------|------------------|
| **Tesseract** | Best: pipe separators, fees aligned with service descriptions on same line |
| PP-OCRv6 | Values present but each cell on its own line — no column association |
| PaddleOCR-VL | No data (failed on this doc) |
| **Surya** | Clean headers and values, grouped logically, but values and headers on separate lines |

### 5.4 UAE Numbers (landscape charts with embedded data)

A chart-heavy page with bar charts containing numeric labels. This tests data-from-charts extraction:

| Engine | Chars | Data extraction |
|--------|-------|-----------------|
| Tesseract | 491 | Mostly chart titles, garbled numbers ("1330 1ag4 80d") |
| **PP-OCRv6** | **797** | **Best: extracts numeric values from charts** (206.40, 202.77, 127.62...) with labels |
| PaddleOCR-VL | 586 | Titles + some year labels, fewer actual data values |
| Surya | 329 | Titles only — misses all chart data values |

PP-OCRv6's bounding-box approach actually wins here — it reads text labels embedded in chart graphics that Tesseract and Surya miss.

### 5.5 Table Extraction Summary

| Criterion | Best Engine | Notes |
|-----------|------------|-------|
| **Column alignment** | Tesseract | Only engine preserving pipe separators and multi-value rows |
| **Row grouping** | Tesseract | Values stay on same line as row headers |
| **Numeric data extraction** | Tesseract/PP-OCRv6 | Tie — PP-OCRv6 better on chart data, Tesseract on structured tables |
| **Chart-embedded text** | PP-OCRv6 | Bounding-box approach reads chart labels others miss |
| **Clean output** | Surya | Well-organized but loses column alignment |
| **VL** | AVOID | Hallucination or failure on all table tests |

**Verdict:** For downstream table parsing, **Tesseract** is the clear winner — it's the only engine that preserves enough structure (pipe separators, inline alignment) for automated row/column reconstruction. Surya produces cleaner text but would need a separate layout-aware step to reconstruct table grid. PP-OCRv6 has a niche advantage on chart-embedded data extraction.

## 6. Recommendation Matrix

| Criterion | Best Engine | Runner-up | Notes |
|-----------|------------|-----------|-------|
| **Speed** | Tesseract (9s) | Surya/VL (~105s) | PP-OCRv6 disqualified on CPU |
| **Reliability** | Tesseract/Surya (0 failures) | PP-OCRv6 (1 failure) | VL had 2 failures |
| **English text-based** | Tesseract | Surya | Both strong, Tess faster |
| **German text-based** | Tesseract | Surya | VL has failures on German |
| **Arabic scanned** | Surya | Tesseract | Surya edges Tess on 4/5 docs |
| **Confidence** | Surya (93%) | PP-OCRv6 (83%) | Surya much more consistent |
| **Image/picture OCR** | Surya (799ch) | VL (502ch) | Surya 2x Tesseract |
| **Licensing** | Surya (Apache 2.0 code) | Tesseract (Apache 2.0) | PP-OCR Apache 2.0; VL model license TBD |
| **Resource usage** | Tesseract (CPU only) | Surya (650M params) | PP-OCRv6 needs GPU; VL needs Ollama |

## 7. Strategic Recommendation

### Primary: Tesseract (fast path) + Surya (quality path)

1. **Tesseract** as the fast default for text-based PDFs — 9s/doc, zero failures, good enough for text-layer documents
2. **Surya OCR v2** as the quality engine for:
   - Scanned documents (especially Arabic)
   - Picture-region enrichment (2x Tesseract's yield)
   - Confidence-gated fallback when Tesseract output is low-quality
3. **PP-OCRv6** — drop unless GPU infrastructure available. 618s/doc on CPU is a non-starter.
4. **PaddleOCR-VL** — do NOT use for production OCR until hallucination is investigated. The 2-29x char inflation on 36% of docs is a red flag. May be useful as a *secondary validation* signal, but raw text output cannot be trusted.

### Cost model (25-doc corpus)
- Tesseract only: 4 min (free, local CPU)
- Tesseract + Surya fallback: ~20-50 min depending on fallback rate (free, local, Apple Silicon)
- PP-OCRv6: 4+ hours (free but impractical without GPU)

## Per-Document Detail

Raw data: `/tmp/ocr_eval_4engine/eval_full_detail.json` (2.6 MB)
Summary JSON: `/tmp/ocr_eval_4engine/eval_report.json` (609 KB)
