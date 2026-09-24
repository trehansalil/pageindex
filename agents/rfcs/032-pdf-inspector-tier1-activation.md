<!-- Space: CITRA -->
<!-- Title: RFC-032: pdf-inspector Tier 1 Activation — Document-Level OCR Pre-Routing -->
<!-- Folder: RFCs -->

# RFC-032: pdf-inspector Tier 1 Activation — Document-Level OCR Pre-Routing

**Status:** Code Complete (D0-D4), Pre-Activation Gate D5 Passed — **Activation Blocked on D6** (full corpus regression gate not yet run; see [Final Checkpoint](#final-checkpoint-task-63))
**Date:** 2026-08-06
**Branch:** `feat/pdf-inspector-shadow-pilot`
**Predecessor:** [RFC-031: pdf-inspector Shadow-Mode Pilot](031-pdf-inspector-shadow-pilot.md)
**Audit:** [PDF_INSPECTOR_VIABILITY_REPORT.md](../../audit/PDF_INSPECTOR_VIABILITY_REPORT.md) · [PDF_INSPECTOR_PHASE2_ACTIVATION_REPORT.md](../../audit/PDF_INSPECTOR_PHASE2_ACTIVATION_REPORT.md)

## Summary

RFC-031 landed pdf-inspector as a shadow-mode classifier: classification runs end-to-end through `probe_conversion_route()` → handshake → worker log → Prometheus, but never influences routing. The `PDF_INSPECTOR_PRECLASSIFY` config flag exists in `config.py` (lines 21-23) but is **dead code** — zero consumers anywhere in the codebase. Flipping it to `1` today does nothing.

This RFC wires the classification into `client.py::index()` so that when `PDF_INSPECTOR_PRECLASSIFY=1`, scanned/image-based PDFs classified with confidence >= 0.90 go straight to `force_full_page_ocr=True` on the first Docling pass — eliminating the reactive double-conversion penalty (~600ms-2000ms per affected doc). `validate_tree()` and the Fix-3 OCR retry remain unconditional safety nets. The feature is opt-in via env var with a zero-code rollback (`PDF_INSPECTOR_PRECLASSIFY=0`).

All 60-PDF corpus promotion criteria from RFC-031 are met (100% accuracy, 14.7ms mean latency, zero crashes, document diversity including 4 scanned + 1 mixed). The outstanding gate is a shadow-mode agreement measurement between pdf-inspector classifications and `validate_tree()` OCR signals on the 5 non-text_based corpus docs, which must run before activation in production.

## Problem Statement

1. **Dead flag.** `PDF_INSPECTOR_PRECLASSIFY` is defined but never consumed. Classification data dead-ends at the worker INFO log. There is no code path from classification to routing decision.
2. **Reactive OCR doubles wall-clock for scanned PDFs.** The pipeline converts without OCR first (`DOCLING_DO_OCR=0`), then if `validate_tree()` rejects the result, reconverts with `force_full_page_ocr=True`. For the 4 scanned + 1 mixed docs in the corpus (~8.3%), this is a guaranteed ~600ms-2000ms waste per document.
3. **Classification data is computed but discarded.** `converters_cli.py` computes `pdf_classification` via `probe_conversion_route()` (line 98) but never passes it to `client.index()` (line 129). The dict is emitted in the handshake JSON for worker logging only.

## Design Decisions

### D0: Thread pdf_classification from converters_cli into client.index()

**Scope:** Add `pdf_classification: dict | None = None` parameter to `client.py::index()` (line 667). Pass it from `converters_cli.py::main()` (line 129).

**Rationale:** The classification dict already exists in the `converters_cli` scope (returned by `probe_conversion_route()` at line 98). An explicit function parameter is testable and self-documenting; the alternative — attribute injection via `client._pdf_classification` — is fragile and opaque.

**Files:**
- `src/pageindex_mcp/client.py :: index()` — signature change (line 667)
- `src/pageindex_mcp/converters_cli.py :: main()` — pass kwarg (line 129)

**Effort:** Trivial (~3 lines).

---

### D1: Document-level OCR routing decision in index()

**Scope:** At the top of the PDF branch in `index()` (~line 726), compute `inspector_force_ocr` from:
- `config.PDF_INSPECTOR_PRECLASSIFY == True`
- `pdf_classification["pdf_type"] in ("scanned", "image_based")`
- `pdf_classification["confidence"] >= 0.90`

When all three hold, set `inspector_force_ocr = True`. Log at INFO and increment a Prometheus counter (`pdf_inspector_preclassify_forced_ocr_total`).

**Rationale:** Mirrors the existing `pre_garbled + PRE_GARBLE_FORCE_OCR_ENABLED` conditional pattern (lines 735-749). The 0.90 confidence threshold is hardcoded — upstream pdf-inspector has no configurable threshold (#266/#267/#254). The threshold is deliberately conservative: all 4 scanned docs in the corpus report confidence 0.950, all text_based report >= 0.750. A 0.90 gate admits all scanned docs while rejecting the 3 text_based docs at 0.750 confidence.

**Deliberately excluded from Tier 1:**
- **Suppressing OCR for high-confidence `text_based` docs.** A false skip would ship garbage. Deferred to Tier 1.5 after shadow agreement data confirms the correlation.
- **Any use of `pages_needing_ocr`.** Blocked by pdf-inspector bug #252 (1-indexing vs Docling's 0-indexing) and Docling's lack of per-page OCR. Deferred to Tier 2.

**Files:**
- `src/pageindex_mcp/client.py :: index()` — decision logic (~line 726)
- `src/pageindex_mcp/metrics.py` — new Counter

**Effort:** Small (~15 lines).

---

### D2: Converter loop wiring — force OCR on first pass

**Scope:** In the converter loop (lines ~770-815), when `inspector_force_ocr` and `'docling' in conv_name`:
- **Remote path:** call `_remote_pdf_to_markdown(staging_key, force_full_page_ocr=True, ocr_lang_override=detect_ocr_langs(filename))`
- **Local path:** call `conv_fn(file_path, True, ocr_lang_override=detect_ocr_langs(filename))`

**Rationale:** Structurally identical to the `pre_garbled` conditionals at lines 779-814. The `force_full_page_ocr` parameter is fully wired through `converters.py → _build_pdf_pipeline_options → TesseractCliOcrOptions` — no converter changes needed. The `_docling_converter` cache key already includes `'force'` for the force-OCR variant.

**Interaction with `pre_garbled`:** Both signals can fire independently. If `pre_garbled` is True (text-layer garble probe detected garbling), OCR is forced regardless of inspector classification. If only `inspector_force_ocr` is True (inspector says scanned, text probe was clean or empty), OCR is forced by this new path. If both fire, the result is the same: first-pass OCR. No conflict. **Implementation note:** Structure `inspector_force_ocr` as `elif` after the `pre_garbled` branch so only one signal drives the OCR forcing decision. The Prometheus counter (`pdf_inspector_preclassify_forced_ocr_total`) should only increment when `inspector_force_ocr` is the *effective* trigger — not when `pre_garbled` already forced OCR. This avoids inflating savings attribution. A `redundant=true` label is optional but recommended for observability.

**Files:**
- `src/pageindex_mcp/client.py :: index()` — converter loop (~lines 770-815)

**Effort:** Small (~10 lines).

---

<a id="d3-worker-timeout-multiplier"></a>
### D3: Worker timeout multiplier for scanned/image_based classifications (optional)

**Scope:** In `worker.py::_run_converter_subprocess()`, when preclassify is enabled and `pdf_type in ("scanned", "image_based")`: apply a 3x multiplier to `effective_timeout`.

**Rationale:** Scanned and image-based docs going straight to full-page OCR on the first pass are 3-10x slower than text-layer extraction. The existing `effective_timeout` (1770s or dynamic from `chunked_docling_timeout_s`) is sized for the text-layer-first path. A scanned or image-based PDF that previously would have failed fast (garbled output) and retried with OCR now does OCR on the first pass — the total wall-clock is similar, but it's concentrated in one pass rather than split across two. A 3x multiplier covers the lower end of the 3-10x range; 2x was insufficient given the stated slowdown range. `image_based` is included because D1/D2 force OCR for both `scanned` and `image_based` — the timeout scope must match the OCR-forcing scope.

**Files:**
- `src/pageindex_mcp/worker.py :: _run_converter_subprocess()` (~line 296-310)

**Effort:** Trivial (~5 lines).

---

### D4: Tests

**Scope:**
- Unit: `inspector_force_ocr` fires when `PRECLASSIFY=1` + `pdf_type=scanned` + `confidence>=0.90`
- Unit: confidence 0.85 falls through to normal path
- Unit: `pdf_type="text_based"` never forces OCR
- Unit: `PRECLASSIFY=0` ignores classification even at confidence 1.0
- Unit: `pdf_classification=None` preserves normal behavior
- Integration: mock `validate_tree` -> `(False, "garbling")` after forced-OCR pass -> Fix-3 retry fires (safety net intact)
- Integration: `(True, None)` -> no retry, normal save
- Remote-path: `_remote_pdf_to_markdown` receives `force_full_page_ocr=True`

**Files:**
- `tests/test_pdf_inspector_tier1.py` (new)

**Effort:** Medium (~2h).

---

### D5: Pre-activation shadow agreement measurement

**Scope:** Before flipping `PDF_INSPECTOR_PRECLASSIFY=1` in production, run a shadow comparison:
1. Ingest all non-text_based corpus docs (currently 4 scanned + 1 mixed, N=5) with the flag **off** (baseline)
2. Compare pdf-inspector's classification against `validate_tree()`'s implicit OCR signal (did the doc pass without OCR, or did Fix-3 garble escalation fire?)
3. Require **zero disagreements** on all non-text_based corpus docs (currently N=5). As the corpus grows with more scanned/mixed/image_based documents, this gate strengthens automatically.

**Note:** At N=5, this is effectively a spot-check, not a statistical measurement — a single disagreement drops agreement to 80%. The ">=99%" language from earlier drafts was misleading at this sample size. The gate is honest about what it proves: zero observed failures on the available non-text_based documents.

**Rationale:** The viability report's accuracy claim is based on classification correctness (is the PDF actually scanned?), not on agreement with `validate_tree()` (does the text layer actually garble?). A scanned PDF with a valid text layer would be a false positive for forced OCR — slower but not corrupt, so low-impact, but still worth measuring.

**Files:** No code changes. Process step before production activation.

**Effort:** Small (~1h corpus run + analysis).

---

### D6: Full corpus regression gate (pre-activation)

**Scope:** Before flipping `PDF_INSPECTOR_PRECLASSIFY=1` in production, run the full 60-doc corpus ingest with the flag enabled and compare the verdict distribution (PASS/MARGINAL/FAIL) against the `PRECLASSIFY=0` baseline. Any PASS→MARGINAL or PASS→FAIL regression blocks activation.

**Rationale:** D5 validates agreement on the 5 non-text_based docs, but the feature could have unexpected side effects on the 55 text_based docs (e.g., timing changes, code-path interactions). A full corpus regression is cheap (one corpus run) and catches regressions D5 cannot see. Recommended by the Phase 2 Activation Report (Rec-2b).

**Files:** No code changes. Process step before production activation.

**Effort:** Small (~2h corpus run + diff analysis).

### D7: Prometheus wall-clock savings measurement

**Scope:** After activation, measure actual wall-clock savings via Prometheus timing. Compare per-document processing time for scanned/image_based PDFs under `PRECLASSIFY=1` (single OCR pass) vs the `PRECLASSIFY=0` baseline (text-layer attempt + garble retry + OCR). The modeled savings of ~600-2000ms per affected document are currently unvalidated.

**Rationale:** The Phase 2 Activation Report (Sec3-criterion-savings, Rec-4) flagged that no empirical savings measurement exists. Without Prometheus data, the throughput benefit claim is unverifiable.

**Files:** No code changes — uses existing `PDF_INSPECTOR_LATENCY` histogram + ingestion timing.

**Effort:** Small (~1h analysis of Prometheus data after production shadow window).

---

### D8: Shadow deployment window (1-2 weeks sustained)

**Scope:** Run `PDF_INSPECTOR_PRECLASSIFY=1` in production for 1-2 weeks in shadow/monitor mode before declaring the feature stable. Monitor for: timeout failures on scanned/image_based docs, false-positive classifications, unexpected verdict regressions, and Prometheus savings vs modeled estimates.

**Rationale:** The Viability Report (Sec9.8-next-step-3) recommended a sustained shadow deployment window. D5 and D6 are point-in-time measurements; a sustained window catches intermittent failures and validates the feature under real traffic patterns.

**Files:** No code changes — operational monitoring step.

**Effort:** Medium (1-2 weeks of passive monitoring + daily check-ins).

---

### D9: Scanned-PDF wall-clock timing calibration

**Scope:** Before finalizing the D3 timeout multiplier (currently 3x), measure actual OCR processing time on the 4 scanned corpus documents to empirically calibrate the value. If actual slowdown is 5x+, increase the multiplier accordingly (e.g., `max(observed_ratio * 1.5, 3.0)`).

**Result (2026-08-06):** Measured OCR-pass vs text-layer-pass wall-clock on the 4 scanned corpus docs via `pdf_to_markdown_docling()` (local, tesseract ara+eng): MOU MOHRE (9pp) 2.32x, SLA Ministry of Economy (20pp) 4.26x, Cabinet Resolution 1/2022 (21pp) 11.00x, Cabinet Resolution 106/2022 (15pp) 7.08x — mean 6.16x, max 11.00x. Actual slowdown exceeds the 5x recalibration threshold, so the multiplier was raised per the formula: `max(11.00 * 1.5, 3.0) = 16.5x`. `worker.py::_run_converter_subprocess()` now applies `effective_timeout *= 16.5` (was `*= 3`).

**Rationale:** The Grilling Report (I3, Q4) flagged that the timeout multiplier has no empirical basis. The RFC states scanned docs are 3-10x slower, so 3x covers only the lower end. Measurement before production activation avoids latent timeout failures.

**Files:** Measurement step informing D3 multiplier value. Result triggered the adjustment clause: `src/pageindex_mcp/worker.py` (`_run_converter_subprocess`) multiplier updated 3x -> 16.5x.

**Effort:** Small (~1h measurement on 4 scanned corpus docs).

---

## Invariants

1. **`validate_tree()` is never bypassed.** It runs unconditionally on every path (line 987), whether or not inspector-forced OCR was applied. This is the ground-truth quality gate.
2. **Fix-3 OCR retry is never suppressed.** If `validate_tree()` rejects the inspector-OCR'd result (e.g., OCR itself produced garbled output), the existing garble escalation retry (lines 1008-1094) fires as normal. Inspector classification is advisory; it does not short-circuit the retry safety net.
3. **Zero-code rollback.** Setting `PDF_INSPECTOR_PRECLASSIFY=0` (the default) makes the entire D0-D2 decision path inert. No data migration, no re-ingestion, no risk to stored trees.
4. **No new derived stores.** Classification data stays in-memory within the converter child process. No new MinIO prefixes, Redis keys, or Postgres tables.
5. **No new LLM egress.** All changes are local routing logic. No new API calls.

## Task Breakdown

| Task | Decision | Effort | Status |
|---|---|---|---|
| T0: `index()` signature — add `pdf_classification` param | D0 | Trivial | Done |
| T1: `converters_cli.py` — pass `pdf_classification` to `index()` | D0 | Trivial | Done |
| T2: Decision logic — compute `inspector_force_ocr` | D1 | Small | Done |
| T3: Prometheus counter for forced-OCR activations | D1 | Trivial | Done |
| T4: Converter loop — wire `force_full_page_ocr` for inspector path | D2 | Small | Done |
| T5: Worker timeout multiplier for scanned PDFs | D3 | Trivial | Done (recalibrated 3x -> 16.5x per D9) |
| T6: Unit + integration tests | D4 | Medium | Done — 13 tests in `tests/test_pdf_inspector_tier1.py`, full suite green (1558 passed) |
| T7: Shadow agreement measurement (pre-activation) | D5 | Small | Done — 5/5 agreement, 0 disagreements ([Viability Report §9.9](../../audit/PDF_INSPECTOR_VIABILITY_REPORT.md#99-shadow-agreement-measurement-rfc-032-d5-pre-activation)) |
| T8: Full corpus regression gate (pre-activation) | D6 | Small | **Blocked — not run** ([Phase 2 Activation Report §10](../../audit/PDF_INSPECTOR_PHASE2_ACTIVATION_REPORT.md#10-d6--full-corpus-regression-gate-pre-activation): "GATE NOT SATISFIED — no PRECLASSIFY=1 corpus run has been executed") |
| T9: Prometheus wall-clock savings measurement | D7 | Small | Blocked — precondition not met, flag never set to `1` in production ([Viability Report §9.10](../../audit/PDF_INSPECTOR_VIABILITY_REPORT.md#910-prometheus-wall-clock-savings-measurement-rfc-032-d7-post-activation)) |
| T10: Shadow deployment window (1-2 weeks sustained) | D8 | Medium | Blocked — cannot run before D6 clears and the flag is activated in production |
| T11: Scanned-PDF wall-clock timing calibration | D9 | Small | Done — measured OCR/text-layer ratio on 4 scanned docs (mean 6.16x, max 11.00x); multiplier recalibrated to 16.5x per `max(observed_ratio * 1.5, 3.0)` |

**Estimated total:** ~30 LOC (D0-D3) + tests (D4) + 1h corpus run (D5) + 2h regression (D6) + deployment monitoring (D7-D9)

## Non-Goals

- Never suppress OCR escalation for `text_based` classifications (Tier 1.5 — needs agreement data)
- Never use `pages_needing_ocr` for per-page routing (Tier 2 — blocked by bug #252 + Docling limitation)
- Never let inspector classification skip `validate_tree()` or Fix-3 retry
- Never use pdf-inspector as markdown extractor (bug #269: `markdown` always None)
- Never add a configurable confidence threshold (upstream #266/#267 — hardcode 0.90)

## Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | False-positive `scanned` on text-based PDF | Low | Low — slower but valid (OCR output, not corruption) | 0.90 confidence gate; `validate_tree()` judges result |
| 2 | Savings overstated (~600-2000ms modeled, not measured) | Medium | Low — feature still net-positive | Corroborate with Prometheus during shadow window |
| 3 | Scanned docs exceed subprocess timeout on OCR-first | Low | Medium — job failure | D3 timeout multiplier (3x) |
| 4 | Regression in existing pipeline from wiring changes | Low | High | Full test plan; conditionals mirror proven `pre_garbled` pattern |
| 5 | Agreement assumption wrong (>98% unmeasured) | Unknown | Medium — savings evaporate | D5 mandatory measurement before flag flip |
| 6 | Zero image_based docs in corpus — OCR-forcing path unvalidated | Low | Low — code handles it identically to scanned; false positive = slower but valid | Accepted risk. Monitor during D8 shadow window; source test PDFs post-activation if image_based docs appear in production |
| 7 | pdf-inspector sole maintainer (upstream supply-chain) | Low | Medium — no fallback classifier if abandoned | MIT license allows fork. Review if upstream dormant > 6 months |
| 8 | Mixed docs (0.70 confidence) may cross 0.90 threshold in future | Low | Low — OCR-forcing on mixed is slower but not harmful | Monitor during D8 shadow window; if mixed docs appear at >= 0.90, validate OCR-forcing is appropriate |

## Final Checkpoint (Task 6.3)

Verification performed 2026-08-06 against the current `feat/pdf-inspector-shadow-pilot` branch.

| Check | Result |
|---|---|
| Full `uv run pytest` green | **PASS** — 1558 passed, 12 skipped, 1 xfailed, 0 failed. `tests/test_pdf_inspector_tier1.py`: 13/13 passed. |
| [Design Property 1](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-1-flag-gate-inertness) — `PDF_INSPECTOR_PRECLASSIFY=0` makes D0-D2 inert | **PASS** — `inspector_force_ocr` (client.py:744-751) is gated entirely on `PDF_INSPECTOR_PRECLASSIFY`; with it unset/`0` the variable stays `False` and every downstream conditional (`elif inspector_force_ocr:` at lines 814, 844) never fires. |
| [Design Property 5](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-5-validate-tree-unconditional) — `validate_tree()` unconditional | **PASS** — confirmed at `client.py:1035` (line number has drifted from the RFC's original 987 due to RFC-029/030 changes landing on this branch since this RFC was drafted; the call itself carries no `PDF_INSPECTOR_PRECLASSIFY`/`inspector_force_ocr` conditional). |
| [Design Property 6](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-6-fix-3-retry-unconditional) — Fix-3 retry unconditional | **PASS** — confirmed at `client.py:1056` onward (drifted from the RFC's original 1008-1094 for the same reason as above; the retry's trigger condition — `reason in ("garbling", ...) or low_content_ocr_eligible` — carries no reference to the inspector flag). |
| D5 — Shadow agreement (Task 6.1) | **PASS** — 5/5 agreement, 0 disagreements on the corpus's N=5 non-`text_based` docs ([Viability Report §9.9](../../audit/PDF_INSPECTOR_VIABILITY_REPORT.md#99-shadow-agreement-measurement-rfc-032-d5-pre-activation)). |
| D6 — Full corpus regression (Task 6.2) | **NOT SATISFIED** — no `PDF_INSPECTOR_PRECLASSIFY=1` corpus run exists anywhere in `audit/`. [Phase 2 Activation Report §10](../../audit/PDF_INSPECTOR_PHASE2_ACTIVATION_REPORT.md#10-d6--full-corpus-regression-gate-pre-activation) records this explicitly as a blocking gate requiring a real `PRECLASSIFY=0`/`PRECLASSIFY=1` before/after run over the full 60-doc corpus, which has not been executed. |
| D8 — Sustained shadow window (Task 7.3) | **NOT SATISFIED** — `PDF_INSPECTOR_PRECLASSIFY` is confirmed `0`/unset in `.env`, `.env.active`, `.env.example`, and the running worker/server environment ([Viability Report §9.10](../../audit/PDF_INSPECTOR_VIABILITY_REPORT.md#910-prometheus-wall-clock-savings-measurement-rfc-032-d7-post-activation)). A window that requires the flag active in production cannot have run. |

**Disposition:** Code (D0-D4) and the D5 shadow-agreement spot-check are complete and verified. D6 (full corpus regression), D7 (Prometheus savings), and D8 (sustained shadow window) remain outstanding — D6 is the hard blocker per the Phase 2 Activation Report's own recommendation ("NO-GO on setting `PDF_INSPECTOR_PRECLASSIFY=1` in production until ... the corpus regression run with the flag on shows no PASS/MARGINAL/FAIL verdict regressions"). `PDF_INSPECTOR_PRECLASSIFY` stays `0` in production until D6 is run and passes. This checkpoint does not claim "Implementation Complete" for the RFC as a whole — see [Status](#rfc-032-pdf-inspector-tier-1-activation--document-level-ocr-pre-routing) above and [D6 disposition](../../audit/PDF_INSPECTOR_PHASE2_ACTIVATION_REPORT.md#10-d6--full-corpus-regression-gate-pre-activation) for the re-run procedure.

## References

- [RFC-031: pdf-inspector Shadow-Mode Pilot](031-pdf-inspector-shadow-pilot.md) — shadow mode implementation (predecessor)
- [PDF Inspector Viability Report](../../audit/PDF_INSPECTOR_VIABILITY_REPORT.md) — corpus validation, benchmark analysis, risk matrix
- [PDF Inspector Phase 2 Activation Report](../../audit/PDF_INSPECTOR_PHASE2_ACTIVATION_REPORT.md) — integration gap analysis, Tier 1 design, implementation plan
- [PRD.md](../../PRD.md) — Functional Requirements
- [ARCHITECTURE.md](../../ARCHITECTURE.md) — Ingestion Pipeline & Data Flow / Tree Quality Gate
- [Design Document](../designs/design-rfc032-pdf-inspector-tier1-activation.md) — architecture decisions, service contracts, correctness properties
- [Implementation Plan (Tasks)](../tasks/tasks-rfc032-pdf-inspector-tier1-activation.md) — wave-ordered task breakdown with checkpoints
