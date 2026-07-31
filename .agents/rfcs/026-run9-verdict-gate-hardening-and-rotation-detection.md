<!-- Space: CITRA -->
<!-- Title: RFC-026: Run 9 — Verdict Gate Hardening & Page Rotation Detection -->
<!-- Folder: RFCs -->

# RFC-026: Run 9 — Verdict Gate Hardening & Page Rotation Detection

**Date:** 2026-07-31
**Run:** 9
**Baseline:** 15 PASS / 8 MARGINAL / 0 FAIL / 1 ERROR (25 docs)
**Prior (Run 8):** 7 PASS / 6 MARGINAL / 9 FAIL / 3 ERROR

---

## Context

Run 9 shows strong numerical improvement (PASS doubled, FAIL dropped to zero), but the audit found that several PASS/MARGINAL verdicts are unreliable — zero-content docs pass the gate via promotion flags and missing FAIL floors. The portrait/landscape rotation issue persists for a third straight run. The scoring harness itself had a process bug (Stage 2 guard false-positive) that the D4 re-verification caught, masking the real tally.

## Fix Dimensions

### D0 — Hard FAIL Floor for Zero-Content Documents
**Root cause:** `classify_verdict()` in `helpers.py` has no FAIL return for `node_count=0` or `total_chars=0`. The only FAIL paths are garbling, reorder, and max_leaf_ratio > 0.75. A doc with 0 nodes/0 chars falls through all checks to the final `return ("MARGINAL", ...)`.
**Affected:** MOU MOHRE (0 chars), اتفاقية SLA (0 chars), قرار 1/2022 (0 chars), قرار 106/2022 (0 chars)
**Fix:** Add an early-exit at the top of `classify_verdict()`: if `node_count == 0` or `total_chars == 0`, return `("FAIL", "zero_content")`. This must come before the `image_enrichment_promoted` check.
**Severity:** Critical — violates Hard Rule 5.

### D1 — Image-Enrichment-Promoted Volume Floor
**Root cause:** The `image_enrichment_promoted` branch (helpers.py lines 1198-1203) returns PASS when `image_enrichment_ratio >= 0.8` but performs NO check on absolute character count. Docs with 38, 123, 492 chars get PASS.
**Affected:** مرسوم (13) 2022 (38 chars PASS), القرار التنظيمي (123 chars PASS), Unfallversicherung (492 chars PASS)
**Fix:** Add a minimum character threshold to the `image_enrichment_promoted` path. If total chars < 500 (configurable), the promotion should cap at MARGINAL, not PASS. This is NOT about disabling promotion — it's about gating it behind a minimum content floor.
**Severity:** Critical — PASS on near-empty docs contravenes Hard Rule 5.

### D2 — Page-Level Rotation Detection
**Root cause:** The portrait and landscape variants of `uae_numbers_english_page_16_17` both yield ~750 chars across 76 flat nodes for a 2-page document (expected ~4000-8000 chars). The converter has no awareness of page rotation metadata — landscape pages get extracted with wrong coordinate mapping, fragmenting text into near-empty nodes. This has stalled across 3 consecutive runs.
**Affected:** uae_numbers_english_page_16_17_landscape (748 chars), uae_numbers_english_page_16_17_portrait (764 chars)
**Fix:** Add page-level rotation detection in the converter pipeline:
1. Read PDF page `/Rotate` key (0/90/180/270) from each page's metadata
2. Detect aspect-ratio heuristic (width > height → likely landscape even without /Rotate)
3. Pass rotation info to the docling service and/or text extraction so coordinate mapping handles rotated pages correctly
4. If rotation detected, apply coordinate transform before text extraction (or signal docling to do so)
The fix should work at page level, not document level — a single PDF can mix portrait and landscape pages.
**Severity:** High — persistent stall across 3 runs.

### D3 — Hysteresis Preservation Across Reingestion Wipe
**Root cause:** `find_prior_verdict()` in `storage.py` scans `processed/*.meta.json` in MinIO. The corpus reingestion pipeline wipes all `processed/` objects before reingesting, so hysteresis always sees "no prior verdict" and threshold flaps occur.
**Affected:** GHV-TKV-Tarif.pdf (PASS→MARGINAL on identical tree due to `leaf_concentration=0.39`)
**Fix:** Before wipe, snapshot prior verdicts to a sidecar file (e.g., `processed/_prior_verdicts.json`) that `find_prior_verdict()` falls back to when no individual meta.json exists. The snapshot is written pre-wipe and read post-reingestion.
**Severity:** Medium — causes false regressions in audit.

### D4 — Scoring Harness Stage 2 Guard Fix
**Root cause:** `corpus-ingest-score.js` Stage 2 guard (line 183) checks `typeof ingestResult === 'string' && ingestResult.includes('error')`. Stage 1 uses a Haiku agent without a schema, returning a plain string. If the string contains the substring "error" anywhere (e.g., "error handling succeeded"), ALL docs short-circuit to verdict=ERROR.
**Affected:** All docs in the pipeline (process-level bug, not corpus-quality bug).
**Fix:** Add a `schema` to the Stage 1 agent call so it returns a structured object, then check `ingestResult.status === 'error'` instead of substring match.
**Severity:** High — undermines audit reliability.

### D5 — Validate-Tree Garble Check Ordering
**Root cause:** `validate_tree()` exits early on `node_count<3` / `depth<2` (helpers.py lines 1053-1056) BEFORE the garble check (line 1057). Numeric-junk text that produces a minimal tree gets reason `node_count<3` instead of `garbling`, so the flat-doc fallback doesn't know to escalate OCR.
**Affected:** وارد 597 (garbled text reaches flat routing without garble flag)
**Fix:** Move the garble check before the node_count/depth early-exit in `validate_tree()`. If garble is detected, return `("FAIL", "garbling")` regardless of node count.
**Severity:** Medium — garbled docs silently pass to flat routing.

### D6 — CMap Latin-Gibberish Detection for Latin-Script Docs
**Root cause:** `_is_garbled_blob()` with `expected_script='Latn'` cannot detect CMap font-encoding garbling (character substitutions producing valid Latin characters). German docs with broken font encodings get clean-looking but semantically wrong text.
**Affected:** Haftpflicht-Allgemeine-Bedingungen.pdf.pdf (61% garbled nodes in Run 8, now PASS in Run 9 — unclear if fixed or undetected)
**Fix:** Add a dictionary-based sanity check for Latin-script garble detection: sample N blocks, tokenize on whitespace, check what fraction of tokens appear in a basic German+English word list. If <30% are real words, flag as garbled. Use a compact word list (~50k entries) loaded once.
**Severity:** Medium — affects German insurance T&C docs specifically. Deferred to next run if scope is tight.

## Out of Scope

- **Image-OCR-never-fires for scanned Arabic PDFs** (picture_ocr RC1): Known deep defect documented since Run 6. The fix requires rearchitecting the image enrichment pipeline (separate from picture-crop OCR). Tracked separately.
- **world-stats-pocketbook-2023.pdf timeout**: 6.1MB file exceeds the 30-min job timeout. Needs either chunked processing or increased timeout. Not a code-quality issue.
- **Depth-drop tree-to-flat routing switch** (Federal Decree-Law 47): Depth-2→depth-1 across runs is a converter non-determinism issue, not a verdict-logic bug. The doc still PASS in both cases.

## Priority Order

1. **D0** (zero-content FAIL floor) — 4 docs, trivial fix, highest impact
2. **D1** (image-enrichment volume floor) — 3 docs, small fix
3. **D5** (garble check ordering) — 1 doc, small fix, prerequisite for D6
4. **D2** (page rotation detection) — 2 docs, medium complexity, user-priority
5. **D3** (hysteresis preservation) — 1 doc, medium complexity
6. **D4** (scoring harness fix) — process fix, not corpus code
7. **D6** (Latin garble detection) — deferred if scope tight

## Estimated Effort

- D0: ~15 lines, 30 min
- D1: ~10 lines, 30 min
- D2: ~80 lines, 2-3 hours (rotation detection + converter integration)
- D3: ~40 lines, 1 hour (snapshot + fallback read)
- D4: ~20 lines in workflow JS, 30 min
- D5: ~10 lines (reorder in validate_tree), 20 min
- D6: ~60 lines + word list, 1.5 hours (deferred)
