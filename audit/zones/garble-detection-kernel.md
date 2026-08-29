---
zone_name: Garble Detection Kernel
severity: critical
bug_count: 7
status: stalled
audit_date: 2026-08-12
audit_run: POST
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md
key_files:
  - src/pageindex_mcp/helpers/garble.py
  - src/pageindex_mcp/converters/pictures.py
  - src/pageindex_mcp/helpers/tree_validation.py
  - src/pageindex_mcp/config.py
tags:
  - zone-spec
  - critical
  - garble-detection
  - shared-choke-point
scorecard_verdict: regressed
scorecard_date: 2026-08-12
scorecard_run: POST
---
## Mechanism

Single-surface API with 15+ direct callers: any change to GarbleConfig defaults, the RFC-025 D2 short-circuit, or any prong threshold has broad blast radius across OCR escalation, verdict gating, and picture-text recovery simultaneously.

The NFKC destruction problem is fundamental: pipeline normalization decomposes Arabic presentation-form codepoints before detect_garble sees the text, and the compensating heuristic (garble.py:585-593) is a workaround that cannot fully undo the destruction.

The consolidation of _tree_is_garbled vs _flat_text_is_garbled created a NEW problem: the garble_short_text_default config flag became a hidden global mode switch affecting all 15+ callers.

The digit-ratio prong (garble.py:399-410) is gated behind cfg.garble_digit_floor, so short numeric-junk blobs escape detection. The FLAT-03 routing path entirely bypasses validate_tree, meaning digit-junk corruption passes with zero quality gate.

## Code Evidence

**detect_garble** (garble.py:529-614): 15+ callers confirmed across converters, gates, tree_validation, pictures, and indexer.

**garble_prongs** (garble.py:339-440): digit-ratio check gates behind len>garble_digit_floor threshold.

**NFKC compensation** (lines 585-593): `elif _arc > 0 and _pf == 0 and _effective_script == "Arabic": _had_pf = True`.

**_text_layer_has_content** (pictures.py:269-272): ScriptContext fallback now calls _infer_pf.

## Key Files

| File | Role |
|------|------|
| src/pageindex_mcp/helpers/garble.py | Detection kernel & prong definitions |
| src/pageindex_mcp/converters/pictures.py | Picture text-layer validation |
| src/pageindex_mcp/helpers/tree_validation.py | Integration with verdict gates |
| src/pageindex_mcp/config.py | GarbleConfig thresholds |

## Related Zones

- [[verdict-gate-cascade]] (verdict gating depends on this)
- [[ocr-recovery-cascade]] (recovery eligibility narrower)
- [[measurement-and-audit-self-reinforcing-blind-spot]] (shared blind spot)
