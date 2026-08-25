---
zone_name: Garble Detection Fragmentation
severity: critical
wave: 1
priority: 2
status: triaged
audit_date: 2026-08-25
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md
tags:
  - zone-spec
  - critical
  - wave-1
---
## Mechanism to Eliminate

Nine independent boolean-gated prongs in garble_prongs() with inline preconditions that are (a) disconnected from live config (garble_digit_floor=500 hardcoded literal in GarbleConfig.from_config, missing from PipelineConfig entirely), (b) unreachable for the primary corpus (latin_gibberish guard requires expected_script != 'Latn', blocking all German/English docs), (c) fed stale had_presentation_forms=False by 10 production call sites that construct throwaway ScriptContext instead of threading the properly-computed one, and (d) produce fully silent OK from validate_tree with zero secondary signal when all prongs miss -- a garble miss is indistinguishable from genuinely clean content. Each prong fix routinely destabilizes adjacent prongs because preconditions are inline if-guards buried in a 90-line function body, not declarative fields reviewable together.

## Strategy

Consolidate garble detection into a declarative prong pipeline in six sequenced steps: (A) fix garble_digit_floor config bug, (B) eliminate throwaway ScriptContext(had_presentation_forms=False) at 10 call sites by threading the once-computed ScriptContext, (C) fix latin_gibberish unreachability by inverting the guard, (D) extract prongs into a declarative PRONG_TABLE with name/function/min_length/script_filter fields, (E) add low-confidence warning path on TreeGateResult, (F) add concatenated whole-tree fallback plus title inspection in _garble_check_nodes. Steps A-C are independent 1-5 line changes with clean rollback; D-F are additive refactors. Each step gets a corpus diff before merge.

## Code Targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| src/pageindex_mcp/config.py | 368-501 | Add garble_digit_floor field to PipelineConfig and read from GARBLE_DIGIT_FLOOR env var in from_env() | Add field 'garble_digit_floor: int' to PipelineConfig class body (after garble_node_ratio_threshold). In from_env(), add 'garble_digit_floor=int(os.environ.get("GARBLE_DIGIT_FLOOR", "500"))' to the constructor call. | Default must remain 500 to preserve existing behavior; field goes in the garble-related group alongside garble_latin_gibberish_enabled, garble_latin_ratio, etc. |
| src/pageindex_mcp/helpers/garble.py | 463 | Fix GarbleConfig.from_config to read cfg.garble_digit_floor instead of hardcoded 500 | Change line 463 from 'garble_digit_floor=500,' to 'garble_digit_floor=cfg.garble_digit_floor,' | Must match the PipelineConfig field name exactly; no behavioral change at default value |
| src/pageindex_mcp/helpers/garble.py | 389-393 | Fix latin_gibberish prong guard to fire on Latin-script and None-script documents | Change the guard from '_effective_script is not None and _effective_script != "Latn" and cfg.garble_latin_gibberish_enabled' to 'cfg.garble_latin_gibberish_enabled and (_effective_script is None or _effective_script != "Latn" or True)' -- effectively remove the script filter entirely, keeping only the config toggle. The prong is gated by latin_ratio_threshold and nonsense_threshold which are the real precision controls. | Must NOT remove the cfg.garble_latin_gibberish_enabled toggle (config killswitch). Corpus diff required: German T&C PDFs must not false-positive. |
| src/pageindex_mcp/helpers/tree_validation.py | 268-276 | Thread ScriptContext properly when validate_tree receives a bare string: use ScriptContext.from_document with available text instead of throwaway had_presentation_forms=False | When expected_script is a bare string (not ScriptContext), use ScriptContext.from_script_str(expected_script) which already exists as a backward-compat factory. This is equivalent but makes the legacy path explicit. The real fix is ensuring callers pass ScriptContext.from_document -- see indexer/recovery targets. | Must remain backward-compatible with callers passing str|None; isinstance check on line 269 already handles ScriptContext |
| src/pageindex_mcp/helpers/tree_validation.py | 187-194 | Thread had_presentation_forms from ScriptContext into TreeSignals.from_tree instead of falling back to False | Line 194 sets _had_pf=False when expected_script is not a ScriptContext. Instead, when flat_text is available, scan for presentation forms ratio before NFKC normalization (same logic as ScriptContext.from_document). Alternatively, accept that the ScriptContext should have been computed upstream and log a warning. | Must not change behavior when a proper ScriptContext is passed (lines 187-189 already extract _had_pf correctly) |
| src/pageindex_mcp/helpers/verdict.py | 276 | Eliminate throwaway ScriptContext(had_presentation_forms=False) in apply_promotions | Thread the ScriptContext from compute_verdict (which receives expected_script: str|None|ScriptContext) through to apply_promotions as a new parameter. Replace the throwaway construction at line 276 with the threaded context. | apply_promotions signature change must be backward-compatible; add script_context as keyword-only with None default |
| src/pageindex_mcp/client/indexer.py | 424 | Replace throwaway ScriptContext in pre_garble_probe with the already-computed script_context | Line 1116 already computes ScriptContext.from_document(filename). Thread this to _convert_to_tree. Replace line 424 throwaway construction with the threaded script_context. | The script_context is computed at index() entry (line 1116) but _convert_to_tree receives expected_script as str. Add script_context parameter. |
| src/pageindex_mcp/client/indexer.py | 763-767 | Replace throwaway ScriptContext in flat_garble_gate with threaded script_context | Line 763-767 already has a conditional: 'script_context if script_context is not None else ScriptContext(...False...)'. Ensure script_context is always passed from the caller, eliminating the False fallback. | Verify script_context parameter flows from index() to _convert_to_tree to this flat path |
| src/pageindex_mcp/client/indexer.py | 791 | Replace throwaway ScriptContext in vlm_fallback_garble with threaded script_context | Same pattern as line 763: ensure script_context is always available, removing the had_presentation_forms=False fallback. | Same constraint as flat_garble_gate target |
| src/pageindex_mcp/client/recovery.py | 228-231 | Replace throwaway ScriptContext in ocr_retry_keep_best with threaded script_context | The recovery mixin receives expected_script as str. Thread the ScriptContext from the caller (indexer) through to _execute_ocr_retry. Replace the throwaway at line 228-231. | RecoveryMixin._execute_ocr_retry signature change must match indexer call sites |
| src/pageindex_mcp/converters/pictures.py | 287-290 | Replace throwaway ScriptContext in _text_layer_has_content with script_context parameter | Add script_context parameter to _text_layer_has_content, use it instead of constructing ScriptContext(had_presentation_forms=False). Update callers. | Must not break existing callers; add as keyword-only with None default and fallback to from_script_str |
| src/pageindex_mcp/converters/pictures.py | 405-408 | Replace throwaway ScriptContext in _document_level_text_fallback with script_context parameter | Same pattern as _text_layer_has_content: accept script_context, thread from caller. | Same as _text_layer_has_content target |
| src/pageindex_mcp/client/images.py | 133 | Replace throwaway ScriptContext in _attempt_tesseract_raster_recovery with threaded script_context | Accept script_context parameter, use instead of throwaway construction. | Backward-compat: default to ScriptContext.from_script_str(expected_script) when not provided |
| src/pageindex_mcp/helpers/garble.py | 735-738 | Replace throwaway ScriptContext fallback in _garble_ratio with proper threading | The function already accepts script_context parameter but falls back to had_presentation_forms=False. Ensure all callers (TreeSignals.from_tree at tree_validation.py:209) pass the ScriptContext they already have. | Backward compat: keep the fallback for any remaining callers but add a deprecation warning |
| src/pageindex_mcp/helpers/garble.py | 595-659 | Add concatenated whole-tree fallback when no per-node garbling detected, and inspect title fields for garbling | After the per-node loop, if garbled==0 and total concatenated text exceeds garble_digit_floor, run garble_prongs on the concatenated text as a secondary check. Title inspection is already present at lines 637-650 (added in prior fix). The concatenated fallback catches per-node decomposition undercutting digit_floor. | Must not double-count nodes already flagged as garbled; concatenated check is additive only when per-node returned 0 |
| src/pageindex_mcp/helpers/types.py | 41-61 | Add warnings field to TreeGateResult for low-confidence garble signals | Add 'warnings: list[str] = field(default_factory=list)' to TreeGateResult dataclass. Exclude from __iter__ (backward-compat tuple unpacking). Populated by validate_tree when any prong fires at sub-threshold confidence or when garble_ratio is above zero but below garble_threshold. | Must not change __iter__ behavior; existing (ok, reason) unpacking must continue working |
| src/pageindex_mcp/helpers/tree_validation.py | 296-308 | Populate TreeGateResult.warnings when validate_tree returns OK but garble signals are non-zero | When returning the OK TreeGateResult, check sig.garble_ratio > 0.0 (sub-threshold garbling detected by TreeSignals.from_tree). If so, add a warning string like 'sub_threshold_garble: ratio={sig.garble_ratio:.3f}' to the warnings list. Also check if any GATE_TABLE entry came close to firing. | Must not change ok=True result; warnings are advisory only. No behavioral change to downstream verdict computation. |

## Wiring Checks

| Symbol | Must Be Imported By | Check Type |
|---|---|---|
| PipelineConfig.garble_digit_floor | ['src/pageindex_mcp/helpers/garble.py'] | call |
| TreeGateResult.warnings | ['src/pageindex_mcp/helpers/tree_validation.py'] | call |
| ScriptContext.from_document | ['src/pageindex_mcp/client/indexer.py'] | call |
| ScriptContext | ['src/pageindex_mcp/helpers/verdict.py', 'src/pageindex_mcp/client/recovery.py', 'src/pageindex_mcp/converters/pictures.py', 'src/pageindex_mcp/client/images.py'] | import |
| GarbleConfig.from_config | ['src/pageindex_mcp/helpers/garble.py'] | call |

## Test Requirements

| Test File | What to Test | Assertion Type |
|---|---|---|
| tests/test_garble_detection.py | GarbleConfig.from_config reads cfg.garble_digit_floor instead of hardcoded 500: construct a mock PipelineConfig with garble_digit_floor=100, verify GarbleConfig.from_config produces config with garble_digit_floor=100 | contract |
| tests/test_garble_detection.py | PipelineConfig.from_env reads GARBLE_DIGIT_FLOOR env var: set env var to 300, verify pipeline_config.garble_digit_floor==300; unset, verify default 500 | contract |
| tests/test_garble_detection.py | latin_gibberish prong fires for Latin-script corpus with nonsense tokens: pass expected_script='Latn' (or None) with morphologically-nonsense Latin tokens exceeding ratio threshold, verify 'latin_gibberish' in garble_prongs result | regression |
| tests/test_garble_detection.py | latin_gibberish prong does NOT fire for clean German prose: pass real German T&C text with expected_script='Latn', verify 'latin_gibberish' not in result (false-positive guard) | regression |
| tests/test_zone1_flat_gate_asymmetry.py | ScriptContext.had_presentation_forms threads through validate_tree to _gate_garbling and _gate_node_garbling: construct ScriptContext with had_presentation_forms=True, pass to validate_tree, verify presentation_forms prong fires in the garble gate | wiring |
| tests/test_zone1_flat_gate_asymmetry.py | apply_promotions receives and uses ScriptContext instead of constructing throwaway: mock detect_garble, call compute_verdict with ScriptContext(had_presentation_forms=True), verify the ScriptContext passed to detect_garble inside apply_promotions has had_presentation_forms=True | wiring |
| tests/test_garble_detection.py | TreeGateResult.warnings is populated when garble_ratio is sub-threshold but non-zero: build a tree where garble_ratio lands between 0 and garble_threshold (0.05), verify TreeGateResult.ok==True and TreeGateResult.warnings is non-empty | contract |
| tests/test_garble_detection.py | _garble_check_nodes concatenated fallback: build a tree with many small nodes each under garble_digit_floor but whose concatenation exceeds it and is garbled, verify garbled count > 0 | regression |
| tests/test_garble_detection.py | Exhaustiveness: every prong name returned by garble_prongs is in a known set (PRONG_TABLE names if implemented, else a frozen set of valid prong names); no silent additions | exhaustiveness |
| tests/test_zone1_flat_gate_asymmetry.py | End-to-end: ScriptContext.from_document flows from indexer.index() through _convert_to_tree, validate_tree, compute_verdict without any had_presentation_forms=False reconstruction: instrument ScriptContext constructor, verify no source='pre_garble_probe' or 'apply_promotions' constructions with had_presentation_forms=False | integration |

## Corpus Validation

- **Affected documents:** ['German T&C PDFs (Latin script - latin_gibberish prong activation)', 'Arabic scanned PDFs (presentation-forms detection fix)', 'MOU documents (garble oscillation history)', 'ward_597 (garbled_blocks=0 despite visible garbling)']
- **Expected verdict direction:** improve
- **Spot check count:** 8

## Dependencies

None

## Complexity

medium
