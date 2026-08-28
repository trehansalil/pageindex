---
zone_name: Converter Chain Fallback and AGPL Gating
severity: high
bug_count: 4
status: regressed
audit_date: 2026-08-28
audit_run: POST-FIX-WAVE3
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md
key_files:
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/converters/pipeline.py
  - src/pageindex_mcp/converters/normalize.py
tags:
  - zone-spec
  - high
  - agpl
  - converter
  - compliance
scorecard_verdict: regressed
scorecard_date: 2026-08-28
scorecard_run: POST-FIX-WAVE3
---
## Mechanism

The converter chain walk treats all failures uniformly, with a ConverterFailurePolicy classification that allows **structural failures to silently advance to AGPL-licensed converters**. Remote Docling microservice runs separately-deployed image predating local converter fixes (bidi heading guard never committed, expected_script not forwarded), creating **local-vs-remote code divergence** that no test can catch:

- Structural failures still allow walking to AGPL converters (only logging warning) — **violates CLAUDE.md Hard Rule #4**
- Remote Docling service runs independently-versioned image with no contract enforcement (no version assertion or script field in payload)
- Local fixes to normalize.py or garble.py have zero effect on remotely-routed documents

## Code Evidence

**indexer.py:441–914** — `_convert_to_tree`:
- Lines 560–565: converter chain walk loop
- Lines 576–600: failure-mode classification via `_classify_transient_failure`
- Lines 609–625: ConverterFailurePolicy decision: RETRY, BLOCK_AGPL, REJECT, or **WALK** (allows advancing to AGPL)
- Lines 570–572: NOTE that remote path does NOT forward expected_script

**pipeline.py:682–770** — `pdf_markdown_converters`:
- Builds converter chain with is_agpl flags
- Lines 641, 656: AGPL_FALLBACK_TOTAL metric shows operator awareness
- No hard gate beyond ConverterFailurePolicy classification

## Key Files

| File | Role |
|------|------|
| src/pageindex_mcp/client/indexer.py | Converter chain walk, failure classification |
| src/pageindex_mcp/converters/pipeline.py | Converter chain definition, AGPL metrics |
| src/pageindex_mcp/converters/normalize.py | Local converter fixes not reaching remote |

## Related Issues

- Chain 1: RFC-033 bidi heading guard never committed
- Chain 3: Rotation detection asymmetric
- Chain 4: Unconditional chain-walk silently activates AGPL
- Chain 29: Converter-gate-route ordering entanglement

## Compliance Note

**CLAUDE.md Hard Rule #4:** "Never silently persist a low-quality tree" + AGPL licensing requirement. Current implementation violates both by allowing structural failures to silently advance to AGPL converters with only logger.warning.

