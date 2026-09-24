<!-- Space: CITRA -->
<!-- Title: RFC-031: pdf-inspector Shadow-Mode Pilot — Pre-Classification Signal for OCR Routing -->
<!-- Folder: RFCs -->

# RFC-031: pdf-inspector Shadow-Mode Pilot — Pre-Classification Signal for OCR Routing

**Status:** Implementation Complete, Corpus Validation Complete (60 PDFs)
**Audit:** [audit/PDF_INSPECTOR_VIABILITY_REPORT.md](../../audit/PDF_INSPECTOR_VIABILITY_REPORT.md)
**Branch:** `feat/pdf-inspector-shadow-pilot`

## Summary

PageIndex's PDF ingestion pipeline has no upfront PDF classification. OCR triggers
reactively: Docling converts without OCR first (`DOCLING_DO_OCR=0`), and if
`validate_tree()` rejects the result (garbling, low nodes), the **entire document
is reconverted** with `force_full_page_ocr=True`. This doubles conversion time for
any scanned or garbled PDF.

`firecrawl/pdf-inspector` (Rust, MIT, v0.2.6) classifies PDFs as
text_based/scanned/image_based/mixed in ~10-50ms and provides per-page
`pages_needing_ocr`. Integrated as a **shadow-mode classifier** in
`probe_conversion_route()`, it logs classification results and Prometheus metrics
without influencing routing decisions. Promotion to active routing requires ≥99%
agreement with `validate_tree()`'s implicit OCR signal on the German T&C corpus.

## Problem Statement

1. **Reactive OCR wastes wall-clock time.** A 20-page scanned PDF incurs two full
   Docling conversions (~4s + ~4s) when proactive classification could route it to
   OCR on the first pass.
2. **No per-page OCR granularity.** Mixed documents (some pages text, some scanned)
   always get full-document OCR or no OCR — never selective.
3. **No classification telemetry.** The pipeline has no visibility into the PDF type
   distribution of ingested documents.

## Design Decisions

### D0: Add pdf-inspector as optional dependency

**Scope:** `pyproject.toml` optional extra `pdf-inspection = ["pdf-inspector>=0.2.6"]`.
Also added to `dev` extras so CI runs include it.

**Rationale:** Mirrors the `agpl-fallback` pattern — the classifier is useful but
not required. If uninstalled, `_pdf_inspector_available = False` and all
classification fields default to None.

**Files:**
- `pyproject.toml` — new optional extra

**Effort:** Trivial.

---

### D1: Shadow-mode classification in probe_conversion_route()

**Scope:** Extend `probe_conversion_route()` return type from `tuple[int, bool]` to
`tuple[int, bool, dict | None]`. The third element is a classification dict with
`pdf_type`, `confidence`, `pages_needing_ocr`, `has_encoding_issues` — or None when
pdf-inspector is unavailable or classification fails.

**Rationale:** `probe_conversion_route()` already reads the PDF byte stream via
PyMuPDF for page-count estimation. It is the natural insertion point — no new I/O,
no new subprocess, no architectural change. The Rust binary runs in-process with
negligible overhead (~10-50ms vs minutes of Docling conversion).

**Critical constraint — shadow mode:** Classification is computed, logged, and
metered via Prometheus. It **NEVER** influences `chunk_count` or `is_docling_route`.
All routing decisions remain exactly as before. `validate_tree()` remains the sole
quality gate.

**Files / Functions:**
- `src/pageindex_mcp/converters.py :: _run_pdf_inspector()` — new helper, calls
  `detect_pdf(path)`, catches all exceptions, returns dict or None
- `src/pageindex_mcp/converters.py :: probe_conversion_route()` — extended return
  type, calls `_run_pdf_inspector()` before fitz probe
- `src/pageindex_mcp/converters.py :: _pdf_inspector_available` — module-level
  try/except ImportError flag

**Effort:** Small (~2h).

---

### D2: Extended handshake and worker logging

**Scope:** `converters_cli.py` emits `pdf_classification` dict in the handshake JSON
when present. `worker.py` parses it and logs at INFO level.

**Rationale:** The handshake is the existing data channel between converter child
and worker parent. Extending it is the minimal-change path.

**Files / Functions:**
- `src/pageindex_mcp/converters_cli.py :: main()` — conditional
  `handshake["pdf_classification"] = classification`
- `src/pageindex_mcp/worker.py :: _run_converter_subprocess()` — parse
  `handshake.get("pdf_classification")`, log type/confidence/ocr_pages

**Effort:** Small (~1h).

---

### D3: Prometheus observability

**Scope:** Two new metrics:
- `pageindex_pdf_inspector_classifications_total` (Counter, label: `pdf_type`)
- `pageindex_pdf_inspector_latency_seconds` (Histogram, sub-100ms buckets)

**Rationale:** Shadow mode's entire value is observability. Without metrics, the
pilot produces no actionable data.

**Files:**
- `src/pageindex_mcp/metrics.py` — two new metric declarations

**Effort:** Trivial.

---

### D4: Config toggle for future promotion

**Scope:** `PDF_INSPECTOR_PRECLASSIFY` env var (default `"0"` / off). Currently
wired in `config.py` but **not consumed** by any routing logic. Reserved for Phase 2
promotion after corpus validation exit criteria are met.

**Rationale:** The toggle exists so promotion is a config flip, not a code change.

**Files:**
- `src/pageindex_mcp/config.py` — new bool constant

**Effort:** Trivial.

---

### D5: Corpus validation (this phase)

**Scope:** Run `pdf_inspector.detect_pdf(path)` on all available PDFs — 27 German
insurance T&Cs in `issue/data/` and 33 Arabic/English/international documents in
`issue/data2/` (60 total). Collect classification results and compare against
expected outcomes.

**Results (60 PDFs):**
- 55 text_based, 4 scanned, 1 mixed — 0 errors, 0 crashes
- Mean confidence: 0.968 (text_based), 0.950 (scanned), 0.700 (mixed)
- Mean latency: 51.8ms (14.7ms excluding 292-page outlier)
- Arabic RTL/BiDi: 11 docs tested, zero failures
- All promotion exit criteria now met

**Files:**
- `audit/PDF_INSPECTOR_VIABILITY_REPORT.md` — Sections 8 (German) and 9 (Arabic+Intl)

**Effort:** Medium (~3h including analysis and report update).

## Task Breakdown

| Task | Decision | Status |
|---|---|---|
| T0: pyproject.toml optional extra | D0 | ✅ Done |
| T1: `_run_pdf_inspector()` + `probe_conversion_route()` extension | D1 | ✅ Done |
| T2: converters_cli handshake extension | D2 | ✅ Done |
| T3: worker handshake parsing + logging | D2 | ✅ Done |
| T4: Prometheus metrics | D3 | ✅ Done |
| T5: `PDF_INSPECTOR_PRECLASSIFY` config | D4 | ✅ Done |
| T6: Unit tests (18 tests) | D1-D4 | ✅ Done |
| T7: Existing test regression fixes | D1 | ✅ Done |
| T8: Corpus validation — German T&Cs (27 PDFs) | D5 | ✅ Done |
| T9: Corpus validation — Arabic + Intl (33 PDFs) | D5 | ✅ Done |
| T10: Before/after probe comparison | D5 | ✅ Done |
| T11: Audit report update (Sections 8 + 9) | D5 | ✅ Done |

## Promotion Criteria (Phase 2 — future RFC)

Shadow mode promotes to active routing when:
1. ≥99% agreement between `pdf_type=text_based` and `validate_tree()` pass-without-OCR
   on a batch of ≥50 German T&C documents
2. Zero false negatives (scanned PDF classified as text_based, causing garbled tree)
3. pdf-inspector bug #252 (0/1-indexing) resolved for per-page OCR routing

## Non-Goals

- Never use pdf-inspector as markdown extractor (bug #269: `markdown` always None)
- Never test on CJK corpus (bug #272: crashes)
- Never let classification skip `validate_tree()`
- Never treat vendor benchmarks as decision grounds
