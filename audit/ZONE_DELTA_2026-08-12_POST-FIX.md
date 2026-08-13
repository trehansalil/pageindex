# Zone Delta Analysis — POST-FIX

**Current audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12.md
**Prior audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-11_RUN-2.md
**Date:** 2026-08-12

## Summary

All 7 defect zones remain **stalled**: total bug count is unchanged at 64 (prior 64 → current 64), with zero zones improved, zero regressed, zero newly opened, and zero closed. Five recent commits (zone-2 through zone-7, `feat(zone-N)` prefix) landed real scaffolding — `TreeDefect`/`TreeGateResult`/`REASON_POLICY` in `helpers.py`, `picture_plane.py`'s `OcrMode`/`SkipReason`/`decide_ocr_mode`, `decide_rtl`/`apply_rtl`/`BlobKind` in `script.py`, `Candidate`/`_run_stages()` in `converters.py`, `Route`/`decide_route()`/`ExtractionSnapshot` in `client.py`/`helpers.py`, and `write_verdict` atomic dual-write with a CAS guard — but in every zone the new abstraction sits **alongside**, not **instead of**, the legacy mechanism it was meant to retire. The old first-match cascades, dual deciders, positional stage ordering, five independent verdict writers, and flag sprawl all remain live and reachable, so none of the carried-forward findings actually resolved. Two zones (6 and 7) never had a simplification proposal to begin with and got infrastructure commits without the audit's bug count moving.

## Delta Table

| Zone | Status | Severity (prior→current) | Bugs (prior→current) | Proposal Status | Key Change |
|---|---|---|---|---|---|
| Zone 1: Verdict engine (11-gate cascade + dual re-derivation) | stalled | critical → critical | 12 → 12 | partially_implemented | `TreeGateResult`/`REASON_POLICY` landed, dead gate 11 removed from cascade; 11 early-return if/blocks and legacy re-derivation path still present |
| Zone 2: OCR escalation vs per-picture enrichment | stalled | critical → critical | 11 → 11 | partially_implemented | `picture_plane.py` (OcrMode, SkipReason, decide_ocr_mode, bind_markers) wired into `client.py`; all-or-nothing marker-count guard and dead D3a probe still present |
| Zone 3: Six RTL deciders + 10-prong garble gate | stalled | critical → critical | 9 → 9 | partially_implemented | `decide_rtl()`/`apply_rtl()`/`BlobKind` landed and called; some garble sites now pass `expected_script`, but multiple deciders and the ungated `helpers.py:1942` call site remain |
| Zone 4: pdf_to_markdown_docling dual pipelines | stalled | high → high | 9 → 9 | partially_implemented | `Candidate` dataclass + `_run_stages()` landed with tests; `_has_recoverable_structure` selector, dual landscape detectors, and empty chunked-route provenance remain |
| Zone 5: `reason` as diagnosis + routing command | stalled | critical → critical | 8 → 8 | partially_implemented | `Route`/`decide_route()`/`ExtractionSnapshot`/write-once `first_defect` landed with tests; `original_reason` still assigned/clobbered at 5 call sites, recovery branches still forge `node_count<3` |
| Zone 6: Verdict persistence (5 writers, lost-update) | stalled | high → high | 8 → 8 | no_proposal | `write_verdict` atomic dual-write + CAS guard added; still 5 independent writers, no shared entry point, verdict still apart from artifact |
| Zone 7: Flag/threshold sprawl (~35 kill-switches) | stalled | high → high | 7 → 7 | no_proposal | `LEAF_SPLIT_RATIO` decoupled, region garble check + snapshot completeness + `inspector_class` guard added; ~35 flags at 3 binding times, fail-open degrade mode unchanged |

## Per-Zone Details

### Zone 1: Verdict engine — 11-gate first-match cascade + second engine re-deriving signals

**What changed:** No material change to the mechanism. `TreeDefect` StrEnum (`helpers.py:67`), `TreeGateResult` dataclass (`helpers.py:83`), and `REASON_POLICY` with an import-time exhaustiveness assert (`helpers.py:182-198`) now exist in production, and `validate_tree` returns `TreeGateResult`. `classify_verdict` accepts `TreeGateResult` and reuses its signals (`helpers.py:1881`) for callers that pass it. Dead gate 11 (`arabic_low_content_ratio`) was removed from the cascade (`helpers.py:1518-1632`), though the enum member is retained for backward compatibility.

**New findings:** None.

**Resolved findings:** None — all 12 prior findings carry forward, including node_count<3 masking garbling (ward 597), the dual-derivation gap for legacy string callers, and the D10/RFC-025/RFC-026 threshold-calibration regressions.

**Proposal implementation status:** Partially implemented. Still missing: a declarative gate table (11 early-return if/return blocks remain), full single-derivation `TreeSignals` for legacy string callers (`helpers.py:1893` still re-derives), threshold folding into `VerdictThresholds` (still per-call env reads), and per-threshold sensitivity sweeps.

### Zone 2: OCR escalation vs per-picture enrichment — fragile marker-count contract

**What changed:** No material change to the mechanism. `picture_plane.py` module was created with `OcrMode` StrEnum (NONE/FULL_PAGE/PER_PICTURE), `SkipReason` StrEnum with a `counts_in_denominator` policy, `PictureRegion` dataclass, `decide_ocr_mode()`, and `bind_markers()` — all imported and called from `client.py` (lines 46-50, 1002, 1027, 1043, 1785-1786). `_OCR_ESCALATION` moved to `config.py` as the canonical source (`converters.py:1543`, `client.py:22`). The destructive `pop('ocr_text')` mutation was replaced with a `spliced_into_markdown` flag (`converters.py:2516`).

**New findings:** None.

**Resolved findings:** None — all 11 prior findings carry forward, including the 5-step Arabic cascade, D0's fabricated duplicate `PictureResults`, and the MAX_FULLPAGE cap ordering bug.

**Proposal implementation status:** Partially implemented. Still missing: full deletion of the all-or-nothing count guard (`'landscape_fallback_picture'` literal filter still at `converters.py:2475`), elimination of the dead D3a probe, and per-marker splice binding as the sole path (the whole-document marker-count bail path still present alongside `bind_markers`).

### Zone 3: Six Arabic/RTL order deciders + 10-prong garble gate via 13 call sites

**What changed:** No material change to the mechanism. `decide_rtl()` returning `RtlDecision` (`script.py:454`), `apply_rtl()` single-pass (`script.py:518`), `BlobKind` StrEnum with RAW_MARKDOWN/TREE_TEXT (`script.py:419`), and `normalize_for_garble(blob, kind)` (`script.py:429`) all exist in production. `decide_rtl` is called from `client.py:1542-1544` and `decider_version` is stamped into meta at `client.py:2184`. Some garble call sites now pass `expected_script=infer_script(text)` with `blob_kind=BlobKind.TREE_TEXT` (`converters.py:1709, 1808`). The 0%-TPR presentation-form check was replaced by a null-detector (`_word_has_reversed_morphology`).

**New findings:** None.

**Resolved findings:** None — all 9 prior findings carry forward, including ward 597's flip-flopping expected_script gap, siyasat hawkama's 67% RTL-split leaves stored PASS, and 5+ remediation RFCs never closing the class.

**Proposal implementation status:** Partially implemented. Still missing: full consolidation of six deciders to one, required `expected_script` on `garble_prongs` (`helpers.py:1942` still calls `_is_garbled_blob` without it — the image_enrichment_promoted rescue path), an ISS-36 shared digit-floor constant, and deletion of the duplicate `_tree_is_rtl_reversed`/`_check_bidi_coherence` samplers.

### Zone 4: pdf_to_markdown_docling — dual candidate pipelines, stage ordering as line positions

**What changed:** No material change to the mechanism. `Candidate` frozen dataclass (`converters.py:3141`), `_candidate_from_document()` entry point (`converters.py:872`, called at 3435-3436 to keep md and heading-page map from drifting), and `_run_stages()` name-keyed stage runner (`converters.py:3149`, called at 3480 and 3496, recording per-stage provenance) all landed with tests (`test_zone4_stage_table.py`, `test_zone4_dual_pipeline_refactor.py`).

**New findings:** None.

**Resolved findings:** None — all 9 prior findings carry forward, including RFC-024 D1's fallback suppressing picture recovery, the two contradictory landscape detectors, and the chunked route's empty `extraction_stages`.

**Proposal implementation status:** Partially implemented. Still missing: deletion of `_has_recoverable_structure` (selector still divergent from real `validate_tree` gates), a single `_page_orientation()` predicate (two contradictory landscape detectors remain), hoisted rotation normalization for the pymupdf4llm route, `body_for_containment` as a named stage dependency (still positional), and chunked-route provenance accumulation (still returns empty `extraction_stages`).

### Zone 5: `reason` as both diagnosis and routing command inside ~1,300-line `index()`

**What changed:** No material change to the mechanism. `Route` StrEnum with TREE/FLAT/REJECT/PERSIST_FAIL (`helpers.py:220`), `decide_route()` performing an exhaustive `REASON_POLICY` lookup (`helpers.py:227`, called at `client.py:1260`), `ExtractionSnapshot` frozen dataclass (`helpers.py:105`, used at `client.py:1292` and `1465`), and `first_defect` typed as `TreeDefect` (`client.py:1254`, write-once, covered by a static-analysis test at `test_zone5_first_defect_immutable.py`) all landed, with additional contract tests (`test_zone5_decide_route_integration.py`, `test_zone5_route_enum.py`).

**New findings:** None.

**Resolved findings:** None — all 8 prior findings carry forward, including ward 597's MARGINAL→blocking ERROR transition and the phantom `image_enrichment_promoted` verdict_reason.

**Proposal implementation status:** Partially implemented. Still missing: deletion of `original_reason` (still assigned at `client.py:1261, 1360, 1507, 1598, 1723` — clobbered across retries exactly as before), full removal of hand-maintained literal-string tuples (`reason` string still used alongside `first_defect`), and `route=Route.FLAT` for flat-prefer/landscape re-routes without forging fake defects (recovery branches still rewrite `reason` to `'node_count<3'`).

### Zone 6: Verdict persistence — five writers, lost-update sidecar merge, verdict apart from artifact

**What changed:** No material change to the mechanism. The recent `feat(zone-6)` commit added `write_verdict` with an atomic dual-write and a CAS guard.

**New findings:** None.

**Resolved findings:** None — all 8 prior findings carry forward, including the persistence races recurring across Runs 15/16/18/19, the Run 9 harness defaulting 24/24 docs to ERROR against live metas holding real verdicts, and the non-fatal dual-write swallow.

**Proposal implementation status:** No proposal exists for this zone (Run-2 gave simplification proposals only to the top-5 zones). The Run-1 reference direction — single-writer-per-field discipline, a field-ownership map, and a symmetric barrier — remains unimplemented. `write_verdict`'s CAS guard did not reduce the writer count from five, and the audit still reports the same 8 bugs.

### Zone 7: Flag and threshold sprawl — ~35 never-retired kill-switches with divergent binding times

**What changed:** No material change to the mechanism. The recent `feat(zone-7)` commit decoupled `LEAF_SPLIT_RATIO`, added a region garble check, snapshot completeness, and an `inspector_class` guard.

**New findings:** None.

**Resolved findings:** None — all 7 prior findings carry forward, including ISS-35's silent AGPL fallthrough, the dead D3a probe when `ALLOW_AGPL_FALLBACK=false`, and `effective_config_snapshot` still omitting `PDF_CONVERTER`, `FLAT_DOC_ROUTING`, `TREE_PATH_PICTURE_SPLICE_ENABLED`, `RFC029_*`, `LOW_CONTENT_OCR_CHAR_FLOOR`, and `BIDI_COHERENCE_ENFORCE`.

**Proposal implementation status:** No proposal exists for this zone (Run-2 gave simplification proposals only to the top-5 zones). The Run-1 reference direction — flag inventory persisted into the sidecar, build sha threaded end-to-end, dead-metric scrub — remains unimplemented. The zone-7 commit added guards without retiring any of the ~35 flags, and the audit still reports the same 7 bugs.

## New Zones

None.

## Closed Zones

None.
