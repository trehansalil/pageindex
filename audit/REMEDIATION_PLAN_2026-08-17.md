# Remediation Plan — 2026-08-17

**Audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-17_POST-FIX-4.md
**Zones:** 1 of 8 (top by priority)
**Waves:** 1

## Priority Scores

| Zone | Score | Severity | Bug Count | Proposal Status | Excluded | Notes |
|---|---:|---|---:|---|---|---|
| Zone 1: Garble Detection Hydra | 57.6 | critical | 12 | no_proposal | no | Highest bug count + critical severity. Git history on this branch (7b345c4, 33cc1f5) shows consolidation work already landed post-audit; a fresh delta would likely reclassify this partially_implemented or closed. |
| Zone 2: God Function Routing Cascade (client.py `index()`) | 52.8 | critical | 11 | no_proposal | no | Commit 646cdc0 ("decompose 1365-line index() into recovery pipeline + orchestrator") indicates this zone was already remediated post-audit. |
| Zone 5: OCR/Enrichment Signal Conflation | 32.4 | high | 9 | no_proposal | no | Commit f37584e ("split OCR_ESCALATION into garble/per-picture, add primary_text, unify enrichment path") matches this zone's proposed steps — appears already implemented post-audit. |
| Zone 4: Threshold Calibration Feedback Loops | 28.8 | high | 8 | no_proposal | no | Hysteresis mechanism makes verdicts path-dependent. No commit evidence of remediation yet — likely still open. |
| Zone 3: Verdict Persistence Split-Brain | 25.2 | high | 7 | no_proposal | no | Two independent offline verdict recomputers can disagree on the same document. No commit evidence of remediation yet. |
| Zone 6: Conversion Pipeline Stage Coupling (pdf_to_markdown_docling) | 25.2 | high | 7 | no_proposal | no | Tied with Zone 3; ordered after it per audit summary table. No evidence of remediation on this branch yet. |
| Zone 7: Registry/Persistence Consistency Gaps | 14.4 | medium | 6 | no_proposal | no | Non-atomic listing-then-delete pattern; medium severity keeps this below the critical/high zones. |
| Zone 8: Dead/Uncommitted/Stale Code Divergence | 14.4 | medium | 6 | no_proposal | no | Config/deployment hygiene class of defect; lowest score of the 8 zones. |

Scoring formula: severity_weight × bug_count × 1.2 (no_proposal multiplier, used since delta was undefined for this run). Only Zone 1 is in scope for this plan; Zones 2-8 are documented for prioritization context but have no fix spec here.

## Wave Sequence

### Wave 1 — Zone 1: Garble Detection Hydra
**Rationale:** Single zone to fix. Key files: `helpers.py`, `converters.py`, `client.py`. No other zones to coordinate with, so it runs alone in wave 1.

**Shared files:** none (no other zone in this plan).

## Fix Specs

### Zone: Zone 1: Garble Detection Hydra (wave 1, priority 1)

**Mechanism to eliminate:** Five parallel garble evaluation functions (`_tree_is_garbled`, `_flat_text_is_garbled`, `_is_garbled_blob` in `converters.py` ×3) diverge on heuristic coverage (`_has_sparse_mojibake` omitted at some sites), text normalization (`FLAT_MARKDOWN` context still uses `BlobKind.TREE_TEXT` instead of `RAW_MARKDOWN`, so markdown formatting chars dilute garble ratios), and `expected_script` threading (`None` fallback on the flat-doc path). Fixing a threshold in one function does not propagate to the others, generating a recurrent fix-then-regress cycle (12 bugs across 6 RFCs).

**Strategy:** Wave 3+4 completion of the existing consolidation: delete the two dead wrapper functions (`_tree_is_garbled`, `_flat_text_is_garbled`) that already delegate to `check_garble` but still exist as confusion vectors; fix `check_garble`'s `blob_kind` assignment so `FLAT_MARKDOWN` context uses `BlobKind.RAW_MARKDOWN` (stripping markdown formatting before ratio computation); relocate the orphaned `_GARBLE_SHORT_TEXT_DEFAULT` constant to sit near `check_garble`. Feature-flag the `RAW_MARKDOWN` behavioral change via `GARBLE_FLAT_MARKDOWN_NORMALIZE` env var (default `true`) for safe rollout.

**Code targets:**

| File | Lines (pre-change anchor — locate by content, not position; see ordering note below) | What | How | Constraint |
|---|---|---|---|---|
| `helpers.py` | 1581-1586 | Delete dead wrapper `_tree_is_garbled` | Remove the entire function definition (6 lines). It already delegates to `check_garble(TREE_BULK)` and has zero production callers (verified: no import in `src/`). Its sole caller was `TreeSignals.from_tree`, already migrated to call `check_garble` directly at line 384. | `TreeSignals.from_tree` (line 384) must continue calling `check_garble` directly, NOT `_tree_is_garbled`. Verify no production import exists. |
| `helpers.py` | 3335-3347 | Delete dead wrapper `_flat_text_is_garbled` | Remove the entire function definition (13 lines). It already delegates to `check_garble(FLAT_MARKDOWN)` and has zero production callers (verified: no import in `src/`). All `client.py` flat-path call sites (lines 446, 1002, 1743, 1769) already call `check_garble` directly. | All `client.py` garble calls must remain on `check_garble`, not revert to `_flat_text_is_garbled`. The `_GARBLE_SHORT_TEXT_DEFAULT` constant at line 3314 must NOT be deleted (used by `check_garble` at line 1400). |
| `helpers.py` | 1406-1414 | Fix `check_garble` `blob_kind`: `FLAT_MARKDOWN` must use `RAW_MARKDOWN`, not `TREE_TEXT` | Replace the `else` branch at line 1414 with context-aware dispatch: if `context == GarbleContext.FLAT_MARKDOWN`, set `blob_kind = BlobKind.RAW_MARKDOWN` (strips heading markers, pipes, HTML comments before ratio computation via `normalize_for_garble`); all other contexts keep `BlobKind.TREE_TEXT`. Guard behind env var `GARBLE_FLAT_MARKDOWN_NORMALIZE` (default `'true'`) so it can be disabled if corpus regressions appear. Fixes the ratio-dilution bug where markdown formatting inflated the denominator below heuristic floors. | `PAGE_TEXT_LAYER`, `DOCUMENT_FALLBACK`, `REGION`, `RETRY_COMPARISON`, `IMAGE_ENRICHMENT`, `NODE`, and `TREE_BULK` contexts must remain on `BlobKind.TREE_TEXT`. Only `FLAT_MARKDOWN` changes. Feature flag must default to `true` (fix on by default). |
| `helpers.py` | 3314 → relocate to ~1370 | Relocate `_GARBLE_SHORT_TEXT_DEFAULT` to sit near `check_garble` | Move `_GARBLE_SHORT_TEXT_DEFAULT = os.getenv("GARBLE_SHORT_TEXT_DEFAULT", "true").lower() == "true"` from line 3314 to immediately before `check_garble` (after the `GarbleContext` enum, around line 1370). Co-locates the constant with its sole consumer at line 1400. | Constant value and env var name must not change. Must remain accessible at line 1400 inside `check_garble`. |
| `helpers.py` | new, ~1370 | Add `GARBLE_FLAT_MARKDOWN_NORMALIZE` env-var feature-flag constant | Add: `_GARBLE_FLAT_MARKDOWN_NORMALIZE = os.getenv('GARBLE_FLAT_MARKDOWN_NORMALIZE', 'true').lower() == 'true'`. Use in the `blob_kind` dispatch inside `check_garble`: when `True` AND `context == FLAT_MARKDOWN`, use `RAW_MARKDOWN`; otherwise `TREE_TEXT`. | Must default to `true`. Must be the sole control point for this behavioral change. |

**Ordering note (line-shift hazard):** these edits interact — deleting 1581-1586 shifts everything below by −6, deleting 3335-3347 shifts everything below it by −13, and inserting the new constant/flag near 1370 shifts the 1406-1414 dispatch and everything below it. Line numbers above are pre-change anchors only. Locate each target by content (function signature / constant name), not by absolute line number, and apply edits bottom-up: (1) delete `_flat_text_is_garbled` at 3335 first, (2) move `_GARBLE_SHORT_TEXT_DEFAULT` from 3314 next, (3) delete `_tree_is_garbled` at 1581, (4) then make the 1406/1370 dispatch edits.

**Additional (documentation) targets required by validation, not in the original spec:**
- Update `check_garble`'s own docstring (~1381-1397), which currently names the wrapper functions and states `FLAT_MARKDOWN` uses default `TREE_TEXT` blob_kind — both false after this change.
- Update stale docstring references to `_tree_is_garbled` at `helpers.py:1830` and `:1970` (post-deletion, these become dangling references).
- Register `GARBLE_FLAT_MARKDOWN_NORMALIZE` in `ARCHITECTURE.md`'s env-var catalog (Data Model & Storage Layout section).

**Expected-script threading — open item, not closed by this wave:** the `mechanism_to_eliminate` names `expected_script` `None`-fallback on the flat-doc path as a third divergence, but no code target here addresses it. Current `client.py` sites do pass `expected_script` (line 446) and `converters.py` sites use `infer_script` (lines 1652, 1751), suggesting this may already be resolved by a prior wave — but this plan does not verify or close it. Before marking Zone 1 done, either (a) add a wiring test asserting every `check_garble` call site threads a non-`None` `expected_script` (AST check forbidding `expected_script=None` literals at call sites), or (b) confirm in writing that this divergence was closed in an earlier wave and is out of scope here.

**Wiring checks:**

| Symbol | Must be imported/referenced by | Check type | Note |
|---|---|---|---|
| `_GARBLE_FLAT_MARKDOWN_NORMALIZE` | `src/pageindex_mcp/helpers.py` | dispatch | **Correction required:** as written this is vacuous — the constant is *defined* in `helpers.py`, so an import check proves nothing. Respecify as an AST/source-inspection check that `check_garble`'s body actually references `_GARBLE_FLAT_MARKDOWN_NORMALIZE`. |
| `BlobKind.RAW_MARKDOWN` | `src/pageindex_mcp/helpers.py` | dispatch | **Correction required:** `BlobKind` is already imported in `helpers.py` (line 39) regardless of whether `RAW_MARKDOWN` is ever referenced — an import check does not prove the new dispatch fires. Respecify as an AST/source-inspection check that `check_garble`'s body references `BlobKind.RAW_MARKDOWN`, plus a behavioral test (see test requirements) that `FLAT_MARKDOWN` with the flag on produces a different verdict than `TREE_TEXT` normalization on a crafted input. |
| `check_garble` | `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/client.py` | call | Valid as written — genuine cross-module call-site check. |
| `GarbleContext` | `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/client.py` | import | Valid as written — genuine cross-module import check. |

**Test requirements:**
- `tests/test_zone1_garble_consolidation.py` — regression: `check_garble` with `FLAT_MARKDOWN` context uses `RAW_MARKDOWN` normalization. Construct markdown text with pipes/headers that would dilute garble ratios below threshold under `TREE_TEXT` normalization but correctly fire garble detection after markdown stripping. Verifies the ratio-dilution bug is fixed.
- `tests/test_zone1_garble_consolidation.py` — contract: `check_garble` with non-`FLAT_MARKDOWN` contexts (`TREE_BULK`, `NODE`, `PAGE_TEXT_LAYER`, `DOCUMENT_FALLBACK`, `REGION`, `RETRY_COMPARISON`, `IMAGE_ENRICHMENT`) all use `TREE_TEXT` blob_kind and produce identical results to pre-change behavior.
- `tests/test_zone1_garble_consolidation.py` — contract: `GARBLE_FLAT_MARKDOWN_NORMALIZE=false` disables `RAW_MARKDOWN` normalization for `FLAT_MARKDOWN` context, falling back to `TREE_TEXT` (feature-flag kill switch).
- `tests/test_zone1_garble_consolidation.py` — exhaustiveness: `_tree_is_garbled` and `_flat_text_is_garbled` are no longer importable from the `helpers` module (deleted). Verify `ImportError`/`AttributeError`.
- `tests/test_zone1_garble_consolidation.py` — exhaustiveness: every `GarbleContext` enum member has a corresponding test case exercising `check_garble` with that context, confirming correct `blob_kind` selection and that both `_is_garbled_blob` and `_has_sparse_mojibake` are evaluated.
- `tests/test_zone1_garble_wiring.py` — wiring: update existing wiring tests to remove references to the deleted `_tree_is_garbled`/`_flat_text_is_garbled`, and assert all production call sites route through `check_garble` — see call-site enumeration correction below.

**Call-site enumeration correction (required before test authoring):** the original spec's wiring-test description says "all 7 production call sites" then lists 9 named items, and the existing `tests/test_zone1_garble_wiring.py` docstring also says 7. An actual grep-based recount is needed before writing/updating this test: candidates include `TreeSignals.from_tree`, `_garble_ratio`, `_garble_check_nodes`, `_text_layer_has_content`, `_document_level_text_fallback`, the region check in `_recover_picture_text`, and `client.py` sites at ~446, ~1002, the retry-comparison cluster (~1350/1355/1362), and the flat-gate cluster (~1743/1769). Do not hardcode a count in the test docstring — enumerate the list explicitly and keep it in sync with the actual call sites.

**Migration required for existing tests (blocker from validation — must be resolved before this wave lands):** deleting `_tree_is_garbled` and `_flat_text_is_garbled` breaks tests across at least 16 files that import or call them directly: `test_zone1_check_garble.py` (imports both at module level, including delegation-equivalence tests `TestMatchesTreeIsGarbled`/`TestMatchesFlatTextIsGarbled` whose oracle disappears once the wrappers are gone), `test_rfc025_d2.py` (tests `_flat_text_is_garbled` directly), `test_zone1_dead_gate.py` (imports `_tree_is_garbled` at runtime), and `test_rfc021_qf3`, `test_rfc021_qf2_qf4`, `test_rfc023_d7`, `test_vlm_fallback`, `test_rfc022_b1`, `test_rfc010_helpers`, `test_rfc013`, `test_rfc015`, `test_rfc020`, `test_rfc027`, `test_rfc029`, `test_rfc036`. Before deleting the wrappers, go through this list file-by-file and either (a) port the assertion to call `check_garble` with the equivalent context, or (b) delete the test as superseded by the new `test_zone1_garble_consolidation.py` suite. Do not delete the wrappers until this migration is complete — landing the deletion first would ship a red test suite.

**Corpus validation:**
- Affected documents (6, spot-check all):
  - وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات حكومة أبوظبي حول برنامج مهارات المهن الحرفية - Copy.pdf *(filename corrected from audit source: المهن الحرفية with ف, not المهن الحرجية with ج — verify against the actual `doc_store/` filename before spot-checking, an exact-string match will otherwise fail to locate the file)*
  - ﺣﻘﻮق اﻹﻧﺴﺎن - Copy.pdf
  - MOU MOHRE & Nafis & وزارة الصناعة والتكنولوجيا المتقدمة (1).pdf
  - قرار مجلس الوزراء رقم (106) لسنة 2022 بشأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (9) لسنة 2022 بشأن عمال الخدمة المساعدة.pdf
  - Haftpflicht-Allgemeine-Bedingungen.pdf.pdf
  - اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf
- Expected verdict direction: improve

**Estimated complexity:** medium
**Severity:** critical

## Validation Results

**Overall quality: needs_work — NOT APPROVED as written.** `validation.approved = false`. The following issues were found in review of the source plan and must be resolved before implementation begins; several are already folded into the fix spec above (marked "correction required" / "required before"), but they originate from validation and are restated here for traceability.

### Blocker
1. **Missing test-migration plan for 16 dependent test files.** Deleting `_tree_is_garbled` and `_flat_text_is_garbled` breaks tests in `test_zone1_check_garble.py` (module-level imports of both, plus delegation-equivalence tests whose oracle disappears), `test_rfc025_d2.py` (tests `_flat_text_is_garbled` directly), `test_zone1_dead_gate.py` (imports `_tree_is_garbled` at runtime), and `test_rfc021_qf3`, `test_rfc021_qf2_qf4`, `test_rfc023_d7`, `test_vlm_fallback`, `test_rfc022_b1`, `test_rfc010_helpers`, `test_rfc013`, `test_rfc015`, `test_rfc020`, `test_rfc027`, `test_rfc029`, `test_rfc036`. The original spec only planned updates to `tests/test_zone1_garble_wiring.py`. **Fix (folded into spec above):** enumerate all 16 files, port or delete each affected test explicitly before the wrapper deletion lands.

### Major
2. **`expected_script` threading divergence not closed.** `mechanism_to_eliminate` names three divergences; the third (`expected_script` `None` fallback on the flat-doc path) has no corresponding code target, test requirement, or explicit statement that it was already fixed in a prior wave. Current code suggests it may be resolved already, but this is unverified. **Fix (folded into spec above):** add a wiring test forbidding `expected_script=None` literals at call sites, or explicitly confirm and document closure in an earlier wave.

### Minor
3. Wiring checks for `_GARBLE_FLAT_MARKDOWN_NORMALIZE` and `BlobKind.RAW_MARKDOWN` as written (`must_be_imported_by helpers.py`) are vacuous — both symbols are defined/already imported in that same file, so the check proves nothing about whether the new dispatch actually fires. **Fix (folded into spec above):** respecify both as `dispatch`-type AST/source checks that `check_garble`'s body references them, plus a behavioral test.
4. Call-site count mismatch: the wiring test description says "7 production call sites" but enumerates 9 items, and the existing `test_zone1_garble_wiring.py` docstring also says 7; an actual grep found a different set/count entirely (4 unique `client.py` sites, 3 `converters.py` sites, plus `TreeSignals.from_tree` and `_garble_ratio` in `helpers.py`). **Fix (folded into spec above):** drop the hardcoded count, use an explicit enumerated list kept in sync via recount.
5. Corpus validation filename mismatch: the spec lists "برنامج مهارات المهن الحرجية" but the actual `doc_store/` file uses "المهن الحرفية" (ف, not ج). An automated spot-check matching the spec's string verbatim would fail to find the document. **Fix (folded into spec above):** corrected filename included in Corpus validation section.
6. Code-target line numbers interact (deleting 1581-1586 and 3335-3347, and inserting new constants near 1370, all shift subsequent line numbers). A fixer applying targets in listed order by absolute line number would edit the wrong lines. **Fix (folded into spec above):** ordering note added — locate by content, apply bottom-up.
7. Stale documentation not covered by the original spec: `check_garble`'s docstring (~1381-1397) names the wrapper functions and states `FLAT_MARKDOWN` uses default `TREE_TEXT` blob_kind — both become false after this change; docstrings at `helpers.py:1830` and `:1970` also reference `_tree_is_garbled` post-deletion. The new `GARBLE_FLAT_MARKDOWN_NORMALIZE` env var is also not registered in `ARCHITECTURE.md`'s env-var catalog, the project's documented home for env vars (per CLAUDE.md's Document Map). **Fix (folded into spec above):** added as explicit additional targets.

**Recommended path:** resolve the blocker (test migration) and the major issue (`expected_script` threading) before implementation starts on Zone 1. The minor issues are folded directly into the fix spec above and should be applied as part of the same wave rather than deferred.
