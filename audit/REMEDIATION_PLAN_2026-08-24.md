# Remediation Plan — 2026-08-24

**Audit:** `audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-24_POST-FIX-11.md`
**Zones:** 5 of 8 (top by priority)
**Waves:** 3
**Overall validation quality:** `needs_work` (**not approved** — see Validation Results; 4 blocker/major cross-zone conflicts must be resolved before implementation starts)

---

## Priority Scores

| Zone | Score | Severity | Bug Count | Proposal Status | Excluded |
|---|---|---|---|---|---|
| Zone 1: Tree-vs-Flat Gate Asymmetry | 56 | critical | 14 | not_implemented | no |
| Zone 3: Garble Detection Prong Blindness | 52 | critical | 13 | not_implemented | no |
| Zone 4: Picture Enrichment / OCR Filter Composition | 45 | high | 15 | not_implemented | no |
| Zone 2: Pre-Tree Text Transforms vs Table/Block Integrity | 44 | critical | 11 | not_implemented | no |
| Zone 6: Recovery Routing Wiring Gaps | 36 | high | 10 | no_proposal | no |
| Zone 5: Verdict Threshold Oscillation and Dual-CAS Divergence | 30 | high | 10 | not_implemented | no |
| Zone 7: Worker/Inspector Dual-Threshold and Timeout Race | 14.4 | medium | 6 | no_proposal | no |
| Zone 8: HR3 PII Egress Gap | 9.6 | medium | 4 | no_proposal | no |

Notes carried from scoring rationale:
- **Zone 1** (highest raw score) shares substrate (`gates.py`/`types.py`/`garble.py`) with Zones 3 and 6 — must be sequenced with them, not run standalone, or it re-fragments the same functions the other zones are touching.
- **Zone 3** has stalled 6+ remediation cycles (NFKC blindness independently rediscovered 3x: RFC-028 D2, RFC-033 D2, RFC-034 D7). Flagged as a **blocker for Waves 2–3**. Best effort-to-impact ratio in the program (~30 min for the single highest-impact fix) — schedule immediately.
- **Zone 4** shares `zdr_egress_gate` (pictures.py) with Zone 8 and `compute_verdict`/`classify_verdict` (verdict.py) with Zone 5 — a Zone 4 refactor moving I/O timing could silently undermine Zone 8's HR3 fix.
- **Zone 2** is a previously undiagnosed root cause behind both table fragmentation and flat-path digit_ratio dilution — directly linked to Zone 1; fixing it first could shrink the surface Zone 1's per-block garble check must cover.
- **Zone 6** has no current proposal (superseded by zone reorganization) and is the clearest instance of the project's chronic "implemented but never wired" anti-pattern.
- **Zone 5** and below are out of scope for this plan (only top 5 zones by priority are speced below); Zone 5's erasure-cascade reachability concern (HR2) should be verified independently regardless of scheduling.
- **Zone 8** is compliance-relevant under CLAUDE.md Hard Rules 3/4 despite the lowest formula score — should not be indefinitely deprioritized purely by score.

---

## Wave Sequence

### Wave 1
**Zones:** Zone 3: Garble Detection Prong Blindness · Zone 2: Pre-Tree Text Transforms vs Table/Block Integrity
**Shared files:** none

Zone 3 is the foundational garble-detection subsystem (`garble.py`, `gates.py`, `tree_validation.py`) whose output (`detect_garble`, `garble_prongs`) is consumed by Zones 1, 4, and 6 via `trace_path`-confirmed call chains — it must stabilize first. Zone 2 (`headings.py`, `tree_split.py`, `pipeline.py`) has zero file overlap and zero data-flow dependency with Zone 3 — `_inject_arabic_structural_headings` has in-degree 0, and `split_oversized_leaf_nodes` callees are entirely within `tree_split.py`. These two zones can safely run in parallel.

### Wave 2
**Zones:** Zone 1: Tree-vs-Flat Gate Asymmetry · Zone 4: Picture Enrichment / OCR Filter Composition
**Shared files:** `src/pageindex_mcp/client/indexer.py`

Zone 1 depends on Zone 3 because it shares `gates.py`/`garble.py` — `_gate_node_garbling`/`_gate_garbling` call `detect_garble` (confirmed at hop 2 via `trace_path`), so garble detection must be stable first. Zone 4 depends on Zone 3 because `_recover_picture_text` calls `detect_garble` at hop 2. Zones 1 and 4 both list `indexer.py` as a key file, but target disjoint methods of `CustomPageIndexClient` — Zone 1 touches `_persist_flat_result`/`_convert_to_tree` (flat-path garble), Zone 4 touches `_recover_picture_text`/`_recover_picture_results` (picture enrichment). Their other primary files (`gates.py`/`garble.py`/`types.py` vs `pictures.py`/`verdict.py`) have zero overlap.

> **Caveat (see Validation Results, blockers 1–3):** the "disjoint methods" claim for `indexer.py` does not fully hold — Zone 1 restructures `_persist_flat_result` in a way that changes the call ordering Zone 4's own wiring-check test asserts. This must be reconciled before Wave 2 starts (see below).

### Wave 3
**Zones:** Zone 6: Recovery Routing Wiring Gaps
**Shared files:** none declared (see Validation Results — Zone 6 in fact edits the same `indexer.py:1197` line as Zone 3)

Zone 6 depends on both Wave 1 and Wave 2 zones. It shares `gates.py`/`types.py` with Zone 1 (`decide_route`/`finalize_gate_and_route` consumed by `RecoveryMixin` methods, confirmed via `trace_path` — `_recover_rtl_repair` calls `finalize_gate_and_route` at hop 2). It shares `tree_validation.py` with Zone 3 (`validate_tree` early-exit reordering). It also implicitly depends on `client/recovery.py` (`RecoveryMixin`), structurally central to Zones 1 and 3. Zone 6 must wait for the gate table (Zone 1), garble prongs (Zone 3), and their shared types to be stable before rewiring recovery dispatch.

---

## Fix Specs

### Zone: Zone 3: Garble Detection Prong Blindness (NFKC, Script Threading, Title Inspection) (wave 1, priority 2)

**Mechanism to eliminate:** `ScriptContext` (carrying `had_presentation_forms` and `dominant_script`) is computed once per document but degraded back to a bare string at 6+ call sites that reconstruct `ScriptContext` with `had_presentation_forms=False`. The `_GateFn` type alias declares `str | None` for the 3rd parameter (`types.py:203-207`, `gates.py:250-253`) while `validate_tree` actually passes a `ScriptContext` object (`tree_validation.py:290`). `_gate_node_garbling` (`gates.py:87-92`) receives the `ScriptContext` but treats it as `str | None` and builds a fresh `ScriptContext(had_presentation_forms=False)`, permanently losing the presentation-forms signal. `ScriptContext.from_document` at `indexer.py:1177` is called with `state.md_content` (post-NFKC) instead of the raw PDF text layer from the fitz probe (`indexer.py:419`), so presentation-form codepoints are already decomposed before the scan runs. The recovery loop at `indexer.py:1197` passes `expected_script` but never `script_context`. The `latin_gibberish` prong (`garble.py:389-393`) is skipped entirely when `expected_script is None`. The `digit_ratio` prong operates on the whole blob with a 500-char floor, so numeric junk confined to one node dilutes below 0.60.

**Strategy:** Thread `ScriptContext` (computed once pre-NFKC from the raw fitz probe text) through all garble/gate call sites: (A) capture the fitz probe `raw_text` into `ScriptContext` before conversion, (B) fix the `_GateFn` type alias to accept `ScriptContext` and update all 10 gate function signatures, (C) eliminate the 6 sites reconstructing `ScriptContext` with `had_presentation_forms=False`, (D) pass `script_context` through the recovery loop, (E) fix `latin_gibberish` to use document-level script when text self-classifies as Latin, (F) add per-node `digit_ratio` checking inside `_garble_check_nodes` with a lowered 50-char floor. Pure parameter threading and type correction — no new abstractions or files.

**Code targets:**
| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `client/indexer.py` | 419-434, 753-756, 783, 1126-1180, 1197 | Capture fitz probe raw_text pre-NFKC; thread script_context through recovery loop and flat garble gate | Store `_fitz_raw_text` at line 419; re-derive `script_context = ScriptContext.from_document(filename, raw_text=_fitz_raw_text)` after the probe block; forward this at line 1177 instead of re-scanning post-NFKC `md_content`; change recovery dispatch at 1197 to pass `script_context=script_context`; replace hardcoded `had_presentation_forms=False` at 753-756 and 783 with `script_context.had_presentation_forms` | validate_tree must still run before save_doc (CLAUDE.md HR5). Fallback to empty string if fitz probe fails. Do not change `_convert_to_tree` signature. |
| `helpers/types.py` | 203-207 | Update `_GateFn` type alias 3rd param from `str \| None` to `ScriptContext` | Change Callable signature; add `ScriptContext` import from `..script` | `GateSpec.gate_fn` uses this type — all 10 gate functions in gates.py must match. `_GateFn` is private, no external consumers. |
| `helpers/gates.py` | 37-253 | Update duplicate `_GateFn` alias and all 10 gate function signatures; fix `_gate_node_garbling` to use received ScriptContext directly | Rename param `expected_script: str \| None` → `script_ctx: ScriptContext` across all gates; access `script_ctx.dominant_script` where a string was needed; in `_gate_node_garbling` (70-104) DELETE lines 87-92 (doc_script inference + fresh ScriptContext construction), pass `script_ctx` directly to `_garble_check_nodes` | Gate functions are called only from `validate_tree` which already passes ScriptContext. `_eligible_*` predicates and GATES list declarations are unaffected. |
| `helpers/tree_validation.py` | 269-282, 290 | Ensure `validate_tree` passes ScriptContext (not bare string) to `TreeSignals.from_tree` and GATE_TABLE | Keep the bare-string fallback branch (backward-compat for offline tooling) but at 278-282 pass `_script_ctx` instead of raw `expected_script` to `TreeSignals.from_tree` | Public signature stays `expected_script: str \| None \| ScriptContext = None`. Offline callers (preprocess_client.py, promotion_sweep.py, verify_corpus.py) keep the `had_presentation_forms=False` fallback — do not force ScriptContext-only callers. |
| `helpers/garble.py` | 389-401, 595-673 | Fix `latin_gibberish` self-classification loop; add per-node digit_ratio in `_garble_check_nodes`; remove deprecated positional params | When `expected_script is None`, infer script from `norm_blob` via `_infer_script`, evaluate `latin_gibberish` if inferred script is non-Latin. After the `detect_garble` node call, add a direct per-node digit-ratio check (50-char floor, same 0.60 threshold) marking `node_garbled=True`. Remove deprecated positional `page_script`/`expected_script` params from `_garble_check_nodes` signature; make `script_context`/`config` required | `garble_prongs` always receives `expected_script` from `script_context.dominant_script` once threaded. Do not lower the bulk prong's 500-char floor — per-node check is additive. Verify no other call site passes positional `page_script`/`expected_script`. |
| `helpers/garble.py` | 694-728 | Fix `_garble_ratio` fallback ScriptContext construction | Keep `had_presentation_forms=False` only as true last-resort fallback (no caller has presentation-forms info) | `TreeSignals.from_tree` (tree_validation.py:209) passes script_context — fallback becomes dead code for primary path once callers are updated; keep for test callers. |

**Wiring checks:**
- `ScriptContext` import required in: `gates.py`, `tree_validation.py`, `garble.py`, `indexer.py`, `recovery.py`, `types.py`.
- Textual presence check (not import-graph, per Validation Results minor issue): `indexer.py` recovery dispatch line contains `script_context=` keyword; `_flat_garble_ctx` construction uses `script_context.had_presentation_forms`.
- `_gate_node_garbling` called from `gates.py`.
- Call-site coverage check (per Validation Results minor issue): all callers of `_garble_check_nodes` (gates.py and the recursive self-call in garble.py) pass the new required keyword args — add explicit enumeration, not just prose claim.

**Test requirements:**
- `tests/test_garble.py` — `_gate_node_garbling` uses `ScriptContext.had_presentation_forms=True` correctly on NFKC-decomposed Arabic text (regression).
- `tests/test_garble.py` — per-node digit_ratio catches an 80%-digit 60-char node amid clean prose that does NOT trigger the bulk whole-blob check (dilution-gap regression).
- `tests/test_garble.py` — `latin_gibberish` fires on Arabic text with embedded Latin gibberish when `expected_script=None` (self-classification, regression); pure-Latin text with `expected_script=None` does not fire.
- `tests/test_rfc_garble_gate.py` — all 10 gate functions accept `ScriptContext` as 3rd param without TypeError (contract).
- `tests/test_rfc_garble_gate.py` — `validate_tree` threads `had_presentation_forms` end-to-end into `TreeGateResult.all_defects` (regression).
- `tests/test_garble.py` — `_garble_check_nodes` raises TypeError on old bare-positional call pattern (contract).
- `tests/test_rfc_recovery.py` — recovery methods receive `script_context` keyword from the recovery loop dispatch (wiring).

**Corpus validation:** 6 Arabic legal PDFs (سياسة حوكمة, حقوق الإنسان, مرسوم بقانون اتحادي رقم 13/2022, رقم 33/2021, قرار مجلس الوزراء رقم 1/2022, اتفاقية مستوى الخدمة). Expected direction: **improve**. Spot-check count: 6.

**Estimated complexity:** medium.

**Post-implementation note (per validation, minor issue #16):** this zone has documented pattern-risk — prior attempts at this exact wiring have stalled or silently regressed 3+ times. After landing, run corpus revalidation specifically confirming `ScriptContext.had_presentation_forms` reaches `_gate_node_garbling` end-to-end, not just that the parameter type changed.

---

### Zone: Zone 2: Pre-Tree Text Transforms vs Table/Block Integrity (wave 1, priority 4)

**Mechanism to eliminate:** Three independent line-by-line text transforms (heading injection in `headings.py`, ordinal splitting in `tree_split.py`, TOC stripping in `table_stitch.py`) fracture markdown pipe-tables before `_segment_table_nodes` — the only function with proper pipe-row detection — ever sees them. No shared table-boundary primitive exists: `_segment_table_nodes` defines its own `_is_pipe_row`/`_is_sep_row` as inner closures, `tables.py` defines a weaker `_flat_is_pipe_row`, and heading-injection/ordinal-splitter functions have zero pipe-row awareness. A fractured table falls below the row-count floor (5 portrait / 10 landscape) and is silently merged into prose, destroying structure and diluting digit_ratio below the 0.60 garble threshold — repeating on every OCR/garble/VLM retry since `prepare_tree` re-runs from scratch.

**Strategy:** Extract `_is_pipe_row`/`_is_sep_row` from `_segment_table_nodes` inner closures to module-level functions in `tree_split.py`. Add `compute_table_spans(lines) -> list[tuple[int,int]]` (contiguous pipe-table blocks, gated on presence of a separator row) and `line_in_table_span(line_idx, spans) -> bool`. Gate the three heading-injection functions and `split_oversized_leaf_nodes`'s ordinal matching on table spans. Make `_segment_table_nodes` reference the module-level functions. Incremental, independently revertible: (1) refactor + new utility (zero behavior change), (2) gate ordinal splitter (highest impact), (3) gate heading injection (lower risk).

**Code targets:**
| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `helpers/tree_split.py` | 485-490 | Extract `_is_pipe_row`/`_is_sep_row` (and `_SEP_RE`/`_PIPE_START`) to module scope | Move definitions before `_segment_table_nodes`; update it to reference module-level versions | Semantics must be IDENTICAL — `_is_pipe_row` requires pipe at both start AND end (stricter than `tables.py`'s `_flat_is_pipe_row`). Existing tests must pass unchanged. |
| `helpers/tree_split.py` | ~480 (before `_segment_table_nodes`) | Add `compute_table_spans` and `line_in_table_span` utilities (~25 lines) | Scan for contiguous pipe-row blocks, trim trailing blanks, qualify as table only if it contains ≥1 separator row (mirrors `_segment_table_nodes`'s own heuristic) | Must produce the same span ranges as `_segment_table_nodes`'s internal `table_spans` (minus quality thresholds — detects PRESENCE not QUALITY). |
| `converters/headings.py` | 141-170, 224-237, 252-265 | Gate all 3 heading-injection functions on table spans | Compute `table_spans` from `md.split('\n')`; in each loop, `if line_in_table_span(i, table_spans): out.append(line); continue`; import from `..helpers.tree_split` | Non-table lines behave identically. Density-guard revert logic unchanged. `_injected_count` must not count table-skipped lines. |
| `helpers/tree_split.py` | 431-432 (inside `split_oversized_leaf_nodes`) | Gate ordinal matching to exclude matches inside table spans | Compute `table_spans` on original text lines; filter `all_matches` via a `_match_in_table_span` helper mapping folded-offset matches back through `idx_map` to original line index | Non-table matches unaffected. Roman-numeral filtering, TOC check, and fallback split paths unchanged. |
| `helpers/__init__.py` | 162-187 | Export `compute_table_spans`/`line_in_table_span` | Add to tree_split import block and `__all__` | No existing imports/exports removed. |

**Wiring checks:**
- Import: `compute_table_spans`, `line_in_table_span` required in `converters/headings.py`, `helpers/__init__.py`.
- Call: `compute_table_spans`, `line_in_table_span` required in `converters/headings.py`, `helpers/tree_split.py`.
- Call: `_is_pipe_row`, `_is_sep_row` required in `helpers/tree_split.py`.

**Test requirements:**
- `tests/test_zone2_table_spans.py` — `compute_table_spans` exhaustiveness: single table, two disjoint tables, false-positive guard (pipe-like lines without separator row), trailing-blank trimming, empty input, no-pipe input (exhaustiveness).
- `tests/test_zone2_table_spans.py` — `compute_table_spans` produces identical spans to `_segment_table_nodes`'s internal `table_spans` on ≥3 real corpus cases, portrait and landscape (contract).
- `tests/test_zone2_table_spans.py` — heading-injection functions (Arabic/German/English) do NOT promote markers inside pipe-tables (regression, 3 cases).
- `tests/test_zone2_table_spans.py` — `split_oversized_leaf_nodes` does not split at ordinal markers inside pipe-tables (regression).
- `tests/test_zone2_table_spans.py` — non-regression: heading injection/ordinal splitting still work on non-table text (3 cases).
- `tests/test_zone2_table_spans.py` — integration: `prepare_tree` preserves table integrity end-to-end (large pipe-table with ordinal markers in cells segments as Table node, not fractured beforehand).

**Corpus validation:** marsoom_13 (~80% content loss from table fracturing), marsoom_33 (node_count collapse 125→58), Arabic/German/English legal docs with pipe-tables containing ordinal markers, landscape docs with table fragmentation (RFC-035 D2/RFC-036 D0), Run 12→13 `low_content_density` ERROR documents. Expected direction: **improve**. Spot-check count: 5.

**Estimated complexity:** medium.

---

### Zone: Zone 1: Tree-vs-Flat Gate Asymmetry (wave 2, priority 1)

> **Do not implement as specced without first resolving the blockers in Validation Results** (contradictory edits to `indexer.py:753-756`/`783` with Zone 3; staleness risk from moving `_apply_picture_enrichment` ahead of the garble gate; conflicting wiring-test ordering with Zone 4). The spec below is preserved as drafted; implementers must apply the corrections noted inline.

**Mechanism to eliminate:** The tree pipeline has a formal GATES table (10 active gates, `gates.py:329-411`, out of 12 total `GateSpec` entries including 2 dead gates) with per-node garble checking via `_garble_check_nodes` and exhaustiveness assertions, while the flat pipeline has exactly ONE ad-hoc whole-blob `detect_garble()` call in `_persist_flat_result` (`indexer.py:752-764`) invisible to the GATES exhaustiveness machinery. The flat garble check evaluates the FULL document blob before `route_and_extract_flat` splits it into blocks, so localized corruption (e.g. a numeric-junk table amid normal prose) dilutes below the 0.60 digit_ratio threshold at the 500-char floor. The flat `ScriptContext` also hardcodes `had_presentation_forms=False` rather than threading it from the document-level context. Any future gate added to GATES silently does NOT protect flat-routed documents.

**Strategy:** Restructure `_persist_flat_result` so block decomposition happens before the garble gate, with the gate itself operating per-block rather than whole-blob. Introduce `_garble_check_flat_blocks` in `garble.py` mirroring `_garble_check_nodes`' per-node granularity. Thread `had_presentation_forms` from the canonical document-level `ScriptContext` (owned by Zone 3 — see correction below) into the flat `ScriptContext`. Add a `FLAT_GATE_COVERAGE` compile-time assertion in `gates.py` ensuring every RAISE-policy defect mapped to `Route.FLAT` has a registered flat-path quality check. Delete the superseded whole-blob `detect_garble` call.

**Code targets:**
| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `helpers/garble.py` | after 673 | Add `_garble_check_flat_blocks(blocks, *, script_context, config, original_defect=None) -> GarbleReport` (~25 lines) | Imports `_flat_block_primary_text` from `helpers.flat`; iterates blocks, calls `detect_garble` on each block's primary text with `blob_kind=BlobKind.RAW_MARKDOWN`; `is_garbled=True` if ANY block fires; `fired_prongs` = union across blocks; `garble_ratio` = fraction of blocks that fired. 500-char floor applies per-block, eliminating dilution | Must NOT change `detect_garble`/`garble_prongs` signatures or `_garble_check_nodes` behavior. Must import `_flat_block_primary_text` (single source of truth, `helpers/flat.py:175-184`), not duplicate it. |
| `client/indexer.py` | 715-806 | Restructure `_persist_flat_result`: decompose blocks before the garble gate; per-block check replaces whole-blob check | **Correction (see Validation Results, major issue):** do NOT move the full `_apply_picture_enrichment` call (with its side effects — doc_id minting, VLM description, `route_and_extract_flat`) ahead of the gate. Instead call the pure `route_and_extract_flat(flat_md)` directly to obtain blocks for `_garble_check_flat_blocks`, keep `_apply_picture_enrichment` in its current position (after the gate), and ensure VLM-recovered `flat_md` flows through the unchanged enrichment call afterward | Must NOT change `_persist_flat_result`'s signature. Must NOT alter VLM fallback's overall try/accept/reject logic. Must preserve `splice_figure_markers` before block splitting. Must not leave `doc_id`/`blocks`/`content_class` stale relative to VLM-replaced `flat_md`. |
| `helpers/gates.py` | after 449 | Add `FLAT_GATE_COVERAGE` exhaustiveness assertion (~12 lines) | Frozen dict mapping each `TreeDefect` routing to FLAT to its flat-path quality-check callable name; import-time assertion that every GATES entry routing to FLAT has an entry | Must NOT modify GATES list, REASON_POLICY, GATE_TABLE, HARD_FAIL_DEFECTS derivations, GateSpec, or TreeDefect. Must tolerate dead gates. |
| `client/indexer.py` | 783-789 | Thread `had_presentation_forms` into VLM fallback garble re-check | Replace hardcoded `had_presentation_forms=False` with the document-level `ScriptContext.had_presentation_forms` (per Zone 3's canonical source — **this line is owned by Zone 3; Zone 1 must verify, not re-edit**, per Validation Results blocker #1) | Must NOT change VLM fallback error handling or metric labeling. |
| `helpers/__init__.py` | 73-98, 231-300 | Export `_garble_check_flat_blocks` | Add to `.garble` import block and `__all__` | Must NOT remove/rename existing exports. |

**Canonical source correction (resolves Validation blocker #1):** `indexer.py:753-756` and `783` are edited ONCE, by Zone 3, using the document-level `ScriptContext` computed pre-NFKC from the fitz probe — this is the stated architectural goal. Zone 1's "Phase 2" for these lines becomes a **verify-only step**: confirm the hardcode is already replaced by Zone 3; do not re-edit with `state.rtl_decision.had_presentation_forms`.

**Wiring checks:**
- Import: `_garble_check_flat_blocks` required in `helpers/__init__.py`, `client/indexer.py`.
- Call: `_garble_check_flat_blocks` required in `client/indexer.py`.
- `FLAT_GATE_COVERAGE`: **corrected per Validation minor issue** — this is defined in `gates.py`, not imported by it; check should instead verify it is referenced by `tests/test_zone1_flat_gate_asymmetry.py`, or drop the check if it has no external consumer beyond its own import-time assertion.
- Import: `_flat_block_primary_text` required in `helpers/garble.py`.

**Test requirements:**
- `tests/test_zone1_flat_gate_asymmetry.py` — per-block garble detection catches a garbled TABLE block amid clean prose blocks (contract).
- `tests/test_zone1_flat_gate_asymmetry.py` — dilution immunity: whole-blob digit-ratio passes but one block individually exceeds 0.60 — `_garble_check_flat_blocks` catches it (regression, RFC-027 #5330 / RFC-026).
- `tests/test_zone1_flat_gate_asymmetry.py` — `had_presentation_forms` threading verified via mocked `detect_garble` call (regression, RFC-019 D2 / RFC-028 D2 class).
- `tests/test_zone1_flat_gate_asymmetry.py` — `FLAT_GATE_COVERAGE` exhaustiveness: every FLAT-routing `TreeDefect` has a coverage entry (exhaustiveness).
- `tests/test_zone1_flat_gate_asymmetry.py` — `short_text_prior_garble` short-circuit fires at block granularity (regression, RFC-025 D2).
- `tests/test_zone1_flat_gate_asymmetry.py` — empty/whitespace-only blocks skipped, not counted as garbled (contract).

**Corpus validation:** marsoom-13, Doc 21, Docs 3 & 9, GHV-TKV-Tarif, cabinet_resolution_no_21, siyasat-hawkama, world-stats-pocketbook. Expected direction: **improve**. Spot-check count: 7.

**Estimated complexity:** large.

**Line-number staleness warning (per Validation Results):** wave-2 line numbers cited above reflect the pre-Zone-3 baseline. Zone 3 lands first (wave 1) and shifts offsets in `gates.py`, `garble.py`, and `indexer.py`. Anchor all edits by symbol/context (e.g. "after `_garble_check_nodes`", "the `_flat_garble_ctx` construction in `_persist_flat_result`"), re-resolving exact line numbers at implementation time.

---

### Zone: Zone 4: Picture Enrichment / OCR Filter Composition (wave 2, priority 3)

**Mechanism to eliminate:** A filter-chain composition where independently-tuned, sequentially-composed filters (coverage gate, clip-text gate, decorative gate, text-layer probe, containment check, OCR-min-chars threshold) combine via shared mutable state to silently zero out enrichment content that no single filter alone would destroy. I/O-dependent metadata extraction is interleaved with the disposition switch inside `_recover_picture_text` (`pictures.py:705-957`), so one filter's side effect alters another filter's input. The two splice entry points (`splice_picture_text_for_tree`, `splice_figure_markers`) share ordinal-alignment logic but diverge on landscape-fallback filtering, and standalone images create synthetic `PictureResult`s with duplicated content that never route through `_enrich_image_blocks`.

**Strategy:** Three-phase separation of concerns: (A) extract all I/O-dependent metadata into a frozen `RegionMetadata` dataclass populated by a new `_scan_regions` function (single fitz.Document pass), (B) feed frozen metadata to the already-pure `_classify_region` with no I/O interleaving, (C) execute dispositions in a third phase that cannot influence classification. Unify the two splice entry points into `splice_pictures` with a mode parameter. Fix the standalone-image synthetic `PictureResult` factory to use per-marker independent instances with distinct `png_bytes` references.

**Code targets:**
| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `picture_plane.py` | 115-138 | Add frozen `RegionMetadata` dataclass (~25 lines) | Fields: index, page_index, coverage, has_own_text, clip_text, clip_text_contained, rect_width, rect_height, rect, rotation. Immutable after construction | Must NOT import fitz or any I/O library. `PictureRegion`, `RegionClassification`, `_classify_region` unchanged. |
| `picture_plane.py` | 269-336 | Add `classify_from_metadata(meta, fullpage_count, config)` factory (~10 lines) | Pure delegation unpacking `RegionMetadata` into existing `_classify_region` kwargs | `_classify_region` signature/logic unchanged — already pure and tested. |
| `converters/pictures.py` | 705-957 | Extract `_scan_regions`; refactor `_recover_picture_text` into a three-phase pipeline | `_scan_regions(pdf_path, regions, md, expected_script, gate_config) -> list[RegionMetadata]` opens one fitz.Document, computes coverage/text-layer/clip-text/containment per region (~50 lines). Refactor `_recover_picture_text`: Phase 1 scan, Phase 2 classify (pure), Phase 3 execute dispositions (separate fitz handle for cropping). `fullpage_ocr_region_count` moves to a pre-pass count over classifications | Ordinal alignment between regions and RegionMetadata preserved. `zdr_egress_gate` (175-199, shared with Zone 8) must NOT move or change. `_text_layer_has_content` remains independently callable (also used by `_document_level_text_fallback`). `_crop_page_region` stays separate, called in Phase 3. |
| `converters/pictures.py` | 966-1049 | Unify `splice_picture_text_for_tree`/`splice_figure_markers` into `splice_pictures(md, pics, *, mode='tree'|'flat')` | `mode='tree'` delegates to `bind_markers(md, pics, inject_chart_text=True)`; `mode='flat'` runs current `splice_figure_markers` logic. Keep both old names as thin wrappers | All 18 existing callers must continue resolving without import changes. `_spliced_into_markdown` flag preserved for flat path. `bind_markers` in picture_plane.py unchanged. **Per Validation blocker #2: the wiring test asserting call ordering must be updated — see below.** |
| `client/indexer.py` | 666-674 | Fix standalone image synthetic `PictureResult` factory | Replace list comprehension with explicit factory copying `png_bytes` via `bytes(img_bytes)` per instance; comment citing RFC-019 D0 | Must NOT change marker_count dedup regex or `MIN_STANDALONE_IMAGE_MD_CHARS` gate. `_IMAGE_EXTS` remains canonical. |
| `client/images.py` | 152-224 | Wire `_apply_picture_enrichment`'s `splice_markers=True` path to unified `splice_pictures` | Replace `splice_figure_markers` call with `splice_pictures(flat_md, pic_results, mode='flat')` | `splice_markers=False` path (called from `_persist_flat_result`) unchanged. Function signature unchanged. |
| `helpers/verdict.py` | 36-63 | Verify `compute_image_enrichment_ratio` handles new `SkipReason` values | No code change — add regression test covering full `SkipReason` enum | `classify_verdict` (in-degree 54) signature must NOT change. |

**Correction required before implementation (resolves Validation blocker #2):** the wiring test requirement below asserting `splice → garble gate → _apply_picture_enrichment(splice_markers=False) → _enrich_image_blocks` describes the **pre-Zone-1 ordering**. Zone 1 reorders `_persist_flat_result` to decompose blocks (via `route_and_extract_flat`) before the per-block garble gate, then runs `_apply_picture_enrichment` after the gate (per Zone 1's corrected strategy above). Zone 4's wiring test must be rewritten to assert the **post-Zone-1 ordering**: `splice → route_and_extract_flat (block decomposition) → per-block garble gate (_garble_check_flat_blocks) → _apply_picture_enrichment(splice_markers=False) → _enrich_image_blocks`. This ordering assertion should live in Zone 1's test file (it tests Zone 1's restructuring) with Zone 4 asserting only that its own splice/enrichment internals are called correctly once invoked.

**Wiring checks:**
- Import: `RegionMetadata` required in `converters/pictures.py`.
- Call: `_scan_regions` required in `converters/pictures.py`.
- Call: `splice_pictures` required in `converters/pictures.py`, `client/images.py`.
- Call: `classify_from_metadata` required in `converters/pictures.py`.
- **Added per Validation minor issue:** Import: `classify_from_metadata` required in `converters/pictures.py` (previously only a call-check existed, no import-check).

**Test requirements:**
- `tests/test_region_metadata.py` — `RegionMetadata` frozen immutability, field types, equality/hashing; `classify_from_metadata` delegation matches `_classify_region` truth table (contract).
- `tests/test_scan_regions.py` — ordinal alignment with `_collect_picture_regions`; fitz handle opened once, closed on exception; `_text_layer_has_content`/`_clip_text_contained` called correctly; garbled text layer → `has_own_text=False`; degenerate regions produce no entry (exhaustiveness).
- `tests/test_image_blocks.py` — coverage+clip-text gates don't interact regardless of ordering (RFC-019 D1 + RFC-018 D0); `fullpage_ocr_region_count` cap enforced as pre-pass, not mid-loop mutation; clip_text extraction not blocked by page-level text check (RFC-024 D1 Human Rights regression); standalone image `png_bytes` independence (regression).
- `tests/test_ocr_decision.py` — `splice_pictures(mode='tree')` ≡ `splice_picture_text_for_tree`; `splice_pictures(mode='flat')` ≡ `splice_figure_markers`, for identical inputs; landscape-fallback and marker/region-mismatch handling consistent across modes (contract).
- `tests/test_image_blocks.py` — **corrected wiring test** (see correction above): production call ordering reflects post-Zone-1 restructuring (wiring).

**Corpus validation:** Human Rights (clip_text RFC-024 D1, 503k→382 chars), GHV-TKV-Tarif.pdf (OCR splice RFC-021/022 B3), Doc 3 (combined filter zeroing 1/4→0/3), Doc 9 (3→0 enrichment), Docs 7/17/20/21 (forced OCR reclassification, 0 PictureResults). Expected direction: **improve**. Spot-check count: 5.

**Estimated complexity:** large.

---

### Zone: Zone 6: Recovery Routing Wiring Gaps (wave 3, priority 5)

**Mechanism to eliminate:** The GateSpec-driven recovery loop (`indexer.py:1190-1197`) dispatches recovery via string-name `getattr` lookup against `RecoveryMixin` methods. Three structural problems: (1) exhaustiveness assertions only enforce recovery wiring for `RETRY_OCR`/`RETRY_RTL` policies — `RAISE`, `PERSIST_FAIL`, `CAP_MARGINAL` defects silently fall through with no recovery and no assertion catching the gap; (2) string-based dispatch has no compile-time validation that `recovery_fns` method names exist on `RecoveryMixin`; (3) `PERSIST_FAIL` defects (`EMPTY_NODE_CONTAMINATION`, `LOW_CONTENT_DENSITY`, `SUSPECT_DENSITY`) and `REORDERED` (`RAISE` policy) have `hard_fail=True` but zero recovery paths — OCR-recoverable PDFs are persisted with FAIL verdict or rejected without any recovery attempt.

**Strategy:** (A) Extend the exhaustiveness assertion in `gates.py` to require every active `GateSpec` with a non-`OK`/`CAP_MARGINAL` policy to either have `recovery_fns`+`recovery_eligible` OR an explicit `recovery_waived=True` flag, so unrecoverable defects are a conscious declaration, not an accidental gap. (B) Add a startup-time assertion that every `recovery_fns` string name resolves to an actual method on `RecoveryMixin`. (C) **Correction (resolves Validation blocker #3 / major issue):** the `script_context` threading into the recovery dispatch call at `indexer.py:1197` is **owned entirely by Zone 3** (wave 1) — Zone 6 does NOT re-edit this line. Zone 6's scope is limited to `GateSpec.recovery_waived`, the extended exhaustiveness assertions, and `validate_recovery_method_names`.

**Code targets:**
| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `helpers/types.py` | 210-242 | Add `recovery_waived: bool = False` field to `GateSpec` | New field with default, backward-compatible on frozen dataclass | Must not change existing fields/order. All existing `GateSpec` instantiations without `recovery_waived` continue to work. |
| `helpers/gates.py` | 329-449 | Set `recovery_waived=True` on REORDERED, BIDI_DEGRADED, EMPTY_NODE_CONTAMINATION, LOW_CONTENT_DENSITY, SUSPECT_DENSITY; extend exhaustiveness assertion | Add `recovery_waived=True` to the 5 GateSpec entries. Add a new assertion block: for every active gate (`gate_fn is not None`) with policy not in `(OK, CAP_MARGINAL)`, either `recovery_fns` is non-empty and `recovery_eligible` is set, OR `recovery_waived=True` | GATES list ordering (severity-load-bearing) unchanged. Dead gates excluded from the new assertion. Existing assertions unchanged. |
| `helpers/gates.py` | 517-625 | Add `validate_recovery_method_names()` | Import `RecoveryMixin` from `client.recovery` via `importlib` (same pattern as `validate_feature_wirings`); assert every `recovery_fns` string resolves to a `RecoveryMixin` method; call from `validate_feature_wirings()` | No top-level import from `client.recovery` into `gates.py` (circular import hazard). Must not fail on dead gates with empty `recovery_fns`. |
| ~~`client/indexer.py:1196-1197`~~ | — | **Removed from Zone 6 scope** (Validation blocker #3) | This edit is owned by Zone 3 (wave 1). Zone 6 only adds a wiring check verifying the dispatch call (already updated by Zone 3) matches what `recovery.py` method signatures expect | — |

**Wiring checks:**
- **Corrected per Validation minor issue:** `recovery_waived` is a dataclass field (defined in `types.py`), not an importable symbol — replace the "import" check with a usage check confirming `GateSpec(..., recovery_waived=True)` appears in `gates.py` for the 5 named entries.
- Call: `validate_recovery_method_names` required in `helpers/gates.py`, invoked from `validate_feature_wirings()`.
- **Removed per Validation minor issue:** the `RecoveryMixin` import check against `client/indexer.py` is pre-existing wiring (verified: `recovery.py:79` defines it, `indexer.py:280` imports it, `indexer.py:287` uses it as a base class) and validates nothing new introduced by this zone — dropped from scope.
- **Added:** verify `indexer.py`'s recovery dispatch call (post-Zone-3) passes `script_context` in the form Zone 3 landed it, as a cross-zone consistency check rather than a new edit.

**Test requirements:**
- `tests/test_recovery.py` — every active GateSpec with non-`OK`/`CAP_MARGINAL` policy has `recovery_fns`+`recovery_eligible` OR `recovery_waived=True` (exhaustiveness).
- `tests/test_recovery.py` — all `recovery_fns` strings resolve to callable `RecoveryMixin` methods (contract).
- `tests/test_recovery.py` — REORDERED, BIDI_DEGRADED, EMPTY_NODE_CONTAMINATION, LOW_CONTENT_DENSITY, SUSPECT_DENSITY all have `recovery_waived=True` AND empty `recovery_fns` (regression guard).
- `tests/test_recovery.py` — a new GateSpec with `RETRY_OCR` policy and empty `recovery_fns` (no waiver) triggers AssertionError at import time (contract).
- `tests/test_recovery.py` — a new GateSpec with a `recovery_fns` entry naming a nonexistent `RecoveryMixin` method triggers AssertionError from `validate_recovery_method_names()` (contract).
- `tests/test_gate_table.py` — `recovery_waived` gates have no `recovery_fns` AND no `recovery_eligible` (inverse guard against contradictory declarations) (contract).

**Corpus validation:** no specific affected documents identified. Expected direction: **stable**. Spot-check count: 5.

**Estimated complexity:** medium.

**Chronic pattern flag (per scoring rationale):** this zone is the clearest instance of the project's "implemented but never wired/committed" anti-pattern (`chunked_docling_timeout_s`, `_check_bidi_coherence`, RFC-034 D19 enrichment guard) — 9 of 12 historically unwired symbols persist across cycles per memory. Post-implementation verification should specifically confirm `validate_recovery_method_names()` actually executes at server startup, not merely that it is defined.

---

## Validation Results

**Overall quality:** `needs_work` — **plan is NOT approved as drafted.** The corrections embedded inline above (in Zone 1, Zone 4, and Zone 6 sections) must be applied before implementation begins. Summary of issues:

### Blockers
1. **Zone 1 vs Zone 3 — contradictory edits to `indexer.py:753-756` / `783`.** Zone 3 (wave 1) replaces `had_presentation_forms=False` with `script_context.had_presentation_forms`; Zone 1 (wave 2) independently specs the same hardcode replaced with `state.rtl_decision.had_presentation_forms` — a different, differently-timed value. **Resolution applied above:** Zone 3 owns these lines exclusively; Zone 1's corresponding target is verify-only.
2. **Zone 4 vs Zone 1 — contradictory wiring-test call ordering.** Zone 4's wiring test asserts `splice → garble gate → _apply_picture_enrichment → _enrich_image_blocks`; Zone 1 (same wave) reorders `_persist_flat_result` to `splice → block decomposition → per-block garble gate → enrichment`, breaking Zone 4's test or blocking Zone 1's landing. **Resolution applied above:** Zone 4's wiring test rewritten to assert the post-Zone-1 ordering, with the ordering assertion itself relocated to Zone 1's test file.
3. **Zone 1's original strategy** (move the full `_apply_picture_enrichment` call, with its side effects, ahead of the garble gate) creates data staleness: `doc_id`, `blocks`, `content_class` computed pre-gate become stale after VLM fallback replaces `flat_md`. **Resolution applied above:** Zone 1 instead calls pure `route_and_extract_flat(flat_md)` for block decomposition, keeping `_apply_picture_enrichment` in its current post-gate position.
4. **Zone 3 vs Zone 6 — duplicate incompatible edits to `indexer.py:1197`.** Zone 3 specs a keyword-arg change (`script_context=script_context`); Zone 6 specs a positional-arg change (`script_context`) to the identical line, with neither zone referencing the other. **Resolution applied above:** collapsed into a single edit owned by Zone 3 (keyword form); Zone 6's corresponding target removed and replaced with a verify-only wiring check.

### Major issues
- **Zone 1** moving `_apply_picture_enrichment` ahead of the gate pulls doc_id minting / VLM description / `route_and_extract_flat` side effects ahead of a gate that can reject/replace `flat_md` — addressed via blocker #3's resolution.
- **Zone 1** cross-wave line-number staleness: wave-2/3 specs cite pre-wave-1 line numbers in files wave 1 rewrites. Flagged inline in Zone 1's spec — implementers must anchor by symbol, not line number, for all wave 2/3 edits.
- **Zone 6** `indexer.py:1197` duplication — see blocker #4.
- **Zone 1** `FLAT_GATE_COVERAGE` wiring check is vacuous as originally written (checks that gates.py imports a symbol it defines itself) — corrected above to target the test file or be dropped.

### Minor issues
- Zone 1: `FLAT_GATE_COVERAGE` malformed check — corrected above.
- Zone 6: `recovery_waived` malformed import check (it's a dataclass field, not an importable symbol) — corrected above.
- Zone 3: `script_context`/`had_presentation_forms` wiring checks are unverifiable as import/call checks (local variable and attribute name, not importable symbols) — corrected above to textual-presence checks.
- Zone 1: mechanism text says "10-entry GATES table" when GATES actually has 12 entries (10 active + 2 dead) — corrected to "10 active gates out of 12 GateSpec entries" throughout this plan.
- Zone 3 / Zone 6 cross-zone conflict on `indexer.py:1197` — see blocker #4.
- Zone 1: `FLAT_GATE_COVERAGE` has no wiring check confirming any consumer outside gates.py's own assertion actually uses it — either drop the check or retarget it (corrected above).
- Zone 4: `classify_from_metadata` has a call-check but no import-check in `converters/pictures.py` — added above.
- Zone 6: `recovery_waived` check_type should reflect field/keyword usage, not import — corrected above.
- Zone 6: `RecoveryMixin` import check against `indexer.py` validates pre-existing wiring, not anything new introduced by this zone — dropped from scope above.
- Zone 3: `_garble_check_nodes` signature-breaking change (removing deprecated positional params) has no enumerated call-site wiring check, only prose claiming call sites were migrated — added an explicit call-check requirement above.
- Zone 1/3: per claude-mem history, Zone 3's garble/ScriptContext threading is the longest-stalling critical zone in the program (every audit cycle since first identified, never landed; 3 prior zones reportedly reopened/regressed in new forms) — flagged as requiring extra post-implementation verification (added to Zone 3's spec above), not just spec correctness.

### Recommendation
Implement in the wave order given, but only after Wave 2's Zone 1 and Zone 4 specs are updated per the corrections embedded above, and Wave 3's Zone 6 spec drops its `indexer.py:1197` edit in favor of Zone 3's single authoritative version. Re-run `codebase-memory` / `trace_path` verification after Wave 1 lands to refresh line-number anchors before Wave 2 implementation starts, given the staleness risk flagged above.
