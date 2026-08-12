# Remediation Scorecard — POST (2026-08-12)

**Pre-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-11_RUN-2.md
**Post-fix audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12.md
**Delta report:** audit/ZONE_DELTA_2026-08-12_POST.md

## Verdict: NEEDS ANOTHER CYCLE

All 7 defect zones from the prior audit remain open with zero net bug movement (64 bugs pre, 64 bugs post; 0 improved, 0 regressed, 0 closed, 0 new). Wiring status is still partial: the Zone 5 type infrastructure (`REASON_POLICY`, `TreeDefect`, `HARD_FAIL_DEFECTS`) exists in `helpers.py` with passing tests but remains unimported by `client.py`, and several proposed symbols (`decide_route()`, `Route` StrEnum, `ExtractionSnapshot`, `first_defect`) were never created. This cycle produced no closures; the recommended path forward prioritizes wiring the already-built Zone 5 infrastructure as a low-effort win before tackling the larger Zone 1 gate-table and Zone 3 RTL-unification efforts.

## Zones Closed (0)

| Zone | Was Severity | Bugs Eliminated |
|---|---|---|
| — | — | — |

_No zones closed this cycle._

## Zones Remaining (7)

| Zone | Severity | Bug Count | Status |
|---|---|---|---|
| Zone 1: Verdict engine (11-gate first-match cascade + dual signal derivation) | critical | 12 | stalled |
| Zone 2: OCR escalation vs per-picture enrichment (marker-count contract) | critical | 11 | stalled |
| Zone 3: Six Arabic/RTL order deciders + 10-prong garble gate | critical | 9 | stalled |
| Zone 4: pdf_to_markdown_docling dual candidate pipelines | high | 9 | stalled |
| Zone 5: reason as diagnosis + routing command in index() | critical | 8 | stalled |
| Zone 6: Verdict persistence (five writers, lost-update sidecar merge) | high | 8 | stalled |
| Zone 7: Flag and threshold sprawl (~35 kill-switches) | high | 7 | stalled |

## New Zones (0)

| Zone | Severity | Introduced By |
|---|---|---|
| — | — | — |

_No new zones introduced this cycle._

## Metrics

- Net bug delta: 0
- Total bugs (current / prior): 64 / 64
- Improved: 0 · Regressed: 0 · Stalled: 7 · New: 0 · Closed: 0
- Wiring status: some_unwired
- Unwired symbols:
  - `REASON_POLICY` (helpers.py:109-121 — not imported by client.py)
  - `TreeDefect` StrEnum (helpers.py:63 — not referenced in client.py routing)
  - `HARD_FAIL_DEFECTS` (helpers.py:132-138 — derived from REASON_POLICY, also unwired)
  - `decide_route()` — proposed but never created
  - `Route` StrEnum — proposed but never created
  - `ExtractionSnapshot` dataclass — proposed but never created
  - `first_defect` field — proposed but never created

## Recommended Next Steps

All 7 zones stalled at 64 total bugs with zero net improvement. Three actions for next cycle, in priority order:

1. **WIRE ZONE 5** (quick win, ~1-2d): `TreeDefect`/`REASON_POLICY`/`HARD_FAIL_DEFECTS` already exist in `helpers.py` with exhaustiveness assertions and passing tests. Import them into `client.py:index()`, replace the literal string comparisons with `REASON_POLICY` lookups, create `decide_route()`. This is the lowest-effort highest-certainty improvement — the type infrastructure is built and tested, it just needs to be plugged in.

2. **ZONE 1 DECLARATIVE GATE TABLE** (highest bug count, ~3.5-4.5d): Convert `validate_tree`'s 11 early-return gates into an exhaustive rule table. `TreeSignals` dataclass already exists (helpers.py:203). The gate-11 dead code (`arabic_low_content_ratio` unreachable behind gate 1) should be removed as part of this. This directly addresses 12 bugs and unblocks Zone 5 wiring (`TreeGateResult` feeds `classify_verdict`).

3. **ZONE 3 RTL UNIFICATION** (persistent regression source, ~3.5-4.5d): Consolidate the six independent RTL deciders into one `decide_rtl()`. This zone has resisted 5+ remediation RFCs; architectural simplification is the only path forward. Sequence after Zone 1 since the garble gate table feeds RTL decisions.

Zones 6 and 7 have no simplification proposals yet — draft proposals before attempting fixes. Zone 2 proposal (picture_plane.py) is fully specified but not started; sequence after Zones 1/5 since OCR escalation interacts with the verdict engine.
