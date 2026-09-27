# RFC-052 conversion benchmark (2026-09-27)

Backend: `http://10.43.47.186:8080` · runs per arm: 2 (medians, plus 1 discarded warm-up) · script: `scripts/conversion_bench.py`

| doc | arm | pages | median s | s/page | verdict | garbled | garble ratio | cells | cell delta vs baseline | changed vs baseline | changed vs r3 (gate) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| pocketbook | baseline | 292 | 757.7 | 2.59 | PASS | False | 0.000 | 72113 | 0 | 0.00% |  |
| pocketbook | r3 | 292 | 741.7 | 2.54 | PASS | False | 0.000 | 72113 | 0 | 0.00% |  |
| pocketbook | r3_fast | 292 | 314.4 | 1.08 | PASS | False | 0.000 | 70550 | -1563 | 73.24% | 73.24% |
| scanned | baseline | 15 | 92.9 | 6.19 | PASS | False | 0.000 | 139 | 0 | 0.00% |  |
| scanned | r3 | 15 | 93.2 | 6.21 | PASS | False | 0.000 | 139 | 0 | 0.00% |  |
| scanned | r3_fast | 15 | 90.2 | 6.02 | PASS | False | 0.000 | 145 | 6 | 11.72% | 11.72% |
| arabic | baseline | 10 | 25.9 | 2.59 | PASS | False | 0.000 | 151 | 0 | 0.00% |  |
| arabic | r3 | 10 | 26.0 | 2.60 | PASS | False | 0.000 | 151 | 0 | 0.00% |  |
| arabic | r3_fast | 10 | 20.6 | 2.06 | PASS | False | 0.000 | 161 | 10 | 18.01% | 18.01% |

## Noise floor (baseline vs baseline, first two timed runs)

Same settings, different runs -- how much of any r3_fast-vs-r3 diff could be non-determinism rather than a real TableFormer-mode effect.

- pocketbook: changed-cell ratio 0.00% across 2 baseline runs
- scanned: changed-cell ratio 0.00% across 2 baseline runs
- arabic: changed-cell ratio 0.00% across 2 baseline runs

## R4 AC4: may FAST become the default?

Rule: on every document, the r3_fast-vs-r3 changed-cell ratio is <= 2%, no verdict gets worse and garble does not increase by more than 0.001 (float-noise tolerance). Baseline numbers above are context only -- they also differ in chunking and OCR policy, so they are not the gate input.

**Result: NO -- FAST stays opt-in.**

- pocketbook: changed-cell ratio (r3_fast vs r3) 73.24% > 2%
- scanned: changed-cell ratio (r3_fast vs r3) 11.72% > 2%
- arabic: changed-cell ratio (r3_fast vs r3) 18.01% > 2%

Per-run verdicts and seconds:

```json
{
  "pocketbook/baseline": {
    "verdicts": [
      "PASS",
      "PASS"
    ],
    "seconds": [
      759.9,
      755.6
    ]
  },
  "pocketbook/r3": {
    "verdicts": [
      "PASS",
      "PASS"
    ],
    "seconds": [
      742.6,
      740.9
    ]
  },
  "pocketbook/r3_fast": {
    "verdicts": [
      "PASS",
      "PASS"
    ],
    "seconds": [
      316.5,
      312.4
    ]
  },
  "scanned/baseline": {
    "verdicts": [
      "PASS",
      "PASS"
    ],
    "seconds": [
      93.5,
      92.2
    ]
  },
  "scanned/r3": {
    "verdicts": [
      "PASS",
      "PASS"
    ],
    "seconds": [
      92.2,
      94.1
    ]
  },
  "scanned/r3_fast": {
    "verdicts": [
      "PASS",
      "PASS"
    ],
    "seconds": [
      90.4,
      90.1
    ]
  },
  "arabic/baseline": {
    "verdicts": [
      "PASS",
      "PASS"
    ],
    "seconds": [
      25.8,
      25.9
    ]
  },
  "arabic/r3": {
    "verdicts": [
      "PASS",
      "PASS"
    ],
    "seconds": [
      26.3,
      25.7
    ]
  },
  "arabic/r3_fast": {
    "verdicts": [
      "PASS",
      "PASS"
    ],
    "seconds": [
      20.5,
      20.7
    ]
  }
}
```
