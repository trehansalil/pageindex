# RFC-046 Wave 6 Gate Checkpoint (6.C)

**Date:** 2026-09-21
**Branch:** `ICR-97-rfc46-ocr-attribution-cluster-remediation`
**Commit (pre-gate):** pending (tasks 6.1–6.3 complete, checkpoint in progress)
**Baseline:** 5.C checkpoint (post-D7, 2026-09-21)

## Summary

Wave 6 (D4 Content-Derived OCR Language Selection) implemented three tasks:

| Task | Description | Status |
|------|-------------|--------|
| 6.1 | Make garble recovery reachable for image inputs | Done |
| 6.2 | Bounded detect-correct-retry in image OCR path | Done |
| 6.3 | Tests + AST guard conformance for D4 | Done |

## Architecture Guards

- `test_architecture_guards.py`: **79 passed**, 0 failed
- `test_facade_surface_guard.py`: **98 passed**, 0 failed
- `test_d4_corrective_retry.py`: **15 passed**, 0 failed
- `test_d7_arbitration.py`: passes (no regression)
- `test_recovery.py`: **291 passed** (combined with other test files)

## Targeted Corpus Results (3 D4-relevant documents)

### اتفاقية مستوى الخدمة (sha8=e16412fe)

| Metric | 5.C Baseline | Post-D4 (6.C) | Delta |
|--------|-------------|---------------|-------|
| Verdict | **PASS** | **PASS** | unchanged |
| Route | tree | tree | — |
| Chars/page | 1728.45 | 1728.45 | — |
| Garble ratio | 0.0 | 0.0 | — |
| Nodes / Depth | 108 / 4 | 108 / 4 | — |

**Attribution:** D7 already resolved this doc in 5.C. D4 does not change the verdict. Landscape reroute guard (`skip_tree_already_passed`) prevents downgrade.

### MOU MOHRE & Nafis (sha8=7ab5bdf3)

| Metric | 5.C Baseline | Post-D4 (6.C) | Delta |
|--------|-------------|---------------|-------|
| Verdict | **FAIL** | **FAIL** | unchanged |
| Route | persist_fail | persist_fail | — |
| Primary defect | suspect_density | suspect_density | — |
| Chars/page | 1437.7 | 1437.7 | — |
| Corrected chars/page | 1579.9 | 1579.9 | — |
| Garble resolved | yes (OCR retry) | yes (OCR retry) | — |

**Attribution:** Density gate is binding (`chars_per_page=1437.7 < 1500`). D4 does not change the verdict. D8 (corrected numerator) is required to clear this gate (`verdict_would_change=true` with corrected 1579.9).

### وارد رقم 597 (sha8=305e8ca9)

| Metric | 5.C Baseline | Post-D4 (6.C) | Delta |
|--------|-------------|---------------|-------|
| Verdict | **REJECTED** | **FAIL** | **improved** |
| Route | rejected (flat garble) | flat | flat persisted |
| Primary defect | garbling (flat) | rtl_reversal | garble → RTL |
| Garble ratio (tree) | — | 0.0 | resolved |
| Chars/page | — | 1913.7 | — |
| Nodes / Depth | — | 107 / 3 | — |
| Flat garble unrecovered | true (5.C) | false (6.C) | resolved |
| Per-block sparse_mojibake | — | 5/297 blocks | residual |

**Attribution:** D4 resolves tree-level garble through OCR retry (garble_ratio=0.0). The `flat_garble_unrecovered_reject` no longer fires — doc proceeds to flat route instead of being rejected. Primary defect shifts from garbling to `rtl_reversal` + `bidi_degraded`. RTL repair does not converge (`bidi_norm_v2`). Residual `sparse_mojibake` on 5/297 flat blocks is from image OCR text (D6 task 4.3 exposure), below the rejection threshold.

**Movement: REJECTED → FAIL (improvement).** This is a two-step gain: (1) garble resolved by OCR retry, (2) doc persisted via flat route. RTL is the next binding defect.

## Gate Decision

| Criterion | Status |
|-----------|--------|
| Architecture guards green | ✅ 79 + 98 + 15 passed |
| No regressions vs 5.C | ✅ اتفاقية PASS→PASS, MOU MOHRE FAIL→FAIL |
| At least one improvement | ✅ وارد REJECTED→FAIL |
| D4 changes scoped to intent | ✅ Image recovery eligibility + corrective retry only |

**6.C GATE: PASS.** Wave 6 (D4) may proceed to commit. Remaining defects (density, RTL reversal) are attributed to D8 and bidi normalization respectively — outside D4 scope.

## Implementation Evidence

### 6.1 — Image recovery eligibility
- `recovery.py`: Widened ext guards in `_recover_garble_ocr`, `_recover_low_content_ocr`, `_recover_image_dominant_ocr`, `_recover_rtl_repair`, `_recover_vlm_fallback` from `ext != ".pdf"` to `ext != ".pdf" and ext not in _IMAGE_EXTS`
- `recovery.py`: Added `_IMAGE_EXTS` import from `.images`, added `image_to_markdown` import from `..converters`
- `recovery.py`: Added image-specific OCR dispatch branch in `_execute_ocr_retry` (`image_tesseract` decision choice)

### 6.2 — Bounded detect-correct-retry
- `indexer.py`: Added `d4_corrective_retry` block after initial OCR in image path — detects content-derived langs, re-OCRs once if they differ from filename-derived, uses `arbitrate()` to pick winner
- `indexer.py`: Imported `Candidate` and `arbitrate` from `..helpers`
- `decision_points.py`: Registered `d4_corrective_retry` event with 5 choices and 7 attrs

### 6.3 — Tests
- `tests/test_d4_corrective_retry.py`: 15 tests covering image recovery eligibility (AST guards), corrective retry (arbitration), decision point registration, architecture guard conformance
- `tests/TEST_INDEX.yaml`: Registered under `client/recovery.py` and `client/indexer.py`
- `decision_points.py`: Updated `ocr_retry_dispatch_route` to include `image_tesseract` choice
