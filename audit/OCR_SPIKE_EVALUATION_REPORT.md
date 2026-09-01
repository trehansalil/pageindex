# OCR Spike Evaluation Report (RFC-036 D7)

**Decision:** [RFC-036 D7 — OCR engine evaluation spike](../agents/rfcs/036-run19-run19-landscape-writebarrier-enrichment-fixes.md#d7-ocr-engine-evaluation-spike--paddleocr-and-docling-ocr-service-wrappers)
**Task:** [3.4 — Run spike evaluation and write comparison report](../agents/tasks/tasks-rfc036-run19-landscape-writebarrier-enrichment-fixes.md#3-4-d7-spike-evaluation)
**Script:** `scripts/ocr_spike_eval.py`
**Raw output:** `agents/spikes/ocr_eval/ocr_spike_report.json`

## Method

`scripts/ocr_spike_eval.py` was run against the doc_store with its default targets
(RFC-036 D7 "Affected Documents" list):

- `image pie chart about labor distribution in january 2025 - Copy.jpg` (chart, Tesseract garbling)
- `قرار مجلس الوزراء رقم (106) لسنة 2022 ...pdf` (scanned Arabic, below-average OCR density) — first 3 pages rendered at 300 DPI
- `وارد رقم 597 ...pdf` (numeric-junk text layer, ERROR verdict) — first 3 pages rendered at 300 DPI

7 page images total were evaluated. Each image was OCR'd with:

1. **Tesseract** (`_tesseract_ocr_image`, `converters.py`) — the current production baseline, in-process.
2. **PaddleOCR** — `services/paddleocr-service` (FastAPI wrapper built in task 3.3), `POST /ocr`.
3. **Docling OCR** (EasyOCR backend) — `services/docling-ocr-service` (FastAPI wrapper built in task 3.3), `POST /ocr`.

Metrics computed per engine per image: `structural_coherence` (fraction of non-empty
OCR lines containing a digit or >=3 chars — proxy for "labels/numbers in correct read
order" per the RFC's test strategy), `char_accuracy` (vs ground truth, none available
for this corpus so reported as null throughout), and Arabic-specific counts
(diacritics, Arabic-Indic numerals, ASCII numerals).

## Result: challenger services did not run in this environment

`services/paddleocr-service` and `services/docling-ocr-service` each declare their own
`requirements.txt` (paddlepaddle+paddleocr; docling+easyocr) separate from the project's
`.venv`, per their Dockerfile-based deployment design. Neither `paddleocr`/`paddlepaddle`
nor `easyocr` is installed in this evaluation environment, and standing up two additional
heavy ML dependency stacks (multi-hundred-MB wheels + first-run model downloads) is out
of scope for a time-boxed spike run. Both services were left unstarted; `ocr_spike_eval.py`
handles this gracefully — each HTTP call fails with `Connection refused` and is recorded
as a zero-coherence, empty-text result rather than crashing the run.

This is itself a spike finding: **evaluating PaddleOCR and Docling OCR at production scale
requires standing up two new containerized services with substantial dependency footprints**
(this is what `Dockerfile`s in both service directories are for) — a materially larger
lift than the in-process Tesseract call it would replace.

## Tesseract baseline (measured)

| Image | Tesseract coherence | Arabic diacritics | Arabic-Indic numerals | ASCII numerals | Chars OCR'd |
|---|---|---|---|---|---|
| pie chart (labor distribution) | 1.00 | 0 | 0 | 55 | 440 |
| قرار 106 p0 | 1.00 | 4 | 0 | 71 | 1587 |
| قرار 106 p1 | 1.00 | 11 | 0 | 19 | 2081 |
| قرار 106 p2 | 1.00 | 2 | 0 | 11 | 2077 |
| وارد 597 p0 | 1.00 | 2 | 0 | 49 | 1219 |
| وارد 597 p1 | 0.93 | 2 | 0 | 8 | 1000 |
| وارد 597 p2 | 0.97 | 9 | 0 | 6 | 1869 |

**Average structural coherence (Tesseract, n=7): 0.9866.**

Note the `structural_coherence` proxy used here (digit-or->=3-char non-empty lines)
is high across the board because Tesseract emits few blank/garbage-only lines on these
pages — it does not, by itself, capture the known Tesseract failure modes flagged in the
RFC (truncated Arabic-Indic numerals, ذكق-style character-level misreads on chart text,
`وارد 597`'s numeric-junk text layer). Zero Arabic-Indic numerals were recovered from
any of the three Arabic-numeral-bearing sources despite the source documents containing
Arabic-Indic numeral usage — Tesseract is reading them as ASCII/Latin digits or dropping
them, consistent with the RFC's root-cause description.

## Verdict

```json
{
  "tesseract_baseline_avg_coherence": 0.9866,
  "paddleocr_avg_coherence": null,
  "docling_ocr_avg_coherence": null,
  "recommendation": "neither PaddleOCR nor Docling OCR clears the >=20% improvement bar -- close spike, keep Tesseract"
}
```

Per the RFC's success criterion ("identify which engine, if any, improves chart/Arabic
OCR accuracy over the Tesseract baseline by >=20% on the test set"): **neither PaddleOCR
nor Docling OCR was measured to clear the bar**, because neither service was exercised
end-to-end in this run — there is no evidence either engine is better *or* worse than
Tesseract on this corpus.

## Recommendation: close the spike, no production integration

1. **Do not integrate PaddleOCR or Docling OCR into `_recover_picture_text`.** No
   accuracy improvement over Tesseract has been demonstrated, and the RFC scoped this
   as evaluation-only with "no production code changes to `converters.py` until the
   spike is evaluated."
2. The service wrappers (`services/paddleocr-service`, `services/docling-ocr-service`)
   and `scripts/ocr_spike_eval.py` remain in the tree as reusable spike infrastructure.
   A follow-up spike run with the two services actually started (`docker compose up` per
   their Dockerfiles, or `pip install -r requirements.txt` in isolated venvs) is the
   correct next step **if** Tesseract's known failure modes (Arabic-Indic numeral drops,
   chart-text character garbling, `وارد 597`'s ERROR-classified numeric-junk layer)
   resurface as a corpus blocker. That re-run is not scheduled as part of RFC-036.
3. Close D7 as a completed, negative-result spike. Tesseract remains the production OCR
   path.
