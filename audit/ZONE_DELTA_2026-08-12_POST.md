# Zone Delta Analysis — POST

**Current audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12.md
**Prior audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-11_RUN-2.md
**Date:** 2026-08-12

## Summary

All 7 architecture defect zones from the 2026-08-11 RUN-2 audit remain **stalled** as of 2026-08-12: total bug count is unchanged at 64 (0 improved, 0 regressed, 0 new, 0 closed; net bug delta 0). No zone's core mechanism changed — findings across all zones were only reworded, condensed, or annotated with observation-ID references (e.g. `#5327`, `#5467`, `#3977`), with no findings resolved and no new defects surfacing. Of the 7 zones, 3 show partial or unwired implementation progress against their prior remediation proposals (Zone 1: `TreeSignals`/`TreeGateResult` refactor landed but the 11-gate first-match cascade persists; Zone 4: `StageRecord` provenance table landed but the dual-pipeline candidate builder was not unified; Zone 5: `TreeDefect`/`REASON_POLICY` fully built and tested but never imported by `client.py`, leaving production routing on literal string comparisons), while Zones 2, 3, 6, and 7 show no implementation movement at all (Zones 6 and 7 still lack any simplification proposal). In short: this cycle produced documentation/observability groundwork in three zones without moving a single zone's severity, bug count, or underlying mechanism.

## Delta Table

| Zone | Status | Severity (prior→current) | Bugs (prior→current) | Proposal Status | Key Change |
|---|---|---|---|---|---|
| Zone 1: Verdict engine (11-gate cascade + dual derivation) | stalled | critical→critical | 12→12 | partially_implemented | `TreeSignals`/`TreeGateResult`/grouped rule table landed; 11-gate first-match-wins cascade still present |
| Zone 2: OCR escalation vs per-picture enrichment | stalled | critical→critical | 11→11 | not_implemented | No proposal artifacts found in code; wording-only refinements |
| Zone 3: Arabic/RTL order deciders + garble gate | stalled | critical→critical | 9→9 | not_implemented | No `RtlDecision`/`decide_rtl()`/`BlobKind` found; six deciders remain unmerged |
| Zone 4: `pdf_to_markdown_docling` dual pipelines | stalled | high→high | 9→9 | partially_implemented | `_run_stages`/`StageRecord` provenance landed and tested; candidate/orientation unification not done |
| Zone 5: `reason` as diagnosis + routing command | stalled | critical→critical | 8→8 | implemented_not_wired | `TreeDefect`/`REASON_POLICY` built and tested in `helpers.py`; `client.py` never imports it |
| Zone 6: Verdict persistence (5 writers, sidecar merge) | stalled | high→high | 8→8 | no_proposal | Line-number references added to findings; no mechanism change |
| Zone 7: Flag/threshold sprawl (~35 kill-switches) | stalled | high→high | 7→7 | no_proposal | Minor wording only ("shadow mode is not actually shadow") |

## Per-Zone Details

### Zone 1 — Verdict engine: 11-gate first-match cascade + second engine re-deriving the same signals
**Files:** `helpers.py`, `client.py`, `converters.py`
**What changed:** Mechanism description reformulated for clarity; `key_files` expanded from `[helpers.py]` to include `client.py` and `converters.py`, tracing the zone's blast radius further into the codebase. Core mechanism (first-match-wins cascade, dual signal derivation, unreachable gate 11) is unchanged.
**New findings:** RFC-023's "zero regressions" claim is contradicted by the D10 0.17→0.20 threshold widening missing Reitlehrer (score 0.2571) — this was a carried-forward finding that gained an observation-ID citation this run. Several other findings gained `obs` references (e.g. `#5327`/`#5467`, `#3977`/`#5627`, `#5356`/`#5397`, `#4127`/`#4196`) without new substantive content.
**Resolved findings:** None.
**Proposal implementation status — partially_implemented:** `TreeSignals` dataclass exists (`helpers.py:203`), computed once per `validate_tree` call (`helpers.py:1450-1453`); `TreeGateResult` wraps it (`helpers.py:79`); `classify_verdict` now accepts `TreeGateResult` and uses grouped rules (HARD_FAILs, PROMOTIONs, CAPs) instead of pure re-derivation. However, `validate_tree` still contains 11 early-return `TreeGateResult` statements — the declarative, exhaustively-evaluated rule table proposed was never implemented. The first-match-wins cascade pattern persists in production.

### Zone 2 — OCR escalation vs per-picture enrichment: fragile marker-count contract
**Files:** `converters.py`, `client.py`, `helpers.py`
**What changed:** Wording-only refinements ("Both OCR passes compete" qualifier added; "garbling numbers" phrasing refined; "role=table blocks carry no text key" detail added to the table-doc drop finding).
**New findings:** None substantive.
**Resolved findings:** None.
**Proposal implementation status — not_implemented:** No `picture_plane.py`, `SkipReason` StrEnum, `PictureRegion` dataclass, `bind_markers()`, `decide_ocr_mode()`, or `OcrMode` found in production code. A `TestF5SkipReason` class exists in `test_rfc020_f1f5_coverage.py` but tests a different, unrelated concept. The four force-OCR branches and the destructive `pop('ocr_text')` shared-mutation pattern remain in place.

### Zone 3 — Six Arabic/RTL order deciders + 10-prong garble gate via 13 call sites
**Files:** `converters.py`, `helpers.py`, `script.py`
**What changed:** Mechanism description condensed but unchanged in substance — six independent orientation decisions, four sampling strategies, five thresholds, successful bidi repair suppressing escalation, prong semantics shifting per caller.
**New findings:** None.
**Resolved findings:** None.
**Proposal implementation status — not_implemented:** No `RtlDecision`, `decide_rtl()`, `BlobKind` StrEnum, or `decider_version` found anywhere in production or test code. `converters.py` still has `_text_is_logical_order` (line 1442) and `_heading_is_logical_order` (line 1473) as separate deciders. `garble_prongs` still accepts `expected_script` as optional (`str | None`) rather than keyword-required. `order_verdict` still exists at `script.py:277`. The six deciders remain unmerged.

### Zone 4 — `pdf_to_markdown_docling`: dual candidate pipelines, stage ordering as line positions
**Files:** `converters.py`
**What changed:** Mechanism description identical in substance; minor wording shift ("third-party monkeypatch" vs "third-party monkeypatch and postprocessor").
**New findings:** None.
**Resolved findings:** None.
**Proposal implementation status — partially_implemented:** `_run_stages` (`converters.py:3171`) and `StageRecord` dataclass (`converters.py:3158`) are implemented in production and covered by tests (`test_zone4_stage_table.py`). However, no `Candidate` frozen dataclass, no `_candidate_from_document()`, and no `_page_orientation()` replacing the two contradictory landscape predicates exist. `_has_recoverable_structure` still exists at `converters.py:839` despite the proposal calling for its deletion. Stage-table provenance recording landed; the core structural simplification (single candidate builder, single orientation function) did not.

### Zone 5 — `reason` as both diagnosis and routing command inside `index()`
**Files:** `client.py`, `helpers.py`
**What changed:** Mechanism description reformulated to explicitly name the `TreeDefect` StrEnum and `REASON_POLICY` table as existing but production-unwired. Same substance as prior run otherwise.
**New findings:** None.
**Resolved findings:** None.
**Proposal implementation status — implemented_not_wired:** `TreeDefect` StrEnum (`helpers.py:63`) and `REASON_POLICY` table (`helpers.py:109-121`) with an exhaustiveness assertion (`helpers.py:124-125`) exist in production `helpers.py`; `HARD_FAIL_DEFECTS` frozenset (`helpers.py:132-138`) is derived from it. Tests exercise exhaustiveness and backward compatibility (`test_zone1_reason_enum.py`). However, `client.py` does **not** import or reference `REASON_POLICY` — production routing still relies on literal string comparisons. No `decide_route()`, `Route` StrEnum, `ExtractionSnapshot` dataclass, or `first_defect` field exist anywhere. The typed infrastructure is fully built and tested but completely disconnected from `client.py`'s `index()` routing logic.

### Zone 6 — Verdict persistence: five writers, lost-update sidecar merge
**Files:** `storage.py`, `registry.py`, `registry_backfill.py`, `worker.py`, `promotion_sweep.py`, `preprocess_client.py`
**What changed:** Wording refinements only — line-number references added to the upload/worker/reaper finding (`upload_app.py:167-177`, `worker.py:446`, `worker.py:640-650`).
**New findings:** None.
**Resolved findings:** None.
**Proposal implementation status — no_proposal:** Neither the prior nor current run includes a simplification proposal for this zone.

### Zone 7 — Flag and threshold sprawl: ~35 never-retired kill-switches
**Files:** `config.py`, `converters.py`, `client.py`, `helpers.py`, `worker.py`
**What changed:** Minor wording refinement ("shadow mode is not actually shadow" phrasing added).
**New findings:** None.
**Resolved findings:** None.
**Proposal implementation status — no_proposal:** Neither the prior nor current run includes a simplification proposal for this zone.

## New Zones

None.

## Closed Zones

None.
