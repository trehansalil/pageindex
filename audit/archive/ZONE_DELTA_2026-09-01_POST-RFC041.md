# Zone Delta Analysis — POST-RFC041

**Current audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-01_POST-RFC041.md
**Prior audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-29_POST-FIX-WAVE4.md
**Date:** 2026-09-01

## Summary

Across RFC-041 remediation and Tier-0 markdown-first extraction work, 7 active defect zones were reassessed against the prior 8-zone post-fix-wave audit. Net improvement: 10 fewer bugs tracked (down from ~33 to ~23), with 6 zones showing improvement and 2 zones regressing to critical severity. The core architectural hazards remain partially addressed: multi-writer verdict cascades, config snapshot vs live-read divergence, and OCR recovery chain semantics are now framed more precisely but require deeper refactoring than incremental fixes have delivered. Two zones merged during remediation scope review, reflecting consolidation of overlapping pattern-reporting rather than defect closure.

## Delta Table

| Zone | Status | Severity (prior→current) | Bugs (prior→current) | Proposal Status | Key Change |
|---|---|---|---|---|---|
| Verdict Computation & Promotion Cascade | improved | critical→critical | 7→6 | partially_implemented | Multi-writer state divergence reframed; cascade ordering still cited as unresolved; config-snapshot threshold drift compounded. |
| Garble Detection & NFKC Signal Destruction | improved | critical→high | 6→4 | partially_implemented | Pre-NFKC signal capture partly carried out; ScriptContext still permits unsafe null-detector pattern. |
| Verdict Persistence Dual-Writer | improved | critical→high | 5→2 | not_implemented | Bug count halved; core five-path writer defect unfixed; CAS-authority collapse not implemented. |
| Config Snapshot vs Live-Read Divergence | improved | high→medium | 4→2 | partially_implemented | Some os.environ re-reads consolidated; BIDI_COHERENCE_ENFORCE mismatch recurs; CI-guard not fully deployed. |
| OCR Recovery Cascade & Converter Fallback Chain | regressed | high→critical | 4→8 | not_implemented | RETRY bare-continue unfixed (loop cannot rewind); absorbed duplicate OCR + gate-ordering flip; converter-chain rewrite not implemented. |
| OCR Recovery Dispatch (merged into above) | regressed | high→critical | 4→8 | no_proposal | Tuple-keyed dedup and unguarded tessdata raise absorbed into merged OCR zone; no evidence of fix application. |
| Content Measurement Blind Spot (Table Block Text Extraction) | improved | high→high | 4→3 | no_proposal | Garble-accessor triplication folded into Garble Detection zone; table/image block text-key duplication remains; schema design trap unaddressed. |
| HR2 Erasure Cascade Hidden Ordering Dependencies | improved | medium→medium | 2→1 | no_proposal | Bug count trimmed; ordering hazards persist: ctx.doc_name population and ctx.sha256 read from file-deletion-order-dependent sidecar still unchecked. |

## Per-Zone Details

### Verdict Computation & Promotion Cascade
**Status:** improved  
**Severity:** critical→critical  
**Bugs:** 7→6

Multi-writer state divergence (finalize_gate_and_route bypassed at 6 call sites in recovery.py) has evolved into a threshold-coupling and promotion-cascade-ordering framing. Bug count decreased from 7 to 6, but the core precedence-locked six-guard cascade (RFC-040 D2) is still cited as unresolved in current audit notes. The proposal's refactoring is only partially implemented; additionally, config-snapshot vs live-read threshold drift (DEPTH_ADEQUACY_FLOOR/CHAR_FLOOR) now compounds the cascade correctness hazard.

**Proposal Status:** partially_implemented

### Garble Detection & NFKC Signal Destruction
**Status:** improved  
**Severity:** critical→high  
**Bugs:** 6→4

The had_presentation_forms signal is now captured correctly at patched call sites, representing partial implementation of the pre-NFKC signal-capture proposal. However, ScriptContext still permits had_presentation_forms=False with no compile-time enforcement, allowing unsafe state transitions. Additionally, the bidi-coherence gate exhibits the identical null-detector pattern (zero violations misread as proof of safety) flagged in prior audits, indicating incomplete pattern fixes.

**Proposal Status:** partially_implemented

### Verdict Persistence Dual-Writer
**Status:** improved  
**Severity:** critical→high  
**Bugs:** 5→2

Bug count nearly halved and severity downgraded, reflecting some progress in verdict-writing paths. However, the core architectural defect remains unfixed: five independent code paths still write verdict across two stores. Additionally, registry_backfill/backfill.py:145 still falsely asserts that save_doc_meta enforces CAS (compare-and-set) priority. The proposed single-CAS-authority collapse (converters_cli child should never write verdict) was not implemented.

**Proposal Status:** not_implemented

### Config Snapshot vs Live-Read Divergence
**Status:** improved  
**Severity:** high→medium  
**Bugs:** 4→2

Bug count dropped from 4 to 2 and severity downgraded, consistent with some os.environ re-reads (tree_split.py/indexer.py) being consolidated onto pipeline_config. However, the flagship BIDI_COHERENCE_ENFORCE truthiness-mismatch between config.py's _envbool and gates.py's exact-match check is reported again verbatim in the current audit, indicating the proposed CI-guard and consolidation were only partially carried out.

**Proposal Status:** partially_implemented

### OCR Recovery Cascade & Converter Fallback Chain (Merged)
**Status:** regressed  
**Severity:** high→critical  
**Bugs:** 4→8 (this zone) + prior 4→8 (recovery dispatch, merged)

The RETRY handler's bare `continue` statement (comment claims 'rewind idx' but the for-loop cannot rewind) is the identical unfixed defect from the prior audit. The proposed while-loop rewrite was not implemented. This zone has now absorbed the Recovery-dispatch tuple-keyed-dedup pattern (causing duplicate full-page OCR passes) and the unguarded ensure_tessdata raise (indexer.py:885), plus a newly-flagged gate-ordering flip: node_count and depth gates are now evaluated before the garbling gate, reversing the prior ordering. Combined, these changes escalated severity to critical with 8 tracked bugs. No evidence exists that the dedup-by-tuple-vs-by-member-name fix was ever applied.

**Proposal Status:** not_implemented / no_proposal (merged zone)

### Content Measurement Blind Spot (Table Block Text Extraction)
**Status:** improved  
**Severity:** high→high  
**Bugs:** 4→3

The garble-accessor-triplication half of this zone (detect_garble vs _garble_check_nodes fallback vs _garble_check_flat_blocks) appears to have folded into the current Garble Detection & NFKC zone's narrative during remediation scope consolidation. The surviving zone narrows to the flat.py table/image block text-key accessor duplication, with bug count trimmed from 4 to 3. However, the underlying no-text-key schema design decision that creates the structural trap remains unaddressed.

**Proposal Status:** no_proposal

### HR2 Erasure Cascade Hidden Ordering Dependencies
**Status:** improved  
**Severity:** medium→medium  
**Bugs:** 2→1

Bug count trimmed from 2 to 1, reflecting some consolidation in the current audit framing. However, both underlying ordering hazards are still described verbatim: (1) ctx.doc_name is populated only by step 1 of the erasure cascade, so steps 5/7 silently skip if reordered; (2) ctx.sha256 is read from the very processed/<id>.meta.json sidecar that step 3 deletes, creating a data-flow ordering dependency. The validate_erasure_manifest function still checks only prefix-completeness, not inter-step data-flow ordering semantics.

**Proposal Status:** no_proposal

## New Zones

None.

## Closed Zones

None. The two-zone merger (OCR Recovery Cascade merged with OCR Recovery Dispatch) represents clarification and consolidation of pattern reporting rather than defect closure.

## Observations

- **Regression driver:** The OCR Recovery Cascade zone's regression to critical severity is driven by the unfixed RETRY bare-continue loop-rewind defect and the newly-flagged gate-ordering flip. This zone aggregates multiple independent hazards that prior audits treated separately.
- **Proposal execution gap:** Across 8 zones, only 2 carry "partially_implemented" status; 3 remain "not_implemented"; 3 carry "no_proposal". This indicates that incremental fixes are addressing symptom reduction (bug count down 10 net) without closing the structural defects that the proposals identified.
- **Pattern recurrence:** NFKC signal destruction, null-detector false-negatives, and config-snapshot vs live-read divergence all recur verbatim in the current audit, suggesting that prior fix attempts were incomplete or were later reverted during unrelated changes.
- **Scope clarification:** Merging of OCR Recovery Dispatch into OCR Recovery Cascade during RFC-041 review reflects tighter problem definition rather than defect resolution.