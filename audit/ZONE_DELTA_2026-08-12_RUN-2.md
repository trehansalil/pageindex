# Zone Delta Analysis — RUN-2

**Current audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-11_RUN-2.md
**Prior audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-11.md
**Date:** 2026-08-12

## Summary

Across the 7 tracked defect zones, total bug count rose from 62 to 64 (net +2). 3 zones regressed (verdict engine, OCR/enrichment split, reason-as-routing-command), 2 stalled with no net bug-count movement despite mechanism refinement (RTL/garble gate, dual pipeline in `pdf_to_markdown_docling`), and 2 improved (verdict persistence, flag/threshold sprawl). No zones opened or closed. The dominant pattern is proposals landing as *infrastructure* (typed enums, dataclasses, provenance tables, snapshot config) that then goes unwired at the call site that matters most — `REASON_POLICY` defined but never imported into `client.py`'s routing, `order_verdict` with no callers outside its own module, the dead gate 11 in `classify_verdict` still present after a full grouped-rule rewrite. Only one zone (`pdf_to_markdown_docling` dual pipelines) reached `implemented_and_wired`; three remain `partially_implemented`, two have `no_proposal`, and one is `not_implemented`.

## Delta Table

| Zone | Status | Severity (prior→current) | Bugs (prior→current) | Proposal Status | Key Change |
|---|---|---|---|---|---|
| Verdict engine (11-gate cascade + second re-deriving engine) | regressed | critical→critical | 11→12 | partially_implemented | Dead gate 11 confirmed still present; grouped rule table landed but REASON_POLICY unwired |
| OCR escalation vs per-picture enrichment | regressed | critical→critical | 10→11 | not_implemented | None of the four prior proposal items (exempt_when, pop protocol fix, landscape split, dedup hack removal) found in code |
| Arabic/RTL order deciders + garble gate | stalled | high→critical | 9→9 | partially_implemented | script.py leaf module landed but order_verdict/garble_prongs not wired into it; severity escalated |
| pdf_to_markdown_docling dual pipelines | stalled | high→high | 9→9 | implemented_and_wired | _run_stages/_build_candidate/body_for_containment all landed and used; function grew instead of shrinking |
| reason as diagnosis + routing command in index() | regressed | critical→critical | 6→8 | partially_implemented | TreeDefect/TreeGateResult/REASON_POLICY exist but REASON_POLICY unused in client.py routing (dead code) |
| Verdict persistence: five writers, lost-update sidecar | improved | high→high | 9→8 | no_proposal | Registry added as 5th writer; several persistence races resolved but recur on new docs |
| Flag/threshold sprawl (~35 kill-switches) | improved | high→high | 8→7 | no_proposal | effective_config_snapshot + BUILD_SHA landed but snapshot omits critical env vars |

## Per-Zone Details

### 1. Verdict engine: 11-gate first-match cascade + second re-deriving engine
**Status:** regressed (critical, 11→12 bugs)

Mechanism refined from "branch-ordering issues in a 15-branch cascade" to explicitly naming the dual-engine signal divergence between `validate_tree` and `classify_verdict`, plus the unwired `REASON_POLICY` as a structural problem rather than a mere ordering bug.

New findings: `node_count<3` masking garbling so OCR never escalates (ward 597); RFC-026 D5 had to reorder the cascade itself; a new `low_content_density` gate simultaneously failed three unrelated previously-PASS docs; RFC-023's threshold widening (0.17→0.20) missed Reitlehrer (0.2571), contradicting its "zero regressions" claim; RFC-025 hysteresis retune silently softened four zero-char Arabic docs from FAIL/ERROR to MARGINAL; a byte-identical tree (GHV-TKV-Tarif) flipped PASS→MARGINAL from the retune alone; RFC-014's threshold promotion let a doc with 67% severely RTL-garbled leaves reach PASS; RFC-026 D0/D1 shipped 5 self-inflicted test regressions; `image_enrichment_promoted` passed docs at 38-492 chars (Hard Rule 5 violation); the char floor checks volume not validity (ward 597 PASS on barcode noise); RFC-015 D2 left a 3.5-6x stored/recomputed `max_leaf_ratio` discrepancy unaddressed; and the dead gate 11 itself, confirmed present in this audit.

Resolved: RFC-024's empty-verdict-reason garbling leak; RFC-022 B1/B2 enrichment-rescue hoisting and bypass; RFC-036's depth-adequacy check preempting promotions.

Carried forward: four consecutive RFCs widening `PASS_MAX_LEAF_RATIO` for Docling jitter, reopening Hard Rule 5.

**Proposal status:** partially_implemented. `TreeSignals.from_tree` (helpers.py:160) and `VerdictThresholds.from_env` (helpers.py:129) landed with tests (test_zone2_classify_verdict.py), as did a grouped-rule `classify_verdict` (helpers.py:1677-1776, GROUP 1/GROUP 2/CAPS). Not done: gate 11 is still dead code in `validate_tree`; `classify_verdict` still returns `tuple[str,str]` instead of `TreeGateResult`; `REASON_POLICY` is defined but never imported/used in `client.py` routing.

### 2. OCR escalation vs per-picture enrichment
**Status:** regressed (critical, 10→11 bugs)

Mechanism reframed from an implicit i-th-marker-to-picture correspondence to a broader mutual-exclusion problem between page-level OCR and per-picture enrichment gated by a single `_OCR_ESCALATION` kill-switch, with the marker-count contract as one dimension of a larger coupling through helpers.py verdict rules.

New findings: per-picture OCR firing a second time during escalation (competing OCR passes); RFC-017 P0a/P0b filters killing legitimate enrichment for two docs; skips without `skipped_reason` leaving literal `<!-- image -->` in prose; OCR text moved to `block['ocr_text']`, invisible to `content_class`; the single kill-switch gating both mechanisms; a D1 probe added because sub-60%-coverage charts were being re-OCR'd at 300 DPI and garbled; a 4,267→375 char table-doc drop misdiagnosed through three hypotheses; RFC-035 regressing both `uae_numbers` orientations together; MAX_FULLPAGE cap firing before the exemption check so Docling's region enumeration order decides which pages get OCR'd.

Resolved: RFC-020 F2+D2 (forced-OCR reclassification hard-fail chain); RFC-020 F1 (zero enrichment from stacked filters); RFC-034 D19 (displacement fix was staged-but-uncommitted through Run 19).

Carried forward: RFC-017's tree-to-flat collapse for scanned Arabic PDFs; fabricated duplicate PictureResults to satisfy the count guard; RFC-035/RFC-036 landscape mismatch and filtering cascade.

**Proposal status:** not_implemented. None of the four prior-proposal items were found: no `exempt_when` symbol anywhere; destructive `pop('ocr_text')` still at converters.py:2554; fabricated landscape PictureResults still referenced at converters.py:2477,2518; landscape routing still not separated into its own return value.

### 3. Six Arabic/RTL order deciders + 10-prong garble gate via 13 call sites
**Status:** stalled (severity escalated high→critical, bugs flat at 9)

Mechanism quantified precisely: 10 prongs across 13 differently-shaped call sites (prior described "nine ORed prongs" and "six semantically distinct decisions"). `script.py` now exists as a key file. The intra-document direction disagreement and successful-repair-suppressing-escalation-detection are new characterizations of the same underlying defect class.

New findings: `expected_script` gap flip-flopping open/closed across 6+ runs, leaving ward 597 (60k chars Latin gibberish) stored PASS; siyasat hukuma stored PASS with 67% RTL-split leaves; a UN Human Rights doc PASS with 97% presentation-form glyphs; a fifth doc (Federal Decree-Law 13/2022) joining the same undetected class; `_check_bidi_coherence` at 0% detection while `BIDI_COHERENCE_ENFORCE` was promoted to default-true on a "zero violations = safe" misreading; a heading-order guard that was verified locally but never committed or deployed; a Run 18 RTL-gate tightening turning ward 597 from silent-garble MARGINAL into a hard blocking ERROR; and 5+ remediation RFCs (010, 015 D6-D9, 018 D2/D3, 026, 027) that never closed the class.

Resolved: RFC-015 D8's `_MIXED_SCRIPT_RE` ASCII-space bug (caught only manually despite 489/489 green tests); RFC-033 D2's 0% TPR instrument misread as a clean bill of health; a German FAIL→PASS flip on byte-identical input from `_script_from_filename` returning None for German.

Carried forward: ISS-36 duplicated >500-char digit-ratio floor across detectors; the space-separated Latin-gibberish recall gap surviving 4+ targeted RFCs.

**Proposal status:** partially_implemented. `script.py` exists (407 lines, 10 functions) and is imported by helpers.py/converters.py; `garble_prongs` decomposition exists but lives in helpers.py, not the leaf module it was proposed for; `order_verdict` is defined at script.py:277 but has no callers outside script.py itself; multiple Arabic range definitions still exist across converters.py and helpers.py (ISS-36 unresolved).

### 4. pdf_to_markdown_docling: dual candidate pipelines, stage ordering as line position
**Status:** stalled (high, bugs flat at 9)

Mechanism refined from a 280-line sequential mutation pipeline to a 330-line monolithic function — it grew rather than shrank — adding the finding that the selector heuristic is itself a divergent copy of the real gate.

New findings: cabinet_resolution_no_21 PASS→MARGINAL via flattened table headers (Run 18, commit c62ef80); Federal Decree-Law No.47 MARGINAL→FAIL with 88% body-less heading fragments; RFC-035 regressing landscape and portrait `uae_numbers` together (Run 19); RFC-026 D2 rotation applying only in the docling route; two landscape detectors with contradictory predicates (`rotate % 180 != 0 or w>h` vs `rotate == 0 and w>h`); the chunked route returning empty `extraction_stages` (no provenance for oversized PDFs); the landscape probe reading the original PDF while char counts come from the rotation-normalized temp copy.

Resolved: RFC-029 D3's fence toggle causing 89-100% content loss; RFC-034 D11's ToC filter collapsing Penal Code from 493 to 595 nodes (fixed in D16); RFC-033 D2's 0% TPR detector (root-caused in RFC-034 D6/D7); the RFC-027→028→029 heading-injection chain.

Carried forward: RFC-024 D1's fallback suppressing legitimate picture recovery; the two disagreeing landscape definitions, now more precisely characterized.

**Proposal status:** implemented_and_wired — the only zone reaching this status. `_run_stages` (converters.py:3171) with per-stage provenance recording is used at converters.py:3492,3508; `_build_candidate` (converters.py:3140) used at converters.py:3447,3448; `body_for_containment` parameter landed (converters.py:2588); `extraction_stages` reaches storage.py:570 `_MERGE_FIELDS`. Tests cover both (test_zone4_stage_table.py, test_zone4_containment_snapshot.py). Not completed: the landscape predicate rename, and the chunked route's empty `extraction_stages`.

### 5. reason as both diagnosis and routing command inside ~1,300-line index()
**Status:** regressed (critical, 6→8 bugs)

Mechanism confirmed and sharpened: a typed `TreeDefect` StrEnum and `REASON_POLICY` table now exist in helpers.py but are production-unwired in client.py — the shared type the prior audit called for now exists, is statically asserted, yet is dead code for actual routing, which arguably is a worse state than no shared type at all. Retry logic clobbering original defect state is a new mechanism dimension.

New findings: Run 18 ward 597 MARGINAL→blocking ERROR; Run 13 FAIL→ERROR; Run 19 phantom `image_enrichment_promoted` verdict_reason; Run 19 SLA doc MARGINAL→ERROR from a polling-window issue; a defeated garble-by-default protection; three recovery paths rewriting `garbling`/`rtl_reversal` reasons down to `node_count<3`; and the REASON_POLICY intended-vs-enacted divergence itself (four PERSIST_FAIL defects, with RETRY_RTL hardcoded at client.py:1475 instead of routed through the policy table).

Resolved: RFC-018 D3b→RFC-025 D3's discarded `node_garbling` return value; RFC-019 D2's unthreaded `expected_script`; RFC-027 D7's written-but-never-called timeout function (3 consecutive run failures before RFC-028 D0 fixed it); RFC-026 D5's garble check masked by structural early-exit.

Carried forward: RFC-029→030 D2's unhandled new reasons / six-variable divergence; RFC-034 D18→RFC-036 D1's unhandled `PersistenceNotVisibleError`.

**Proposal status:** partially_implemented. `TreeDefect` (helpers.py:63, 12 members), `TreeGateResult` (helpers.py:79) returned by `validate_tree`, `REASON_POLICY` (helpers.py:103) with a static exhaustiveness assertion (helpers.py:118), and `page_count` threading to all 5 `validate_tree` call sites in client.py all landed with tests (test_zone1_reason_enum.py). Not implemented: `REASON_POLICY` is not imported or used in client.py — routing still matches on string literals; no separate route variable in `index()`; no `ExtractionSnapshot` dataclass.

### 6. Verdict persistence: five writers, lost-update sidecar merge
**Status:** improved (high, 9→8 bugs)

Mechanism sharpened: the writer inventory now names five writers (registry.py added; client.py dropped, worker.py and preprocess_client.py added). The description moves from "whole-object overwrite" to explicitly naming the two-phase non-atomic write sequence and a queryability mismatch between filter and writer over where the verdict actually lives. Parameter-namespace conflation causing stored strings to misroute through classification is a new mechanism dimension.

New findings: Run 19 SLA doc MARGINAL→ERROR from barrier-delayed completion; the fabricated corpus report; Runs 15/16 mis-dispatched figures never fixed in the harness; a non-fatal dual-write swallow (job reports success with no registry row); `run_auto_backfill`'s complete-flag on zero failures combined with removed MinIO fallback producing `backfill_incomplete` on any migration failure; a three-writer (upload/worker/reaper) status hash with no governing state machine.

Resolved: RFC-025 hysteresis structurally dead for three RFCs (snapshot never called before MinIO wipe, fixed RFC-033 D0); RFC-033 D3's persistence race with read-retry; RFC-034 D18's write barrier (though it introduces its own new problem, tracked separately); the audit harness's `includes('error')` substring bug that defaulted all 24 docs to ERROR across Runs 7-9.

Carried forward: persistence races continuing to recur across runs (Runs 15/16/18/19) despite the D18 barrier; the Run 9 harness defaulting docs to ERROR while live metas held real verdicts; RFC-036 D1's barrier causing an ERROR-despite-PASS outcome.

**Proposal status:** no_proposal. Neither run produced a simplification proposal for this zone.

### 7. Flag and threshold sprawl: ~35 never-retired kill-switches
**Status:** improved (high, 8→7 bugs)

Scope narrowed from broad state-vs-code skew (dark flags, stale deploys, stale docstrings, unscrapable metrics) to specifically the binding-time mechanics of ~35 never-retired kill-switches operating at three different lifetimes (import constants, per-call environ reads, snapshots). Stale-deploy/docstring/auth.py material from the prior zone is not re-surfaced. `effective_config_snapshot`'s incompleteness is a new finding reflecting the partial state of the prior zone's observability proposal.

New findings: a dead D3a probe when `ALLOW_AGPL_FALLBACK` is false; the PDF_INSPECTOR Phase-2 report showing no file imports or branches on the `PDF_INSPECTOR_PRECLASSIFY` flag; the duplicated `_OCR_ESCALATION` constant (verbatim at converters.py:1581 and client.py:344); Run-13/Run-9 single-doc-calibrated thresholds applied corpus-wide; untestable 6-flag recovery-ladder combinatorics; `effective_config_snapshot` omitting critical env vars (`PDF_CONVERTER`, `FLAT_DOC_ROUTING`, `TREE_PATH_PICTURE_SPLICE_ENABLED`, `RFC029_*`, `LOW_CONTENT_OCR_CHAR_FLOOR`, `BIDI_COHERENCE_ENFORCE`).

Resolved: `REGION_AWARE_TEXT_CHECK_ENABLED` routing around the dormant `_TEXT_LAYER_GARBLE_CHECK_ENABLED` (RFC-023 D0); `probe_conversion_route`'s docstring claiming shadow mode never influences routing while actually forcing OCR and multiplying timeouts 16.5x; `PRE_GARBLE_FORCE_OCR_ENABLED` defaulting false, running, logging, and being discarded; the worker Prometheus registry never being scraped; RFC-033 fixes being validated against a Docling build that lacked them (2026-07-30 through 2026-08-07); RFC-034 D19 staged-but-uncommitted through Run 19; `ensure_tessdata` silently substituting deu/eng for missing Arabic traineddata (ISS-34).

Carried forward: ISS-35 silent AGPL fallthrough — `ALLOW_AGPL_FALLBACK=false` disabling six orthogonal features via a single flag.

**Proposal status:** no_proposal. Neither run produced a formal simplification proposal; the prior zone's recommendation was a sequencing step (persist effective flags + build sha into the sidecar), not a structural proposal. `effective_config_snapshot` and `BUILD_SHA` (config.py:258, worker.py `_WORKER_BUILD_SHA`) were implemented, but this audit flags the snapshot as incomplete.

## New Zones

None.

## Closed Zones

None.
