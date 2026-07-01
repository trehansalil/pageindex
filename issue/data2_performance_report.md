# PageIndex Performance Report — `issue/data2/` corpus

**Question asked:** does our system's performance *degrade* or stay *intact* when pushed past the German-T&C validation set onto a harder corpus (UAE/Arabic law, scanned images, statistical tables, mixed binaries)?

**Method.** Each of 35 files was run through the live ingestion path (`converters_cli` → `CustomPageIndexClient.index`), twice for scanned/Arabic files (baseline = OCR off, then a re-run with `DOCLING_DO_OCR=1 DOCLING_OCR_LANG=ara,eng`). In parallel, a **Sonnet** agent read each *raw* file and produced independent ground-truth (language, doc_type, hierarchy, tables, source quality, real headings); an **Opus** judge then graded the extracted artifact against that ground-truth. 70 agents, ~1.7M tokens.

---

## Headline verdict

|                        |      INTACT |     DEGRADED |      FAILED |
| ---------------------- | ----------: | -----------: | ----------: |
| **All 35 files** | **9** | **21** | **5** |

The system **does not collapse** on the harder corpus — every clean, text-layer **prose document** ingests without garbling, and 9 land fully intact. But performance **degrades materially** along two axes the German set never stressed: **deep hierarchy** and **tabular/statistical content**. Net: usable extraction on ~86% of files (30/35 produced *some* artifact), faithful structure on far fewer.

### What "DEGRADED" actually means here

The 21 DEGRADED verdicts are **overwhelmingly structure loss, not content loss**. The dominant failure mode (seen in every multi-article law): the first ~5 articles level correctly, then one node swallows the entire rest of the document as a single multi-tens-of-KB text blob —

- Penal Code: Article (9) node = **236,413 chars** (Articles 9–end flattened)
- Cabinet Res. 21/2020: Article 5 node = **42,697 chars** (Articles 6–12 + 4 schedules swallowed)
- Exec. Regulations Decree-Law 33: Article 6 node = **52,777 chars** (Articles 7–39 swallowed)

Content is *present and clean* (faithfulness 60–85%) but the tree stops being navigable. This is the same depth-collapse class noted in prior memory (`depth2-flatprose-outline-class`, `node-count3-hierarchical-overprune`), now reproduced at scale on a new corpus.

---

## Cut 1 — by language (the degradation question's core)

| Language       | INTACT | DEGRADED | FAILED | avg faithfulness |
| -------------- | -----: | -------: | -----: | ---------------: |
| English/German |      6 |       13 |      1 |  **63.2%** |
| Arabic         |      3 |        8 |      4 |  **47.9%** |

**Degradation is real but graceful, not catastrophic.** Arabic costs ~15 faithfulness points and concentrates the hard failures (4 of 5). Critically, Arabic *clean-text* prose still ingests to a tree (e.g. the UN CISG convention → INTACT 90%, data-governance policy → 80%); the Arabic failures are scanned-image or corrupt-text-layer cases, not "Arabic" per se.

## Cut 2 — by source quality

| Source             | INTACT | DEGRADED | FAILED |
| ------------------ | -----: | -------: | -----: |
| clean_text         |      6 |       13 |      2 |
| scanned_image      |      3 |        8 |      2 |
| unsupported_binary |     – |       – |      1 |

Scanned ≠ doomed: several scanned UAE resolutions produced INTACT trees with correct umlauts/English and **zero mojibake even with OCR off** — Docling's layout model recovered the embedded text layer. Scanning hurts mainly when the text layer is *corrupt* (the مرسوم pair) or *infographic-style* (pie charts, stat pages).

## Cut 3 — by document shape (the strongest signal)

| Shape                    | INTACT | DEGRADED | FAILED |
| ------------------------ | -----: | -------: | -----: |
| legal / structured prose |      8 |       15 |      2 |
| statistical / tabular    |      0 |        4 |      3 |
| other                    |      1 |        2 |      0 |

**This is where the system degrades hardest.** Not one statistical/tabular file landed INTACT. The flat-doc router *correctly* declines to invent hierarchy (good), but rendered table bodies are heavily lossy: Economic Activities → 25% (column-header axis kept, every ISIC row label + Grand Total + numeric cells **dropped**); UAE-numbers infographic pages → 25%. Tables are recognized but their **row×column body is not faithfully serialized**.

---

## The 5 hard FAILURES (per-file root cause)

| File                        | Cause                                                                                                               | Recoverable?                 |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| `NAS GN Network …xlsx`   | `.xlsx` not in `_SUPPORTED` → `ValueError` at client.py:263                                                  | Add xlsx adapter             |
| `image pie chart … .jpg` | image format unsupported                                                                                            | Needs VLM/OCR-image route    |
| `وارد 597 …pdf`      | scanned Arabic gov letter, flat_mixed but garbling-flagged, faith 2%                                                | OCR force route              |
| `مرسوم 13/2022`      | **corrupt embedded text layer** (broken font/CMap, e.g. `'t \ t i g;'`), garbling-rejected at client.py:497 | needs`force_full_page_ocr` |
| `مرسوم 33/2021`      | clean but**sparse** Arabic (~213 chars/pg) trips garble ratio                                                 | garble-gate tuning           |

Note: 2 of 5 are simply **unsupported formats** (xlsx/jpg) — a capability gap, not a quality regression. The other 3 are the genuine quality failures.

---

## OCR re-run results (Phase B)

OCR was run on the 6 Arabic scanned/failed files.

| File                                        | baseline   | after OCR      | effect                        |
| ------------------------------------------- | ---------- | -------------- | ----------------------------- |
| MOU MOHRE & Nafis                           | flat_prose | **tree** | **recovered hierarchy** |
| اتفاقية مستوى الخدمة      | flat_prose | **tree** | **recovered**           |
| قرار مجلس الوزراء (1)/2022   | flat_prose | **tree** | **recovered**           |
| قرار مجلس الوزراء (106)/2022 | flat_prose | **tree** | **recovered**           |
| مرسوم 13/2022                          | FAIL       | FAIL           | **no_change**           |
| مرسوم 33/2021                          | FAIL       | FAIL           | **no_change**           |

**Positive signal:** OCR upgraded 4 flat Arabic docs → full hierarchical trees — a genuine recovery the baseline could not reach. **Limit:** OCR did **not** rescue the two outright failures, because Docling will not OCR over an *existing* (corrupt) text layer without `force_full_page_ocr=True`. That single missing flag is the concrete blocker for the مرسوم class.

---

## Bottom line 

- **Performance is intact for the system's core target** (clean-text structured prose, any language): no garbling, faithful content, trees where trees exist.
- **It degrades gracefully on deep hierarchy** — content survives, navigability is lost when one node swallows the document tail. This is the #1 quality issue and is corpus-agnostic (hits English laws too).
- **It degrades hardest on tables/statistics** — recognized but body not serialized; the flat router prevents false hierarchy but doesn't preserve the grid.
- **Hard failures are narrow and explainable:** 2 unsupported formats + 3 scanned/corrupt-text-layer Arabic docs.
- **OCR helps where the text layer is weak (flat→tree), not where it's corrupt** — the corrupt-layer case needs `force_full_page_ocr`.

### Recommended fixes (not yet authorized to implement)

1. **Node-tail-blob splitter** — the highest-leverage fix; restores hierarchy on every multi-article law. Affects English corpus too.
2. **Table body serialization** — emit row×column cells in flat blocks, not just header axis.
3. **`force_full_page_ocr` escalation** when garble-gate fires on a present-but-corrupt text layer (rescues مرسوم class).
4. **Format adapters** for `.xlsx` and image inputs (closes 2/5 failures).
5. Optional: auto-language detection + on-demand tessdata fetch (today `DOCLING_OCR_LANG` is a static env list, no auto-detect).

