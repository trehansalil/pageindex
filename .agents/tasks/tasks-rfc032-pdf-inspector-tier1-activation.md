<!-- Space: CITRA -->
<!-- Title: Tasks: RFC-032 — pdf-inspector Tier 1 Activation -->
<!-- Folder: Tasks -->

# Tasks: RFC-032 — pdf-inspector Tier 1 Activation

## Traceability

| Artifact | Link |
|---|---|
| RFC | [032-pdf-inspector-tier1-activation.md](../rfcs/032-pdf-inspector-tier1-activation.md) |
| Design | [design-rfc032-pdf-inspector-tier1-activation.md](../designs/design-rfc032-pdf-inspector-tier1-activation.md) |
| PRD | [PRD.md](../../PRD.md) — Functional Requirements |
| Architecture | [ARCHITECTURE.md](../../ARCHITECTURE.md) — Ingestion Pipeline & Data Flow / Tree Quality Gate |
| Audit (Viability) | [audit/PDF_INSPECTOR_VIABILITY_REPORT.md](../../audit/PDF_INSPECTOR_VIABILITY_REPORT.md) |
| Audit (Activation) | [audit/PDF_INSPECTOR_PHASE2_ACTIVATION_REPORT.md](../../audit/PDF_INSPECTOR_PHASE2_ACTIVATION_REPORT.md) |
| Predecessor | [tasks-rfc031-pdf-inspector-shadow.md](tasks-rfc031-pdf-inspector-shadow.md) |

## Overview

RFC-032 activates the `PDF_INSPECTOR_PRECLASSIFY` flag that RFC-031 left as dead code.
When enabled, scanned/image-based PDFs classified with confidence >= 0.90 go straight to
`force_full_page_ocr=True` on the first Docling pass, eliminating the reactive
double-conversion penalty (~600ms-2000ms per affected doc). `validate_tree()` and the
Fix-3 OCR retry remain unconditional safety nets
([RFC-032 Invariants](../rfcs/032-pdf-inspector-tier1-activation.md#invariants),
[Design Property 5](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-5-validate-tree-unconditional),
[Design Property 6](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-6-fix-3-retry-unconditional)).
The feature is opt-in via env var with zero-code rollback
([Design Property 1](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-1-flag-gate-inertness)).

Implementation threads the `pdf_classification` dict from `converters_cli.py` into
`client.py::index()` (D0), computes a routing decision (D1), wires `force_full_page_ocr`
into the converter loop (D2), optionally extends the worker subprocess timeout for
scanned docs (D3), adds unit/integration tests (D4), and runs a pre-activation shadow
agreement measurement (D5). Total: ~30 LOC (D0-D3) + tests + 1h corpus run (D5) + 2h regression (D6) + deployment monitoring (D7-D9).

## Tasks

- [x] <a id="1-batch-0--threading-d0"></a>1. Batch 0 — Threading ([D0](../rfcs/032-pdf-inspector-tier1-activation.md#d0-thread-pdf_classification-from-converters_cli-into-clientindex))
  - [x] <a id="11-index-signature-pdf-classification-param"></a>1.1 `index()` signature — add `pdf_classification` param ([D0](../rfcs/032-pdf-inspector-tier1-activation.md#d0-thread-pdf_classification-from-converters_cli-into-clientindex))
    - Add `pdf_classification: dict | None = None` parameter to `client.py::index()` at line 667.
    - The parameter is optional with `None` default so all existing callers (tests, preprocess_client, direct invocations) remain unaffected.
    - No logic change in this task — the parameter is threaded but not consumed until Batch 1.
    - _Requirements:_ [RFC-032 D0](../rfcs/032-pdf-inspector-tier1-activation.md#d0-thread-pdf_classification-from-converters_cli-into-clientindex) | [Design AD1](../designs/design-rfc032-pdf-inspector-tier1-activation.md#ad1-thread-classification-param-d0) | [Design §3 client.py](../designs/design-rfc032-pdf-inspector-tier1-activation.md#3-clientpy)
  - [x] <a id="12-converters-cli-pass-kwarg"></a>1.2 `converters_cli.py` — pass `pdf_classification` kwarg ([D0](../rfcs/032-pdf-inspector-tier1-activation.md#d0-thread-pdf_classification-from-converters_cli-into-clientindex))
    - At `converters_cli.py` line 129, pass `pdf_classification=pdf_classification` to the `client.index()` call. The `pdf_classification` variable already exists in scope (returned by `probe_conversion_route()` at line 98).
    - No new imports or logic — a single kwarg addition.
    - _Requirements:_ [RFC-032 D0](../rfcs/032-pdf-inspector-tier1-activation.md#d0-thread-pdf_classification-from-converters_cli-into-clientindex) | [Design AD1](../designs/design-rfc032-pdf-inspector-tier1-activation.md#ad1-thread-classification-param-d0) | [Design §2 converters_cli.py](../designs/design-rfc032-pdf-inspector-tier1-activation.md#2-converters-clipy)
  - [ ] <a id="13-checkpoint--batch-0"></a>1.3 Checkpoint — Batch 0
    - Confirm `uv run pytest` passes with the new parameter defaulting to `None` and no behavioral change.
    - Verify all existing `client.index()` call sites still work without passing `pdf_classification`.

- [x] <a id="2-batch-1--decision-logic-and-metrics-d1"></a>2. Batch 1 — Decision Logic and Metrics ([D1](../rfcs/032-pdf-inspector-tier1-activation.md#d1-document-level-ocr-routing-decision-in-index))
  - [x] <a id="21-compute-inspector-force-ocr"></a>2.1 Compute `inspector_force_ocr` ([D1](../rfcs/032-pdf-inspector-tier1-activation.md#d1-document-level-ocr-routing-decision-in-index))
    - At the top of the PDF branch in `client.py::index()` (~line 726), add a decision block:
      - `inspector_force_ocr = False`
      - If all three conditions hold: `config.PDF_INSPECTOR_PRECLASSIFY == True`, `pdf_classification` is not `None`, `pdf_classification["pdf_type"] in ("scanned", "image_based")`, and `pdf_classification["confidence"] >= 0.90` — set `inspector_force_ocr = True`.
      - Log at INFO level: `"pdf-inspector pre-classify forcing OCR: pdf_type=%s confidence=%.3f"`.
    - Mirror the existing `pre_garbled + PRE_GARBLE_FORCE_OCR_ENABLED` conditional pattern (lines 735-749).
    - The 0.90 confidence threshold is hardcoded (upstream pdf-inspector has no configurable threshold, issues #266/#267/#254). All 4 scanned corpus docs report confidence >= 0.950; all text_based docs report >= 0.750. The 0.90 gate admits scanned while rejecting text_based.
    - When `PDF_INSPECTOR_PRECLASSIFY=0` (default), the entire block is inert ([Design Property 1](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-1-flag-gate-inertness)).
    - _Requirements:_ [RFC-032 D1](../rfcs/032-pdf-inspector-tier1-activation.md#d1-document-level-ocr-routing-decision-in-index) | [Design AD2](../designs/design-rfc032-pdf-inspector-tier1-activation.md#ad2-ocr-routing-decision-d1) | [Design §3 client.py](../designs/design-rfc032-pdf-inspector-tier1-activation.md#3-clientpy) | [Design Property 2](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-2-scanned-image-force-ocr) | [Design Property 3](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-3-text-based-no-force) | [Design Property 4](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-4-confidence-threshold-gate)
  - [x] <a id="22-prometheus-counter"></a>2.2 Prometheus counter for forced-OCR activations ([D1](../rfcs/032-pdf-inspector-tier1-activation.md#d1-document-level-ocr-routing-decision-in-index))
    - Add a new Counter `pdf_inspector_preclassify_forced_ocr_total` to `src/pageindex_mcp/metrics.py`.
    - Increment the counter in `client.py::index()` when `inspector_force_ocr` is set to `True`.
    - _Requirements:_ [RFC-032 D1](../rfcs/032-pdf-inspector-tier1-activation.md#d1-document-level-ocr-routing-decision-in-index) | [Design AD2](../designs/design-rfc032-pdf-inspector-tier1-activation.md#ad2-ocr-routing-decision-d1) | [Design §5 metrics.py](../designs/design-rfc032-pdf-inspector-tier1-activation.md#5-metricspy)
  - [ ] <a id="23-checkpoint--batch-1"></a>2.3 Checkpoint — Batch 1
    - Confirm `inspector_force_ocr` is `True` when `PRECLASSIFY=1` + `pdf_type=scanned` + `confidence>=0.90`.
    - Confirm `inspector_force_ocr` is `False` when `PRECLASSIFY=0`, or `pdf_type=text_based`, or `confidence<0.90`, or `pdf_classification=None`.
    - Confirm Prometheus counter increments only on `inspector_force_ocr=True`.

- [x] <a id="3-batch-2--converter-loop-wiring-d2"></a>3. Batch 2 — Converter Loop Wiring ([D2](../rfcs/032-pdf-inspector-tier1-activation.md#d2-converter-loop-wiring--force-ocr-on-first-pass))
  - [x] <a id="31-wire-force-ocr-converter-loop"></a>3.1 Wire `force_full_page_ocr` in converter loop ([D2](../rfcs/032-pdf-inspector-tier1-activation.md#d2-converter-loop-wiring--force-ocr-on-first-pass))
    - In the converter loop at `client.py::index()` (~lines 770-815), when `inspector_force_ocr` is `True` and `'docling' in conv_name`:
      - **Remote path:** call `_remote_pdf_to_markdown(staging_key, force_full_page_ocr=True, ocr_lang_override=detect_ocr_langs(filename))`
      - **Local path:** call `conv_fn(file_path, True, ocr_lang_override=detect_ocr_langs(filename))`
    - Structurally identical to the existing `pre_garbled` conditionals at lines 779-814. The `force_full_page_ocr` parameter is already wired through `converters.py -> _build_pdf_pipeline_options -> TesseractCliOcrOptions` — no converter changes needed. The `_docling_converter` cache key already includes `'force'` for the force-OCR variant.
    - **Interaction with `pre_garbled`:** Both signals fire independently. If `pre_garbled` is `True`, OCR is forced regardless of inspector classification. If only `inspector_force_ocr` is `True`, OCR is forced by this new path. If both fire, the result is the same: first-pass OCR. No conflict.
    - `validate_tree()` remains unconditional at line 987 ([Design Property 5](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-5-validate-tree-unconditional)). Fix-3 OCR retry remains unconditional at lines 1008-1094 ([Design Property 6](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-6-fix-3-retry-unconditional)).
    - _Requirements:_ [RFC-032 D2](../rfcs/032-pdf-inspector-tier1-activation.md#d2-converter-loop-wiring--force-ocr-on-first-pass) | [Design AD3](../designs/design-rfc032-pdf-inspector-tier1-activation.md#ad3-converter-loop-force-ocr-d2) | [Design §3 client.py](../designs/design-rfc032-pdf-inspector-tier1-activation.md#3-clientpy) | [Design OCR Routing Flow](../designs/design-rfc032-pdf-inspector-tier1-activation.md#ocr-routing-flow--d0--d1--d2)
  - [ ] <a id="32-checkpoint--batch-2"></a>3.2 Checkpoint — Batch 2
    - Confirm `force_full_page_ocr=True` is passed to converter when `inspector_force_ocr` is set.
    - Confirm `force_full_page_ocr` is not passed (or `False`) when `inspector_force_ocr` is `False`.
    - Confirm `validate_tree()` at line 987 is unconditional and unaffected.
    - Confirm Fix-3 retry at lines 1008-1094 is unconditional and unaffected.

- [x] <a id="4-batch-3--worker-timeout-d3"></a>4. Batch 3 — Worker Timeout ([D3](../rfcs/032-pdf-inspector-tier1-activation.md#d3-worker-timeout-multiplier))
  - [x] <a id="41-worker-timeout-multiplier"></a>4.1 Worker timeout multiplier for scanned/image_based PDFs ([D3](../rfcs/032-pdf-inspector-tier1-activation.md#d3-worker-timeout-multiplier))
    - In `worker.py::_run_converter_subprocess()` (~lines 296-310), when `PDF_INSPECTOR_PRECLASSIFY` is enabled and the handshake `pdf_classification["pdf_type"] in ("scanned", "image_based")`: apply a 3x multiplier to `effective_timeout`.
    - Scanned and image-based docs going straight to full-page OCR on the first pass are 3-10x slower than text-layer extraction. The existing `effective_timeout` (1770s or dynamic from `chunked_docling_timeout_s`) is sized for the text-layer-first path. A 3x multiplier covers the lower end of the 3-10x range. The scope matches D1/D2: both `scanned` and `image_based` get forced OCR, so both need the extended timeout.
    - The `pdf_classification` dict is already available in worker scope from the handshake parsing landed in [RFC-031 task 3.2](tasks-rfc031-pdf-inspector-shadow.md#32-extend-worker-handshake-parsing) (~line 311).
    - _Requirements:_ [RFC-032 D3](../rfcs/032-pdf-inspector-tier1-activation.md#d3-worker-timeout-multiplier) | [Design AD4](../designs/design-rfc032-pdf-inspector-tier1-activation.md#ad4-worker-timeout-multiplier-d3) | [Design §4 worker.py](../designs/design-rfc032-pdf-inspector-tier1-activation.md#4-workerpy)
  - [ ] <a id="42-checkpoint--batch-3"></a>4.2 Checkpoint — Batch 3
    - Confirm timeout is 3x when `PRECLASSIFY=1` and `pdf_type in (scanned, image_based)`.
    - Confirm timeout is unchanged for `text_based`, `mixed`, or when `PRECLASSIFY=0`.

- [x] <a id="5-batch-4--tests-d4"></a>5. Batch 4 — Tests ([D4](../rfcs/032-pdf-inspector-tier1-activation.md#d4-tests))
  - [x] <a id="51-unit-tests"></a>5.1 Unit tests ([D4](../rfcs/032-pdf-inspector-tier1-activation.md#d4-tests))
    - Create `tests/test_pdf_inspector_tier1.py` with the following unit test cases:
      - `inspector_force_ocr` fires when `PRECLASSIFY=1` + `pdf_type=scanned` + `confidence>=0.90`
      - `inspector_force_ocr` fires when `PRECLASSIFY=1` + `pdf_type=image_based` + `confidence>=0.90`
      - Confidence 0.85 falls through to normal path (below 0.90 threshold)
      - `pdf_type="text_based"` never forces OCR regardless of confidence
      - `PRECLASSIFY=0` ignores classification even at confidence 1.0 ([Design Property 1](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-1-flag-gate-inertness))
      - `pdf_classification=None` preserves normal behavior
    - _Requirements:_ [RFC-032 D4](../rfcs/032-pdf-inspector-tier1-activation.md#d4-tests) | [Design AD5](../designs/design-rfc032-pdf-inspector-tier1-activation.md#ad5-tests-d4) | [Design Property 1](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-1-flag-gate-inertness) | [Design Property 2](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-2-scanned-image-force-ocr) | [Design Property 3](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-3-text-based-no-force) | [Design Property 4](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-4-confidence-threshold-gate) | [Design Testing Strategy](../designs/design-rfc032-pdf-inspector-tier1-activation.md#testing-strategy)
  - [x] <a id="52-integration-tests"></a>5.2 Integration tests ([D4](../rfcs/032-pdf-inspector-tier1-activation.md#d4-tests))
    - Add integration test cases to `tests/test_pdf_inspector_tier1.py`:
      - Mock `validate_tree` returning `(False, "garbling")` after a forced-OCR pass -- confirm Fix-3 retry fires (safety net intact, [Design Property 6](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-6-fix-3-retry-unconditional))
      - Mock `validate_tree` returning `(True, None)` -- confirm no retry, normal save
    - _Requirements:_ [RFC-032 D4](../rfcs/032-pdf-inspector-tier1-activation.md#d4-tests) | [Design AD5](../designs/design-rfc032-pdf-inspector-tier1-activation.md#ad5-tests-d4) | [Design Property 5](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-5-validate-tree-unconditional) | [Design Property 6](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-6-fix-3-retry-unconditional) | [Design Testing Strategy](../designs/design-rfc032-pdf-inspector-tier1-activation.md#testing-strategy)
  - [x] <a id="53-remote-path-test"></a>5.3 Remote-path test ([D4](../rfcs/032-pdf-inspector-tier1-activation.md#d4-tests))
    - Add a test confirming `_remote_pdf_to_markdown` receives `force_full_page_ocr=True` when `inspector_force_ocr` is set.
    - _Requirements:_ [RFC-032 D4](../rfcs/032-pdf-inspector-tier1-activation.md#d4-tests) | [Design AD5](../designs/design-rfc032-pdf-inspector-tier1-activation.md#ad5-tests-d4) | [Design AD3](../designs/design-rfc032-pdf-inspector-tier1-activation.md#ad3-converter-loop-force-ocr-d2) | [Design Testing Strategy](../designs/design-rfc032-pdf-inspector-tier1-activation.md#testing-strategy)
  - [ ] <a id="54-checkpoint--batch-4"></a>5.4 Checkpoint — Batch 4
    - Full `uv run pytest tests/test_pdf_inspector_tier1.py` green.
    - Full `uv run pytest` green with zero regressions in existing test suites.

- [x] <a id="6-batch-5--pre-activation-d5"></a>6. Batch 5 — Pre-Activation ([D5](../rfcs/032-pdf-inspector-tier1-activation.md#d5-pre-activation-shadow-agreement-measurement))
  - [x] <a id="61-shadow-agreement-measurement"></a>6.1 Shadow agreement measurement ([D5](../rfcs/032-pdf-inspector-tier1-activation.md#d5-pre-activation-shadow-agreement-measurement))
    - Before flipping `PDF_INSPECTOR_PRECLASSIFY=1` in production, run a shadow comparison:
      1. Ingest the 4 scanned + 1 mixed corpus docs with the flag **off** (baseline).
      2. Compare pdf-inspector's classification against `validate_tree()`'s implicit OCR signal (did the doc pass without OCR, or did Fix-3 garble escalation fire?).
      3. Require **zero disagreements** on all non-text_based corpus docs (currently N=5). At N=5 this is a spot-check, not a statistical measurement — the gate strengthens as the corpus grows.
    - No code changes. Process step before production activation.
    - Record results in the [PDF Inspector Viability Report](../../audit/PDF_INSPECTOR_VIABILITY_REPORT.md).
    - _Requirements:_ [RFC-032 D5](../rfcs/032-pdf-inspector-tier1-activation.md#d5-pre-activation-shadow-agreement-measurement) | [Design AD6](../designs/design-rfc032-pdf-inspector-tier1-activation.md#ad6-shadow-agreement-measurement-d5)
  - [x] <a id="62-full-corpus-regression-gate"></a>6.2 Full corpus regression gate ([D6](../rfcs/032-pdf-inspector-tier1-activation.md#d6-full-corpus-regression-gate-pre-activation))
    - Run full 60-doc corpus ingest with `PDF_INSPECTOR_PRECLASSIFY=1`.
    - Compare verdict distribution (PASS/MARGINAL/FAIL) against the `PRECLASSIFY=0` baseline.
    - Any PASS→MARGINAL or PASS→FAIL regression **blocks activation**.
    - No code changes. Process step before production activation.
    - _Requirements:_ [RFC-032 D6](../rfcs/032-pdf-inspector-tier1-activation.md#d6-full-corpus-regression-gate-pre-activation) | [Design AD7](../designs/design-rfc032-pdf-inspector-tier1-activation.md#ad7-corpus-regression-gate-d6) | Phase 2 Activation Report Rec-2b
  - [x] <a id="63-final-checkpoint"></a>6.3 Final Checkpoint
    - Full `uv run pytest` green with all Tier 1 tests passing.
    - Verify [Design Property 1](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-1-flag-gate-inertness): `PDF_INSPECTOR_PRECLASSIFY=0` (default) makes entire D0-D2 decision path inert.
    - Verify [Design Property 5](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-5-validate-tree-unconditional): `validate_tree()` at line 987 remains unconditional.
    - Verify [Design Property 6](../designs/design-rfc032-pdf-inspector-tier1-activation.md#property-6-fix-3-retry-unconditional): Fix-3 retry at lines 1008-1094 remains unconditional.
    - Shadow agreement measurement (6.1) passes with zero disagreements.
    - Full corpus regression (6.2) shows no verdict regressions.
    - RFC-032 status updated to "Implementation Complete".

- [x] <a id="7-batch-6--post-activation-monitoring-d7-d9"></a>7. Batch 6 — Post-Activation Monitoring ([D7](../rfcs/032-pdf-inspector-tier1-activation.md#d7-prometheus-wall-clock-savings-measurement), [D8](../rfcs/032-pdf-inspector-tier1-activation.md#d8-shadow-deployment-window-1-2-weeks-sustained), [D9](../rfcs/032-pdf-inspector-tier1-activation.md#d9-scanned-pdf-wall-clock-timing-calibration))
  - [x] <a id="71-wall-clock-timing-calibration"></a>7.1 Scanned-PDF wall-clock timing calibration ([D9](../rfcs/032-pdf-inspector-tier1-activation.md#d9-scanned-pdf-wall-clock-timing-calibration))
    - Measured OCR-pass (`force_full_page_ocr=True`, tesseract ara+eng) vs text-layer-pass wall-clock on the 4 scanned corpus docs via `pdf_to_markdown_docling()`: MOU MOHRE (9pp) 2.32x, SLA Ministry of Economy (20pp) 4.26x, Cabinet Resolution 1/2022 (21pp) 11.00x, Cabinet Resolution 106/2022 (15pp) 7.08x — mean 6.16x, max 11.00x.
    - Measured ratio exceeds both the 3x D3 baseline and the D9 5x recalibration threshold.
    - Multiplier adjusted per D9 formula `max(observed_ratio * 1.5, 3.0)`, `observed_ratio` = max 11.00x -> 16.5x, applied in `worker.py::_run_converter_subprocess()` (`effective_timeout *= 3` -> `*= 16.5`).
    - _Requirements:_ [RFC-032 D9](../rfcs/032-pdf-inspector-tier1-activation.md#d9-scanned-pdf-wall-clock-timing-calibration) | Grilling Report I3, Q4
  - [x] <a id="72-prometheus-savings-measurement"></a>7.2 Prometheus wall-clock savings measurement ([D7](../rfcs/032-pdf-inspector-tier1-activation.md#d7-prometheus-wall-clock-savings-measurement))
    - After `PRECLASSIFY=1` is active in production, compare per-document processing time for scanned/image_based PDFs vs the `PRECLASSIFY=0` baseline.
    - Validate that modeled savings of ~600-2000ms per affected document are achieved.
    - Use existing `PDF_INSPECTOR_LATENCY` histogram + ingestion timing metrics.
    - No code changes. Analysis step during shadow deployment window.
    - _Requirements:_ [RFC-032 D7](../rfcs/032-pdf-inspector-tier1-activation.md#d7-prometheus-wall-clock-savings-measurement) | Phase 2 Report Sec3-criterion-savings, Rec-4
  - [x] <a id="73-shadow-deployment-window"></a>7.3 Shadow deployment window (1-2 weeks sustained) ([D8](../rfcs/032-pdf-inspector-tier1-activation.md#d8-shadow-deployment-window-1-2-weeks-sustained))
    - Run `PDF_INSPECTOR_PRECLASSIFY=1` in production for 1-2 weeks.
    - Monitor for: timeout failures on scanned/image_based docs, false-positive classifications, unexpected verdict regressions, and Prometheus savings vs modeled estimates.
    - Daily check-ins on Prometheus dashboards.
    - No code changes. Operational monitoring step.
    - _Requirements:_ [RFC-032 D8](../rfcs/032-pdf-inspector-tier1-activation.md#d8-shadow-deployment-window-1-2-weeks-sustained) | Viability Report Sec9.8-next-step-3

## Notes

- **D0 (Threading):** Trivial plumbing — the `pdf_classification` dict already exists in `converters_cli` scope. An explicit parameter is testable and self-documenting vs. attribute injection ([RFC-032 D0](../rfcs/032-pdf-inspector-tier1-activation.md#d0-thread-pdf_classification-from-converters_cli-into-clientindex)).
- **D1 (Decision Logic):** The 0.90 confidence threshold is hardcoded, not configurable. Upstream pdf-inspector has no threshold configuration (issues #266/#267/#254). The threshold is conservative: all scanned docs report >= 0.950, all text_based report >= 0.750 ([RFC-032 D1](../rfcs/032-pdf-inspector-tier1-activation.md#d1-document-level-ocr-routing-decision-in-index)).
- **D2 (Converter Loop):** The `force_full_page_ocr` parameter is already fully wired through `converters.py -> _build_pdf_pipeline_options -> TesseractCliOcrOptions`. No converter-level changes needed. The `inspector_force_ocr` and `pre_garbled` signals are independent and non-conflicting. **Implementation note:** Structure `inspector_force_ocr` as `elif` after the `pre_garbled` branch. The Prometheus counter should only increment when `inspector_force_ocr` is the *effective* trigger, not when `pre_garbled` already forced OCR — this avoids inflating savings attribution ([RFC-032 D2](../rfcs/032-pdf-inspector-tier1-activation.md#d2-converter-loop-wiring--force-ocr-on-first-pass)).
- **D3 (Worker Timeout):** Optional but recommended. Scanned/image-based docs going OCR-first are 3-10x slower; the 3x multiplier covers the lower end of that range and applies to both `scanned` and `image_based` (matching D1/D2 scope) ([RFC-032 D3](../rfcs/032-pdf-inspector-tier1-activation.md#d3-worker-timeout-multiplier)).
- **Risk 1 (False positive):** A false-positive `scanned` classification on a text-based PDF is low-impact: OCR output is slower but valid, and `validate_tree()` judges the result regardless ([RFC-032 Risks](../rfcs/032-pdf-inspector-tier1-activation.md#risks)).
- **Risk 4 (Regression):** All new conditionals mirror the proven `pre_garbled` pattern. Full test coverage in Batch 4 mitigates ([RFC-032 Risks](../rfcs/032-pdf-inspector-tier1-activation.md#risks)).
- **Risk 5 (Agreement assumption):** D5 shadow agreement measurement is mandatory before production flag flip. Without it, the savings estimate is unvalidated ([RFC-032 Risks](../rfcs/032-pdf-inspector-tier1-activation.md#risks)).

## Task Dependency Graph

```json
{
  "waves": [
    {
      "wave": 1,
      "tasks": ["1.1", "1.2", "2.1", "2.2", "3.1", "4.1"],
      "description": "All production code: D0 threading, D1 decision logic + metrics, D2 converter wiring, D3 timeout. Run `uv run pytest` after wave completes.",
      "checkpoint": "uv run pytest — all existing tests green, no behavioral change for PRECLASSIFY=0."
    },
    {
      "wave": 2,
      "tasks": ["5.1", "5.2", "5.3"],
      "description": "All tests: unit tests for decision matrix, integration tests for safety nets, remote-path test. Run `uv run pytest` after wave completes.",
      "checkpoint": "uv run pytest tests/test_pdf_inspector_tier1.py green + full suite green."
    },
    {
      "wave": 3,
      "tasks": ["6.1", "6.2", "7.1"],
      "description": "Pre-activation measurements: D5 shadow agreement, D6 corpus regression, D9 wall-clock timing calibration. All are process steps, no code changes.",
      "checkpoint": "Zero disagreements on N=5 non-text_based docs. No verdict regressions on full 60-doc corpus. Timeout multiplier value confirmed or adjusted based on measured OCR time."
    },
    {
      "wave": 4,
      "tasks": ["7.2", "7.3", "6.3"],
      "description": "Post-activation monitoring: D7 Prometheus savings measurement, D8 shadow deployment window (1-2 weeks sustained), final checkpoint.",
      "checkpoint": "Measured savings corroborate modeled estimates. No timeout failures or false positives during sustained shadow window. RFC-032 status updated to Implementation Complete."
    }
  ]
}
```
