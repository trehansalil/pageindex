# RFC-046 POC: Multi-Engine OCR Evaluation Report

Generated: 2026-09-21 16:58:04
Host: Salils-Mac-mini.local (arm64, Darwin 25.6.0)
Max pages/doc: 3 (0 = 9999 cap, not literally unlimited)

> **This report measures character yield, not accuracy.**
> No ground-truth transcription exists for this corpus. "Winner" means
> the engine that produced more characters, which is a necessary but not
> sufficient signal for quality. A high character count from garbled output
> is noise, not accuracy. Interpret with that caveat.

### Engine versions

- **Tesseract:** local (`tesseract --version` at runtime)
- **PaddleOCR:** PP-OCRv5/v6 via service at http://localhost:8202
- **PaddleOCR-VL:** PaddleOCR-VL-1.6-GGUF via Ollama + service at http://localhost:8204
- **Surya:** surya-ocr ≥0.22 via service at http://localhost:8207

## Summary

- **Documents evaluated:** 25
- **PaddleOCR wins (by char yield):** 3
- **Tesseract wins (by char yield):** 22
- **Recommendation:** Tesseract holds advantage or results are mixed — further investigation needed

## PDF Pre-Classification (pdf_inspector)

- Text-based (extractable text layer): **19**
- Scanned (OCR required): **4**
- Mixed (partial text layer): **1**
- Images (non-PDF): **1**

*Scanned/mixed documents are where OCR engine choice matters most.*

## Results by Language

### Arabic (10 documents)

- PaddleOCR wins: 0
- Tesseract wins: 10
- Avg PaddleOCR confidence: 65.04%
- Low-confidence docs (<50%): 1

### German (8 documents)

- PaddleOCR wins: 1
- Tesseract wins: 7
- Avg PaddleOCR confidence: 79.75%
- Low-confidence docs (<50%): 0

### English (25 documents)

- PaddleOCR wins: 3
- Tesseract wins: 22
- Avg PaddleOCR confidence: 71.89%
- Low-confidence docs (<50%): 2


## Per-Document Detail

| Document | Langs | PDF Type | Tess chars | Paddle chars | Paddle conf | VL chars | Surya chars | Surya conf | Winner | VL Winner | Surya Winner |
|----------|-------|----------|-----------|-------------|-------------|---------|------------|------------|--------|-----------|--------------|
| FEDERAL LAW NO (3) OF 1987 ON ISSUANCE OF THE PENAL CODE  -  | en | text_based | 5894 | 4197 | 69.24% | 5835 | 6024 | 97.04% | tesseract | tesseract | other |
| Federal Decree-Law No. (47) of 2021 - Copy.pdf | en | text_based | 3567 | 3301 | 92.77% | 3553 | 3534 | 96.85% | tesseract | tesseract | tesseract |
| GHV-TKV-Tarif.pdf | de+en | text_based | 3707 | 3808 | 99.03% | - | 4007 | 93.71% | other | - | other |
| Haftpflicht-Allgemeine-Bedingungen.pdf.pdf | de+en | text_based | 5423 | 3907 | 54.77% | 5453 | 5362 | 97.88% | tesseract | other | tesseract |
| Haftpflicht-Besondere-Bedingungen-2024-001_01.pdf.pdf | de+en | text_based | 6425 | 3974 | 56.02% | 6302 | 6340 | 97.54% | tesseract | tesseract | tesseract |
| MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1). | ar+en | scanned | 2901 | 2480 | 90.75% | - | 2958 | 95.74% | tesseract | - | other |
| Ministerial Resolution No279 of 2022 Monitoring Mechanisms o | de+en | text_based | 4992 | 3635 | 86.85% | 4993 | 4991 | 97.54% | tesseract | other | tesseract |
| Reitlehrer - Schäden am Berittpferd.pdf | de+en | text_based | 2816 | 2780 | 99.87% | 2896 | 2764 | 97.13% | tesseract | other | tesseract |
| Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf | de+en | text_based | 4922 | 3307 | 81.98% | - | 5068 | 98.29% | tesseract | - | other |
| cabinet_resolution_no_21_of_2020_concerning_service_fees_and | de+en | text_based | 3877 | 3328 | 85.49% | 4070 | 3848 | 97.53% | tesseract | other | tesseract |
| cabinet_resolution_no_96_of_2023_regarding_an_alternative_en | en | text_based | 4297 | 3805 | 86.74% | 4367 | 4267 | 98.53% | tesseract | other | tesseract |
| federal_decree_law_no_33_of_2021_regarding_the_regulation_of | en | text_based | 238 | 175 | 22.13% | - | 230 | 32.64% | tesseract | - | tesseract |
| image pie chart about labor distribution in january 2025 - C | en | image | 440 | 113 | 68.93% | 502 | 799 | 92.94% | tesseract | other | other |
| uae_numbers_english_page_16_17_landscape - Copy.pdf | en | text_based | 491 | 797 | 95.91% | 586 | 329 | 91.37% | other | other | tesseract |
| uae_numbers_english_page_16_17_portrait - Copy.pdf | en | text_based | 594 | 798 | 96.45% | - | 315 | 85.58% | other | - | tesseract |
| world-stats-pocketbook-2023.pdf | en | text_based | 4526 | 2593 | 50.76% | 4867 | 4362 | 97.85% | tesseract | other | tesseract |
| اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من ا | ar+en | scanned | 2900 | 1966 | 67.16% | 2837 | 3003 | 96.37% | tesseract | tesseract | other |
| القرار التنظيمي لوزارة الاقتصاد1 (2) - Copy.pdf | ar+de+en | text_based | 3731 | 2254 | 74.01% | 3918 | 3759 | 95.57% | tesseract | other | other |
| سياسة حوكمة و إدارة البيانات - Copy.pdf | ar+en | text_based | 2500 | 2145 | 81.02% | 2405 | 2678 | 97.98% | tesseract | tesseract | other |
| قرار مجلس الوزراء رقم (1) لسنة 2022 في شأن اللائحة التنفيذية | ar+en | scanned | 4914 | 3363 | 67.08% | 39732 | 4994 | 96.95% | tesseract | other | other |
| قرار مجلس الوزراء رقم (106) لسنة 2022 بشأن اللائحة التنفيذية | ar+en | scanned | 5749 | 4643 | 72.89% | 5456 | 5689 | 96.40% | tesseract | tesseract | tesseract |
| مرسوم بقانون اتحادي رقم (13) لسنة 2022 بشان التأمين ضد التعط | ar+en | mixed | 4796 | 4116 | 81.92% | 4799 | 4816 | 94.02% | tesseract | other | other |
| مرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات الع | ar+en | text_based | 230 | 31 | 1.34% | - | 324 | 32.70% | tesseract | - | other |
| وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات | ar+en | text_based | 4101 | 3051 | 62.80% | - | 4223 | 97.47% | tesseract | - | other |
| ﺣﻘﻮق اﻹﻧﺴﺎن - Copy.pdf | ar+en | text_based | 766 | 376 | 51.46% | 1104 | 1183 | 97.16% | tesseract | other | other |

## Garble Signal Analysis

Documents where PaddleOCR confidence < 50% (potential garble candidates):

- **federal_decree_law_no_33_of_2021_regarding_the_regulation_of_employment_relationship_and_its_amendments - Copy.pdf** — avg conf 22.13%
  - Page 1: conf=0.00%, chars=0, script=empty
  - Page 2: conf=0.00%, chars=0, script=empty
- **مرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل وتعديلاته.pdf** — avg conf 1.34%
  - Page 0: conf=0.00%, chars=0, script=empty
  - Page 1: conf=4.01%, chars=31, script=Latin
  - Page 2: conf=0.00%, chars=0, script=empty