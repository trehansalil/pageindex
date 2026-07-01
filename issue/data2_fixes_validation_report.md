# Five-Fixes Validation Report — Full 62-File Corpus (Before → After)

**Date:** 2026-06-30
**Method:** `python -m pageindex_mcp.converters_cli <file>` per document (exact arq-worker entry point), fresh ingest after hash-cache clear. Structural metrics read back from persisted MinIO artifacts.
**Corpus:** 27 × `issue/data/` (German T&C) + 35 × `issue/data2/` (Arabic/UAE law, tables, images, xlsx) = 62 files.
**Baseline:** `issue/data2_performance_report.md` — **INTACT 9 / DEGRADED 21 / FAILED 5**.
**Fixes under test:** plan `fizzy-forging-pearl.md` (Fix-1 tail-blob splitter, Fix-2 table fidelity, Fix-3 OCR escalation, Fix-4 xlsx+image adapters, Fix-5 auto-lang + tessdata).

---

## Headline

| Metric | Baseline | After fixes | Δ |
|---|---|---|---|
| Hard failures (no artifact) | 5 | **0** | **−5** ✅ |
| German regression | n/a | **0** | clean ✅ |
| Tail-blobs ≥50k still present | ≥3 named | **5** (incl. 2 newly surfaced) | unchanged ❌ |
| Garble-gate quality escapes | (latent) | **2 identified** | newly exposed ⚠️ |

The system no longer *crashes* on any of the 62 files — every file now produces a queryable artifact. But the two **quality** axes the corpus was designed to stress (deep-hierarchy tail-blobs, and trustworthy OCR/garble rejection) are **not** resolved, and the test surfaced two distinct garble-gate holes.

---

## Per-Fix Verdicts

### Fix-1 — Node-tail-blob hierarchy splitter — ❌ FAILED (0/5)

`split_oversized_leaf_nodes` (helpers.py:474, wired client.py:448 before validate_tree:451) **never fired** on any oversized leaf. All five tail-blobs survive byte-identical:

| Doc | Tail-blob chars | Why it didn't split |
|---|---|---|
| Human Rights law (319,975) | 319,975 | Presentation-form Arabic ﺍﳌـﺎﺩﺓ (U+FExx) — Arabic regex can't match |
| Penal Code Art (9) (236,413) | 236,413 | Latin regex lacks paren form `Article (9)`; markers 458× but inline, splitter line-anchored |
| federal_decree_33 (100,176) | 100,176 | same Latin inline/line-anchor root cause |
| مرسوم 33/2021 (114,387) | 114,387 | logical Arabic المادة present but inline, not line-anchored |
| Exec-Reg 33 Art 6 (52,777) | 52,777 | same Latin inline/line-anchor root cause |

Two compounding defects: (1) `_OVERSIZED_ORDINAL_RE` Latin branch requires a digit immediately after `Art`, so paren forms miss; (2) more fundamentally the splitter is **line-anchored** (`^[ \t]*`, MULTILINE) while Docling demotes post-#9 articles to inline prose. Unit tests pass only because fixtures are synthetic line-anchored. **Remediation is a design change** (inline-split with cross-ref guarding, or fix upstream heading detection), not a regex tweak.

### Fix-2 — Table fidelity — ✅ PARTIAL WIN (2a works; 2b/2c unproven)

- **Fix-2a column-stitch — demonstrably works.** Economic Activities (`1e5e08de`): baseline dropped all ISIC labels + numeric cells; now `row_records` carry full `ISIC 3.1: A-Agriculture; 201612: 23973; 201701: 24137; …` joins. 6 column-slice blocks → 2 stitched. world-stats-pocketbook (`621512a9`) shows 11,552 row_records — plausible but **not spot-verifiable** (artifact truncated; recommend grep on a deep country block).
- **Fix-2b RTL — entirely unvalidated.** All 11 Arabic docs are legal prose; **zero** Arabic tables in the corpus. Ships behind synthetic fixtures only, no field evidence (as the plan flagged).
- **Fix-2c empty-cell flag** — no observable trace in artifact dumps; needs raw-JSON schema check.

### Fix-3 — `force_full_page_ocr` escalation — ⚠️ PARTIAL (correct targeting, mixed outcome)

Fired on exactly 2/62 docs — the two corrupt-CMap مرسوم docs, the intended target.
- **مرسوم 33/2021** (`4a6f61ab`, esc=True): recovered **clean logical Arabic** (proper المادة (1)…(N) structure) → genuine **FAIL → DEGRADED** win. Residual: 114k tail-blob (Fix-1's failure, not Fix-3's).
- **مرسوم 13/2022** (`1db2ad74`, esc=True): OCR produced **Latin mojibake** (`"2022 Aiud (13) pd; goles!"`) that **passed validate_tree and was persisted** — an HR5-adjacent quality escape. Cause: `ara` tessdata absent/unselected → eng-only OCR over Arabic script.

### Fix-4 — Format adapters (.xlsx + image) — ✅ MOSTLY SUCCESS

- **.xlsx fully recovered.** NAS GN Network (`3519203d`): ValueError → flat_mixed with real UAE provider rows. **Residual:** flat TABLE block saturates at ~7 rows; the rest spill to `prose:` blocks (searchable, but not `row_records`).
- **image .jpg recovered-degraded.** Pie chart (`f035ffeb`): Docling's image path got clean Arabic title/caption, but **`escalated_ocr=False` — the Tesseract route never fired**, so numeric wedge data is lost as `<!-- image -->`. uae_numbers PDFs (`27e5dc5f`/`ac02e0b4`) correctly yield 0 tables (pure infographic, no DOM).

### Fix-5 — Auto language detection + tessdata — ⚠️ MIXED / largely ineffective

- **μ33** → `ara` correctly selected, clean Arabic OCR. **Worked.**
- **μ13** → escalated but Latin mojibake → `ara` tessdata missing/unselected. **Failed.**
- **وارد 597** (`1c1bfbab`) → `escalated_ocr=False`; scanned letter never reached OCR at all (see garble-gate hole below). **Never invoked.**

Root issue: **`ara` traineddata is not reliably provisioned** in this environment, and detection isn't reaching the image route. Fix-5 efficacy is coupled to tessdata pre-bake (mirror `DOCLING_ARTIFACTS_PATH` precedent).

---

## Two Garble-Gate Holes Surfaced (HR5-relevant)

1. **Latin-mojibake pass-through** (μ13): garbled Latin OCR has no control/replacement chars, so the gate (keyed on `\x00`/`�`) lets it through.
2. **Numeric-junk pass-through** (وارد 597): corrupt text layer of repeated `1651001429` ×74 is not flagged garbled → OCR escalation **never triggers**, scanned Arabic stays unread.

Both argue the gate needs **low-alpha-ratio / wrong-script / repeated-token** detection, not just control-char ratio.

---

## Recommended Next Steps (priority order)

1. **Pre-bake `ara` tessdata** (Fix-5 dependency) — unblocks Fix-3 Arabic recovery and the image route; without it μ13 and any scanned Arabic stay junk.
2. **Redesign Fix-1** for inline article markers (not line-anchored) + add Latin paren form + presentation-form Arabic normalization (NFKC) — the only path to collapsing the 5 surviving tail-blobs.
3. **Harden the garble gate** with alpha-ratio / script-match / repeated-token signals to close both escapes.
4. **Wire the Tesseract route for image input** (currently dead — Docling handles images but the OCR path Fix-5 gates never fires).
5. **xlsx row-overflow**: lift the flat TABLE block row cap so large sheets stay as `row_records` instead of spilling to prose.
6. **Fix-2b/2c**: collect a real Arabic paginated table before claiming RTL coverage; spot-verify pocketbook deep tables.

---

## Honesty Notes (CLAUDE.md HR1)

No claim here frames these fixes as beating vector RAG on accuracy — all are structure/extraction-fidelity fixes. Fix-2b RTL and Fix-2c are unvalidated (no field evidence). The two garble-gate escapes are flagged, not glossed.
