# RFC-032 Grilling Report: pdf-inspector Tier 1 Activation

**Date:** 2026-08-06
**Reviewer model:** Opus (complex analysis)
**Artifacts reviewed:** RFC-032, Design, Tasks, Viability Report, Phase 2 Activation Report
**Code verified against:** `client.py`, `converters_cli.py`, `worker.py`, `config.py`, `metrics.py` (via Serena LSP + CodeGraph + raw reads)

---

## CRITICAL FINDINGS

### C1: D3 timeout multiplier scope does not match D1/D2 OCR forcing scope

**The gap:** D1/D2 forces OCR for both `"scanned"` AND `"image_based"` documents (confidence >= 0.90). But D3 only applies the 2x timeout multiplier when `pdf_type == "scanned"`. An `image_based` PDF that gets forced OCR via D1/D2 runs with the *text-layer-sized* timeout, not the OCR-sized timeout.

**Why this matters:** The RFC's own rationale for D3 is: "Scanned docs going straight to full-page OCR on first pass are 3-10x slower than text-layer extraction." This rationale applies equally to `image_based` docs forced to OCR — they hit the same Tesseract/Docling OCR path.

**Evidence:**

- RFC D1 (line 48): `pdf_classification["pdf_type"] in ("scanned", "image_based")`
- RFC D3 (line 85): `pdf_type == "scanned"` — no mention of `image_based`
- Design AD4: "2x timeout multiplier for scanned classifications" — silent on `image_based`
- Task 4.2 checkpoint: "Confirm timeout is unchanged for `text_based`, `image_based`, `mixed`" — explicitly expects `image_based` to get NO multiplier

**Question:** Is this intentional? If `image_based` docs get forced to full-page OCR (D2), why wouldn't they also need the timeout safety margin? If all `image_based` docs are expected to be fast despite OCR, that assumption should be stated and justified.

---

### C2: Zero `image_based` documents in the 60-doc validation corpus

The RFC gates OCR forcing on `pdf_type in ("scanned", "image_based")`, but the 60-PDF corpus contains:

- 55 `text_based`
- 4 `scanned`
- 1 `mixed`
- **0 `image_based`**

There is zero empirical evidence that the `image_based` classification behaves correctly, that the confidence thresholds are calibrated for it, or that forced OCR on `image_based` docs produces valid trees. The design makes routing decisions for a pdf_type that has never been observed in the corpus.

**Question:** Should `image_based` be included in the D1 predicate at all, given no validation data? If yes, what is the justification for trusting it without testing? If a future `image_based` doc at confidence 0.91 gets forced to OCR and produces garbage, the RFC claims `validate_tree()` catches it — but has this been verified for this specific path?

---

## IMPORTANT FINDINGS

### I1: D5 sample size is statistically meaningless for a ">=99% agreement" claim

D5 proposes testing agreement on "4 scanned + 1 mixed corpus docs" — that's 5 documents. With N=5:

- A single disagreement = 80% agreement (fails the 99% gate)
- Zero disagreements = 100% agreement (passes trivially)

This is a binary pass/fail on 5 samples, not a statistical measurement. You cannot distinguish 99% agreement from 90% agreement with N=5. The test proves nothing that a single spot check wouldn't. The ">=99% agreement" threshold is meaningless at this sample size — it's really "zero disagreements on 5 docs."

**Question:** Is the team aware this is a binary check, not a statistical measurement? Should the RFC be honest and restate D5 as "zero disagreements on the 5 non-text_based corpus docs" rather than implying statistical rigor with ">=99%"? Or should more scanned/mixed documents be acquired before promotion?

---

### I2: Viability Report Section 4.7 contradicts the RFC's implementation approach

The Viability Report (Section 4.7, Implementation Checklist) says:

> "Implement text-based routing: pass `DOCLING_DO_OCR=0` if confidence >= 0.95"

But the RFC and Design use `force_full_page_ocr=True` as a function parameter, never mentioning `DOCLING_DO_OCR` env var manipulation. These are different mechanisms:

- `force_full_page_ocr` is a parameter wired through `_build_pdf_pipeline_options` (per-call)
- `DOCLING_DO_OCR` is a process-wide env var (global side effect)

The RFC correctly chose the per-call parameter approach, but the Viability Report's checklist was never updated. This creates confusion — someone reading the Viability Report's checklist would implement the wrong approach.

**Question:** Should the Viability Report's Section 4.7 be amended or marked superseded by RFC-032?

---

### I3: The 2x timeout multiplier is arbitrarily chosen and under-justified

The RFC states: "Scanned docs... are 3-10x slower than text-layer extraction" but then applies only a 2x multiplier. The rationale says "total wall-clock is similar" because previously the doc would fail fast then retry — but this assumes the retry path was always hitting the timeout boundary, which it wasn't (the retry was a second full conversion, not a timeout-recovery).

If a scanned doc takes 5x longer on OCR-first (within the stated 3-10x range), a 2x multiplier is insufficient. The existing `effective_timeout` (1770s or dynamic) multiplied by 2 might still not cover a 5-10x slowdown on a large document.

**Question:** What data supports 2x specifically? Shouldn't this be `max(2, estimated_ocr_slowdown_factor)` or configurable? Was 2x chosen because it's "probably enough" or because it was measured?

---

## MODERATE FINDINGS

### M1: Hardcoded 0.90 threshold has no runtime escape hatch

The 0.90 confidence threshold is hardcoded in `index()`. If the threshold needs adjustment (e.g., a new document type has different confidence characteristics, or pdf-inspector updates its confidence calibration), it requires a code change, rebuild, and redeploy.

The RFC explicitly says "Never add configurable confidence threshold (upstream #266/#267)" in Non-Goals — but this conflates two things: (a) upstream pdf-inspector having a configurable threshold, and (b) PageIndex having a configurable threshold for its own routing decision. The RFC could expose `PDF_INSPECTOR_CONFIDENCE_THRESHOLD` as an env var without waiting for upstream.

**Question:** Is the explicit non-goal of configurability a deliberate risk acceptance, or was it conflated with the upstream issue? The env var pattern is already established for the flag itself (`PDF_INSPECTOR_PRECLASSIFY`).

---

### M2: The `pre_garbled` and `inspector_force_ocr` interaction is under-specified for edge cases

The RFC says: "Both signals fire independently. If both fire, result is same: first-pass OCR. No conflict."

But is it truly no conflict? When `pre_garbled` fires, the garble-gate machinery also sets `ocr_lang_override` and potentially other state. When `inspector_force_ocr` fires, D2 also sets `ocr_lang_override=detect_ocr_langs(filename)`. If both fire:

- Is `detect_ocr_langs()` called twice?
- Do the two `ocr_lang_override` values always agree?
- Is there any side effect (counter increment, logging) that doubles?

The Prometheus counter `pdf_inspector_preclassify_forced_ocr_total` increments for inspector-forced OCR. If `pre_garbled` also fires for the same doc, the counter still increments — but the inspector's forcing was redundant (garble-gate would have forced OCR anyway). This inflates the "savings" metric.

**Question:** Should the counter only increment when `inspector_force_ocr=True AND pre_garbled=False` (i.e., cases where the inspector actually saved a retry)?

---

### M3: `converters_cli.py::main()` does NOT pass `pdf_classification` to `index()` — verified as expected

The current code at `converters_cli.py` line ~131:

```python
doc_id = await client.index(args.input_path)
```

This confirms the RFC's claim that `pdf_classification` is currently not threaded through. D0 will add the kwarg. However, the `pdf_classification` variable IS in scope (from `probe_conversion_route()` at line ~100) — verified via Serena. The threading is indeed trivial.

**Status:** Confirmed correct — no issue here, just verification.

---

### M4: Viability Report handshake schema is stale

The Viability Report Section 3 shows a handshake with flat fields:

```json
{"handshake": true, "pdf_classification": "scanned_with_text", "pdf_confidence": 0.94, ...}
```

But the actual implementation (verified in `converters_cli.py::main()` lines 106-108) uses a nested dict:

```json
{"handshake": true, "pdf_classification": {"pdf_type": "scanned", "confidence": 0.95, ...}}
```

These are different schemas. The Viability Report predates the RFC-031 implementation and was never updated.

**Question:** Should the Viability Report be annotated as superseded, or should Section 3 be corrected to match the actual implementation?

---

### M5: Task dependency graph has 12 waves for 14 tasks — overstructured

The task plan creates 12 sequential waves, with every checkpoint being a full wave. This means:

- Wave 1: Tasks 1.1, 1.2 (parallel, ~3 lines each)
- Wave 2: Task 1.3 (checkpoint)
- Wave 3: Tasks 2.1, 2.2 (parallel)
- Wave 4: Task 2.3 (checkpoint)
- ...

For ~30 LOC of implementation, this is 12 serialization barriers. A competent implementer could execute D0-D3 in a single pass (~20 minutes), run the test suite, and be done. The checkpoint-after-every-batch pattern adds process overhead without proportional safety — the "safety" is really just `uv run pytest` between each batch.

**Question:** Could waves 1-8 (all implementation) be collapsed into 2-3 waves? The checkpoints are all "run pytest" — they're not independent verification steps.

---

## VERIFICATION SUMMARY

| Claim in RFC/Design                                                           | Verified Against                     | Status                                               |
| ----------------------------------------------------------------------------- | ------------------------------------ | ---------------------------------------------------- |
| `PDF_INSPECTOR_PRECLASSIFY` defined at config.py lines 21-23                | Read config.py:21-23                 | **CONFIRMED**                                  |
| `PDF_INSPECTOR_PRECLASSIFY` has zero consumers                              | CodeGraph search + Serena            | **CONFIRMED** (dead code)                      |
| `index()` signature at line 667                                             | Serena: line 666                     | **CONFIRMED** (off-by-one on decorator vs def) |
| `probe_conversion_route()` returns `pdf_classification`                   | CodeGraph + Serena converters_cli.py | **CONFIRMED**                                  |
| `pdf_classification` not passed to `index()` currently                    | Serena converters_cli.py:131         | **CONFIRMED**                                  |
| Existing`PDF_INSPECTOR_CLASSIFICATIONS` + `PDF_INSPECTOR_LATENCY` metrics | Read metrics.py                      | **CONFIRMED**                                  |
| No`PDF_INSPECTOR_FORCED_OCR` counter exists yet                             | Read metrics.py symbol overview      | **CONFIRMED**                                  |
| `_run_converter_subprocess` handshake parses `pdf_classification`         | Serena worker.py:311-319             | **CONFIRMED**                                  |
| `force_full_page_ocr` already wired through converters.py                   | CodeGraph trace                      | **CONFIRMED** (per RFC-031)                    |
| 60-doc corpus: 55 text_based, 4 scanned, 1 mixed, 0 image_based               | Viability Report Section 9.5         | **CONFIRMED**                                  |
| Scanned doc confidence floor: 0.950                                           | Viability Report Section 9.1         | **CONFIRMED**                                  |
| Text_based confidence floor: 0.750                                            | Viability Report Section 9.1         | **CONFIRMED**                                  |

---

## QUESTIONS FOR THE AUTHOR

1. **C1:** Why does D3 timeout only cover `scanned` but not `image_based`, when D1/D2 forces OCR for both?
2. **C2:** How do you validate the `image_based` routing path with zero corpus examples?
3. **I1:** Is the ">=99% agreement on 5 docs" test honest about its statistical power?
4. **I3:** What data supports the 2x timeout multiplier specifically?
5. **M1:** Is the hardcoded 0.90 threshold a deliberate risk acceptance or an oversight?
6. **M2:** Should the Prometheus counter distinguish inspector-only forcing from redundant forcing?
7. **M5:** Is the 12-wave task structure proportionate to 30 LOC of changes?
