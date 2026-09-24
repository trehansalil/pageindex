# Remediation Scorecard — POST-FIX-2 (2026-08-13)

**Pre-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-11_RUN-2.md
**Post-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12.md
**Delta report:** audit/ZONE_DELTA_2026-08-12_POST-FIX-2.md

## Verdict: NEEDS ANOTHER CYCLE

Fix-2 closed exactly one zone — Zone 5 (reason as diagnosis+routing inside `index()`) — by deleting the legacy reason-routing mechanism outright rather than layering a typed abstraction on top of it, eliminating all 8 bugs attributed to that zone. The six remaining zones (three critical, three high) are unchanged since the prior audit: no bugs closed, none newly introduced, but none advanced either. The zone-5 pattern is the template to repeat — delete, don't supplement — and the wiring check surfaces six concrete places where prior "fixes" left the old mechanism running alongside the new one (duplicate RTL deciders, contradictory landscape predicates, non-required `expected_script`, and validate_tree's still-sequential 11-gate cascade). Net effect: real progress on one zone, no regression elsewhere, but the three critical zones (verdict engine, OCR/enrichment marker-count contract, Arabic/RTL) remain fully stalled and need a dedicated cycle before this can be called stable.

## Zones Closed (1)

| Zone | Was Severity | Bugs Eliminated |
|---|---|---|
| Zone 5: reason as diagnosis+routing inside `index()` | critical | 8 |

## Zones Remaining (6)

| Zone | Severity | Bug Count | Status |
|---|---|---|---|
| Zone 1: Verdict engine (11-gate first-match cascade + dual re-derivation) | critical | 12 | stalled |
| Zone 2: OCR escalation vs per-picture enrichment (marker-count contract) | critical | 11 | stalled |
| Zone 3: Arabic/RTL (six deciders + 10-prong garble gate x 13 call sites) | critical | 9 | stalled |
| Zone 4: pdf_to_markdown_docling (dual pipelines + positional stages) | high | 9 | stalled |
| Zone 6: Verdict persistence (five writers, no CAS, sidecar-only) | high | 8 | stalled |
| Zone 7: Flag/threshold sprawl (~35 kill-switches) | high | 7 | stalled |

## New Zones (0)

| Zone | Severity | Introduced By |
|---|---|---|
| — | — | — |

No new zones introduced by Fix-2.

## Metrics

- Net bug delta: -8
- Wiring status: some_unwired
- Unwired symbols:
  - `_is_garbled_blob` (helpers.py:1942, image_enrichment_promoted path) called without `expected_script` — unconditionally disables the `latin_gibberish` prong
  - `_tree_is_rtl_reversed` (helpers.py:1485) and `_check_bidi_coherence` (helpers.py:1344) — duplicate RTL samplers survive alongside `decide_rtl()`; six orientation deciders not consolidated
  - `_detect_arabic_reversal` (converters.py:111) and `reconstruct_bidi_order` (converters.py:1483) — old converter-level deciders not deleted, only shimmed to `decide_rtl()`
  - Two contradictory landscape predicates: converters.py:1837 (`rotate==0 and w>h`) vs converters.py:1940 (`(rotate%180!=0) or (w>h)`); single `_page_orientation()` not implemented
  - Declarative gate table (zone-1): `validate_tree` still has 11 sequential `return TreeGateResult` early exits (helpers.py), preserving first-match-wins masking instead of exhaustive gate evaluation
  - `expected_script` defaults to `None` on `garble_prongs` (helpers.py:1212) — not keyword-REQUIRED as the zone-3 proposal specified

## Recommended Next Steps

Deletion-first cycle targeting the three critical zones. Zone-5 proved the pattern: typed abstractions only close a zone when the legacy mechanism is deleted, not supplemented. Priority order:

1. **Zone 1** — replace the 11 sequential early-return gates with a declarative gate table that evaluates ALL gates and reports every co-firing defect; delete the re-derivation path in `classify_verdict` for legacy string callers.
2. **Zone 3** — make `expected_script` keyword-required on `garble_prongs` (breaking change, fix all 13 call sites); delete `_detect_arabic_reversal`, `reconstruct_bidi_order`, `_tree_is_rtl_reversed`, `_check_bidi_coherence`; consolidate to `decide_rtl()` as sole decider.
3. **Zone 2** — collapse the all-or-nothing marker-count guard now that `bind_markers()` exists; delete the dead D3a probe.

Zones 4/6/7 (high severity) can follow once the three critical zones move. Zone 6 and 7 still need simplification proposals before any fix work begins.
