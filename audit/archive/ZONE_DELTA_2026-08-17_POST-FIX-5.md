# Zone Delta — POST-FIX-5 (2026-08-17)

**Baseline:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-17_POST-FIX-4.md (pre-remediation)
**Commits:** `7b345c4` (Zone 1), `f37584e` (Zone 5), `646cdc0` (Zone 2)
**Branch:** feat/pdf-inspector-shadow-pilot

## Summary

| Zone | Status | Severity (prior→current) | Bugs (prior→current) | Key Change |
|---|---|---|---|---|
| Garble Detection Hydra | improved | critical→high | 12→6 | `check_garble()` + `GarbleContext` sole entry point (26 callsites); legacy `_tree_is_garbled`/`_flat_text_is_garbled` still exist as internal helpers |
| God Function Routing Cascade | **closed** | critical→low | 11→2 | `index()` 1365→153 lines; `ExtractionState` dataclass; 7 recovery methods + shared `_reconvert_and_revalidate`; partial-state-revert bug class eliminated |
| Verdict Persistence Split-Brain | stalled | high→high | 7→7 | Not targeted this cycle |
| Threshold Calibration Feedback Loops | stalled | high→high | 8→8 | Not targeted this cycle |
| OCR/Enrichment Signal Conflation | **closed** | high→low | 9→3 | `OCR_ESCALATION` split into `_GARBLE` + `_PER_PICTURE`; `primary_text` on TreeSignals; `_apply_picture_enrichment` unified |
| Conversion Pipeline Stage Coupling | stalled | high→high | 7→7 | Not targeted this cycle |
| Registry/Persistence Consistency Gaps | stalled | medium→medium | 6→6 | Not targeted this cycle |
| Dead/Uncommitted/Stale Code Divergence | improved | medium→medium | 6→5 | 2 unwired symbols resolved (ExtractionState, _reconvert_and_revalidate landed) |

## Per-Zone Details

### Zone 1: Garble Detection Hydra — improved (critical→high, 12→6)

Wave 1 (`7b345c4`) introduced `check_garble()` with `GarbleContext` enum as the sole public garble API across `helpers.py`, `converters.py`, and `client.py` (26 callsites). All client.py callsites now route through `check_garble` instead of calling `_is_garbled_blob`/`_flat_text_is_garbled` directly.

**Resolved (6):**
- RFC-033 D1 tautology: single function eliminates divergence between `_tree_is_garbled` and `_flat_text_is_garbled`
- ISS-36 duplicated digit-ratio floor guards: unified under `check_garble`
- RFC-028 D2 no-treatment-path: `check_garble` fires at all callsites uniformly
- converters.py `_has_sparse_mojibake` silent omission: `check_garble` always runs both checks
- Cross-cutting Issue 3 Latin-gibberish bypass: uniform `_has_sparse_mojibake` coverage via `check_garble`
- RFC-033/034 null bidi detector: encoding-range mismatch caught uniformly

**Remaining (6):**
- `presentation_forms` prong still active (helpers.py:1289-1296) — proposal says delete
- `_tree_is_garbled` still standalone (helpers.py:1577) — called from `TreeSignals.from_tree` but not yet folded in
- `expected_script=None` on flat path in `classify_verdict` (Discovery #5331) — not yet threaded
- `_flat_text_is_garbled` still exists as internal helper, not yet deleted
- Per-node `_infer_script` override inconsistency
- Flat-path markdown-formatting dilution (wave 4 behavioral fix not yet applied)

**Severity downgrade rationale:** The sole-entry-point pattern eliminates the fix-in-one-function-regress-in-another bug class that drove critical severity. Remaining bugs are localized cleanup items, not systemic.

---

### Zone 2: God Function Routing Cascade — closed (critical→low, 11→2)

Wave 3 (`646cdc0`) decomposed the 1365-line `index()` method into a 153-line orchestrator + `ExtractionState` dataclass + 12 focused methods. The reconvert+revalidate 4x duplication is eliminated. Recovery methods are independent — adding a new `TreeDefect` flows through `decide_route()` without new if/elif branches.

**Resolved (9):**
- RFC-030 D1 partial-state-revert: `ExtractionState` dataclass moves as a unit — impossible to restore tree without md_content
- RFC-029 D0/D1/D2/D8 unwired defects: new defects flow through `decide_route()` policy, never fall through unhandled
- RFC-005 Fix-3 OCR-gated-on-garbling-only: independent recovery guard clauses
- RFC-029 D3 fence-stripping blast radius: contained to `_recover_ocr_escalation`
- RFC-030 D2 fallthrough: orchestrator has explicit route switch, no unhandled case
- RFC-029 D1 content-density gate: isolated in `_recover_ocr_escalation`
- 4x reconvert+revalidate duplication: eliminated by `_reconvert_and_revalidate` shared helper
- `ExtractionState` dataclass: implemented (was "never implemented")
- `_rebuild_and_validate`: implemented as `_reconvert_and_revalidate` (was "never implemented")

**Remaining (2):**
- `_gate_bidi_degraded` dead gate still in GATE_TABLE (helpers.py:1793)
- `low_content_ocr_eligible` workaround still active in `_recover_ocr_escalation` (client.py:1266-1304)

**Severity downgrade rationale:** The god-function pattern is eliminated. Remaining items are individual dead-code/workaround cleanup, not architectural defects.

---

### Zone 5: OCR/Enrichment Signal Conflation — closed (high→low, 9→3)

Wave 2 (`f37584e`) split `OCR_ESCALATION` into two independent flags, added `primary_text` to `TreeSignals`, and extracted `_apply_picture_enrichment` as a shared helper for PDF and standalone-image paths.

**Resolved (6):**
- `_OCR_ESCALATION` single-boolean conflation: split into `_GARBLE` + `_PER_PICTURE`
- `sig.flat_text` conflation in verdict path: `primary_text` field structurally excludes enrichment metadata
- Standalone image vs PDF enrichment divergence: `_apply_picture_enrichment` shared helper
- RFC-025 D1 `_flat_block_text` conflation: `_flat_block_primary_text` separates verdict from search
- RFC-026 warid-597 barcode noise earning PASS: `primary_text` excludes image-block content
- OCR/image-block conflation: `content_class` computation now aware of image blocks via unified pipeline

**Remaining (3):**
- `max_leaf_ratio` gate bypass for `image_enrichment_promoted` docs — enrichment promotion still skips the hard-fail gate
- Image-dominant OCR gate (client.py `_recover_image_dominant_ocr`) uses `_OCR_ESCALATION_GARBLE` — re-conflates a third behavior
- `content_class` computation still counts only table/kv/prose; image blocks invisible to routing

**Severity downgrade rationale:** The single-boolean conflation — the mechanism that drove repeated regressions — is eliminated. Remaining items are edge-case gaps, not systemic coupling.

---

### Zones 3, 4, 6, 7 — stalled (not targeted)

No changes from the pre-remediation baseline. Bug counts and severities unchanged.

### Zone 8: Dead/Uncommitted/Stale Code — improved (6→5)

Collateral improvement: `ExtractionState` and `_reconvert_and_revalidate` were "never implemented" items tracked in Zone 8 — now landed via Zone 2. Core Zone 8 items (dead `PDF_INSPECTOR_PRECLASSIFY`, stale remote Docling image) unchanged.

## Unwired Symbols

Pre-remediation: 12 unwired symbols.
Post-remediation: 9 unwired symbols (3 resolved, all Zone 2).

| # | Symbol | Status | Zone |
|---|--------|--------|------|
| 1 | `presentation_forms` prong | STILL_UNWIRED | Zone 1 |
| 2 | `_tree_is_garbled` standalone | STILL_UNWIRED | Zone 1 |
| 3 | `decide_recovery()` | RESOLVED — superseded by 7-method recovery pipeline | Zone 2 |
| 4 | `_gate_bidi_degraded` | STILL_UNWIRED | Zone 2 |
| 5 | `low_content_ocr_eligible` | STILL_UNWIRED | Zone 2 |
| 6 | `ExtractionState` dataclass | RESOLVED — helpers.py:177 | Zone 2 |
| 7 | `_rebuild_and_validate` | RESOLVED — as `_reconvert_and_revalidate` client.py:921 | Zone 2 |
| 8 | `_hard_gate()` | STILL_UNWIRED | Zone 4 |
| 9 | `hysteresis_band` | STILL_UNWIRED | Zone 4 |
| 10 | `write_verdict` | STILL_UNWIRED | Zone 3 |
| 11 | `_confirm_write_visible` | STILL_UNWIRED | Zone 3 |
| 12 | `recompute_verdicts` | STILL_UNWIRED | Zone 3 |

## Metrics

- **Net bug delta:** -22 (66 → 44)
- **Zones closed:** 2 (Zone 2, Zone 5)
- **Zones improved:** 2 (Zone 1, Zone 8)
- **Zones stalled:** 4 (Zones 3, 4, 6, 7)
- **Unwired symbols resolved:** 3 of 12
