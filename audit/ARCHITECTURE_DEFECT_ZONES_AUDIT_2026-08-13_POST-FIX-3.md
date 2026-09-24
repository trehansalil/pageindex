# Architecture Defect Zones Audit — 2026-08-13 POST-FIX-3

**Date:** 2026-08-13
**Sources:** 8 history miners, 3 code maps

## Summary Table

| # | Zone                                                                                       | Severity | Bug Count | Key Files                                                                                                                            |
| - | ------------------------------------------------------------------------------------------ | -------- | --------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| 1 | Zone 1: validate_tree gate-ordering / OCR-escalation routing                               | critical | 10        | `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/client.py`                                                                    |
| 2 | Zone 2: classify_verdict threshold/bypass/hysteresis feedback loop                         | critical | 9         | `src/pageindex_mcp/helpers.py`                                                                                                     |
| 3 | Zone 3: NFKC/garble/bidi normalization ordering and detection blindness                    | critical | 8         | `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/script.py`                               |
| 4 | Zone 4: client.index() god-function with mutable state and 5 validate_tree re-entry points | high     | 7         | `src/pageindex_mcp/client.py`                                                                                                      |
| 5 | Zone 5: Triple-write verdict persistence and read-merge-write races                        | high     | 5         | `src/pageindex_mcp/storage.py`, `src/pageindex_mcp/client.py`                                                                    |
| 6 | Zone 6: Picture/chart recovery pipeline with ordering-dependent stage coupling             | high     | 7         | `src/pageindex_mcp/converters.py`                                                                                                  |
| 7 | Zone 7: Heading depth recovery chain with cascading overwrites                             | medium   | 6         | `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/helpers.py`                                                                |
| 8 | Zone 8: Wiring-gap pattern (implemented-but-unwired / marked-complete-but-absent)          | high     | 8         | `src/pageindex_mcp/client.py`, `src/pageindex_mcp/worker.py`, `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/storage.py` |

## Zone Details

### Zone 1: validate_tree gate-ordering / OCR-escalation routing

**Severity:** critical | **Bug count:** 10

#### Mechanism

Adding a new gate or reordering existing gates changes which defect becomes 'primary', which changes the REASON_POLICY lookup, which changes whether OCR escalation fires, which changes whether the document recovers or errors. Every new gate implicitly interacts with every other gate via this priority chain. Fixes to one gate (e.g., making garble detection stricter) can cause a different gate to fire first for the same document, rerouting it away from the intended recovery path.

#### History

a. RFC-025/026 D5: validate_tree early-exit ordering ran node_count<3 before garble check, preventing OCR recovery (obs#5333).
b. RFC-029 D0/D1/D2: 4 new failure reasons never wired into client.py recovery -- 3 PASS-to-ERROR regressions (RFC-030 D2).
c. RFC-029 D0: _check_bidi_coherence implemented but never called, dead code found by RFC-030 D5.
d. RFC-027 D2: LOW_CONTENT_OCR_CHAR_FLOOR workaround added to client.py:1271-1280 specifically because NODE_COUNT_LOW shadows GARBLING for near-empty garbled docs.
e. Run 13: low_content_density gate blocked 3 large legal docs (606/502/230+ nodes).
f. Run 8: wholesale revert of 11 RFC-023 improvements from branch merge.
g. BIDI_DEGRADED gate acknowledged as unreachable in code comments (helpers.py:1589-1590).

#### Code Evidence

helpers.py:1676-1696 GATE_TABLE defines priority order (garbling, node_count_low, depth_low, node_garbling...). helpers.py:230-264 decide_route maps TreeDefect to Route via REASON_POLICY. client.py:1286-1294 OCR escalation only fires for GARBLING/NODE_GARBLING/low_content_ocr_eligible. client.py:1271-1280 low_content_ocr_eligible workaround for NODE_COUNT_LOW shadowing GARBLING. helpers.py:1575-1598 _gate_bidi_degraded returns (False, '') -- permanent placeholder. helpers.py:185-198 REASON_POLICY dict: NODE_COUNT_LOW maps to RAISE, GARBLING maps to RETRY_OCR.

#### Key Files

- src/pageindex_mcp/helpers.py
- src/pageindex_mcp/client.py

#### Simplification Proposal

Now I have a complete picture. Here is the analysis:

---

**(1) Core simplification (2-3 sentences)**

Replace the "primary defect determines recovery" model with a "recovery action computed from all_defects" model. Instead of REASON_POLICY mapping a single TreeDefect to a single policy, add a function `decide_recovery(all_defects: frozenset[TreeDefect]) -> RecoveryAction` that scans the full defect set for any OCR-recoverable defect (GARBLING, NODE_GARBLING, or content-floor breach) regardless of which defect happened to fire first. Delete the dead `_gate_bidi_degraded` placeholder and the `low_content_ocr_eligible` workaround in client.py, since both exist solely to compensate for priority-ordering artifacts.

**(2) Concrete restructuring steps**

Step A -- helpers.py: Add `RecoveryAction` enum (NONE, RETRY_OCR, RETRY_RTL, PERSIST_FAIL, CAP_MARGINAL, FLAT) and a function `decide_recovery(all_defects: frozenset[TreeDefect], total_chars: int, flat_routing_enabled: bool) -> RecoveryAction` (~30 lines). This function checks: if any defect in all_defects is in {GARBLING, NODE_GARBLING} => RETRY_OCR; if any is RTL_REVERSAL => RETRY_RTL; if any is PERSIST_FAIL-class => PERSIST_FAIL; if remaining structural-only => FLAT when enabled; else NONE. The key change: content-integrity defects win over structural defects for recovery selection regardless of table position. Keep REASON_POLICY and decide_route for backward compat in verdict/metadata paths but mark as not used for recovery routing. (+30 lines)

Step B -- helpers.py: Delete `_gate_bidi_degraded` (lines 1575-1598, -24 lines). Remove its entry from GATE_TABLE. Remove BIDI_DEGRADED from TreeDefect only if no persisted verdict_reason strings reference it; otherwise keep the enum value but drop the gate. Net: -24 to -30 lines.

Step C -- helpers.py: Add a `recovery_action` field to TreeGateResult (or compute it lazily from all_defects). This makes the recovery decision travel with the gate result instead of being re-derived in client.py. (+5 lines)

Step D -- client.py (~lines 1262-1294): Replace the three-part routing logic (first_defect lookup via decide_route + low_content_ocr_eligible workaround + hardcoded defect-set check for OCR escalation) with a single call: `recovery = gate_result.recovery_action` or `recovery = decide_recovery(gate_result.all_defects, total_chars, settings.flat_doc_routing)`. The OCR escalation branch condition becomes `recovery == RecoveryAction.RETRY_OCR`. Delete the `low_content_ocr_eligible` variable and its 10-line computation block (lines 1271-1280). Net: -15 lines.

Step E -- client.py: Wire the four RFC-029 defects (EMPTY_NODE_CONTAMINATION, LOW_CONTENT_DENSITY, SUSPECT_DENSITY, ARABIC_LOW_CONTENT_RATIO) through `decide_recovery` so they get explicit recovery paths instead of falling through to raise. Currently their PERSIST_FAIL policy maps to terminal error, which is correct for some but was never validated per-defect. The new function makes each defect's recovery explicit and auditable in one place. (0 net lines, just ensures coverage.)

Rough line-count delta: +30 (decide_recovery) -24 (dead bidi gate) -15 (low_content_ocr_eligible removal and client.py simplification) +5 (TreeGateResult field) = **-4 net lines**, with the real value being the elimination of the priority-coupling mechanism.

**(3) Historical bug classes this would have prevented**

- RFC-025/026 D5 (node_count<3 firing before garble, blocking OCR recovery): decide_recovery scanning all_defects would have found GARBLING in the set and returned RETRY_OCR regardless of NODE_COUNT_LOW being "primary."
- RFC-029 D0/D1/D2 (4 new failure reasons never wired into client.py recovery, causing 3 PASS-to-ERROR regressions per RFC-030 D2): new defects would need an entry in decide_recovery, which is a single function with an exhaustiveness assert -- the omission would be caught at the enum level or by the assert, not silently fall through.
- RFC-027 D2 (LOW_CONTENT_OCR_CHAR_FLOOR workaround): the workaround exists because NODE_COUNT_LOW shadows GARBLING. With all_defects-based recovery, the workaround is unnecessary.
- RFC-030 D5 (_check_bidi_coherence dead code): the dead BIDI_DEGRADED gate that motivated that finding would not exist.
- Run-13 regression (low_content_density blocking large legal docs): the recovery function makes the action for each defect explicit and testable in isolation, rather than depending on which other gate fires first.
- The general class of "adding gate N changes routing for documents that also trigger gate M" is eliminated because recovery is derived from the union, not a single winner.

**(4) Migration risk and sequencing**

Risk is moderate-low because the change is in the routing/recovery path, not in the gate evaluation itself. All gates continue to fire exhaustively and all_defects continues to be collected -- only the consumer changes.

Incremental sequence:

1. (Safe, no behavior change) Add `decide_recovery` function in helpers.py with full test coverage. Assert it produces identical results to the current primary-defect-based routing for every document in the corpus. Ship this as a shadow-mode function that logs but does not act.
2. (Safe, deletion) Remove `_gate_bidi_degraded` and its GATE_TABLE entry. This is dead code removal -- it always returns (False, ""). Zero behavior change.
3. (Behavior change, gated) Add a feature flag `USE_ALL_DEFECTS_RECOVERY=false`. When true, client.py uses `decide_recovery(all_defects)` instead of `decide_route(first_defect)` + workarounds. Run the full 25-doc corpus with the flag on and diff verdicts. The expected change: documents where NODE_COUNT_LOW shadowed GARBLING now get OCR retry.
4. (Cleanup) Once the flag is validated, delete `low_content_ocr_eligible`, the old `first_defect` routing in client.py, and the feature flag. Keep `decide_route` and `REASON_POLICY` for metadata/verdict paths that still reference the primary defect.

The biggest risk is that some document currently benefits from NODE_COUNT_LOW routing to FLAT instead of RETRY_OCR (i.e., OCR retry would produce a worse result). The shadow-mode step (1) and corpus diff (3) catch this before any behavior change ships.

**(5) Estimated effort**

2-3 days. Day 1: implement decide_recovery + shadow logging + unit tests. Day 2: delete BIDI_DEGRADED gate, add feature flag, wire client.py, run corpus diff. Day 3: validate corpus results, remove flag and workarounds, final cleanup. The core code change is roughly 50 lines of new logic and 40 lines of deletion. The bulk of the effort is corpus validation.

Key files: `/Users/saliltrehan/Documents/Python_n_R/Personal/pageindex/src/pageindex_mcp/helpers.py` (lines 70-265, 1490-1745), `/Users/saliltrehan/Documents/Python_n_R/Personal/pageindex/src/pageindex_mcp/client.py` (lines 1240-1380).

---

### Zone 2: classify_verdict threshold/bypass/hysteresis feedback loop

**Severity:** critical | **Bug count:** 9

#### Mechanism

Threshold softening (to fix false FAILs) opens bypass paths that let near-zero-content trees persist as PASS, violating Hard Rule 5. Subsequent threshold hardening (to close those bypasses) re-surfaces the same pre-existing defects the softening had masked, generating a new batch of regressions on byte-identical content. The hysteresis band means the same document gets a different threshold depending on its prior verdict, so reingestion cycles oscillate. Judge-vs-gate divergence arises because the persisted gate verdict and the downstream audit judge apply different thresholds to the same tree.

#### History

a. RFC-023 D10: widened PASS_MAX_LEAF_RATIO 0.17->0.20, caused unprojected regression on Doc 8 (Reitlehrer, leaf_concentration=0.26) in Run 7.
b. RFC-025/026: threshold hardening surfaced 12 pre-existing defects in Run 10 (7P/8M/10F/0E from 15P/8M/0F/1E).
c. RFC-025/026: image_enrichment_promoted bypass let docs with 38/123/492 chars persist as PASS (Hard Rule 5 violation, Run 9).
d. RFC-025 D0: GHV-TKV-Tarif.pdf flipped PASS->MARGINAL on identical 5-node tree from hysteresis threshold drift (obs#5327).
e. Run 10: Haftpflicht-Allgemeine stored PASS gate verdict while audit judge downgraded to MARGINAL.
f. Runs 12-15: image pie chart oscillated MARGINAL->FAIL->MARGINAL on unchanged 489-char content.
g. RFC-029 D6 Phase B: judge calibration rules marked complete but never written to skill file (RFC-030 D6).

#### Code Evidence

helpers.py:1944-2127 classify_verdict function with noqa:C901 marker. helpers.py:2042-2052 image-enrichment rescue before max_leaf_ratio hard-fail (comment: 'locked by RFC-022 B2'). helpers.py:2061-2063 hysteresis band widens threshold when prior_verdict=='PASS'. helpers.py:306 _verdict_thresholds_cache module-level global. helpers.py:2603 _blank_line_fallback_enabled comment about Zone-2 feedback loop. helpers.py:291 PASS_MAX_LEAF_RATIO default 0.30. helpers.py:292 PASS_HYSTERESIS_BAND default 0.10.

#### Key Files

- src/pageindex_mcp/helpers.py

#### Simplification Proposal

Now I have a clear picture of the full zone. Let me analyze and produce the proposal.

---

**(1) Core simplification (2-3 sentences)**

Replace the single 185-line `classify_verdict` function with a two-phase pipeline: a deterministic **gate phase** (hard-fail checks, zero-content, structural bounds) that returns FAIL and cannot be overridden, followed by a stateless **scoring phase** (promotion rules evaluated in priority order) that never reads `prior_verdict`. Delete the hysteresis band entirely -- same content must always produce the same verdict regardless of history. The image-enrichment rescue remains but moves into the scoring phase with an explicit minimum-chars floor (already present at line 2049), making it a promotion path rather than a hard-fail bypass.

**(2) Concrete restructuring steps**

Step A -- Delete hysteresis (helpers.py, ~-8 lines net).
Remove `prior_verdict` parameter from `classify_verdict` signature. Delete lines 2061-2063 (the `_effective_max_leaf` widening). Remove `hysteresis_band` from `VerdictThresholds` dataclass and `from_env`. Remove `PASS_HYSTERESIS_BAND` env var read (line 292). Update all callers passing `prior_verdict` (grep shows ~3 call sites in worker.py/client.py). Delta: -8 lines helpers.py, -1 line per caller (~-3 elsewhere).

Step B -- Extract gate phase into `_hard_gate()` (helpers.py, ~+15 / -20 net).
Pull lines 2000-2058 (zero-content, HARD_FAIL_DEFECTS, image_standalone dispatch, max_leaf_ratio hard-fail) into a standalone `_hard_gate(sig, defect, content_class, ...) -> tuple[str, str] | None` function that returns a (verdict, reason) tuple on terminal conditions or None to continue. This makes the "cannot be overridden" boundary explicit in the function signature rather than implicit in ordering within a C901 blob.

Step C -- Flatten promotion rules into a scored list (helpers.py, ~+10 / -15 net).
Replace the if/elif/else chain (lines 2060-2116) with a list of `(predicate, reason)` tuples evaluated in order, first-match wins. Each predicate is a pure function of `(sig, content_class, th, image_enrichment_ratio)` -- no mutable state, no prior_verdict. The `_pass()` cap wrapper stays as-is (bidi_degraded + depth_adequacy are uniform post-filters, not decision inputs).

Step D -- Make VerdictThresholds injectable, not cached (helpers.py, ~+5 / -8 net).
Change `classify_verdict` to accept an optional `thresholds: VerdictThresholds | None` parameter (defaulting to `_get_verdict_thresholds()`). Remove module-level `_verdict_thresholds_cache` global. Tests pass thresholds directly; production reads from env once at startup via the FastMCP lifespan. Delta: -8 lines (cache + reset function), +5 lines (parameter plumbing).

Net line delta across all steps: approximately -25 lines in helpers.py, -3 lines in callers. The `noqa:C901` marker is removed because the extracted functions each fall well under the complexity threshold.

Target files: `src/pageindex_mcp/helpers.py` (primary), `src/pageindex_mcp/worker.py` and `src/pageindex_mcp/client.py` (caller updates), test files touching `classify_verdict` or `reset_verdict_thresholds`.

**(3) Historical bug classes this would have prevented**

- RFC-025 D0 hysteresis drift (GHV-TKV-Tarif.pdf PASS->MARGINAL on identical tree): eliminated entirely -- no hysteresis means same input always produces same output.
- Run 12-15 image pie chart oscillation (MARGINAL->FAIL->MARGINAL on unchanged 489-char content): the char-floor check (already at line 2049, `min_image_promoted_chars=500`) would consistently MARGINAL this document every run without the hysteresis band shifting the effective threshold.
- Judge-vs-gate verdict divergence (Run 10 Haftpflicht-Allgemeine stored PASS vs audit MARGINAL): with thresholds injected rather than cached per-process, gate and judge use the identical `VerdictThresholds` instance -- no divergence from stale cache in a long-lived worker vs a fresh audit process.
- RFC-023 D10 unprojected regression (Doc 8 leaf_concentration=0.26 flipped on threshold widening): the extracted `_hard_gate` makes the structural boundary explicit and testable in isolation, so a threshold change can be unit-tested against the full corpus metadata before deployment.
- RFC-025/026 bypass-via-image-enrichment (38/123/492-char trees persisting as PASS): already blocked by the existing char floor, but the restructuring makes the floor check the first line of the image-enrichment promotion predicate rather than a nested conditional inside a 185-line function, making it impossible to accidentally reorder past it.

**(4) Migration risk and incremental sequencing**

Risk: Low-medium. The hysteresis deletion (Step A) is the only semantically breaking change -- some documents currently PASS only because of hysteresis widening and will flip to MARGINAL on reingestion. This is the correct behavior (same content, same verdict), but it will show as regressions in a corpus diff.

Sequence:

1. Step D first (injectable thresholds) -- pure refactor, zero behavioral change, unblocks test improvements.
2. Step B next (extract `_hard_gate`) -- pure refactor, zero behavioral change, removes C901.
3. Step C (flatten promotions) -- pure refactor, zero behavioral change, improves readability.
4. Step A last (delete hysteresis) -- behavioral change. Run a full corpus ingest-score before and after, diff the results. Any documents that flip PASS->MARGINAL are candidates for tree-shape improvement (splitter tuning), not threshold softening. This is the step that breaks the feedback loop permanently.

Each step is independently committable and testable. Steps B-D can be combined into a single PR if desired; Step A should be its own PR with a corpus diff attached.

**(5) Estimated effort**

Steps B+C+D (pure refactors): 2-3 hours implementation + 1 hour test updates.
Step A (hysteresis deletion + corpus validation): 1 hour implementation + 2 hours corpus ingest-score-diff cycle.
Total: ~6 hours, or one focused day including the corpus verification run.

---

### Zone 3: NFKC/garble/bidi normalization ordering and detection blindness

**Severity:** critical | **Bug count:** 8

#### Mechanism

Upstream normalization changes the Unicode codepoint profile that downstream detectors are written to key on. Adding a new normalization step (like NFKC) silently disables detectors written for pre-normalization data. Detectors written for one encoding class (PUA markers) miss entirely different corruption classes (valid-Unicode fragmentation, numeric-junk watermarks). Because garble detection runs at 5+ different pipeline points with different normalization states, a fix at one site can leave the same corruption undetected at another.

#### History

a. RFC-033 D2: _reversed_morphology detector achieved 0% TPR because NFKC decomposes the presentation forms it checks for (RFC-034 D7).
b. RFC-028 D2: Arabic Presentation Forms garble detection unconditionally rejected Human Rights PDF (native PF encoding), FAIL->ERROR regression (RFC-029 D0).
c. RFC-028 D5: improved Arabic OCR diluted garble thresholds for warid-597, MARGINAL->PASS false negative (RFC-029 D2).
d. RFC-015 D7: RTL word-splitting corruption uses valid Unicode, PUA-only garble detector misses it entirely -- 5th distinct document with garble-gate blind spot (obs#5639).
e. Runs 13-15: garble-gate false positive on MOU MOHRE (garble_ratio=1.00 with 0 actual garbled sequences, then fixed, then reappeared).
f. Run 16: Data Governance Policy 67% blocks show RTL word-splitting, downgraded PASS->FAIL.

#### Code Evidence

converters.py:2477-2481 NFKC normalization in _pre_inference_normalize runs before heading inference. helpers.py:1251-1259 garble_prongs presentation_forms prong checks PRESENTATION_RANGES after normalize_for_garble. script.py:27-29 PRESENTATION_RANGES = ((0xFB50, 0xFDFF), (0xFE70, 0xFEFF)). helpers.py:1301-1313 _is_garbled_blob boolean wrapper. helpers.py:1474-1480 _tree_is_garbled operates on flattened tree text. helpers.py:1451-1462 _garble_check_nodes per-node with script inference. converters.py:1635-1651 _text_layer_has_content uses _is_garbled_blob. converters.py:1698-1762 _document_level_text_fallback garble check on full text layer.

#### Key Files

- src/pageindex_mcp/helpers.py
- src/pageindex_mcp/converters.py
- src/pageindex_mcp/script.py

#### Simplification Proposal

Now I have the full picture. Here is the analysis:

**(1) Core Simplification**

The bug-generating mechanism is that garble detection runs on text at arbitrary points in the pipeline -- some before NFKC normalization, some after -- with no contract about what Unicode profile the text carries when it reaches the detector. The fix is to make `garble_prongs` itself responsible for its own normalization contract: it should NFKC-normalize internally (once, idempotently) before evaluating any prong, and all prongs should be written for post-NFKC codepoints only. This eliminates the implicit ordering dependency between `_pre_inference_normalize` and garble detection, collapses the five call-site-specific normalization strategies into one canonical path inside `garble_prongs`, and deletes the `presentation_forms` prong entirely (replaced by `_word_has_reversed_morphology` which already operates on post-NFKC base Arabic). The separate `normalize_for_garble` function remains but only for markdown-stripping; Unicode normalization moves inside `garble_prongs`.

**(2) Concrete Restructuring Steps**

Step 1 -- Internalize NFKC into `garble_prongs` (helpers.py ~line 1228-1236):
Add `norm = unicodedata.normalize("NFKC", norm)` after the existing `normalize_for_garble` call inside `garble_prongs`. This makes every call site -- `_text_layer_has_content`, `_document_level_text_fallback`, `_recover_picture_text` region check, `_tree_is_garbled`, `_garble_check_nodes`, and the three `client.py` retry-comparison calls -- receive identical NFKC-normalized evaluation, regardless of whether `_pre_inference_normalize` ran upstream.
Delta: +2 lines in helpers.py.

Step 2 -- Delete the `presentation_forms` prong (helpers.py ~lines 1251-1259):
After NFKC inside `garble_prongs`, presentation-form codepoints no longer exist; the prong is dead code. Delete it. The `_word_has_reversed_morphology` detector in script.py (already post-NFKC aware, uses Joining_Type table) covers the reversal signal this prong was meant to provide. Also delete `_reversed_morphology` if it still exists as a separate prong referencing presentation forms.
Delta: -9 lines in helpers.py. `PRESENTATION_RANGES` in script.py stays (used by `_pre_inference_normalize`'s gating condition).

Step 3 -- Add `single_letter_fragments` prong to catch valid-Unicode RTL word-splitting (helpers.py ~lines 1261-1266):
This prong already exists (RFC-033 D2). Verify it fires on post-NFKC text. If so, no change needed. The existing 40% threshold on single-char Arabic tokens is the correct detector for the warid-597 / Data Governance Policy class of corruption that uses valid codepoints.
Delta: 0 lines (verification only).

Step 4 -- Remove NFKC-gating from `_pre_inference_normalize` is NOT recommended:
The NFKC in `_pre_inference_normalize` (converters.py:2477-2481) serves heading inference, not garble detection. Leave it. But because `garble_prongs` now self-normalizes, the order between them no longer matters for garble correctness.
Delta: 0 lines in converters.py. Add a 1-line comment clarifying that garble detection is NFKC-independent.

Step 5 -- Consolidate `_tree_is_garbled` into `TreeSignals.from_tree` (helpers.py ~lines 1474-1480, 340-366):
`_tree_is_garbled` does `_flatten_tree_text(nodes)` then calls `_is_garbled_blob` + `_has_sparse_mojibake`. `TreeSignals.from_tree` already calls `_flatten_tree_text` at line 349 then passes the result to `_tree_is_garbled` which re-flattens. Inline the garble check in `from_tree` using the already-computed `flat_text`, delete `_tree_is_garbled` as a standalone function.
Delta: -6 lines (delete function), +2 lines (inline in from_tree). Net: -4 lines.

Step 6 -- Document the normalization contract at the `garble_prongs` docstring:
State explicitly: "This function NFKC-normalizes internally. Callers must NOT assume any particular Unicode normalization state. All prongs are written for post-NFKC codepoints."
Delta: +3 lines.

Total estimated delta: -8 net lines. 6 files touched: helpers.py (primary), script.py (comment only), converters.py (comment only), client.py (no change -- already correct since garble_prongs self-normalizes).

**(3) Historical Bug Classes This Would Have Prevented**

- RFC-034 D7 (`_reversed_morphology` 0% TPR): The prong checked presentation-form codepoints that NFKC had already decomposed. With NFKC inside `garble_prongs`, all prongs would be written for post-NFKC text from day one; nobody would write a presentation-form prong.
- RFC-028 D2 (Human Rights PDF false positive): The `presentation_forms` prong unconditionally rejected a document with native presentation-form encoding. With NFKC internalized, presentation forms decompose before the prong runs, and the prong itself is deleted -- the false positive cannot occur.
- RFC-028 D5 / RFC-029 D2 (warid-597 MARGINAL->PASS false negative): The garble gate missed numeric-junk corruption using valid Unicode. The `single_letter_fragments` prong (already present) catches this class. The consolidation ensures it runs at every call site, not just the ones that happen to go through `_garble_check_nodes`.
- Run 13-15 MOU MOHRE false positive (garble_ratio=1.00 with 0 garbled sequences): The per-site normalization differences meant the same text got different garble verdicts at different pipeline stages. A single normalization contract inside `garble_prongs` eliminates this class.
- Run 16 Data Governance Policy RTL word-splitting: Valid-Unicode fragmentation goes through `single_letter_fragments` regardless of call site.

Would NOT have prevented: the `_has_sparse_mojibake` regex/threshold calibration issues (those are pattern-level, not normalization-level).

**(4) Migration Risk and Sequencing**

Risk: NFKC inside `garble_prongs` changes the text profile for ALL garble evaluations, not just the ones downstream of `_pre_inference_normalize`. Specifically, the three `client.py` call sites (retry comparison) and the `converters.py` text-layer checks currently receive raw (non-NFKC) text. Adding NFKC changes what they evaluate.

Mitigation -- sequence in 3 increments:

Increment A (safe, zero behavioral change): Delete the `presentation_forms` prong. It already fires on 0 documents in the corpus because NFKC runs upstream of every real path. This is dead-code deletion. Run corpus cycle to confirm no regression.

Increment B (low risk): Add NFKC normalization inside `garble_prongs`, after `normalize_for_garble`. Run dual-mode: log when the NFKC-normalized result differs from the non-NFKC result without changing the returned verdict. Review the diffs across the full corpus. This is a shadow-mode deployment.

Increment C (activate): Once shadow-mode confirms no unexpected verdict changes, remove the dual-mode logging and make NFKC-normalized the sole path. Inline `_tree_is_garbled` into `from_tree`. Update docstrings.

Rollback: Each increment is independently revertible. Increment B's shadow mode is the safety net -- if unexpected verdict flips appear, the NFKC addition is not activated.

**(5) Estimated Effort**

Increment A: 0.5 hours (delete prong + run tests).
Increment B: 2 hours (add NFKC + shadow logging + corpus cycle).
Increment C: 1 hour (activate + inline `_tree_is_garbled` + docstrings + corpus cycle).
Total: ~3.5 hours of implementation, plus one full corpus cycle per increment (~45 min each).

Key files: `/Users/saliltrehan/Documents/Python_n_R/Personal/pageindex/src/pageindex_mcp/helpers.py` (lines 1212-1313, 1474-1480, 340-366), `/Users/saliltrehan/Documents/Python_n_R/Personal/pageindex/src/pageindex_mcp/script.py` (lines 27-30, 419-442), `/Users/saliltrehan/Documents/Python_n_R/Personal/pageindex/src/pageindex_mcp/converters.py` (lines 2464-2485, 1635-1651, 1698-1762, 2140-2155), `/Users/saliltrehan/Documents/Python_n_R/Personal/pageindex/src/pageindex_mcp/client.py` (lines 1402-1435).

---

### Zone 4: client.index() god-function with mutable state and 5 validate_tree re-entry points

**Severity:** high | **Bug count:** 7

#### Mechanism

Each new recovery branch (OCR retry, RTL repair, VLM fallback, image-dominant escalation) adds another validate_tree call that can overwrite shared mutable state. A fix in one branch can leave stale state from a prior branch's partial execution, causing downstream persistence to use the wrong validate_tree result. The flat-prefer guard (client.py:1746) can override ok=True (from validate_tree) to ok=False, discarding a tree that passed validation. Without snapshot protection on most branches, retry failures silently leave the wrong extraction state for classify_verdict.

#### History

a. RFC-027 D7: timeout calculation function created but never imported by worker.py -- dead code, task marked complete (RFC-028 D0).
b. RFC-034 D19: OCR density preservation fix fully implemented and staged in git but never committed, inactive during Run 19 (RFC-036 D2).
c. RFC-029 D4: keep-best comparison short-text floor returns 0.0 for <20 tokens, win condition becomes impossible (RFC-030 D1).
d. RFC-034 D3: re-normalization safety net interacted badly with block-merging, MOU MOHRE PASS->MARGINAL with 90% content loss (RFC-034 D17).
e. Run 18: Federal Decree-Law 47 splitter created 88% body-less heading fragments with 40% chars discrepancy between meta and tree.

#### Code Evidence

client.py:840 'async def index(  # noqa: C901, PLR0915'. client.py:1240,1360,1506,1596,1720 five validate_tree call sites. client.py:1299-1309 ExtractionSnapshot only on OCR escalation branch. client.py:1505-1513 RTL repair overwrites gate_result/ok/reason/original_gate_result unconditionally. client.py:1746-1758 flat-prefer guard overrides ok=True to ok=False. client.py:2116-2118 comment: 'Zone-8: classify verdict BEFORE save_doc'. client.py:2148-2165 save_doc with verdict fields. client.py:2170-2178 write_verdict call. client.py:2222 save_doc_meta call.

#### Key Files

- src/pageindex_mcp/client.py

#### Simplification Proposal

Here is the analysis.

---

**(1) Core simplification (2-3 sentences)**

Bundle all mutable pipeline state (result, ok, reason, gate_result, original_gate_result, md_content, pic_results, used_converter, route, total_chars) into a single `ExtractionState` dataclass that replaces the ~10 loose local variables. Extract a `_rebuild_and_validate(self, md_content, pic_results, ...) -> ExtractionState` method that performs the duplicated 6-step sequence (write temp md, _run_md_to_tree, split_oversized_leaf_nodes, _segment_table_nodes, validate_tree, unpack gate_result) exactly once in one place. Each recovery branch becomes a pure function `_try_<name>(state, ...) -> ExtractionState | None` that either returns a new state (with snapshot-protected keep-best comparison built in) or None (no change), eliminating the unconditional overwrites of shared mutable variables.

**(2) Concrete restructuring steps**

Step A -- `ExtractionState` dataclass in helpers.py (~40 lines net new, replaces ExtractionSnapshot which is a subset):

- File: `src/pageindex_mcp/helpers.py`
- Extend ExtractionSnapshot into ExtractionState with fields: result, ok, reason, gate_result, original_gate_result, md_content, pic_results, used_converter, route, total_chars, first_defect, tmp_md_path. Add a `keep_best(self, candidate: "ExtractionState", expected_script) -> "ExtractionState"` method that contains the keep-best comparison logic currently at client.py:1370-1474.
- Delete ExtractionSnapshot (it becomes ExtractionState). Net: +20 lines (ExtractionState is larger but ExtractionSnapshot and the inline keep-best logic move into it).

Step B -- `_rebuild_and_validate()` private method on CustomPageIndexClient (~35 lines):

- File: `src/pageindex_mcp/client.py`
- Extracts the duplicated sequence that appears 5 times: write md to temp file, call _run_md_to_tree, split_oversized_leaf_nodes, _segment_table_nodes, validate_tree, unpack gate_result into ExtractionState.
- Each current call site (lines 1231-1269, 1352-1367, 1593-1603, 1710-1727, and the initial tree build at ~1230) becomes a single call: `state = await self._rebuild_and_validate(md_content, expected_script, pdf_page_count, ext)`.
- Removes ~80 lines of duplicated tree-rebuild+validate boilerplate across the 4 recovery branches (the initial call stays but also uses the method).

Step C -- Extract recovery branches into private methods (~5 methods, each 30-80 lines):

- File: `src/pageindex_mcp/client.py`
- `_try_ocr_escalation(self, state, file_path, filename, ext, expected_script, ...) -> ExtractionState`
- `_try_rtl_repair(self, state, filename, ext, expected_script, ...) -> ExtractionState`
- `_try_rtl_flat_comparison(self, state, filename, ext, ...) -> ExtractionState`
- `_try_vlm_fallback(self, state, file_path, filename, ext, ...) -> ExtractionState`
- `_try_image_dominant_escalation(self, state, file_path, filename, ext, ...) -> ExtractionState`
- Each method receives an ExtractionState, snapshots it internally, attempts recovery, calls `_rebuild_and_validate`, and returns `state.keep_best(candidate)` or the original state on failure. The snapshot protection that currently exists only on OCR escalation is now uniform across all branches.
- The index() recovery section (lines 1286-1738, ~450 lines) collapses to ~30 lines of sequential calls with eligibility guards.
- Net: the methods total ~300 lines (moved, not new); index() shrinks by ~400 lines. The methods are slightly shorter than their inline versions because _rebuild_and_validate and keep_best are shared.

Step D -- Collapse persistence to two paths via `_persist_tree()` and reuse existing `save_flat_doc`:

- File: `src/pageindex_mcp/client.py`
- Lines 2116-2235 (tree persistence: classify_verdict + save_doc + write_verdict + save_doc_meta + save_raw + hash_cache_set) become `_persist_tree(self, state, ...)`. Similar extraction for flat persistence (already somewhat contained in the `if route == Route.FLAT` block).
- Net: ~120 lines moved into 2 methods, index() shrinks by ~100 lines (method signatures + calls replace inline code).

Step E -- Collapse flat-prefer guards (lines 1746-1798) into a `_check_flat_prefer(state) -> ExtractionState`:

- File: `src/pageindex_mcp/client.py`
- Both RFC-029 D1 (content-density) and RFC-035 D2 (landscape fallback) set ok=False + route=FLAT. Extract into one method that returns the modified state. ~50 lines moved.

**Overall line-count delta**: index() drops from ~1415 lines to ~350-400 lines. Total file grows by ~50 lines net (shared helpers offset the duplication removal). helpers.py grows by ~20 lines net (ExtractionState replaces ExtractionSnapshot).

**(3) Historical bug classes this would have prevented**

- **RFC-034 D3 (re-normalization + block-merging interaction causing MOU MOHRE PASS->MARGINAL with 90% content loss)**: The keep-best comparison would have been applied to the RTL repair branch (which currently has no snapshot protection), catching the content regression before it overwrote the better pre-repair state.
- **RFC-029 D4 (keep-best short-text floor returning 0.0 for <20 tokens)**: Centralizing keep-best into ExtractionState.keep_best() means the floor logic is written once and tested once, not scattered across one branch while missing from others.
- **RFC-028 D0 (dead code from timeout calculation never imported)**: Fewer integration points (recovery branches call _rebuild_and_validate instead of reimplementing the sequence) means less surface area for wiring oversights.
- **Run 18 (Federal Decree-Law 47 splitter creating 88% body-less fragments)**: The flat-prefer guard (RFC-029 D1) currently overwrites ok=True to ok=False without snapshot protection. With ExtractionState, the original passing tree would be preserved and the flat-prefer decision would be a routing change on the state object, not a destructive mutation of shared variables that downstream code depends on.
- **The general class of "recovery branch N overwrites state that recovery branch N+1 or persistence depends on"**: Every branch without ExtractionSnapshot today (RTL repair, VLM fallback, image-dominant escalation) can silently leave stale gate_result/original_gate_result values that classify_verdict then consumes. Uniform snapshot+keep-best eliminates this class entirely.

**(4) Migration risk and incremental sequencing**

Risk is moderate -- the function's control flow is deeply nested and every recovery branch has subtle interactions with downstream routing. The key risk is that extracting branches changes exception handling scope (try/except blocks currently catch at the branch level and fall through to the next branch).

Incremental sequence (each step is independently shippable and testable):

1. **Wave 1 (lowest risk)**: Introduce ExtractionState in helpers.py alongside ExtractionSnapshot. Add _rebuild_and_validate() to the client. Change only the INITIAL tree-build (line 1231) to use it. Full test suite validates no behavioral change. ExtractionSnapshot stays alive.
2. **Wave 2**: Migrate the OCR escalation branch (lines 1286-1487) to use ExtractionState and _rebuild_and_validate(). This branch already has snapshot protection so it is the safest migration target. Extract as _try_ocr_escalation(). Delete ExtractionSnapshot (its functionality is now in ExtractionState). Corpus spot-check the OCR-escalation documents (the garbled Arabic PDFs).
3. **Wave 3**: Migrate the three unprotected branches (RTL repair, VLM fallback, image-dominant escalation) one at a time, adding snapshot protection as they move. Each gets a corpus spot-check against the documents that exercise that branch. This is the highest-value wave -- it adds the missing snapshot protection.
4. **Wave 4**: Extract flat-prefer guards and persistence paths. Extract _persist_tree() and _persist_flat(). This is mechanical and low risk but touches the persistence contract, so verify with the full test suite + one end-to-end ingest.
5. **Wave 5**: Remove the noqa:C901 + PLR0915 suppressions from index() (it should now be under the complexity threshold). Final cleanup pass.

Each wave should be a separate commit on a feature branch, with the full test suite (`uv run pytest`) gating each merge. Corpus spot-checks (reingest the 3-5 documents that exercise each recovery path) provide the integration safety net.

**(5) Estimated effort**

- Wave 1: 0.5 day (ExtractionState + _rebuild_and_validate + initial callsite)
- Wave 2: 0.5 day (OCR escalation extraction + ExtractionSnapshot deletion)
- Wave 3: 1.5 days (3 branches, each needs careful scope analysis for exception handling + corpus spot-check)
- Wave 4: 1 day (persistence extraction + end-to-end validation)
- Wave 5: 0.25 day (cleanup + suppression removal)

**Total: ~3.5-4 days** of focused work, assuming the existing test suite covers the recovery branches adequately. If test coverage gaps are found during extraction, add 1 day for writing targeted tests for the unprotected branches before migrating them.

---

### Zone 5: Triple-write verdict persistence and read-merge-write races

**Severity:** high | **Bug count:** 5

#### Mechanism

Adding a new field to the persistence payload requires understanding which of the 3 write paths carries it, and ensuring the read-merge-write in save_doc_meta does not clobber it from a concurrent call. The asymmetric tree/flat persistence paths mean a fix to one path does not fix the other. The _confirm_write_visible barrier adds latency that compounds across the 3 writes (up to 1.35s per document), and its RFC-034 D18 predecessor (4.4s/8.8s worst-case) caused scoring-window timing regressions. The registry dual-write in worker.py:669 races with reconcile_registry_drift's stale-row deletion.

#### History

a. RFC-034 D18: write-visibility barrier with up to 4.4s/8.8s worst-case delay caused Arabic SLA doc to land 3-5 minutes late, verdict recorded as MARGINAL/ERROR despite successful persistence (RFC-036 D1).
b. RFC-033 D3: MinIO read-retry addressed read side only, left write-after-read consistency gap; cabinet_resolution_no_96 regressed MARGINAL->ERROR in Run 3 (RFC-034 D18).
c. Run 12: artifact-persistence loss for Human Rights doc (disappeared from MinIO between runs, NoSuchKey on both meta.json and tree/flat).
d. RFC-033 D0: hysteresis snapshot implemented but scoped out of operational wiring (RFC-034 D0).

#### Code Evidence

storage.py:205-226 save_doc writes processed/<id></id>.json with _confirm_write_visible. storage.py:644-728 write_verdict reads artifact, re-injects verdict, re-writes, chains to save_doc_meta. storage.py:545-641 save_doc_meta read-merge-write with _verdict_cas_guard. storage.py:262-287 save_flat_doc writes flat.json then chains to save_doc_meta (no write_verdict). storage.py:37 _WRITE_BARRIER_DELAYS = (0.05, 0.1, 0.3). storage.py:515-541 _verdict_cas_guard protects only verdict fields. client.py:2148-2165 save_doc call. client.py:2170-2178 write_verdict call. client.py:2222 save_doc_meta call.

#### Key Files

- src/pageindex_mcp/storage.py
- src/pageindex_mcp/client.py

#### Simplification Proposal

I have enough context. Here is the analysis:

---

**(1) Core simplification (2-3 sentences)**

Eliminate `write_verdict` entirely and merge all sidecar fields into a single `save_doc_meta` call issued once per ingestion. Today the tree path does three MinIO writes with visibility barriers: `save_doc` (artifact), `write_verdict` (re-reads artifact, re-injects the same verdict fields that `save_doc` already wrote, re-writes artifact, then calls `save_doc_meta`), and a standalone `save_doc_meta` (provenance fields). The fix is: `save_doc` already writes verdict into the artifact -- delete `write_verdict` -- and collapse the two separate `save_doc_meta` calls (one from the deleted `write_verdict`, one explicit at client.py:2222) into a single call carrying both verdict and provenance fields.

**(2) Concrete restructuring steps**

**Step A -- Delete `write_verdict` function** (`storage.py:644-728`, ~85 lines deleted).
Remove the function and its import from `__init__.py` / `client.py`. The artifact already contains verdict fields from `save_doc`. The sidecar will get verdict fields from the unified `save_doc_meta` call.

**Step B -- Merge the two `save_doc_meta` call-sites in client.py tree path** (`client.py:2170-2222`).
Replace the `write_verdict` call (lines 2170-2178) and the standalone `save_doc_meta` call (line 2222) with a single `save_doc_meta` call whose payload includes both verdict fields (`verdict`, `verdict_reason`, `pipeline_version`, `verdict_computed_at`, `max_leaf_ratio`) and provenance fields (`build_sha`, `effective_config`, `extraction_route`, etc.). This merges roughly 55 lines into 30. Net delta: ~-25 lines in client.py.

**Step C -- Align flat-doc path field coverage** (`client.py:2038-2060`).
The flat path already writes a single `save_flat_doc` (which chains to one `save_doc_meta`). Verify it carries the same provenance fields (extraction_route, converter_name, page_count, extraction_stages, remote_build_sha) that the tree path writes. Add any missing fields to the `flat_meta` dict. This is a gap-close, not a structural change. ~+10 lines.

**Step D -- Make `save_doc_meta` write-only (no read-merge) for the primary ingestion call** (`storage.py:545-641`).
Add an optional `merge=True` parameter. When `merge=False` the function writes the sidecar directly from the supplied dict without reading the existing sidecar. The ingestion path (which always constructs the complete sidecar payload) passes `merge=False`, eliminating the read-before-write race. Promotion sweeps and registry backfill -- which legitimately need read-merge-write semantics -- keep the default `merge=True`. ~+8 lines, ~-3 lines (net +5).

**Rough totals:** -85 (write_verdict deletion) -25 (client.py consolidation) +10 (flat field alignment) +5 (merge parameter) = **net ~-95 lines**.

**(3) Historical bug classes this would have prevented**

- **RFC-034 D18 / RFC-036 D1 timing regressions**: The 3-write chain accumulated up to 1.35s of barrier delays (worst-case 4.4s/8.8s under the original tuning). Collapsing to 2 writes (artifact + sidecar) removes one entire barrier cycle. The `merge=False` mode removes the read-before-write GET, further cutting latency.
- **Run-12 Human Rights artifact disappearance**: The read-merge-write race in `save_doc_meta` could clobber fields when `write_verdict` and the provenance `save_doc_meta` ran in close succession. A single write-only call eliminates this race for the primary ingestion path.
- **RFC-033 D3 cabinet_resolution_no_96 regression**: The write_verdict re-read of the artifact is a consistency gap -- if the artifact was not yet visible (barrier not reached), `write_verdict` falls through to sidecar-only mode, losing the artifact update. Removing `write_verdict` removes this gap entirely.
- **Field-addition confusion**: Any future field added to persistence requires understanding only two writes (artifact via `save_doc`, sidecar via `save_doc_meta`) instead of three, with clearly separated scopes: artifact = query-serving data, sidecar = metadata/provenance.

**(4) Migration risk and sequencing**

Risk is low-to-moderate. The artifact shape is unchanged (verdict was already in `save_doc`). The sidecar shape is unchanged (same fields, just written once instead of twice).

Sequence:

1. **Step D first** (add `merge` parameter to `save_doc_meta`, default True). Zero behavioral change -- all existing callers keep merge=True. This is independently shippable and testable.
2. **Steps A+B together** (delete `write_verdict`, consolidate to one `save_doc_meta(merge=False)` call). This is the core change. Gate on: re-ingest 3-5 corpus docs, diff the resulting `.meta.json` field-by-field against baseline. The `_verdict_cas_guard` remains active for `merge=True` callers (promotion sweep, backfill) and is inert under `merge=False`.
3. **Step C last** (flat-path field alignment). Independently testable -- re-ingest a flat doc, verify sidecar parity with tree path.

The main risk is that some downstream reader depends on `write_verdict` being callable independently (e.g., a re-scoring pass that updates verdict without re-ingesting). Grep for `write_verdict` call-sites beyond client.py before deleting. If a standalone re-scoring path exists, it should call `save_doc_meta(merge=True)` with verdict fields (which the CAS guard already protects).

**(5) Estimated effort**

2-3 days for an engineer familiar with the codebase. Day 1: Step D + unit tests for the `merge=False` path. Day 2: Steps A+B + integration test (ingest 5 docs, diff sidecars). Day 3: Step C + corpus spot-check on flat docs. The risk-gating corpus diff is the calendar bottleneck, not the code change.

---

### Zone 6: Picture/chart recovery pipeline with ordering-dependent stage coupling

**Severity:** high | **Bug count:** 7

#### Mechanism

The pipeline stage ordering determines whether content is recovered or lost: text fallback before containment check suppresses recovery; NFKC before garble checking disables detection; source selection before fallback stages means fallbacks operate on inferior input. Each new stage or flag creates ordering dependencies with every existing stage. The body_for_containment patch is a parameter-threading workaround for a structural ordering problem -- adding a new stage between the fallback and the containment check would reintroduce the same suppression bug. The conjunctive flag dependencies mean toggling one flag can silently disable a seemingly-unrelated code path.

#### History

a. RFC-029 D3: naive fence-parity toggle caused 0-100% content loss on Arabic documents (RFC-030 D0).
b. RFC-015 D6: per-picture Tesseract OCR pipeline wired but never actually extracts text from scanned Arabic PDFs (obs#5329).
c. RFC-035 D2: landscape rasterize-rotate-reextract caused timeout on world-stats-pocketbook, chart labels shattered into 71+ singleton kv blocks, both uae_numbers variants regressed PASS->FAIL/MARGINAL (RFC-036 D0).
d. RFC-010 D1: OCR escalation only fires on page-level image_ratio>50%, partial-page charts permanently lose content (obs#4104, obs#4150).
e. RFC-020 F2: expected_script + D2 Latin-gibberish detection disabled F0 splice guard, tree collapsed to flat on Arabic docs (2026-07-27 audit).
f. Run 9-10: world-stats-pocketbook failed across 2 consecutive runs (6.1MB, timeout).

#### Code Evidence

converters.py:2061-2372 _recover_picture_text god-function. converters.py:3424-3427 body_for_containment snapshot workaround. converters.py:2520-2523 decide_ocr_mode requires _OCR_ESCALATION AND image markers. converters.py:2140-2225 page_coverage gate with coverage exemption cascade. converters.py:1698-1762 _document_level_text_fallback appends pdfium text before containment. converters.py:3449-3458 landscape fallback creates orphaned picture regions invisible to _recover_picture_results.

#### Key Files

- src/pageindex_mcp/converters.py

#### Simplification Proposal

No proposal was generated for this zone in the current data set.

---

### Zone 7: Heading depth recovery chain with cascading overwrites

**Severity:** medium | **Bug count:** 6

#### Mechanism

Each heading recovery stage overwrites the previous stage's depth decisions, so a bug in any one stage cascades through the entire chain. The in-place mutation of result.document means downstream code that reads heading data sees mutated state unless it captured a pre-mutation snapshot. Arabic heading injection must run before NFKC (because NFKC alters the regex-matched text), creating a tight ordering constraint between converters.py:3060-3063 and converters.py:2477-2481. Adding a new heading heuristic requires understanding the full overwrite chain and where in the sequence it must be inserted.

#### History

a. RFC-027 D4: Arabic structural heading injection prev_blank guard fails on continuous OCR output, 60-char length cap too tight for 66-90+ char Arabic legal headings, producing flat depth-0 trees (RFC-028 D1, obs#5639).
b. RFC-034 D11: ToC heading stripping collapsed Penal Code depth-3 to depth-2, 493/595 nodes flattened, PASS->MARGINAL (RFC-034 D16).
c. RFC-034 D16: ToC-strip depth guard disabled body-text association, Federal Decree-Law 47 created 88% body-less heading fragments (Run 18).
d. RFC-034 D16/D17: table repair row guard flattened cabinet_resolution_no_21 multi-row table headers, PASS->MARGINAL (Run 18).
e. Heading-number parser _relevel_by_numbering mis-nested subsections 2.1.6/3.1.6/1.3.6 as top-level siblings of section 6 (obs#5639).

#### Code Evidence

converters.py:480 _relevel_by_containment (PRIMARY depth source). converters.py:268 _relevel_by_numbering (overwrites if depth<2). converters.py:743 _relevel_by_outline (overwrites if still depth<2). converters.py:822 _recover_heading_depth orchestrates the chain. converters.py:3050-3064 _build_candidate ordering: inject headings THEN _pre_inference_normalize. converters.py:3391-3402 over-prune fallback source selection. helpers.py:3004-3023 toc_strip_guard (depth drop >1 or node loss >20%).

#### Key Files

- src/pageindex_mcp/converters.py
- src/pageindex_mcp/helpers.py

#### Simplification Proposal

No proposal was generated for this zone in the current data set.

---

### Zone 8: Wiring-gap pattern (implemented-but-unwired / marked-complete-but-absent)

**Severity:** high | **Bug count:** 8

#### Mechanism

The process-boundary split (worker.py parent spawns converters_cli.py child) means a function added to converters.py or helpers.py is not automatically available in the worker parent's decision path. The 5 validate_tree re-entry points in client.index() each need independent wiring for any new failure reason or recovery strategy. The gap between 'implement the function' and 'wire it into every call site that needs it' is invisible to task completion tracking -- the function exists, the tests pass on the function, but it is never reached from production code paths.

#### History

a. RFC-027 D7: dynamic timeout calculation function created but never imported/called by worker.py (RFC-028 D0).
b. RFC-029 D0: _check_bidi_coherence defined TWICE but never called from any pipeline path (RFC-030 D5).
c. RFC-029 D6 Phase B: judge calibration rules marked task 1.7 complete but never written to skill file (RFC-030 D6).
d. RFC-034 D19: enrichment displacement fix fully implemented and staged in git but never committed, inactive during Run 19 (RFC-036 D2).
e. RFC-033 D0: snapshot_prior_verdicts() implemented but scoped out of operational wiring (RFC-034 D0).
f. RFC-029 D0/D1/D2: 4 new validate_tree failure reasons never wired into client.py recovery routing, 3 PASS-to-ERROR regressions (RFC-030 D2).
g. RFC-019 D1: find_prior_verdict hysteresis lookup wired but reingestion pipeline wipes source files before search runs (obs#5333).

#### Code Evidence

worker.py:6-8 'Conversion runs in a fresh child process (converters_cli)' documenting process boundary. client.py:1240,1360,1506,1596,1720 five independent validate_tree wiring points. helpers.py:1354-1356 comment: 'Zone-3: _check_bidi_coherence DELETED' (was dead code). storage.py:900 find_prior_verdict. storage.py:972 snapshot_prior_verdicts. helpers.py:185-198 REASON_POLICY must be kept exhaustive with TreeDefect (assertion at line 200-202).

#### Key Files

- src/pageindex_mcp/client.py
- src/pageindex_mcp/worker.py
- src/pageindex_mcp/helpers.py
- src/pageindex_mcp/storage.py

#### Simplification Proposal

No proposal was generated for this zone in the current data set.

## Cross-Cutting Themes

- Incomplete wiring: fixes, detectors, and new failure reasons are implemented but never called, never wired into recovery/escalation routing, or wired but disconnected from the live data path (dead code, unwired validate_tree reasons, unwired OCR escalation, unwired hysteresis lookup, uncommitted staged fixes).
- Threshold/gate softening (widened leaf-ratio, hysteresis bands, image_enrichment_promoted) opens bypass paths that suppress quality checks and let near-zero-content or degenerate trees persist as PASS, violating Hard Rule 5, rather than fixing root causes.
- Subsequent gate hardening re-surfaces the same pre-existing defects the earlier softening had masked; meanwhile gate check ordering (structural checks running before content/garble checks) misroutes failures and prevents correct escalation.
- Garble/bidi detection is brittle to upstream normalization — NFKC decomposition removes the Arabic Presentation Forms the detectors were written to key on — and is blind to whole classes of corruption (valid-Unicode RTL word-splitting, RTL title reversal, numeric-junk watermarks) unless the exact assumed encoding survives.
- Naive or overly-broad post-processing heuristics (fence/HR stripping, ToC-node stripping, global bidi re-normalization, table row guards) fail catastrophically on edge cases outside their target scope, causing partial-to-total content loss or table/hierarchy collapse.
- Judge-vs-gate verdict divergence: the persisted verdict gate and the downstream audit judge disagree, so trees a human/judge would flag as unusable (RTL-reversed, garbled, image-only, shallow) persist under a stored PASS/MARGINAL verdict.
- Timing/visibility races between worker writes and scorer reads cause false ERROR verdicts on successfully-processed documents; mitigating one side (read-retry) leaves the other side (write-visibility barrier delay) unaddressed or introduces its own new regression.
- Chart/image/table content is a recurring blind spot: Docling clusters chart text into Picture bboxes lost as image placeholders, per-picture OCR pipelines are wired but never actually recover Arabic text, and OCR escalation triggers only on whole-page image ratio, missing partial-page charts.
- Metrics/verdict feedback loops: changes that reduce measured metrics (char counts, node counts) without removing real structure trigger judge re-evaluation and verdict oscillation independent of actual content quality; meta-reported character counts can diverge sharply (up to 15x) from what is actually persisted.
- The same recurring problem documents (world-stats-pocketbook, uae_numbers_landscape/portrait, MOU MOHRE, وارد 597, حقوق الإنسان, سياسة حوكمة) fail the same way across multiple consecutive runs and RFCs, indicating unresolved architectural gaps rather than one-off regressions.
- Large feature-branch merges (e.g. feat/image-block-picture-ocr) can wholesale-revert prior, already-verified fixes across many unrelated mechanisms simultaneously, indicating architectural conflict between the new route and the existing pipeline.
- RFC tasks marked 'complete' (dynamic timeout wiring, judge calibration rules, staged density-preservation fix, hysteresis wiring) were in some cases never actually implemented, committed, or wired despite being reported as done.
