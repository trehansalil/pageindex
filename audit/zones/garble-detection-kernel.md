---
zone_name: Garble Detection Kernel
severity: critical
bug_count: 5
status: partially-remediated
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
remediation_date: 2026-08-29
remediation_provenance: source-verified
---

## Remediation status (2026-08-29)

**The wave-2 triage spec is retired — it described work already shipped.**
Commit `e02ec93` implemented all four of its items:

| Triage-spec item | Landed in |
|---|---|
| `_infer_presentation_forms` at verdict/images/indexer + export from `helpers/__init__.py` | `images.py:135`, `indexer.py:513`, `helpers/__init__.py:96` |
| `_garble_check_nodes` reads table content via `_node_text_parts` | `garble.py:686-698` (title excluded, dedicated check retained) |
| Short-text numeric-junk prong below `garble_digit_floor` | `garble.py:402-409`, prong `numeric_junk_short`, >0.90 threshold |
| Wire the dead `_effective_script` into `latin_gibberish` | `garble.py:428` |

**What that pass missed, fixed 2026-08-29.** `e02ec93` closed three
`had_presentation_forms=False` fallbacks and left two live ones in the same
file, both on paths that run *after* NFKC normalisation:

- `indexer.py:996` — the flat-path per-block garble gate (`flat_garble_gate`)
- `indexer.py:1024` — the VLM fallback re-check (`vlm_fallback_garble`)

Each now infers from the same text its gate goes on to check
(`_infer_presentation_forms(flat_md)` / `(vlm_md)`). Until this fix, an Arabic
document whose presentation-form codepoints were normalised away reached both
gates asserting it never had any, so the compensation at `garble.py:583` could
not fire and the document read as clean.

`tests/test_architecture_guards.py::TestPresentationFormsNotHardcoded` pins
this shut. It walks the `src/` AST for `had_presentation_forms=False` keyword
arguments — parsed, not grepped, so the docstring at `garble.py:41` that
*describes* the defect is not mistaken for an instance of it. The single
allowed occurrence is `ScriptContext.from_script_str()` in `script.py`, the
documented no-information constructor. Verified adversarially: run against
pre-fix `HEAD`, the guard reports exactly lines 998 and 1024.

**Still open in this zone (5 bugs).** The blast-radius mechanism below is
untouched: `detect_garble` remains a 15+ caller shared kernel, and
`garble_short_text_default` remains a hidden global mode switch. Those need a
design pass, not a patch.
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
