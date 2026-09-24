<!-- Space: CITRA -->
<!-- Title: RFC-040: Verdict Gate & Garble Detection Critical Zone Remediation -->
<!-- Folder: RFCs -->

---
id: "RFC-040"
title: "Verdict Gate & Garble Detection Critical Zone Remediation"
type: rfc
status: draft
date: "2026-08-27"
plan-impact: "yes"
tags:
  - rfc
  - verdict
  - garble
  - wave-4
  - corpus-quality
aliases:
  - "RFC-040"
  - "Verdict-Garble-Critical-Zones"
governs:
  - "[[design-rfc040-verdict-garble-critical-zone-remediation]]"
  - "[[tasks-rfc040-verdict-garble-critical-zone-remediation]]"
supersedes: []
---

## Context

The POST-RUN20 architecture defect zones audit (2026-08-27) identified two CRITICAL-severity zones that survived wave 1–3 remediation with reduced but persistent bug counts:

| Zone | Severity | Bugs (pre) | Bugs (post) | Status |
|---|---|---|---|---|
| 1 — Verdict Gate Threshold / Promotion Override Cascade | critical | 11 | 5 | improved |
| 2 — Garble Detection Cross-Cutting Kernel | critical | 12 | 5 | improved |

Together these zones account for 10 of the 24 remaining attributed bugs and directly caused 7 of Run-20's 10 corpus regressions (4 verdict-gate tightening reclassifications, 2 garble detection surfacing pre-existing corruption, 1 tessdata refusal breaking bilingual recovery).

The zones share a generative mechanism: **layered threshold parameters and competing promotion paths whose interactions create a ratchet** — every softening change reveals masked defects, every hardening change causes regressions against previously-masked boundaries. Wave 1–3 fixes reduced bug counts but did not eliminate the structural coupling that produces the ratchet.

## Problem Statement

### Zone 1: Verdict Gate — Image-Enrichment Bypass of Structural Hard-Fail

**Root cause:** `apply_promotions` (verdict.py:402–513) evaluates `_try_image_enrichment` BEFORE the structural hard-fail check on `max_leaf_ratio`. When image enrichment fires (priority=100), `_has_image_rescue` (line 461) suppresses the hard-fail entirely — even for documents with 38 chars, zero structural depth, and garbled content.

```python
# verdict.py:461-471 — the bypass
_has_image_rescue = any(c.path_name == "image_enrichment_promoted" for c in candidates)
if not _has_image_rescue and sig.max_leaf_ratio > th.hard_fail_max_leaf_ratio:
    return VerdictResult("FAIL", ...)
```

The `max(candidates, key=lambda c: c.priority)` winner-take-all pattern among six independent `_try_*` functions means priority is a numeric field silently mis-ranked rather than expressed in source-code order. Each threshold change (PASS_MAX_LEAF_RATIO widened 0.17→0.30, hysteresis added, floor checks tightened) invalidates test fixtures written against the prior boundary, producing test failures that look like code bugs but are measurement-calibration drift.

**Corpus evidence (Run-20):** 4 docs reclassified MARGINAL→FAIL after verdict.py fix #3 closed the image-enrichment leniency path for near-empty docs. The fix was correct — the underlying defect is that `_has_image_rescue` still exists for non-near-empty docs, meaning any future threshold change can re-open the same bypass.

**Bugs attributed (5):**
1. `_has_image_rescue` suppresses hard-fail for image-enrichment candidates regardless of content volume
2. `_try_image_enrichment` checks `image_enrichment_ratio >= 0.8` and `total_chars >= min_image_promoted_chars` but not `sig.node_count` or garble status, so 38-char docs can re-enter
3. Six independent `_try_*` functions use numeric priority with `max()` — ordering is implicit and fragile
4. Each threshold change invalidates test fixtures written against the prior boundary
5. Hysteresis reclassified zero-content extraction failures FAIL→MARGINAL, violating HR5

### Zone 2: Garble Detection — Duplicated Logic, Masked Reasons, Tessdata Holes

**Root cause 2a — Duplicated digit-ratio floor:** `_garble_check_nodes` (garble.py:617–709) and `_garble_check_flat_blocks` (garble.py:712–754) both call `detect_garble` → `garble_prongs`, which contains the 500-char digit-ratio floor. However, `_garble_check_nodes` also has a whole-tree concatenated fallback (lines 696–709) that independently checks `len(_concat) >= config.garble_digit_floor` — a second application of the same floor. A fix to the floor value in `garble_prongs` does NOT propagate to the fallback gate, creating drift between tree and flat detection.

**Root cause 2b — GATE_TABLE reason-ordering masks garbling:** `tree_validation.py` GATE_TABLE evaluates garbling (severity=0) first, but the reason-assignment mechanism means a minimal-tree garbled document gets reason=`node_count_low` (severity=1) instead of `garbling`. Since OCR recovery only fires when `reason in ('garbling', 'node_garbling')`, the recovery path is blocked by reason masking.

**Root cause 2c — Tessdata Latin substitution:** `ensure_tessdata` (ocr_langs.py:92–196) now correctly raises `TessdataUnavailableError` for non-Latin missing traineddata, but Latin languages are still silently substituted with `['deu','eng']`. When Arabic OCR falls back to Latin-only tessdata, the resulting Latin mojibake passes all garble prongs — it's not PUA, not glued mixed-script, not digit-heavy, and rarely hits 30% token repetition.

**Root cause 2d — NFKC destroys bidi signal:** The bidi coherence check looks for Arabic Presentation-Forms codepoints (U+FB50–FEFF), but NFKC normalization runs BEFORE the check, decomposing those codepoints. The check is a zero-sensitivity null detector that produced "0 violations" — taken at face value and used to promote `BIDI_COHERENCE_ENFORCE=true`.

**Corpus evidence (Run-20):**
- MOU MOHRE: PASS→ERROR — `ocr_langs.py` fix now refuses Latin-only fallback for mixed-script, breaking the recovery path that previously worked via silent substitution
- القرار التنظيمي: PASS→MARGINAL — garble.py fix surfaced pre-existing Arabic encoding corruption previously invisible
- 2 Arabic scanned-image FAILs (0 chars extracted) — OCR escalation never triggered because reason was `node_count_low` not `garbling`

**Bugs attributed (5):**
1. Duplicated digit-ratio floor between `garble_prongs` and `_garble_check_nodes` fallback
2. GATE_TABLE reason-ordering masks `garbling` with `node_count_low`, blocking OCR recovery
3. Latin tessdata silent substitution produces undetectable mojibake
4. NFKC normalization destroys presentation-form signal before bidi coherence check reads it
5. `token_repetition` false-positive fix excluded non-alphanumeric tokens but did not address numeric-junk or Latin-script mojibake passing undetected

## Proposed Changes

### D1: Unconditional Structural Hard-Fail (Zone 1)

**What:** Move the structural hard-fail check (`max_leaf_ratio > th.hard_fail_max_leaf_ratio`) to the TOP of `apply_promotions`, evaluated BEFORE any `_try_*` function runs. Delete `_has_image_rescue`.

**Why:** Hard-fail must be a gate, not a candidate that competes on priority. Image-enrichment becomes a documented, floor-gated EXCEPTION to hard-fail rather than a bypass.

**How:**
- verdict.py:461–471 — Delete `_has_image_rescue` variable and its conditional block
- verdict.py:~440 — Insert hard-fail check immediately after `image_standalone` early-return, before candidate collection
- verdict.py:220–265 — `_try_image_enrichment` adds explicit `sig.node_count >= 3` and `not sig.effectively_garbled` guards so structurally-empty or garbled docs cannot re-enter via image rescue
- If image-enrichment legitimately needs to override hard-fail for `flat_prose`/`flat_mixed` docs (single-leaf image-dominant docs where `max_leaf_ratio=1.0` is structurally expected), add an explicit named exception with a content-floor AND node-count check, not a priority-bypass

**Lines changed:** ~25 net (15 deleted, 10 added)

### D2: Ordered Promotion Pipeline (Zone 1)

**What:** Replace six independent `_try_*` functions + `max(candidates, key=priority)` with a single ordered `if/elif` chain where priority is expressed in source-code order.

**Why:** Eliminates implicit priority mis-ranking. When a new promotion path is added, its position in the chain makes its priority relationship to other paths explicit at the code level.

**How:**
- verdict.py:450–510 — Replace candidate-collection + `max()` with ordered evaluation:
  1. image-enrichment (only if hard-fail exception conditions met)
  2. structural pass
  3. OCR promotion
  4. flat promotion
  5. content-class promotion
  6. small-doc promotion
- Delete `PromotionCandidate` dataclass and `priority` field
- Each path returns `VerdictResult` directly through `_apply_clamp`

**Lines changed:** ~−60 net (removes dataclass, collection, max-scan)

**Migration risk:** Medium — changes verdict distribution. Requires corpus diff before merge. Test fixtures must be regenerated against new boundaries in the SAME PR to prevent drift.

### D3: Garble Detection Deduplication (Zone 2)

**What:** Remove the independent `garble_digit_floor` check in `_garble_check_nodes`'s whole-tree fallback. The fallback already calls `garble_prongs`, which applies the floor internally.

**Why:** The duplicate floor means a fix to the threshold in `garble_prongs` doesn't propagate to the fallback path, creating silent divergence.

**How:**
- garble.py:696–698 — Remove `if len(_concat) >= config.garble_digit_floor:` guard around the `garble_prongs` call in the fallback. Let `garble_prongs` apply its own floor internally (it already does at line 380).

**Lines changed:** ~−3 net

**Migration risk:** Low — behavior-preserving for documents above the floor; documents below the floor now consistently skip the digit-ratio prong via `garble_prongs` rather than via the caller's guard. Existing tests should pass without modification.

### D4: GATE_TABLE Reason-Ordering Fix (Zone 2)

**What:** When both `garbling` and `node_count_low` fire on the same document, ensure `garbling` wins as the surfaced reason.

**Why:** OCR escalation only triggers for `reason in ('garbling', 'node_garbling')`. When `node_count_low` masks `garbling`, recovery never fires for garbled minimal-tree documents — detection without remediation.

**How:**
- tree_validation.py — In the reason-selection logic after GATE_TABLE iteration, add a short-circuit: if `garbling` or `node_garbling` is in the fired-defects set AND the selected reason is something else, override with `garbling`. This matches GATE_TABLE's already-intended severity=0 priority for garbling.

**Lines changed:** ~5

**Migration risk:** Medium — more documents will correctly enter OCR retry, which may change their verdicts. Requires corpus diff.

### D5: Tessdata Latin Substitution Closure (Zone 2)

**What:** Extend `TessdataUnavailableError` to also fire when Latin traineddata is silently substituted for a request that included non-Latin languages.

**Why:** The current fix (commit cf904ff) correctly raises for missing non-Latin traineddata but does not catch the case where Latin `['deu','eng']` is silently substituted when the original request included Arabic. This substitution produces Latin mojibake that passes all garble prongs.

**How:**
- ocr_langs.py — In `ensure_tessdata`, after resolving available languages, check whether ALL originally-requested non-Latin languages were dropped and ONLY Latin languages remain. If so, raise `TessdataUnavailableError` instead of silently proceeding.

**Lines changed:** ~10

**Migration risk:** Medium — will cause bilingual Arabic/Latin documents to ERROR if Arabic tessdata is missing, matching the behavior for other non-Latin languages. This is the correct behavior — silent mojibake is worse than a loud failure. The MOU MOHRE regression (PASS→ERROR) in Run-20 was caused by the FIRST half of this fix; this completes it.

### D6: NFKC-Before-Bidi Reordering (Zone 2)

**What:** Move the bidi coherence presentation-forms check to run BEFORE NFKC normalization in `_pre_inference_normalize`.

**Why:** NFKC decomposes U+FB50–FEFF codepoints, destroying the signal the bidi check reads. The check currently runs on already-normalized text and has zero true-positive rate.

**How:**
- normalize.py:129–161 — Reorder `_pre_inference_normalize` so `had_presentation_forms` is computed BEFORE NFKC folding. NFKC continues to run afterward for downstream consumers.

**Lines changed:** ~10–15 (reorder, no new logic)

**Migration risk:** Low — this is a detection-only fix that makes a null detector functional. Documents with Arabic Presentation-Forms will now correctly trigger the bidi coherence gate. No extraction behavior changes.

## Sequencing

The deliverables have ordering constraints:

1. **D3** (garble dedup) — Zero-risk refactor, behavior-preserving. Land first to establish clean base.
2. **D6** (NFKC reorder) — Detection-only, low risk. Land second.
3. **D5** (tessdata closure) — Changes corpus verdicts. Land third with corpus diff.
4. **D4** (GATE_TABLE reason fix) — Changes OCR retry routing. Land fourth with corpus diff.
5. **D1** (unconditional hard-fail) — Changes verdict distribution. Land fifth with corpus diff.
6. **D2** (ordered pipeline) — Structural refactor. Land last, after D1 stabilizes verdict boundaries. Regenerate test fixtures in same PR.

D3+D6 can be combined in one PR (both low-risk). D4+D5 can be combined (both medium-risk, both need corpus diff). D1+D2 should be combined (D2 is the structural cleanup of D1's behavioral change).

## Effort Estimate

| Deliverable | Effort | Risk |
|---|---|---|
| D1 + D2 | 2–3 days | Medium — corpus diff + fixture regen |
| D3 | 0.5 day | Low |
| D4 | 0.5 day | Medium — corpus diff |
| D5 | 0.5 day | Medium — corpus diff |
| D6 | 0.5 day | Low |
| **Total** | **4–5 days** | |

## Test Strategy

- **D1/D2:** Unit tests for each promotion path in isolation + integration test confirming hard-fail fires unconditionally for `max_leaf_ratio > threshold` regardless of image enrichment. Corpus diff verifying no unexpected regressions beyond the known reclassifications.
- **D3:** Existing `test_garble_detection.py` + `test_garble.py` suites should pass unchanged. Add one test: document below `garble_digit_floor` where fallback previously skipped, now consistently handled by `garble_prongs`.
- **D4:** Add test: garbled minimal-tree document (both `garbling` and `node_count_low` fire) → verify reason=`garbling` → verify OCR recovery triggers.
- **D5:** Add test: mixed Arabic/Latin tessdata request where Arabic is unavailable → verify `TessdataUnavailableError` raised instead of silent Latin substitution.
- **D6:** Add test: text with Arabic Presentation-Forms → verify `had_presentation_forms=True` BEFORE NFKC normalization. Verify `_gate_bidi_degraded` now fires on documents with presentation-form codepoints.

## Corpus Impact Forecast

| Deliverable | Expected verdict changes |
|---|---|
| D1 | 0–2 PASS→FAIL for docs currently rescued by image-enrichment bypass with inadequate content volume |
| D3 | 0 — behavior-preserving |
| D4 | 1–3 docs enter OCR retry that previously did not; net effect depends on OCR success |
| D5 | 0–1 additional ERROR for docs relying on silent Latin tessdata substitution |
| D6 | 0–2 docs newly flagged by bidi coherence gate (detection only, no verdict change unless `BIDI_COHERENCE_ENFORCE=true`) |

## Open Questions

1. **D1 flat_prose/flat_mixed exception:** Should image-enrichment hard-fail exception be limited to `content_class in ("flat_prose", "flat_mixed")` (current behavior) or should ALL content classes with image_enrichment_ratio >= 0.8 qualify? Current scope preserves existing behavior.
2. **D5 bilingual recovery:** After closing the Latin substitution hole, bilingual Arabic/English documents with missing Arabic tessdata will ERROR. Should we add a distinct recovery path that attempts English-only extraction as a documented degradation rather than a silent substitution?
