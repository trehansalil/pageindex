---
zone_name: Converter Chain Fallback and AGPL Gating
severity: high
wave: 1
priority: 4
status: triaged
audit_date: 2026-08-28
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md
tags:
  - zone-spec
  - high
  - wave-1
---
## Mechanism to Eliminate

Converter chain failure-policy logic (indexer.py:664-671) treats structural failures as unconditional WALK-to-next, silently advancing to AGPL-licensed converters with only logger.warning and zero metric increment — violating HR4 requirement that AGPL activation be conscious operator decision.

- **Structural-to-AGPL fall-through**: BLOCK_AGPL policy only fires for transient failures (lines 665-666); structural failures fall through to else-WALK (line 670), logs but proceeds.
- **Remote version check won't block**: _check_remote_docling_version (remote.py:30-56) is warn-only; never blocks even when pipeline_version stale.
- **Missing script forwarding**: _remote_pdf_to_markdown never forwards expected_script to external service payload (lines 96-100), causing remote-converted docs to skip script-aware garble detection.

## Strategy

Type-safe contract:
1. Add GATE_AGPL_STRUCTURAL value to ConverterFailurePolicy enum (distinct, testable policy branch vs fall-through WALK)
2. When not-transient AND next-is-AGPL, assign GATE_AGPL_STRUCTURAL. Handler increments AGPL_FALLBACK_TOTAL(reason="structural_walk") and checks new config flag agpl_structural_fallback_enabled (default True for backward compat)
3. Upgrade _check_remote_docling_version to raise RemoteVersionSkewError when remote pipeline_version < local AND new config flag remote_version_enforce is True (default False for backward compat)
4. Add expected_script parameter to _remote_pdf_to_markdown and include in JSON payload (closing script-forwarding gap)

## Code Targets

| File | What | How | Constraint |
|---|---|---|---|
| `src/pageindex_mcp/converters/pipeline.py` lines 63–93 | Add GATE_AGPL_STRUCTURAL to ConverterFailurePolicy enum | Add new enum member GATE_AGPL_STRUCTURAL = 'gate_agpl_structural' with docstring | Existing RETRY, BLOCK_AGPL, WALK, REJECT unchanged; new value after BLOCK_AGPL |
| `src/pageindex_mcp/client/indexer.py` lines 664–671 | Replace structural-to-AGPL fall-through with explicit GATE_AGPL_STRUCTURAL | Add elif branch between BLOCK_AGPL and REJECT checks | Ordering: RETRY, BLOCK_AGPL, GATE_AGPL_STRUCTURAL, REJECT, WALK |
| `src/pageindex_mcp/client/indexer.py` lines 720–736 | Add GATE_AGPL_STRUCTURAL handler | Insert handler incrementing metric, checking config flag, blocking or continuing accordingly | When enabled (default), behavior identical to current WALK |
| `src/pageindex_mcp/client/remote.py` lines 30–56 | Upgrade _check_remote_docling_version to optionally block | After logger.error for pipeline_version skew, raise RemoteVersionSkewError when remote_version_enforce is True | When enforce=False (default), behavior identical to current warn-only |
| `src/pageindex_mcp/client/remote.py` lines 70–100 | Add expected_script parameter to _remote_pdf_to_markdown | Add optional expected_script kwarg and include in JSON payload with key 'expected_script' | Parameter optional; payload key 'expected_script' |
| `src/pageindex_mcp/client/indexer.py` lines 570–584 | Forward expected_script to _remote_pdf_to_markdown calls | Add expected_script=expected_script to both remote call sites | Both call sites must pass it |
| `src/pageindex_mcp/config.py` lines 399–401 | Add agpl_structural_fallback_enabled and remote_version_enforce flags | Add two bool fields with env-driven defaults, wire in from_env()/reset_pipeline_config() | Defaults preserve backward compat: True / False respectively |
| `src/pageindex_mcp/metrics/definitions.py` lines 193–197 | Verify AGPL_FALLBACK_TOTAL accepts arbitrary reason labels | No change needed; AGPL_FALLBACK_TOTAL already accepts reason labels. Document new reason in comment. | reason='structural_walk' distinct from existing reasons |

## Wiring Checks

| Symbol | Must Be Imported By | Check Type |
|---|---|---|
| ConverterFailurePolicy.GATE_AGPL_STRUCTURAL | `src/pageindex_mcp/client/indexer.py` | import |
| RemoteVersionSkewError | `src/pageindex_mcp/client/remote.py` | isinstance |
| AGPL_FALLBACK_TOTAL | `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/converters/pipeline.py` | call |
| pipeline_config.agpl_structural_fallback_enabled | `src/pageindex_mcp/client/indexer.py` | dispatch |
| pipeline_config.remote_version_enforce | `src/pageindex_mcp/client/remote.py` | dispatch |

## Test Requirements

| Test File | What to Test | Assertion Type |
|---|---|---|
| `tests/test_converters.py` | Structural failure on non-AGPL converter → AGPL triggers GATE_AGPL_STRUCTURAL, increments metric, proceeds when enabled=True | contract |
| `tests/test_converters.py` | Structural failure with enabled=False blocks walk, falls to legacy page_index | contract |
| `tests/test_converters.py` | ConverterFailurePolicy enum has GATE_AGPL_STRUCTURAL = 'gate_agpl_structural' | exhaustiveness |
| `tests/test_converters.py` | Existing transient-to-AGPL BLOCK_AGPL behavior unchanged | regression |
| `tests/test_hr3_zdr_egress.py` | _remote_pdf_to_markdown includes/omits expected_script in payload correctly | contract |
| `tests/test_hr3_zdr_egress.py` | _check_remote_docling_version raises RemoteVersionSkewError when stale and enforce=True | contract |
| `tests/test_hr3_zdr_egress.py` | _check_remote_docling_version only warns when enforce=False (default) | regression |
| `tests/test_config.py` | New flags default correctly in PipelineConfig.from_env() | contract |
| `tests/test_converters.py` | indexer.py contains reason="structural_walk" wiring | wiring |

## Corpus Validation

- **Affected documents**: All PDFs routed through converter chain; particularly those where primary Docling converter fails structurally and pymupdf4llm AGPL fallback fires
- **Expected direction**: stable
- **Spot check count**: 5

## Dependencies

None

## Complexity

Medium
