# Remediation Plan — 2026-08-25

**Audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md  
**Zones Under Remediation:** 5 of 7 (top priority, wave-sequenced)  
**Waves:** 3  
**Scope Justification:** All 5 zones are prioritized by severity score and wave-sequenced to prevent merge conflicts across overlapping file sets.

---

## Priority Scores — Full Zone Roster

All zones ranked by remediation priority (score combines severity, bug count, and regression velocity):

| Rank | Zone Name | Score | Severity | Bug Count | Status | Wave | Excluded |
|------|-----------|-------|----------|-----------|--------|------|----------|
| 1 | Multi-Store Dual-Write Consistency | 45.54 | HIGH | 11 | NEW (no proposal) | 3 | NO |
| 2 | Garble Detection Fragmentation | 24.84 | CRITICAL | 18 | Regressed despite wiring | 1 | NO |
| 3 | OCR Strategy Bifurcation | 20.7 | CRITICAL | 15 | Regressed, severity escalated | 1 | NO |
| 4 | Verdict Promotion / Quality Gate Stack | 19.32 | CRITICAL | 14 | Relocated to Wave 2 | 2 | NO |
| 5 | Config Layering Split and Dead-Code Accumulation | 16.8 | MEDIUM | 7 | NEW (no proposal) | 3 | NO |
| 6 | God-Function Orchestration | 10.35 | HIGH | 10 | Regressed, severity escalated | — | NOT IN SCOPE |
| 7 | validate_tree Reason-String Dispatch | 8.1 | HIGH | 9 | Improved, lowest priority | — | NOT IN SCOPE |

**Focus Zones (This Plan):** Garble Detection (Wave 1, P2) + OCR Strategy (Wave 1, P3) + Verdict Promotion (Wave 2, P4) + Multi-Store Dual-Write (Wave 3, P1) + Config Layering (Wave 3, P5)

---

## Wave Sequence & Rationale

### Wave 1: Parallel Independent Fixes
**Zones:** Garble Detection Fragmentation, OCR Strategy Bifurcation  
**Duration:** Estimated 3–5 days  
**Key Constraint:** Zero shared key_files between zones; both are upstream signal producers (garble ratios, OCR/image classification) consumed downstream by Verdict Promotion gate stack.

**File Isolation Verified:**
- Garble Detection key_files: `helpers/garble.py`, `helpers/gates.py`, `helpers/tree_validation.py`, `helpers/verdict.py`
- OCR Strategy key_files: `picture_plane.py`, `converters/pictures.py`, `client/indexer.py`, `client/images.py`, `client/recovery.py`, `converters/ocr_langs.py`, `metrics/definitions.py`
- Overlap: NONE (safe for parallel execution)

**Why Both Must Precede Wave 2:**  
Wave 2's Verdict Promotion zone calls `evaluate_gates()` (from `client/indexer.py`) and reads garble signals from `helpers/verdict.py`—both wave-1 outputs. Garble Detection must stabilize its ratio producers before Verdict Promotion consumes them; OCR Strategy must ensure all OCR paths feed the tree properly before Verdict gates inspect tree quality.

---

### Wave 2: Verdict Quality Gate Stack
**Zones:** Verdict Promotion / Quality Gate Stack  
**Duration:** Estimated 2–3 days  
**Key Constraint:** Shares PRIMARY files with both Wave 1 zones:
- `helpers/verdict.py` (shared with Garble Detection)
- `helpers/gates.py` (shared with Garble Detection)
- `client/indexer.py` (shared with OCR Strategy)

**Sequencing Rule:** Isolation from Wave 1 is mandatory; this zone must run AFTER both Wave 1 zones complete. It computes the verdict outcome (PASS/REQUIRE_MANUAL/FAIL) that Wave 3 will persist.

**Why Before Wave 3:**  
Multi-Store Dual-Write (Wave 3) persists the verdict computed here via `upsert_verdict()` and `upsert_doc()` in `registry/queries.py`. Verdict Promotion MUST complete and be validated before persistence logic lands.

---

### Wave 3: Persistence & Configuration Cleanup
**Zones:** Multi-Store Dual-Write Consistency, Config Layering Split and Dead-Code Accumulation  
**Duration:** Estimated 2–3 days  
**Key Constraint:** Both zones share PRIMARY file `registry/queries.py` (carried from Wave 2 verdict output) + `helpers/garble.py` (carried from Wave 1 garble output).

**Coexecution Rationale:**  
Multi-Store and Config Layering share ZERO inter-zone key_files with each other; no dependency was found between them via `search_graph`/`trace_path`. However, Config Layering depends on Garble Detection (it reads `garble_digit_floor` that Garble Detection produces), so it cannot run in Wave 1. Wave 3 is the earliest both can safely coexist.

**Intra-Wave Ordering (If Sequential Needed):**  
If merge conflicts on `registry/queries.py` arise during concurrent edits, prioritize Multi-Store completion first (it owns the primary verdict persistence contract), then Config Layering (cleanup/refactor, less critical path).

---

## Fix Specifications

### Zone: Garble Detection Fragmentation (Wave 1, Priority 2)

**Severity:** CRITICAL  
**Estimated Complexity:** MEDIUM  
**Key Files:** `helpers/garble.py`, `helpers/gates.py`, `helpers/tree_validation.py`, `helpers/verdict.py`, `helpers/types.py`, `config.py`, `client/indexer.py`, `client/recovery.py`, `client/images.py`, `converters/pictures.py`

#### Mechanism to Eliminate

Nine independent boolean-gated prongs in garble_prongs() with inline preconditions that are (a) disconnected from live config (garble_digit_floor=500 hardcoded literal in GarbleConfig.from_config, missing from PipelineConfig entirely), (b) unreachable for the primary corpus (latin_gibberish guard requires expected_script != 'Latn', blocking all German/English docs), (c) fed stale had_presentation_forms=False by 10 production call sites that construct throwaway ScriptContext instead of threading the properly-computed one, and (d) produce fully silent OK from validate_tree with zero secondary signal when all prongs miss -- a garble miss is indistinguishable from genuinely clean content. Each prong fix routinely destabilizes adjacent prongs because preconditions are inline if-guards buried in a 90-line function body, not declarative fields reviewable together.

#### Strategy

Consolidate garble detection into a declarative prong pipeline in six sequenced steps: (A) fix garble_digit_floor config bug, (B) eliminate throwaway ScriptContext(had_presentation_forms=False) at 10 call sites by threading the once-computed ScriptContext, (C) fix latin_gibberish unreachability by inverting the guard, (D) extract prongs into a declarative PRONG_TABLE with name/function/min_length/script_filter fields, (E) add low-confidence warning path on TreeGateResult, (F) add concatenated whole-tree fallback plus title inspection in _garble_check_nodes. Steps A-C are independent 1-5 line changes with clean rollback; D-F are additive refactors. Each step gets a corpus diff before merge.

#### Code Targets

**Target 1: Add garble_digit_floor field to PipelineConfig and read from GARBLE_DIGIT_FLOOR env var in from_env() (config.py:368-501)**
- **File:** `src/pageindex_mcp/config.py`
- **Lines:** 368-501
- **What:** Add garble_digit_floor field to PipelineConfig and read from GARBLE_DIGIT_FLOOR env var in from_env()
- **How:** Add field 'garble_digit_floor: int' to PipelineConfig class body (after garble_node_ratio_threshold). In from_env(), add 'garble_digit_floor=int(os.environ.get("GARBLE_DIGIT_FLOOR", "500"))' to the constructor call.
- **Constraint:** Default must remain 500 to preserve existing behavior; field goes in the garble-related group alongside garble_latin_gibberish_enabled, garble_latin_ratio, etc.

**Target 2: Fix GarbleConfig.from_config to read cfg.garble_digit_floor instead of hardcoded 500 (helpers/garble.py:463)**
- **File:** `src/pageindex_mcp/helpers/garble.py`
- **Lines:** 463
- **What:** Fix GarbleConfig.from_config to read cfg.garble_digit_floor instead of hardcoded 500
- **How:** Change line 463 from 'garble_digit_floor=500,' to 'garble_digit_floor=cfg.garble_digit_floor,'
- **Constraint:** Must match the PipelineConfig field name exactly; no behavioral change at default value

**Target 3: Fix latin_gibberish prong guard to fire on Latin-script and None-script documents (helpers/garble.py:389-393)**
- **File:** `src/pageindex_mcp/helpers/garble.py`
- **Lines:** 389-393
- **What:** Fix latin_gibberish prong guard to fire on Latin-script and None-script documents
- **How:** Change the guard from '_effective_script is not None and _effective_script != "Latn" and cfg.garble_latin_gibberish_enabled' to 'cfg.garble_latin_gibberish_enabled and (_effective_script is None or _effective_script != "Latn" or True)' -- effectively remove the script filter entirely, keeping only the config toggle. The prong is gated by latin_ratio_threshold and nonsense_threshold which are the real precision controls.
- **Constraint:** Must NOT remove the cfg.garble_latin_gibberish_enabled toggle (config killswitch). Corpus diff required: German T&C PDFs must not false-positive.

**Target 4: Thread ScriptContext properly when validate_tree receives a bare string: use ScriptContext.from_document with available text instead of throwaway had_presentation_forms=False (helpers/tree_validation.py:268-276)**
- **File:** `src/pageindex_mcp/helpers/tree_validation.py`
- **Lines:** 268-276
- **What:** Thread ScriptContext properly when validate_tree receives a bare string: use ScriptContext.from_document with available text instead of throwaway had_presentation_forms=False
- **How:** When expected_script is a bare string (not ScriptContext), use ScriptContext.from_script_str(expected_script) which already exists as a backward-compat factory. This is equivalent but makes the legacy path explicit. The real fix is ensuring callers pass ScriptContext.from_document -- see indexer/recovery targets.
- **Constraint:** Must remain backward-compatible with callers passing str|None; isinstance check on line 269 already handles ScriptContext

**Target 5: Thread had_presentation_forms from ScriptContext into TreeSignals.from_tree instead of falling back to False (helpers/tree_validation.py:187-194)**
- **File:** `src/pageindex_mcp/helpers/tree_validation.py`
- **Lines:** 187-194
- **What:** Thread had_presentation_forms from ScriptContext into TreeSignals.from_tree instead of falling back to False
- **How:** Line 194 sets _had_pf=False when expected_script is not a ScriptContext. Instead, when flat_text is available, scan for presentation forms ratio before NFKC normalization (same logic as ScriptContext.from_document). Alternatively, accept that the ScriptContext should have been computed upstream and log a warning.
- **Constraint:** Must not change behavior when a proper ScriptContext is passed (lines 187-189 already extract _had_pf correctly)

**Target 6: Eliminate throwaway ScriptContext(had_presentation_forms=False) in apply_promotions (helpers/verdict.py:276)**
- **File:** `src/pageindex_mcp/helpers/verdict.py`
- **Lines:** 276
- **What:** Eliminate throwaway ScriptContext(had_presentation_forms=False) in apply_promotions
- **How:** Thread the ScriptContext from compute_verdict (which receives expected_script: str|None|ScriptContext) through to apply_promotions as a new parameter. Replace the throwaway construction at line 276 with the threaded context.
- **Constraint:** apply_promotions signature change must be backward-compatible; add script_context as keyword-only with None default

**Target 7: Replace throwaway ScriptContext in pre_garble_probe with the already-computed script_context (client/indexer.py:424)**
- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 424
- **What:** Replace throwaway ScriptContext in pre_garble_probe with the already-computed script_context
- **How:** Line 1116 already computes ScriptContext.from_document(filename). Thread this to _convert_to_tree. Replace line 424 throwaway construction with the threaded script_context.
- **Constraint:** The script_context is computed at index() entry (line 1116) but _convert_to_tree receives expected_script as str. Add script_context parameter.

**Target 8: Replace throwaway ScriptContext in flat_garble_gate with threaded script_context (client/indexer.py:763-767)**
- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 763-767
- **What:** Replace throwaway ScriptContext in flat_garble_gate with threaded script_context
- **How:** Line 763-767 already has a conditional: 'script_context if script_context is not None else ScriptContext(...False...)'. Ensure script_context is always passed from the caller, eliminating the False fallback.
- **Constraint:** Verify script_context parameter flows from index() to _convert_to_tree to this flat path

**Target 9: Replace throwaway ScriptContext in vlm_fallback_garble with threaded script_context (client/indexer.py:791)**
- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 791
- **What:** Replace throwaway ScriptContext in vlm_fallback_garble with threaded script_context
- **How:** Same pattern as line 763: ensure script_context is always available, removing the had_presentation_forms=False fallback.
- **Constraint:** Same constraint as flat_garble_gate target

**Target 10: Replace throwaway ScriptContext in ocr_retry_keep_best with threaded script_context (client/recovery.py:228-231)**
- **File:** `src/pageindex_mcp/client/recovery.py`
- **Lines:** 228-231
- **What:** Replace throwaway ScriptContext in ocr_retry_keep_best with threaded script_context
- **How:** The recovery mixin receives expected_script as str. Thread the ScriptContext from the caller (indexer) through to _execute_ocr_retry. Replace the throwaway at line 228-231.
- **Constraint:** RecoveryMixin._execute_ocr_retry signature change must match indexer call sites

**Target 11: Replace throwaway ScriptContext in _text_layer_has_content with script_context parameter (converters/pictures.py:287-290)**
- **File:** `src/pageindex_mcp/converters/pictures.py`
- **Lines:** 287-290
- **What:** Replace throwaway ScriptContext in _text_layer_has_content with script_context parameter
- **How:** Add script_context parameter to _text_layer_has_content, use it instead of constructing ScriptContext(had_presentation_forms=False). Update callers.
- **Constraint:** Must not break existing callers; add as keyword-only with None default and fallback to from_script_str

**Target 12: Replace throwaway ScriptContext in _document_level_text_fallback with script_context parameter (converters/pictures.py:405-408)**
- **File:** `src/pageindex_mcp/converters/pictures.py`
- **Lines:** 405-408
- **What:** Replace throwaway ScriptContext in _document_level_text_fallback with script_context parameter
- **How:** Same pattern as _text_layer_has_content: accept script_context, thread from caller.
- **Constraint:** Same as _text_layer_has_content target

**Target 13: Replace throwaway ScriptContext in _attempt_tesseract_raster_recovery with threaded script_context (client/images.py:133)**
- **File:** `src/pageindex_mcp/client/images.py`
- **Lines:** 133
- **What:** Replace throwaway ScriptContext in _attempt_tesseract_raster_recovery with threaded script_context
- **How:** Accept script_context parameter, use instead of throwaway construction.
- **Constraint:** Backward-compat: default to ScriptContext.from_script_str(expected_script) when not provided

**Target 14: Replace throwaway ScriptContext fallback in _garble_ratio with proper threading (helpers/garble.py:735-738)**
- **File:** `src/pageindex_mcp/helpers/garble.py`
- **Lines:** 735-738
- **What:** Replace throwaway ScriptContext fallback in _garble_ratio with proper threading
- **How:** The function already accepts script_context parameter but falls back to had_presentation_forms=False. Ensure all callers (TreeSignals.from_tree at tree_validation.py:209) pass the ScriptContext they already have.
- **Constraint:** Backward compat: keep the fallback for any remaining callers but add a deprecation warning

**Target 15: Add concatenated whole-tree fallback when no per-node garbling detected, and inspect title fields for garbling (helpers/garble.py:595-659)**
- **File:** `src/pageindex_mcp/helpers/garble.py`
- **Lines:** 595-659
- **What:** Add concatenated whole-tree fallback when no per-node garbling detected, and inspect title fields for garbling
- **How:** After the per-node loop, if garbled==0 and total concatenated text exceeds garble_digit_floor, run garble_prongs on the concatenated text as a secondary check. Title inspection is already present at lines 637-650 (added in prior fix). The concatenated fallback catches per-node decomposition undercutting digit_floor.
- **Constraint:** Must not double-count nodes already flagged as garbled; concatenated check is additive only when per-node returned 0

**Target 16: Add warnings field to TreeGateResult for low-confidence garble signals (helpers/types.py:41-61)**
- **File:** `src/pageindex_mcp/helpers/types.py`
- **Lines:** 41-61
- **What:** Add warnings field to TreeGateResult for low-confidence garble signals
- **How:** Add 'warnings: list[str] = field(default_factory=list)' to TreeGateResult dataclass. Exclude from __iter__ (backward-compat tuple unpacking). Populated by validate_tree when any prong fires at sub-threshold confidence or when garble_ratio is above zero but below garble_threshold.
- **Constraint:** Must not change __iter__ behavior; existing (ok, reason) unpacking must continue working

**Target 17: Populate TreeGateResult.warnings when validate_tree returns OK but garble signals are non-zero (helpers/tree_validation.py:296-308)**
- **File:** `src/pageindex_mcp/helpers/tree_validation.py`
- **Lines:** 296-308
- **What:** Populate TreeGateResult.warnings when validate_tree returns OK but garble signals are non-zero
- **How:** When returning the OK TreeGateResult, check sig.garble_ratio > 0.0 (sub-threshold garbling detected by TreeSignals.from_tree). If so, add a warning string like 'sub_threshold_garble: ratio={sig.garble_ratio:.3f}' to the warnings list. Also check if any GATE_TABLE entry came close to firing.
- **Constraint:** Must not change ok=True result; warnings are advisory only. No behavioral change to downstream verdict computation.

#### Wiring Checks

| Symbol | Must Be Imported By | Check Type | Rationale |
|--------|-------------------|-----------|-----------|
| `PipelineConfig.garble_digit_floor` | `['src/pageindex_mcp/helpers/garble.py']` | call | — |
| `TreeGateResult.warnings` | `['src/pageindex_mcp/helpers/tree_validation.py']` | call | — |
| `ScriptContext.from_document` | `['src/pageindex_mcp/client/indexer.py']` | call | — |
| `ScriptContext` | `['src/pageindex_mcp/helpers/verdict.py', 'src/pageindex_mcp/client/recovery.py', 'src/pageindex_mcp/converters/pictures.py', 'src/pageindex_mcp/client/images.py']` | import | — |
| `GarbleConfig.from_config` | `['src/pageindex_mcp/helpers/garble.py']` | call | — |

#### Test Requirements

| Test File | What to Test | Assertion Type |
|-----------|-------------|-----------------|
| `tests/test_garble_detection.py` | GarbleConfig.from_config reads cfg.garble_digit_floor instead of hardcoded 500: construct a mock PipelineConfig with garble_digit_floor=100, verify GarbleConfig.from_config produces config with garble_digit_floor=100 | CONTRACT |
| `tests/test_garble_detection.py` | PipelineConfig.from_env reads GARBLE_DIGIT_FLOOR env var: set env var to 300, verify pipeline_config.garble_digit_floor==300; unset, verify default 500 | CONTRACT |
| `tests/test_garble_detection.py` | latin_gibberish prong fires for Latin-script corpus with nonsense tokens: pass expected_script='Latn' (or None) with morphologically-nonsense Latin tokens exceeding ratio threshold, verify 'latin_gibberish' in garble_prongs result | REGRESSION |
| `tests/test_garble_detection.py` | latin_gibberish prong does NOT fire for clean German prose: pass real German T&C text with expected_script='Latn', verify 'latin_gibberish' not in result (false-positive guard) | REGRESSION |
| `tests/test_zone1_flat_gate_asymmetry.py` | ScriptContext.had_presentation_forms threads through validate_tree to _gate_garbling and _gate_node_garbling: construct ScriptContext with had_presentation_forms=True, pass to validate_tree, verify presentation_forms prong fires in the garble gate | WIRING |
| `tests/test_zone1_flat_gate_asymmetry.py` | apply_promotions receives and uses ScriptContext instead of constructing throwaway: mock detect_garble, call compute_verdict with ScriptContext(had_presentation_forms=True), verify the ScriptContext passed to detect_garble inside apply_promotions has had_presentation_forms=True | WIRING |
| `tests/test_garble_detection.py` | TreeGateResult.warnings is populated when garble_ratio is sub-threshold but non-zero: build a tree where garble_ratio lands between 0 and garble_threshold (0.05), verify TreeGateResult.ok==True and TreeGateResult.warnings is non-empty | CONTRACT |
| `tests/test_garble_detection.py` | _garble_check_nodes concatenated fallback: build a tree with many small nodes each under garble_digit_floor but whose concatenation exceeds it and is garbled, verify garbled count > 0 | REGRESSION |
| `tests/test_garble_detection.py` | Exhaustiveness: every prong name returned by garble_prongs is in a known set (PRONG_TABLE names if implemented, else a frozen set of valid prong names); no silent additions | EXHAUSTIVENESS |
| `tests/test_zone1_flat_gate_asymmetry.py` | End-to-end: ScriptContext.from_document flows from indexer.index() through _convert_to_tree, validate_tree, compute_verdict without any had_presentation_forms=False reconstruction: instrument ScriptContext constructor, verify no source='pre_garble_probe' or 'apply_promotions' constructions with had_presentation_forms=False | INTEGRATION |

#### Corpus Validation

- **Affected Document Classes:** ['German T&C PDFs (Latin script - latin_gibberish prong activation)', 'Arabic scanned PDFs (presentation-forms detection fix)', 'MOU documents (garble oscillation history)', 'ward_597 (garbled_blocks=0 despite visible garbling)']
- **Expected Verdict Direction:** IMPROVE
- **Spot Check Count:** 8

---

### Zone: OCR Strategy Bifurcation (Wave 1, Priority 3)

**Severity:** CRITICAL  
**Estimated Complexity:** MEDIUM  
**Key Files:** `picture_plane.py`, `converters/pictures.py`, `client/indexer.py`, `client/images.py`, `client/recovery.py`, `converters/ocr_langs.py`, `metrics/definitions.py`

#### Mechanism to Eliminate

Three independent OCR entry points make structurally coupled decisions independently:

1. **PDF page-level escalation** (`picture_plane.py:26-46`) — calls `decide_ocr_strategy()` based on page metrics
2. **Per-picture crop OCR** (`converters/pictures.py:249-259`) — handles Tesseract failures silently with bare `except Exception`
3. **Standalone-image pipeline** (`client/indexer.py:656-685`) — bypasses `decide_ocr_strategy()` entirely, hardcodes language list `["ara", "deu", "eng"]` instead of calling `detect_ocr_langs()`, skips `splice_picture_text_for_tree()` before tree construction, duplicates constants

Result: Each filter added for one document class silently degrades another because there is no single decision point accounting for `document_type`. Example regressions:
- Arabic scanned images: standalone path hardcodes `["ara", "deu", "eng"]` but should call `detect_ocr_langs(filename)` to detect script from filename
- Image-only PDFs: forced OCR in retry path (RFC-027/RFC-028) unconditionally overwrites content without keep-best heuristic
- Tree path picture splicing: standalone images never call `splice_picture_text_for_tree()`, leaving OCR text in metadata only, not in tree

#### Strategy

**Consolidate + Type-Safe Contract**

1. **Extend `decide_ocr_strategy()`** with `document_type` parameter and enhanced return contract
2. **Add `OcrPlan` fields** to `OcrDecision`: `ocr_langs`, `splice_required`
3. **Narrow exception handling** in `_tesseract_ocr_image()` (from bare `Exception` to specific subprocess/file errors)
4. **Add Prometheus counter** `TESSERACT_OCR_FAILURE_TOTAL` for local OCR failures
5. **Deduplicate constants** into `images.py` as canonical source
6. **Insert `splice_picture_text_for_tree()` call** into standalone-image path before tree construction
7. **Fix `_recover_image_dominant_ocr()`** to use `keep_best=True` for OCR retry (prevent unconditional content regression)

**Implementation Sequence (7 Steps):**
1. Hardcoded lang fix (1 line, indexer.py:658)
2. Constant dedup (remove from indexer.py, import from images.py)
3. Exception narrowing + metric (pictures.py:257, add TESSERACT_OCR_FAILURE_TOTAL)
4. Splice insertion for standalone images (indexer.py after line 679)
5. `OcrPlan` unification in `picture_plane.py` (add fields, extend signature)
6. Update callers (none yet; feature-flag for shadow validation)
7. Keep-best fix in recovery.py (line 416)

**Feature Flag:** `UNIFIED_OCR_PLAN_ENABLED` (default false) — old path runs until shadow validation completes; new OcrDecision fields backward-compatible.

#### Code Targets

**Target 1: Fix hardcoded OCR language list (indexer.py:658)**
- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 658
- **What:** Replace hardcoded `["ara", "deu", "eng"]` with `detect_ocr_langs(filename)` call
- **How:** Change line 658 from `img_langs = await asyncio.to_thread(ensure_tessdata, ["ara", "deu", "eng"])` to `img_langs = await asyncio.to_thread(ensure_tessdata, detect_ocr_langs(filename))`. `detect_ocr_langs` is already imported at line 33.
- **Constraint:** `detect_ocr_langs()` returns `['deu', 'eng']` for empty/Latin filenames, preserving prior default for non-Arabic files. Enables Arabic detection from filename script.

**Target 2: Deduplicate constants into images.py**
- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 209, 220–222, 227
- **What:** Remove duplicated `_IMAGE_EXTS`, `_IMAGE_STANDALONE_PIPELINE_ENABLED`, `MIN_STANDALONE_IMAGE_MD_CHARS`; import from `images.py` instead
- **How:**
  - Delete local definitions at lines 209, 220–222, 227
  - Add `from .images import _IMAGE_EXTS, MIN_STANDALONE_IMAGE_MD_CHARS, _IMAGE_STANDALONE_PIPELINE_ENABLED` to imports
  - Keep `_SUPPORTED` computed from imported `_IMAGE_EXTS` (line 210 union operation)
- **Constraint:** `images.py` remains the canonical source; any future constant changes affect all paths uniformly.

**Target 3: Narrow exception handling in _tesseract_ocr_image + add Prometheus counter**
- **File:** `src/pageindex_mcp/converters/pictures.py`
- **Lines:** 249–259, specifically line 257
- **What:** Replace bare `except Exception` with specific exception list; add `TESSERACT_OCR_FAILURE_TOTAL` counter
- **How:**
  - Change `except Exception as exc:` (line 257) to `except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError, OSError) as exc:`
  - Add import: `from ..metrics import TESSERACT_OCR_FAILURE_TOTAL`
  - Increment counter before logger.warning: `TESSERACT_OCR_FAILURE_TOTAL.labels(reason=type(exc).__name__).inc()`
- **Constraint:** Must still return `''` on failure (never raise). Local OCR failures must not propagate per HR3 contract.

**Target 4: Add TESSERACT_OCR_FAILURE_TOTAL metric**
- **File:** `src/pageindex_mcp/metrics/definitions.py`
- **Lines:** new (append after existing Counter definitions)
- **What:** Add Prometheus counter for Tesseract per-picture OCR failures
- **How:** Add `TESSERACT_OCR_FAILURE_TOTAL = Counter('pageindex_tesseract_ocr_failure_total', 'Tesseract per-picture OCR failures', ['reason'])` following existing Counter naming (e.g., `AGPL_FALLBACK_TOTAL`). Export from `metrics/__init__.py`.
- **Constraint:** Label `'reason'` carries exception class name for failure triage.

**Target 5: Extend OcrDecision + decide_ocr_strategy with document_type**
- **File:** `src/pageindex_mcp/picture_plane.py`
- **Lines:** 26–46 (OcrDecision), 344–386 (decide_ocr_strategy)
- **What:** Add `document_type` parameter to `decide_ocr_strategy`; add `ocr_langs` and `splice_required` fields to `OcrDecision`
- **How:**
  - Add parameter to `decide_ocr_strategy`: `document_type: Literal['pdf','image','html','text','xlsx'] = 'pdf'`
  - Add dataclass fields to `OcrDecision`: 
    - `ocr_langs: list[str] = field(default_factory=lambda: ['deu','eng'])`
    - `splice_required: bool = False`
  - Logic: when `document_type='image'`, set `mode=FULL_PAGE`, `splice_required=True`. For PDF, preserve existing logic.
  - Gate behind `UNIFIED_OCR_PLAN_ENABLED` env var (default false)
- **Constraint:** Backward-compatible (existing callers get `pdf` default). `OcrDecision` remains frozen (`dataclass(frozen=True)`).

**Target 6: Insert splice_picture_text_for_tree into standalone-image path**
- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 679–685
- **What:** Call `splice_picture_text_for_tree()` for standalone images before `md_to_tree()`
- **How:** After line 679 (`state.md_content = md_content`), before writing to temp file, insert:
  ```python
  if state.pic_results and TREE_PATH_PICTURE_SPLICE_ENABLED:
      md_content = splice_picture_text_for_tree(md_content, state.pic_results)
      state.md_content = md_content
  ```
  This mirrors PDF path at lines 587–589. `splice_picture_text_for_tree` already imported at line 43.
- **Constraint:** Gate on `TREE_PATH_PICTURE_SPLICE_ENABLED` to match PDF path behavior.

**Target 7: Fix _recover_image_dominant_ocr keep-best logic**
- **File:** `src/pageindex_mcp/client/recovery.py`
- **Lines:** 416
- **What:** Change `use_keep_best=False` to `use_keep_best=True` in OCR retry
- **How:** At line 416, change `use_keep_best=False` to `use_keep_best=True` in `_execute_ocr_retry()` call.
- **Constraint:** Other recovery paths (garble, low_content) already pass `use_keep_best=True` (lines 342, 376). This is consistency fix to prevent RFC-027/RFC-028 regression class.

#### Wiring Checks

| Symbol | Must Be Imported By | Check Type | Rationale |
|--------|-------------------|-----------|-----------|
| `TESSERACT_OCR_FAILURE_TOTAL` | `converters/pictures.py`, `metrics/__init__.py` | import | Metric must be defined and exported before pictures.py can increment it |
| `_IMAGE_EXTS` | `client/indexer.py` | import | Deduplication: indexer imports from images.py, not local constant |
| `MIN_STANDALONE_IMAGE_MD_CHARS` | `client/indexer.py` | import | Deduplication: same source |
| `_IMAGE_STANDALONE_PIPELINE_ENABLED` | `client/indexer.py` | import | Deduplication: same source |
| `detect_ocr_langs` | `client/indexer.py` | call | Language detection must be called at line 658, not hardcoded |
| `splice_picture_text_for_tree` | `client/indexer.py` | call | Standalone-image path must call splice before tree construction |
| `decide_ocr_strategy` | `client/indexer.py` | call | PDF path already calls; standalone path to call in follow-up wave |

#### Test Requirements

| Test File | What to Test | Assertion Type |
|-----------|-------------|-----------------|
| `tests/test_ocr_decision.py` | `decide_ocr_strategy(document_type='image')` returns `OcrDecision` with `mode=FULL_PAGE`, `splice_required=True`; `document_type='pdf'` preserves existing truth table; `OcrDecision.ocr_langs` defaults to `['deu','eng']` when not overridden | EXHAUSTIVENESS |
| `tests/test_image_blocks.py` | Standalone image path calls `splice_picture_text_for_tree()` before `md_to_tree()`: given `.jpg` with OCR text in `pic_results`, tree must contain OCR-recovered text. Test `TREE_PATH_PICTURE_SPLICE_ENABLED=false` skips splice. | REGRESSION |
| `tests/test_image_blocks.py` | Standalone image path calls `detect_ocr_langs(filename)`: filename with Arabic characters must include `'ara'` in langs passed to `ensure_tessdata()` | REGRESSION |
| `tests/test_rfc_converters.py` | `_tesseract_ocr_image()` increments `TESSERACT_OCR_FAILURE_TOTAL` on subprocess/FileNotFoundError/OSError; returns `''`; does NOT catch arbitrary exceptions (KeyboardInterrupt) | CONTRACT |
| `tests/test_rfc_promotions.py` | `_recover_image_dominant_ocr()` with `keep_best=True`: when OCR retry produces fewer chars, pre-retry content preserved | REGRESSION |
| `tests/test_client.py` | `MIN_STANDALONE_IMAGE_MD_CHARS`, `_IMAGE_EXTS` imported from `images.py` in indexer.py (no local redefinition); monkeypatch images.MIN_STANDALONE_IMAGE_MD_CHARS affects indexer behavior | WIRING |

#### Corpus Validation

- **Affected Document Classes:** 
  - Standalone `.jpg`/`.png`/`.jpeg`/`.tiff`/`.tif` images in `doc_store/`
  - Arabic scanned images (language detection regression class)
  - Image-dominant PDFs (keep-best guard)
- **Expected Verdict Direction:** IMPROVE (fewer content losses, better language coverage)
- **Spot Check Count:** 5 documents, each class
- **Regression Guard:** Before/after verdicts must not degrade; Low Content / Garble scores must not oscillate

---

### Zone: Verdict Promotion / Quality Gate Stack (Wave 2, Priority 4)

**Severity:** CRITICAL  
**Estimated Complexity:** MEDIUM  
**Depends On:** Garble Detection Fragmentation, OCR Strategy Bifurcation  
**Key Files:** `helpers/verdict.py`, `helpers/gates.py`, `helpers/types.py`, `registry/queries.py`, `client/indexer.py`, `worker/registry_mirror.py`, `registry_backfill/reconcile.py`, `config.py`

#### Mechanism to Eliminate

Sequential promotion cascade in apply_promotions() evaluates rescue paths in fixed order where an early match bypasses all subsequent paths, combined with a Postgres verdict-priority CAS (PASS=3>MARGINAL=2>FAIL=1>ERROR=0) that can only upgrade or tie, never downgrade. This creates three interlocking failure modes: (1) a promotion path that fires early (e.g. image_enrichment_promoted) without a sufficient content-volume floor lets zero-content or garbled documents reach PASS, which the SQL CAS then permanently locks in -- a later improved garble check correctly classifying the doc as FAIL cannot self-heal the stored verdict; (2) threshold changes calibrated against one problematic document (e.g. low_content_density from 500 to 150 chars/node) cause corpus-wide oscillation because the sequential cascade gives no visibility into near-miss candidates across the corpus; (3) the verdict-priority CASE expression is copy-pasted verbatim 4 times in _UPSERT_SQL (lines 67, 73, 79, 85 of queries.py) diverging from the canonical VERDICT_PRIORITY dict in helpers/types.py:37, meaning a priority-map change must be replicated in 5 locations.

#### Strategy

Three-PR restructuring: PR1 extracts the 4x duplicated verdict-priority CASE into a Postgres function referencing the canonical VERDICT_PRIORITY dict and adds a force_verdict_override boolean to upsert_doc (default False, no behavioral change). PR2 refactors apply_promotions() from sequential first-match cascade to score-all-then-pick-best pattern using a PromotionCandidate list with uniform content-floor filtering, extracting each promotion path into a named _try_* function. PR3 wires force_verdict_override=True into re-ingestion calls when pipeline_version is strictly newer, gated behind VERDICT_DOWNGRADE_ENABLED config flag (default False).

#### Code Targets

**Target 1: Extract 4x duplicated verdict-priority CASE expression into a SQL helper function verdict_priority(text) and a Python-side _VERDICT_PRIORITY_SQL constant generated from VERDICT_PRIORITY dict (registry/queries.py:64-90)**
- **File:** `src/pageindex_mcp/registry/queries.py`
- **Lines:** 64-90
- **What:** Extract 4x duplicated verdict-priority CASE expression into a SQL helper function verdict_priority(text) and a Python-side _VERDICT_PRIORITY_SQL constant generated from VERDICT_PRIORITY dict
- **How:** Replace the 4 identical CASE WHEN expressions (verified at lines 67, 73, 79, 85) with calls to a single verdict_priority() SQL function generated from helpers/types.py:37's VERDICT_PRIORITY dict at module load time.
- **Constraint:** The SQL function must be created via a migration or CREATE OR REPLACE in a startup hook; the priority mapping must be generated from VERDICT_PRIORITY dict, never hardcoded separately

**Target 2: Add force_verdict_override parameter to upsert_doc() that bypasses the verdict-priority CAS guard (registry/queries.py:94-134)**
- **File:** `src/pageindex_mcp/registry/queries.py`
- **Lines:** 94-134
- **What:** Add force_verdict_override parameter to upsert_doc() that bypasses the verdict-priority CAS guard
- **How:** Add optional bool parameter force_verdict_override=False to upsert_doc(); when True use an alternate SQL template where verdict columns always take EXCLUDED values.
- **Constraint:** Default must be False; force_verdict_override=True must still respect processed_at CAS guard

**Target 3: Refactor apply_promotions() (verified starts exactly at line 219, ends at 347) from sequential first-match cascade to score-all-then-pick-best using PromotionCandidate list (helpers/verdict.py:219-347)**
- **File:** `src/pageindex_mcp/helpers/verdict.py`
- **Lines:** 219-347
- **What:** Refactor apply_promotions() (verified starts exactly at line 219, ends at 347) from sequential first-match cascade to score-all-then-pick-best using PromotionCandidate list
- **How:** Extract each promotion path into named _try_* functions returning Optional[PromotionCandidate]; collect, filter below floor, pick highest priority.
- **Constraint:** image_enrichment_promoted must retain RFC-022 B2 priority weight

**Target 4: Add PromotionCandidate type; VERDICT_PRIORITY confirmed canonical (verified at types.py:37) (helpers/types.py:37)**
- **File:** `src/pageindex_mcp/helpers/types.py`
- **Lines:** 37
- **What:** Add PromotionCandidate type; VERDICT_PRIORITY confirmed canonical (verified at types.py:37)
- **How:** Add frozen dataclass PromotionCandidate; import-time assertion VERDICT_PRIORITY values unique/ordered.
- **Constraint:** priority: higher is better

**Target 5: Wire force_verdict_override into re-ingestion path (compute_verdict call sites verified exact at lines 853 and 973) (client/indexer.py:853-860, 973-980)**
- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 853-860, 973-980
- **What:** Wire force_verdict_override into re-ingestion path (compute_verdict call sites verified exact at lines 853 and 973)
- **How:** Pass force_verdict_override=True into verdict_fields dict when VERDICT_DOWNGRADE_ENABLED and pipeline_version strictly newer.
- **Constraint:** Gated behind VERDICT_DOWNGRADE_ENABLED, default False

**Target 6: Thread force_verdict_override through to upsert_doc call (fields.update verified at line 101, upsert_doc call verified at line 104) (worker/registry_mirror.py:98-104)**
- **File:** `src/pageindex_mcp/worker/registry_mirror.py`
- **Lines:** 98-104
- **What:** Thread force_verdict_override through to upsert_doc call (fields.update verified at line 101, upsert_doc call verified at line 104)
- **How:** Pop force_verdict_override from fields dict before calling upsert_doc so it's a kwarg not a column value.
- **Constraint:** Must not be persisted to MinIO sidecar

**Target 7: Add VERDICT_DOWNGRADE_ENABLED config flag (config.py:)**
- **File:** `src/pageindex_mcp/config.py`
- **Lines:** 
- **What:** Add VERDICT_DOWNGRADE_ENABLED config flag
- **How:** Add to PipelineConfig dataclass (class verified present at config.py:368), sourced from env var, default False.
- **Constraint:** Must be a PipelineConfig field, not module-level constant

#### Wiring Checks

| Symbol | Must Be Imported By | Check Type | Rationale |
|--------|-------------------|-----------|-----------|
| `PromotionCandidate` | `['src/pageindex_mcp/helpers/verdict.py', 'src/pageindex_mcp/helpers/__init__.py']` | import | — |
| `verdict_priority` | `['src/pageindex_mcp/registry/queries.py']` | call | — |
| `force_verdict_override` | `['src/pageindex_mcp/registry/queries.py', 'src/pageindex_mcp/worker/registry_mirror.py', 'src/pageindex_mcp/client/indexer.py']` | call | — |
| `VERDICT_DOWNGRADE_ENABLED` | `['src/pageindex_mcp/client/indexer.py']` | import | — |
| `_try_image_enrichment` | `['src/pageindex_mcp/helpers/verdict.py']` | call | — |
| `_try_flat_promotion` | `['src/pageindex_mcp/helpers/verdict.py']` | call | — |
| `_try_structural_pass` | `['src/pageindex_mcp/helpers/verdict.py']` | call | — |
| `_try_ocr_promotion` | `['src/pageindex_mcp/helpers/verdict.py']` | call | — |
| `_try_content_class_promotion` | `['src/pageindex_mcp/helpers/verdict.py']` | call | — |
| `_try_small_doc_promotion` | `['src/pageindex_mcp/helpers/verdict.py']` | call | — |

#### Test Requirements

| Test File | What to Test | Assertion Type |
|-----------|-------------|-----------------|
| `tests/test_verdict_promotion_candidates.py` | Each _try_* extractor boundary cases; score-all-then-pick-best matches old cascade for existing fixtures. | EXHAUSTIVENESS |
| `tests/test_verdict_promotion_candidates.py` | PromotionCandidate priority ordering, image_enrichment_promoted wins. | CONTRACT |
| `tests/test_rfc037_verdict_cas.py` | force_verdict_override bypass behavior and default preservation; SQL verdict_priority() matches Python dict. | CONTRACT |
| `tests/test_rfc037_verdict_cas.py` | SQL verdict_priority function mapping matches VERDICT_PRIORITY dict exactly. | REGRESSION |
| `tests/test_verdict_promotion_candidates.py` | RFC-025/023/036 regression fixtures. | REGRESSION |
| `tests/test_registry.py` | force_verdict_override threads through _upsert_registry_row -> upsert_doc without persisting to MinIO sidecar. | WIRING |
| `tests/test_compute_verdict.py` | compute_verdict with VERDICT_DOWNGRADE_ENABLED True/False and version comparisons. | INTEGRATION |

#### Corpus Validation

- **Affected Document Classes:** ['federal_decree_law_no_33', 'marsoom_33', 'penal_code', 'cabinet_resolution_no_96', 'sla_document', 'reitlehrer', 'mou_document']
- **Expected Verdict Direction:** STABLE
- **Spot Check Count:** 7

---

### Zone: Multi-Store Dual-Write Consistency (Wave 3, Priority 1)

**Severity:** HIGH  
**Estimated Complexity:** MEDIUM  
**Depends On:** Verdict Promotion / Quality Gate Stack  
**Key Files:** `client/indexer.py`, `converters_cli.py`, `worker/registry_mirror.py`, `worker/job.py`, `storage/documents.py`, `storage/hash_cache.py`, `storage/verdict.py`, `registry_backfill/cleanup.py`, `registry/queries.py`

#### Mechanism to Eliminate

Fan-out dual-write where converter child writes MinIO artifacts then parent re-reads MinIO via read_registry_fields() to extract registry columns for Postgres upsert. If the MinIO write is not yet read-visible, the parent gets partial/empty data, producing a Postgres row with missing fields. This partial row then gets deleted by _delete_stale_rows which treats empty processed_at as 'old enough to delete'. The pattern is compounded by three secondary HR2 violations: (1) hash_cache_delete only issues Redis HDEL, never purging the legacy MinIO blob hashes/processed_hashes.json, leaving filename-to-hash PII correlation surviving erasure; (2) staging objects keyed as uploads/staging/<job_id>/<filename> have no stored job_id-to-doc_id mapping, placing them outside delete_doc's uploads/<doc_id>/ scan; (3) delete_doc's 203-line monolithic inline cascade makes store omissions likely when adding new derived stores.

#### Strategy

Eliminate MinIO re-read by construction: expand the child process JSON return (converters_cli.py) to carry all _REGISTRY_FIELDS alongside existing verdict_fields, so _upsert_registry_row receives the full payload directly from the child's stdout. This closes the persistence-timing race window for ALL registry columns, not just verdict columns. Secondarily: (A) add legacy-blob purge to hash_cache_delete for HR2 compliance, (B) add a job_id-to-doc_id Redis mapping written at job completion so staging objects become reachable by delete_doc, (C) extract delete_doc's inline cascade into a declarative erasure manifest (list of (store, key_pattern, required) tuples iterated by a compact driver loop), and (D) invert _delete_stale_rows' empty-processed_at default so partial-write rows get the age-guard grace period instead of being treated as immediately stale.

#### Code Targets

**Target 1: Add last_registry_fields stash alongside existing last_verdict_fields in _persist_tree_result (client/indexer.py:1063-1072)**
- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 1063-1072
- **What:** Add last_registry_fields stash alongside existing last_verdict_fields in _persist_tree_result
- **How:** After line 1071 (last_verdict_fields assignment), add self.last_registry_fields = { 'doc_name': filename, 'source_url': source_url, 'processed_at': processed_at, 'sha256': sha256, 'doc_description': state.result.get('doc_description', ''), 'product': '', 'tier': '', 'doc_family': '', 'effective_date': '', 'node_count': len(structure) } containing all _REGISTRY_FIELDS values computed in-memory during persist. This dict is what the parent would otherwise re-read from MinIO via read_registry_fields.
- **Constraint:** Must include every key in _REGISTRY_FIELDS (verdict.py:225-236) plus node_count; values must match exactly what save_doc writes to MinIO so the parent receives identical data without a re-read

**Target 2: Add last_registry_fields stash in _persist_flat_result (flat doc path) (client/indexer.py:938-947)**
- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 938-947
- **What:** Add last_registry_fields stash in _persist_flat_result (flat doc path)
- **How:** After line 946 (last_verdict_fields assignment for flat docs), add self.last_registry_fields = { 'doc_name': filename, 'source_url': source_url, 'processed_at': processed_at, 'sha256': sha256, 'content_class': content_class, 'doc_description': flat_desc, 'product': '', 'tier': '', 'doc_family': '', 'effective_date': '', 'node_count': 0 } mirroring the tree-path stash for flat documents (node_count=0 matches read_registry_fields behavior for flat docs).
- **Constraint:** content_class must be included for flat docs; node_count must be 0 to match read_registry_fields flat-doc convention

**Target 3: Surface last_registry_fields in the child's stdout JSON payload alongside verdict_fields (converters_cli.py:162-169)**
- **File:** `src/pageindex_mcp/converters_cli.py`
- **Lines:** 162-169
- **What:** Surface last_registry_fields in the child's stdout JSON payload alongside verdict_fields
- **How:** After the verdict_fields block (line 168), add: registry_fields = getattr(client, 'last_registry_fields', None); if registry_fields: payload['registry_fields'] = registry_fields. Pattern identical to existing verdict_fields surfacing. Backward-compatible: old workers that do not read registry_fields simply ignore the extra key.
- **Constraint:** Must not emit registry_fields when None (backward compat with older workers); key name must be 'registry_fields' to match the new _upsert_registry_row parameter

**Target 4: Extract registry_fields from child result and pass to _upsert_registry_row (worker/job.py:355-362)**
- **File:** `src/pageindex_mcp/worker/job.py`
- **Lines:** 355-362
- **What:** Extract registry_fields from child result and pass to _upsert_registry_row
- **How:** After verdict_fields = result.get('verdict_fields') (approx line 357), add: registry_fields = result.get('registry_fields'). Change the _upsert_registry_row call to pass registry_fields=registry_fields as an additional kwarg. Falls back gracefully when registry_fields is absent (older child binaries).
- **Constraint:** Must be backward-compatible: when registry_fields is None, _upsert_registry_row falls back to existing read_registry_fields MinIO-read path

**Target 5: Accept optional registry_fields dict; when present, skip read_registry_fields MinIO re-read (worker/registry_mirror.py:55-135)**
- **File:** `src/pageindex_mcp/worker/registry_mirror.py`
- **Lines:** 55-135
- **What:** Accept optional registry_fields dict; when present, skip read_registry_fields MinIO re-read
- **How:** Add parameter registry_fields: dict[str, Any] | None = None to _upsert_registry_row signature. Inside the try block, replace the unconditional `fields = await asyncio.to_thread(read_registry_fields, doc_id, content_class)` with: if registry_fields is not None, use fields = dict(registry_fields) (copy to avoid mutation); fields['doc_id'] = doc_id; else fall back to existing MinIO read. Then merge verdict_fields on top as before. This eliminates the persistence-timing race by construction when registry_fields is available.
- **Constraint:** When registry_fields is None, behavior must be identical to current code (backward compat for preprocess_client.py batch CLI and reconcile callers). The verdict_fields overlay must still apply on top of registry_fields.

**Target 6: Purge legacy MinIO blob entry alongside Redis HDEL (HR2 compliance) (storage/hash_cache.py:68-72)**
- **File:** `src/pageindex_mcp/storage/hash_cache.py`
- **Lines:** 68-72
- **What:** Purge legacy MinIO blob entry alongside Redis HDEL (HR2 compliance)
- **How:** After the existing HDEL call, add a best-effort legacy-blob purge: try to load _load_legacy_minio_hash_cache(), check if filename is a key, if so pop it and re-PUT the shrunk blob via minio_ops.get_minio().put_object(). Wrap in try/except (best-effort, log warning on failure). If the blob does not exist (NoSuchKey) or filename is absent, no-op. This closes the HR2 gap where filename+hash correlation survived erasure in the legacy store.
- **Constraint:** Must be best-effort (never fail the erasure cascade). Last-writer-wins is acceptable since the legacy blob is append-shrink only. Must import _load_legacy_minio_hash_cache lazily to avoid circular imports.

**Target 7: Extract monolithic 7-step inline cascade into declarative erasure manifest (storage/documents.py:141-343)**
- **File:** `src/pageindex_mcp/storage/documents.py`
- **Lines:** 141-343
- **What:** Extract monolithic 7-step inline cascade into declarative erasure manifest
- **How:** Define an ErasureStep dataclass/NamedTuple at module level: (name: str, execute: Callable[[str, str|None, MinioClient], Awaitable[None]|None], required: bool). Build _ERASURE_MANIFEST as a list of ErasureStep tuples, one per current inline step (1-7), each wrapping its existing logic in a small async callable. Replace the 203-line inline body with a 15-line driver loop that iterates _ERASURE_MANIFEST, calls each step, catches exceptions, appends to errors[] if the step failed but is required. Adding a new derived store becomes a one-line manifest entry.
- **Constraint:** Manifest ordering must match CLAUDE.md HR2 cascade order: uploads -> processed -> meta -> Redis -> hash-cache -> registry -> preloaded. Each step's required flag must match current error-reporting behavior (some steps tolerate NoSuchKey, others report).

**Target 8: Invert empty-processed_at stale candidate default: protect instead of delete (registry_backfill/cleanup.py:56-75)**
- **File:** `src/pageindex_mcp/registry_backfill/cleanup.py`
- **Lines:** 56-75
- **What:** Invert empty-processed_at stale candidate default: protect instead of delete
- **How:** In _delete_stale_rows, change the empty-processed_at handling from 'treat as old enough (continue)' to 'age_protected.add(doc_id)' so rows with empty/missing processed_at get the grace period instead of being immediately deletable. This prevents partial-write rows (whose processed_at was not yet written due to the dual-write race) from being deleted by the next reconcile tick.
- **Constraint:** Config-gated: add a setting (e.g. cleanup_protect_empty_processed_at, default True) so truly stale legacy rows can still be swept via a manual override. Rows with parseable but old processed_at are unaffected.

#### Wiring Checks

| Symbol | Must Be Imported By | Check Type | Rationale |
|--------|-------------------|-----------|-----------|
| `last_registry_fields` | `['src/pageindex_mcp/converters_cli.py']` | dispatch | — |
| `registry_fields` | `['src/pageindex_mcp/worker/job.py', 'src/pageindex_mcp/worker/registry_mirror.py']` | call | — |
| `_ERASURE_MANIFEST` | `['src/pageindex_mcp/storage/documents.py']` | dispatch | — |
| `ErasureStep` | `['src/pageindex_mcp/storage/documents.py']` | isinstance | — |
| `_purge_legacy_hash_entry` | `['src/pageindex_mcp/storage/hash_cache.py']` | call | — |
| `cleanup_protect_empty_processed_at` | `['src/pageindex_mcp/registry_backfill/cleanup.py']` | import | — |

#### Test Requirements

| Test File | What to Test | Assertion Type |
|-----------|-------------|-----------------|
| `tests/test_registry_mirror.py` | When registry_fields kwarg is provided, _upsert_registry_row must NOT call read_registry_fields (no MinIO re-read). Verify upsert_doc receives the registry_fields values directly. When registry_fields is None, verify read_registry_fields IS called (backward compat). | CONTRACT |
| `tests/test_registry_mirror.py` | When both registry_fields and verdict_fields are provided, verdict_fields values must override any overlapping keys in registry_fields (overlay semantics preserved). | CONTRACT |
| `tests/test_converters_cli.py` | Successful child stdout JSON must include registry_fields dict with all _REGISTRY_FIELDS keys when client.last_registry_fields is set. Must NOT include registry_fields key when last_registry_fields is None (backward compat). | EXHAUSTIVENESS |
| `tests/test_worker.py` | process_document_job extracts registry_fields from child result and passes to _upsert_registry_row. When child result lacks registry_fields (old binary), _upsert_registry_row is called with registry_fields=None. | WIRING |
| `tests/test_storage.py` | hash_cache_delete must issue both Redis HDEL AND attempt legacy MinIO blob purge. When legacy blob contains the filename, it must be removed. When legacy blob does not exist, no error. When legacy blob purge fails, Redis HDEL must still have succeeded (best-effort). | CONTRACT |
| `tests/test_storage.py` | ErasureManifest ordering test: _ERASURE_MANIFEST step names must appear in HR2 cascade order (uploads, processed, meta, redis-cache, reconcile-etag, hash-cache, registry, preloaded). Each step's required flag must match the current behavior. | EXHAUSTIVENESS |
| `tests/test_storage.py` | delete_doc with declarative manifest produces identical errors[] output as current inline cascade for: full success, partial MinIO failure, registry timeout, unknown doc_name scenarios. | REGRESSION |
| `tests/test_registry_backfill.py` | _delete_stale_rows must protect rows with empty/missing processed_at via age guard (not treat them as stale candidates) when cleanup_protect_empty_processed_at is True (default). When the setting is False, old behavior (treat as stale) must be preserved. | CONTRACT |
| `tests/test_converters_cli.py` | last_registry_fields stashed by _persist_tree_result must contain all keys matching _REGISTRY_FIELDS plus node_count. last_registry_fields from _persist_flat_result must include content_class and have node_count=0. | EXHAUSTIVENESS |

#### Corpus Validation

- **Affected Document Classes:** ['cabinet_resolution_no_96', 'world-stats-pocketbook']
- **Expected Verdict Direction:** STABLE
- **Spot Check Count:** 3

---

### Zone: Config Layering Split and Dead-Code Accumulation (Wave 3, Priority 5)

**Severity:** MEDIUM  
**Estimated Complexity:** MEDIUM  
**Depends On:** Garble Detection Fragmentation (Wave 1)  
**Key Files:** `config.py`, `converters/pipeline.py`, `helpers/garble.py`, `client/indexer.py`, `client/recovery.py`, `worker/subprocess_mgr.py`, `converters/pictures.py`

#### Mechanism to Eliminate

Six module-level constants (frozen at import time in `config.py` lines 22–61):
- `PDF_INSPECTOR_PRECLASSIFY`
- `ALLOW_AGPL_FALLBACK`
- `REMOTE_MD_RENORMALIZE`
- `OCR_ESCALATION_GARBLE`
- `OCR_ESCALATION_PER_PICTURE`
- `IMAGE_DOMINANT_OCR_ESCALATION_ENABLED`

**Three Failure Modes:**

1. **Stale Values in `reset_pipeline_config()`:** `PipelineConfig.from_env()` copies frozen values instead of re-reading `os.environ`, so reset returns stale values for all 6 fields. Docstring claims "re-read env vars" but delivers frozen snapshot.

2. **Dual-Path Live/Frozen Split:** `pdf_markdown_converters()` reads `PDF_CONVERTER` live via `os.getenv()` at line 606 but reads `ALLOW_AGPL_FALLBACK` from frozen constant at line 603–604. Two routing inputs can diverge: you can enable Docling while blocking AGPL, creating inconsistency.

3. **Hardcoded Garble Floor:** `GarbleConfig.from_config()` hardcodes `garble_digit_floor=500` instead of reading `cfg.garble_digit_floor`. Field doesn't even exist on `PipelineConfig`, so config consolidation is incomplete. Config audit trail (`effective_config_snapshot()`) persists stale frozen `allow_agpl_fallback` into `meta.json` sidecar, violating CLAUDE.md Hard Rule 4 (AGPL compliance evidence).

#### Strategy

**Consolidate all 6 frozen constants into live `os.environ` reads inside `PipelineConfig.from_env()`**

1. Replace frozen module-level assignments with `_envbool()` calls in `from_env()`
2. Add `garble_digit_floor: int` field to `PipelineConfig`
3. Update all internal consumers to read from `pipeline_config` singleton instead of importing frozen constants
4. Migrate `pdf_markdown_converters()` to read both routing inputs from `pipeline_config` (eliminate os.getenv dual-path)
5. Keep 6 module-level names as deprecated read-through aliases for one release cycle (backward compat for external scripts)
6. Update `reset_pipeline_config()` to reassign aliases after rebuild

**Result:** `reset_pipeline_config()` actually re-reads all 6 values from `os.environ`. Config audit trail reflects live values, not stale snapshots. Single decision point for AGPL routing.

#### Code Targets

**Target 1: Replace frozen module constants with deprecated aliases**
- **File:** `src/pageindex_mcp/config.py`
- **Lines:** 22–61 (remove), 505+ (reassign after singleton init)
- **What:** Remove 6 `os.environ.get` assignments at module level; create read-through aliases to `pipeline_config`
- **How:** 
  - Delete original assignments (lines 22–61)
  - After `pipeline_config` singleton initialization (line 505), assign as module attributes:
    ```python
    PDF_INSPECTOR_PRECLASSIFY = pipeline_config.pdf_inspector_preclassify
    ALLOW_AGPL_FALLBACK = pipeline_config.allow_agpl_fallback
    # ... etc
    ```
  - Update `reset_pipeline_config()` (line 514–541) to reassign all 6 after rebuilding
- **Constraint:** Must not break `from ..config import PDF_INSPECTOR_PRECLASSIFY` patterns in src/ and tests/

**Target 2: PipelineConfig.from_env() reads 6 fields live from os.environ**
- **File:** `src/pageindex_mcp/config.py`
- **Lines:** 430–437 (method body)
- **What:** Replace `PDF_INSPECTOR_PRECLASSIFY=PDF_INSPECTOR_PRECLASSIFY` with live `_envbool()` calls
- **How:** For each of 6 fields:
  - `pdf_inspector_preclassify=_envbool('PDF_INSPECTOR_PRECLASSIFY', '0')`
  - `allow_agpl_fallback=_envbool('ALLOW_AGPL_FALLBACK', '1')`
  - `remote_md_renormalize=_envbool('REMOTE_MD_RENORMALIZE', '1')`
  - `ocr_escalation_garble=_envbool('OCR_ESCALATION_GARBLE', '1')`
  - `ocr_escalation_per_picture=_envbool('OCR_ESCALATION_PER_PICTURE', '1')`
  - `image_dominant_ocr_escalation_enabled=_envbool('IMAGE_DOMINANT_OCR_ESCALATION_ENABLED', '1')`
- **Constraint:** Defaults must match frozen-constant defaults exactly to avoid behavior flip

**Target 3: Add garble_digit_floor to PipelineConfig**
- **File:** `src/pageindex_mcp/config.py`
- **Lines:** 380–425 (dataclass fields section)
- **What:** Add `garble_digit_floor: int` field to `PipelineConfig`
- **How:**
  - Add field: `garble_digit_floor: int`
  - In `from_env()`: `garble_digit_floor=int(os.environ.get('GARBLE_DIGIT_FLOOR', '500'))`
  - Add to `_SIDECAR_FIELDS` in `effective_config_snapshot()`
- **Constraint:** Default 500 matches current hardcoded behavior

**Target 4: Update reset_pipeline_config() to reassign deprecated aliases**
- **File:** `src/pageindex_mcp/config.py`
- **Lines:** 514–541
- **What:** After rebuilding `pipeline_config`, reassign all 6 module-level backward-compat names
- **How:**
  ```python
  global PDF_INSPECTOR_PRECLASSIFY, ALLOW_AGPL_FALLBACK, ...
  pipeline_config = PipelineConfig.from_env()
  PDF_INSPECTOR_PRECLASSIFY = pipeline_config.pdf_inspector_preclassify
  # ... etc
  ```
- **Constraint:** Global declarations must come after `pipeline_config` rebuild

**Target 5: Fix GarbleConfig.from_config() to read garble_digit_floor**
- **File:** `src/pageindex_mcp/helpers/garble.py`
- **Lines:** 463
- **What:** Read `cfg.garble_digit_floor` instead of hardcoding 500
- **How:** Change `garble_digit_floor=500` to `garble_digit_floor=getattr(cfg, 'garble_digit_floor', 500)`
- **Constraint:** Use `getattr` with default for backward compat during migration

**Target 6: Migrate pdf_markdown_converters() to pipeline_config**
- **File:** `src/pageindex_mcp/converters/pipeline.py`
- **Lines:** 603–606
- **What:** Eliminate dual-path split (os.getenv + frozen constant); read both inputs from `pipeline_config`
- **How:**
  - Replace `from ..config import ALLOW_AGPL_FALLBACK` with `from ..config import pipeline_config`
  - Change `primary = os.getenv('PDF_CONVERTER', 'docling').strip().lower()` to `primary = pipeline_config.pdf_converter`
  - Replace all `ALLOW_AGPL_FALLBACK` with `pipeline_config.allow_agpl_fallback`
- **Constraint:** Both routing inputs must read from same source

**Target 7: Replace frozen-constant imports in indexer.py**
- **File:** `src/pageindex_mcp/client/indexer.py`
- **Lines:** 20–29 (imports), 370, 587–589, 1039 (usage sites)
- **What:** Remove module-level imports; read from `pipeline_config` at call time
- **How:**
  - Remove `PDF_INSPECTOR_PRECLASSIFY`, `REMOTE_MD_RENORMALIZE`, `OCR_ESCALATION_PER_PICTURE` from imports
  - Ensure `from ..config import pipeline_config` present
  - Replace usage sites: `pipeline_config.pdf_inspector_preclassify`, etc.
- **Constraint:** Reads at method-body level, not module-import level (call-time read, not bind-time)

**Target 8: Replace frozen-constant imports in recovery.py**
- **File:** `src/pageindex_mcp/client/recovery.py`
- **Lines:** 11–16 (imports), all usage sites
- **What:** Remove `IMAGE_DOMINANT_OCR_ESCALATION_ENABLED`, `OCR_ESCALATION_GARBLE`, `REMOTE_MD_RENORMALIZE` imports; use `pipeline_config`
- **How:**
  - Remove frozen-constant imports
  - Add `from ..config import pipeline_config`
  - Replace all usage sites with `pipeline_config.xxx`
- **Constraint:** Preserved for consistency with other modules

**Target 9: Replace frozen-constant imports in subprocess_mgr.py**
- **File:** `src/pageindex_mcp/worker/subprocess_mgr.py`
- **Lines:** 12 (import), 169 (usage)
- **What:** Replace `PDF_INSPECTOR_PRECLASSIFY` with `pipeline_config` read
- **How:**
  - Change import to `from ..config import pipeline_config, settings`
  - At line 169, use `pipeline_config.pdf_inspector_preclassify`
- **Constraint:** `settings` import must be preserved

**Target 10: Replace frozen-constant imports in pictures.py**
- **File:** `src/pageindex_mcp/converters/pictures.py`
- **Lines:** 20 (import), all usage sites (including deferred imports at lines 479, 529, 638, 750)
- **What:** Replace `OCR_ESCALATION_PER_PICTURE` + deferred `ALLOW_AGPL_FALLBACK` imports with `pipeline_config`
- **How:**
  - Remove `from ..config import OCR_ESCALATION_PER_PICTURE`
  - Add `from ..config import pipeline_config`
  - Replace all direct references and deferred-import usages
- **Constraint:** Deferred imports migrate to `pipeline_config` reads too

#### Wiring Checks

| Symbol | Must Be Imported By | Check Type | Rationale |
|--------|-------------------|-----------|-----------|
| `pipeline_config` | `converters/pipeline.py`, `client/indexer.py`, `client/recovery.py`, `worker/subprocess_mgr.py`, `converters/pictures.py` | import | All 5 modules must read from live singleton, not frozen constants |
| `garble_digit_floor` | `config.py` (field definition), `helpers/garble.py` (GarbleConfig.from_config call at line 467) | call | Field must be defined on PipelineConfig before garble.py reads it via `getattr()` |
| `reset_pipeline_config` | `tests/test_config.py` | call | Reset must reassign all 6 deprecated aliases so tests monkeypatching env see new values |

#### Test Requirements

| Test File | What to Test | Assertion Type |
|-----------|-------------|-----------------|
| `tests/test_config.py` | `reset_pipeline_config()` re-reads os.environ for all 6 fields: monkeypatch env, call reset, assert `pipeline_config.xxx` AND module alias reflect new value | CONTRACT |
| `tests/test_config.py` | `PipelineConfig.from_env()` reads `garble_digit_floor` from `GARBLE_DIGIT_FLOOR` env var; monkeypatch GARBLE_DIGIT_FLOOR=1000, assert result is 1000 | CONTRACT |
| `tests/test_config.py` | `GarbleConfig.from_config()` threads `garble_digit_floor` from `PipelineConfig`; build config with 1000, assert result matches | REGRESSION |
| `tests/test_config.py` | `effective_config_snapshot()` includes `garble_digit_floor` in sidecar, reflects live value (not stale frozen) | CONTRACT |
| `tests/test_config.py` | `pdf_markdown_converters()` reads both `pdf_converter` and `allow_agpl_fallback` from same source; monkeypatch both, verify routing consistency | INTEGRATION |
| `tests/test_observability.py` | `effective_config_snapshot().allow_agpl_fallback` is consistent with `pipeline_config.allow_agpl_fallback` after reset; HR4 audit trail correctness | REGRESSION |

#### Corpus Validation

- **Affected Document Classes:** ALL — config layering affects every pipeline run via `effective_config_snapshot()` sidecar persistence and garble detection thresholds
- **Expected Verdict Direction:** STABLE (configuration correctness, not content quality change)
- **Spot Check Count:** 3 documents (representative, not regression-class-specific)
- **Regression Guard:** Config snapshots in old vs new runs must match live `pipeline_config` state; no verdict flips from stale config reads

---

## Validation Results

### Overall Quality: NEEDS WORK

**Total Validation Issues Found:** 17 (11 blocker/major, 6 minor/cosmetic)

### Blocker Issues (Must Fix Before Implementation)

1. **[BLOCKER] Incorrect line numbers in Multi-Store zone (NOT IN SCOPE but critical for Wave 2)**  
   Worker job.py code_target claims lines 355–362 contain verdict_fields assignment and _upsert_registry_row call. **Actual:** lines 348–352.  
   **Impact:** Implementer following spec literally edits wrong region.  
   **Fix:** Wave 2 zone author must correct to lines 340–352.

2. **[BLOCKER] Missing setting definition for cleanup zone (NOT IN SCOPE)**  
   Wiring check requires `cleanup_protect_empty_processed_at` import but code_targets never define it in config.py.  
   **Fix:** Wave 2+ zone author must add PipelineConfig field.

3. **[BLOCKER] Wiring check / code_target mismatch on _purge_legacy_hash_entry (NOT IN SCOPE)**  
   Code_target describes inline try/except but wiring check names a `_purge_legacy_hash_entry()` function that doesn't exist.  
   **Fix:** Align wiring check or code_target.

4. **[BLOCKER] Contradiction in Multi-Store field list constraint (NOT IN SCOPE)**  
   Constraint says "must include every key in _REGISTRY_FIELDS" but code_target deliberately omits doc_id (added downstream).  
   **Fix:** Correct constraint wording.

### Major Issues (Significant Risk)

5. **[MAJOR] OCR Strategy mechanism claims consolidation that code_targets don't deliver**  
   Mechanism says "consolidate three independent OCR entry points into one decision point," but no code_target wires `indexer.py`'s standalone-image branch (lines 656–685) to call `decide_ocr_strategy(document_type='image')`. `UNIFIED_OCR_PLAN_ENABLED` defaults false with no consumer → dead code.  
   **Recommendation:** Reframe mechanism as "fix lang detection, dedup constants, narrow exceptions, insert splice, fix keep-best" (all correctly spec'd), defer true single-decision-point to follow-up zone. OR add code_target wiring standalone images to `decide_ocr_strategy()` when flag enabled.

6. **[MAJOR] Wiring check gap for new script_context parameter threading**  
   Five code_targets add `script_context` parameter threading but only have generic "import ScriptContext" checks (already true today). No call-site wiring checks verify the actual threading.  
   **Recommendation:** Add wiring_checks with check_type='call' for each modified function.

7. **[MAJOR] Config Layering Wave 3 depends on Wave 1 (Garble Detection) but both not same wave**  
   Config Layering spec says `depends_on: ["Garble Detection Fragmentation"]` but that zone is Wave 1, this is Wave 3. Shared file `helpers/garble.py` could cause merge conflicts if both run concurrently.  
   **Recommendation:** Confirm Garble Detection is Wave 1 (it is) and explicitly note Wave 3 isolation from Wave 1 outputs.

8. **[MAJOR] Multi-Store wiring misses third registry writer (NOT IN SCOPE but cascades)**  
   Spec names `registry_backfill/reconcile.py::_drain_verdict_retry_queue()` as a key_file but zero code_targets touch it. On Postgres outage recovery, queued verdicts replayed through this path will bypass `force_verdict_override` kwarg handling.  
   **Fix:** Wave 3 author must add reconcile.py code_target.

### Minor Issues (Cosmetic/Precision)

9. **[MINOR] Multi-Store manifest extraction cascade has 11 sub-steps, spec lists 8**  
   Not clear which are grouped; step2d/4b not mentioned.

10. **[MINOR] Missing PRONG_TABLE extraction code_target**  
    Garble Detection mechanism names it; strategy says "D-F additive"; no code_target. Likely deferred but test_requirements hedge it as "if implemented."

11. **[MINOR] Corpus validation mislabels Arabic scanned PDFs**  
    OCR Strategy code_target:1 touches standalone IMAGE branch, not PDF branch. Arabic PDF language detection is separate (unchanged).

12. **[MINOR] Line number drift on several code_targets**  
    config.py from_env() cited 430–437 vs actual 433–444; pictures.py imports cited 479/529/638/749 vs actual 480/530/639/750. Non-blocking (implementer will locate regardless) but shows less rigor than verified-exact targets.

13. **[MINOR] splice_picture_text_for_tree import cited line 42 vs actual 43**  
    Off by one (detect_ocr_langs pushed block down).

14. **[MINOR] Wave 2 dependency note unclear**  
    Spec says zones depend on Wave 1 outputs but provides no sequencing guidance if both Wave 1 zones finish at different times.

15. **[MINOR] Wiring check schema tension on field definitions**  
    `garble_digit_floor` is a dataclass field, not a function call, but check_type='call'; `GarbleConfig.from_config` wiring check lists garble.py as 'must_be_imported_by' when it's defined/called there. Schema semantics stretched.

16. **[MINOR] Config Layering backward-compat alias assignment timing**  
    Code_target says "after pipeline_config singleton init (line 505)" but line 505 is where `pipeline_config = PipelineConfig.from_env()` sits; unclear if reassignments go inline or at module-end.

17. **[MINOR] OCR Strategy keep-best fix in recovery.py affects image-dominant OCR only, not all images**  
    Mechanism framing overstates impact; this is a regression guard on one fallback path.

### Verification Status

**OCR Strategy Bifurcation Zone:**  
- ✓ All 7 code_targets line numbers verified or drift documented
- ✓ All wiring checks reference real symbols
- ✓ All test_requirements are specific and mechanizable
- ✗ Mechanism/strategy scope mismatch (see Major Issue #5)

**Config Layering Split Zone:**  
- ✓ All 10 code_targets reference real files and syntax
- ✓ All wiring checks map to real files
- ✓ All test_requirements are concrete
- ✓ Dependencies and wave sequencing noted

---

## Recommended Actions Before Implementation

### For This Plan (All 5 Zones)

1. **Clarify OCR Strategy scope:** Confirm whether "consolidation" means landing the full single-decision-point unification or deferring it to a follow-up. Update mechanism/strategy language accordingly.
2. **Add script_context call-site wiring checks:** Ensure every function receiving new `script_context` parameter has a wiring_check verifying the call-site threading, not just class-level imports.
3. **Resolve Config Layering Wave 3 / Garble Detection Wave 1 overlap:** Confirm both zones complete independently; if Wave 1 still running when Wave 3 starts, sequential or parallel merge strategy.

### Cross-Zone Coordination

1. **Fix Multi-Store blocker line numbers** before any implementation starts
2. **Add config.py cleanup_protect_empty_processed_at setting** before cleanup zone code_targets can import it
3. **Add reconcile.py code_target** for force_verdict_override handling on Postgres recovery path
4. **Resolve wiring check / code_target mismatches** on all three areas above

---

## Implementation Checklist

- [ ] **Wave 1 Kickoff:** Both OCR Strategy and Garble Detection parallel (zero shared key_files)
- [ ] **Garble Detection Target 1:** Add garble_digit_floor to PipelineConfig (config.py)
- [ ] **Garble Detection Target 2:** Fix GarbleConfig.from_config hardcoded 500 (garble.py:463)
- [ ] **Garble Detection Target 3:** Fix latin_gibberish prong guard unreachability
- [ ] **Garble Detection Targets 4-10:** Thread ScriptContext, add warnings, add fallback
- [ ] **Garble Detection Tests:** Run test suite for garble detection coverage
- [ ] **Garble Detection Corpus Validation:** Spot-check garble ratios
- [ ] **OCR Strategy Target 1:** Fix hardcoded lang list (indexer.py:658) — 1 line, 5 min
- [ ] **OCR Strategy Target 2:** Deduplicate constants — remove from indexer.py, import from images.py
- [ ] **OCR Strategy Target 3–4:** Narrow exceptions + add Prometheus counter
- [ ] **OCR Strategy Target 5:** Extend OcrDecision + decide_ocr_strategy (feature-flag gated)
- [ ] **OCR Strategy Target 6:** Insert splice_picture_text_for_tree into standalone path
- [ ] **OCR Strategy Target 7:** Fix keep-best in recovery.py
- [ ] **OCR Strategy Tests:** Run test suite for regression coverage
- [ ] **OCR Strategy Corpus Validation:** 5-spot-check documents (images, Arabic, image-heavy PDFs)
- [ ] **Wave 1 Complete:** Both zones validated; Wave 2 can begin
- [ ] **Wave 2 Kickoff:** Verdict Promotion / Quality Gate Stack (after both Wave 1 zones validated)
- [ ] **Verdict Promotion Target 1:** Extract duplicated verdict-priority SQL CASE into helper function
- [ ] **Verdict Promotion Target 2:** Add force_verdict_override parameter to upsert_doc()
- [ ] **Verdict Promotion Tests:** Run test suite for verdict promotion regression coverage
- [ ] **Verdict Promotion Corpus Validation:** Spot-check verdict outcomes
- [ ] **Wave 2 Complete:** Verdict promotion validated; Wave 3 can begin
- [ ] **Wave 3 Kickoff:** Multi-Store + Config Layering parallel (zero inter-zone shared_files)
- [ ] **Multi-Store Target 1:** Add last_registry_fields stash in _persist_tree_result
- [ ] **Multi-Store Target 2:** Purge legacy MinIO blob alongside Redis HDEL (HR2)
- [ ] **Multi-Store Target 3:** Extract monolithic erasure cascade into declarative manifest
- [ ] **Multi-Store Targets 4-8:** Remaining dual-write consistency fixes
- [ ] **Multi-Store Tests:** Run test suite for registry/storage contract coverage
- [ ] **Multi-Store Corpus Validation:** Spot-check erasure + persistence
- [ ] **Config Layering Target 1:** Refactor frozen constants into deprecated aliases
- [ ] **Config Layering Target 2:** PipelineConfig.from_env() reads all 6 fields live
- [ ] **Config Layering Target 3:** Add garble_digit_floor field to PipelineConfig
- [ ] **Config Layering Target 4–10:** Migrate all consumers to pipeline_config singleton
- [ ] **Config Layering Tests:** reset_pipeline_config() round-trip, effective_config_snapshot audit trail
- [ ] **Config Layering Corpus Validation:** 3-spot-check full-pipeline runs; verify meta.json snapshots
- [ ] **Wave 3 Complete:** Config clean, audit trail fixed; multi-store dual-write follow-up

---

*Remediation Plan Version: 2026-08-25*  
*Zones: Garble Detection (W1) + OCR Strategy (W1) + Verdict Promotion (W2) + Multi-Store Dual-Write (W3) + Config Layering (W3)*  
*Status: NEEDS WORK — 17 validation issues identified across 5 zones; prioritize blocker issues before wave 1 kickoff.*
