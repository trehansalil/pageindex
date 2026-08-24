# Zone Triage Report — Post-Remediation 2026-08-12

| Field | Value |
|---|---|
| **Date** | 2026-08-12 (triage run: 2026-08-21) |
| **Audit source** | `audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md` |
| **Delta source** | `audit/ZONE_DELTA_2026-08-12_POST.md` |
| **Scorecard** | `audit/REMEDIATION_SCORECARD_2026-08-12_POST.md` |
| **Workflow** | zone-triage (14 agents, 5 phases: Explore → Score → Sequence → Spec Gen → Validate) |

---

## 1. Executive Summary

**Overall verdict: REGRESSED** — net bug count rose by 3 (50 → 53) despite closing one 12-bug critical zone (Picture/OCR Enrichment and Page-Level Escalation Conflation).

### What improved
- 1 zone **closed**: Picture/OCR Enrichment and Page-Level Escalation Conflation (was critical, 12 bugs eliminated)
- 2 zones **improved**: Worker-Child Process Boundary (high, 5 bugs) and Duplicated Convergent Logic (medium, 4→6 bugs by count but structural improvement)

### What got worse
- 3 zones **regressed**: Tree/Flat Verdict Split (critical, 11→18 bugs), Converter-Gate-Route Ordering Chain (critical, 12→14 bugs), Arabic/RTL Pipeline Blindness (high, 9→14 bugs)
- 1 zone **stalled**: Garble Detection Fragmentation (critical, 12→16 bugs — stalled 6+ cycles)
- 2 **new zones** surfaced: Registry Dual-Write Consistency (high, 8 bugs) and ZDR/PII Egress Gap (high, 3 bugs — Hard Rule 3 compliance violation)

### Recommended approach
**Convergence, not expansion.** The 7 zones are organized into 3 dependency-ordered waves. Wave 1 fixes the foundational verdict split and compliance gaps (independent zones, no shared files). Wave 2 fixes garble detection and converter-gate ordering (share `indexer.py` but in disjoint method regions). Wave 3 adds RTL support and deduplicates convergent logic (depends on waves 1+2 outputs).

---

## 2. Priority-Ordered Zone Table

| Priority | Zone Name | Severity | Wave | Bug Count | Score | Status | Key Mechanism |
|:---:|---|:---:|:---:|:---:|:---:|:---:|---|
| 1 | **Tree/Flat Verdict Split** | critical | 1 | 18 | 140.4 | regressed | Tree/flat paths run different gate subsets; 7 hard-fail gates inert on flat docs |
| 2 | **Arabic/RTL Pipeline Blindness** | high | 3 | 14 | 65.52 | regressed | Latin-centric defaults in 5 interlocking subsystems; ScriptContext never enriched from content |
| 3 | **Registry Dual-Write Consistency** | high | 1 | 8 | 28.8 | new | Registry writes scattered across 4 call sites with no transactional guarantee |
| 4 | **Garble Detection Fragmentation** | critical | 2 | 16 | 24.96 | stalled | NFKC ordering, expected_script self-corruption, per-call-site dispatch |
| 5 | **Converter-Gate-Route Ordering Chain** | critical | 2 | 14 | 21.84 | regressed | decide_route not re-evaluated after recovery; gate results stale |
| 6 | **ZDR/PII Egress Gap** | high | 1 | 3 | 10.8 | new | zdr_egress_gate missing from _run_md_to_tree and _run_page_index_retrying |
| 7 | **Duplicated Convergent Logic** | medium | 3 | 6 | 6 | improved | Hysteresis, recovery, flat-extract duplicated across tree/flat paths |

> **Excluded zone:** Worker-Child Process Boundary (score 4.5, improved — already partially fixed, not triaged for further work)

---

## 3. Wave Plan

### Wave 1: Tree/Flat Verdict Split, Registry Dual-Write Consistency, ZDR/PII Egress Gap

**Rationale:** Three independent zones with zero confirmed file overlaps. Z1 (verdict.py, gates.py, indexer.py) is the highest-priority foundational fix — Z2 and Z3 both depend on its evaluate_gates and indexer.py changes landing first. Z4 (registry_mirror.py, storage/*.py, reconcile.py) is fully isolated and addresses Hard Rule 2 compliance. Z6 (llm.py, config.py, server.py, pictures.py egress gate) is isolated and addresses Hard Rule 3 compliance. The only cross-wave file touch is converters/pictures.py (Z6 edits zdr_egress_gate; Z2 in wave 2 edits detect_garble callers) but these are confirmed as different functions with no call-edge between them.

**Shared files:** None (all zones edit disjoint file sets)

### Wave 2: Garble Detection Fragmentation, Converter-Gate-Route Ordering Chain

**Rationale:** Both zones depend on Z1 (wave 1) and are prerequisites for Z7 (wave 3). They share client/indexer.py but in distinct method regions: Z2 edits _persist_flat_result (garble gate integration at line ~720-954) while Z3 edits _convert_to_tree (gate-and-route finalization at line ~956+) and helpers/types.py (decide_route). The indexer has only 12 methods; the two zones' target methods do not overlap. Z2 consolidates garble detection into helpers/garble.py and merges the flat-path gate into the now-stabilized evaluate_gates from wave 1. Z3 rewires converter-gate-route ordering in the _convert_to_tree path stabilized by wave 1. Running these in parallel halves the wave count versus serializing them, with managed merge risk on indexer.py.

**Shared files:** `src/pageindex_mcp/client/indexer.py`

### Wave 3: Arabic/RTL Pipeline Blindness, Duplicated Convergent Logic

**Rationale:** Both zones depend on wave 2 outputs: Z7 needs Z2's consolidated garble detection (tree_validation.py) and Z3's stabilized converter pipeline (pipeline.py). Z8 needs Z2's garble dedup and Z7's new RTL code paths to exist before it can safely deduplicate convergent logic. Z7's primary edit targets are converters/pipeline.py and helpers/tree_validation.py (RTL support); Z8's primary targets are helpers/flat.py and client/recovery.py (deduplication). The secondary overlap on flat.py is in different aspects: Z7 adds RTL handling to route_and_extract_flat while Z8 extracts shared logic from it. Within the wave, Z7's flat.py changes should merge before Z8's deduplication pass. client/indexer.py is touched by both but in different recovery-mixin regions. Sequencing Z7 before Z8 within the wave manages the flat.py/recovery.py overlap.

**Shared files:** `src/pageindex_mcp/helpers/flat.py`, `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/client/recovery.py`

---

## 4. Per-Zone Fix Specs

### 4.1. Tree/Flat Verdict Split

| Field | Value |
|---|---|
| **Priority** | 1 |
| **Severity** | critical |
| **Wave** | 1 |
| **Bug count** | 18 |
| **Score** | 140.4 |
| **Complexity** | medium |
| **Depends on** | None |

#### Mechanism to Eliminate

The tree-path and flat-path persistence methods (_persist_tree_result vs _persist_flat_result) compute document verdicts through fundamentally different gate evaluation pipelines but write to the same verdict sidecar/registry. The flat path at indexer.py:842 calls compute_verdict WITHOUT passing state.gate_result (defaults to None), triggering the evaluate_gates flat branch (verdict.py:184-193) which re-derives signals and runs only FLAT_GATE_SUBSET (3 of 10 gates: GARBLING, NODE_GARBLING, REORDERED). The tree path at indexer.py:980 correctly passes state.gate_result, getting all 10 gates. This means 7 hard-fail gates (EMPTY_NODE_CONTAMINATION, LOW_CONTENT_DENSITY, SUSPECT_DENSITY, BIDI_DEGRADED, RTL_REVERSAL, NODE_COUNT_LOW, DEPTH_LOW) are structurally inert on flat-routed documents. Additionally, apply_promotions at verdict.py:280-283 computes _structural_ok differently when validate_result is None (flat) vs present (tree): flat uses sig.node_count >= 3 and sig.depth >= 2, while tree uses _all_defects disjointness check -- these can diverge. The hysteresis block is duplicated verbatim at indexer.py:851-877 (flat) and indexer.py:989-1014 (tree), creating independently-drifting copies. Each fix to any gate/threshold/promotion must be validated against both paths, and historically half of such fixes only land on one path.

#### Strategy

Eliminate the split by threading state.gate_result through the flat path so both paths run through identical gate evaluation. Extract the duplicated hysteresis block into a shared helper in `helpers/verdict.py` (sync function `apply_verdict_hysteresis` — **Zone 1 owns this extraction**; Zone 7 references it). Remove FLAT_GATE_SUBSET and the flat re-derivation branch entirely. Sequence: (D) extract hysteresis first (pure refactor, zero semantic change), (A) thread gate_result through flat path, (B+C) remove dead flat branch and FLAT_GATE_SUBSET, (E) remove flat kwarg. This avoids the 16-cycle pattern of broadening mechanisms -- it is a net deletion (-60 to -80 lines) that converges two paths into one.

#### Code Targets

**Target A: Extract duplicated hysteresis block into shared sync helper `apply_verdict_hysteresis(verdict, verdict_reason, sha256, filename, path_label) -> tuple[str, str]` in `helpers/verdict.py`**

- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 851-877, 989-1014
- **How:** Create a new module-level sync function `apply_verdict_hysteresis` in `helpers/verdict.py` that encapsulates the _LEDGER_PRIORITY dict, the read_verdict_ledger call, the comparison, the override, and the graceful-degradation except block. In `indexer.py`, import `apply_verdict_hysteresis` from `helpers.verdict` and replace the inline block at lines 851-877 (flat path) and lines 989-1014 (tree path) with calls to this import. The _LEDGER_PRIORITY dict is defined once at module level inside `helpers/verdict.py`. The path_label parameter ('flat'/'tree') is only for logging differentiation.
- **Constraint:** The extracted function must produce byte-identical verdict/verdict_reason values for the same inputs. The graceful-degradation behavior (continue with computed verdict on ledger read failure) must be preserved. This is a zero-semantic-change refactor -- run full test suite and corpus scoring to confirm no verdict changes.

**Target B: Thread state.gate_result through flat path's compute_verdict call**

- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 842-848
- **How:** At line 842, change the compute_verdict call from compute_verdict(flat_structure, content_class, image_enrichment_ratio=..., expected_script=..., flat=True) to compute_verdict(flat_structure, content_class, state.gate_result, image_enrichment_ratio=..., expected_script=..., flat=True). The gate_result is the TreeGateResult from validate_tree (set at line 705), which is the same object the tree path passes at line 983. When state.gate_result is None (non-PDF paths that skip validate_tree), the existing None-handling in evaluate_gates already works correctly.
- **Constraint:** state.gate_result may legitimately be None for non-PDF file types that skip validate_tree (e.g. .docx, .md). The None branch in evaluate_gates (lines 157-161) must continue to handle this. Flat-routed documents that previously escaped 7 hard-fail gates will now be subject to them -- expect some PASS/MARGINAL docs to shift to FAIL (semantically correct but needs corpus validation before merging).

**Target C: Remove the flat re-derivation branch in evaluate_gates**

- **File:** `src/pageindex_mcp/helpers/verdict.py`
- **Lines:** 184-193
- **How:** Delete lines 184-193 (the 'if validate_result is None and flat:' block that iterates FLAT_GATE_SUBSET and re-derives defects). After Step A guarantees validate_result is always passed for PDFs, this branch is dead code. For non-PDFs where validate_result is genuinely None, the existing None-handling at lines 157-161 plus the reordered fallback at line 195 already cover the cases.
- **Constraint:** The reordered fallback at line 195 ('if validate_result is None and not flat and sig.is_reordered') must be preserved or generalized -- it covers the case where validate_tree was not called but the document has reordered content. Once the flat branch is removed, consider changing this to 'if validate_result is None and sig.is_reordered' (removing the 'not flat' guard since flat=True with validate_result=None should no longer occur in production after Step A).

**Target D: Remove the flat: bool = False kwarg from evaluate_gates and compute_verdict signatures**

- **File:** `src/pageindex_mcp/helpers/verdict.py`
- **Lines:** 131, 382, 407
- **How:** In evaluate_gates (line 131), remove 'flat: bool = False' from the parameter list. In compute_verdict (line 382), remove 'flat: bool = False' from the parameter list. Remove the 'flat=flat' pass-through at line 407 (the call from compute_verdict to evaluate_gates). The flat parameter is only consumed by the re-derivation branch deleted in the previous step.
- **Constraint:** All callers of compute_verdict and evaluate_gates must be updated. Production caller: indexer.py:842-847 (remove flat=True). Test callers: test_compute_verdict.py lines 102, 107 (update or delete). The classify_verdict wrapper (line 422-443) does not pass flat, so no change needed there.

**Target E: Remove FLAT_GATE_SUBSET, _FLAT_APPLICABLE_DEFECTS, and the flat_applicable field from GateSpec**

- **File:** `src/pageindex_mcp/helpers/gates.py`
- **Lines:** 474-504
- **How:** Delete lines 474-504 (the _FLAT_APPLICABLE_DEFECTS derivation, assertion, and FLAT_GATE_SUBSET construction). Remove the flat_applicable field from GateSpec in types.py:244. Remove flat_applicable=True from the three GateSpec entries in GATES (lines 336, 361, 371). Remove the import of FLAT_GATE_SUBSET from verdict.py:17. Remove the export from helpers/__init__.py.
- **Constraint:** Must also update: helpers/__init__.py (remove FLAT_GATE_SUBSET from imports and __all__), test_compute_verdict.py (delete TestFlatGateSubset class), test_gate_table.py (delete test_flat_applicable_derivation, update GateSpec field assertions). The assertion at gates.py:482-495 that validates _FLAT_APPLICABLE_DEFECTS must also be deleted.

**Target F: Remove flat_applicable field from GateSpec dataclass**

- **File:** `src/pageindex_mcp/helpers/types.py`
- **Lines:** 244, 234-236
- **How:** Delete the flat_applicable: bool = False line at line 244. Remove any docstring references to flat_applicable (lines 234-236).
- **Constraint:** Any test that introspects GateSpec fields (test_gate_table.py:173 checks for 'flat_applicable' in fields) must be updated.

**Target G: Unify _structural_ok computation in apply_promotions to always use all_defects-based check**

- **File:** `src/pageindex_mcp/helpers/verdict.py`
- **Lines:** 280-283, 246
- **How:** At lines 280-283, _structural_ok uses two different computations depending on whether validate_result is not None. After Step A, validate_result will always be provided for PDF documents. Change the computation to always use the all_defects-based check: _structural_ok = {TreeDefect.NODE_COUNT_LOW, TreeDefect.DEPTH_LOW}.isdisjoint(outcome.all_defects) -- the outcome already carries all_defects from evaluate_gates. Remove the validate_result parameter from apply_promotions signature entirely since it is only used for this one check, and the check can now use outcome.all_defects unconditionally.
- **Constraint:** For the edge case where validate_result is None (non-PDF file types), outcome.all_defects will be an empty frozenset (set at verdict.py:161), so isdisjoint returns True (structural_ok=True). The fallback sig.node_count/sig.depth check currently returns False for thin non-PDF docs -- this behavioral change is acceptable because non-PDF docs with thin trees should not reach the promotion path anyway (they are caught earlier by zero_content or hard-fail gates). Verify by running the full test suite and checking no non-PDF test cases regress.

#### Test Requirements

- **`tests/test_compute_verdict.py`** (contract): Unified gate evaluation: compute_verdict with a TreeGateResult containing EMPTY_NODE_CONTAMINATION or LOW_CONTENT_DENSITY must produce FAIL verdict regardless of whether flat=True was formerly passed. Specifically: (1) TreeGateResult with defect=EMPTY_NODE_CONTAMINATION + all_defects including it must return FAIL, not be silently promoted. (2) TreeGateResult with defect=LOW_CONTENT_DENSITY must return FAIL. (3) compute_verdict with validate_result=None (non-PDF path) must still produce a valid VerdictResult using TreeSignals derivation.
- **`tests/test_compute_verdict.py`** (regression): Delete or update TestFlatGateSubset class and TestComputeVerdictFlatMode class. test_flat_true_accepted (line 102) must be replaced with a test that compute_verdict does NOT accept a flat kwarg (TypeError). test_flat_true_with_treegateresult_uses_gate_result (line 107) must be replaced with a test confirming the same behavior via the unified path (passing TreeGateResult as the third positional arg without flat=True).
- **`tests/test_zone1_hysteresis.py`** (contract): The extracted _apply_verdict_hysteresis helper must: (1) return the original verdict when no prior exists in ledger. (2) return the prior verdict when ledger has higher-priority verdict (PASS > MARGINAL > FAIL > ERROR). (3) return the original verdict when ledger has lower-priority verdict. (4) return the original verdict and log a warning when read_verdict_ledger raises an exception (graceful degradation). (5) produce byte-identical verdict_reason format: 'anchored_by_ledger(was={original_verdict}:{original_reason})'.
- **`tests/test_gate_table.py`** (regression): Update test_flat_applicable_derivation (line 179) -- either delete it or replace with a test confirming flat_applicable field no longer exists on GateSpec. Update GateSpec field assertions (line 173) to remove 'flat_applicable' from expected fields.
- **`tests/test_zone1_verdict_unification.py`** (exhaustiveness): End-to-end contract: a flat-routed document (state.ok=False, state.route=Route.FLAT) with state.gate_result containing all_defects={EMPTY_NODE_CONTAMINATION, GARBLING} must receive a FAIL verdict (not MARGINAL or PASS), because EMPTY_NODE_CONTAMINATION is in HARD_FAIL_DEFECTS. This is the exact regression path from RFC-029 D1/D2 and Runs 7-8 that the zone fix prevents.
- **`tests/test_zone1_verdict_unification.py`** (contract): Structural_ok unification: apply_promotions must use all_defects-based _structural_ok check for both tree and flat paths. Test with outcome.all_defects containing NODE_COUNT_LOW -- _structural_ok must be False regardless of sig.node_count value. Test with empty all_defects -- _structural_ok must be True.

#### Wiring Checks

| Symbol | Must Be In | Check Type |
|---|---|---|
| `apply_verdict_hysteresis` | `src/pageindex_mcp/helpers/verdict.py` | symbol_exists |
| `apply_verdict_hysteresis` | `src/pageindex_mcp/client/indexer.py` | import |
| `_LEDGER_PRIORITY` | `src/pageindex_mcp/helpers/verdict.py` | symbol_exists |
| `FLAT_GATE_SUBSET` | `src/pageindex_mcp/helpers/gates.py` | must_not_exist |
| `FLAT_GATE_SUBSET` | `src/pageindex_mcp/helpers/verdict.py` | must_not_exist |
| `FLAT_GATE_SUBSET` | `src/pageindex_mcp/helpers/__init__.py` | must_not_exist |
| `flat_applicable` | `src/pageindex_mcp/helpers/types.py` (GateSpec) | must_not_exist |

#### Corpus Validation

**Affected documents:** Haftpflicht (German garbled doc -- previously FAIL->PASS via flat 3-gate subset missing LOW_CONTENT_DENSITY; expected to stay FAIL after unification), Any flat-routed PDF with empty_node_contamination in original gate_result (previously invisible to flat verdict; now correctly FAIL), Any flat-routed PDF with low_content_density in original gate_result (previously invisible; now correctly FAIL), Any flat-routed PDF with suspect_density in original gate_result (previously invisible; now correctly FAIL), Small flat documents previously promoted via small_doc_promoted where _structural_ok diverged between all_defects and sig-based checks

**Expected verdict direction:** improve

**Spot-check count:** 15

---

### 4.2. Arabic/RTL Pipeline Blindness

| Field | Value |
|---|---|
| **Priority** | 2 |
| **Severity** | high |
| **Wave** | 3 |
| **Bug count** | 14 |
| **Score** | 65.52 |
| **Complexity** | large |
| **Depends on** | Garble Detection Fragmentation, Converter-Gate-Route Ordering Chain |

#### Mechanism to Eliminate

Latin-centric pipeline assumptions cause every Arabic-specific fix to create new interactions with Latin-centric defaults elsewhere. Five interlocking defect patterns: (1) heading injection (_inject_arabic_structural_headings) injects just enough headings to clear validate_tree depth>=2 threshold, blocking flat fallback that yields 3-5x more content; (2) OCR language detection reads FILENAME not content (_script_from_filename via detect_ocr_langs), so Arabic scans with English filenames never get 'ara' added to Tesseract lang list; (3) ensure_tessdata silently falls back to deu+eng when Arabic tessdata unavailable instead of raising TessdataUnavailableError (the raise only fires when TESSDATA_PREFIX is set AND download is disabled); (4) table_is_rtl re-evaluates per-merge in stitch_continuation_tables, so borderline Arabic-char ratios can flip RTL/LTR mid-document; (5) flat-prefer multiplier (3.0x) is too high for Arabic docs where heading injection produces content-poor trees (marsoom 13: flat=5972 chars vs tree=1225 chars, ratio=4.87x required to trigger but 5972 < 3*1225=3675 is false, wait -- 5972 > 3675 so it SHOULD fire -- the actual problem is heading injection makes ok=True, and _recover_flat_prefer requires ok=True, so it runs but the heading injection also inflates tree chars above the raw threshold). The architectural root is that ScriptContext is computed once at index() entry from filename only and is never enriched with content-derived signals, so all downstream subsystems either use filename-only inference or use their own ad-hoc Arabic detection with inconsistent thresholds.

#### Strategy

Introduce a content-aware ScriptContext enrichment pass after converter output is available (post-conversion, pre-validation). Enrich ScriptContext.from_document with raw converter output text so content-based Arabic detection supplements filename inference. Add an Arabic-aware flat-prefer guard with a lower multiplier when expected_script=='Arab'. Stabilize table_is_rtl by computing it once per document (not per-merge). Ensure tessdata raises TessdataUnavailableError for non-Latin languages even when TESSDATA_PREFIX is unset (currently assumes system install has all languages). Gate the flat-prefer comparison behind a script-aware threshold so Arabic documents with heading-injection-inflated trees can still fall to flat when flat yields significantly more content.

#### Code Targets

**Target A: Enrich ScriptContext with post-conversion content text after _convert_to_tree returns md_content**

- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 1145-1151
- **How:** After line 1151 (expected_script = script_context.dominant_script), add a post-conversion enrichment block: after _convert_to_tree completes and state.md_content is available, call ScriptContext.from_document(filename, raw_text=state.md_content) to re-derive a content-enriched ScriptContext. If the enriched context discovers Arabic content that filename-only missed (e.g. English filename on Arabic PDF), update expected_script. This is a ~10-line addition after the _convert_to_tree call at line ~718.
- **Constraint:** Must not change behavior for German/Latin documents where filename and content agree. Must not re-run ScriptContext computation if md_content is None (non-PDF paths). Must preserve existing expected_script value when content inference returns None.

**Target B: Add content-text enrichment to ScriptContext.from_document factory**

- **File:** `src/pageindex_mcp/script.py`
- **Lines:** 896-930
- **How:** Extend the from_document classmethod to accept optional raw_text parameter (already exists) and ensure it actually influences dominant_script when filename inference returns 'Latn' or None but content is Arabic. Currently the method falls through filename -> text inference correctly, but the text inference path (_infer_script) may return None for short Arabic samples. Lower the Arabic detection floor in _infer_script or add a secondary AR_CHAR_RE ratio check in from_document when raw_text has >= 15% Arabic characters. Delta: ~8 lines changed in from_document.
- **Constraint:** Must not change dominant_script for documents where both filename and content clearly indicate Latin. Must preserve had_presentation_forms detection which must run on raw pre-NFKC text.

**Target C: Add script-aware flat-prefer threshold for Arabic documents**

- **File:** `src/pageindex_mcp/client/recovery.py`
- **Lines:** 554-587
- **How:** In _recover_flat_prefer, after computing _flat_char_count and _tree_char_count, check expected_script. When expected_script == 'Arab', use a lower multiplier (e.g. 1.5x instead of 3.0x) via a new env var ARABIC_FLAT_PREFER_MULTIPLIER (default 1.5). This captures the marsoom-13 case where heading injection produces ok=True tree with 1225 chars but flat would yield 5972 chars (ratio 4.87x). The method needs the expected_script parameter added to its signature. Delta: +8 lines, signature change from (self, state, filename, ext) to (self, state, filename, ext, expected_script).
- **Constraint:** Must not change behavior for Latin documents (multiplier stays 3.0x for non-Arab scripts). Must not break callers of _recover_flat_prefer -- the call site at indexer.py:1223 must be updated to pass expected_script.

**Target D: Update _recover_flat_prefer call site to pass expected_script**

- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 1223
- **How:** Change `await self._recover_flat_prefer(state, filename, ext)` to `await self._recover_flat_prefer(state, filename, ext, expected_script)`. One-line change.
- **Constraint:** Must match the new signature of _recover_flat_prefer.

**Target E: Make ensure_tessdata raise TessdataUnavailableError for non-Latin languages even when TESSDATA_PREFIX is unset**

- **File:** `src/pageindex_mcp/converters/ocr_langs.py`
- **Lines:** 86-130
- **How:** Currently when TESSDATA_PREFIX is empty (line 102-104), ALL languages are assumed present and appended to available[]. This silently passes 'ara' through even when no Arabic tessdata exists on the system, producing garbage Latin-mode OCR on Arabic pages. Add a check: when TESSDATA_PREFIX is empty AND the language is non-Latin, verify via shutil.which('tesseract') + subprocess call that the traineddata file actually exists in tesseract's default datadir. If verification fails and download is disabled, raise TessdataUnavailableError. If verification is impractical, at minimum log a warning and increment TESSDATA_LATIN_FALLBACK_TOTAL. Delta: ~15 lines.
- **Constraint:** Must not break deployments where TESSDATA_PREFIX is intentionally unset and system tesseract has Arabic tessdata. Must not add a subprocess call on every invocation -- cache the check result per language per process lifetime.

**Target F: Stabilize table_is_rtl decision across continuation merges by computing it once for the anchor**

- **File:** `src/pageindex_mcp/helpers/table_stitch.py`
- **Lines:** 39-67
- **How:** In _merge_continuation_table, table_is_rtl(anchor) is called on line 47. When merging a chain of continuations in stitch_continuation_tables (lines 70-90), the anchor mutates after each merge (line 86: anchor = _merge_continuation_table(anchor, blocks[j])). The merged anchor has different char ratios, so subsequent table_is_rtl calls on the evolved anchor can flip RTL/LTR mid-chain. Fix: compute rtl_decision once on the ORIGINAL anchor before the merge loop in stitch_continuation_tables, pass it to _merge_continuation_table as a parameter instead of re-computing inside. Delta: +3 lines in stitch_continuation_tables (compute once, pass down), +1 parameter in _merge_continuation_table signature, -1 line (remove table_is_rtl call inside _merge_continuation_table).
- **Constraint:** Must not change merge semantics for Latin-script tables where table_is_rtl always returns False. Must preserve existing test assertions for table stitching.

**Target G: Pass pre-computed rtl flag into the merge loop**

- **File:** `src/pageindex_mcp/helpers/table_stitch.py`
- **Lines:** 70-90
- **How:** Before the while loop at line 83, add `is_rtl = table_is_rtl(block)`. Pass is_rtl to _merge_continuation_table. In _merge_continuation_table signature, replace the internal table_is_rtl(anchor) call with the passed-in is_rtl parameter.
- **Constraint:** stitch_continuation_tables is called from route_and_extract_flat (flat.py:157) and from prepare_tree (tree_split.py). Both call sites must continue to work without changes.

**Target H: Add content-density guard to _inject_arabic_structural_headings to prevent content-poor heading injection**

- **File:** `src/pageindex_mcp/converters/headings.py`
- **Lines:** 95-155
- **How:** After heading injection, count the number of injected headings vs total content lines. If the injected headings constitute more than 30% of all non-empty lines AND total content (excluding headings) is less than 2000 chars, revert the injection -- the document is too thin to benefit from forced hierarchy and should fall through to flat extraction. Add a Prometheus counter (ARABIC_HEADING_INJECTION_REVERTED) for observability. Delta: ~15 lines added at end of function before return.
- **Constraint:** Must not revert injection for documents with substantial content (e.g. full Arabic legal codes with many articles). The 2000-char threshold should be configurable via env var ARABIC_HEADING_MIN_CONTENT_CHARS.

#### Test Requirements

- **`tests/test_arabic_rtl_pipeline.py`** (regression): Script-aware flat-prefer guard: Arabic document with heading-injection-inflated tree (1225 chars) and flat extraction yielding 5972 chars triggers flat-prefer with 1.5x Arab multiplier but would not trigger with default 3.0x Latin multiplier. Verify state.route becomes Route.FLAT for Arab script.
- **`tests/test_arabic_rtl_pipeline.py`** (contract): Content-enriched ScriptContext: English-named PDF file with Arabic content body produces ScriptContext.dominant_script='Arab' after post-conversion enrichment, not None or 'Latn' from filename-only inference.
- **`tests/test_arabic_rtl_pipeline.py`** (contract): ensure_tessdata with empty TESSDATA_PREFIX and non-Latin language 'ara': must not silently assume Arabic tessdata exists; must either verify or warn (not silently pass through).
- **`tests/test_arabic_rtl_pipeline.py`** (regression): table_is_rtl stability: when stitch_continuation_tables merges 3 continuation tables, the RTL decision computed on the original anchor is used for all merges, not recomputed on the evolving merged result.
- **`tests/test_arabic_rtl_pipeline.py`** (contract): Arabic heading injection revert guard: document with 5 injected headings but only 800 chars of content has injection reverted; document with 5 injected headings and 5000 chars of content keeps injection.
- **`tests/test_arabic_rtl_pipeline.py`** (wiring): _recover_flat_prefer signature accepts expected_script parameter and passes it through correctly for both Arab and Latn scripts.
- **`tests/test_arabic_rtl_pipeline.py`** (integration): End-to-end: Arabic PDF with English filename processes through content-enriched ScriptContext, uses Arab flat-prefer multiplier, and produces more content via flat path than heading-injection tree path.

#### Wiring Checks

| Symbol | Must Be In | Check Type |
|---|---|---|
| `ARABIC_FLAT_PREFER_MULTIPLIER` | `src/pageindex_mcp/client/recovery.py` | import |
| `_recover_flat_prefer` | `src/pageindex_mcp/client/indexer.py` | call |
| `ARABIC_HEADING_INJECTION_REVERTED` | `src/pageindex_mcp/converters/headings.py` | import |
| `ARABIC_HEADING_MIN_CONTENT_CHARS` | `src/pageindex_mcp/converters/headings.py` | import |
| `table_is_rtl` | `src/pageindex_mcp/helpers/table_stitch.py` | call |
| `ScriptContext.from_document` | `src/pageindex_mcp/client/indexer.py` | call |

#### Corpus Validation

**Affected documents:** marsoom-13 (Arabic legal decree), MOU MOHRE & Nafis (Arabic MOU), SLA (Arabic service level agreement), warid-597 (Arabic telecom doc), qerar-106 (Arabic administrative decision), Federal Decree-Law No. (47) of 2021 - Copy.pdf (Arabic law), al-qarar (Arabic decision document)

**Expected verdict direction:** improve

**Spot-check count:** 7

---

### 4.3. Registry Dual-Write Consistency

| Field | Value |
|---|---|
| **Priority** | 3 |
| **Severity** | high |
| **Wave** | 1 |
| **Bug count** | 8 |
| **Score** | 28.8 |
| **Complexity** | medium |
| **Depends on** | None (Note: job.py:341-343 control-flow interaction with Worker-Child Process Boundary zone is out of scope; re-verify if that zone is later fixed) |

#### Mechanism to Eliminate

Two independent stores (MinIO sidecar .meta.json + Postgres registry) are written via separate code paths in _upsert_registry_row (registry_mirror.py:55-158) with no transactional guarantee. A mode flag (registry_verdict_authority) simultaneously controls write order, write-barrier behavior, and verdict-retry-queue drain eligibility, coupling latency tuning with safety topology. In the 'postgres' path, verdict fields are written twice per call (upsert_verdict then folded into upsert_doc). The erasure cascade (delete_doc, documents.py:141-305) silently skips Postgres deletion when registry_enabled/postgres_dsn is missing without adding an errors[] entry (line 264-284). The verdict-retry-queue drain in reconcile.py:145 is gated on registry_verdict_authority=='postgres', so retries enqueued during a transient Postgres outage are never drained under the default 'minio' mode.

#### Strategy

Collapse the registry_verdict_authority mode flag by making Postgres the sole verdict-authority path unconditionally (eliminating the branching dual-write topology). This reduces _upsert_registry_row from two branching code paths to a single linear sequence. Fold upsert_verdict into upsert_doc as a single CAS-guarded SQL statement to eliminate the double verdict write. Remove the mode guard on verdict-retry-queue drain. Add errors[] entry for the silent registry skip in delete_doc. Remove the conditional write-visibility barrier from sidecar writes (sidecar becomes archival-only).

#### Code Targets

**Target A: Collapse the two branching paths (minio vs postgres) in _upsert_registry_row into a single linear path: (1) upsert to Postgres via combined CAS SQL, (2) best-effort sidecar backfill to MinIO. Remove the if/else branch on registry_verdict_authority at lines 91-143.**

- **File:** `src/pageindex_mcp/worker/registry_mirror.py`
- **Lines:** 55-158
- **How:** Replace lines 91-143 with a single linear sequence: call upsert_doc (with verdict_fields merged) to Postgres, then best-effort save_doc_meta to MinIO. Remove the upsert_verdict call since verdict columns are folded into upsert_doc. Keep the outer try/except at line 150 and metrics at lines 145-158 unchanged. Retain the pool-not-ready guard at lines 82-89 but change its verdict retry enqueue to be unconditional (remove the registry_verdict_authority=='postgres' check at line 87).
- **Constraint:** Must not break preprocess_client.py:168-171 which calls _upsert_registry_row(doc_id, content_class) WITHOUT verdict_fields. The function signature (doc_id, content_class, verdict_fields=None) must remain backward-compatible.

**Target B: Remove the registry_verdict_authority field from Settings and the _VALID_VERDICT_AUTHORITY validation block.**

- **File:** `src/pageindex_mcp/config.py`
- **Lines:** 182, 280, 289-296
- **How:** Delete the registry_verdict_authority field at line 182, its assignment at line 280, and the validation block at lines 289-296. The _VALID_VERDICT_AUTHORITY tuple and the if-block can be fully removed.
- **Constraint:** Must coordinate with all 4 files that read settings.registry_verdict_authority (registry_mirror.py:87+91, verdict.py:232, reconcile.py:145). All must be updated in the same commit.

**Target C: Remove the conditional write-visibility barrier in save_doc_meta that checks registry_verdict_authority. Make the sidecar always skip the barrier (sidecar is now archival; reads go through Postgres).**

- **File:** `src/pageindex_mcp/storage/verdict.py`
- **Lines:** 232-233
- **How:** At line 232, remove the if-condition 'if settings.registry_verdict_authority != "postgres":' and its guarded _confirm_write_visible call at line 233. The sidecar write (mc.put_object at lines 221-227) remains; only the read-after-write barrier is removed.
- **Constraint:** Must not remove the write-visibility barrier from save_doc (documents.py:68) or save_flat_doc (documents.py:127) which protect the primary processed artifacts -- only the sidecar barrier in save_doc_meta is removed.

**Target D: Remove the mode guard on _drain_verdict_retry_queue so retries are always drained regardless of former mode flag.**

- **File:** `src/pageindex_mcp/registry_backfill/reconcile.py`
- **Lines:** 145-146
- **How:** At line 145, remove the 'if settings.registry_verdict_authority == "postgres":' condition so _drain_verdict_retry_queue(redis_client) is called unconditionally on every reconcile tick. This ensures verdict retries enqueued during any Postgres outage are always healed.
- **Constraint:** _drain_verdict_retry_queue must remain best-effort (it already is -- its outer try/except never raises). The reconcile flow after line 148 must not be affected.

**Target E: Add an errors[] entry when the Postgres registry delete step is silently skipped because registry_enabled or postgres_dsn is missing. Also add an entry when pool is not ready (line 284).**

- **File:** `src/pageindex_mcp/storage/documents.py`
- **Lines:** 264, 284
- **How:** At line 264, when the condition 'settings.registry_enabled and settings.postgres_dsn' is False, append to errors: errors.append('registry: skipped (registry_enabled=False or postgres_dsn missing)'). At line 284, when pool is None, add: errors.append('registry: pool not ready, skipped Postgres row deletion'). Both ensure HR2 cascade failures are observable.
- **Constraint:** delete_doc must remain non-raising (Property 4). The new entries go into errors[] which is already returned to the caller. Do not change the return type or add raises.

**Target F: Fold upsert_verdict's CAS-guarded verdict columns into upsert_doc's _UPSERT_SQL so both descriptor and verdict columns are written in a single statement with RETURNING.**

- **File:** `src/pageindex_mcp/registry/queries.py`
- **Lines:** 19-122, 129-201
- **How:** Merge _UPSERT_VERDICT_SQL's RETURNING clause into _UPSERT_SQL, add RETURNING doc_id, verdict, pipeline_version, permanent_marginal, verdict_computed_at. Change upsert_doc to use pool.fetchrow instead of pool.execute and return the winning row dict (or None). The existing temporal CAS guards in _UPSERT_SQL (lines 46-80) already protect verdict columns with the same logic as _UPSERT_VERDICT_SQL. After the merge, upsert_verdict can be deprecated or removed.
- **Constraint:** The _UPSERT_SQL CAS guards on verdict_computed_at (lines 62-80) must be preserved exactly -- they prevent stale-verdict regression. The upsert_verdict function should be kept as a deprecated thin wrapper delegating to upsert_doc for one release cycle to avoid breaking reconcile.py:_drain_verdict_retry_queue line 68 which still calls it.

#### Test Requirements

- **`tests/test_registry_mirror.py`** (contract): _upsert_registry_row single linear path: (1) when Postgres pool is available, verify upsert_doc is called once (not upsert_verdict + upsert_doc), verdict_fields are merged into the fields dict, and save_doc_meta is called for sidecar backfill. (2) When pool is unavailable, verify verdict_fields are enqueued to Redis verdict-retry queue unconditionally (no mode guard). (3) Verify metrics (REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP, REGISTRY_WRITE_FAILURES_TOTAL) fire on success/failure.
- **`tests/test_registry_mirror.py`** (regression): _upsert_registry_row backward compat: verify the function works correctly when called with verdict_fields=None (the preprocess_client.py code path), falling back to read_registry_fields for all columns.
- **`tests/test_storage.py`** (contract): delete_doc errors[] observable for registry skip: (1) when registry_enabled=False, verify errors[] contains a registry-skipped entry. (2) When registry pool is None, verify errors[] contains a pool-not-ready entry. (3) When registry delete succeeds, verify no spurious errors[] entry.
- **`tests/test_reconcile_incremental.py`** (regression): _drain_verdict_retry_queue runs unconditionally: verify that reconcile_registry_drift calls _drain_verdict_retry_queue regardless of any mode configuration. Previously it was gated on registry_verdict_authority=='postgres'.
- **`tests/test_registry.py`** (contract): upsert_doc RETURNING: after folding verdict into upsert_doc, verify (1) upsert_doc returns the winning row dict with verdict columns, (2) CAS temporal guard preserves existing verdict when incoming verdict_computed_at is older, (3) CAS temporal guard allows write when incoming is newer or existing is NULL.
- **`tests/test_storage.py`** (regression): save_doc_meta no longer calls _confirm_write_visible: verify that after the change, no write-barrier stat_object calls are made for sidecar writes. The barrier must still be called for save_doc and save_flat_doc.
- **`tests/test_worker.py`** (wiring): process_document_job calls _upsert_registry_row after DONE status is set: verify the existing ordering contract -- job status is set to DONE before registry write, and a registry write failure does not change job status.

#### Wiring Checks

| Symbol | Must Be In | Check Type |
|---|---|---|
| `upsert_doc` | `src/pageindex_mcp/worker/registry_mirror.py` | call |
| `upsert_verdict` (deprecated wrapper) | `src/pageindex_mcp/registry_backfill/reconcile.py` | call |
| `_drain_verdict_retry_queue` | `src/pageindex_mcp/registry_backfill/reconcile.py` | call |
| `_enqueue_verdict_retry` | `src/pageindex_mcp/worker/registry_mirror.py` | call |
| `save_doc_meta` | `src/pageindex_mcp/worker/registry_mirror.py`, `src/pageindex_mcp/registry_backfill/reconcile.py`, `src/pageindex_mcp/storage/documents.py` | call |
| `read_registry_fields` | `src/pageindex_mcp/worker/registry_mirror.py` | call |

#### Corpus Validation

**Affected documents:** all documents in corpus -- the write path changes for every ingestion

**Expected verdict direction:** stable

**Spot-check count:** 10

---

### 4.4. Garble Detection Fragmentation

| Field | Value |
|---|---|
| **Priority** | 4 |
| **Severity** | critical |
| **Wave** | 2 |
| **Bug count** | 16 |
| **Score** | 24.96 |
| **Complexity** | large |
| **Depends on** | Tree/Flat Verdict Split |

#### Mechanism to Eliminate

Multiple independently-maintained garble detection code paths with different normalization (RAW_MARKDOWN vs TREE_TEXT), different Unicode range heuristics, different call-site wiring, and broken ScriptContext threading. The legacy `check_garble` shim (garble.py:597-636) rebuilds GarbleConfig from os.environ at every call, bypassing the frozen pipeline_config. `_garble_ratio` is byte-identical in both tree_validation.py:167-185 and garble.py:756-772 (same for `ocr_noise_ratio` at tree_validation.py:149-157 / garble.py:738-746, and `hash_pipe_ratio` at tree_validation.py:160-164 / garble.py:749-753). ScriptContext is built once at indexer.py:1150 via `ScriptContext.from_document(filename)` but its `had_presentation_forms` field is discarded: four call sites (gates.py:90, pictures.py:283, pictures.py:401, indexer.py:760) construct throwaway ScriptContext objects with `had_presentation_forms=False`, and 10 production `check_garble` calls use the legacy bare `expected_script` parameter that also defaults `had_presentation_forms=False`. This means Arabic Presentation Forms detection (the `presentation_forms` prong in garble_prongs at garble.py:369-370) never fires on production documents despite the prong being correctly implemented.

#### Strategy

Five-step consolidation: (A) Delete the 3 duplicated helper functions from tree_validation.py, redirect to garble.py canonical copies. (B) Delete the `check_garble` backward-compat shim and `_rebuild_garble_config_compat`, migrate all 10 production call sites to `detect_garble` with explicit ScriptContext + GarbleConfig. (C) Thread the computed ScriptContext from `index()` (indexer.py:1150) through `_convert_to_tree`, `_persist_flat_result`, `_persist_tree_result`, and all recovery methods, eliminating per-call-site ScriptContext construction with hardcoded `had_presentation_forms=False`. Modify `_gate_node_garbling` to accept ScriptContext instead of bare `expected_script`. (D) Merge the flat-path inline garble gate (indexer.py:757-809) into GATE_TABLE-driven evaluation via evaluate_gates, keeping VLM-fallback recovery in indexer triggered by gate result. (E) Confirm `_check_bidi_coherence` remains deleted (already removed, only referenced in gates.py:153 comment).

#### Code Targets

**Target A: Delete the 3 duplicated functions: ocr_noise_ratio (149-157), hash_pipe_ratio (160-164), _garble_ratio (167-185). These are byte-identical to garble.py:738-753 and garble.py:756-772.**

- **File:** `src/pageindex_mcp/helpers/tree_validation.py`
- **Lines:** 149-185
- **How:** Delete the function bodies. In TreeSignals.from_tree (line 213 lazy import `from .garble import BULK_PROFILE, check_garble`), replace with `from .garble import BULK_PROFILE, _garble_ratio` and call `_garble_ratio` from garble.py. Update line 236 `gr = _garble_ratio(flat_text, expected_script=_eff_script)` to use the imported version.
- **Constraint:** TreeSignals.from_tree must produce identical garble_ratio values. The effectively_garbled field (line 237 comparison against garble_threshold) must remain unchanged.

**Target B: Delete the backward-compat shim `_rebuild_garble_config_compat` (571-594) and `check_garble` (597-636). Make `detect_garble` the sole public entry point.**

- **File:** `src/pageindex_mcp/helpers/garble.py`
- **Lines:** 571-636
- **How:** Remove both function definitions. Remove `check_garble` from the module's exports. Update `_garble_ratio` (756-772) to call `detect_garble` instead of `check_garble`, threading ScriptContext.from_script_str(expected_script) and BULK_PROFILE-equivalent BlobKind.TREE_TEXT + _garble_config.
- **Constraint:** detect_garble's GarbleReport.__bool__ must remain the drop-in for the prior bool return of check_garble. Tests using patch.dict(os.environ, ...) to override garble thresholds must be migrated to patch _garble_config or use GarbleConfig.from_config.

**Target C: Remove re-exports of deleted functions: `_garble_ratio` (line 93 -- the garble.py copy stays but is re-exported for use by tree_validation), `check_garble` (line 97), `_rebuild_garble_config_compat` (not in __all__ but imported). Update __all__ list accordingly.**

- **File:** `src/pageindex_mcp/helpers/__init__.py`
- **Lines:** 93, 97, 270
- **How:** Remove `check_garble` from the import block and from __all__. Keep `_garble_ratio` re-export from garble.py since tree_validation.py will now import it. Remove `_rebuild_garble_config_compat` import.
- **Constraint:** Any test importing check_garble from helpers must be updated to use detect_garble. No production code outside helpers/ should break.

**Target D: Migrate 3 check_garble calls in _execute_ocr_retry to detect_garble with ScriptContext. Currently at lines 222, 227, 235, all pass bare expected_script + BULK_PROFILE.**

- **File:** `src/pageindex_mcp/client/recovery.py`
- **Lines:** 222-238
- **How:** Accept `script_context: ScriptContext` parameter in _execute_ocr_retry (or the containing method). Replace `check_garble(_pre_text, expected_script=expected_script, profile=BULK_PROFILE)` with `detect_garble(_pre_text, script_context=script_context, config=_garble_config, blob_kind=BlobKind.TREE_TEXT)`. Same pattern for all 3 call sites. Import detect_garble and _garble_config from helpers.garble; remove check_garble import at line 37.
- **Constraint:** OCR retry win-condition logic must remain identical: pre garbled AND post not-garbled means retry wins. The _repeating_token_density comparison at line 250 must stay unchanged.

**Target E: Migrate 1 check_garble call in _attempt_tesseract_raster_recovery to detect_garble. Currently passes bare expected_script + profile (line 131).**

- **File:** `src/pageindex_mcp/client/images.py`
- **Lines:** 131
- **How:** Accept `script_context: ScriptContext` parameter. Replace `check_garble(ocr_text, expected_script=expected_script, profile=profile)` with `detect_garble(ocr_text, script_context=script_context, config=_garble_config, blob_kind=BlobKind.RAW_MARKDOWN if profile.normalize_markdown else BlobKind.TREE_TEXT)`. Update import at line 23.
- **Constraint:** Recovery behavior must remain identical: garbled OCR text returns None, clean OCR text returns the text.

**Target F: Migrate 1 check_garble call in apply_promotions (image_enrichment_promoted guard) to detect_garble. Currently passes bare expected_script + BULK_PROFILE.**

- **File:** `src/pageindex_mcp/helpers/verdict.py`
- **Lines:** 301
- **How:** Accept `script_context: ScriptContext | None` parameter in apply_promotions (or resolve from existing expected_script parameter). Replace `check_garble(_promoted_text, expected_script=expected_script, profile=BULK_PROFILE)` with `detect_garble(_promoted_text, script_context=_ctx, config=_garble_config, blob_kind=BlobKind.TREE_TEXT)` where _ctx is built from the expected_script param via ScriptContext.from_script_str if a full ScriptContext is not threaded. Update import at line 11.
- **Constraint:** image_enrichment_promoted verdict must fire under identical conditions. Zone 1 dependency: evaluate_gates (lines 125-236) is modified by Zone 1; this change must land AFTER Zone 1's evaluate_gates refactor.

**Target G: Update TreeSignals.from_tree to use detect_garble instead of check_garble, threading ScriptContext properly including had_presentation_forms.**

- **File:** `src/pageindex_mcp/helpers/tree_validation.py`
- **Lines:** 207-254
- **How:** Replace the lazy import `from .garble import BULK_PROFILE, check_garble` (line 213) with `from .garble import detect_garble, _garble_config, _garble_ratio` and `from ..script import BlobKind`. Replace lines 229-233 `garbled = bool(structure) and check_garble(flat_text, expected_script=_eff_script, profile=BULK_PROFILE, had_presentation_forms=_had_pf)` with `garbled = bool(structure) and bool(detect_garble(flat_text, script_context=expected_script if isinstance(expected_script, ScriptContext) else ScriptContext.from_script_str(_eff_script), config=_garble_config, blob_kind=BlobKind.TREE_TEXT))`. When ScriptContext is passed in (the normal path from indexer), had_presentation_forms flows through correctly.
- **Constraint:** TreeSignals.garbled and effectively_garbled must remain semantically identical. The from_tree method already accepts ScriptContext at line 210; must preserve backward compat for bare str|None callers.

**Target H: Thread the computed script_context (line 1150) through _convert_to_tree, _persist_flat_result, _persist_tree_result, and recovery methods instead of just extracting expected_script. Eliminate the ad-hoc ScriptContext construction at line 758-762.**

- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 1145-1151, 757-809
- **How:** Add `script_context: ScriptContext` parameter to _convert_to_tree, _persist_flat_result, _persist_tree_result signatures. Pass the script_context built at line 1150 to these methods. In _persist_flat_result (line 758-762), replace the throwaway ScriptContext construction with the threaded parameter. Replace check_garble at line 790 (VLM fallback) with detect_garble using the threaded script_context. Also thread through recovery dispatch at line 1209 via the state or as an explicit parameter.
- **Constraint:** The ScriptContext.from_document(filename) call at line 1150 must remain the single construction point. raw_text is not available at that point (PDF text comes from fitz probe); the from_document factory handles empty raw_text. The VLM-fallback recovery block (lines 780-808) must stay in indexer (not move to gates) since it involves async I/O.

**Target I: Update _gate_node_garbling to accept ScriptContext instead of constructing a throwaway one with had_presentation_forms=False (line 90).**

- **File:** `src/pageindex_mcp/helpers/gates.py`
- **Lines:** 70-104
- **How:** Change the function signature to accept `script_context: ScriptContext | None = None` as an additional parameter. When provided, pass it directly to _garble_check_nodes instead of constructing a new ScriptContext at line 88-92. Update the _GateFn type alias (line 250-253) and the GATE_TABLE dispatch accordingly, or pass script_context through the gate evaluation loop in validate_tree.
- **Constraint:** The _GateFn signature change affects ALL gate functions (lines 37-247). Either add script_context as an optional kwarg to _GateFn or thread it through validate_tree's gate loop. The existing validate_tree already accepts ScriptContext at line 264; pass it through to gate_fn calls.

**Target J: Thread ScriptContext from caller instead of constructing throwaway ones with had_presentation_forms=False at lines 283 and 401.**

- **File:** `src/pageindex_mcp/converters/pictures.py`
- **Lines:** 280-286, 398-404
- **How:** Add `script_context: ScriptContext | None = None` parameter to _text_layer_has_content (around line 265) and _document_level_text_fallback (around line 370). When provided, use it directly; when None, fall back to current behavior for backward compat. Update callers (_recover_picture_text and _document_level_text_fallback's caller chain) to pass the document-level ScriptContext.
- **Constraint:** These functions are called from within the converter pipeline where the full ScriptContext may not be available (pictures.py operates at the page level during extraction). The fallback to ScriptContext construction with had_presentation_forms=False is acceptable for non-Arabic documents but should be documented as a known limitation.

#### Test Requirements

- **`tests/test_garble_detection.py`** (regression): After check_garble deletion: all existing tests that call check_garble must be migrated to detect_garble with explicit ScriptContext + GarbleConfig. Verify GarbleReport.__bool__ preserves backward compat (TestDetectGarbleWard597 class, lines 24-29). Verify GarbleConfig.from_config produces identical thresholds to the old env-var-based _rebuild_garble_config_compat.
- **`tests/test_garble_detection.py`** (contract): New test: ScriptContext threading exhaustiveness -- assert that detect_garble receives had_presentation_forms=True when ScriptContext.from_document is called with raw Arabic text containing Presentation Forms (U+FB50-FEFF range). Test that the presentation_forms prong fires when had_presentation_forms=True and does NOT fire when False.
- **`tests/test_garble_detection.py`** (wiring): New test: TreeSignals.from_tree called with ScriptContext (not bare str) preserves had_presentation_forms through to the garble evaluation. Verify garbled=True for Arabic text with presentation forms when ScriptContext.had_presentation_forms=True.
- **`tests/test_helpers.py`** (regression): After _garble_ratio deduplication: verify that importing _garble_ratio from helpers (which re-exports from garble.py) produces identical results to the deleted tree_validation.py copy. Test with known garbled and clean text windows.
- **`tests/test_rfc_garble_gate.py`** (wiring): Verify _gate_node_garbling threads ScriptContext (including had_presentation_forms) from validate_tree down to _garble_check_nodes. Assert that node-level garble detection sees the document-level script_context. TestExpectedScriptThreading (lines 349-363) must pass with detect_garble instead of check_garble.
- **`tests/test_garble_detection.py`** (exhaustiveness): Exhaustiveness assertion: every production call site of detect_garble passes a ScriptContext (not None) and a GarbleConfig (not None). Static analysis test using AST parsing of all files under src/pageindex_mcp/ to verify no bare expected_script parameter remains.
- **`tests/test_verdict.py`** (regression): Verify apply_promotions image_enrichment_promoted guard uses detect_garble and respects ScriptContext. The test_ocr_noise_ratio_replacement test (line 116) must still pass after importing from the canonical garble.py location.
- **`tests/test_garble_detection.py`** (integration): New integration test: end-to-end garble detection on a flat-path document where ScriptContext is built at the index() entry point and threaded through _persist_flat_result. Verify the inline garble gate (indexer.py:757-809) produces the same verdict when driven through evaluate_gates after Step D.

#### Wiring Checks

| Symbol | Must Be In | Check Type |
|---|---|---|
| `detect_garble` | `src/pageindex_mcp/client/recovery.py`, `src/pageindex_mcp/client/images.py`, `src/pageindex_mcp/helpers/verdict.py`, `src/pageindex_mcp/helpers/tree_validation.py`, `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/converters/pictures.py`, `src/pageindex_mcp/helpers/garble.py` | import |
| `ScriptContext` | `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/client/recovery.py`, `src/pageindex_mcp/client/images.py`, `src/pageindex_mcp/helpers/gates.py`, `src/pageindex_mcp/helpers/tree_validation.py`, `src/pageindex_mcp/helpers/verdict.py`, `src/pageindex_mcp/converters/pictures.py` | import |
| `check_garble` | (none) | import |
| `_rebuild_garble_config_compat` | (none) | call |
| `GarbleConfig` | `src/pageindex_mcp/client/recovery.py`, `src/pageindex_mcp/client/images.py`, `src/pageindex_mcp/helpers/verdict.py` | import |
| `BlobKind` | `src/pageindex_mcp/client/recovery.py`, `src/pageindex_mcp/client/images.py`, `src/pageindex_mcp/helpers/tree_validation.py`, `src/pageindex_mcp/helpers/verdict.py` | import |
| `_garble_config` | `src/pageindex_mcp/client/recovery.py`, `src/pageindex_mcp/client/images.py`, `src/pageindex_mcp/helpers/verdict.py`, `src/pageindex_mcp/helpers/gates.py` | import |

#### Corpus Validation

**Affected documents:** Arabic T&C PDFs with Presentation Forms (U+FB50-FEFF) -- currently get had_presentation_forms=False at all 4 hardcoded sites; after fix, presentation_forms prong activates correctly, Latin-gibberish CMap mojibake PDFs -- ScriptContext threading ensures expected_script is never None when filename contains Arabic/RTL hints, Scanned PDFs with thin text layers routed through flat path -- garble gate moves from inline check to GATE_TABLE-driven evaluation, Image-enriched flat documents checked by apply_promotions image_enrichment_promoted guard

**Expected verdict direction:** improve

**Spot-check count:** 8

---

### 4.5. Converter-Gate-Route Ordering Chain

| Field | Value |
|---|---|
| **Priority** | 5 |
| **Severity** | critical |
| **Wave** | 2 |
| **Bug count** | 14 |
| **Score** | 21.84 |
| **Complexity** | medium |
| **Depends on** | Tree/Flat Verdict Split |

#### Mechanism to Eliminate

Three-part chain (converter selection, gate evaluation, route decision) with no shared invariant. _reconvert_and_revalidate (indexer.py:326-353) and _recover_rtl_repair (recovery.py:434-440) update state.gate_result/ok/reason via validate_tree but do NOT update state.first_defect or state.route, creating a stale-routing window. The recovery loop (indexer.py:1210-1218) partially compensates but only when not state.ok and state.route == _pre_route -- if recovery converges (ok=True), first_defect and route remain stale, producing the workaround match arms at lines 1247-1260. OCR escalation is gated on string-matching 'docling' in conv_name (indexer.py:461,492,505,1063) instead of a typed capability flag, so pymupdf4llm-as-primary disarms OCR escalation silently. Recovery OCR path hardcodes pdf_to_markdown_docling directly (recovery.py:158) and state.used_converter = 'docling' (recovery.py:167), permanently coupling OCR escalation to one converter regardless of configuration.

#### Strategy

Extract a finalize_gate_and_route() function in types.py as the single writer of state.gate_result/ok/reason/first_defect/route from a validate_tree result, eliminating the 3 incomplete-update sites and the stale-routing workaround match arms. Add a supports_ocr: bool field to the converter chain tuples returned by pdf_markdown_converters(), replacing 4 'docling' in conv_name string-match gates with a typed boolean check. Move _defect_from_reason_str into types.py next to decide_route so all routing logic is co-located.

#### Code Targets

**Target A: Add finalize_gate_and_route() function next to decide_route**

- **File:** `src/pageindex_mcp/helpers/types.py`
- **Lines:** 285-320
- **How:** New function that takes (state: ExtractionState, vt_raw: TreeGateResult | tuple, flat_routing_enabled: bool) and atomically sets state.gate_result, state.ok, state.reason, state.first_defect, state.route in one call. It encapsulates the 6-line pattern currently repeated at indexer.py:705-717, but missing at indexer.py:346-353 and recovery.py:434-440. Import _defect_from_reason_str from verdict.py (moved in Step F) and decide_route (already here). Return None (mutates state in place). ~15 lines added.
- **Constraint:** Must accept both TreeGateResult and legacy (ok, reason) tuples from validate_tree via the __iter__ protocol. Must NOT change the semantics of decide_route itself.

**Target B: Replace 6-line gate_result/ok/reason/first_defect/route derivation in _convert_to_tree with single finalize_gate_and_route() call**

- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 699-717
- **How:** Replace lines 705-717 (state.gate_result = ...; state.ok, state.reason = ...; state.first_defect = ...; state.route = ...) with: finalize_gate_and_route(state, _vt_raw, settings.flat_doc_routing). Keep the all_defects logging at lines 707-713 after the call. Net: -6 lines +2 lines.
- **Constraint:** The prepare_tree call (line 695-698) and validate_tree call (lines 699-704) remain unchanged. The logging block (lines 707-713) stays but reads from state.gate_result which is now populated by finalize_gate_and_route.

**Target C: Replace incomplete update in _reconvert_and_revalidate with finalize_gate_and_route()**

- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 346-353
- **How:** Replace lines 352-353 (state.gate_result = ...; state.ok, state.reason = ...) with: finalize_gate_and_route(state, _vt_raw, settings.flat_doc_routing). This ADDS the previously missing first_defect/route derivation, eliminating the stale-routing window. Net: -2 lines +1 line.
- **Constraint:** The validate_tree call (lines 346-350) remains unchanged. After this change, _reconvert_and_revalidate produces a fully consistent state, so callers (recovery.py:195, recovery.py:516) no longer need compensating logic.

**Target D: Replace incomplete update in _recover_rtl_repair with finalize_gate_and_route()**

- **File:** `src/pageindex_mcp/client/recovery.py`
- **Lines:** 434-440
- **How:** Replace lines 439-440 (state.gate_result = ...; state.ok, state.reason = ...) with: finalize_gate_and_route(state, _vt_raw, settings.flat_doc_routing). This ADDS the previously missing first_defect/route derivation after RTL repair. Net: -2 lines +1 line. Add import of finalize_gate_and_route at top of file.
- **Constraint:** The validate_tree call (lines 434-438) and the stale rtl_decision clearing (lines 430-433) remain unchanged.

**Target E: Remove ad-hoc re-derivation of first_defect/route in recovery loop**

- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 1210-1218
- **How:** Delete the conditional block at lines 1213-1218 (if not state.ok and state.route == _pre_route: ...). With finalize_gate_and_route() now called inside every recovery method (via _reconvert_and_revalidate and _recover_rtl_repair), first_defect/route are always current after recovery -- the compensating re-derivation is dead code. Keep lines 1219 (total_chars) and 1207 (_pre_route capture, still needed for logging). Net: -6 lines.
- **Constraint:** The _pre_route variable capture at line 1207 may be kept for debug logging or removed entirely. The total_chars re-derivation at line 1219 must stay.

**Target F: Remove workaround match arms for stale routes that become dead code**

- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 1247-1260
- **How:** Delete the three workaround match arms: (True, Route.REJECT) | (True, Route.PERSIST_FAIL) at lines 1247-1251, and (True, Route.FLAT) at lines 1253-1260. With finalize_gate_and_route() always running, ok=True always implies route=TREE (because decide_route maps OK/CAP_MARGINAL/RETRY_OCR policies to TREE). These stale-route cases are unreachable. Net: -12 lines.
- **Constraint:** The remaining match arms (True, Route.TREE) at line 1244, (False, Route.FLAT) at line 1262, (False, Route.REJECT) at line 1289, and (False, Route.TREE|PERSIST_FAIL) at line 1299 must remain. Add an assertion or exhaustiveness guard after the match to catch any missed case.

**Target G: Add supports_ocr: bool to converter chain tuples**

- **File:** `src/pageindex_mcp/converters/pipeline.py`
- **Lines:** 572-634
- **How:** Change the return type of pdf_markdown_converters() from list[tuple[str, Callable[...]]] to list[tuple[str, Callable[...], bool]]. Docling entry returns ('docling', pdf_to_markdown_docling, True); pymupdf4llm entry returns ('pymupdf4llm', _pdf_to_markdown_no_pics, False). Update the type alias at line 614 accordingly. Net: +5 lines.
- **Constraint:** Must NOT change the callable signature or behavior. The bool is a static metadata field, not a runtime toggle. All callers that unpack the chain tuples must be updated to handle the 3-tuple.

**Target H: Replace 'docling' in conv_name string-match gates with supports_ocr check**

- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 458-520
- **How:** Update the for loop at line 458 to unpack (conv_name, conv_fn, supports_ocr) from the 3-tuple chain. Replace 3 occurrences of '"docling" in conv_name' (lines 461, 492, 505) with 'supports_ocr'. The state.use_remote check at line 461 stays (remote dispatch is still docling-specific for now, but should be gated on supports_ocr AND use_remote). Net: -3 lines +3 lines (net 0).
- **Constraint:** The force_full_page OCR escalation gate at line 492 must fire for ANY converter that supports_ocr=True, not just docling. The pre_garbled log message at line 505 is informational and can remain.

**Target I: Replace 'docling' in state.used_converter string-match with capability check**

- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 1063
- **How:** Store the supports_ocr flag from the winning converter in state (add supports_ocr: bool = False field to ExtractionState in types.py) and use it at line 1063 instead of string-matching state.used_converter. Alternatively, the extraction_route metadata can check state.use_remote directly since the remote path is unconditionally docling-backed. Net: ~3 lines changed.
- **Constraint:** The meta['extraction_route'] value must remain 'remote' or 'local' for backward compatibility with existing verdict sidecars.

**Target J: Add supports_ocr field to ExtractionState**

- **File:** `src/pageindex_mcp/helpers/types.py`
- **Lines:** 157-191
- **How:** Add 'supports_ocr: bool = False' field to the ExtractionState dataclass, after the existing used_converter field. This field is set from the converter chain's supports_ocr tuple element when a converter succeeds. Net: +1 line.
- **Constraint:** Default must be False (non-PDF paths do not use converter chain). Must not break RecoveryOutcome.apply() since supports_ocr is not recovery-relevant state.

**Target K: Move _defect_from_reason_str to types.py next to finalize_gate_and_route and decide_route**

- **File:** `src/pageindex_mcp/helpers/verdict.py`
- **Lines:** 88-100
- **How:** Cut _defect_from_reason_str from verdict.py and paste it into types.py (after or before decide_route). Update the re-export in helpers/__init__.py to import from types instead of verdict. Update direct imports in indexer.py. Net: 0 lines (pure relocation).
- **Constraint:** The function behavior and signature must not change. All existing callers (indexer.py:55, indexer.py:715, indexer.py:1217, helpers/__init__.py:208) must continue to resolve.

#### Test Requirements

- **`tests/test_finalize_gate_route.py`** (exhaustiveness): finalize_gate_and_route() atomically sets all 5 fields (gate_result, ok, reason, first_defect, route) on ExtractionState for each TreeDefect variant. Verify: (1) TreeGateResult with defect=GARBLING -> first_defect=GARBLING, route=TREE (RETRY_OCR policy); (2) TreeGateResult with defect=NODE_COUNT_LOW -> first_defect=NODE_COUNT_LOW, route=FLAT (RAISE policy + flat_routing_enabled); (3) TreeGateResult with defect=OK -> first_defect=OK, route=TREE; (4) legacy (ok, reason) tuple without TreeGateResult -> _defect_from_reason_str parses reason string correctly; (5) flat_routing_enabled=False changes RAISE policy defects to REJECT instead of FLAT.
- **`tests/test_finalize_gate_route.py`** (regression): After _reconvert_and_revalidate completes, state.first_defect and state.route are consistent with state.gate_result. Regression test: construct ExtractionState with first_defect=GARBLING, route=TREE; call _reconvert_and_revalidate with a tree that has NODE_COUNT_LOW defect; assert state.first_defect is now NODE_COUNT_LOW and state.route is now FLAT (not stale TREE).
- **`tests/test_finalize_gate_route.py`** (regression): After recovery converges (ok=True), state.route is TREE and state.first_defect is OK. Regression test: simulate recovery loop where _recover_rtl_repair makes ok=True; verify state.route=TREE and state.first_defect=OK, NOT stale RTL_REVERSAL/FLAT.
- **`tests/test_finalize_gate_route.py`** (contract): Workaround match arms (True, Route.REJECT), (True, Route.PERSIST_FAIL), (True, Route.FLAT) are unreachable after finalize_gate_and_route is wired. Property test: for every TreeDefect d where decide_route(d, True)==Route.TREE, the corresponding gate_result.ok must be True; conversely ok=True can only produce route=TREE after finalize_gate_and_route.
- **`tests/test_converter_chain_ocr.py`** (contract): pdf_markdown_converters() returns 3-tuples (name, fn, supports_ocr). Verify: (1) docling entry has supports_ocr=True; (2) pymupdf4llm entry has supports_ocr=False; (3) when PDF_CONVERTER=pymupdf4llm, docling is secondary but still supports_ocr=True; (4) chain iteration unpacks without error.
- **`tests/test_converter_chain_ocr.py`** (wiring): OCR escalation gates fire based on supports_ocr flag, not converter name string. Verify: when converter chain returns a converter with supports_ocr=True, force_full_page_ocr is threaded through; when supports_ocr=False, OCR escalation is skipped regardless of converter name.

#### Wiring Checks

| Symbol | Must Be In | Check Type |
|---|---|---|
| `finalize_gate_and_route` | `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/client/recovery.py`, `src/pageindex_mcp/helpers/__init__.py` | call |
| `supports_ocr` | `src/pageindex_mcp/client/indexer.py` | dispatch |
| `_defect_from_reason_str` | `src/pageindex_mcp/helpers/__init__.py`, `src/pageindex_mcp/client/indexer.py` | import |

#### Corpus Validation

**Affected documents:** scanned PDFs currently processed by pymupdf4llm as primary (OCR escalation was silently disarmed), documents where recovery converges but had stale FLAT/REJECT route (now correctly route TREE), flat-routed documents that previously escaped 7 hard-fail gates via FLAT_GATE_SUBSET (Zone 1 dependency)

**Expected verdict direction:** improve

**Spot-check count:** 10

---

### 4.6. ZDR/PII Egress Gap

| Field | Value |
|---|---|
| **Priority** | 6 |
| **Severity** | high |
| **Wave** | 1 |
| **Bug count** | 3 |
| **Score** | 10.8 |
| **Complexity** | medium |
| **Depends on** | None |

#### Mechanism to Eliminate

Per-call-site opt-in ZDR gating: zdr_egress_gate is a voluntary gate that only 2 of ~6 LLM egress sites call. The two highest-volume ingestion paths (_run_md_to_tree, _run_page_index_retrying) send full document text without any ZDR check. The _llm_with_retry fallback path silently reroutes to LLM_FALLBACK_BASE_URL (never validated against the ZDR allowlist) exactly when the primary ZDR-compliant endpoint fails. vlm_extract_markdown rasterizes full PDF pages and sends them via get_openai_client() with no ZDR gate. This is an architectural gap -- no single enforcement choke point -- not an implementation bug in any one call site.

#### Strategy

Extract a mandatory ZDR enforcement layer into the two LLM client construction points (get_openai_client and _llm_with_retry) so every outbound LLM call is gated at the transport layer rather than at each call site. Specifically: (1) Add a require_zdr_compliance() validator that checks both primary and fallback URLs against _is_zdr_allowlisted when pii_corpus=True, called from _llm_with_retry before the fallback path and from get_openai_client at client construction. (2) Add zdr_egress_gate calls to the ungated vlm_extract_markdown and html_to_markdown_with_images paths. (3) Validate LLM_FALLBACK_BASE_URL against ZDR allowlist at server startup alongside the existing OPENAI_BASE_URL check. This converts ZDR from opt-in to opt-out: every new egress site is gated by default because the transport layer refuses non-ZDR endpoints when pii_corpus=True.

#### Code Targets

**Target A: Add require_zdr_compliance(base_url, purpose) function that raises RuntimeError when pii_corpus=True and base_url is not ZDR-allowlisted. Also add validate_fallback_zdr() that checks LLM_FALLBACK_BASE_URL at import time.**

- **File:** `src/pageindex_mcp/config.py`
- **Lines:** 198-204
- **How:** After _is_zdr_allowlisted (line ~203), add a new function require_zdr_compliance(base_url: str | None, purpose: str) -> None that calls _is_zdr_allowlisted and raises RuntimeError with a clear message including the purpose string if pii_corpus=True and the URL fails the check. This function is the single enforcement primitive all egress sites will use.
- **Constraint:** _is_zdr_allowlisted must remain unchanged (used by existing callers). The new function must not break Settings frozen dataclass. Must not change the _ZDR_ALLOW_PATTERNS tuple.

**Target B: Extend the startup ZDR validation in _lifespan_with_scrape to also check LLM_FALLBACK_BASE_URL when pii_corpus=True.**

- **File:** `src/pageindex_mcp/server.py`
- **Lines:** 76-84
- **How:** After the existing _is_zdr_allowlisted(settings.openai_base_url) check at lines 77-84, add a second check: if LLM_FALLBACK_BASE_URL env var is set and non-empty, validate it against _is_zdr_allowlisted. Raise RuntimeError with a clear message if it fails. Import os for the env read or read _LLM_FALLBACK_BASE_URL from client.llm.
- **Constraint:** The existing startup check for openai_base_url must remain intact. Server must still start when LLM_FALLBACK_BASE_URL is unset/empty (that is the default case). Must not break the lifespan context manager flow.

**Target C: Gate the fallback path in _llm_with_retry with require_zdr_compliance before sending document content to the fallback URL.**

- **File:** `src/pageindex_mcp/client/llm.py`
- **Lines:** 110-118
- **How:** At line ~111 (the 'if fallback_base_url:' block), before the call_fn(base_url=fallback_base_url) call, import and call require_zdr_compliance(fallback_base_url, 'LLM fallback retry'). If pii_corpus=True and the fallback URL is not ZDR-compliant, the RuntimeError propagates as a LLMTransientFailure rather than silently sending PII to a non-ZDR endpoint. This is the critical fix for Bug 2.
- **Constraint:** Must not change the retry logic for non-PII corpora (pii_corpus=False). The fallback path must remain functional for operators not using PII mode. _llm_with_retry's call signature must not change (would break all callers in indexer.py).

**Target D: Add zdr_egress_gate check to vlm_extract_markdown before sending rasterized PDF pages to the LLM.**

- **File:** `src/pageindex_mcp/converters/formats.py`
- **Lines:** 376-391
- **How:** At the top of vlm_extract_markdown (after line 382 imports), add: from .pictures import zdr_egress_gate; allowed, api_base = zdr_egress_gate('VLM markdown extraction', doc_id=pdf_path); if not allowed: raise RuntimeError('vlm_extract_markdown blocked by ZDR gate (HR3)'). This gates Bug 3's primary egress path.
- **Constraint:** Must not break the VLM fallback recovery path in recovery.py:511 and indexer.py:789 -- those callers already catch exceptions and fall through gracefully. The RuntimeError is the correct signal (same pattern as the existing 'no pages rasterized' RuntimeError on line 389).

**Target E: Add zdr_egress_gate check to html_to_markdown_with_images._describe before sending image data to the LLM.**

- **File:** `src/pageindex_mcp/converters/formats.py`
- **Lines:** 113-143
- **How:** Inside the _describe inner function (line ~113), before the _call() invocation, add: from .pictures import zdr_egress_gate; allowed, _ = zdr_egress_gate('HTML image description', doc_id=path); if not allowed: return 'image'. This matches the existing fallback behavior (returning 'image' string on failure).
- **Constraint:** Must not change the return type or caller contract. The 'image' fallback string is already the established pattern for failed descriptions (see line 156).

**Target F: Refactor zdr_egress_gate to use the new require_zdr_compliance from config.py internally, consolidating the enforcement primitive.**

- **File:** `src/pageindex_mcp/converters/pictures.py`
- **Lines:** 175-193
- **How:** Replace the inline _is_zdr_allowlisted check in zdr_egress_gate (lines 183-192) with a try/except around require_zdr_compliance. On success return (True, api_base); on RuntimeError return (False, api_base) with the existing log message. This keeps the existing (allowed, api_base) return contract unchanged while using the single enforcement primitive.
- **Constraint:** Return type tuple[bool, str | None] must not change. All existing callers (_add_vlm_descriptions, _generate_flat_doc_description) must continue working identically. The log message format should remain the same.

**Target G: Add ZDR gate to the query-path _llm function to prevent PII in query responses from egressing through non-ZDR endpoints.**

- **File:** `src/pageindex_mcp/helpers/rag.py`
- **Lines:** 31-53
- **How:** At the top of _llm (line ~31), add: from ..config import settings; if settings.pii_corpus: from ..config import require_zdr_compliance; require_zdr_compliance(settings.openai_base_url, 'RAG query'). This ensures even the query path respects HR3 when pii_corpus is set.
- **Constraint:** Must not add latency to the non-PII query path. The import should be conditional (only when pii_corpus=True). Must not change the _llm function signature or return type.

#### Test Requirements

- **`tests/test_zdr_egress.py`** (exhaustiveness): Exhaustive coverage of every LLM egress site under pii_corpus=True with a non-ZDR endpoint. For each of the 6 egress sites (_run_md_to_tree via _llm_with_retry, _run_page_index_retrying via _llm_with_retry, vlm_extract_markdown, html_to_markdown_with_images._describe, _add_vlm_descriptions, _generate_flat_doc_description, _llm in rag.py), verify that: (a) with pii_corpus=True + non-ZDR URL, the call is blocked/returns empty, (b) with pii_corpus=False, the call proceeds normally, (c) with pii_corpus=True + ZDR-allowlisted URL, the call proceeds normally.
- **`tests/test_zdr_egress.py`** (contract): LLM_FALLBACK_BASE_URL validation: verify that _llm_with_retry raises when pii_corpus=True and fallback_base_url is not ZDR-allowlisted, even though the primary URL is allowlisted. Verify the fallback still works when pii_corpus=False.
- **`tests/test_zdr_egress.py`** (contract): Server startup validation: verify _lifespan_with_scrape raises RuntimeError when pii_corpus=True and LLM_FALLBACK_BASE_URL is set to a non-ZDR endpoint, and that it passes when the env var is empty/unset.
- **`tests/test_zdr_egress.py`** (contract): require_zdr_compliance contract: verify it raises RuntimeError with informative message when pii_corpus=True and URL not allowlisted; returns None silently when pii_corpus=False or URL is allowlisted; handles None/empty URL correctly.
- **`tests/test_zdr_egress.py`** (regression): Regression: verify that the two previously-gated sites (_add_vlm_descriptions, _generate_flat_doc_description) still block under pii_corpus=True with non-ZDR URL, confirming the refactor to require_zdr_compliance did not regress existing protection.

#### Wiring Checks

| Symbol | Must Be In | Check Type |
|---|---|---|
| `require_zdr_compliance` | `src/pageindex_mcp/client/llm.py`, `src/pageindex_mcp/converters/pictures.py`, `src/pageindex_mcp/helpers/rag.py` | call |
| `zdr_egress_gate` | `src/pageindex_mcp/converters/formats.py`, `src/pageindex_mcp/converters/pictures.py`, `src/pageindex_mcp/client/indexer.py` | call |

#### Corpus Validation

**Affected documents:** any PII-flagged document ingested with pii_corpus=True

**Expected verdict direction:** stable

**Spot-check count:** 3

---

### 4.7. Duplicated Convergent Logic

| Field | Value |
|---|---|
| **Priority** | 7 |
| **Severity** | medium |
| **Wave** | 3 |
| **Bug count** | 6 |
| **Score** | 6 |
| **Complexity** | medium |
| **Depends on** | Tree/Flat Verdict Split (wave 1, hysteresis ownership) |

#### Mechanism to Eliminate

Multiple independent code paths compute the same derived value (flat-block text rendering, verdict-ledger hysteresis, route_and_extract_flat invocation, row_records collection) with subtly different implementations that converge on the same downstream consumer (verdict sidecar, search index, get_document response). When one copy is updated and others are not, the copies silently disagree. The triplicate _flat_block_* functions each reimplement the table->join(row_records) branch; the hysteresis block is copy-pasted across both persistence methods; route_and_extract_flat runs 2-3 times per ingestion with results discarded; flat_doc_view re-derives row_records on every read.

#### Strategy

Consolidate convergent copies into single-source-of-truth functions, then wire all call sites to use the canonical version. (A) Unify the three _flat_block_* text functions into two with clear contracts: _flat_block_primary_text (document text, no enrichment metadata) stays as the canonical block-level text extractor, _flat_search_text stays as the whole-doc search renderer that intentionally includes OCR/description -- delete dead _flat_block_text entirely. (B) Extract the hysteresis block into a standalone function called by both persistence methods. (C) Cache route_and_extract_flat results on ExtractionState so recovery comparisons reuse the cached result instead of re-invoking the full parse+stitch pipeline. (D) Store pre-aggregated row_records at ingestion time so flat_doc_view reads from storage instead of re-deriving.

#### Code Targets

**Target A: Delete the _flat_block_text function (dead production code -- zero production callers, only test imports)**

- **File:** `src/pageindex_mcp/helpers/flat.py`
- **Lines:** 187-199
- **How:** Remove the function definition at lines 187-199. Update tests that import _flat_block_text to use _flat_block_primary_text (for document-text use cases) or _flat_search_text (for search-index use cases that need OCR/description). The image-block branch (lines 196-198) in _flat_block_text is the only behavioral difference from _flat_block_primary_text -- callers needing image text should use _flat_search_text which already includes it.
- **Constraint:** Tests in test_rfc_pipeline.py:524, test_rfc_storage.py:250, test_rfc_blocks.py:50 import _flat_block_text and must be updated to use _flat_block_primary_text. Verify no production caller regresses.

**Target B: Remove _flat_block_text from helpers re-exports and __all__**

- **File:** `src/pageindex_mcp/helpers/__init__.py`
- **Lines:** 65,258
- **How:** Delete the import line at ~65 and the __all__ entry at ~258 for _flat_block_text.
- **Constraint:** Must be done atomically with the flat.py deletion to avoid ImportError.

**Target C: (REMOVED — owned by Zone 1 Tree/Flat Verdict Split. Zone 1 extracts `apply_verdict_hysteresis` into `helpers/verdict.py` and replaces both inline blocks in indexer.py. This zone's targets E+ assume Zone 1 has already landed.)**

**Target D: (REMOVED — see Target C above.)**

**Target E: Eliminate row_records re-derivation in flat_doc_view by reading pre-aggregated row_records from stored data**

- **File:** `src/pageindex_mcp/helpers/flat.py`
- **Lines:** 227-250
- **How:** The flat_doc_view function currently iterates blocks to collect row_records (lines 235-241) on every read. Instead, check data.get('row_records') first -- if present (set at ingestion time by the persistence path), use it directly. Only fall back to re-derivation if the key is missing (backward compatibility for docs ingested before row_records was pre-aggregated). This requires _persist_flat_result to store row_records at ingestion time (see indexer.py target below).
- **Constraint:** Must remain backward-compatible with flat docs already stored without a top-level row_records key. flat_doc_view's return shape must not change.

**Target F: Pre-aggregate row_records into flat_meta at ingestion time**

- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 895-910
- **How:** In _persist_flat_result, after blocks are finalized (around line 895 where flat_meta is built), compute row_records from blocks (same logic currently in flat_doc_view: iterate blocks with role='table', extend row_records from block.get('row_records', [])). Store as flat_meta['row_records'] so flat_doc_view can read it directly instead of re-deriving.
- **Constraint:** Must not change the flat_meta schema in a way that breaks save_flat_doc. The row_records key is additive -- existing consumption via flat_doc_view is the primary reader.

**Target G: NOTE ONLY (deferred to Zone 7 fix): route_and_extract_flat is re-invoked from _recover_rtl_flat_compare (line 468) and _recover_flat_prefer (line 568) for comparison purposes. These invocations produce different results if markdown was mutated between calls (e.g. by splice_figure_markers). Caching the result on ExtractionState would eliminate redundant invocations, but these recovery methods are in the Zone 7 (Arabic/RTL Pipeline Blindness) dependency chain. This fix target is deferred -- the Zone 7 fix must land first because it restructures the recovery mixin call order and state management.**

- **File:** `src/pageindex_mcp/client/recovery.py`
- **Lines:** 467-468,567-568
- **How:** Deferred to Zone 7 dependency. When Zone 7 lands, add an optional cached_flat_result: tuple[str, list[dict]] | None = None field to ExtractionState. Populate it on first route_and_extract_flat call in _apply_picture_enrichment (images.py:185). Recovery methods that only need comparison (lines 468 and 568) read from the cache instead of re-invoking. Invalidate cache when md_content is mutated (splice_figure_markers, bidi repair, OCR re-extraction).
- **Constraint:** DEFERRED: depends on Zone 7 landing first. The cache invalidation logic must correctly track markdown mutations -- if splice_figure_markers runs between cache population and read, the cached result is stale.

#### Test Requirements

- **`tests/test_verdict_hysteresis.py`** (contract): Test the extracted apply_verdict_hysteresis function: (1) returns original verdict when no prior exists, (2) overrides to higher-priority prior verdict, (3) does NOT override when prior is lower priority, (4) gracefully degrades (returns original) when read_verdict_ledger raises, (5) verdict_reason format includes anchored_by_ledger annotation with original verdict:reason, (6) path_label appears in log output
- **`tests/test_flat_block_text_consolidation.py`** (regression): Test that _flat_block_primary_text returns correct text for all block roles (prose, table, kv, image) and specifically that table blocks join row_records. Verify _flat_search_text produces the same table row_records output as _flat_block_primary_text for table blocks. Verify _flat_search_text additionally includes OCR text and description for image blocks (the only behavioral difference). Regression test: ensure no production code path that previously called _flat_block_text breaks when using _flat_block_primary_text instead.
- **`tests/test_flat_doc_view.py`** (regression): Test that flat_doc_view uses pre-aggregated row_records from data dict when present (new path), falls back to block-iteration derivation when row_records key is absent (backward compat), and produces identical output in both paths for the same input blocks. Test that the row_records pre-aggregation in _persist_flat_result produces the same list as flat_doc_view's re-derivation logic.
- **`tests/test_hysteresis_parity.py`** (contract): Integration test: verify that _persist_flat_result and _persist_tree_result produce identical hysteresis behavior by mocking read_verdict_ledger to return a higher-priority verdict and confirming both paths override identically. This is the key regression test -- the two previously-independent blocks must now produce the same result via the shared function.
- **`tests/test_flat_block_text_dead_code.py`** (exhaustiveness): Exhaustiveness test: verify _flat_block_text is NOT importable from helpers after removal (ImportError on from pageindex_mcp.helpers import _flat_block_text). Verify _flat_block_primary_text and _flat_search_text remain importable.

#### Wiring Checks

| Symbol | Must Be In | Check Type |
|---|---|---|
| `apply_verdict_hysteresis` (prerequisite: Zone 1 landed) | `src/pageindex_mcp/client/indexer.py` | import |
| `_LEDGER_PRIORITY` (prerequisite: Zone 1 landed) | `src/pageindex_mcp/helpers/verdict.py` | symbol_exists |

#### Corpus Validation

**Affected documents:** all flat-routed documents (content_class in flat_table, flat_kv, flat_prose, flat_mixed), all tree-routed documents with prior verdict ledger entries

**Expected verdict direction:** stable

**Spot-check count:** 5

---

## 5. Validation Findings

**Approved:** No — **Overall quality:** needs_work

### Blockers

**Duplicated Convergent Logic**: ~~BLOCKER~~ **RESOLVED.** Both zones extracted the same hysteresis blocks into differently-named/located helpers. **Resolution:** Zone 1 owns the extraction — places sync `apply_verdict_hysteresis` in `helpers/verdict.py`. Zone 7 targets C/D removed; Zone 7's wiring checks updated to reference Zone 1's symbol as a prerequisite.

> **Suggested fix:** Remove the hysteresis extraction (code target C/D and test_verdict_hysteresis.py / test_hysteresis_parity.py requirements) from 'Duplicated Convergent Logic' entirely — Zone 1 already owns it. If the helpers/verdict.py location and sync signature is preferred, put that design in Zone 1 and drop it from Zone 7, and reconcile the two _LEDGER_PRIORITY wiring checks to one location.

**Registry Dual-Write Consistency**: ~~BLOCKER~~ **RESOLVED.** Wiring check required `upsert_doc` imported by `reconcile.py`, but code target kept `upsert_verdict` as deprecated wrapper for backward compat. **Resolution:** Dropped `reconcile.py` from `upsert_doc`'s `must_be_imported_by` in wiring checks table. Added separate wiring check entry for `upsert_verdict` (deprecated wrapper) called by `reconcile.py`. Consistent with one-release-cycle deprecation strategy.

### Major Issues

**Registry Dual-Write Consistency**: ~~Major~~ **RESOLVED.** depends_on referenced non-existent 'Zone 5: Worker-Child Process Boundary'. **Resolution:** Removed the phantom dependency. The job.py:341-343 control-flow interaction is out of scope for this zone and documented as a re-verification item if the Worker-Child zone is later fixed.

**Duplicated Convergent Logic**: Same-wave dependency violation: depends_on ['Arabic/RTL Pipeline Blindness'] but both zones are in wave 3. The wave rules require a dependency to land in an earlier wave; the rationale's 'sequence Z7 before Z8 within the wave' is intra-wave ordering that the wave machinery does not guarantee. The deferred route_and_extract_flat caching target explicitly says 'the Zone 7 fix must land first'.

> **Suggested fix:** Move 'Duplicated Convergent Logic' to a wave 4, or strip the Arabic/RTL-dependent target (the recovery.py:467-468/567-568 caching note, which is already marked deferred) so the remaining targets have no dependency and the depends_on list can be emptied.

**Converter-Gate-Route Ordering Chain**: Wave 2 rationale claims Z2 (Garble) and Z3 (Converter) touch disjoint regions of indexer.py ('~720-954' vs '~956+'), but the actual code targets collide in the same recovery-loop region of index(): Garble threads script_context through 'recovery dispatch at line 1209' and edits 1145-1151, while Converter deletes lines 1210-1218 and 1247-1260 and edits the recovery call sites. Two parallel wave-2 agents will produce merge conflicts in the same method, and Converter's deletion of 1213-1218 changes the exact lines Garble's target references.

> **Suggested fix:** Either serialize the two zones' indexer.py edits (Converter's finalize_gate_and_route refactor first, then Garble's script_context threading rebased on it), or correct the wave rationale and give one zone ownership of the index() recovery loop with the other expressed as a follow-up edit.

**Arabic/RTL Pipeline Blindness**: Wave 3 rationale describes code targets this zone does not have: it claims 'Z7's primary edit targets are converters/pipeline.py and helpers/tree_validation.py (RTL support)' and 'Z7 adds RTL handling to route_and_extract_flat' (helpers/flat.py), but the zone's actual code_targets are indexer.py, script.py, recovery.py, ocr_langs.py, table_stitch.py, and headings.py — none of pipeline.py, tree_validation.py, or flat.py. The wave-3 shared_files list and the Z7-before-Z8 flat.py sequencing argument are therefore built on a misdescription.

> **Suggested fix:** Rewrite the wave 3 rationale and shared_files against the zone's real targets. Actual wave-3 overlap is indexer.py and recovery.py (both zones), not flat.py (only Duplicated Convergent Logic touches flat.py).

**Tree/Flat Verdict Split**: ~~Minor~~ **RESOLVED.** Wiring checks were missing negative checks for deleted symbols. **Resolution:** Added `must_not_exist` wiring checks for `FLAT_GATE_SUBSET` (gates.py, verdict.py, __init__.py) and `flat_applicable` (types.py GateSpec). These now appear in the Zone 1 wiring checks table. Original issue: code_targets deletes/renames several other symbols that need verification: `FLAT_GATE_SUBSET` (removed from gates.py/verdict.py/__init__.py), `flat_applicable` field on GateSpec (removed from types.py and 3 GATES entries in gates.py), and the `flat: bool = False` kwarg removed from `evaluate_gates`/`compute_verdict`. Unlike Zone 4 (Garble Detection Fragmentation), which explicitly adds negative wiring_checks (must_be_imported_by: []) for deleted symbols like `check_garble`, Zone 1 has no such negative checks for FLAT_GATE_SUBSET or flat_applicable, so a partial implementation that deletes the branch but leaves stray references (confirmed present today at gates.py:474-504, types.py:244, helpers/__init__.py:110/220, verdict.py:17/187/399) would not be caught.

> **Suggested fix:** Add negative wiring_checks: {symbol: 'FLAT_GATE_SUBSET', must_be_imported_by: [], check_type: 'import'} and {symbol: 'flat_applicable', must_be_imported_by: [], check_type: 'reference'} (or equivalent), mirroring Zone 4's pattern for check_garble/_rebuild_garble_config_compat.

**Tree/Flat Verdict Split**: ~~BLOCKER~~ **RESOLVED.** Both zones targeted extracting the same hysteresis block. **Resolution:** Zone 1 owns the extraction — sync `apply_verdict_hysteresis` placed in `helpers/verdict.py` (matching Zone 7's preferred location). Zone 1's code target A and wiring checks updated. Zone 7's targets C/D removed and marked as Zone 1 prerequisite.

> **Suggested fix:** Have one zone own this extraction (Zone 1, since it's wave 1) and have Zone 7 either drop this code_target entirely or reference Zone 1's already-extracted symbol instead of re-specifying a duplicate extraction with a different name/location/signature.

### Minor Issues

**Arabic/RTL Pipeline Blindness**: The mechanism_to_eliminate text contains unresolved self-contradictory reasoning about marsoom-13 ('ratio=4.87x required to trigger but 5972 < 3*1225=3675 is false, wait -- 5972 > 3675 so it SHOULD fire') — leftover chain-of-thought that leaves the actual failure mechanism (and hence whether the 1.5x multiplier change is even needed vs. only the heading-injection guard) ambiguous for the implementing agent.

> **Suggested fix:** Verify against recovery.py:554-587 which condition actually blocks marsoom-13 (heading-injection inflating tree chars vs. the ok=True gate) and rewrite the mechanism as a single clean statement; adjust the multiplier target if 3.0x would already fire.

**Tree/Flat Verdict Split**: ~~Minor~~ **RESOLVED.** Wiring checks were self-inconsistent (required import of symbols defined in same file). **Resolution:** `apply_verdict_hysteresis` and `_LEDGER_PRIORITY` now placed in `helpers/verdict.py` with `symbol_exists` checks, and `indexer.py` gets an `import` check — consistent with cross-file usage.

> **Suggested fix:** Pin the design to one location (e.g. helpers/verdict.py, aligning with the Duplicated Convergent Logic resolution) and make the wiring checks match, or change check_type semantics to 'defined-or-imported'.

**Registry Dual-Write Consistency**: config.py constraint says 'all 4 files that read settings.registry_verdict_authority' but lists three (registry_mirror.py, storage/verdict.py, reconcile.py). Grep confirms exactly those three reader files plus the definition in config.py — the count is off by one but the enumerated list is complete.

> **Suggested fix:** Change '4 files' to '3 reader files plus config.py' so an implementer does not hunt for a nonexistent fourth reader.

**Garble Detection Fragmentation**: Wiring check requires _garble_config to be imported by src/pageindex_mcp/helpers/gates.py, but no gates.py code target describes introducing that import — the gates.py target only changes _gate_node_garbling to accept ScriptContext and pass it to _garble_check_nodes.

> **Suggested fix:** Either drop gates.py from the _garble_config wiring check or add the corresponding instruction to the gates.py code target.

**Tree/Flat Verdict Split**: ~~Minor~~ **RESOLVED.** check_type mismatch for `_LEDGER_PRIORITY` and `_apply_verdict_hysteresis`. **Resolution:** Both symbols now in `helpers/verdict.py` with `symbol_exists` checks; `indexer.py` gets `import` check. Zone 1 and Zone 7 location conflict eliminated by placing extraction in `helpers/verdict.py` consistently.

**Arabic/RTL Pipeline Blindness**: wiring_checks omits `TessdataUnavailableError`, even though the code_target for ocr_langs.py explicitly changes ensure_tessdata to raise it for non-Latin languages in a previously-unguarded branch (TESSDATA_PREFIX empty). This is the entire point of that code_target (silently-passing Arabic OCR through Latin fallback was the bug), so its wiring is unverified. Source check confirms `TessdataUnavailableError` is already imported in ocr_langs.py today and raised somewhere -- but the spec's new raise path (TESSDATA_PREFIX unset + non-Latin) is a distinct, currently-missing branch per the mechanism description, and no wiring_check confirms the new call site was actually added.

> **Suggested fix:** Add {symbol: 'TessdataUnavailableError', must_be_imported_by: ['src/pageindex_mcp/converters/ocr_langs.py'], check_type: 'call'} or an isinstance/raise-specific check_type distinguishing the new branch from the existing one.

**Arabic/RTL Pipeline Blindness**: check_type 'import' is used for env-var-style symbols `ARABIC_FLAT_PREFER_MULTIPLIER`, `ARABIC_HEADING_INJECTION_REVERTED` (a Prometheus counter per code_targets, not a plain constant), and `ARABIC_HEADING_MIN_CONTENT_CHARS`. A Prometheus counter object is typically module-level and referenced via `.inc()` calls at defect sites, not imported elsewhere -- 'import' as a check_type doesn't validate that the counter is actually incremented at the revert branch, which is the behavior that matters.

> **Suggested fix:** For `ARABIC_HEADING_INJECTION_REVERTED`, use check_type 'call' (verifying `.inc()` or similar is invoked) rather than 'import', since the risk being guarded against is the counter being defined but never incremented.

**Converter-Gate-Route Ordering Chain**: check_type 'dispatch' for `supports_ocr` is a non-standard value not used elsewhere in these specs (others use import/call). It's unclear what a 'dispatch' check verifies (that the field is read in a branch condition? that it flows from the converter chain into ExtractionState?). Ambiguous check semantics make it easy for a verifier to rubber-stamp a superficial match.

> **Suggested fix:** Replace with a concrete check_type such as 'call' (verifying `supports_ocr` appears as a condition inside the 3 replaced 'docling in conv_name' string-match sites at indexer.py lines ~461/492/505) or split into per-site checks.

**Converter-Gate-Route Ordering Chain**: code_targets for pipeline.py changes `pdf_markdown_converters()` return type from a 2-tuple to a 3-tuple `(name, fn, supports_ocr)`, a breaking change to the function's contract. wiring_checks do not include a check confirming the return-type/unpacking change itself (only the downstream `supports_ocr` usage at indexer.py). Source confirms there is exactly one production call site (indexer.py:451/458), so the blast radius is small, but there's no explicit wiring_check verifying pipeline.py's tuple arity actually changed to 3, only that indexer.py reads a 'supports_ocr'-shaped value.

> **Suggested fix:** Add a wiring_check on the return type of pdf_markdown_converters (e.g. via an isinstance/arity check_type) so a partial implementation that adds supports_ocr as a 4th positional-only kwarg or a dict instead of extending the tuple doesn't silently pass.

**Garble Detection Fragmentation**: wiring_checks do not include the new `GarbleReport` type, despite code_targets stating 'detect_garble's GarbleReport.__bool__ must remain the drop-in for the prior bool return of check_garble' -- this is called out as a specific compatibility contract but has no corresponding wiring_check (e.g. isinstance check_type) to catch a caller that forgets `bool(...)` wrapping and instead does truthy checks on something that isn't GarbleReport.

> **Suggested fix:** Add {symbol: 'GarbleReport', must_be_imported_by: [...8 call sites...], check_type: 'isinstance'} to verify each detect_garble() call result is consumed via bool()/isinstance(GarbleReport) rather than assumed truthy.

---

## 6. CI Wiring Gap

`scripts/gates/test-index-guard.sh` and `tests/TEST_INDEX.yaml` are not wired into CI or `eval.sh`. These were added in commit `9e85650` but never integrated into the gate pipeline.

**Action required:** Add `test-index-guard.sh` to `.github/workflows/ci.yml` and/or `scripts/eval.sh` so that source-to-test mapping is enforced on every PR.

---

## 7. Cross-References

| Document | Path |
|---|---|
| Architecture Defect Zones Audit | `audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md` |
| Zone Delta Analysis | `audit/ZONE_DELTA_2026-08-12_POST.md` |
| Remediation Scorecard | `audit/REMEDIATION_SCORECARD_2026-08-12_POST.md` |
| This Triage Report | `audit/ZONE_TRIAGE_2026-08-12_POST.md` |
