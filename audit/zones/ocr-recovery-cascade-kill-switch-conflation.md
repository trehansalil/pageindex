---
zone_name: OCR Recovery Cascade and Kill-Switch Conflation
severity: high
wave: 3
priority: 3
status: triaged
audit_date: 2026-08-28
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-28_POST-FIX-WAVE3.md
tags:
  - zone-spec
  - high
  - wave-3
---
## Mechanism to Eliminate

Three independently-evolving OCR concerns (page-level garble/low-content escalation, per-picture crop OCR, image-dominant promotion) share conflated kill-switches and decision surfaces:

1. **Kill-switch conflation**: _recover_low_content_ocr gates on pipeline_config.ocr_escalation_garble instead of dedicated flag; disabling garble OCR silently disables low-content recovery.

2. **Marker orphaning**: _recover_picture_results returning [] when per-picture kill-switch fires leaves literal `<!-- image -->` markers in tree output with no stripping step.

3. **Hidden re-entry guard**: decide_ocr_strategy ordered if-chain where sequence IS specification; re-entry guard position load-bearing but invisible to callers.

4. **Narrow eligibility**: recovery eligibility checks only state.first_defect, so garble as secondary defect behind NODE_COUNT_LOW never triggers garble-specific recovery.

5. **Coupled metrics**: _recover_image_dominant_ocr shares _execute_ocr_retry with garble/low-content paths, coupling success/failure metrics.

6. **Separate code path**: standalone .jpg/.png pipeline bypasses per-picture OCR gate entirely, creating decision-surface gap.

## Strategy

Decouple the three kill-switches into independent config flags, add marker-cleanup step when per-picture OCR skipped, and extract recovery eligibility into defect-set predicate (not first_defect only):

- Add ocr_escalation_low_content config flag (independently gatable)
- Add strip_unresolved_image_markers() cleanup when _recover_picture_results returns empty
- Widen recovery eligibility to check all active defects (state.active_defects set) not just first_defect
- Delete decide_ocr_mode wrapper (all callers use decide_ocr_strategy directly)

## Code Targets

| File | What | How | Constraint |
|---|---|---|---|
| `src/pageindex_mcp/config.py` lines 374–395 | Add dedicated ocr_escalation_low_content config flag | Add ocr_escalation_low_content: bool field after line 374. Wire to env var OCR_ESCALATION_LOW_CONTENT defaulting to current ocr_escalation_garble for backward compat. Add module-level alias. | Default must match ocr_escalation_garble; existing OCR_ESCALATION_GARBLE continues controlling garble-only |
| `src/pageindex_mcp/client/recovery.py` line 438 | Switch _recover_low_content_ocr to new flag | Change line 438 from 'if not pipeline_config.ocr_escalation_garble:' to 'if not pipeline_config.ocr_escalation_low_content:' | Only flag check changes; no signature/behavior change |
| `src/pageindex_mcp/picture_plane.py` lines 430–475 | Add strip_unresolved_image_markers() and delete decide_ocr_mode | Add pure function strip_unresolved_image_markers(md: str) -> str removing all `<!-- image -->` markers. Delete decide_ocr_mode (thin wrapper, 1 caller already switched). | Pure function, no side effects; reuse _IMAGE_MARKER constant |
| `src/pageindex_mcp/converters/pipeline.py` lines 628–637 | Strip markers when _recover_picture_results returns empty | After line 637, add: if not pic_results: md = strip_unresolved_image_markers(md). Import from picture_plane. | Only strip when pic_results empty; do not mutate pre_fallback_md |
| `src/pageindex_mcp/helpers/types.py` line 257 | Widen recovery_eligible signature | ExtractionState already carries gate_result encoding all fired defects. No type change to GateSpec. | GateSpec is frozen dataclass; do not change structure |
| `src/pageindex_mcp/helpers/gates.py` lines 270–307 | Widen recovery predicates to check all active defects | Change _eligible_garble and _eligible_low_content to check gate_result for defect presence, not just first_defect. Use gate_result.get() with safe defaults (gate_result may be None before gates run). | Preserve severity ordering (first_defect still determines primary); safe dict access |
| `src/pageindex_mcp/client/indexer.py` lines 779–781 | Add marker cleanup fallback | After line 781, add elif: if not state.pic_results and '<!-- image -->' in md_content: md_content = strip_unresolved_image_markers(md_content). Import from picture_plane. | Do not strip when pic_results contains entries; import from picture_plane not pictures.py |

## Wiring Checks

| Symbol | Must Be Imported By | Check Type |
|---|---|---|
| strip_unresolved_image_markers | `src/pageindex_mcp/converters/pipeline.py`, `src/pageindex_mcp/client/indexer.py` | import |
| ocr_escalation_low_content | `src/pageindex_mcp/client/recovery.py` | call |
| strip_unresolved_image_markers | `src/pageindex_mcp/converters/pipeline.py`, `src/pageindex_mcp/client/indexer.py` | call |
| decide_ocr_strategy | `src/pageindex_mcp/converters/pictures.py`, `src/pageindex_mcp/client/indexer.py` | import |

## Test Requirements

| Test File | What to Test | Assertion Type |
|---|---|---|
| `tests/test_recovery.py` | _recover_low_content_ocr gates on new ocr_escalation_low_content flag independently from ocr_escalation_garble | contract |
| `tests/test_recovery.py` | Disabling ocr_escalation_garble does NOT silently disable low-content recovery when ocr_escalation_low_content is True | regression |
| `tests/test_gates.py` | Widened _eligible_garble: garble recovery fires when garble is secondary defect (first_defect=NODE_COUNT_LOW but GARBLING in gate_result) | contract |
| `tests/test_gates.py` | strip_unresolved_image_markers: removes all `<!-- image -->` markers; no-op on markdown without markers; partial markers not stripped | exhaustiveness |
| `tests/test_converters.py` | When _recover_picture_results returns [] (OCR skip), downstream strips `<!-- image -->` markers from md output | regression |
| `tests/test_converters.py` | decide_ocr_mode wrapper removed; importing it raises ImportError | wiring |
| `tests/test_recovery.py` | Integration: full recovery loop with NODE_COUNT_LOW first + GARBLING secondary. Both _recover_low_content_ocr AND _recover_garble_ocr fire in severity order. | integration |

## Corpus Validation

- **Affected documents**: AVB_Wohngebaeude_2022.pdf, Vertragsunterlagen_2024.pdf, AKB_2015.pdf
- **Expected direction**: improve
- **Spot check count**: 5

## Dependencies

- Garble Detection Cross-Cutting Kernel (Wave 2)

## Complexity

Medium
