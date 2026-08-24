# Architecture Defect Zones Audit — 2026-08-24 POST-FIX-11

**Date:** 2026-08-24
**Sources:** 16 history miners, 1 code maps

## Summary Table

| # | Zone | Severity | Bug Count | Key Files |
|---|------|----------|-----------|-----------|
| 1 | Tree-vs-Flat Gate Asymmetry | critical | 14 | `helpers/gates.py`, `client/indexer.py`, `helpers/garble.py`, `helpers/types.py` |
| 2 | Pre-Tree Text Transforms vs Table/Block Integrity | critical | 11 | `converters/headings.py`, `helpers/tree_split.py`, `converters/pipeline.py` |
| 3 | Garble Detection Prong Blindness (NFKC, Script Threading, Title Inspection) | critical | 13 | `helpers/garble.py`, `helpers/tree_validation.py`, `helpers/gates.py`, `client/indexer.py` |
| 4 | Picture Enrichment / OCR Filter Composition | high | 15 | `converters/pictures.py`, `client/indexer.py`, `helpers/verdict.py` |
| 5 | Verdict Threshold Oscillation and Dual-CAS Divergence | high | 10 | `helpers/verdict.py`, `storage/verdict.py`, `storage/documents.py`, `registry/queries.py` |
| 6 | Recovery Routing Wiring Gaps (Detection Without Remediation) | high | 10 | `client/indexer.py`, `helpers/gates.py`, `helpers/types.py`, `helpers/tree_validation.py` |
| 7 | Worker/Inspector Dual-Threshold and Timeout Race | medium | 6 | `worker/subprocess_mgr.py`, `client/indexer.py`, `worker/job.py`, `converters/pipeline.py` |
| 8 | HR3 PII Egress Gap (Docling + VLM Silent Degradation) | medium | 4 | `server.py`, `config.py`, `converters/pictures.py`, `client/indexer.py` |

## Zone Details

### Zone 1: Tree-vs-Flat Gate Asymmetry

**Severity:** critical | **Bug count:** 14

The entire 10-entry GATES table (gates.py:329-411) is shaped exclusively around tree structures. When a document routes to FLAT via decide_route(), NONE of the 10 GateSpec entries (RTL_REVERSAL, NODE_GARBLING, LOW_CONTENT_DENSITY, SUSPECT_DENSITY, EMPTY_NODE_CONTAMINATION, BIDI_DEGRADED, etc.) re-run against the flat markdown. The flat path is protected by exactly ONE hand-written detect_garble() call inside _persist_flat_result (indexer.py:762-770) that is not a GateSpec and therefore invisible to gates.py's exhaustiveness asserts (lines 424-474). Any future gate added to GATES will silently NOT protect flat-routed documents unless hand-ported. Furthermore, the flat garble gate evaluates the FULL document blob before route_and_extract_flat splits it into blocks, so localized corruption (e.g. a numeric-junk table amid normal prose) dilutes below the 0.60 digit-ratio threshold and passes undetected. Tree-routed docs do not have this hole because _garble_check_nodes evaluates every node individually.

#### Mechanism
The tree pipeline has a formal gate table with exhaustiveness assertions and per-node granularity; the flat pipeline has an ad-hoc single-blob check with no structural decomposition. Any fix to tree-path quality detection (new GateSpec, threshold tuning, per-node garble check) automatically does NOT apply to flat-routed documents. Conversely, any fix to the flat garble gate is invisible to the GATES table's exhaustiveness machinery. A document routed to FLAT specifically because RTL_REVERSAL fired on the tree is NEVER re-verified for reversal on the persisted flat text -- garble_prongs has no reversal or word-order prong at all. This structural asymmetry means every quality-gate improvement generates a new blind spot on whichever path it was not applied to.

#### History
a. RFC-004 Amendment 1: flat docs bypassed validate_tree entirely (HR5 violation).
b. RFC-010 D3B: added _flat_text_is_garbled but as a separate function duplicating _tree_is_garbled logic.
c. RFC-013 D7 (ISS-36): confirmed fix-one-miss-the-other drift between tree and flat garble paths as root cause of marsoom-13 Latin-mojibake-passes-garble-gate.
d. RFC-019 D2: garble-gate Latin-gibberish detection unfireable because expected_script never passed to flat callers.
e. RFC-020 F2: fixed expected_script threading for flat path.
f. RFC-023 D4: cat_b_promoted gate let zero-content flat doc (Doc 21) reach PASS.
g. RFC-026: char-floor check used meta counter instead of actual persisted block text (7,471 meta vs 492 persisted chars).
h. RFC-027 (#5330): _flat_text_is_garbled operates on raw markdown including formatting characters, diluting digit-ratio.
i. RFC-028 D2: Arabic presentation-forms garble detection fired on legitimate text, reason='garbling' excluded from flat routing (FLAT-03-C2).
j. RFC-029 D3: in_fence toggle in route_and_extract_flat silently dropped 89-100% content on 5 documents.
k. RFC-030 D0: stray fence marker permanent content loss confirmed.

#### Code Evidence
gates.py:329-411 GATES list (10 GateSpec entries, all tree-structure-only). gates.py:424-474 REASON_POLICY exhaustiveness assert (covers only TreeDefect, not flat). indexer.py:762-770 _persist_flat_result detect_garble() call (single ad-hoc blob-level check, BlobKind.RAW_MARKDOWN). garble.py:377-380 digit_ratio prong: `if len(norm) > cfg.garble_digit_floor` (500 char floor, 0.60 whole-document ratio). types.py:281-316 decide_route() maps RAISE defects to Route.FLAT when flat_routing_enabled=True.

#### Key Files
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/helpers/garble.py
- src/pageindex_mcp/helpers/types.py

#### Simplification Proposal
Now I have a comprehensive understanding of the architecture. Here is my analysis:

---

**(1) Core simplification (2-3 sentences)**

Introduce a unified `QualityGate` protocol that both tree and flat paths execute, replacing the current split where GATES (10 GateSpec entries) run only on tree structures and flat gets a single ad-hoc `detect_garble` call on the whole blob. The key change: after `route_and_extract_flat` splits markdown into blocks, run garble detection per-block (mirroring `_garble_check_nodes` per-node granularity) and feed the block-level results through the same `GarbleReport`-based verdict machinery. This is not a new abstraction layer -- it is lifting the existing per-block decomposition (`route_and_extract_flat` already produces typed blocks) to happen BEFORE the garble gate instead of after, then reusing `detect_garble` on each block's primary text.

**(2) Concrete restructuring steps**

Step A: New function `_garble_check_flat_blocks` in `garble.py` (~25 lines net new). Takes `list[dict]` blocks (from `route_and_extract_flat`), iterates each block, calls `detect_garble` on `_flat_block_primary_text(block)` with `BlobKind.RAW_MARKDOWN`. Returns a `GarbleReport` that aggregates per-block prongs (union of all fired prongs) and a garble ratio (fraction of blocks that individually fired). This mirrors `_garble_check_nodes` structurally but operates on flat blocks.

Step B: Restructure `_persist_flat_result` in `indexer.py` (lines 715-870, net delta ~-15 lines). Move the `route_and_extract_flat` call (currently at line 807 via `_apply_picture_enrichment`) to BEFORE the garble gate. Then replace the current whole-blob `detect_garble` call (lines 752-764) with `_garble_check_flat_blocks(blocks, ...)`. This eliminates the dilution problem because each block is evaluated independently -- a numeric-junk TABLE block fires even if surrounding prose is clean.

Step C: Add `had_presentation_forms` threading to the flat garble context in `indexer.py` (line 753-756). Currently hardcoded `False`. Thread it from `state.rtl_decision` (already available on ExtractionState) the same way the tree path does via ScriptContext. Net delta: ~3 lines changed.

Step D: Extend the GATES exhaustiveness assertion in `gates.py` (lines 424-428). Add a compile-time assertion that every `_ReasonPolicy.RAISE` defect that maps to `Route.FLAT` has a corresponding flat-path quality check registered. This can be a simple frozen dict `FLAT_GATE_COVERAGE` mapping `TreeDefect -> callable` (~10 lines), asserted at import time alongside `REASON_POLICY`. This ensures future gates cannot silently skip flat coverage.

Step E: Delete the duplicated whole-blob garble path. The pre-block-split `detect_garble` call on `flat_md` is removed (it is superseded by per-block checking in Step B). Net delta: ~-8 lines from indexer.py.

Total estimated line delta: +25 (garble.py) -15 (indexer.py reorder) +10 (gates.py assertion) = ~+20 net lines.

**(3) Historical bug classes this would have prevented**

- RFC-027 #5330: `_flat_text_is_garbled` operating on raw markdown including formatting characters diluting digit-ratio -- per-block checking evaluates cleaned block text, not the full markdown blob with fences/headers.
- RFC-013 D7 (ISS-36): fix-one-miss-the-other drift between tree and flat garble paths -- both paths would use `detect_garble` with per-segment granularity; a new prong added to `garble_prongs` automatically applies to both.
- RFC-019 D2: `expected_script` never passed to flat callers -- Step C threads `had_presentation_forms` from `rtl_decision`, and the per-block function inherits the same ScriptContext constructor the tree path uses.
- RFC-023 D4: zero-content flat doc reaching PASS -- per-block checking with the `short_text_prior_garble` prong fires on each empty/tiny block individually rather than being diluted by a 500-char floor across the whole document.
- RFC-026: char-floor using meta counter instead of persisted block text -- per-block checking uses `_flat_block_primary_text` (the actual persisted text), not the raw markdown blob length.
- RFC-029 D3 / RFC-030 D0: content loss from fence-marker bugs would manifest as empty blocks that individually fail garble detection rather than being masked by the remaining document passing whole-blob ratio thresholds.

Would NOT have prevented: RFC-028 D2 (Arabic presentation-forms on legitimate text) -- that is a prong calibration issue, not a structural asymmetry issue.

**(4) Migration risk and incremental sequencing**

Risk is moderate. The main danger is changing the ORDER of operations in `_persist_flat_result` (block decomposition moves before garble gate). Mitigation: sequence as follows.

Phase 1 (safe, additive): Implement `_garble_check_flat_blocks` in garble.py. Add unit tests. No callers yet. Add the `FLAT_GATE_COVERAGE` assertion in gates.py gated behind an env var (`FLAT_GATE_ASSERT=true`) so it does not break existing code. Zero production risk.

Phase 2 (wire in, shadow mode): In `_persist_flat_result`, add the per-block garble check ALONGSIDE the existing whole-blob check (run both, log disagreements as warnings, use the whole-blob result for the actual decision). This exposes dilution-gap cases in production logs without changing behavior. Run a corpus cycle to measure divergence.

Phase 3 (cut over): Replace the whole-blob check with the per-block check. Remove the shadow logging. Enable the `FLAT_GATE_COVERAGE` assertion unconditionally. Run full corpus ingest-score-diff to confirm no regressions and verify the dilution-gap documents now correctly fail.

Key constraint: `validate_tree` must still run before `save_doc` (CLAUDE.md HR5). This change does not touch `validate_tree` -- it only affects the flat path that runs AFTER `validate_tree` has already fired and routed the document to FLAT. The tree path is unchanged.

**(5) Estimated effort**

Phase 1: 0.5 day (function + tests + gated assertion).
Phase 2: 0.5 day (shadow wiring + corpus run to collect divergence data).
Phase 3: 0.5 day (cut over + corpus diff + cleanup).
Total: 1.5 days of focused implementation, plus one corpus cycle between Phase 2 and Phase 3 to validate.

---

### Zone 2: Pre-Tree Text Transforms vs Table/Block Integrity

**Severity:** critical | **Bug count:** 11

Three independent pre-tree-build text transforms operate line-by-line on raw markdown with no awareness of table structure: (1) heading-injection functions (_inject_arabic_structural_headings, _inject_german_clause_headings, _inject_english_article_headings in converters/headings.py, called from pipeline.py:72 _build_candidate BEFORE md_to_structure/tree_split), (2) split_oversized_leaf_nodes (tree_split.py:401-477, ordinal regex with zero pipe-row awareness), and (3) _strip_toc_heading_nodes (no depth/node-count guard). Each independently fractures tables before _segment_table_nodes (tree_split.py:480-640) -- the only function with proper pipe-row detection helpers (_is_pipe_row, _is_sep_row) -- ever sees them. No shared 'line is inside a table' primitive exists despite three modules having built ad-hoc versions.

#### Mechanism
The pipeline processes raw markdown through heading injection -> tree build -> split_oversized_leaf_nodes -> _segment_table_nodes in strict sequence. Each earlier stage can fracture a table at ordinal markers or heading-like patterns inside table cells (e.g. 'Ziffer 3', 'Art. 9', 'Article (5)'). Once split, each fragment independently faces _segment_table_nodes's row-count floor (5 portrait / 10 landscape) and can fall under threshold on both halves -- never promoted to a Table node, silently merged into prose. This repeats on every OCR/garble/VLM retry since prepare_tree re-runs from scratch. The same fragmentation pattern also feeds into the flat-path digit_ratio dilution: table numeric content split across prose blocks dilutes below the 0.60 whole-document garble threshold. Two independently-discovered subsystems (garble detection, table segmentation) share one unnamed architectural root cause: no shared table-boundary primitive.

#### History
a. RFC-005 Fix-1: ordinal-matching splitter creates artificial fragmentation (documented as bounded safety net).
b. RFC-010 D4: splitter's dot-leader noise filter caused marsoom 33 node_count collapse 125->58 (-54%).
c. RFC-029 D4: _repair_docling_tables degenerate-row collapse destroyed Schedule 1-5 table structure in cabinet_resolution_no_21.
d. RFC-033 D11: _strip_toc_heading_nodes over-stripped Penal Code depth 3->2, 493/595 nodes flattened.
e. RFC-034 D16/D20: same unguarded mechanism implicated in marsoom 13 depth regression 4->2.
f. RFC-035 D2: landscape reextract chart axis labels shattered by _segment_table_nodes into 71+ singleton kv blocks.
g. RFC-036 D0: three compounding defects in landscape code, including singleton-ratio fragmentation.
h. RFC-028 D1: Arabic heading injection removed prev_blank guard and raised char limit, injecting just enough headings to clear validate_tree thresholds but blocking richer flat fallback (marsoom 13 lost ~80% content).
i. Run 12->13 unattributed: three large legal documents newly ERROR on low_content_density gate, shared failure signature pointing to systemic tree-builder change.

#### Code Evidence
headings.py:101-199 _inject_arabic_structural_headings: line-by-line regex matching with no pipe-row exclusion, called from pipeline.py:72 _build_candidate. tree_split.py:401-477 split_oversized_leaf_nodes: _OVERSIZED_ORDINAL_RE.finditer on folded text with zero _is_pipe_row check. tree_split.py:480-640 _segment_table_nodes: defines its own _is_pipe_row/_is_sep_row helpers (lines 484-492) that are NOT shared with split_oversized_leaf_nodes or heading injection. tree_split.py:536 _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD=2000 short-circuits before any scan.

#### Key Files
- src/pageindex_mcp/converters/headings.py
- src/pageindex_mcp/helpers/tree_split.py
- src/pageindex_mcp/converters/pipeline.py

#### Simplification Proposal
Now I have a clear picture. Here is the analysis:

---

**(1) Core Simplification (2-3 sentences)**

Extract `_is_pipe_row` and `_is_sep_row` from inside `_segment_table_nodes` to module-level functions in `tree_split.py`, then add a single shared predicate `is_inside_table(lines, line_index) -> bool` (or equivalently, a `mark_table_spans(lines) -> list[tuple[int,int]]` that returns ranges of line indices belonging to pipe-tables). Gate all three heading-injection functions in `headings.py` and `split_oversized_leaf_nodes` on this predicate so they skip lines/matches inside table spans. This eliminates the architectural root cause -- three independent line-by-line transforms fracturing tables -- with one shared primitive rather than three ad-hoc exclusions.

**(2) Concrete Restructuring Steps**

| Step | File | Change | Line delta |
|------|------|--------|------------|
| A. Promote `_is_pipe_row`/`_is_sep_row` to module-level in `tree_split.py` | `helpers/tree_split.py:485-490` | Move the two closures out of `_segment_table_nodes` to module scope; update internal references. No behavior change. | +0 (move, not add) |
| B. Add `compute_table_spans(lines: list[str]) -> list[tuple[int,int]]` | `helpers/tree_split.py` (new function, near line 70) | Scan lines for contiguous pipe-row runs (reusing `_is_pipe_row`). Return inclusive `(start, end)` index pairs. ~20 lines. | +20 |
| C. Add `line_in_table_span(idx, spans)` one-liner | `helpers/tree_split.py` | Binary search or linear check against precomputed spans. | +5 |
| D. Gate heading injection in `headings.py` | `converters/headings.py:144,226,252` | In each of the three `_inject_*` functions, call `compute_table_spans(lines)` once before the loop, skip promotion when `line_in_table_span(i, spans)`. Import from `helpers.tree_split`. | +9 (3 lines per function) |
| E. Gate `split_oversized_leaf_nodes` ordinal matching | `helpers/tree_split.py:431-432` | After `_fold_with_index_map`, compute table spans on the original text lines. Filter `all_matches` to exclude any match whose original-text offset falls inside a table span. | +8 |
| F. Delete the inner closure versions | `helpers/tree_split.py:485-490` | Already moved in step A; the nested definitions inside `_segment_table_nodes` become references to the module-level functions. | -6 |

**Net delta: ~+36 lines** (25 new utility + 9 gate lines in headings + 8 gate lines in splitter - 6 deleted closure lines).

**(3) Historical Bug Classes Prevented**

- **RFC-005 Fix-1 / RFC-010 D4**: ordinal splitter fragmenting tables mid-row would be blocked by the table-span gate in step E.
- **RFC-029 D4**: table structure destruction by splits before `_segment_table_nodes` sees the table -- the heading injection gate (step D) and splitter gate (step E) would preserve table integrity upstream.
- **RFC-028 D1**: Arabic heading injection promoting pipe-row content (e.g. "مادة (3)" appearing inside a table cell) would be skipped, preventing false structural-depth injection that blocked flat fallback. The marsoom 13 ~80% content loss scenario is directly addressed.
- **RFC-035 D2 / RFC-036 D0**: landscape table fragmentation into singleton KV blocks -- the splitter and heading injectors would not fracture the table before `_segment_table_nodes` applies its row-count floor, so the original contiguous table meets the threshold.
- **Run 12->13 `low_content_density` ERRORs**: the digit-ratio dilution mechanism (table numeric content split across prose blocks diluting below the 0.60 garble threshold) would be prevented because tables stay intact, keeping their numeric content concentrated in Table-typed nodes rather than dispersed into prose.
- **RFC-033 D11 / RFC-034 D16/D20**: while `_strip_toc_heading_nodes` is a separate issue (depth/node-count guard), the table-span primitive could also gate that function if needed, preventing table headings from being stripped.

**(4) Migration Risk and Sequencing**

**Risk**: Low-to-moderate. The change is additive (a new gate that skips processing, never adds processing), so the failure mode is under-injection/under-splitting rather than corruption. The main risk is false positives in `compute_table_spans` -- lines that look like pipe-rows but are not tables (e.g. `| some prose |` in non-table context). This is mitigated by requiring a separator row (`|---|---|`) within the contiguous pipe-row block, matching `_segment_table_nodes`'s existing heuristic.

**Incremental sequence**:

1. **Step A+B+C first** (pure refactor + new utility, zero behavior change). Ship with tests that assert `compute_table_spans` matches `_segment_table_nodes`'s own table detection on the existing corpus. This is risk-free and can be merged immediately.
2. **Step E second** (gate `split_oversized_leaf_nodes`). This is the highest-value fix -- the ordinal splitter is the most aggressive table-fracturing transform. Run corpus scoring diff to verify no regressions.
3. **Step D third** (gate heading injection). Lower risk since heading injection already has char-limit and line-start-anchor guards, but still important for Arabic legal docs. Run corpus diff focused on Arabic/German/English legal documents.
4. Each step is independently revertible. The shared primitive (steps A-C) stays even if a gate (D or E) is reverted.

**(5) Estimated Effort**

- Steps A+B+C (shared primitive): 2-3 hours including tests.
- Step E (splitter gate): 2-3 hours including corpus validation.
- Step D (heading injection gates): 2 hours including corpus validation.
- Total: **1-1.5 developer-days**, plus one corpus scoring cycle per gate step to confirm no regressions.

---

### Zone 3: Garble Detection Prong Blindness (NFKC, Script Threading, Title Inspection)

**Severity:** critical | **Bug count:** 13

The garble detection subsystem (garble.py garble_prongs, detect_garble) has multiple structurally independent blind spots that no single detector prong covers: (A) NFKC normalization (applied upstream) decomposes ALL Arabic Presentation Forms (U+FB50-FEFF) to base Arabic before garble_prongs runs, so the presentation_forms prong only fires when had_presentation_forms is pre-computed and threaded through; (B) the digit_ratio prong operates on the whole document with a 500-char floor, so numeric junk confined to one table or region dilutes below 0.60; (C) _garble_check_nodes in tree_validation inspects only node.text, never node.title -- RTL-reversed titles (23/24 nodes in siyasat-hawkama) are permanently invisible; (D) the latin_gibberish prong requires expected_script != 'Latn', but _infer_script derives script from the text itself, so Latin-gibberish text self-classifies as 'Latn' and the check is skipped -- unfireable for the exact case it targets.

#### Mechanism
Each garble prong is independently reasonable but their composition creates systematic blind spots. Detection fires but its signal never reaches the code that should act on it -- a recurring pattern where the detection->remediation wiring is missing. For example, 'node_garbling' was added as a new validate_tree reason string but client.py's OCR-escalation trigger was never updated to recognize it alongside 'garbling'. Four new RFC-029 failure reasons (low_content_density, suspect_density, empty_node_contamination, arabic_low_content_ratio) were never wired into client.py's recovery routing despite classify_verdict already mapping all four to FAIL. The NFKC blindness was independently rediscovered in RFC-033 D2, RFC-034 D7, and the separate Zone 5 audit -- the same root cause found by different investigations.

#### History
a. RFC-013 D7 (ISS-36): fix-one-miss-the-other drift between _tree_is_garbled and _flat_text_is_garbled.
b. RFC-018 D3b: 'node_garbling' reason never recognized by OCR-escalation conditional.
c. RFC-019 D2: expected_script never passed to garble callers, unfireable latin_gibberish.
d. RFC-025 D3: recovery triggers only check literal 'garbling', missing 'node_garbling'.
e. RFC-028 D2: Arabic PF garble detection tripped on legitimate text.
f. RFC-029 D0: _check_bidi_coherence fully implemented but never wired (dead code, duplicated).
g. RFC-029 D1/D2: four new validate_tree failure reasons never wired into recovery.
h. RFC-030 D2: unhandled reasons caused 3 PASS->ERROR regressions (single highest-impact systemic bug in Run 13).
i. RFC-030 D4: RTL-reversed titles invisible to garble gate.
j. RFC-030 D5: _check_bidi_coherence dead code confirmed.
k. RFC-033 D2: _reversed_morphology 0% TPR after NFKC.
l. Run 11: five independent Arabic-garble instances catalogued, none caught by PUA-only heuristic.

#### Code Evidence
garble.py:318-409 garble_prongs: digit_ratio prong at line 377 uses `len(norm) > cfg.garble_digit_floor` (500 chars), 0.60 threshold over whole blob. garble.py:386-392 latin_gibberish prong: guarded by `_effective_script != 'Latn'`. detect_garble (garble.py:498-568): computes had_presentation_forms from text AFTER NFKC normalization has already destroyed the codepoints. gates.py:329-411 GATES: NODE_GARBLING severity=3, GARBLING severity=0, both share recovery_fns ('_recover_garble_ocr', '_recover_vlm_fallback'). types.py:281-316 decide_route: RETRY_OCR maps to Route.TREE (retry handled upstream).

#### Key Files
- src/pageindex_mcp/helpers/garble.py
- src/pageindex_mcp/helpers/tree_validation.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/client/indexer.py

#### Simplification Proposal
I see the problem. The gate function declares `expected_script: str | None` but validate_tree passes a `ScriptContext` in that position (line 290). The gate then rebuilds a fresh `ScriptContext` with `had_presentation_forms=False`, losing the real value. This is the core structural flaw -- the ScriptContext computed once at entry is not threaded through; instead it's degraded back to a string and reconstructed multiple times with `had_presentation_forms=False`.

Here is the analysis:

---

**(1) Core Simplification (2-3 sentences)**

Replace the gate function signature's `expected_script: str | None` parameter with `script_context: ScriptContext` and pass the document-level ScriptContext (which already carries `had_presentation_forms` and `dominant_script`) directly through the gate table dispatch. This eliminates the six sites that reconstruct `ScriptContext(had_presentation_forms=False)` from a degraded string, closes the NFKC presentation-forms blindness, and removes the `_infer_script` self-classification loop that makes `latin_gibberish` unfireable. Additionally, make `_garble_check_nodes` inspect `node.title` (already done at line 645-661 in garble.py) and change the `digit_ratio` prong to operate per-node rather than on the whole document blob, so region-confined numeric junk is no longer diluted below threshold.

**(2) Concrete Restructuring Steps**

Step A -- Thread ScriptContext through gate functions (gates.py, tree_validation.py):
- Change `_GateFn` type alias: replace `str | None` (3rd param) with `ScriptContext`. (~0 net lines)
- Update all 10 `_gate_*` function signatures: `expected_script: str | None` -> `script_context: ScriptContext`. Each gate that needs the string accesses `script_context.dominant_script`. (-10 lines of ad-hoc ScriptContext construction in `_gate_node_garbling`, `_gate_low_content_density`).
- In `_gate_node_garbling` (gates.py:70-104): delete lines 87-92 (the `doc_script` inference + fresh ScriptContext construction). Pass `script_context` directly to `_garble_check_nodes`. (-6 lines)
- In `validate_tree` (tree_validation.py:289-290): already passes `_script_ctx`; just fix the type annotation. (0 lines)
- In `TreeSignals.from_tree` (tree_validation.py:196-200): when `expected_script` is already a `ScriptContext`, use its `had_presentation_forms` directly instead of hardcoding `False`. (-3 lines)

Step B -- Fix latin_gibberish self-classification (garble.py:389-401):
- In `garble_prongs`: when `expected_script is None`, do NOT fall through silently. Instead, the `latin_gibberish` prong should use the document-level `expected_script` from ScriptContext (already passed via the `expected_script` param). The actual fix is upstream: `_infer_script` is now never called inside `garble_prongs` -- the document-level script is authoritative. Remove the `_effective_script = expected_script` local alias that previously enabled call sites to omit it. (+2 lines for an explicit `if expected_script is None: return frozenset()` guard, -2 lines removing the alias)

Step C -- Per-node digit_ratio (garble.py:377-380):
- Move the `digit_ratio` prong logic so it runs inside `_garble_check_nodes` per-node (where it already runs via `detect_garble`), but lower `garble_digit_floor` from 500 to 50 for per-node context. The document-level prong stays as a backstop with the 500-char floor. (+4 lines in `_garble_check_nodes`, 0 change to `garble_prongs`)

Step D -- Delete backward-compat parameter cruft (garble.py:595-673):
- In `_garble_check_nodes`: remove `page_script` and `expected_script` params (replaced by `script_context` which is now mandatory). Update the 2 call sites (gates.py:94, garble.py:666). (-8 lines of parameter threading, -4 lines of fallback logic)

Step E -- Ensure indexer.py ScriptContext construction uses raw_text (client/indexer.py:1126):
- `ScriptContext.from_document(filename)` is called without `raw_text` (line 1126). After the fitz probe extracts raw text (around line 418), enrich the ScriptContext with `ScriptContext.from_document(filename, raw_text=raw_text)` so `had_presentation_forms` is computed from pre-NFKC text. This is the single most impactful line change. (+3 lines, -0 lines -- add a re-computation after raw_text is available)

Estimated net delta: -15 to -20 lines. No new abstractions, no new files.

Files touched: `src/pageindex_mcp/helpers/gates.py`, `src/pageindex_mcp/helpers/garble.py`, `src/pageindex_mcp/helpers/tree_validation.py`, `src/pageindex_mcp/client/indexer.py`.

**(3) Historical Bug Classes Prevented**

- RFC-028 D2 / RFC-033 D2 / RFC-034 D7 (NFKC presentation-forms blindness, independently rediscovered 3 times): eliminated by Step A+E -- `had_presentation_forms` computed once pre-NFKC, threaded everywhere.
- RFC-018 D3b / RFC-025 D3 (node_garbling reason not recognized by OCR escalation): already fixed by Zone-1 GateSpec-driven recovery loop, but Step A removes the remaining data-loss path where ScriptContext is reconstructed with `had_presentation_forms=False` inside the gate.
- RFC-019 D2 (latin_gibberish unfireable): fixed by Step B -- document-level script is authoritative, no self-classification.
- RFC-030 D4 (RTL-reversed titles invisible to garble gate): already fixed at garble.py:645-661, but Step D ensures title inspection inherits the correct ScriptContext.
- RFC-030 D2 (unhandled reasons causing PASS->ERROR regressions): prevented structurally because the gate table is exhaustive and recovery is driven by GateSpec declarations, not string matching. Step A ensures all gates see the full ScriptContext, closing the "detection fires but signal never reaches remediation" pattern.

**(4) Migration Risk and Sequencing**

Risk is low-to-moderate. The ScriptContext type is already defined and used at the `validate_tree` call boundary. The change is mostly removing redundant reconstruction.

Sequence:
1. Step E first (enrich ScriptContext with raw_text in indexer.py) -- standalone, zero breakage risk, immediately closes NFKC blindness for the bulk garble check.
2. Step A (thread ScriptContext through gate signatures) -- mechanical signature change, all call sites pass through validate_tree which already constructs ScriptContext. Run full test suite to verify.
3. Step D (remove backward-compat params from `_garble_check_nodes`) -- follows naturally from Step A.
4. Step B (latin_gibberish fix) -- isolated prong logic change, testable with a Latin-gibberish fixture.
5. Step C (per-node digit_ratio) -- lowest priority, additive, testable in isolation.

Each step is independently deployable and corpus-verifiable. Steps 1-3 can land in a single PR; Steps 4-5 as a follow-up.

**(5) Estimated Effort**

Steps A+D+E (core ScriptContext threading): 2-3 hours implementation + 1 hour test updates.
Step B (latin_gibberish fix): 1 hour.
Step C (per-node digit_ratio): 1-2 hours.
Corpus verification (re-score after each step): 1 hour per step.
Total: 6-10 hours across 1-2 PRs, with the highest-impact fix (Step E, one line in indexer.py) deliverable in under 30 minutes.

---

### Zone 4: Picture Enrichment / OCR Filter Composition

**Severity:** high | **Bug count:** 15

The picture-enrichment pipeline (_recover_picture_text, splice_figure_markers, _enrich_image_blocks) has a chain of independently-tuned filters (page-coverage >60% exemption, text-layer >20 chars clip-text check, forced-OCR reclassification, synthetic PictureResult list multiplication) that repeatedly combine to silently zero out enrichment or content that neither filter alone would destroy. Forced-OCR causes Docling to reclassify PictureItems as TextItems, returning 0 PictureResults, killing the enrichment pipeline. The coverage filter blocks recovery of genuine full-page scanned content. The clip-text filter combined with the coverage filter killed ALL picture regions in text-layer PDFs with large regions. Python list multiplication for synthetic PictureResults shared object references, so popping png_bytes from one entry mutated all siblings.

#### Mechanism
Each RFC's picture-enrichment fix addresses one filter's misbehavior, but the fix interacts with other filters in the chain to create a new failure mode. RFC-018 D0 added page-coverage filter -> RFC-019 D1 added clip-text filter -> together they zeroed ALL enrichment for text-layer PDFs. RFC-021 QF1 removed forced-OCR to preserve PictureItem segmentation -> RFC-022 B3 regressed OCR splice. RFC-024 D1's clip_text capture logic never executed because the page-level text check (any header/footer >20 chars) blocked the exemption before reaching it, silently dropping entire body content (Human Rights: 503k->382 chars). The pipeline is wired only for PDFs; standalone images (.jpg/.png/.tiff) never run enrichment at all (client.py image branch never calls _enrich_image_blocks or splice_figure_markers).

#### History
a. RFC-015 D6: per-picture OCR relocated text from prose to image blocks, degrading retrieval (6+ regressions Run 6).
b. RFC-018 D0: page-coverage >60% filter blocked full-page scanned content.
c. RFC-018 D3a: forced OCR without ocr_lang_override -> mojibake on Arabic.
d. RFC-018 D3a side-effect: forced OCR reclassified PictureItems as TextItems, 0 PictureResults, tree has no heading structure -> flat routing (persisted through Run 13 for docs 7,17,20,21).
e. RFC-019 D0: list multiplication shared references, mutated siblings.
f. RFC-019 D1+D0: combined filters zeroed all enrichment (doc 3 1/4->0/3, doc 9 3->0).
g. RFC-020 F1/F2/F3/F4/F5: five fixes to the same filter composition.
h. RFC-021 QF1->RFC-022 B3: GHV-TKV OCR splice regression.
i. RFC-024 D1->RFC-025 D1: clip_text never executed, Human Rights 503k->382 chars.
j. RFC-023 D0: _text_layer_has_content checks char count without garble detection.
k. RFC-023 D8a: standalone images create synthetic PictureResult with ocr_text='', no enrichment.

#### Code Evidence
indexer.py:715-944 _persist_flat_result: splice_figure_markers called on line 752 before block splitting. GATES: enrichment is orthogonal to all 10 gate specs (no IMAGE_ENRICHMENT gate). indexer.py:1229-1300 match statement: (False, Route.PERSIST_FAIL) falls through to _persist_tree_result.

#### Key Files
- src/pageindex_mcp/converters/pictures.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/helpers/verdict.py

#### Simplification Proposal
I now have a thorough understanding of the full picture enrichment pipeline. Here is my analysis:

---

**(1) Core Simplification**

Replace the current chain of independently-tuned, sequentially-composed filters (coverage gate, clip-text gate, decorative gate, text-layer probe, containment check, document-level fallback, OCR-min-chars threshold) with a single per-region classification that produces an immutable `RegionPlan` before any I/O runs. The `_classify_region` function in `picture_plane.py` already does half of this (lines 269-336), but the I/O-dependent metadata extraction (text-layer probe, clip-text extraction, containment check) still lives interleaved with the disposition switch inside `_recover_picture_text` (pictures.py lines 775-886). Hoist all metadata extraction into a serial scan phase that populates a frozen `RegionMetadata` dataclass per region, then pass that to `_classify_region`, and execute the resulting disposition in a third phase that cannot influence classification. This eliminates the shared-mutable-state channel where one filter's side effect (e.g., `fullpage_ocr_region_count` incrementing mid-loop, clip-text reading from the same fitz page handle used for coverage) alters another filter's input.

**(2) Concrete Restructuring Steps**

Step A -- Introduce `RegionMetadata` dataclass in `picture_plane.py` (~+25 lines):
- Fields: `index`, `page_index`, `coverage`, `has_own_text`, `clip_text`, `clip_text_contained`, `rect_width`, `rect_height`, `rect` (opaque, for crop phase).
- Frozen dataclass, constructed once per region.

Step B -- Extract metadata scan from `_recover_picture_text` into `_scan_regions` in `pictures.py` (~+40 lines, -60 lines from `_recover_picture_text`):
- Opens fitz document once, iterates regions, populates `list[RegionMetadata]`.
- All fitz I/O (page access, `get_text`, bbox conversion, coverage calculation) lives here.
- `_text_layer_has_content` and `_clip_text_contained` called here, results stored in the frozen metadata.
- Returns `list[RegionMetadata]` and closes fitz handle.

Step C -- Refactor `_recover_picture_text` into `_execute_plans` (~net -80 lines):
- Phase 1: `_scan_regions` (Step B).
- Phase 2: `_classify_region` called on each `RegionMetadata` (already pure, no change needed).
- Phase 3: crop+OCR execution loop that reads only the disposition from phase 2. No classification logic, no text-layer checks, no containment checks. Opens a second fitz handle for cropping only.
- The `fullpage_ocr_region_count` counter moves into a pre-pass over classifications (count how many are CROP_AND_OCR with coverage_exempt=True) so the cap can be enforced without mid-loop mutation.

Step D -- Fix standalone image list multiplication in `indexer.py` line 666-674 (~net 0 lines):
- Replace `[PictureResult(...) for _ in range(max(1, marker_count))]` with explicit list comprehension creating independent dict instances (this is the shared-reference bug from RFC-019 D0). Note: `PictureResult` is a `TypedDict`, so each comprehension iteration already creates a new dict -- this specific instance is safe, but the pattern should use an explicit factory to prevent future regressions.

Step E -- Consolidate the two splice entry points (`splice_picture_text_for_tree` and `splice_figure_markers`) (~net -15 lines):
- Both call into `bind_markers` or do regex substitution over `<!-- image -->`. Merge into a single `splice_pictures(md, pics, mode="tree"|"flat")` function that selects the output format (neutral `<!-- image -->` + `[Chart text]` for tree, `[Figure: fig-k]` for flat). Eliminates the risk of one splice path diverging from the other on landscape-fallback filtering or ordinal alignment.

File targets and line-count delta:
- `src/pageindex_mcp/picture_plane.py`: +25 (RegionMetadata dataclass)
- `src/pageindex_mcp/converters/pictures.py`: -55 net (_scan_regions extracted, _recover_picture_text simplified, splice consolidated)
- `src/pageindex_mcp/client/indexer.py`: -5 (standalone image factory, remove dual splice import)
- `src/pageindex_mcp/client/images.py`: -5 (use unified splice)
- Total: ~-40 lines net

**(3) Historical Bug Classes Prevented**

- RFC-019 D0 (list multiplication shared references): eliminated by the standalone-image factory fix in Step D and by the frozen `RegionMetadata` making per-region state immutable.
- RFC-019 D1 + RFC-018 D0 (combined filters zeroing all enrichment): prevented because classification runs on frozen metadata with no I/O interleaving -- the coverage gate cannot observe a text-layer result that was probed under different conditions than the clip-text gate.
- RFC-024 D1 (clip_text never executed because page-level text check blocked the exemption): prevented because `_scan_regions` extracts clip_text and text-layer content independently for every region before classification; the coverage gate's `has_own_text` check cannot short-circuit the clip-text extraction.
- RFC-021 QF1 to RFC-022 B3 (OCR splice regression): prevented by the unified `splice_pictures` function -- tree and flat paths share one ordinal-alignment implementation, so fixing one cannot regress the other.
- RFC-018 D3a (forced OCR reclassifying PictureItems as TextItems): mitigated by the `full_page_already_applied` re-entry guard (already in place via `decide_ocr_strategy`), and further hardened by the scan phase being unable to trigger re-conversion.
- RFC-025 D1 (heading-only tree with clip_text containment false positive): prevented because `body_for_containment` is already snapshotted in `_fallback_and_recover_pictures` before fallback appends, and the scan phase receives it as a frozen input.

**(4) Migration Risk and Sequencing**

Risk: The main risk is ordinal alignment between `<!-- image -->` markers and PictureResult indices. The current code relies on `_collect_picture_regions` iteration order matching marker emission order. The refactoring preserves this invariant because `_scan_regions` iterates in the same `iterate_items` order.

Incremental sequence:
1. Ship Step A (RegionMetadata dataclass) and Step B (_scan_regions) as a pure addition with no callers -- zero behavioral change, tests validate the new dataclass.
2. Ship Step C (refactor _recover_picture_text to call _scan_regions + _classify_region + execute) behind a feature flag `PICTURE_PIPELINE_V2=false`. Run corpus scoring against both paths, diff results.
3. Ship Step D (standalone image factory) independently -- it is a one-line fix with its own test.
4. Ship Step E (unified splice) last, once ordinal alignment is validated by corpus diff.

Each step is independently revertible. The feature flag in step 2 is the highest-risk change and should run through at least one full corpus cycle before becoming the default.

**(5) Estimated Effort**

3-4 days for implementation + tests. 1 day for corpus diff validation. Steps A/B/D can be done in parallel by different people. Step C depends on A+B. Step E depends on C being validated. Total wall-clock with corpus validation: ~1 week.

---

### Zone 5: Verdict Threshold Oscillation and Dual-CAS Divergence

**Severity:** high | **Bug count:** 10

The verdict system has three interacting instability sources: (1) PASS_MAX_LEAF_RATIO was widened three times (0.17->0.20->0.30) chasing jitter on different documents each time, and RFC-025's hysteresis fix was defeated by corpus reingestion wiping the prior-verdict store. (2) Two independently-computed CAS guards protect the SAME verdict fields on two different substrates -- _verdict_cas_guard (Python string comparison in verdict.py:91-118 for MinIO sidecar) and SQL CASE/EXCLUDED in queries.py:19-83 for Postgres -- and they never consult each other, so the Postgres 'winner' can be silently rejected by the sidecar-local guard. (3) The verdict ledger (verdicts/{sha256}.json) used by apply_verdict_hysteresis (verdict.py:449-496) anchors against content-hash-keyed prior verdicts, but this ledger is NOT included in the HR2 erasure cascade (delete_doc has no reference to verdicts/ prefix), creating a compliance gap where erased document verdict data survives and is silently reapplied on re-ingestion.

#### Mechanism
Threshold widening without hysteresis produces a recurrence cycle: Doc A triggers a widen, Doc B now exceeds the widened threshold, triggering another widen or a different fix. The hysteresis mechanism itself introduces a new failure mode -- it masks real gate-softening regressions. The dual-CAS divergence means MinIO and Postgres can hold different verdicts indefinitely with no error raised. The verdict ledger HR2 gap means content-hash-keyed verdict data from deleted documents persists and is reapplied, undermining the right-to-erasure guarantee. Three independent registry writers (live dual-write, cron reconcile delta-upsert, verdict-retry-queue drain) each construct their own upsert payload for the same doc_id concurrently, with safety resting entirely on the SQL-level CAS remaining correct as new columns are added.

#### History
a. RFC-023 D10: widened PASS_MAX_LEAF_RATIO 0.17->0.20.
b. RFC-024 D0: widened 0.20->0.30, own risk table predicted failure.
c. RFC-025 D0: hysteresis via prior-verdict anchoring, defeated by corpus reingestion wiping processed/*.meta.json.
d. RFC-026 D3: GHV-TKV-Tarif flapped PASS->MARGINAL on identical tree after wipe.
e. Run 8: Doc 8 Reitlehrer remained degraded despite widening.
f. RFC-030 D6: judge calibration rules never written to skill files, producing phantom regressions on byte-identical content.
g. RFC-034 D18: write-visibility barrier over-provisioned (4.4s blocking delay), PersistenceNotVisibleError propagated as RuntimeError.
h. RFC-036 D1: reduced delays, caught error as warning.
i. Run 10-12: verdict inflation/oscillation on unchanged extraction metrics across multiple documents.

#### Code Evidence
verdict.py:449-496 apply_verdict_hysteresis: reads verdicts/{sha256}.json via read_ledger_fn, overrides computed verdict with prior higher-priority. verdict.py:472-553 persist_verdict_ledger: writes to 'verdicts/{sha256}.json' MinIO key. documents.py:141-314 delete_doc: 7-step cascade covers uploads/, processed/*.json, processed/*.meta.json, Redis, Postgres, preloaded/ -- NO reference to verdicts/ prefix anywhere. verdict.py:91-118 _verdict_cas_guard: Python string comparison on verdict_computed_at. queries.py:19-83 _UPSERT_SQL: independent SQL-level CAS per-column.

#### Key Files
- src/pageindex_mcp/helpers/verdict.py
- src/pageindex_mcp/storage/verdict.py
- src/pageindex_mcp/storage/documents.py
- src/pageindex_mcp/registry/queries.py

#### Simplification Proposal
I have the full picture. Here is my analysis.

---

**(1) Core simplification (2-3 sentences)**

Unify verdict authority into Postgres as the single source of truth, eliminating the MinIO verdict ledger (`verdicts/{sha256}.json`) entirely and collapsing the MinIO sidecar CAS guard (`_verdict_cas_guard`) into a thin pass-through that defers to the SQL CAS already in `_UPSERT_SQL`. The hysteresis mechanism (`apply_verdict_hysteresis` + `persist_verdict_ledger` + `read_verdict_ledger`) is replaced by a single SQL query in `_UPSERT_SQL` that already implements max-priority-wins via its CASE/EXCLUDED guard -- the SQL CAS becomes the sole concurrency arbiter, and the sidecar becomes a read-only archive backfilled from the RETURNING row. This eliminates the dual-CAS divergence, the verdict ledger HR2 gap, and the duplicate priority maps (`_LEDGER_PRIORITY` in helpers/verdict.py, `_LEDGER_VERDICT_PRIORITY` in storage/verdict.py).

**(2) Concrete restructuring steps**

Step A -- Add max-priority-wins to SQL CAS (queries.py, ~+8 lines):
Extend the verdict CASE block in `_UPSERT_SQL` (lines 63-82) to also reject a verdict downgrade: when the incoming `verdict_computed_at` is newer but the incoming verdict has lower priority than the existing row's verdict, preserve the existing verdict. This replaces the hysteresis mechanism at the authoritative layer. Net: ~+8 lines in queries.py.

Step B -- Add `verdicts/` prefix to HR2 erasure cascade (documents.py, ~+15 lines):
In `delete_doc` (line 141), after step 3 (meta.json removal), add a new step that reads sha256 from the sidecar or processed doc (already available from the `data` variable at line 158) and removes `verdicts/{sha256}.json`. This closes the compliance gap immediately, independent of the other steps. Net: ~+15 lines in documents.py.

Step C -- Remove verdict ledger write path (storage/verdict.py, ~-90 lines):
Delete `persist_verdict_ledger` (lines 472-553), `read_verdict_ledger` (lines 556-597), and `_LEDGER_VERDICT_PRIORITY` (line 469). Remove the fire-and-forget call to `persist_verdict_ledger` inside `save_doc_meta` (lines 237-250). Net: ~-90 lines in storage/verdict.py.

Step D -- Remove hysteresis from indexer (helpers/verdict.py ~-35 lines, client/indexer.py ~-15 lines):
Delete `apply_verdict_hysteresis` (helpers/verdict.py lines 449-496) and `_LEDGER_PRIORITY` (line 444). Remove the two call sites in client/indexer.py (around line 847-857 for flat path, and the equivalent tree path). The SQL CAS now handles this. Net: ~-50 lines across both files.

Step E -- Collapse sidecar CAS to pass-through (storage/verdict.py, ~-15 lines):
Simplify `_verdict_cas_guard` (lines 91-118) to always return False (allow the write), since the sidecar is now an archive backfilled from the Postgres RETURNING row -- Postgres is the arbiter. Alternatively, delete it entirely and remove the `_skip_verdict` branch in `save_doc_meta`. The `_upsert_registry_row` in registry_mirror.py already backfills the sidecar from the winning Postgres row (line 112), making the sidecar CAS guard redundant. Net: ~-15 lines in storage/verdict.py.

Step F -- Remove deprecated `upsert_verdict` wrapper (queries.py, ~-25 lines):
Delete `upsert_verdict` (lines 138-160) and update its sole remaining caller (`reconcile._drain_verdict_retry_queue`) to call `upsert_doc` directly (it already does, via the wrapper). Net: ~-25 lines.

**Total estimated delta: roughly -170 lines net.**

**(3) Historical bug classes this would have prevented**

- RFC-025 D0 hysteresis defeated by corpus reingestion: eliminated entirely -- no separate ledger to wipe.
- RFC-026 D3 GHV-TKV-Tarif PASS->MARGINAL flap on identical tree after wipe: the SQL max-priority-wins guard survives processed/ wipes because it reads the registry row, not a MinIO prefix.
- Run 10-12 verdict inflation/oscillation on unchanged metrics: single decision point (SQL CAS) instead of three competing writers means no divergence window.
- Dual-CAS divergence where MinIO and Postgres hold different verdicts indefinitely: eliminated -- sidecar is backfilled from RETURNING, never independently guarded.
- Verdict ledger HR2 compliance gap: eliminated entirely by removing the ledger (and the intermediate step B closes it before removal).
- RFC-034 D18 PersistenceNotVisibleError from write-visibility barrier: the sidecar is no longer authoritative, so its write-visibility is irrelevant.
- PASS_MAX_LEAF_RATIO widening cascade: the max-priority-wins SQL guard means a document that once passed will not flap to MARGINAL on reingestion even if thresholds change, removing the pressure to widen thresholds.

**(4) Migration risk and sequencing**

Sequence the steps in dependency order with a release between B and C:

1. **Step B first (HR2 fix, zero risk)**: Adding `verdicts/` to the erasure cascade is additive. Deploy immediately. This closes the compliance gap regardless of whether the rest ships.

2. **Step A second (SQL max-priority-wins, low risk)**: The SQL change is self-contained in `_UPSERT_SQL`. Test by running the corpus scoring cycle and verifying no verdict downgrades occur. Risk: a document whose extraction genuinely improves from MARGINAL to PASS still upgrades (priority is higher), and one that degrades is now anchored -- this is the desired behavior. One edge case: a document that was erroneously PASSed needs a manual registry reset to re-evaluate. Mitigate with a `--force-recompute` flag that sets `verdict_computed_at` to epoch+1 and verdict to empty string, bypassing the CAS.

3. **Steps C+D together (ledger removal, medium risk)**: Once the SQL guard is proven, remove the hysteresis mechanism and ledger. Risk: if the SQL CAS has a bug, there is no fallback anchoring. Mitigate by running a full corpus cycle with the SQL guard alone (Step A) for one release before removing the ledger.

4. **Steps E+F last (cleanup, low risk)**: Collapsing the sidecar CAS and removing the deprecated wrapper are pure cleanup. Ship after C+D is validated.

Rollback: Steps are independently revertible. The only state dependency is the `verdicts/` prefix objects -- if Step C ships and needs rollback, the ledger objects still exist in MinIO (nothing deletes them except the future Step B in the erasure cascade), so re-adding the read path restores hysteresis.

**(5) Estimated effort**

- Step B (HR2 fix): 0.5 day (code + test for erasure cascade coverage)
- Step A (SQL max-priority-wins): 1 day (SQL change + integration test with corpus scoring)
- Steps C+D (ledger + hysteresis removal): 1 day (deletion + test updates)
- Steps E+F (CAS collapse + deprecated wrapper removal): 0.5 day
- Corpus validation cycle between each release: 0.5 day each (x3 = 1.5 days)

**Total: approximately 4-5 engineering days**, sequenced across 3 releases.

Key files affected: `src/pageindex_mcp/registry/queries.py`, `src/pageindex_mcp/storage/verdict.py`, `src/pageindex_mcp/storage/documents.py`, `src/pageindex_mcp/helpers/verdict.py`, `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/registry_backfill/reconcile.py`.

---

### Zone 6: Recovery Routing Wiring Gaps (Detection Without Remediation)

**Severity:** high | **Bug count:** 10

The GateSpec-driven recovery loop (indexer.py:1190-1197) dispatches recovery methods via string-name lookup (getattr(self, fn_name)) in GATES list order, deduped by recovery_fns tuple. This design has three structural problems: (1) Parameter/reason-string threading gaps make otherwise-correct detectors unfireable -- expected_script, node_garbling, and four RFC-029 failure reasons were each fully implemented but their signals never reached the recovery code. (2) 'Fixed but never wired/committed' is a distinct failure class: chunked_docling_timeout_s (RFC-027/028), _check_bidi_coherence (RFC-029/030), RFC-029 D6 judge rules, and RFC-034 D19's enrichment guard were each correct in isolation but inert in production. (3) validate_tree's early-exit ordering (node_count<3/depth<2 BEFORE the garble check) means numeric-junk trees get reason='node_count<3' instead of 'garbling', so OCR escalation (which only fires on garbling/node_garbling reasons) never triggers.

#### Mechanism
The system separates detection (gates.py GateSpecs, garble_prongs, validate_tree) from remediation (recovery methods in indexer.py, OCR escalation in client.py) across module boundaries with string-based dispatch. New detection signals added to gates.py or helpers/ require corresponding wiring in indexer.py's recovery chain, but there is no compile-time or test-time enforcement that a new TreeDefect reason has a matching recovery path. The GATES exhaustiveness asserts verify that RETRY_OCR/RETRY_RTL gates have recovery_fns, but they do NOT verify that RAISE/PERSIST_FAIL defects have any recovery path at all -- so new defects in those categories silently fall through to LowQualityTreeError with no recovery attempted. Static call-graph analysis is misleading: _recover_rtl_flat_compare shows in_degree=0 (looks dead) but is live-wired through gates.py's recovery_fns tuple.

#### History
a. RFC-018 D3b: node_garbling reason never recognized by OCR escalation.
b. RFC-025 D3: recovery triggers only check 'garbling', missing 'node_garbling'.
c. RFC-027 task 4.2: chunked_docling_timeout_s created but never wired to worker.py (marked complete).
d. RFC-028 D0: world-stats-pocketbook timed out 3 consecutive runs.
e. RFC-029 D0: _check_bidi_coherence fully implemented, duplicated, never wired (dead code).
f. RFC-029 D1/D2: four new failure reasons never wired into recovery, caused 3 PASS->ERROR regressions (Run 13's highest-impact systemic bug).
g. RFC-030 D5: confirmed dead code.
h. RFC-034 D19: enrichment displacement guard staged in git, never committed, inactive through Run 19.
i. RFC-036 D2: finally committed the already-staged code.
j. Cross-cutting: validate_tree early-exit ordering causes numeric-junk trees to get node_count<3 reason, bypassing garble detection AND OCR escalation (#5330).

#### Code Evidence
indexer.py:1190-1197 recovery loop: `for _gate in GATES: if not _gate.recovery_fns ... await getattr(self, _fn_name)(...)`. gates.py:329-411 GATES: GARBLING/NODE_GARBLING share recovery_fns, RTL_REVERSAL has its own pair, NODE_COUNT_LOW/DEPTH_LOW have recovery but with RAISE policy. gates.py:434-449 exhaustiveness asserts: verify recovery_fns/recovery_eligible for RETRY_OCR/RETRY_RTL only. types.py:281-316 decide_route: PERSIST_FAIL maps to Route.PERSIST_FAIL (no recovery, persist with FAIL verdict).

#### Key Files
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/helpers/types.py
- src/pageindex_mcp/helpers/tree_validation.py

#### Simplification Proposal
_No proposal was generated for this zone in the source data._

---

### Zone 7: Worker/Inspector Dual-Threshold and Timeout Race

**Severity:** medium | **Bug count:** 6

The PDF_INSPECTOR_PRECLASSIFY flag is checked in TWO places with DIFFERENT confidence thresholds: indexer.py requires confidence>=0.90 before forcing full-page OCR (inspector-force-OCR gate), while subprocess_mgr.py applies the 16.5x timeout multiplier on pdf_type alone with NO confidence check. At confidence in [0, 0.90), the worker sizes a 16.5x CHILD_TIMEOUT expecting an OCR pass that never happens. Separately, the extended deadline is computed pre-flight but only persisted to Redis AFTER the subprocess returns, so for the entire duration of a long scanned-PDF conversion, Redis shows the conservative default deadline, and the cluster-wide reap_stale_jobs cron can reap a legitimately-executing job. JOB_TIMEOUT=3630 is independently defined in both job.py and subprocess_mgr.py (comment admits duplication to sidestep circular import). The ALLOW_AGPL_FALLBACK flag causes the same MAX_DOCLING_PAGES/chunking decision to be computed twice with two different PDF libraries (fitz vs pypdfium2), and when ALLOW_AGPL_FALLBACK=false, pipeline.py reports page_count=0 while the worker handshake correctly reports the real chunk_count.

#### Mechanism
The inspector classification flows from probe_conversion_route through a handshake dict to both client-side and worker-side consumers, but each independently applies its own threshold. A doc classified scanned at confidence 0.5 gets the 16.5x timeout budget but never runs forced OCR -- the worker budgets for work that never happens, while the reaper's staleness gate works against an oversized deadline. The reap_stale_jobs race is self-acknowledged and papered over with an ERROR->DONE 'late_success' transition rather than closed. The page-count duplication means the worker sizes a large CHILD_TIMEOUT for a chunked route that pipeline.py then declines to chunk.

#### History
a. RFC-032 D3: 3x worker timeout multiplier empirically shown insufficient (actual range 2.32x-11.00x).
b. RFC-032 D9: recalibrated to 16.5x.
c. RFC-028 D0: chunked_docling_timeout_s never wired, world-stats-pocketbook timed out 3 consecutive runs (ERROR, FAIL, ERROR).
d. RFC-034 D18: write-visibility barrier added 4.4s blocking delay, SLA document completed 3-5 minutes late, scored as false ERROR.
e. RFC-036 D1: reduced delays.
f. Run 8->Run 9: exception-handling patch converted Arabic CMap crash to near-empty artifact instead of genuine fix.

#### Code Evidence
indexer.py:_convert_to_tree ~365-380: `if PDF_INSPECTOR_PRECLASSIFY and pdf_classification.pdf_type in ('scanned','image_based') AND confidence>=0.90` sets inspector_force_ocr=True. subprocess_mgr.py:_run_converter_subprocess ~171-184: `if PDF_INSPECTOR_PRECLASSIFY and pdf_class.get('pdf_type') in ('scanned','image_based'):` with NO confidence read anywhere in the function, applies effective_timeout *= 16.5.

#### Key Files
- src/pageindex_mcp/worker/subprocess_mgr.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/worker/job.py
- src/pageindex_mcp/converters/pipeline.py

#### Simplification Proposal
_No proposal was generated for this zone in the source data._

---

### Zone 8: HR3 PII Egress Gap (Docling + VLM Silent Degradation)

**Severity:** medium | **Bug count:** 4

Hard Rule 3 (PII corpus -> ZDR/zero-retention LLM tier) is enforced at startup (_lifespan_with_scrape, server.py:73-94) for the primary LLM path (openai_base_url, LLM_FALLBACK_BASE_URL) but has TWO structural bypass paths: (1) DOCLING_SERVICE_URL sends the full raw PDF to whatever remote service is configured with ZERO ZDR/pii_corpus check anywhere (confirmed: no references to require_zdr_compliance/_is_zdr_allowlisted/pii_corpus in remote.py, worker/subprocess_mgr.py, or indexer.py call sites). (2) When the flat garble gate's VLM fallback is blocked by HR3 compliance (zdr_egress_gate raises), the exception is caught by the same generic except-block used for real VLM API failures (VLM_FALLBACK_TOTAL.labels('error').inc()), making a compliance block indistinguishable from a genuine failure in metrics and logs. The primary LLM path has NO per-call gate -- safety rests entirely on a boot-time invariant, assuming Settings is frozen and never reloaded.

#### Mechanism
HR3 compliance checks are implemented as point guards at different pipeline stages rather than as a unified egress control plane. Each new egress path (Docling remote, VLM fallback, future LLM_FALLBACK_BASE_URL) must independently implement its own ZDR check, and the absence of a check produces no compile-time or runtime error -- only a silent PII leak. The startup gate protects a boot-time snapshot of OPENAI_BASE_URL but the actual HTTP client may be constructed later with a different URL if configuration is mutated. The VLM silent-degradation pattern (compliance block caught as generic error) means an operator cannot distinguish policy-blocked-for-compliance from genuinely-broken VLM, degrading quality-gate telemetry.

#### History
a. RFC-004 D3: async/sync boundary hazard in pdf_to_markdown_docling, bare except Exception swallows VlmCallError and silently falls through to AGPL pymupdf4llm.
b. RFC-004 Amendment 4: only removed direct PyMuPDF dependency, pymupdf4llm still pulls PyMuPDF transitively.
c. CLAUDE.md HR3: 'Route PII-bearing documents only through a no-training + zero-retention LLM tier'.
d. CLAUDE.md HR4: 'pymupdf4llm/PyMuPDF are AGPL-3.0 ... serving them over a network is a legal decision'.
e. Project memory: 'AGPL has THREE pullers' -- pymupdf enters via pymupdf4llm + docling-hierarchical-pdf + pageindex fork.

#### Code Evidence
server.py:73-94 _lifespan_with_scrape: startup HR3 gate checks openai_base_url and LLM_FALLBACK_BASE_URL against ZDR allowlist. config.py:200-221 require_zdr_compliance: core check against _ZDR_ALLOW_PATTERNS. converters/pictures.py:175-199 zdr_egress_gate: per-call wrapper that catches RuntimeError->(False,api_base). indexer.py:_persist_flat_result VLM fallback except block (line ~806): `except Exception as vlm_exc: VLM_FALLBACK_TOTAL.labels(result='error').inc()` -- compliance block indistinguishable from API error.

#### Key Files
- src/pageindex_mcp/server.py
- src/pageindex_mcp/config.py
- src/pageindex_mcp/converters/pictures.py
- src/pageindex_mcp/client/indexer.py

#### Simplification Proposal
_No proposal was generated for this zone in the source data._

---

## Cross-Cutting Themes

- Sequential remediation chains recur throughout the project's history: nearly every RFC's fix becomes the next RFC's root-cause finding (RFC-006→007→009 registry/backfill correctness; RFC-018→019→020 picture-OCR filter composition; RFC-021→022→023 verdict-gate/routing chain; RFC-024→025→026 leaf-ratio threshold saga; RFC-027→028→029→030 Arabic-recovery cascade; RFC-033→034→035→036 landscape/write-barrier cascade) — quality work here is iterative narrowing, not one-shot fixes.
- Filter/gate composition is rarely validated end-to-end: independently reasonable filters (page-coverage exemption + clip-text filter; fence-toggle + garble ratio; density floor + digit-ratio) repeatedly combine to silently zero out enrichment or content that neither filter alone would have destroyed.
- Parameter- and reason-string threading gaps make otherwise-correct detectors unfireable: expected_script never passed to garble callers (RFC-019 D2), node_garbling never recognized by the OCR-escalation conditional (RFC-018 D3b), and validate_tree's four new RFC-029 failure reasons never wired into client.py recovery routing — detection logic exists but its signal never reaches the code that should act on it.
- 'Fixed but never wired/committed' is a recurring failure class distinct from logic bugs: chunked_docling_timeout_s() (RFC-027/028), _check_bidi_coherence() (RFC-029/030), RFC-029 D6 judge calibration rules, and RFC-034 D19's enrichment-displacement guard were each fully implemented and demonstrably correct in isolation, yet inert in production because nothing called or committed them.
- Unicode normalization (NFKC) is a recurring blind spot for Arabic/RTL quality gates: it silently strips the presentation-form signal that bidi-coherence and garble detectors were built to key on, discovered independently in RFC-033 D2/RFC-034 D7 and in the separate Zone 5 NFKC audit finding — the same root cause rediscovered by different investigations.
- Threshold-widening without hysteresis or anchoring produces recurrence cycles: PASS_MAX_LEAF_RATIO was widened three times in succession (0.17→0.20→0.30) chasing jitter on different documents each time, and even RFC-025's hysteresis/prior-verdict-anchoring fix was itself defeated by an orthogonal issue (corpus reingestion wiping the prior-verdict store).
- The image_enrichment_promoted / cat_b_promoted verdict-promotion paths are a persistent Hard-Rule-5 violation surface: across many RFCs and runs they repeatedly let near-zero-content or junk-OCR documents reach PASS by checking volume (or a meta counter divergent from actual persisted content) rather than content validity, and each attempted fix (char-floor, garble-ratio check) has been independently bypassed or diluted by a later, unrelated change.
- Arabic/RTL text handling has many structurally independent failure modes that no single detector class covers: PUA-only garble heuristics miss RTL word-splitting, Presentation-Forms encoding, and embedded-Latin gibberish; garble checks inspect node.text but never node.title, leaving RTL-reversed headings permanently invisible; and OCR-language defaults silently drop Arabic unless explicitly threaded through.
- Feature branches and refactors have repeatedly reverted or silently disabled prior fixes without an explicit revert: the Run 7→8 branch replayed nearly all of Run 6's OCR/garble/hierarchy regressions simultaneously, and Zone 4's recovery.py consolidation reintroduced a throwaway-context bug — indicating the codebase lacks regression coverage strong enough to catch fix erosion during refactors.
- Non-deterministic upstream extraction (Docling PDF conversion jitter, LLM judge scoring variance) is fundamentally in tension with fixed quality-gate thresholds: every attempt to bound jitter with a wider or hysteresis-anchored threshold has been followed by a different document jittering past the new bound, and unwritten judge-calibration rules produced verdict flip-flops on byte-identical content.
- AGPL exposure narrowing has been incremental and incomplete: removing the direct PyMuPDF dependency (RFC-004) left pymupdf4llm's transitive pull unaddressed, consistent with the project's own memory note that AGPL exposure has three independent entry points that must all be verified together.
- Detection-without-remediation is a recurring pattern distinct from wiring gaps: garble/quality signals correctly fire (23/24 nodes at ratio=1.00, per-node garbling) but no downstream recovery path is triggered, so the document persists at a degraded verdict rather than being recovered or correctly rejected — tightening detection alone does not close the Hard-Rule-5 gap without a matched recovery hook.

