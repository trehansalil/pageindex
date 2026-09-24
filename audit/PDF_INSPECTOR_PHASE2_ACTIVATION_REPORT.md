# PDF-Inspector Integration: Synthesis Report & Phase 2 Activation Guide

**Branch:** `feat/pdf-inspector-shadow-pilot` · **Date:** 2026-08-06
**Sources:** Integration-gap audit, performance analysis, promotion design (3 parallel investigations)

---

## 1. EXECUTIVE SUMMARY

Do **not** flip `PDF_INSPECTOR_PRECLASSIFY=1` today — the flag is currently dead code (defined in `config.py` lines 21-23, read nowhere), so enabling it would have zero effect. The value proposition is real but modest: pdf-inspector's classification already flows through the pipeline in shadow mode and, once wired into `client.py::index()`, would eliminate the wasted non-OCR first pass for scanned/image-based PDFs (~600ms-2000ms saved per affected doc, modeled ~30% corpus throughput gain in the optimistic scenario, though only ~8.3% of the current 60-doc corpus — 4 scanned + 1 mixed — directly benefits). Risk is **medium and well-bounded**: `validate_tree()` and the Fix-3 OCR retry remain unconditional safety nets, the feature is opt-in via env var with a zero-code rollback, and the worst false-positive outcome is slower-but-valid OCR output rather than corruption. The single hard precondition before activation: the >=99% agreement-with-`validate_tree()` measurement has **not been run** — the savings figures are model-derived, not measured. Recommendation: implement the ~30-LOC Tier 1 wiring now behind the flag, run the shadow comparison on the 5 non-text_based corpus docs, then enable.

---

## 2. CURRENT STATE

### Integrated (shadow mode, working)
- `probe_conversion_route()` (`src/pageindex_mcp/converters.py`) calls `_run_pdf_inspector()` and returns `pdf_classification` — a dict of `{pdf_type, confidence, pages_needing_ocr, has_encoding_issues}` — as the third tuple element.
- `converters_cli.py::main()` emits the classification in the handshake JSON (lines 106-108).
- `worker.py::_run_converter_subprocess()` extracts it from the handshake and logs it at INFO (lines 312-319) as shadow telemetry.
- Prometheus metrics exist for classification counts and latency.
- `PDF_INSPECTOR_PRECLASSIFY` flag defined in `config.py` (lines 21-23, default `'0'`).

### Shadow-only (computed but never acted upon)
- The classification **dead-ends at the worker log**. Grep confirms no file imports or branches on `PDF_INSPECTOR_PRECLASSIFY`. No code path changes behavior based on `pdf_type`.

### Missing
1. Threading of the classification dict from `converters_cli.py` into `CustomPageIndexClient.index()`.
2. Branching logic in `index()` to force OCR on the first pass for scanned/image_based docs.
3. Optional timeout multiplier in `worker.py` for scanned classifications.
4. Empirical agreement measurement between pdf-inspector classifications and `validate_tree()` outcomes.

### Existing infrastructure that makes this cheap
- `force_full_page_ocr` parameter is fully wired through `converters.py` -> `_build_pdf_pipeline_options` -> `TesseractCliOcrOptions`; **no converter changes needed**.
- The `pre_garbled + PRE_GARBLE_FORCE_OCR_ENABLED` conditional tree in `client.py` (lines ~779-805) is structurally identical to what Tier 1 needs — `inspector_force_ocr` slots into the same tree.
- The `_docling_converter` cache key already includes `'force'` for the force-OCR variant.

---

## 3. ACTIVATION READINESS

| Criterion | Status |
|---|---|
| Classification computed and transmitted end-to-end | MET |
| Config flag exists with safe default | MET |
| `force_full_page_ocr` plumbing available to caller | MET |
| Corpus size >=50 with scanned+mixed diversity | MET (60 docs: 4 scanned, 1 mixed) |
| Code reads the flag / acts on classification | NOT MET — flag is dead |
| >=99% agreement with `validate_tree()` measured | NOT MET — assumed, never run |
| Savings figures empirically measured | NOT MET — flow-diagram estimates only |
| Configurable confidence threshold | NOT MET (upstream #266/#267/#254) — hardcode 0.90 for Tier 1 |

### Blockers
- **Hard blocker for activation:** none code-side once Tier 1 lands; the wiring itself is the blocker.
- **Hard blocker for per-page routing (Tier 2 only):** pdf-inspector bug **#252** (1-indexed `pages_needing_ocr` vs Docling's 0-indexing). Explicitly **does NOT block** document-level routing, which consumes only `pdf_type` + `confidence`.
- **Process blocker:** shadow-mode agreement measurement on the 4 scanned + 1 mixed docs must run before the flag flips in production.

---

## 4. PHASE 2 TIER 1 DESIGN — Document-Level OCR Routing

**Principle:** When `PDF_INSPECTOR_PRECLASSIFY=1` and classification says `pdf_type in {scanned, image_based}` with `confidence >= 0.90`, pass `force_full_page_ocr=True` on the **first** Docling conversion inside `client.py::index()` — eliminating the guaranteed-to-fail non-OCR pass. `validate_tree()` (line 987) runs unconditionally; the Fix-3 OCR retry (lines 1008-1094) stays intact as the idempotent safety net.

**Deliberately excluded from Tier 1** (per the performance analysis's caution on unmeasured text-layer reliability):
- Suppressing OCR escalation for high-confidence `text_based` docs. Investigation 1 proposed it; Investigation 2 flags that 3 long legal docs classified text_based at only 0.70-0.75 confidence and the agreement rate is unmeasured. **Defer this to Tier 1.5** after shadow data confirms the correlation — a false skip of OCR escalation is the one path that could ship garbage.
- Any use of `pages_needing_ocr` (blocked by #252, and Docling has no per-page OCR anyway).

**Classification flow (target state):**
```
converters_cli.main()
  +-- probe_conversion_route(pdf) -> (chunks, is_docling, pdf_classification)   [no change]
  +-- client.index(path, pdf_classification=pdf_classification)               [NEW: 1 arg]
       +-- if PRECLASSIFY and type in (scanned, image_based) and conf >= 0.90:
              inspector_force_ocr = True                                      [NEW: ~15 lines]
       +-- converter loop: docling paths get force_full_page_ocr=True +
              ocr_lang_override=detect_ocr_langs(filename)                    [NEW: ~10 lines]
       +-- validate_tree()  -> unchanged, unconditional                         [no change]
       +-- Fix-3 OCR retry  -> unchanged (idempotent re-run if needed)          [no change]
```

**Estimated total: ~30 LOC** plus tests.

---

## 5. IMPLEMENTATION PLAN (ordered)

1. **`src/pageindex_mcp/client.py` — `index()` signature** (~1 line)
   Add `pdf_classification: dict | None = None` parameter (signature at line 667).

2. **`src/pageindex_mcp/client.py` — decision logic** (~15 lines)
   At top of the PDF branch (~line 726): compute `inspector_force_ocr` from `config.PDF_INSPECTOR_PRECLASSIFY`, `pdf_type in ('scanned', 'image_based')`, `confidence >= 0.90`. Log at INFO when it fires; increment a Prometheus counter (e.g. `pdf_inspector_preclassify_forced_ocr_total`).

3. **`src/pageindex_mcp/client.py` — converter loop** (~10 lines)
   In lines ~770-815, when `inspector_force_ocr` and `'docling' in conv_name`: local path -> `conv_fn(file_path, True, ocr_lang_override=detect_ocr_langs(filename))`; remote path -> `_remote_pdf_to_markdown(staging_key, force_full_page_ocr=True, ocr_lang_override=...)`. Mirror the existing `pre_garbled` conditionals exactly.

4. **`src/pageindex_mcp/converters_cli.py` — thread the dict** (~3 lines)
   Line ~129: `doc_id = await client.index(args.input_path, pdf_classification=pdf_classification)`. (Preferred over the `client._pdf_classification` attribute injection — an explicit parameter is testable and self-documenting.)

5. **`src/pageindex_mcp/worker.py` — timeout multiplier** (~5 lines, optional but recommended)
   In `_run_converter_subprocess`, when preclassify enabled and `pdf_type == 'scanned'`: apply a 2x multiplier to `effective_timeout` (scanned docs go straight to full-page OCR, 3-10x slower than text-layer extraction).

6. **No changes needed:** `probe_conversion_route`, `_build_pdf_pipeline_options`, `validate_tree` call site, Fix-3 retry path.

7. **Tests:**
   - Unit: scanned/0.95 + flag=1 -> first call has `force_full_page_ocr=True`, no double conversion.
   - Unit: confidence 0.85 -> falls through to normal path.
   - Unit: `pdf_type='native'`/`text_based` -> no forced OCR.
   - Unit: flag=0 -> classification ignored even at confidence 1.0.
   - Unit: `pdf_classification=None` -> normal behavior.
   - Integration: mock `validate_tree` -> `(False, 'garbling')` after forced-OCR pass -> Fix-3 retry fires.
   - Integration: `(True, None)` -> no retry, normal save.
   - Remote-path: `_remote_pdf_to_markdown` receives `force_full_page_ocr=True`.
   - Corpus regression: full corpus with flag=1 vs baseline — verdict distribution must not regress.
   - Performance: wall-clock on known scanned PDFs flag=1 vs 0.

8. **Pre-activation measurement (before flipping in prod):** run shadow-mode comparison of classifications vs `validate_tree()` implicit OCR signals on the 4 scanned + 1 mixed docs; corroborate savings with real Prometheus timings over a 1-2 week shadow deployment.

---

## 6. PHASE 2 TIER 2 OUTLOOK — Per-Page Routing (DEFERRED)

Per-page selective OCR (`pages_needing_ocr` -> OCR only those pages) is **not feasible now**:

1. **Docling limitation:** `PdfPipelineOptions.ocr_options` is document-uniform; no page-level OCR toggle exists upstream.
2. **Bug #252:** pdf-inspector's `pages_needing_ocr` is 1-indexed vs Docling's 0-indexing — off-by-one page selection until fixed.
3. **Chunking coordination:** `_pdf_to_markdown_docling_chunked` splits at `MAX_DOCLING_PAGES` boundaries; per-page OCR would require mapping page indices to chunks with per-chunk OCR configs.
4. **Converter cache model:** per-chunk OCR configs would break the process-lifetime `_docling_converter` cache that prevents the ~250MB/converter memory leak.
5. **No quality evidence:** no corpus data proves per-page OCR avoids heading/section discontinuities at OCR/non-OCR page boundaries.

Impact of deferral is small: only 1/60 corpus docs is `mixed` (conf 0.70), and it falls back safely to the existing conservative full-document behavior. Also deferred: text_based OCR-escalation suppression (Tier 1.5, needs agreement data) and configurable confidence thresholds (upstream #266/#267).

---

## 7. RISK MATRIX

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | False-positive `scanned` on a text-based PDF -> unnecessary full-page OCR | Low | Low — slower but valid output (OCR is lossy vs text layer, not corrupt) | 0.90 confidence gate; `validate_tree()` still judges the result |
| 2 | False-positive `text_based` on garbled-CMap PDF -> skipped OCR | N/A in Tier 1 | Medium if ever enabled | **Excluded from Tier 1**; Fix-3 escalation remains the net; gate Tier 1.5 on measured agreement |
| 3 | Agreement assumption (>98%) wrong — classifier disagrees with `validate_tree()` | Unknown (unmeasured) | Medium — savings evaporate, extra OCR passes | Mandatory shadow measurement on 5 non-text_based docs before flag flip |
| 4 | Savings overstated (modeled ~600ms-2000ms, not empirically confirmed) | Medium | Low — feature still net-positive, just smaller | Corroborate with Prometheus wall-clock during shadow deployment |
| 5 | Scanned docs exceed subprocess timeout when going OCR-first | Low | Medium — job failure | Step 5 timeout multiplier (2x) in `worker.py` |
| 6 | Upstream library immaturity (pre-1.0; markdown #269; CJK #272; self-reported benchmarks) | Medium | Low for classifier-only use on German/Arabic corpus | Use classifier only; treat as advisory; env-var kill switch |
| 7 | Bug #252 misroutes pages | None in Tier 1 | High if Tier 2 shipped prematurely | Tier 2 hard-blocked on #252 + Docling per-page support |
| 8 | Regression in existing pipeline from wiring changes | Low | High | Full test plan; corpus regression run; validate_tree never bypassed; conditionals mirror proven `pre_garbled` pattern |
| 9 | Rollback needed in production | — | Low | `PDF_INSPECTOR_PRECLASSIFY=0` (or unset) — zero-code rollback, only worker/converter restart |

---

## 8. ENV VARS

| Var | Status | Action |
|---|---|---|
| `PDF_INSPECTOR_PRECLASSIFY` | Exists (`config.py` lines 21-23, default `'0'`) | Set to `1` to activate Tier 1 after it lands and shadow validation passes. Already documented in `.env.example`. |
| `DOCLING_DO_OCR` / `DOCLING_FORCE_FULL_PAGE_OCR` | Exist | No changes — Tier 1 uses the `force_full_page_ocr` function parameter, not env overrides. |
| *(future, Tier 1.5)* `PDF_INSPECTOR_CONFIDENCE_THRESHOLD` | Not needed yet | Hardcode 0.90 in Tier 1; add only if tuning proves necessary (upstream #266/#267 substitute). |

No new env vars are strictly required for Tier 1 activation.

---

## 9. RECOMMENDATION

**Conditional GO — implement now, activate after measurement.**

1. **GO** on implementing the Tier 1 wiring (~30 LOC across `client.py`, `converters_cli.py`, `worker.py`) immediately. It is low-risk, fully flag-gated, structurally mirrors the proven `pre_garbled` path, and has a zero-code rollback.
2. **NO-GO** on setting `PDF_INSPECTOR_PRECLASSIFY=1` in production until two conditions are met:
   - **(a)** The shadow-mode agreement measurement runs on the 4 scanned + 1 mixed corpus docs and shows >=99% agreement with `validate_tree()` OCR signals (all three investigations converge on this as the outstanding gate).
   - **(b)** The corpus regression run with the flag on shows no PASS/MARGINAL/FAIL verdict regressions vs baseline.
3. **NO-GO** (hard) on Tier 2 per-page routing and on text_based escalation suppression until bug #252 is resolved, Docling gains per-page OCR, and shadow data validates text-layer classifications respectively.
4. During the 1-2 week shadow window, use Prometheus wall-clock data to replace the modeled savings figures (~600-2000ms/doc) with measured ones before citing throughput gains anywhere.

**Key file anchors:** `src/pageindex_mcp/client.py` (`index()` line 667, PDF branch ~726, converter loop ~770-815, `validate_tree` 987, Fix-3 retry 1008-1094); `src/pageindex_mcp/converters_cli.py` (~98-129); `src/pageindex_mcp/worker.py` (~279, 312-319); `src/pageindex_mcp/config.py` (21-23).

---

## 10. D6 — FULL CORPUS REGRESSION GATE (PRE-ACTIVATION)

**Task:** [Task 6.2](../agents/tasks/tasks-rfc032-pdf-inspector-tier1-activation.md#62-full-corpus-regression-gate) · **RFC:** [D6](../agents/rfcs/032-pdf-inspector-tier1-activation.md#d6-full-corpus-regression-gate-pre-activation) · **Design:** [AD7](../agents/designs/design-rfc032-pdf-inspector-tier1-activation.md#ad7-corpus-regression-gate-d6) · **Recommended by:** Rec-2b (this report)

**Status: GATE NOT SATISFIED — no PRECLASSIFY=1 corpus run has been executed. Activation remains blocked on this task.**

### What was checked

- Confirmed D0-D2 are landed in `client.py` (`PDF_INSPECTOR_PRECLASSIFY` import at line 19, `inspector_force_ocr` decision at lines 744-751, converter-loop wiring at lines 814/844) — the flag is live code, no longer dead. A `PRECLASSIFY=1` run today would exercise the real routing path.
- The 60-doc source corpus exists locally as `issue/data/*.pdf` (27 German T&C docs) + `issue/data2/*.pdf` (33 Arabic/English/international docs) = 60 total, per RFC-031's corpus definition.
- Remote MinIO (`10.43.23.66:9000`) answers `/minio/health/live` with `200` — infra reachable from this environment.
- Searched `audit/` for any prior `PRECLASSIFY=1` ingest record: none found. The most recent full-corpus scoring pass (`CORPUS_REINGESTION_AUDIT_RUN-15.md`, 2026-08-06) predates this RFC's code landing and was produced with the flag off by construction (it was dead code at ingest time) — Tally: **11 PASS, 12 MARGINAL, 1 FAIL, 1 ERROR** (25/25 docs audited in that run's scope; run-over-run verdict churn on ~10 docs was already flagged there as scorer non-determinism unrelated to pdf-inspector).
- The local `doc_store/` used by `make ingest` currently holds 9 unrelated HR onboarding docs, not the 60-doc legal/insurance corpus — a `make ingest` run today would not exercise the corpus this gate requires; the run must target `issue/data/` + `issue/data2/` (e.g. via `make ingest-minio` after upload, or two `DIR=... make ingest` passes).
- No app server / arq worker process was running in this session (`localhost:8201` connection refused).

### Why the run was not executed here

A full 60-doc regression is a real ingestion: each document goes through live Docling conversion and LLM tree-building against the configured Azure OpenAI deployment (RFC-032 estimates ~2h wall-clock). That is a materially costly, long-running operation requiring a running server + worker pair and real LLM spend — outside what this task should trigger unattended as a side effect of an audit-report edit. It needs to be run explicitly, by a human or an operator-authorized job, not inferred from existing data: the pre-code Run 15 baseline is not a valid `PRECLASSIFY=0` substitute (the flag was inert when Run 15 ran, but Run 15 also predates the D0-D2 code path entirely, so a fresh `PRECLASSIFY=0` control run should be taken with the current code before diffing against a `PRECLASSIFY=1` run, to avoid attributing unrelated scorer/pipeline drift to this feature).

### What running the gate requires

1. `make up` (or `make serve` + `make worker` in separate shells) with `.env.active` pointed at remote MinIO/Redis/Postgres/Docling.
2. `PDF_INSPECTOR_PRECLASSIFY=0` control run over `issue/data/` + `issue/data2/` (27 + 33 = 60 docs) — capture verdict distribution via the existing corpus scoring path (`corpus-score-diff` skill / scorer).
3. `PDF_INSPECTOR_PRECLASSIFY=1` treatment run over the same 60 docs, same code, same session window.
4. Diff verdict distributions per-doc (not just aggregate tallies — a regression can hide inside an unchanged total). Any doc that moves PASS→MARGINAL or PASS→FAIL blocks activation per RFC-032 D6.

### Disposition

Recorded here as an explicit outstanding blocker. `PDF_INSPECTOR_PRECLASSIFY` must remain `0` in production until this gate is run with a real `PRECLASSIFY=0`/`PRECLASSIFY=1` pair on the full 60-doc corpus and shows zero PASS→MARGINAL / PASS→FAIL regressions. [Task 6.2](../agents/tasks/tasks-rfc032-pdf-inspector-tier1-activation.md#62-full-corpus-regression-gate) and the RFC-032 [Final Checkpoint](../agents/tasks/tasks-rfc032-pdf-inspector-tier1-activation.md#63-final-checkpoint) should not be marked complete until this section is updated with an actual before/after verdict table.
