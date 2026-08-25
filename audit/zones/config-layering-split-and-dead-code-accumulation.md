---
zone_name: Config Layering Split and Dead-Code Accumulation
severity: medium
wave: 3
priority: 5
status: triaged
audit_date: 2026-08-25
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md
tags:
  - zone-spec
  - medium
  - wave-3
---
## Mechanism to Eliminate

Six module-level constants (PDF_INSPECTOR_PRECLASSIFY, ALLOW_AGPL_FALLBACK, REMOTE_MD_RENORMALIZE, OCR_ESCALATION_GARBLE, OCR_ESCALATION_PER_PICTURE, IMAGE_DOMINANT_OCR_ESCALATION_ENABLED) are frozen at import time in config.py lines 22-61. PipelineConfig.from_env() copies these frozen values instead of re-reading os.environ, so reset_pipeline_config() silently returns stale values for those 6 fields while its docstring claims 're-read env vars'. Consumers that import frozen constants at module level (indexer.py, recovery.py, subprocess_mgr.py, pictures.py) bind the stale value permanently. 

pdf_markdown_converters() creates a dual-path split: it reads os.getenv('PDF_CONVERTER') live at line 606 but reads ALLOW_AGPL_FALLBACK from the frozen module constant at line 603-604, so the two routing inputs can diverge. GarbleConfig.from_config() hardcodes garble_digit_floor=500 (garble.py line 463) instead of reading cfg.garble_digit_floor — and garble_digit_floor does not even exist as a PipelineConfig field, so the config consolidation it claims to implement is incomplete. effective_config_snapshot() persists these stale frozen values into meta.json sidecar, tainting the audit trail for allow_agpl_fallback (CLAUDE.md Hard Rule 4 compliance evidence).

## Strategy

Consolidate all 6 frozen module-level bool constants into live os.environ reads inside PipelineConfig.from_env() (replacing references to the frozen names with _envbool calls). Add garble_digit_floor: int as a PipelineConfig field so GarbleConfig.from_config() can read cfg.garble_digit_floor. Replace all direct imports of frozen constants in src/pageindex_mcp/ with reads from the pipeline_config singleton (e.g., pipeline_config.allow_agpl_fallback). Make pdf_markdown_converters() read pipeline_config.pdf_converter and pipeline_config.allow_agpl_fallback instead of os.getenv + frozen constant. Keep the 6 module-level names as deprecated aliases (thin wrappers reading pipeline_config.xxx) for one release cycle to avoid breaking external scripts. This eliminates the live/frozen split and makes reset_pipeline_config() actually re-read all values from os.environ.

## Code Targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| src/pageindex_mcp/config.py | 22-61 | Replace frozen constants with deprecated aliases | Remove the 6 os.environ.get assignments at module level. After pipeline_config singleton init (line 505), define each as deprecated alias reading from pipeline_config (e.g., `PDF_INSPECTOR_PRECLASSIFY = property(lambda: pipeline_config.pdf_inspector_preclassify)` or assign below line 505: `PDF_INSPECTOR_PRECLASSIFY = pipeline_config.pdf_inspector_preclassify`, then update reset_pipeline_config() to reassign them too). | Must not break `from ..config import PDF_INSPECTOR_PRECLASSIFY` patterns in src/ and tests/ |
| src/pageindex_mcp/config.py | 430-437 | PipelineConfig.from_env() reads os.environ live | Replace `pdf_inspector_preclassify=PDF_INSPECTOR_PRECLASSIFY` with `pdf_inspector_preclassify=_envbool('PDF_INSPECTOR_PRECLASSIFY', '0')`. Same for allow_agpl_fallback (_envbool('ALLOW_AGPL_FALLBACK', '1')), remote_md_renormalize (_envbool('REMOTE_MD_RENORMALIZE', '1')), ocr_escalation_garble (_envbool('OCR_ESCALATION_GARBLE', '1')), ocr_escalation_per_picture (_envbool('OCR_ESCALATION_PER_PICTURE', '1')), image_dominant_ocr_escalation_enabled (_envbool('IMAGE_DOMINANT_OCR_ESCALATION_ENABLED', '1')). | Default values must match the current frozen-constant defaults exactly |
| src/pageindex_mcp/config.py | 380-425 | Add garble_digit_floor field to PipelineConfig | Add `garble_digit_floor: int` to the PipelineConfig fields section (behavior flags area). In from_env(), add `garble_digit_floor=int(os.environ.get('GARBLE_DIGIT_FLOOR', '500'))`. Add 'garble_digit_floor' to the _SIDECAR_FIELDS set in effective_config_snapshot(). | Default must be 500 to match current hardcoded GarbleConfig behavior |
| src/pageindex_mcp/config.py | 514-541 | Update reset_pipeline_config() | After rebuilding pipeline_config from PipelineConfig.from_env(), reassign all 6 module-level backward-compat aliases: `global PDF_INSPECTOR_PRECLASSIFY, ALLOW_AGPL_FALLBACK, REMOTE_MD_RENORMALIZE, OCR_ESCALATION_GARBLE, OCR_ESCALATION_PER_PICTURE, IMAGE_DOMINANT_OCR_ESCALATION_ENABLED` then set each to its pipeline_config.xxx counterpart. This ensures tests calling reset_pipeline_config() after monkeypatching env vars see the new values. | The global declarations and reassignments must come after pipeline_config is rebuilt |
| src/pageindex_mcp/helpers/garble.py | 463 | Fix GarbleConfig.from_config() hardcoding | Change `garble_digit_floor=500` to `garble_digit_floor=getattr(cfg, 'garble_digit_floor', 500)`. Use getattr with default for backward compat during migration, but once PipelineConfig carries the field, the getattr fallback is just safety. | Must not break if called with a PipelineConfig instance that predates the new field (getattr safety) |
| src/pageindex_mcp/converters/pipeline.py | 603-606 | Eliminate dual-path split in pdf_markdown_converters() | Replace `from ..config import ALLOW_AGPL_FALLBACK` with `from ..config import pipeline_config`. Replace `primary = os.getenv('PDF_CONVERTER', 'docling').strip().lower()` with `primary = pipeline_config.pdf_converter`. Replace all `ALLOW_AGPL_FALLBACK` references in function body with `pipeline_config.allow_agpl_fallback`. | Must read from pipeline_config (not os.getenv or frozen constant) for both routing inputs |
| src/pageindex_mcp/client/indexer.py | 20-29 | Replace frozen-constant imports with pipeline_config | Remove `PDF_INSPECTOR_PRECLASSIFY`, `REMOTE_MD_RENORMALIZE`, `OCR_ESCALATION_PER_PICTURE` from import lines. Add `from ..config import pipeline_config` if not already imported. Replace all usage sites (lines 370, 387-389, 1038) with pipeline_config.pdf_inspector_preclassify, pipeline_config.remote_md_renormalize, pipeline_config.ocr_escalation_per_picture. | Each usage site is inside a method body so reads pipeline_config at call time, not import time |
| src/pageindex_mcp/client/recovery.py | 11-16 | Replace frozen-constant imports with pipeline_config | Remove IMAGE_DOMINANT_OCR_ESCALATION_ENABLED and OCR_ESCALATION_GARBLE from imports. Add `from ..config import pipeline_config` (if not present). Replace _IMAGE_DOMINANT_OCR_ESCALATION_ENABLED usages with pipeline_config.image_dominant_ocr_escalation_enabled and _OCR_ESCALATION_GARBLE with pipeline_config.ocr_escalation_garble at each usage site. | Must also replace REMOTE_MD_RENORMALIZE import similarly |
| src/pageindex_mcp/worker/subprocess_mgr.py | 12 | Replace PDF_INSPECTOR_PRECLASSIFY import | Change `from ..config import PDF_INSPECTOR_PRECLASSIFY, settings` to `from ..config import pipeline_config, settings`. Replace `PDF_INSPECTOR_PRECLASSIFY` usage at line 169 with `pipeline_config.pdf_inspector_preclassify`. | settings import must be preserved |
| src/pageindex_mcp/converters/pictures.py | 20 | Replace OCR_ESCALATION_PER_PICTURE import | Remove `from ..config import OCR_ESCALATION_PER_PICTURE as _OCR_ESCALATION_PER_PICTURE`. Import pipeline_config instead. Replace all _OCR_ESCALATION_PER_PICTURE usages with pipeline_config.ocr_escalation_per_picture. | Function-level ALLOW_AGPL_FALLBACK imports in this file (lines 479, 529, 638, 749) are already deferred — migrate those too for consistency |

## Wiring Checks

| Symbol | Must Be Imported By | Check Type |
|---|---|---|
| pipeline_config | src/pageindex_mcp/converters/pipeline.py, src/pageindex_mcp/client/indexer.py, src/pageindex_mcp/client/recovery.py, src/pageindex_mcp/worker/subprocess_mgr.py, src/pageindex_mcp/converters/pictures.py | import |
| garble_digit_floor | src/pageindex_mcp/config.py | call |
| GarbleConfig.from_config | src/pageindex_mcp/helpers/garble.py | call |

## Test Requirements

| Test File | What to Test | Assertion Type |
|---|---|---|
| tests/test_config.py | reset_pipeline_config() actually re-reads os.environ for all 6 formerly-frozen fields: monkeypatch each env var to non-default value, call reset_pipeline_config(), assert pipeline_config.xxx reflects new value AND module-level backward-compat alias reflects it too | contract |
| tests/test_config.py | PipelineConfig.from_env() reads garble_digit_floor from GARBLE_DIGIT_FLOOR env var (not hardcoded 500): monkeypatch GARBLE_DIGIT_FLOOR=1000, call from_env(), assert pipeline_config.garble_digit_floor == 1000 | contract |
| tests/test_config.py | GarbleConfig.from_config(pipeline_config) threads garble_digit_floor from PipelineConfig rather than hardcoding: build PipelineConfig with garble_digit_floor=1000, call GarbleConfig.from_config(), assert result.garble_digit_floor == 1000 | regression |
| tests/test_config.py | effective_config_snapshot() includes garble_digit_floor in sidecar output and reflects live pipeline_config value (not stale frozen value) | contract |
| tests/test_config.py | pdf_markdown_converters() reads pdf_converter and allow_agpl_fallback from same source (pipeline_config): monkeypatch to set PDF_CONVERTER=pymupdf4llm and ALLOW_AGPL_FALLBACK=0, call reset_pipeline_config(), verify chain is consistent (no pymupdf4llm entry when AGPL blocked) | integration |
| tests/test_observability.py | effective_config_snapshot() allow_agpl_fallback field is consistent with pipeline_config.allow_agpl_fallback after reset_pipeline_config() with ALLOW_AGPL_FALLBACK=0 — HR4 audit trail correctness | regression |

## Corpus Validation

- **Affected documents:** All documents — config layering affects every pipeline run via effective_config_snapshot sidecar persistence and garble detection thresholds
- **Expected verdict direction:** stable
- **Spot check count:** 3

## Dependencies

Depends on: Garble Detection Fragmentation

## Complexity

medium
