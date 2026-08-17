# Remediation Plan — 2026-08-17

**Audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-17_POST-FIX-4.md
**Zones:** 3 of 8 (top by priority)
**Waves:** 3

## Priority Scores

| Zone | Score | Severity | Bug Count | Proposal Status | Excluded | In This Plan |
|---|---:|---|---:|---|---|---|
| Zone 1: Garble Detection Hydra | 57.6 | critical | 12 | no_proposal | no | yes (wave 1) |
| Zone 2: God Function Routing Cascade (client.py index()) | 52.8 | critical | 11 | no_proposal | no | yes (wave 3) |
| Zone 5: OCR/Enrichment Signal Conflation | 32.4 | high | 9 | no_proposal | no | yes (wave 2) |
| Zone 4: Threshold Calibration Feedback Loops | 28.8 | high | 8 | no_proposal | no | no |
| Zone 3: Verdict Persistence Split-Brain | 25.2 | high | 7 | no_proposal | no | no |
| Zone 6: Conversion Pipeline Stage Coupling (pdf_to_markdown_docling) | 25.2 | high | 7 | no_proposal | no | no |
| Zone 7: Registry/Persistence Consistency Gaps | 14.4 | medium | 6 | no_proposal | no | no |
| Zone 8: Dead/Uncommitted/Stale Code Divergence | 14.4 | medium | 6 | no_proposal | no | no |

Scoring formula: severity_weight × bug_count × 1.2 (no_proposal multiplier — no delta run available this pass). Zones 1, 2, and 5 selected as the top 3 by score; Zones 3/4/6/7/8 are documented in the audit but out of scope for this plan.

## Wave Sequence

### Wave 1 — Zone 1: Garble Detection Hydra
**Rationale:** Zone 1 introduces the unified `check_garble()` API in `helpers.py` and consolidates 5+ garble evaluation sites across `helpers.py`, `converters.py`, and `client.py`. Both Zone 2 and Zone 5 depend on this API: Zone 2's recovery routing gates OCR escalation on garble results (`client.py:1286-1293`), and Zone 5's OCR escalation paths consume garble detection output. Zone 1 must land first to establish the contract that downstream zones consume.

**Shared files:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/client.py`

### Wave 2 — Zone 5: OCR/Enrichment Signal Conflation
**Rationale:** Zone 5 splits the single `OCR_ESCALATION` boolean (`config.py:41`) into `OCR_ESCALATION_GARBLE` and `OCR_ESCALATION_PER_PICTURE`, and fixes verdict char-counting to use primary text only. Zone 2's recovery pipeline refactor should consume the split OCR flags rather than the legacy single flag. Zone 5's `client.py` changes (image path unification and the flat garble gate) overlap with Zone 2's `index()` extraction scope, so they must be in separate waves to avoid merge conflicts.

**Shared files:** `src/pageindex_mcp/client.py`, `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/config.py`

**Note:** Validation flagged a blocker between Zone 5 (wave 2) and Zone 2 (wave 3) over ownership of `client.py` lines ~1837-1968 — see Validation Results below. This must be resolved before wave 2 lands, not deferred to wave 3.

### Wave 3 — Zone 2: God Function Routing Cascade (client.py index())
**Rationale:** Zone 2 performs the massive `client.py` `index()` refactor (1409 lines into an `ExtractionState` dataclass + recovery-step pipeline). It goes last because it consumes both Zone 1's unified `check_garble()` API and Zone 5's split OCR flags. Landing it after Zones 1 and 5 means the extracted recovery methods (`_recover_ocr_escalation`, etc.) are built on the final garble and OCR interfaces, avoiding a second round of rewrites. `helpers.py` also receives the `ExtractionState` dataclass, which must not conflict with Zone 1's `check_garble` additions or Zone 5's `primary_text` field.

**Shared files:** none declared, but line anchors throughout this zone's spec were computed against pre-wave-1/2 `client.py` and `helpers.py` and require re-baselining (see Validation Results).

## Fix Specs

### Zone: Zone 1: Garble Detection Hydra (wave 1, priority 1)

**Mechanism to eliminate:** Five parallel garble evaluation functions (`_tree_is_garbled`, `_flat_text_is_garbled`, `_text_layer_has_content`, `_document_level_text_fallback` garble check, region-level garble check) operate on different text shapes with inconsistent heuristic coverage. `_flat_text_is_garbled` includes raw markdown formatting (pipes, headers) that dilute ratios below heuristic floors; `converters.py` callsites call only `_is_garbled_blob` without `_has_sparse_mojibake` (silent omission); `_gate_node_garbling` uses text-inferred `expected_script` that can override filename-derived script per-node; `classify_verdict` passes `expected_script=None` on the flat-doc path when `validate_result` is `None`, losing script context. Fixing a threshold in one function does not propagate to the others, generating a recurring fix-then-regress cycle (12 bugs across 6 RFCs).

**Strategy:** Consolidate all 5+ parallel garble evaluation functions into a single `check_garble(text, expected_script, context)` entry point with a `GarbleContext` enum. Delete `_tree_is_garbled` and `_flat_text_is_garbled`. Make `expected_script` required keyword-only. Add `_has_sparse_mojibake` to `converters.py` callsites (silent omission fix). Strip markdown formatting for `FLAT_MARKDOWN` context. Execute in 4 waves: thin wrapper, swap callers, delete dead code, behavioral fixes behind feature flags.

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `helpers.py` | 1300-1315 | Introduce `GarbleContext` enum and `check_garble()` entry point | Add `GarbleContext(StrEnum)` with values `TREE_BULK, NODE, FLAT_MARKDOWN, PAGE_TEXT_LAYER, DOCUMENT_FALLBACK, REGION, RETRY_COMPARISON, IMAGE_ENRICHMENT` near `_is_garbled_blob`. Implement `check_garble(text, *, expected_script, context, original_defect=None)`. ~40 new lines. | Waves 1-3: must replicate exact per-site current behavior (see validation issue below) — not literally "behaviorally identical" plus unconditional new behavior. `_is_garbled_blob`/`_has_sparse_mojibake` remain internal; `check_garble` is the sole public API. |
| `helpers.py` | 1474-1480, 350 | Replace `_tree_is_garbled` body with `check_garble` delegation, then delete | Wave 2: delegate to `check_garble(..., context=GarbleContext.TREE_BULK)`. Wave 3: delete function, update `TreeSignals.from_tree` to call `check_garble` directly. | `TreeSignals.from_tree` must produce identical `garbled` field values consumed by `_gate_garbling`. |
| `helpers.py` | 3228-3245 | Replace `_flat_text_is_garbled` body with `check_garble` delegation, then delete | Wave 2: delegate to `check_garble(..., context=GarbleContext.FLAT_MARKDOWN, original_defect=original_defect)`. Wave 3: delete, update `client.py` callers at lines 443, 980, 1855, 1883. | Short-text garble-by-default rule (current lines 3238-3243) must be preserved exactly for `FLAT_MARKDOWN` + `original_defect in (GARBLING, NODE_GARBLING)`. |
| `helpers.py` | 1423-1471 | Route `_garble_check_nodes` inner calls through `check_garble` | Replace direct `_is_garbled_blob` calls (lines 1451, 1462) with `check_garble(text, expected_script=node_script, context=GarbleContext.NODE)` — enables `_has_sparse_mojibake` per-node (currently omitted). | Per-node `page_script` inference via `_infer_script` must still be passed as `expected_script`. Recursion pattern preserved. |
| `helpers.py` | 1861-1881 | Route `_garble_ratio` through `check_garble` | Replace inline OR of `_is_garbled_blob`/`_has_sparse_mojibake` (lines 1870-1871, 1879) with `check_garble(chunk, expected_script=expected_script, context=GarbleContext.TREE_BULK)`. | Window size (2000) and chunking logic preserved. Called from `TreeSignals.from_tree` only when `garbled=True`. |
| `helpers.py` | 2105 | Route `classify_verdict` image-enrichment garble check through `check_garble` | Replace `_is_garbled_blob(_promoted_text, expected_script=expected_script)` with `check_garble(..., context=GarbleContext.IMAGE_ENRICHMENT)` — adds `_has_sparse_mojibake` coverage to image-enrichment promotion. | `image_enrichment_promoted` verdict path must continue using deduplicated text from `_dedupe_chart_text_lines`. |
| `converters.py` | 1647-1652 | Replace `_text_layer_has_content` garble check with `check_garble` | Lazy-import `check_garble, GarbleContext`; call with `context=GarbleContext.PAGE_TEXT_LAYER`, `expected_script=infer_script(text)`. Adds `_has_sparse_mojibake` coverage (currently missing). | `_TEXT_LAYER_GARBLE_CHECK_ENABLED` flag remains the guard. |
| `converters.py` | 1747-1750 | Replace `_document_level_text_fallback` garble check with `check_garble` | Same pattern, `context=GarbleContext.DOCUMENT_FALLBACK`. | Fallback-to-md-unchanged behavior on garble detection (`return md`) preserved. |
| `converters.py` | 2147-2154 | Replace region-level garble check with `check_garble` | Same pattern, `context=GarbleContext.REGION`. | `has_own_text = False` assignment on garble detection preserved. `_TEXT_LAYER_GARBLE_CHECK_ENABLED` guard remains. |
| `client.py` | 63, 443, 980, 1855, 1883 | Replace `_flat_text_is_garbled` calls with `check_garble` | Update import; thread `original_defect=first_defect` only at line 1855 (flat-path gate); other sites default `original_defect=None`. | `original_defect` must be threaded at line 1855 only. |
| `client.py` | 65, 1402, 1406, 1418 | Replace `_is_garbled_blob` retry-comparison calls with `check_garble` | `context=GarbleContext.RETRY_COMPARISON`. Remove `_is_garbled_blob` from `client.py` import. | Retry comparison logic (pre_garbled AND NOT post_garbled ⇒ retry_wins; revert on similar repeating-token density) must remain intact. |

**Wiring checks:**
- `GarbleContext` must be imported by `helpers.py`, `converters.py`, `client.py` (dispatch)
- `check_garble` must be imported by `converters.py`, `client.py` (call)
- `check_garble` "must be imported by `helpers.py`" — **flagged vacuous by validation**: `check_garble` is *defined* in `helpers.py`; a module cannot import its own symbol. Respecify as a call-site scan (grep/AST for internal callers) rather than an import check.

**Test requirements:**
- `tests/test_zone1_check_garble.py` — exhaustiveness: `check_garble` per `GarbleContext` matches the function it replaces, across Arabic/German/Latin garbled and clean texts. **Correction required per validation:** the `FLAT_MARKDOWN` case must NOT assert "identical to `_flat_text_is_garbled` including markdown stripping" — the legacy function performs no stripping today. Waves 1-3 must assert identity *without* stripping; a separate wave-4, flag-gated regression test asserts stripping changes the verdict on diluted garbled markdown.
- `tests/test_zone1_check_garble.py` — contract: `expected_script` is required keyword-only (raises `TypeError` if omitted); `GarbleContext` is a `StrEnum` with exactly 8 values.
- `tests/test_zone1_check_garble.py` — regression: short-text garble-by-default fires only for `FLAT_MARKDOWN` + `original_defect in (GARBLING, NODE_GARBLING)`.
- `tests/test_zone1_check_garble.py` — regression: `FLAT_MARKDOWN` strips markdown formatting before ratio computation (wave 4 only, per correction above).
- `tests/test_zone1_check_garble.py` — regression: `converters.py` contexts (`PAGE_TEXT_LAYER`, `DOCUMENT_FALLBACK`, `REGION`) now include `_has_sparse_mojibake` (Arabic-Latin-Arabic glued fragments, MOU/warid-597-style).
- `tests/test_zone1_garble_wiring.py` — wiring: AST-scan confirms all 7 production callsites call `check_garble`, not the legacy functions directly.
- `tests/test_zone1_garble_wiring.py` — integration: `classify_verdict` with `validate_result=None` (flat-doc path) threads `expected_script` through to `check_garble` (Discovery #5331 regression prevention). **No code target currently fixes the underlying `expected_script=None` threading bug** — see Validation Results; this test will fail against the spec as written unless a code target is added.

**Corpus validation:**
- Affected documents (6, spot-check all): وارد رقم 597 (warid-597), MOU MOHRE, قرار مجلس الوزراء رقم 106, حقوق الإنسان (Human-Rights), Haftpflicht-Allgemeine-Bedingungen, اتفاقية مستوى الخدمة (SLA)
- Expected verdict direction: improve

**Estimated complexity:** large
**Severity:** critical

---

### Zone: Zone 5: OCR/Enrichment Signal Conflation (wave 2, priority 3)

**Mechanism to eliminate:** A single `OCR_ESCALATION` boolean controls two orthogonal behaviors (page-level garble OCR retry AND per-picture crop+OCR enrichment), causing toggling one to inadvertently disable the other. The `image_enrichment_promoted` verdict path in `classify_verdict` checks char volume via `sig.flat_text` with no type-level guarantee that enrichment metadata (`ocr_text`/`description` from image blocks) is excluded — correctness depends solely on the caller building synthetic structure with `_flat_block_primary_text`, not on the verdict function itself. The standalone image path (`client.py` `_IMAGE_EXTS` branch) constructs synthetic `PictureResults` but does not go through the same `splice_figure_markers`/`_enrich_image_blocks` flow as the PDF flat-success path, creating divergent enrichment behavior for structurally identical decisions.

**Strategy:** Split the monolithic `OCR_ESCALATION` flag into two independent controls. Add a dedicated `primary_text` field to `TreeSignals` so `classify_verdict`'s image-enrichment branch is structurally correct regardless of caller. Extract the flat-doc picture-enrichment pipeline into a shared helper callable from both PDF and standalone-image paths.

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `config.py` | 39-43, 264-326 | Split `OCR_ESCALATION` into `OCR_ESCALATION_GARBLE` and `OCR_ESCALATION_PER_PICTURE` | Two new boolean env-backed flags, default `'1'`. Backward-compat shim: if legacy `OCR_ESCALATION` is explicitly set but new flags are not, both inherit its value. Keep `OCR_ESCALATION` as legacy alias in `effective_config_snapshot`. | `OCR_ESCALATION=0` must still disable both (backward compat). **Validation flags:** flags are module-level constants bound at import time — tests that monkeypatch `OCR_ESCALATION`/`_OCR_ESCALATION` at runtime will not propagate to the split flags. Enumerate and migrate affected tests, or expose module-local aliases tests can patch directly. |
| `client.py` | 22, 1002-1003, 1293, 1662 | Replace `_OCR_ESCALATION` with the two split flags at each callsite | Import both new flags. Line 1293 (garble retry gate) → `_OCR_ESCALATION_GARBLE`. Line 1662 (image-dominant OCR gate) → `_OCR_ESCALATION_GARBLE`. `decide_ocr_mode` call (1002-1003) → `ocr_escalation_enabled=_OCR_ESCALATION_PER_PICTURE`. | **Validation flags:** line 1662 gates image-dominant retry (fires on `NODE_COUNT_LOW`/`DEPTH_LOW`, not garbling) — assigning it to `OCR_ESCALATION_GARBLE` re-creates a 3-way conflation under a 2-way split. Either rename to `OCR_ESCALATION_PAGE_RETRY` and document it spans both, or gate line 1662 solely on `IMAGE_DOMINANT_OCR_ESCALATION_ENABLED`. |
| `converters.py` | 1485, 2520-2521 | Replace `_OCR_ESCALATION` with `OCR_ESCALATION_PER_PICTURE` at per-picture recovery site | Import `OCR_ESCALATION_PER_PICTURE`; pass as `ocr_escalation_enabled` in `_recover_picture_results`'s `decide_ocr_mode` call. | `_recover_picture_text` internal logic unchanged; only the gate changes. |
| `helpers.py` | 328-369, 2096-2106 | Add `primary_text` field to `TreeSignals`; use in `classify_verdict` image-enrichment promotion | New frozen-dataclass field `primary_text: str`, computed identically to `flat_text` in `TreeSignals.from_tree`. `classify_verdict` uses `sig.primary_text` (not `sig.flat_text`) for `_dedupe_chart_text_lines` and the char-count check. | `TreeSignals` is frozen — field must be in `__init__`. `sig.flat_text` remains available for garble checks and other non-verdict uses. |
| `client.py` | 1174-1221, 1836-1968 | Extract `_apply_picture_enrichment` helper, call from both PDF and standalone-image paths | Extract `splice_figure_markers`, `_enrich_image_blocks`, `compute_image_enrichment_ratio`, VLM describe, `route_and_extract_flat`, zero-block guard, image_standalone detection, content_class override into `_apply_picture_enrichment(flat_md, pic_results, blocks, doc_id, ext, filename, settings)` → `(content_class, blocks, image_enrichment_ratio, flat_md)`. Call from PDF flat-success branch AND standalone-image path. | VLM describe gated on `settings.vlm_describe_images`; zero-block guard still raises `LowQualityTreeError`; image_standalone override still applies; MinIO figure upload still on event-loop thread. **BLOCKER (validation):** the 1837-1968 range includes the flat garble gate + nested VLM flat-garble recovery (`_flat_garble_unrecovered` flag, lines ~1855-1901) that Zone 2's `_persist_flat_result` spec claims to own and dispatch on. This helper's return signature does not propagate `flat_garble_unrecovered`. Must resolve before implementation: either narrow this extraction to start at ~1902 (excluding the garble gate), or extend the helper to return `flat_garble_unrecovered` and respecify Zone 2's `_persist_flat_result` to call it. Also correct the wave-2 rationale's stray line reference (529-541 → should read 1174-1221). |
| `helpers.py` | 3420-3454 | Audit `_flat_block_text` callers; redirect any verdict-path use to `_flat_block_primary_text` | Search all callers of `_flat_block_text`; confirm none are in verdict computation (should be search-index-only: `get_document`, `_flat_search_text`). Add docstring note marking it search-index-only. | Do not delete `_flat_block_text`. `_flat_block_primary_text` must continue excluding `role=image` blocks' `ocr_text`/`description`. |

**Wiring checks:**
- `OCR_ESCALATION_GARBLE` imported by `client.py` (import, dispatch)
- `OCR_ESCALATION_PER_PICTURE` imported by `client.py`, `converters.py` (import, dispatch)
- `TreeSignals.primary_text` "must be imported by `helpers.py`" — **flagged vacuous by validation**: an attribute is not importable. Respecify as an attribute-access scan (`sig.primary_text` grep/AST) in `helpers.py` and callers.
- `_apply_picture_enrichment` "must be imported by `client.py`" — **flagged vacuous by validation**: this is a method defined on the client class, not importable. Respecify as a call-site scan (`self._apply_picture_enrichment(`).

**Test requirements:**
- `tests/test_zone5_ocr_split.py` — exhaustiveness across the 4 combinations of the two split flags plus legacy `OCR_ESCALATION=0`.
- `tests/test_zone5_ocr_split.py` — contract: `decide_ocr_mode` respects `OCR_ESCALATION_PER_PICTURE` independently; `force_full_page=True` always wins.
- `tests/test_zone5_ocr_split.py` — wiring: `effective_config_snapshot` includes `ocr_escalation_garble` and `ocr_escalation_per_picture` alongside legacy `ocr_escalation`.
- `tests/test_zone5_primary_text.py` — contract: `primary_text` == `flat_text` for tree-derived structures; excludes enrichment metadata for synthetic flat structures; `classify_verdict` image-enrichment path uses `primary_text`.
- `tests/test_zone5_primary_text.py` — regression: warid-597-shaped doc (70 image blocks, 3208 chars of barcode/digit noise) does NOT earn `image_enrichment_promoted` PASS when `primary_text` is below the `min_image_promoted_chars` floor.
- `tests/test_zone5_enrichment_unify.py` — contract: standalone `.jpg` and PDF flat-success paths both call `_apply_picture_enrichment`, producing identical block structures given identical inputs.
- `tests/test_zone5_enrichment_unify.py` — contract: helper preserves zero-block guard, image_standalone detection, content_class override.

**Corpus validation:**
- Affected documents (5, spot-check all): warid-597, marsoom-13, MOU-MOHRE, image_pie_chart_sample, arabicSLA
- Expected verdict direction: improve

**Estimated complexity:** medium
**Severity:** high

---

### Zone: Zone 2: God Function Routing Cascade (client.py index()) (wave 3, priority 2)

**Mechanism to eliminate:** Sequential if/elif recovery cascade in a 1409-line monolithic `index()` method where recovery branches share mutable local state (`result`, `ok`, `reason`, `gate_result`, `md_content`, `pic_results`, `tmp_md_path`, `used_converter`). First-match-wins ordering means later branches never fire when an earlier gate triggers on a different defect. Adding new `validate_tree` failure reasons without wiring `client.py` routing causes silent fallthrough to `LowQualityTreeError`. Partial-state reverts produce divergent extraction artifacts. The reconvert+revalidate pattern is duplicated 4 times across recovery branches.

**Strategy:** Extract an immutable `ExtractionState` dataclass carrying all mutable per-extraction variables. Refactor `index()` into: (A) conversion front-end `_convert_to_tree()`, (B) typed recovery-step pipeline where each strategy is an independent async method taking/returning `ExtractionState`, (C) shared `_reconvert_and_revalidate()` helper eliminating 4x duplication, (D) persistence tail `_persist_tree_result()`/`_persist_flat_result()`. `index()` becomes a ~120-line orchestrator.

**Code targets:**

| File | Lines (pre-wave1/2 baseline — MUST re-anchor, see below) | What | How | Constraint |
|---|---|---|---|---|
| `helpers.py` | ~174 (near `ExtractionSnapshot`) | Add `ExtractionState` frozen dataclass | 15 fields incl. `result`, `ok`, `reason`, `gate_result`, `original_gate_result`, `first_defect`, `route`, `md_content`, `tmp_md_path`, `pic_results`, `used_converter`, `total_chars`, `extraction_stages_captured`, `flat_garble_unrecovered`. `from_initial()` classmethod + `update()` wrapping `dataclasses.replace`. | `ExtractionSnapshot` (actually at `helpers.py:108`, not ~174 — **line correction per validation**) must remain unchanged; `ExtractionState` is additive. |
| `helpers.py` | ~265-266 | Add `_reconvert_and_revalidate()` shared helper | Async function: write md → `run_md_to_tree` → `split_oversized_leaf_nodes` → `_segment_table_nodes` → `validate_tree` → unpack `gate_result`. Returns `(result, gate_result, ok, reason)`. Replaces 4 duplicated blocks. | Must pass `expected_script`/`page_count` identically to current inline calls; returns `tmp_md_path` for caller cleanup. |
| `client.py` | ~928-1249 | Extract `_convert_to_tree()` | Move conversion front-end (PDF converter chain, .md/.txt/.docx/.pptx/.xlsx/image/.html dispatch, initial split+segment+validate_tree) into async method returning `ExtractionState`. | Dedup early-return stays in `index()`. `pdf_page_count` local threaded as state field or return alongside. |
| `client.py` | ~1286-1798 | Extract 6 recovery branches into standalone async methods | `_recover_ocr_escalation` (~200 lines, keep-best/revert via `ExtractionSnapshot`), `_recover_rtl_repair`, `_recover_rtl_flat_compare`, `_recover_vlm_fallback` (incl. D7 Tesseract sub-recovery), `_recover_image_dominant_ocr`, `_recover_flat_prefer`/`_recover_landscape_reroute`. Each uses `_reconvert_and_revalidate()`; returns state unchanged if guard doesn't fire. | `ExtractionSnapshot` keep-best logic preserved exactly. VLM sub-recovery `Route.FLAT` semantics preserved. `_recover_flat_prefer`/landscape reroute only fire when `ok=True`. |
| `client.py` | ~1902-2243 | Extract `_persist_tree_result()` and `_persist_flat_result()` | Tree persistence (`classify_verdict`, `save_doc`, `write_verdict`, `save_doc_meta`, `save_raw`, `hash_cache_set`) and flat persistence (`route_and_extract_flat`, `classify_verdict`, `save_flat_doc`, `save_raw`, `hash_cache_set`) into separate methods returning `doc_id`. | **BLOCKER (validation, shared with Zone 5):** flat persistence's flat-garble-gate + nested VLM flat-garble recovery must be inside `_persist_flat_result`, not the recovery pipeline — but Zone 5 wave 2 already extracts this exact range into `_apply_picture_enrichment`. This code target must be rewritten post-wave-2 to call `_apply_picture_enrichment` (extended to return `flat_garble_unrecovered`) rather than re-extracting code that no longer exists in `index()`. |
| `client.py` | ~840-2249 | Rewrite `index()` as ~120-line orchestrator | Preamble (~30 lines) → `state = await self._convert_to_tree(...)` → `for step in RECOVERY_PIPELINE: state = await step(state)` → route dispatch (`Route.FLAT` → `_persist_flat_result`; `Route.REJECT`/`flat_garble_unrecovered` → raise `LowQualityTreeError`; `Route.PERSIST_FAIL` → log+persist-as-FAIL; else → `_persist_tree_result`) → `finally` cleanup. Remove `noqa: C901, PLR0915`. | Terminal-reject path must handle both `Route.REJECT` and unhandled-defect `PERSIST_FAIL`. `finally` must clean up `tmp_lo_dir` and `tmp_md_path`. |

**Line-anchoring warning (validation, major):** All Zone 2 line anchors are computed against the *pre-wave-1/wave-2* `client.py`/`helpers.py`. Waves 1 and 2 both edit `client.py` first (Zone 1 rewrites/deletes several functions; Zone 5 extracts ~130 lines into `_apply_picture_enrichment` and edits config-flag callsites). Implementing wave 3 against stale line numbers risks mis-scoped extraction. **Required mitigation:** re-anchor all wave-3 code targets to symbols and structural landmarks (function names, guard-clause conditions, comments) at the start of wave 3, not absolute line numbers.

**Wiring checks:**
- `ExtractionState` imported by `client.py` (import) — genuine cross-module symbol, valid check.
- `_reconvert_and_revalidate` called from `client.py` (call) — genuine cross-module symbol, valid check.
- `_convert_to_tree`, `_recover_ocr_escalation`, `_recover_rtl_repair`, `_recover_rtl_flat_compare`, `_recover_vlm_fallback`, `_recover_image_dominant_ocr`, `_recover_flat_prefer`, `_recover_landscape_reroute`, `_persist_tree_result`, `_persist_flat_result` — all listed as "must be imported by `client.py`" in the source plan; **flagged vacuous by validation**: these are methods defined on the client class in `client.py` itself and can never appear in an import statement. Respecify all of these as call-site scans (`self._recover_x(` / `self._persist_x(`), reserving `must_be_imported_by` for genuine cross-module symbols only.

**Test requirements:**
- `tests/test_zone2_extraction_state.py` — contract: frozen immutability, `dataclasses.replace` semantics, `from_initial()`/`update()`, all 15 fields.
- `tests/test_zone2_reconvert_revalidate.py` — contract: correct call order, `expected_script`/`page_count` threading, correct return tuple, `tmp_md_path` creation, both pass/fail `validate_tree` outcomes.
- `tests/test_zone2_recovery_pipeline.py` — exhaustiveness: each recovery method's guard clause fires/doesn't fire correctly per defect type and config flag.
- `tests/test_zone2_recovery_pipeline.py` — regression: new `TreeDefect` values added to `REASON_POLICY`/`GATE_TABLE` automatically flow through `decide_route()` and the orchestrator's `Route.PERSIST_FAIL` dispatch without new if/elif branches (RFC-029 D0/D1/D2/D8 unwired-defect class).
- `tests/test_zone2_state_atomicity.py` — regression: `_recover_ocr_escalation` revert (`retry_wins=False`) restores the exact pre-retry `ExtractionState` across ALL fields (RFC-030 D1 partial-state-revert bug).
- `tests/test_zone2_persist_paths.py` — contract: `_persist_tree_result`/`_persist_flat_result` call sequences; flat garble gate raises `LowQualityTreeError` on garbled flat_md; zero-block guard raises `LowQualityTreeError('flat_zero_block')`.
- `tests/test_zone2_orchestrator.py` — integration: end-to-end route dispatch for `Route.TREE`/`FLAT`/`REJECT`/`PERSIST_FAIL`; recovery pipeline runs in declared order.

**Corpus validation:**
- Affected documents (14, spot-check all): marsoom-13, qarar-106, warid-597, arabicSLA, SLA, MOU, Penal_Code, federal_decree_law_no_33, marsoom-33, cabinet_resolution_no_96, Haftpflicht, Reitlehrer, GHV-TKV-Tarif, Human-Rights
- Expected verdict direction: stable (refactor-only; no verdict changes expected)

**Estimated complexity:** large
**Severity:** critical

## Validation Results

**Overall quality: needs_work — NOT APPROVED as written.** The following issues must be resolved before implementation begins on the affected zone/wave.

### Blockers
1. **Zone 2 vs Zone 5 ownership conflict on `client.py` lines ~1837-1968.** Zone 5 (wave 2) extracts this range into `_apply_picture_enrichment`, which includes the flat garble gate + nested VLM flat-garble recovery (`_flat_garble_unrecovered` flag). Zone 2 (wave 3) requires that same logic live inside `_persist_flat_result` and dispatch on `state.flat_garble_unrecovered`, but Zone 5's helper signature — `(content_class, blocks, image_enrichment_ratio, flat_md)` — cannot propagate that flag, and Zone 2's code targets never reference `_apply_picture_enrichment` despite declaring a dependency on Zone 5. **Fix before wave 3 starts:** either narrow Zone 5's extraction to exclude the flat garble gate (start at ~1902), or extend `_apply_picture_enrichment` to return `flat_garble_unrecovered` and rewrite Zone 2's `_persist_flat_result` code target to call it.

### Major issues
2. **Zone 1 internal contradiction:** the constraint "`check_garble` must be behaviorally identical to existing functions for waves 1-3" conflicts with the same code target's "how," which bakes in two unconditional behavioral changes (markdown stripping for `FLAT_MARKDOWN`; always OR-ing `_has_sparse_mojibake`) with no feature flag specified anywhere. Verified against source: `_flat_text_is_garbled` does not strip markdown today; `client.py` retry-comparison sites, `classify_verdict:2105`, and `_garble_check_nodes` call `_is_garbled_blob` without `_has_sparse_mojibake` today. **Fix:** split `check_garble`'s behavior matrix per wave — waves 1-3 replicate exact current per-site behavior (per-context flags matching today's callsites); wave 4 flips new behaviors behind named feature flags as the strategy already promises.
3. **Zone 1 exhaustiveness test built on a false premise:** it asserts `FLAT_MARKDOWN` identity to `_flat_text_is_garbled` "including markdown stripping" — but `_flat_text_is_garbled` performs no stripping today (contradicts the zone's own mechanism narrative). **Fix:** waves 1-3 test identity without stripping; a separate wave-4 flag-gated test asserts stripping changes the verdict on diluted garbled markdown.
4. **Zone 1 test-without-code-target:** the integration test requires `classify_verdict` to thread `expected_script` through on the flat-doc path when `validate_result=None` (Discovery #5331), but no Zone 1 code target modifies that callsite. **Fix:** add a code target for the `classify_verdict` flat-doc callsite (or its `client.py` caller), or drop the test to a later wave and record the defect as explicitly out of scope.
5. **Zone 2 line anchors are stale by construction:** all extraction ranges are computed against pre-wave-1/2 `client.py`, but waves 1 and 2 both edit `client.py` first. **Fix:** re-anchor wave-3 code targets to symbols/structural landmarks at the start of wave 3 rather than trusting the line numbers in this document.

### Minor issues
6. Zone 1's `check_garble` "must be imported by `helpers.py`" wiring check is vacuous (a module can't import its own symbol) — respecify as a call-site scan.
7. Zone 2's eleven `_recover_*`/`_persist_*`/`_convert_to_tree` wiring checks and Zone 5's `TreeSignals.primary_text`/`_apply_picture_enrichment` checks are vacuous under `must_be_imported_by` (methods/attributes aren't importable) — respecify as call-site/attribute-access scans; reserve `must_be_imported_by` for genuine cross-module symbols (`ExtractionState`, `_reconvert_and_revalidate`, `OCR_ESCALATION_GARBLE`, `OCR_ESCALATION_PER_PICTURE`).
8. Zone 2's `ExtractionState` placement note ("after `ExtractionSnapshot`, line ~174") is wrong — `ExtractionSnapshot` is actually at `helpers.py:108`. Corrected above; anchor by symbol going forward.
9. Zone 5's backward-compat shim for the split OCR flags is import-time only (module-level constants bound at import); existing tests that monkeypatch `OCR_ESCALATION`/`_OCR_ESCALATION` at runtime won't propagate. Needs an enumerated per-test migration list or module-local aliases tests can patch directly.
10. Zone 5's `client.py:1662` gate (image-dominant OCR retry, fires on `NODE_COUNT_LOW`/`DEPTH_LOW`) is assigned to `OCR_ESCALATION_GARBLE`, silently re-conflating a third orthogonal behavior into the "garble" flag. Rename to something spanning both page-level retries, or gate solely on `IMAGE_DOMINANT_OCR_ESCALATION_ENABLED`.
11. Zone 5's wave-2 rationale cites "image path unification at lines 529-541," which matches nothing relevant; the correct reference (per the zone's own code target) is lines 1174-1221.
12. Zone 1's `converters.py` sites retain self-inferred `expected_script = infer_script(text)` — behavior-preserving for waves 1-3, but the zone's own mechanism narrative criticizes exactly this pattern ("text-inferred expected_script that can override filename-derived script") and no wave-4 item addresses it in `converters.py`. Add an explicit wave-4 item or an honesty note.

**Recommended path:** resolve blocker #1 and major issues #2-#5 before any implementation work starts; the minor issues (#6-#12) should be folded into each zone's spec as corrections during implementation but do not block wave 1 from starting.
