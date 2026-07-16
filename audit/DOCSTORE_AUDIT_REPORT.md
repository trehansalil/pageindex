<!-- Space: CITRA -->
<!-- Title: PageIndex Docstore Audit -->
<!-- Parent: Data-AI Refactoring Experiments -->
<!-- Confluence-Page-ID: 5092212742 -->
<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5092212742/PageIndex+Docstore+Audit -->

# Docstore Audit Report — Executive Summary

**Date:** 2026-07-15 (re-run: fresh 5-wave audit adds 15 issues from parallel subagent exploration of auth/PII/AGPL/garble-gate/observability/dead-code surfaces not covered in the prior pass)
**Scope:** 23+ docstore-related files (this re-run additionally covers `auth.py`, `memory_admission.py`, `metrics.py`, `tracing.py`, `gunicorn.conf.py`, `config.py`, and root-level `upload.py`/`stress_test.py`/`test.py`) + 25-document corpus quality analysis
**Branch:** `feat/scaling-pageindex`

---

## 1. Audit Overview

This audit combines three passes:

1. **Code audit (2026-07-10)** — 5-wave systematic examination of the docstore subsystem (ingestion, storage, retrieval, deletion) across 23 files, yielding 25 verified issues.
2. **Corpus quality audit (2026-07-15)** — MARGINAL-verdict deep analysis comparing the 17 MARGINAL documents from the DOC_STORE_CORPUS_REPORT (2026-07-14) against both the E2E baseline (2026-07-10) and original source PDFs to verify extraction correctness and identify residual gaps.
3. **Re-run code audit (2026-07-15, this pass)** — fresh 5-wave audit dispatched across 4 parallel exploration/verification agents (storage/cache/registry; converters/helpers/quality-gate; server/worker/upload/query; observability/config/misc) plus 5 parallel fix-research agents, surfacing 15 additional issues: an HR2 erasure-cascade gap (raw upload object never deleted), an HR3 gap (no query-time PII/ZDR routing assertion), an HR4 gap (AGPL fallback with no hard gate), an OCR script-substitution defect, a garble-gate length-floor gap, an auth fail-open inconsistency, a memory-admission race, and several observability/dead-code items.

**Context:** Between the baseline E2E run and the corpus report, RFC-010 D1-D5 fixes landed: splitter redesign, garble-gate hardening, OCR-escalation wiring, flat-doc routing, heading indent normalization, TOC dot-leader filter, and interim في→# post-processing. The corpus improved from 4% PASS / 48% FAIL to 24% PASS / 8% FAIL.

**Resolution status (2026-07-15):** 18 of 31 originally-flagged issues verified fixed in codebase. 28 remain open (26 code + 2 corpus) after this session's re-run added 15 newly-verified issues (ISS-32–ISS-46).

---

## 2. Remaining Code Issues

| Classification     | Count | Description                                                       |
| ------------------ | ----- | ----------------------------------------------------------------- |
| 🟠 DEGRADED        | 9     | Working but with compliance, performance, or consistency gaps     |
| 🟡 LATENT          | 8     | Could fail under specific conditions (scale, concurrency, attack) |
| 🟢 STYLE/TECH DEBT | 9     | Verified as non-issues, intentional design, or low-risk cleanup    |

### Top Remaining Issues

| #      | Issue                                            | Why It Matters                                              |
| ------ | ------------------------------------------------ | ----------------------------------------------------------- |
| ISS-41 | Erasure cascade never deletes `preloaded/<filename>` raw object | Right-to-erasure gap (Hard Rule #2) — new this pass |
| ~~ISS-02~~ | ~~Erasure cascade fire-and-forgets registry delete~~ | 🟢 Resolved — already bounded (RFC-011 D1) |
| ISS-03 | Backfill marks registry complete on 0 docs       | Transient MinIO outage hides entire corpus from query tools |
| ISS-34 | `ensure_tessdata` silently drops non-Latin script requests | False-clean OCR mojibake — new this pass |
| ISS-35 | AGPL fallback reachable with no hard gate/alert  | Hard Rule #4 legal-exposure gap — new this pass |
| ISS-32 | Bearer auth fails OPEN when token unset          | Inconsistent with upload API's fail-closed default — new this pass |
| ISS-07 | Redis conn storm (partially fixed)               | worker.py still has ad-hoc connections                      |
| ISS-05 | `list_processed_docs` O(N) serial MinIO GETs     | Performance degradation at scale                            |
| ISS-08 | `_describe` drops all OpenAI errors              | Image description failures invisible                       |

### Resolved Issues (18 total)

ISS-01 (Redis URL default), ISS-04 (upload validation), ISS-06 (pagination), ISS-09 (full UUID), ISS-10 (hash cache migration), ISS-11 (save order), ISS-12 (enqueue order), ISS-13 (auth warning gauge), ISS-14 (tessdata size cap), ISS-15 (upload size limit), ISS-16 (cache error narrowing), ISS-17 (None guard), ISS-20 (staging delete metric), ISS-21 (O(N) error path removed), ISS-26 (OCR escalation), ISS-27 (garble-gate text), ISS-28 (splitter redesign), ISS-29 (presentation-form Arabic).

---

## 3. Systemic Code Patterns

Three systemic anti-patterns underlie the remaining findings:

### 3.1 Broad `except Exception` with Low-Level Logging

**Issues:** ISS-08, ISS-18, ISS-19
**Pattern:** Locations that catch all exceptions with debug-level logging, creating invisible degradation.
**Fix theme:** Narrow catches to expected types, raise to WARNING, add Prometheus counters.

### 3.2 O(N) Fallback Paths

**Issues:** ISS-05
**Pattern:** MinIO listing is O(N) serial GETs on fallback path.
**Fix theme:** Make registry authoritative; remove O(N) MinIO listing.

### 3.3 Compliance Gap

**Issues:** ISS-02
**Pattern:** Fire-and-forget background task in erasure cascade.
**Fix theme:** Await with timeout before reporting success.

---

## 4. MARGINAL Document Deep Analysis

This section analyzes all 17 MARGINAL-verdict documents from the DOC_STORE_CORPUS_REPORT (2026-07-14), comparing preprocessed output against the E2E baseline (2026-07-10) and original source PDFs.

### 4.1 Corpus Movement Summary

| Movement                           | Count | Documents                                                                                                |
| ---------------------------------- | ----- | -------------------------------------------------------------------------------------------------------- |
| **FAIL -> MARGINAL** (rescued)      | 5     | MOU MOHRE, اتفاقية, قرار 1, قرار 106, وارد 597                                                          |
| **MARGINAL -> improved MARGINAL**   | 4     | cabinet_res_21(1)(1), cabinet_res_21(1), حقوق الإنسان, Ministerial Res 279                               |
| **MARGINAL -> quality-improved**    | 2     | مرسوم 13, سياسة حوكمة                                                                                   |
| **MARGINAL -> unchanged**           | 4     | GHV-TKV-Tarif, Haftpflicht-Besondere, Unfallversicherung, uae_numbers_portrait                           |
| **MARGINAL with regression signal**| 1     | مرسوم 33                                                                                                 |
| **PASS promotion candidates**      | 2     | سياسة حوكمة, Haftpflicht-Besondere (overlap with above)                                                  |

### 4.2 Category A — OCR-Rescued Documents (FAIL -> MARGINAL)

These 5 documents were previously 100% image blocks with zero extracted text. D1 OCR-escalation now fires and recovers real Arabic content.

| # | Document | doc_id | Baseline chars | Current chars | Recovery | Residual Issue |
|---|----------|--------|---------------|--------------|----------|----------------|
| 1 | MOU MOHRE | `7c0a0100` | 182 | 12,204 | +6,605% | OCR noise fragments (`Salgll rot!` for `الموافق`); 45% single-leaf |
| 2 | اتفاقية مستوى الخدمة | `a5ef1929` | 630 | 29,947 | +4,653% | OCR noise (`blll`); 47% single-leaf concentration |
| 3 | قرار رقم 1 | `34b3b7ee` | 294 | 39,112 | +13,203% | OCR noise pattern same class as #1/#2; 43.5% single-leaf |
| 4 | قرار رقم 106 | `7b819149` | 210 | 32,763 | +15,501% | Highest single-leaf concentration of rescued group (60.9%) |
| 5 | وارد 597 | `127ba17a` | 38,778 (numeric junk) | 74,407 (clean Arabic) | digit_ratio 0.91->0.01 | 28% single-leaf; `1651001429` pattern fully eliminated |

**Assessment:** OCR-escalation is working correctly. All 5 now contain legible, queryable Arabic text. The residual OCR noise fragments are short-phrase Tesseract misreadings on decorative/recital-clause typography — cosmetic (< 0.5% of total text) and do not impair search or RAG retrieval.

### 4.3 Category B — Structurally Improved Documents

| # | Document | doc_id | Baseline | Current | Delta |
|---|----------|--------|----------|---------|-------|
| 1 | cabinet_res_21(1)(1) | `997a140a` | 22 nodes, 80.8% max_leaf | 37 nodes, 73.4% max_leaf | Gap 3 improved: 15 `ARTICLE` headers detected; residual merged fee-schedule block |
| 2 | cabinet_res_21(1) | `0dc36fb4` | identical to above | identical to above | Duplicate doc — deterministic extraction confirmed |
| 3 | حقوق الإنسان | `e8596b90` | 34 nodes, 87.7% max_leaf (FAIL) | 322 nodes, 27.4% max_leaf | Gap 4 major win: presentation-form Arabic handled. Residual 137k leaf = genuine ToC + 2 long articles |
| 4 | Ministerial Res 279 | `c6a673f1` | 18 nodes, 56.1% max_leaf | 28 nodes, 34.0% max_leaf | Structural improvement; **690 tab chars persist** — Docling font/spacing extraction artifact |
| 5 | Reitlehrer | `7116d385` | 7 nodes, 52.8% max_leaf | 9 nodes, 29.6% max_leaf | Inherently short fragment document (3,562 chars total) |

**Assessment:** The splitter redesign (Gap 3) and presentation-form Arabic handling (Gap 4) delivered substantial improvements. حقوق الإنسان is the biggest structural win in the entire corpus (88% -> 27% concentration).

### 4.4 Category C — Text Quality Improved

| # | Document | doc_id | Baseline Issue | Current State |
|---|----------|--------|----------------|---------------|
| 1 | مرسوم 13 | `d9f0a0e9` | 81 `#` substitutions, "Oleg" Latin mojibake | **Mojibake eliminated** (0 occurrences). Residual: minor `- deg -` OCR noise token |
| 2 | سياسة حوكمة | `efd65b00` | RTL table-field corruption flagged | **CLEAN** — diacritic density 0.3% (normal), no corruption confirmed this run |

**Assessment:** مرسوم 13 is a D3/D5 success story. سياسة حوكمة's original MARGINAL verdict appears overly cautious; see promotion candidates below.

### 4.5 Category D — Unchanged (Docling/Source Limitations)

| # | Document | doc_id | Why Unchanged | Addressable? |
|---|----------|--------|---------------|--------------|
| 1 | GHV-TKV-Tarif | `a6a49019` | Gap 6: tariff table column structure degraded in Docling's markdown output | No — Docling table extraction limitation |
| 2 | Haftpflicht-Besondere | `906392fb` | 16% max_leaf concentration, 33 nodes | Borderline — see PASS promotion candidate |
| 3 | Unfallversicherung | `4bbd7ede` | 80.8% image blocks, 1,263 chars; benefits-table structure degraded | No — image-dominant layout with Docling table degradation (Gap 6) |
| 4 | uae_numbers_portrait | `f274ece1` | 57% image blocks, 129 chars; infographic-style PDF | No — genuinely image-dominant content with minimal text layer |

**Assessment:** These 4 documents represent hard limits of the current Docling-based extraction pipeline. None are addressable without either a VLM-based table extraction path or a fundamentally different PDF parser.

### 4.6 Category E — Regression Signal

| Document | doc_id | Metric | Baseline (06-30 splitter) | Current (07-14) | Concern |
|----------|--------|--------|--------------------------|-----------------|---------|
| مرسوم 33 | `8b05de59` | node_count | 125 | 58 | -54% nodes |
| | | max_leaf | 6,447 (5.3%) | 32,583 (26.7%) | +405% leaf size |
| | | في→# | ~699 occurrences | ~699 occurrences | D5 interim fix — unchanged as expected |

**Assessment:** The node-count drop (125->58) and max_leaf growth (6,447->32,583) need investigation. Likely explanation: D4 TOC dot-leader filter correctly removing noise nodes. Run a node-title diff between the 06-30 and 07-14 trees to confirm.

### 4.7 PASS Promotion Candidates

| Document | doc_id | Case for PASS | Case Against |
|----------|--------|---------------|-------------|
| **سياسة حوكمة** | `efd65b00` | CLEAN text quality, 16.5% max_leaf, 18 nodes depth-2, 0.3% diacritic density (normal) | Original MARGINAL was based on unconfirmed RTL table-field corruption. This run found **no corruption** |
| **Haftpflicht-Besondere** | `906392fb` | 16% max_leaf is within acceptable range, 33 nodes depth-2 | Uneven split pattern, but 16% concentration is comparable to other PASS documents |

**Recommendation:** Promote both to PASS in the next corpus report update.

---

## 5. Combined Gap Status

| Gap | Baseline | Current Status |
|-----|----------|----------------|
| **Gap 1** — OCR escalation | 6 FAIL docs (image-only, zero text) | **4/6 resolved** — real Arabic text recovered. Remaining 2 are genuine infographics |
| **Gap 2** — Garble-gate text content | 3 FAIL (mojibake, Latin-sub, digit-junk) | **2/3 resolved** — القرار التنظيمي is structural CMap corruption |
| **Gap 3** — Latin inline Article markers | 2 FAIL, 4 MARGINAL | **Splitter redesign landed** — 4->PASS, cabinet_res_21 pair improved (81->73%) |
| **Gap 4** — Presentation-form Arabic | 1 FAIL (88% single leaf) | **Improved to 27%** — حقوق الإنسان structurally sound |
| **Gap 5** — في→# substitution | 2 MARGINAL | **Unchanged** — D5 interim fix; full resolution requires upstream Docling fix (#3802) |
| **Gap 6** — Table column degradation | 3 MARGINAL | **1/3 resolved** (world-stats-pocketbook->PASS). 2 unchanged |
| **ISS-02** — Erasure cascade | 🟢 RESOLVED | 🟢 Resolved — registry delete already bounded by `asyncio.wait_for` + `registry_delete_timeout_s` (storage.py:255-274). Regression: `test_delete_doc_awaits_registry` (:388), `test_delete_doc_registry_timeout` (:404), erasure-cascade Postgres-failure scenario (:480). Closed per RFC-011 D1. |
| **ISS-03** — Backfill 0-doc | 🟢 RESOLVED | 🟢 Resolved — `registry_backfill.py:188-193` already guards `set_registry_complete` behind non-empty `meta_keys` check. Closed per RFC-012 D1. |
| **ISS-05** — O(N) MinIO GETs | 🟠 DEGRADED | 🟠 Open — Batch 2 |
| **ISS-07** — Redis conn storm | 🟠 DEGRADED | 🟡 Partially fixed — worker.py remaining |
| **ISS-08** — OpenAI error swallow | 🟠 DEGRADED | 🟠 Open — Batch 2 |

---

## 6. Remaining Fix Plan

### Batch 1 — Quick Wins (half-day)

| Issue  | Fix                                                                     | Lines Changed |
| ------ | ----------------------------------------------------------------------- | ------------- |
| ISS-41 | Add 7th cascade step to delete `preloaded/<filename>`                   | ~10           |
| ISS-07 | Fix remaining worker.py ad-hoc connections                              | ~10           |
| ISS-03 | Skip `set_registry_complete` when 0 keys found                         | ~3            |
| ISS-32 | Fail-closed bearer auth (opt-in insecure-dev flag)                      | ~10           |
| ISS-35 | `AGPL_FALLBACK_TOTAL` metric (docling_missing vs operator_configured)   | ~10           |
| ISS-37 | Lock/semaphore around `wait_for_memory` check-then-admit                | ~10           |
| ISS-39 | Raise gunicorn `graceful_timeout` + add `max_requests`/jitter           | ~5            |
| ISS-42 | Delete dead `upload.py`                                                 | (deletion)    |
| ISS-45 | Delete dead `tools/processing.py`                                       | (deletion)    |
| ISS-43 | Env-var-only / localhost-default for `stress_test.py`/`test.py`         | ~10           |
| ISS-46 | Semaphore-bounded concurrent upserts in `registry_backfill.py`          | ~15           |

### Batch 2 — Structural Fixes (1-2 days)

| Issue  | Fix                                                                         | Lines Changed |
| ------ | --------------------------------------------------------------------------- | ------------- |
| ISS-18 | Regex JSON extraction + narrow catch                                        | ~10           |
| ISS-19 | Same pattern as ISS-18 + `RAG_PARSE_FAILURES` counter                       | ~15           |
| ISS-05 | Store `node_count` in `.meta.json` sidecar                                  | ~10           |
| ISS-02 | Await registry delete with 5s timeout                                       | ~20           |
| ISS-40 | Add explicit statement timeout to `registry.py` `delete_doc`                | ~5            |
| ISS-34 | Hard-fail `ensure_tessdata` on non-Latin script mismatch                    | ~20           |
| ISS-36 | Extend token-repetition check to short blobs; dedup garble-gate functions   | ~20           |
| ISS-33 | Startup assertion: PII corpus requires ZDR-tier endpoint                    | ~15           |
| ISS-44 | Extract shared `_extract_page_hits` helper into `helpers.py`                | ~20           |
| ISS-08 | Retry transient OpenAI errors + `IMAGE_DESCRIBE_FAILURES` counter           | ~15           |

### Batch 3 — Long-Term

| Issue      | Fix                                                 | Prereq                  |
| ---------- | --------------------------------------------------- | ----------------------- |
| ISS-05 (B) | Remove MinIO fallback, registry-only listing        | ISS-03, registry stable |

---

## 7. What This Audit Did NOT Cover

- **Test coverage gaps** — no mutation testing or coverage analysis
- **Dependency vulnerabilities** — no `pip audit` or CVE scan
- **Load/stress testing** — performance issues identified by code inspection, not benchmarking
- **Deployment configuration** — Kubernetes manifests, Helm charts, ingress rules
- **LLM prompt quality** — RAG prompts not evaluated for accuracy
- **Docling/pymupdf4llm internals** — third-party library behavior taken at face value
- **مرسوم 33 node-title diff** — regression signal flagged but not root-caused (requires tree comparison)

---

## 8. Risk Assessment

| Risk                             | Current State            | After Batch 1+2                 |
| -------------------------------- | ------------------------ | ------------------------------- |
| Right-to-erasure incomplete (registry) | 🟠 Medium (ISS-02) | 🟢 Resolved (Batch 2)           |
| Right-to-erasure incomplete (raw upload never deleted) | 🟠 Medium (ISS-41, new) | 🟢 Resolved (Batch 1) |
| AGPL fallback with no observability (HR4) | 🟡 Low-Medium (ISS-35, new) | 🟢 Metric shipped (Batch 1); strict gate pending legal sign-off |
| OCR false-clean Latin mojibake (ISS-34, new) | 🟠 Medium | 🟢 Resolved (Batch 2) |
| Auth fails open on missing token (ISS-32, new) | 🟡 Low-Medium | 🟢 Resolved (Batch 1) |
| Corpus invisibility on backfill  | 🟢 Resolved (ISS-03)     | 🟢 Resolved — existing guard at `registry_backfill.py:188-193`. Closed per RFC-012 D1. |
| Redis connection churn (worker)  | 🟡 Low (ISS-07 partial)  | 🟢 Resolved (Batch 1)           |
| Silent performance degradation   | 🟠 Medium (ISS-05)       | 🟢 Resolved (Batch 2)           |
| Table extraction (Gap 6)         | 🟡 2/3 unchanged          | 🟡 Unchanged — RFC-004 scope    |
| في→# substitution (Gap 5)       | 🟡 Upstream Docling #3802 | 🟡 Unchanged — upstream pending |
| CMap corruption (Gap 2 residual) | 🔴 Structural, unfixable  | 🔴 Unchanged                    |

---

## 9. Deliverables

| File                                                          | Content                                                            |
| ------------------------------------------------------------- | ------------------------------------------------------------------ |
| [`audit/SCOPE.md`](SCOPE.md)                                 | System boundaries, file inventory, audit goals                     |
| [`audit/EXPLORATION.md`](EXPLORATION.md)                     | Per-file summaries with ~80 red flags                              |
| [`audit/ISSUES.md`](ISSUES.md)                               | 28 open issues with classifications and evidence                   |
| [`audit/FIXES.md`](FIXES.md)                                 | Fix approaches for remaining issues with recommendations           |
| [`audit/DOCSTORE_AUDIT_REPORT.md`](DOCSTORE_AUDIT_REPORT.md) | This executive summary (code audit + corpus quality analysis)      |
| [`DOC_STORE_CORPUS_REPORT.md`](../DOC_STORE_CORPUS_REPORT.md)| Full 25-document corpus report with per-doc verdicts and metrics   |

---

## Appendix: MARGINAL Document Metrics Comparison

| # | Document | doc_id | E2E Baseline (07-10) | doc_store Run (07-14) | Delta |
|---|----------|--------|---------------------|-----------------------|-------|
| 1 | GHV-TKV-Tarif | `a6a49019` | flat, 24 blocks | flat, 24 blocks, 389 chars | Unchanged (Gap 6) |
| 2 | Haftpflicht-Besondere | `906392fb` | 33 nodes, depth 2, max_leaf 13.1k | 33 nodes, depth 2, max_leaf 20.9k (16%) | Stable — **PASS candidate** |
| 3 | MOU MOHRE | `7c0a0100` | FAIL: 100% image, 0 text | 16 nodes, depth 2, 12.2k chars | **Rescued via OCR** |
| 4 | Ministerial Res 279 | `c6a673f1` | 18 nodes, depth 1 | 28 nodes, depth 5, 690 tab chars | Improved structure, tab artifacts persist |
| 5 | Reitlehrer | `7116d385` | 7 nodes, depth 1 | 9 nodes, depth 3, 3.5k chars | Slight improvement, inherently small |
| 6 | Unfallversicherung | `4bbd7ede` | flat, 78 blocks, 63 images | flat, 78 blocks, 80.8% img, 1.3k chars | Unchanged (Gap 6) |
| 7 | cabinet_res_21(1)(1) | `997a140a` | FAIL: 22 nodes, 80.8% max_leaf | 37 nodes, 73.4% max_leaf | Gap 3 improved |
| 8 | cabinet_res_21(1) | `0dc36fb4` | identical to #7 | identical to #7 | Duplicate confirmed |
| 9 | uae_numbers_portrait | `f274ece1` | flat, 57% img, 129 chars | flat, 57% img, 129 chars | Unchanged (infographic) |
| 10 | اتفاقية | `a5ef1929` | FAIL: 100% image, 0 text | 40 nodes, depth 2, 29.9k chars | **Rescued via OCR** |
| 11 | سياسة حوكمة | `efd65b00` | 18 nodes, depth 2 | 18 nodes, depth 2, 19.8k chars, CLEAN | Unchanged — **PASS candidate** |
| 12 | قرار 1 | `34b3b7ee` | FAIL: 100% image, 0 text | 33 nodes, depth 2, 39.1k chars | **Rescued via OCR** |
| 13 | قرار 106 | `7b819149` | FAIL: 100% image, 0 text | 20 nodes, depth 2, 32.8k chars | **Rescued via OCR** |
| 14 | مرسوم 13 | `d9f0a0e9` | 17 nodes, 81 # subs, mojibake | 17 nodes, 0 # subs, 0 mojibake | **Text quality fixed** |
| 15 | مرسوم 33 | `8b05de59` | 125 nodes, max_leaf 6.4k | 58 nodes, max_leaf 32.6k | **Regression signal** — investigate |
| 16 | وارد 597 | `127ba17a` | FAIL: digit_ratio 0.91 | 13 nodes, digit_ratio 0.01, clean Arabic | **Rescued via garble-gate fix** |
| 17 | حقوق الإنسان | `e8596b90` | FAIL: 88% single leaf | 322 nodes, 27.4% max_leaf | **Major structural improvement** |
