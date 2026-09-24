# RFC-033 D2 Part B / Task 9.1 Operational Gate — Scoped Arabic Re-Ingest

**Superseded task:** RFC-033 Task 9.1. Executed here as RFC-034 Task 13.10 (D21), which supersedes it per the RFC-034 task list.
**Date:** 2026-08-09
**Run type:** Operational gate (0 code lines). Measures the currently-landed `BIDI_COHERENCE_ENFORCE` / `bidi_degraded` / `classify_verdict` capping logic (helpers.py, commit `932d634`+) against the pre-registered sampling frame.

## 1. Sampling frame (pre-registered, per D13 unbiased-frame requirement)

Selected before results were inspected, per the design's D21 frame definition:

| Label | Document | Selection reason |
|---|---|---|
| R5 | قرار مجلس الوزراء رقم (106) لسنة 2022 | Run-16 R5 — MARGINAL→FAIL, 40% Latin mojibake, garble gate reported 0 |
| R6 | مرسوم بقانون اتحادي رقم (13) لسنة 2022 | Run-16 R6 — PASS→FAIL, 36% Latin OCR garbage, garble gate reported 0 |
| S5 | سياسة حوكمة و إدارة البيانات | Run-16 stall S5 — stored PASS vs audit FAIL, garble gate (single-letter) |
| S6 | قرار مجلس الوزراء رقم (1) لسنة 2022 | Run-16 stall S6, reversed-heading Arabic |
| S7 | وارد رقم 597 من مكتب أبوظبي التنفيذي | Run-16 stall S7 — garble_blocks=0 despite garbled text |
| control | مرسوم بقانون اتحادي رقم (33) لسنة 2021 | Clean Arabic control (design.md:597) — negative-test: must not false-trigger |
| control | cabinet_resolution_no_96_of_2023 | R3 Arabic control (design.md:973) |

All 7 files confirmed present in `doc_store/` and in the processed-doc store (MinIO `processed/`) prior to measurement; no substitution occurred.

## 2. Method

For each frame document, loaded the currently-persisted tree (`load_doc`) and ran, unmodified, against the flattened text:

- `_check_bidi_coherence(flat_text)` — the RFC-033 D2 Part B detector, gated behind `BIDI_COHERENCE_ENFORCE` (default `"true"`, helpers.py:1386) inside `validate_tree`.
- `validate_tree(structure, expected_script="Arab")` and `classify_verdict(structure, content_class, validate_reason)` — the full landed pipeline, to confirm 9.2/9.3 wiring (verdict capped to MARGINAL when `bidi_degraded` fires).
- `_tree_is_garbled(structure, expected_script="Arab")` — the binary garble gate, with `expected_script` **correctly threaded** (unlike `classify_verdict`'s internal call, see §4).

## 3. Raw measurement

| Doc | doc_script (inferred) | `binary_garbled` (Arab-threaded) | `bidi_ok` | `bidi_reason` | classify_verdict (re-run) |
|---|---|---|---|---|---|
| R5 قرار 106 | Arab | **False** | **True** | — | PASS |
| R6 مرسوم 13 | Arab | **False** | **True** | — | PASS |
| S5 سياسة حوكمة | Arab | False | True | — | PASS |
| S6 قرار (1) | Arab | False | True | — | PASS |
| S7 وارد 597 | Arab | False | True | — | PASS |
| control مرسوم 33 | Arab | False | True | — | PASS |
| control cabinet_96 | Latn | False | True | — | PASS |

**`bidi_coherence_violations` across the frame: 0 / 7.** No document in the frame — including R5 and R6, both independently confirmed by the Run-16 audit to contain visible Latin mojibake in Arabic-script text — triggered `bidi_degraded`.

This is a lower-bound-only measurement (frame drawn from documents already known to be affected, not a corpus-wide random sample), consistent with the design's caveat at helpers.py:1375-1378.

## 4. 9.2/9.3 wiring validation

- `BIDI_COHERENCE_ENFORCE` defaults `"true"` — confirmed (helpers.py:1386).
- `validate_tree` returns `bidi_degraded` when `_check_bidi_coherence` fails — confirmed by code path (helpers.py:1392); **not exercised** by this frame since `_check_bidi_coherence` never failed.
- `classify_verdict` caps a would-be PASS at MARGINAL when `validate_reason == "bidi_degraded"` — confirmed by code path (helpers.py:1634-1639); **not exercised** by this frame for the same reason.

**Verdict: PASS/FAIL split.** The 9.2/9.3 *wiring* is correctly implemented and would fire on a positive case (verified by direct code read of the conditional chain — not by an observed positive in this frame, since none occurred). The *detector* (`_check_bidi_coherence` and, separately, `_tree_is_garbled`'s Latin-gibberish prong) does not fire on any of the 7 known-affected documents. Per D21's own instruction, this triggers step 5's escalation.

## 5. Escalation: two independent gaps, not one

RFC-034 D21 hypothesized the gap is `classify_verdict()` hardcoding `expected_script=None` into `_garble_ratio()` (helpers.py:1693), while `validate_tree` threads `expected_script` correctly. **That hardcoding is confirmed by direct code read**: `classify_verdict` (helpers.py:1590) has no `expected_script` parameter at all, and line 1693 passes `expected_script=None` unconditionally.

However, measurement in §3 shows a **second, independent gap**: even with `expected_script="Arab"` forced correctly (the code path `validate_tree` already uses), `_tree_is_garbled` still returns `False` on R5 and R6. Root cause, traced with `_latin_token_ratio` on the actual stored text:

| Doc | Latin-token ratio of flat text | Threshold to trigger (`GARBLE_LATIN_RATIO`) | Nonsense fraction of Latin tokens | Threshold (`GARBLE_NONSENSE_RATIO`) |
|---|---|---|---|---|
| R5 قرار 106 | 0.03 | 0.4 | 0.44 | 0.7 |
| R6 مرسوم 13 | 0.06 | 0.4 | 0.33 | 0.7 |

The Latin-gibberish prong (`_is_garbled_blob`, helpers.py:943-954) requires Latin tokens to be **>40% of the whole document's tokens** before it even evaluates nonsense-morphology. The Run-16 audit's "40%/36% Latin mojibake" figures describe the fraction of *affected nodes/blocks* within the document (a handful of OCR-mangled `> [Chart text]:` injections), not the fraction of the *whole flattened document's tokens*. A short, dense mojibake block gets diluted below the 40%-of-tokens gate by tens of thousands of surrounding clean Arabic characters — the detector is scaled for a document that is *mostly* Latin gibberish, not one with concentrated, localized gibberish nested in otherwise-clean prose.

**Both gaps must be fixed for R5/R6 to be caught:**
1. Thread `expected_script` through `classify_verdict()` → `_garble_ratio()` (the RFC-034 D21-anticipated fix).
2. Even after (1), the ratio-based Latin-gibberish prong needs a node/window-local check (matching what `_garble_check_nodes` already does per-node in `validate_tree`, helpers.py:1171-1216) rather than — or in addition to — the whole-document ratio, or the localized-block dilution described above will continue to mask R5/R6-shaped defects.

Scoped to a follow-on RFC per D21's instruction; not implemented here (D21 is code-line-0 by design).

## 6. Deliverables checklist (per D21 Test Strategy)

- [x] Pre-registered sampling frame (§1).
- [x] Raw `bidi_coherence_violations` counts per document (§3): 0/7.
- [x] Explicit pass/fail statement on 9.2/9.3 enforcement (§4): wiring correct, detector under-sensitive on this frame.
- [x] Escalation evidence with line-level code references (§5), including a second gap (ratio dilution) beyond the RFC's anticipated `expected_script=None` gap.

**Gate result: RFC-033 Batch 4 Checkpoint / Final Checkpoint condition met** — the measurement required to close them is now recorded. Both checkpoints close on a documented 0-violation reading plus the required escalation, per D21 step 5/6, not on a positive detection (none occurred in this frame).

RFC-033 Task 9.1 is superseded by this document (RFC-034 Task 13.10 / D21).
