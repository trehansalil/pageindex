# Remediation Scorecard — POST-FIX (2026-08-12)

**Pre-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-11_RUN-2.md
**Post-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12.md
**Delta report:** audit/ZONE_DELTA_2026-08-12_POST-FIX.md

## Verdict: NEEDS ANOTHER CYCLE

All seven defect zones remain open with zero net bug reduction (64 → 64). The zone-1 through zone-7 fix commits landed correct, well-typed abstractions — `TreeDefect`, `Route`, `OcrMode`, `Candidate`, `decide_rtl()`, `write_verdict()` — but in every case the legacy mechanism the new type was meant to replace is still live and still on the execution path. The new code supplements rather than supersedes, so no bug was actually eliminated: `REASON_POLICY` is defined and exhaustiveness-asserted but production routing still branches on the literal `reason=='garbling'`; `decide_rtl()` coexists with two duplicate RTL samplers; two contradictory landscape predicates remain unreconciled; `write_verdict()`'s atomic dual-write does not replace the five independent `save_doc_meta` call sites it was meant to retire. The recommended next cycle is deletion-first, not addition-first.

## Zones Closed (0)

| Zone | Was Severity | Bugs Eliminated |
| ---- | ------------ | --------------- |
| —   | —           | —              |

None closed this cycle.

## Zones Remaining (7)

| Zone                                                                     | Severity | Bug Count | Status  |
| ------------------------------------------------------------------------ | -------- | --------- | ------- |
| Zone 1: Verdict engine (11-gate cascade + dual derivation)               | critical | 12        | stalled |
| Zone 2: OCR escalation vs per-picture enrichment (marker-count contract) | critical | 11        | stalled |
| Zone 3: Arabic/RTL (six deciders + 10-prong garble gate x 13 call sites) | critical | 9         | stalled |
| Zone 4: pdf_to_markdown_docling (dual pipelines + positional stages)     | high     | 9         | stalled |
| Zone 5: reason as diagnosis+routing inside index()                       | critical | 8         | stalled |
| Zone 6: Verdict persistence (five writers, no CAS, sidecar-only)         | high     | 8         | stalled |
| Zone 7: Flag/threshold sprawl (~35 kill-switches)                        | high     | 7         | stalled |

## New Zones (0)

| Zone | Severity | Introduced By |
| ---- | -------- | ------------- |
| —   | —       | —            |

None introduced this cycle. No red flag.

## Metrics

- Net bug delta: 0 (64 prior → 64 current)
- Improved: 0
- Regressed: 0
- Stalled: 7
- New: 0
- Closed: 0
- Wiring status: some_unwired
- Unwired symbols:
  - `REASON_POLICY` (helpers.py:182) — exhaustiveness-asserted but production routing still uses literal `reason=='garbling'` at client.py:2093-2094; `decide_route()` called but `original_reason` clobbered at 5 sites (client.py:1261,1360,1507,1598,1723) defeating garble-by-default
  - `_is_garbled_blob` at helpers.py:1942 — `image_enrichment_promoted` garble check called without `expected_script`, unconditionally disabling the `latin_gibberish` prong
  - `_tree_is_rtl_reversed` (helpers.py:1485) and `_check_bidi_coherence` (helpers.py:1344) — duplicate RTL samplers survive alongside `decide_rtl()`; six orientation deciders not consolidated to one
  - Two contradictory landscape predicates — converters.py:1837 (`rotate==0 and w>h`) vs converters.py:1940 (`(rotate%180!=0) or (w>h)`); single `_page_orientation()` not implemented
  - `bind_markers` (picture_plane.py) — exists and imported but old all-or-nothing marker-count bail path coexists with `landscape_fallback_picture` literal filter at converters.py:2475
  - `save_doc_meta` — still called from 5+ independent sites (storage.py:287/728, registry_backfill.py:202/358, worker.py) with read-merge-write; `write_verdict` (storage.py:644) added but does not replace the multi-writer pattern
  - `effective_config_snapshot` (config.py:264) — still omits `FLAT_DOC_ROUTING`, `TREE_PATH_PICTURE_SPLICE_ENABLED`, `RFC029_*`, `LOW_CONTENT_OCR_CHAR_FLOOR`, `BIDI_COHERENCE_ENFORCE` per delta

## Recommended Next Steps

All seven zones stalled at 0 net delta. The fix commits (zone-1 through zone-7) added correct typed abstractions (TreeDefect, Route, OcrMode, Candidate, decide_rtl, write_verdict) but left the OLD mechanisms live beside them -- the new types supplement rather than replace, so no bugs were eliminated. Next cycle should focus on DELETION of the old paths rather than addition of new ones, prioritized by severity:

1. **Zone 5** — delete `original_reason`, replace remaining `reason=='garbling'` literals with `Route`/`first_defect` checks, wire `REASON_POLICY` into actual routing.
2. **Zone 3** — delete `_tree_is_rtl_reversed` and `_check_bidi_coherence`, consolidate to `decide_rtl`, add required `expected_script` to helpers.py:1942.
3. **Zone 1** — convert 11 if/return blocks to a declarative gate table, delete the legacy re-derivation path at helpers.py:1893.
4. **Zone 2** — delete the all-or-nothing marker-count bail, remove the `landscape_fallback_picture` literal filter.
5. **Zone 4** — unify the two landscape predicates into a single `_page_orientation()`.

Zones 6 and 7 need proposals before fixes can start. Each zone fix should be followed by an immediate wiring verification (grep for the deleted symbol to confirm zero references) before moving to the next zone.
