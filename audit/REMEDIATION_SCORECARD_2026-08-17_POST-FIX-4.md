# Remediation Scorecard — POST-FIX-4 (2026-08-17)

**Pre-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-13_POST-FIX-3.md
**Post-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-17_POST-FIX-4.md
**Delta report:** audit/ZONE_DELTA_2026-08-12_POST-FIX-4.md

## Verdict: NEEDS ANOTHER CYCLE

Fix-4 nets a bug-count improvement (47 → 40, -7) but no zone actually closed: four zones improved, one regressed, two stalled, and one new zone split off from prior work. Twelve symbols named for deletion or implementation in earlier zone proposals remain unwired — including a proposal (Zone 3 / write_verdict) that was implemented in the opposite direction from what was specified. The critical-severity Garble Detection Hydra zone is stalled at 6 bugs with no progress since Fix-3, and the previously medium-severity conversion pipeline coupling zone regressed toward high severity. Net effect: real but partial progress, offset by incomplete wiring and one active regression, so another remediation cycle is required before any zone can be declared closed.

## Zones Closed (0)

| Zone | Was Severity | Bugs Eliminated |
|---|---|---|
| — | — | — |

No zones closed this cycle.

## Zones Remaining (8)

| Zone | Severity | Bug Count | Status |
|---|---|---|---|
| Garble Detection Hydra | critical | 6 | stalled |
| God Function Routing Cascade (client.py index()) | critical | 5 | improved |
| Verdict Persistence Split-Brain | high | 4 | stalled |
| Threshold Calibration Feedback Loops | high | 4 | improved |
| OCR/Enrichment Signal Conflation | high | 5 | improved |
| Conversion Pipeline Stage Coupling (pdf_to_markdown_docling) | high | 4 | regressed |
| Registry/Persistence Consistency Gaps | medium | 6 | stalled |
| Dead/Uncommitted/Stale Code Divergence | medium | 6 | improved |

## New Zones (1)

| Zone | Severity | Introduced By |
|---|---|---|
| Registry/Persistence Consistency Gaps | medium | Split from prior zone 5 (Triple-write verdict persistence). Registry-specific persistence issues (write-visibility barriers, fire-and-forget deletes, backfill completion flags, sole-read-path after MinIO fallback removal) separated into dedicated zone. Partially a refactoring of existing findings, partially surfaced by RFC-034 D18 write-visibility barrier changes. |

**RED FLAG:** This zone is largely a decomposition of pre-existing findings rather than a genuinely new defect surface, but it also absorbs real new issues from RFC-034 D18 (write-visibility barrier). Treat as watch-list, not noise — the `_confirm_write_visible` barrier it centers on is independently flagged below as an unwired, actively regressing symbol.

## Metrics

- **Net bug delta:** -7 (47 → 40)
- **Improved:** 4 zones
- **Regressed:** 1 zone
- **Stalled:** 2 zones
- **New:** 1 zone
- **Closed:** 0 zones
- **Wiring status:** some_unwired

### Unwired Symbols (12)

| Symbol | Location | Proposal Origin | Gap |
|---|---|---|---|
| `presentation_forms` prong | helpers.py:1252-1259 | Zone 1 | Proposal says delete after NFKC normalization; still active |
| `_tree_is_garbled` | helpers.py:1474 | Zone 1 | Proposal says consolidate into `TreeSignals.from_tree`; still standalone |
| `decide_recovery()` | — | Zone 2 | Never implemented |
| `_gate_bidi_degraded` | helpers.py:1575 | Zone 2 | Proposal says delete dead gate; still in `GATE_TABLE:1691` |
| `low_content_ocr_eligible` | client.py:1277 | Zone 2 | Proposal says delete workaround; still active |
| `ExtractionState` dataclass | — | Zone 2 | Never implemented |
| `_rebuild_and_validate` | — | Zone 2 | Never implemented |
| `_hard_gate()` | — | Zone 4 | Never implemented |
| `hysteresis_band` | helpers.py:276 (used at 2117) | Zone 4 | Proposal says delete entirely; still active |
| `write_verdict` | storage.py:653 | Zone 3 | Proposal said eliminate; instead consolidated as entry point — opposite direction taken |
| `_confirm_write_visible` | storage.py:44 (4 call sites) | Zone 3 | Barrier causing timing regressions; still active |
| `recompute_verdicts` | preprocess_client.py:221 | Zone 3 | Second offline recomputer diverging from `promotion_sweep`; still separate |

## Recommended Next Steps

**Priority 1 — Zone 1 (Garble Detection Hydra):** stalled at critical/6 bugs. Complete the proposal: delete the `presentation_forms` prong after NFKC normalization, consolidate `_tree_is_garbled` into `TreeSignals.from_tree`, fix `expected_script=None` on the flat path in `classify_verdict`.

**Priority 2 — Zone 6 (Conversion Pipeline Stage Coupling):** regressed medium-to-high. Needs a simplification proposal (currently has none). The fence-marker stripping catastrophe (SLA 264→0 blocks) and heading injection preventing flat-fallback suggest the two-candidate source selection with independent heading recovery chains needs architectural simplification.

**Priority 3 — Zone 3 (Verdict Persistence Split-Brain):** stalled at high/4, proposal went opposite direction (consolidated around `write_verdict` instead of eliminating it). Decide on architectural direction: either finish the `write_verdict` consolidation properly or pivot to the eliminate-and-merge-into-`save_doc_meta` approach. Unify the two offline recomputers (`promotion_sweep` vs `recompute_verdicts`).

**Priority 4 — Wire the unwired:** wire the 12 unwired symbols from partially-implemented proposals before adding new features — the incomplete implementations are themselves a defect source (Zone 8 exists precisely because of this pattern).
