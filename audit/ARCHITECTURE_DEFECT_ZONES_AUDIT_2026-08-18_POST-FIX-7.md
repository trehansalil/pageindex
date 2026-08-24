# Architecture Defect Zones Audit — 2026-08-18 POST-FIX-7

**Date:** 2026-08-18
**Sources:** 8 history miners, 2 code maps

## Summary Table

| # | Zone | Severity | Bug Count | Key Files |
|---|---|---|---|---|
| 1 | Garble Detection Surface Fragmentation | critical | 12 | `helpers.py`, `converters.py`, `script.py` |
| 2 | OCR Recovery Pipeline Flag Conflation and Mutable State Ordering | critical | 11 | `client.py`, `converters.py`, `config.py` |
| 3 | Three-Layer Verdict Pipeline Implicit GATE_TABLE Coupling | critical | 10 | `helpers.py`, `client.py` |
| 4 | Dual-Store Verdict Consistency and Persistence Timing | high | 9 | `storage.py`, `registry.py`, `worker.py`, `registry_backfill.py` |
| 5 | Dead Code and Incomplete Wiring Enforcement Gap | high | 7 | `helpers.py`, `client.py`, `worker.py`, `config.py` |
| 6 | Content-Destructive Heuristics Without Safety Bounds | critical | 7 | `helpers.py`, `converters.py`, `client.py` |

## Zone Details

### Zone 1: Garble Detection Surface Fragmentation

**Severity:** critical | **Bug count:** 12

#### Mechanism

The garble gate is a single consolidated entry point (`check_garble`, 114 callers) that always runs BOTH `garble_prongs` AND `_has_sparse_mojibake` in additive-OR. But `garble_prongs` internally dispatches to 8+ independent heuristic prongs, each with its own Unicode range assumptions, threshold constants, and `expected_script` dependency. When a fix narrows one prong's false-positive rate (e.g., raising Arabic Presentation Forms threshold from 0% to 50%), it leaves adjacent prongs unchanged, and the underlying corruption signal re-emerges through whichever prong is blind to that particular encoding. The NFKC normalization at `converters.py:2357` is the root amplifier: it runs BEFORE any garble check, decomposing Presentation Forms that multiple downstream detectors explicitly check for. Every detector written assuming U+FB50-FEFF survival is silently nulled. The `expected_script` bootstrapping problem compounds this: callers infer script from the text being checked rather than from filename/metadata context, so corrupted text self-reports as the wrong script and the detection surface disables itself.

#### History

a. RFC-028 D2 (Arabic Presentation Forms >50% threshold) → false rejection of Human Rights PDF Run12.
b. RFC-033 D1 garble ratio nulled by upstream NFKC decomposition.
c. RFC-033 D2 `_reversed_morphology` returns False for virtually every word (0% TPR) because it checks U+FB50-FEFF which no longer exists post-NFKC.
d. `_check_bidi_coherence` line selector at `helpers.py:1029` scans U+0600-06FF only, discarding U+FB50-FEFF lines carrying the reversal signal.
e. Haftpflicht 61% garbled tree flipped FAIL→PASS in Run9 (4 interconnected bugs: `_script_from_filename` returns None, `latin_gibberish` requires non-None `expected_script`, `classify_verdict` hardcodes None).
f. وارد 597 saga across Runs 6-11 (garble gate hole, detection without remediation).
g. Latin-in-Arabic mojibake undetected Runs 16-19 (`expected_script` inferred from corrupted text returns 'Latn').
h. سياسة حوكمة 100% reversed node titles stored PASS Run10.
i. D6 rotation correction: Arabic titles character-reversed PASS.
j. `ensure_tessdata` silent fallback to deu/eng when ara unavailable, producing Latin mojibake that passes garble gate (ISS-34).
k. Run8 `expected_script` lost from `_is_garbled_blob` (81/132 nodes garbled PyPDF2).
l. D2 Part B: `expected_script` gate never fires on already-garbled text because inference returns 'Latn'.

#### Code Evidence

`helpers.py:1397-1440` `check_garble`: consolidated entry point, always runs BOTH `garble_prongs()` OR `_has_sparse_mojibake()` — single API but delegates to fragmented detection surfaces. `helpers.py:1283-1368` `garble_prongs`: latin_gibberish prong (lines 1355-1368) gated by `_effective_script is not None and _effective_script != 'Latn'` — None or 'Latn' silently disables entire prong. `helpers.py:1321-1328` presentation_forms prong: checks `any(lo <= ord(c) <= hi for lo, hi in PRESENTATION_RANGES)` which is U+FB50-FEFF range that NFKC decomposes before this code runs. `converters.py:1761-1787` `_text_layer_has_content`: thin wrapper importing `check_garble` with function-scoped import, duplicating pattern `check_garble(text, expected_script=expected_script or infer_script(text), profile=BULK_PROFILE)`.

#### Key Files

- `src/pageindex_mcp/helpers.py`
- `src/pageindex_mcp/converters.py`
- `src/pageindex_mcp/script.py`

#### Simplification Proposal

Now I have a clear picture of the architecture. Let me trace the key structural issues.

**(1) Core Simplification**

Move garble detection to a two-phase architecture: (A) a metadata-authoritative `expected_script` resolved ONCE at the job entry point from filename/OCR-language metadata (never from the text being checked), threaded as a non-optional parameter through the entire pipeline; (B) a single `GarbleResult` dataclass returned by `check_garble` that carries which prongs fired plus the pre-NFKC presentation-forms signal (already captured as `had_presentation_forms` on `RtlDecision`), so the presentation_forms prong operates on the pre-NFKC boolean rather than re-scanning codepoints that NFKC already destroyed. The `_has_sparse_mojibake` regex check folds into `garble_prongs` as just another prong rather than a parallel OR surface.

**(2) Concrete Restructuring Steps**

Step 1 — Authoritative script source (client.py, ~+15/-25 lines). At the top of `process_pdf` (the job entry point in client.py), resolve `expected_script` from `_script_from_filename(filename)`, falling back to `"Latn"` for German-detected filenames (currently returns None, which disables latin_gibberish). Store this on the processing state object. Remove all `expected_script or infer_script(text)` fallback patterns at the ~12 call sites in client.py, converters.py, and helpers.py — every caller passes the metadata-derived value, never self-infers from the text under test. Change `_script_from_filename` to return `"Latn"` instead of None when OCR langs include `deu`/`eng` (currently: only returns `"Arab"` for `ara`, None otherwise). Net: ~-20 lines (removing 12 `or infer_script(...)` expressions).

Step 2 — Fold `_has_sparse_mojibake` into `garble_prongs` (helpers.py, ~-8 lines). Move the regex match from `_has_sparse_mojibake` into `garble_prongs` as a `"sparse_mojibake"` prong. Delete `_has_sparse_mojibake` as a standalone function. `check_garble` simplifies to `return bool(garble_prongs(...))`. This eliminates the dual-surface OR that let fixes to one surface leave the other uncovered.

Step 3 — Thread `had_presentation_forms` into garble_prongs (helpers.py + converters.py, ~+8/-12 lines). Add `had_presentation_forms: bool = False` parameter to `garble_prongs` (and `check_garble`). The presentation_forms prong (lines 1318-1326) switches from re-scanning U+FB50-FEFF codepoints (which NFKC destroyed) to checking this boolean flag. Callers that have an `RtlDecision` pass `rtl_decision.had_presentation_forms`; callers without it pass False (safe default — no false negatives on non-Arabic docs, and Arabic docs always go through `_pre_inference_normalize` which produces the `RtlDecision`). Delete the dead `sum(1 for c in norm if any(lo <= ord(c) <= hi ...))` scan.

Step 4 — Make `expected_script` non-optional in `check_garble` (helpers.py, ~+2/-2 lines). Change the signature from `expected_script: str | None` to `expected_script: str`. The latin_gibberish prong's `_effective_script is not None` guard becomes simply `_effective_script != "Latn"`, which is the actual semantic check. Callers that previously passed None now pass the metadata-derived value from Step 1.

Target files: `src/pageindex_mcp/helpers.py` (Steps 2-4), `src/pageindex_mcp/client.py` (Steps 1, 4), `src/pageindex_mcp/converters.py` (Steps 1, 3). Estimated net delta: -30 to -40 lines.

**(3) Historical Bug Classes Prevented**

- Haftpflicht FAIL-to-PASS flip (Run 9): `_script_from_filename` returning None for German docs disabled latin_gibberish prong entirely. Step 1 fixes this by returning `"Latn"` for deu/eng filenames.
- Ward 597 / Latin-in-Arabic mojibake (Runs 6-11, 16-19): `infer_script` on corrupted text returned `"Latn"`, disabling the Arabic garble checks. Step 1 eliminates text-self-inference entirely; the metadata-derived `"Arab"` would persist.
- Presentation Forms prong nullified by NFKC (RFC-033 D1, D2): `_reversed_morphology` and the presentation_forms prong scan for U+FB50-FEFF which NFKC decomposed. Step 3 replaces the dead codepoint scan with the pre-NFKC boolean already captured.
- Siyasat-hawkama 100% reversed titles stored PASS (Run 10): the split between `garble_prongs` and `_has_sparse_mojibake` meant a fix to one surface did not cover the other. Step 2 merges them.
- ISS-34 silent tessdata fallback producing Latin mojibake that passes garble gate: Step 1 ensures the Arabic `expected_script` from filename metadata survives regardless of what the OCR output text looks like.

**(4) Migration Risk and Sequencing**

Risk is moderate — `check_garble` has ~15 direct call sites across 3 files, and changing `expected_script` from optional to required is a breaking signature change. Sequence incrementally:

Wave 1 (safe, no behavior change): Fold `_has_sparse_mojibake` into `garble_prongs` (Step 2). Pure internal refactor, no caller signature change. Validate with existing test suite.

Wave 2 (low risk): Fix `_script_from_filename` to return `"Latn"` for German docs (Step 1 partial). This is a one-line change with a net-positive effect (enables latin_gibberish for German docs). Run corpus spot-check on the 5 German docs.

Wave 3 (medium risk): Thread `had_presentation_forms` boolean (Step 3). Requires touching `check_garble` and `garble_prongs` signatures plus the ~6 call sites that have access to an `RtlDecision`. Run corpus on the 3 Arabic docs (ward, siyasat-hawkama, human-rights).

Wave 4 (highest risk): Eliminate all `infer_script(text)` fallbacks and make `expected_script` required (Steps 1 complete + 4). This touches all 12 call sites. Must confirm every call site has access to the metadata-derived script before removing the fallback. Full corpus regression run required.

Rollback: Each wave is independently revertible. The env-var gates (`GARBLE_LATIN_GIBBERISH_ENABLED`, `GARBLE_SHORT_TEXT_DEFAULT`) remain as emergency kill switches throughout.

**(5) Estimated Effort**

Wave 1: 0.5 day (mechanical merge + test update).
Wave 2: 0.25 day (one-line fix + 5-doc spot-check).
Wave 3: 1 day (signature threading + Arabic corpus validation).
Wave 4: 1.5 days (12 call sites + full 25-doc corpus regression).
Total: ~3.25 days, executable by one developer across 4 independent PRs.

---

### Zone 2: OCR Recovery Pipeline Flag Conflation and Mutable State Ordering

**Severity:** critical | **Bug count:** 11

#### Mechanism

The generative mechanism is three-fold: (1) Flag conflation — `OCR_ESCALATION_GARBLE` is checked at `client.py:1316` (Recovery 1) AND `client.py:1624` (Recovery 5), so toggling 'garble escalation' silently also disables image-dominant escalation. The two flags (`OCR_ESCALATION_GARBLE` and `OCR_ESCALATION_PER_PICTURE`) are claimed independent but are not when page-level recovery re-enters the per-picture path. (2) Implicit state mutation — `ExtractionState` is a mutable dataclass with 18 fields, mutated in-place by each recovery method. If Recovery 1 flips `state.ok=True`, Recovery 5's gate (requires `!state.ok`) short-circuits. The recovery loop in `index()` iterates GATES order for tag-driven recoveries but image-dominant is a 'post-loop' ad-hoc recovery, creating two non-uniform dispatch mechanisms. (3) Arithmetic impossibility — `_repeating_token_density` returns None for texts with <20 alnum tokens (guaranteed for no-text-layer PDFs pre-retry), making the density comparison `_post_density < 0.0` which is never true, so OCR retry is always reverted.

#### History

a. RFC-029 D4: keep-best guardrail made OCR retry arithmetically impossible to win for no-text-layer PDFs (48k→14.8k chars, 69% loss reverted).
b. RFC-020 Regression 1: picture splice removal caused 5 Arabic PDFs to regress to flat with 60% content loss.
c. RFC-027 D7→RFC-028 D0: dynamic timeout calculation implemented but never wired into worker subprocess, world-stats-pocketbook ERROR across 3 consecutive runs.
d. RFC-015 D6: per-picture OCR pipeline never fires on scanned Arabic (force-full-page-ocr yields only `<!-- image -->` markers).
e. RFC-028 D5: improved OCR language detection produced higher-volume junk diluting garble-ratio below thresholds (وارد 597 MARGINAL→PASS Run12).
f. RFC-025 D1: page-level `_text_layer_has_content` check returning True from header/footer text disabled picture OCR for entire page (Human Rights 503k→382 chars).
g. Image enrichment replacing real chart OCR with boilerplate placeholder text (Run16 FAIL, Run18 FAIL).
h. RFC-035 D2 landscape: serial loop over flagged pages with no cap blows 1500s timeout.
i. Per-picture OCR conflation with page-level OCR (investigation 2026-07-27).
j. Standalone image ingestion bypasses per-picture enrichment entirely.

#### Code Evidence

`client.py:1295-1458` `_recover_ocr_escalation` (complexity 21, 164 lines): entry gate at `:1308-1316` checks `state.first_defect in (TreeDefect.GARBLING, TreeDefect.NODE_GARBLING) ... and _OCR_ESCALATION_GARBLE`. Retry-wins heuristic at `:1389-1400` calls `_repeating_token_density` which returns None for <20 alnum tokens (line ~1386). `client.py:1611-1693` `_recover_image_dominant_ocr`: entry gate at `:1620-1628` checks `_OCR_ESCALATION_GARBLE and _IMAGE_DOMINANT_OCR_ESCALATION_ENABLED` — both required. Language-derivation block at `:1638-1646` is identical to `:1335-1343` in Recovery 1. `helpers.py:203-231` `ExtractionState`: mutable dataclass with 18 fields including `ok`, `route`, `first_defect`, `md_content`, `pic_results`, `rtl_decision` — all mutated in-place by recovery methods with no return value.

#### Key Files

- `src/pageindex_mcp/client.py`
- `src/pageindex_mcp/converters.py`
- `src/pageindex_mcp/config.py`

#### Simplification Proposal

I now have full understanding of the three bug-generating mechanisms. Here is my analysis:

**(1) Core Simplification**

Replace the three competing OCR recovery paths (Recovery 1 `_recover_ocr_escalation`, Recovery 5 `_recover_image_dominant_ocr`, and the implicit per-picture re-entry via `pdf_to_markdown_docling`) with a single `_recover_ocr_retry` method that accepts a typed `OcrRetryReason` enum (GARBLE, LOW_CONTENT, IMAGE_DOMINANT) as its trigger. This method owns all OCR re-extraction, language derivation, and the keep-best heuristic in one place. Separately, split `OCR_ESCALATION_GARBLE` into two independent flags — `OCR_ESCALATION_PAGE_GARBLE` and `OCR_ESCALATION_IMAGE_DOMINANT` — so toggling garble retry does not silently disable image-dominant retry. The `_repeating_token_density` None-for-<20-tokens path must return 1.0 (worst possible density) instead of None, eliminating the arithmetic impossibility for no-text-layer PDFs.

**(2) Concrete Restructuring Steps**

Step A — Split the flag (config.py, ~+6/-2 lines):
- Rename `OCR_ESCALATION_GARBLE` to `OCR_ESCALATION_PAGE_GARBLE` with same default.
- Add `OCR_ESCALATION_IMAGE_DOMINANT` as a new independent flag (default true), replacing the combination of `_OCR_ESCALATION_GARBLE and _IMAGE_DOMINANT_OCR_ESCALATION_ENABLED` currently at `client.py:1624`.
- Update the import in client.py (~3 lines changed).
- Remove `_IMAGE_DOMINANT_OCR_ESCALATION_ENABLED` from client.py (delete lines 415-417, ~-3 lines).

Step B — Fix the density arithmetic (client.py, ~+3/-5 lines):
- Move `_repeating_token_density` out of the closure to a module-level function.
- Change the `len(tokens) < 20` branch to `return 1.0` instead of `return None`. This makes the `_post_density < _pre_density * 0.80` comparison reachable for no-text-layer PDFs, so OCR retry can actually win.
- Remove the None-guard branches at lines 1415-1424 and 1426-1427 (dead code after the fix).

Step C — Consolidate Recovery 1 and Recovery 5 into one method (client.py, ~+30/-180 lines net):
- Create an `OcrRetryReason` enum with values GARBLE, LOW_CONTENT, IMAGE_DOMINANT in helpers.py.
- Write `_recover_ocr_retry(self, state, file_path, filename, ext, expected_script, reason: OcrRetryReason)` that:
  - Contains the entry-gate logic (each reason checks its own independent flag).
  - Contains the language-derivation block exactly once (currently duplicated at `:1330-1338` and `:1638-1646`).
  - Contains the OCR dispatch (remote vs local) exactly once (currently duplicated at `:1345-1356` and `:1655-1669`).
  - Contains the picture-splice block exactly once (currently duplicated at `:1358-1362` and `:1670-1676`).
  - Contains the keep-best heuristic (only for GARBLE/LOW_CONTENT reasons; IMAGE_DOMINANT always takes the new result since it starts from `<!-- image -->` markers).
  - Clears `state.rtl_decision = None` before revalidation (currently only done in Recovery 5 at `:1679`, missing in Recovery 1).
- Delete `_recover_ocr_escalation` (lines 1295-1458, 164 lines).
- Delete `_recover_image_dominant_ocr` (lines 1611-1693, 83 lines).
- Move `_recover_image_dominant_ocr`'s image-line ratio check (lines 1631-1634) into the entry gate of the unified method under the IMAGE_DOMINANT reason.

Step D — Unify dispatch (client.py, ~+5/-15 lines):
- Add `recovery_tag="ocr_escalation"` to the `GateSpec` for `NODE_COUNT_LOW` and `DEPTH_LOW` in helpers.py GATES table (currently these defects have no recovery_tag, so image-dominant recovery lives as a post-loop ad-hoc call).
- In the `_recovery_dispatch` dict, the `"ocr_escalation"` entry calls the unified `_recover_ocr_retry` with reason derived from `state.first_defect` (GARBLE for GARBLING/NODE_GARBLING, LOW_CONTENT for NODE_COUNT_LOW with low chars, IMAGE_DOMINANT for NODE_COUNT_LOW/DEPTH_LOW with high image ratio).
- Delete the post-loop ad-hoc call at lines 2221-2236 and its duplicated re-derivation block.

Step E — Guard per-picture re-entry (converters.py, ~+3/-0 lines):
- In `pdf_to_markdown_docling`, when `force_full_page_ocr=True`, skip the per-picture OCR path entirely (it re-fires redundantly during page-level escalation). Add an early return in the per-picture loop when the caller has already requested full-page OCR.

Estimated net delta: ~-160 lines (deletion of ~247 lines of duplicated recovery code, addition of ~87 lines for unified method + enum + flag split).

**(3) Historical Bug Classes Prevented**

- RFC-029 D4 (OCR retry arithmetically impossible to win for no-text-layer PDFs): Directly fixed by Step B — `_repeating_token_density` returning 1.0 instead of None makes the density comparison reachable.
- RFC-028 D5 (OCR language detection producing higher-volume junk): Prevented by single language-derivation block — fixes applied once propagate to all three retry reasons instead of needing parallel fixes in Recovery 1 and Recovery 5.
- RFC-025 D1 (page-level `_text_layer_has_content` disabling picture OCR): Step E prevents per-picture OCR from re-firing during page-level escalation, eliminating the conflation.
- RFC-015 D6 (per-picture OCR never fires on scanned Arabic during force-full-page-ocr): Same as above — the guard in Step E makes the boundary explicit.
- RFC-020 Regression 1 (picture splice removal causing Arabic regressions): Single splice block means splice logic changes propagate uniformly.
- Flag conflation (toggling OCR_ESCALATION_GARBLE silently disabling image-dominant escalation): Directly fixed by Step A — independent flags.
- Recovery 5 missing RTL decision clear (silent bug, never manifested because Recovery 5 rarely fires): Fixed by having a single code path that always clears `rtl_decision` before revalidation.
- Copy-paste divergence between Recovery 1 and Recovery 5 (any future fix to one missed in the other): Eliminated by consolidation.

**(4) Migration Risk and Sequencing**

Risk is moderate. The retry-wins heuristic change (Step B) alters acceptance behavior for no-text-layer PDFs — OCR retries that were always reverted will now be kept when density improves. This is the *intended* behavior (the current behavior is a bug), but it changes corpus outcomes for affected documents. Sequencing:

Wave 1 (zero behavioral change): Step A (flag split) + Step B density fix only. Ship behind the existing kill-switches. Run corpus scoring to confirm no regressions on the 25-doc corpus. The density fix will change outcomes for no-text-layer PDFs — verify those specific documents improve.

Wave 2 (consolidation, behavioral equivalent): Steps C + D. The unified method must produce identical OCR dispatch calls, identical language derivation, and identical keep-best decisions as the two separate methods. Verify by running the corpus with debug logging that captures pre/post chars and retry_wins for every OCR escalation, then diff against Wave 1 output. The only expected difference is Recovery 5 now clears `rtl_decision` (which Recovery 1 already should have done).

Wave 3 (re-entry guard): Step E. This changes behavior for documents where per-picture OCR was redundantly re-firing during page-level escalation. Test with the specific corpus documents that trigger `force_full_page_ocr` (the Arabic PDFs from RFC-015/RFC-020).

Each wave is independently shippable and revertible via the existing env-var kill-switches.

**(5) Estimated Effort**

Wave 1: 0.5 day (flag rename + density fix + corpus spot-check).
Wave 2: 1.5 days (consolidation + corpus regression run + debug-log diffing).
Wave 3: 0.5 day (re-entry guard + targeted Arabic PDF verification).
Total: 2.5 days, assuming corpus scoring infrastructure is already operational.

Key files: `client.py` (lines 1295-1458 Recovery 1, lines 1611-1693 Recovery 5, lines 2167-2236 dispatch), `config.py` (lines 39-48 flag definitions), `helpers.py` (lines 202-232 ExtractionState, lines 1826-1842 GATES table), `converters.py` (per-picture OCR re-entry guard).

---

### Zone 3: Three-Layer Verdict Pipeline Implicit GATE_TABLE Coupling

**Severity:** critical | **Bug count:** 10

#### Mechanism

The generative mechanism is implicit positional coupling: `GATE_TABLE` is a Python list where position encodes severity rank. `validate_tree` returns the FIRST firing gate as the primary defect, but `compute_verdict` checks ALL defects against `HARD_FAIL_DEFECTS` with `_GATE_PRIORITY` tiebreak (derived from `enumerate(GATE_TABLE)`). Adding a new `GateSpec` to `GATES` with a new `TreeDefect` enum requires simultaneous updates to: (1) GATES list position, (2) `REASON_POLICY` mapping (auto-derived), (3) `HARD_FAIL_DEFECTS` membership (auto-derived from `hard_fail=True`), (4) `recovery_tag` dispatch in `client.py` `index()`, (5) `compute_verdict` promotion/exemption logic. Steps 1-3 are auto-derived but steps 4-5 are manual. The promotion ladder in `compute_verdict` embeds ordering assumptions (image-enrichment rescue BEFORE `max_leaf_ratio` hard-fail, locked by RFC-022 B2) that interact with threshold calibration. Threshold constants (`PASS_MAX_LEAF_RATIO`, `PASS_HYSTERESIS_BAND`, content-density floors) are calibrated by-incident against specific failing corpus documents rather than derived analytically, causing oscillation when new documents exercise boundary cases.

#### History

a. RFC-029 D0/D1/D2/D8: 4 new validation reasons (suspect_density, low_content_density, empty_node_contamination, arabic_low_content_ratio) added to `validate_tree` without updating client.py recovery routing → 3 PASS→ERROR and 1 FAIL→ERROR in Run13 (fixed RFC-030 D2).
b. RFC-029 D1: content-density gate (500 chars/node) false-rejected Penal Code (408.2), federal_decree_law_no_33 (54.3), marsoom-33 (459.4) in Run13 (fixed RFC-030 D3 threshold lowered, but then became too lenient for RTL-corrupted trees Run14-15).
c. RFC-018 D3b: `node_garbling` reason code not matched by OCR escalation literal 'garbling' string.
d. RFC-024→RFC-025 D0: `PASS_MAX_LEAF_RATIO` widened 0.17→0.20→0.30 + hysteresis +0.10 = effective 0.40, masking 61% garbled trees and content loss as PASS.
e. RFC-025/RFC-026 threshold retune + depth check re-add: 3 docs flip PASS↔MARGINAL across Runs 7-10.
f. RFC-026 D5: garble check ordering fix (validate_tree exited early on node_count<3 before garble check).
g. `image_enrichment_promoted` auto-pass violating Hard Rule 5 (Runs 9/10).
h. RFC-026 char floor checks volume not validity (barcode noise PASS).
i. Table `row_records` invisible to content scoring (block['text'] only, not row_records that retrieval correctly uses).

#### Code Evidence

`helpers.py:1826-1842` GATES: ordered list of 12 GateSpec entries, position encodes severity. `GATE_TABLE` at `:1848-1850` derived as `[(g.gate_fn, g.defect) for g in GATES if g.gate_fn is not None]`. `REASON_POLICY` at `:1853` derived as `{g.defect: g.policy for g in GATES}`. `HARD_FAIL_DEFECTS` at `:1870` derived as `frozenset(g.defect for g in GATES if g.hard_fail)`. `_GATE_PRIORITY` at `:1875` derived as `{defect: idx for idx, (_fn, defect) in enumerate(GATE_TABLE)}`. `helpers.py:1898-1964` `validate_tree`: iterates GATE_TABLE exhaustively, returns first firing as primary defect. `helpers.py:2186-2411` `compute_verdict` (complexity 28, 226 lines): consumes TreeGateResult, checks primary defect against HARD_FAIL_DEFECTS then masked co-firing with `_GATE_PRIORITY` tiebreak. `helpers.py:286-320` `decide_route`: REASON_POLICY lookup mapping TreeDefect to Route.

#### Key Files

- `src/pageindex_mcp/helpers.py`
- `src/pageindex_mcp/client.py`

#### Simplification Proposal

I now have a comprehensive picture of the architecture. Here is my analysis.

**(1) Core simplification (2-3 sentences)**

Replace the implicit position-encodes-severity GATES list with an explicit `severity: int` field on GateSpec. Each GateSpec already declares `defect`, `policy`, `hard_fail`, `gate_fn`, and `recovery_tag` — adding a numeric `severity` (lower = more severe) eliminates the coupling between list order and priority ranking, making `_GATE_PRIORITY` derivation order-independent. Then move the FLAT_GATE_SUBSET membership and the promotion/exemption rules that are currently scattered through `compute_verdict`'s body into per-GateSpec declarative fields (`flat_applicable: bool`, and a new `VerdictPhase2Rule` registry keyed by content_class), so adding a gate is a single GateSpec addition with a compile-time completeness check rather than a five-site manual wiring exercise.

**(2) Concrete restructuring steps**

Step A — Add `severity: int` to GateSpec, remove positional coupling (helpers.py)
- Add `severity: int` field to `GateSpec` dataclass (line 244-262). Each existing gate gets an explicit integer (GARBLING=0, NODE_COUNT_LOW=1, ..., SUSPECT_DENSITY=9).
- Rewrite `_GATE_PRIORITY` derivation (line 1875-1877) from `{g.defect: g.severity for g in GATES if g.gate_fn}` instead of `enumerate(GATE_TABLE)`.
- Rewrite `validate_tree` (line 1943-1964) to sort `fired` by `severity` before picking primary, instead of relying on iteration order: `primary = min(fired, key=lambda pair: _GATE_PRIORITY[pair[0]])`.
- GATE_TABLE derivation (line 1848-1850) becomes order-independent — can sort by severity or leave as-is since validate_tree no longer relies on list order.
- Delta: +12 lines (severity assignments), -3 lines (enumerate logic). Net ~+9 lines.

Step B — Add `flat_applicable: bool` to GateSpec, delete `_FLAT_APPLICABLE_DEFECTS` (helpers.py)
- Add `flat_applicable: bool = False` to GateSpec.
- Set `flat_applicable=True` on GARBLING, NODE_GARBLING, REORDERED gates.
- Derive `FLAT_GATE_SUBSET` from `[... for g in GATES if g.gate_fn and g.flat_applicable]` (already derived, just change the filter source).
- Delete the standalone `_FLAT_APPLICABLE_DEFECTS` frozenset (lines 1882-1886).
- Delta: +3 lines (field + assignments), -5 lines (frozenset deletion). Net ~-2 lines.

Step C — Add import-time exhaustiveness assertion for recovery_dispatch (client.py)
- The existing runtime assert at `client.py:2190-2194` (`_gate_tags <= set(_recovery_dispatch)`) already catches missing dispatch entries, but it fires per-call inside `index()`. Promote it to module-level or `__init__` so it fails at startup, not on first document. This is a 5-line move.
- Delta: net 0 lines (move, not add).

Step D — Extract compute_verdict Phase 2 promotion rules into a registry (helpers.py)
- Define a `PromotionRule` dataclass: `content_class_prefix: str`, `check: Callable[[TreeSignals, VerdictThresholds, ...], tuple[str, str] | None]`, `label: str`.
- Move the 5 promotion branches (image-enrichment rescue, base PASS, cat_a, cat_b/flat, cat_c, small_doc) into a `PROMOTION_RULES: list[PromotionRule]` list, each with its own pure function.
- compute_verdict Phase 2 becomes a loop: `for rule in PROMOTION_RULES: result = rule.check(...); if result: return _apply_clamp(result)`.
- The RFC-022 B2 ordering lock (image-enrichment before max_leaf_ratio) becomes an explicit `severity` or position in PROMOTION_RULES with a comment, rather than implicit code ordering.
- Delta: ~+40 lines (dataclass + 5 small functions), -60 lines (inline branches removed from compute_verdict). Net ~-20 lines. compute_verdict drops from ~226 lines / complexity 28 to ~80 lines / complexity ~8.

Step E — Add a compile-time (import-time) completeness check for TreeDefect coverage (helpers.py)
- After GATES derivation, assert that every non-deprecated TreeDefect member with a gate_fn also has: a REASON_POLICY entry (already checked), a severity value, and if policy is RETRY_*, a recovery_tag (already checked). Add: assert no two gates share the same severity value.
- Delta: +5 lines.

**Total estimated delta**: ~-8 lines net. compute_verdict complexity drops from 28 to ~8. The number of sites requiring update when adding a gate drops from 5 (GATES position + _FLAT_APPLICABLE + compute_verdict promotion + client.py dispatch + recovery_tag) to 2 (GateSpec entry + client.py dispatch method, if recoverable).

**(3) Historical bug classes this would have prevented**

- **RFC-029 D0/D1/D2/D8 (gates added without recovery wiring)**: The startup-time exhaustiveness assertion (Step E + Step C promotion) would have crashed the worker on deploy instead of producing PASS-to-ERROR flips in Run-13. The current runtime assert only fires inside `index()` on first document processing.
- **RFC-024/RFC-025 threshold oscillation**: Extracting promotion rules into named, independently testable functions (Step D) makes each threshold's coverage explicit and unit-testable in isolation, rather than buried in a 226-line function where interaction effects are invisible.
- **RFC-026 D5 gate ordering bug (early exit before garble check)**: With explicit severity ranking (Step A), validate_tree evaluates all gates and picks the minimum-severity winner, so gate evaluation order no longer determines which defect is reported — reordering gates cannot cause early-exit skipping.
- **RFC-018 D3b literal string mismatch**: The recovery_tag field (already present) plus the startup assertion (Step C) would have caught the `'garbling'` vs `'node_garbling'` literal mismatch at process start.
- **image_enrichment_promoted Hard Rule 5 violation (Runs 9/10)**: With promotion rules as a named registry (Step D), the image-enrichment rescue rule becomes a discrete, auditable unit with an explicit position relative to max_leaf_ratio, rather than an implicit code-ordering dependency.

**(4) Migration risk and incremental sequencing**

Sequence: A -> B -> E -> C -> D (each independently deployable and testable).

- **Step A (severity field)**: Lowest risk. Pure additive — adds a field, changes derivation, existing behavior is identical if severity values match current list positions. Verify: run full corpus scoring, diff verdicts against baseline (expect zero changes).
- **Step B (flat_applicable)**: Lowest risk. Moves a hardcoded set into the dataclass. Verify: assert `FLAT_GATE_SUBSET` contents are identical before/after.
- **Step E (exhaustiveness assertions)**: No runtime behavior change. Assertions that pass today will continue to pass. Risk: false-positive assertion on a deprecated gate (ARABIC_LOW_CONTENT_RATIO) — guard with `if g.gate_fn is not None`.
- **Step C (startup assertion promotion)**: Moves an existing assert earlier. Risk: test harnesses that import client.py without full GATES wiring would fail at import — mitigate by guarding with `if not os.environ.get("PAGEINDEX_SKIP_GATE_ASSERTIONS")`.
- **Step D (promotion registry)**: Highest risk — restructures the most complex function. Mitigate: (a) extract one promotion rule at a time (5 sub-PRs), (b) each extraction must produce identical corpus verdicts (zero-diff gate), (c) keep the old code commented for one release cycle. The RFC-022 B2 ordering constraint must be preserved as an explicit test: `assert PROMOTION_RULES.index(image_enrichment_rule) < PROMOTION_RULES.index(max_leaf_ratio_rule)`.

Key risk: the promotion rules interact with each other through early returns (first matching rule wins). The registry must preserve this short-circuit semantics. This is straightforward with a loop but must be verified with the full 25-document corpus scoring.

**(5) Estimated effort**

- Step A: 0.5 day (field addition + derivation rewrite + corpus verification)
- Step B: 0.25 day
- Step E: 0.25 day
- Step C: 0.25 day
- Step D: 1.5 days (5 sub-extractions x 0.3 day each, including corpus diff verification per extraction)
- Total: ~2.75 days of implementation + verification

Key files: `helpers.py` (lines 244-262, 1826-1877, 1882-1895, 1898-1964, 2186-2411), `client.py` (lines 2160-2220).

---

### Zone 4: Dual-Store Verdict Consistency and Persistence Timing

**Severity:** high | **Bug count:** 9

#### Mechanism

The generative mechanism is dual-store divergence under concurrent writes with asymmetric CAS protection. `_verdict_cas_guard` (`storage.py:515-542`) uses Python lexicographic ISO-8601 comparison on `verdict_computed_at`; `_UPSERT_SQL` (`registry.py:166-211`) uses SQL `CASE WHEN EXCLUDED.verdict_computed_at >= COALESCE(doc_registry.verdict_computed_at, '')`. Both must agree for cross-store consistency; a logic divergence in either creates drift detectable only by `reconcile_registry_drift`'s periodic cron. The non-verdict columns (doc_name, source_url, node_count, content_class, sha256, etc.) are ALL unconditional `EXCLUDED.*` last-writer-wins in `_UPSERT_SQL` — a reconcile with stale MinIO-read data landing after a live dual-write silently regresses these fields. The write-visibility problem is structural: the worker child process writes to MinIO via `save_doc`/`save_doc_meta`, then the worker parent reads child stdout and does the Postgres dual-write — but the scorer/audit harness reads MinIO independently with no coordination, creating a read-after-write race window. Fix attempts oscillate between under-provisioned (no retry) and over-provisioned (4.4s delay) barriers.

#### History

a. Run15: القرار التنظيمي scored ERROR (NoSuchKey) but live artifacts exist (83 nodes/48,586 chars).
b. حقوق الإنسان NoSuchKey Run12→13 (transient one-run loss).
c. RFC-034 D18 write barrier (`_confirm_write_visible` 4-attempt 4.4s delay) overcorrection → cabinet_resolution ERROR Run16, اتفاقية مستوى الخدمة ERROR Run19 (persisted after scorer window).
d. RFC-036 D1 reduced delay to 0.45s.
e. RFC-033 D3 read-side-only retry insufficient, persistence-timing race recurred Run16.
f. Run9 scoring harness: all 24 docs defaulted to verdict=ERROR despite real PASS/MARGINAL in MinIO.
g. world-stats-pocketbook: PASS Run16→MISSING Run18→ERROR Run19 (no artifacts in MinIO).
h. cabinet_resolution_no_96: MARGINAL→ERROR Run16.
i. Fire-and-forget registry delete (erasure compliance gap).
j. Erasure cascade missing preloaded/ bucket prefix.
k. Registry write fails silently on pool unavailability.

#### Code Evidence

`storage.py:515-542` `_verdict_cas_guard`: Python lexicographic `existing_ts > incoming_ts` comparison, returns True to skip merge when existing is newer. `registry.py:166-211` `_UPSERT_SQL`: SQL `CASE WHEN EXCLUDED.verdict_computed_at >= COALESCE(doc_registry.verdict_computed_at, '')` for 4 verdict columns; remaining 11 columns are unconditional `EXCLUDED.*` last-writer-wins. `worker.py:684-731` `_upsert_registry_row`: best-effort try/except, `if get_pool() is None: return` silent no-op, reads MinIO via `read_registry_fields` then overlays verdict_fields from child stdout. `client.py:2260-2330` `index()` match block: the only dispatch point between persistence routes, called AFTER all recovery methods have mutated state.

#### Key Files

- `src/pageindex_mcp/storage.py`
- `src/pageindex_mcp/registry.py`
- `src/pageindex_mcp/worker.py`
- `src/pageindex_mcp/registry_backfill.py`

#### Simplification Proposal

I have a complete understanding of all the moving parts. Here is the analysis:

**(1) Core Simplification (2-3 sentences)**

Make Postgres the single authoritative verdict store and demote the MinIO sidecar to a read-through cache that is always written FROM the Postgres row, never independently. Eliminate the dual-CAS by moving verdict writes to a single `INSERT ... ON CONFLICT` path in registry.py (the SQL CAS) and removing `_verdict_cas_guard` from storage.py entirely — the sidecar gets its verdict fields by copying whatever Postgres holds, never by racing it. The child subprocess (converters_cli) returns verdict_fields via stdout JSON (already implemented) and the worker parent writes Postgres first, then backfills the sidecar from the committed row, inverting the current flow that writes MinIO first and Postgres second.

**(2) Concrete Restructuring Steps**

Step A — Single verdict authority in registry.py (~+30/-0 lines):
- Add `async def upsert_verdict(doc_id, verdict_fields) -> dict` that does the CAS-guarded INSERT/UPDATE for verdict columns only and returns the winning row (using `RETURNING *`). This becomes the sole verdict write point.

Step B — Invert worker.py write order (~+15/-25 lines in `_upsert_registry_row`):
- Write Postgres FIRST via `upsert_verdict()`, receiving the winning row back.
- THEN write MinIO sidecar via `save_doc_meta()` using the Postgres-returned values.
- Remove the `read_registry_fields()` call from `_upsert_registry_row` (the MinIO re-read that creates the race window). The sidecar is now a write-behind cache, not a source.
- Remove the `if get_pool() is None: return` silent no-op: if Postgres is down, the verdict must not be silently lost. Instead, write to a Redis retry queue (`pageindex:verdict_retry:<doc_id>`) so the reconcile cron can drain it.

Step C — Delete `_verdict_cas_guard` and simplify `save_doc_meta` (~-40 lines in storage.py):
- Remove `_verdict_cas_guard()` (lines 515-542) and the `_VERDICT_CAS_FIELDS` frozenset (lines 509-512).
- In `save_doc_meta`, remove the CAS branch (lines 624-634). Verdict fields arriving here now come exclusively from the Postgres-committed row, so there is no ordering conflict to guard against.

Step D — Protect non-verdict columns in `_UPSERT_SQL` (~+10/-5 lines in registry.py):
- Add CAS guards to `sha256`, `node_count`, and `processed_at` using `CASE WHEN EXCLUDED.processed_at >= COALESCE(doc_registry.processed_at, '')` so reconcile-with-stale-MinIO-data cannot regress these fields. The remaining facet columns (product, tier, doc_family, effective_date, doc_description) stay last-writer-wins as they are human-curated and reconcile is the correct authority.

Step E — Reconcile cron drains retry queue (~+25 lines in registry_backfill.py):
- In `reconcile_registry_drift()`, before the MinIO scan, drain the Redis `pageindex:verdict_retry:*` keys. For each, call `upsert_verdict()` and then `save_doc_meta()` to heal the sidecar. This replaces the current silent-loss behavior.

Step F — Remove write-visibility barrier from sidecar path (~-5 lines in storage.py):
- The `_confirm_write_visible` call in `save_doc_meta` (line 652) can be removed. Since the sidecar is now a write-behind cache populated from Postgres, no downstream reader (scorer, audit harness) depends on its immediate visibility for verdict data. The barrier remains in `save_doc` and `save_flat_doc` for the artifact body (these are still the tree/flat source of truth).

Step G — Erasure cascade fix (~+8 lines in storage.py `delete_doc`):
- Add `preloaded/` prefix to the erasure cascade documentation and code (the existing step 7 already handles it but is conditioned on `doc_name` being known; add a fallback listing scan of `preloaded/` filtered by doc_id prefix).
- Make the registry delete awaited with error surfacing (already done per the code, but verify the timeout is reasonable).

**Estimated net delta**: ~+90/-75 lines across 4 files. No new modules, no new abstractions.

**(3) Historical Bug Classes This Would Have Prevented**

- Run-15 cabinet_resolution ERROR (NoSuchKey): the scorer reads MinIO before `_confirm_write_visible` completes. With Postgres-first, the verdict is committed before any sidecar write; the scorer reads Postgres directly (or the sidecar is populated from a committed source, not racing).
- Run-9 all-24-docs defaulting to ERROR: `read_registry_fields` returned None because the pool was not ready and silently no-oped. With the retry queue, those verdicts would have been queued and drained by the next reconcile tick rather than lost.
- Run-16 cabinet_resolution_no_96 MARGINAL-to-ERROR: the `_confirm_write_visible` 4.4s overcorrection pushed the job past the scorer window. Removing the barrier from sidecar writes eliminates this entire class.
- Cross-store CAS divergence (Python lexicographic vs SQL `>=`): eliminated entirely — only one CAS exists (SQL), and the sidecar never independently decides verdict ordering.
- Reconcile silently regressing non-verdict columns (sha256, node_count): the `processed_at` CAS guard on the SQL side prevents stale reconcile data from overwriting live dual-write data.

**(4) Migration Risk and Incremental Sequencing**

Phase 1 (zero-risk, additive): Add `upsert_verdict()` with RETURNING in registry.py. Add the Redis retry queue drain in reconcile. No callers changed yet. Deploy and verify the new functions work via the backfill script.

Phase 2 (low risk, swap write order): Change `_upsert_registry_row` to write Postgres first, then sidecar from the returned row. Feature-flag it behind `REGISTRY_VERDICT_AUTHORITY=postgres` (default: `minio` for backward compat). Run one corpus cycle with the flag on; diff MinIO sidecars vs Postgres rows to confirm convergence.

Phase 3 (medium risk, delete dead code): Once Phase 2 is validated over 2+ corpus runs, remove `_verdict_cas_guard`, simplify `save_doc_meta`, remove the sidecar write barrier, flip the feature flag default to `postgres`, and delete the flag.

Risk: If Postgres is down during Phase 2, the retry queue is the safety net. If Redis is ALSO down, the verdict is logged as a warning (same as today's silent loss, but now observable via a dedicated metric `VERDICT_RETRY_QUEUE_FAILURES`). The MinIO sidecar still gets written in Phase 2 (just from Postgres data), so existing MinIO-only readers (preprocess_client, scoring harness) continue working unchanged.

**(5) Estimated Effort**

Phase 1: 0.5 day (add upsert_verdict + retry queue drain, unit tests).
Phase 2: 1 day (swap write order, feature flag, integration test with corpus run).
Phase 3: 0.5 day (delete dead code, remove flag, update tests).
Total: 2 days of implementation + 1 corpus validation cycle between Phase 2 and Phase 3.

Key files modified: `registry.py`, `worker.py`, `storage.py`, `registry_backfill.py`.

---

### Zone 5: Dead Code and Incomplete Wiring Enforcement Gap

**Severity:** high | **Bug count:** 7

#### Mechanism

The generative mechanism is a gap between implementation completeness checking and integration completeness checking. The codebase has assertions for some integration properties (e.g., GATE_TABLE recovery_tag assertion at `helpers.py:1860-1866` ensures every RETRY_OCR/RETRY_RTL gate has a tag, and `index()` asserts every tag has a dispatch entry). But these assertions only cover the gate-driven recovery loop, not: (a) whether a function body was ever committed (git staged != git committed), (b) whether a function defined in helpers.py is imported and called in client.py or converters.py, (c) whether task-file status matches code reality. The pdf-inspector shadow pilot exemplifies the pattern at scale: classification runs unconditionally (cost always paid), but `PDF_INSPECTOR_PRECLASSIFY` defaults to false, so the result is logged and metered but never influences OCR mode or timeout — shadow-mode is the permanent state despite the branch name implying activation is imminent.

#### History

a. RFC-027 D7: dynamic timeout calculation (`chunked_docling_timeout_s`) implemented but never wired into worker subprocess → world-stats-pocketbook ERROR across 3 consecutive runs (Run9/10/11), fixed RFC-028 D0.
b. RFC-029 D0: `_check_bidi_coherence` fully implemented but defined twice (`helpers.py:936` and `:1028`) and never called from `validate_tree` or any client.py path (RFC-030 D5).
c. RFC-029 D6 Phase B: calibration rules for LLM judge marked complete but never written to SKILL.md (RFC-030 D6).
d. RFC-034 D19: enrichment density-preserve fix fully implemented and staged in git but never committed, inactive during Run-19 (RFC-036 D2).
e. RFC-033 D2 Part A: `_heading_is_logical_order` guard exists uncommitted only, `git log -S` confirms never committed, property tests Tasks 1.11-1.12 marked complete but do not exist.
f. RFC-035 D2: landscape rasterize-rotate-reextract shipped with 3 compounding pre-commit defects (serial loop no cap, non-daemon threads, end-of-document append).
g. pdf-inspector classification computed but never consumed (shadow-only, ~30% throughput gain deferred).

#### Code Evidence

`helpers.py:1826-1842` GATES + `:1860-1866` recovery_tag assertion: enforces tag→dispatch wiring but not code-committed or function-called status. `client.py:2184-2196` `index()` `_recovery_dispatch` + `:2198-2201` `_gate_tags` assertion: `assert _gate_tags <= set(_recovery_dispatch)` ensures dispatch coverage but only for tag-driven recoveries. `worker.py:320-341` `_run_converter_subprocess`: `chunked_docling_timeout_s` consumes inspector classification for 3x multiplier, but `PDF_INSPECTOR_PRECLASSIFY` defaults false (`config.py:21-23`). `converters.py:3669-3729` `pdf_markdown_converters`: chain-build gate decides which converters are included, but cannot detect whether a converter's internal features (landscape reextract, picture OCR) are wired end-to-end.

#### Key Files

- `src/pageindex_mcp/helpers.py`
- `src/pageindex_mcp/client.py`
- `src/pageindex_mcp/worker.py`
- `src/pageindex_mcp/config.py`

#### Simplification Proposal

I now have a clear picture of the zone. Here is the analysis.

**(1) Core simplification (2-3 sentences)**

Extend the existing GateSpec declarative registry pattern to become the single integration manifest for all pipeline features — not just gate-to-recovery wiring. Each feature that crosses a module boundary (config flag, converter capability, inspector classification consumer) gets a `FeatureWiring` declaration that names the config flag, the producer function (with module path), and every consumer call-site; a single import-time assertion loop (analogous to the existing `assert _gate_tags <= set(_recovery_dispatch)`) validates that every declared producer is importable, callable, and that every consumer imports it. The pdf-inspector shadow pilot, landscape reextract, and any future cross-module feature would be forced through this registry, making "implemented but unwired" a startup crash rather than a silent gap.

**(2) Concrete restructuring steps**

Step A — Define FeatureWiring registry (helpers.py, +~60 lines):
- Add a `FeatureWiring` frozen dataclass alongside GateSpec (~line 270): fields `name: str`, `config_flag: str | None` (name in config.py), `producer: str` (dotted path like `converters._run_pdf_inspector`), `consumers: tuple[str, ...]` (dotted paths like `worker._run_converter_subprocess`, `client._convert_and_validate`), `shadow_only: bool = False`.
- Add a module-level `FEATURE_WIRINGS: list[FeatureWiring]` populated with the current cross-module features: pdf_inspector classification (shadow_only=True currently), chunked_docling_timeout, landscape_reextract, picture_ocr_enrichment.
- Add an import-time validation loop (~15 lines) that for each wiring entry: (a) resolves `producer` via `importlib` to confirm the function exists, (b) for each consumer, confirms the consumer module imports or calls the producer (via `inspect.getsource` substring check or a simpler `hasattr` probe). Fail with `AssertionError` naming the broken link.

Step B — Consolidate pdf-inspector into the registry (config.py -2 lines, worker.py ~-10 lines, client.py ~-5 lines, converters.py ~0):
- The `PDF_INSPECTOR_PRECLASSIFY` flag stays in config.py but its consumption sites (worker.py:336, client.py:990, client.py:2050) are validated by the FeatureWiring entry. If shadow_only=True, the assertion skips consumer-reachability checks but still validates the producer exists.
- When shadow_only is flipped to False (activating the inspector), the assertion automatically enforces that all three consumer sites actually read the classification dict. Net delta: ~-10 lines of ad-hoc guard code in worker.py/client.py replaced by the registry constraint.

Step C — Add a CI pre-commit hook (new file `.github/hooks/check_wiring.py`, +~40 lines):
- A lightweight script that imports `helpers.FEATURE_WIRINGS` and runs the same assertion loop. This catches the "staged but uncommitted" class of bugs: the hook runs against the working tree, so a function that exists in the index but not in HEAD will fail if the wiring entry references it.
- Wire into `.pre-commit-config.yaml` or the existing Makefile `preflight` target.

Step D — Annotate existing GateSpec with module provenance (+~5 lines per gate, helpers.py +~50 lines):
- Add an optional `gate_module: str` field to GateSpec that names where the gate function lives (currently all in helpers.py, but this future-proofs against extraction). The existing `assert set(REASON_POLICY) == set(TreeDefect)` and recovery_tag assertions remain unchanged.

Rough line-count delta: +~150 new lines (registry + dataclass + CI hook), -~20 lines of scattered ad-hoc guards = net +~130 lines. No functionality changes.

**(3) Historical bug classes this would have prevented**

- RFC-027 D7 (chunked_docling_timeout_s implemented but never wired into worker subprocess): FeatureWiring would have declared `worker._run_converter_subprocess` as a consumer; import-time assertion would have caught the missing call.
- RFC-029 D0 (_check_bidi_coherence defined twice, never called): The function would need a FeatureWiring entry to exist; the consumer list would be empty, triggering the "no consumers" assertion at import time.
- RFC-034 D19 (enrichment density-preserve staged but uncommitted): The CI pre-commit hook would have failed because the producer function was not in HEAD, only in the index.
- RFC-033 D2 Part A (_heading_is_logical_order guard uncommitted): Same as D19 — pre-commit hook catches staged-only code referenced by a wiring entry.
- pdf-inspector shadow pilot (classification computed but never consumed): The shadow_only=True flag makes this explicit and auditable rather than implicit. Flipping to False would immediately enforce consumer wiring.
- RFC-029 D6 Phase B (calibration rules marked complete but never written): FeatureWiring for the LLM judge calibration would have no producer resolvable, caught at import time.

**(4) Migration risk and sequencing**

Risk is low because this is purely additive validation — no existing code paths change, no function signatures change, no data flow changes.

Sequence:
1. Land FeatureWiring dataclass + empty FEATURE_WIRINGS list + assertion loop (zero behavioral change, zero risk).
2. Add entries one feature at a time, starting with the already-working gate recovery wiring (proves the pattern on known-good code).
3. Add the pdf-inspector entry with shadow_only=True (validates the current state without breaking anything).
4. Add the CI pre-commit hook (catches future staged-but-uncommitted gaps).
5. Backfill remaining cross-module features (landscape reextract, picture OCR enrichment, table repair).

Each step is independently committable and revertible. The only risk is false-positive assertion failures during development if a developer adds a FeatureWiring entry before implementing all consumers — mitigated by making `consumers` a required field that can be an empty tuple (with a log warning instead of assert for shadow_only entries).

**(5) Estimated effort**

- Step A (dataclass + assertion loop): 2-3 hours
- Step B (pdf-inspector consolidation): 1-2 hours
- Step C (CI pre-commit hook): 1-2 hours
- Step D (GateSpec provenance annotation): 1 hour
- Testing + validation across existing 238-test suite: 1-2 hours

Total: approximately 1-1.5 days of focused work. No corpus reingestion required. No production deployment risk beyond the normal CI gate.

---

### Zone 6: Content-Destructive Heuristics Without Safety Bounds

**Severity:** critical | **Bug count:** 7

#### Mechanism

The generative mechanism is unbounded heuristics applied to heterogeneous documents. Each heuristic is calibrated against specific failing corpus documents (calibration-by-incident) but the document corpus spans German insurance PDFs, Arabic legal decrees, statistical yearbooks, and multilingual MOUs with wildly different structural characteristics. A threshold tuned to catch genuinely under-dense RTL-corrupted trees (500 chars/node) false-rejects well-structured legal hierarchies with fine-grained articles. A ToC-stripping heuristic with no depth guard and no node-count threshold collapses a 595-node depth-3 Penal Code to depth-2 with 493 nodes flattened to top level. A fence-marker parity toggle (`in_fence` boolean) permanently silences all content after any stray backtick line. When a guard is added to fix over-stripping, the guard itself over-strips different documents (RFC-034 D16 guarding D11: Penal Code fixed but Federal Decree-Law 47 fell into 88% body-less heading fragments). Shared segmentation changes for landscape pages break portrait pages simultaneously because the splitting logic is not orientation-aware.

#### History

a. RFC-029 D3: fence-marker stripping `in_fence` parity toggle permanently silenced all content after any stray backtick — SLA doc 264→0 blocks, MOU 89% content loss, qerar-106 truncated at Article 4, Reitlehrer 32% char reduction (Run12/14/15, fixed RFC-030 D0).
b. RFC-034 D11: ToC-heading node stripping with no depth guard collapsed Penal Code depth 3→2 with 493/595 nodes flattened (PASS→MARGINAL Run16).
c. RFC-034 D16: guard for D11 over-stripped Federal Decree-Law 47 into 88% body-less heading fragments with 40% meta-vs-tree char mismatch (FAIL Run18), Arabic decrees flattened to depth-1/depth-0 (Run19).
d. RFC-035 landscape: shared table/chart segmentation change regressed BOTH landscape MARGINAL→FAIL (748 chars fragmented into 71 unusable kv blocks) AND portrait PASS→MARGINAL (89% singleton fragmentation) in Run19.
e. Three Arabic legal docs (مرسوم 13, قرار 106, SLA) recovered content Runs 13→14 but with permanent structural flattening to depth-1 (flat depth disabling article/clause navigation for legal RAG).
f. RFC-034 D3/D17: bidi re-normalization double-application suspected cause of MOU block collapse from 134→20 nodes (Run16).

#### Code Evidence

`helpers.py:2186-2411` `compute_verdict`: promotion ladder with calibration-by-incident thresholds (`_has_sparse_mojibake` docstring: 'calibrated against 92eebefa while sparing b1a72fb2'). `helpers.py:1762-1782` `_gate_low_content_density`: `fires when node_count>=200 AND chars_per_node < _RFC029_MIN_CHARS_PER_NODE` — single threshold with no document-type awareness. `helpers.py:3064` `_segment_table_nodes`/`_split_node`: table segmentation with `_RFC029_TABLE_SEGMENT_CHAR_THRESHOLD`, `_RFC029_TABLE_SEGMENT_MIN_ROWS`, `_RFC036_SINGLETON_ROW_RATIO_THRESHOLD` — three thresholds jointly controlling behavior but NOT RTL-aware (tree-build route has zero RTL awareness for table stitching). `client.py:2260-2330` `index()` match block: `(False, Route.TREE)|(False, Route.PERSIST_FAIL)` explicitly persists low-quality tree with FAIL verdict rather than raising `LowQualityTreeError` — deliberate Hard Rule 5 exception for gates 8/9/10.

#### Key Files

- `src/pageindex_mcp/helpers.py`
- `src/pageindex_mcp/converters.py`
- `src/pageindex_mcp/client.py`

#### Simplification Proposal

No standalone proposal was captured for this zone in this audit cycle. The zone's mechanism overlaps with, and should be remediated together with, the Zone 3 (Three-Layer Verdict Pipeline) promotion-rule extraction — the same calibration-by-incident threshold problem appears in `compute_verdict`'s promotion ladder and in the content-destructive heuristics (fence-marker toggle, ToC-stripping, table segmentation). Recommend: (1) add a pre/post content-preservation check (char-count and depth delta) around every content-destructive transformation, aborting or flagging when loss exceeds a bound (e.g. >20% char loss or >1 depth-level collapse) instead of applying unconditionally; (2) make ToC-stripping and table-segmentation thresholds orientation- and script-aware rather than global constants; (3) replace the `in_fence` boolean parity toggle with a bounded state machine that cannot silence content indefinitely from a single stray marker.

---

## Cross-Cutting Themes

- Recurring bidi/reversed-Arabic and Latin-gibberish garble-gate blind spots are never fully closed: successive fixes across RFC-023/RFC-028/RFC-029/RFC-033/RFC-034 each patch one detection surface (PUA-only, expected_script inference, presentation-form range, run selector encoding range) while leaving an adjacent surface uncovered, so the same failure mode (character-reversed titles, Presentation-Forms visual garbling, Latin-in-Arabic mojibake, RTL word-splitting) resurfaces on new documents run after run.
- Normalization sequencing mismatches: detectors are repeatedly written assuming Arabic Presentation-Form Unicode (U+FB50-FEFF) will survive to the check, but upstream NFKC normalization (converters.py:2357) decomposes it to base Arabic before those checks run, nulling multiple independent detectors (RFC-033 D1/D2, garble ratio, reversed-morphology, bidi coherence selector).
- Incomplete task implementations repeatedly cascade as silent dead code or uncommitted diffs across RFC cycles: RFC-027 D7 timeout calculation never wired into worker.py; RFC-029 D0 bidi coherence check defined twice but never called; RFC-029 D6 calibration rules marked complete but never written to the skill file; RFC-034 D19 enrichment fix and RFC-033 D2 Part A bidi guard fully implemented but staged/uncommitted at audit time; RFC-035 D2 landscape reextract shipped with unfixed compounding defects before commit.
- Naive or oversimplified gate/toggle logic causes catastrophic, disproportionate content loss: RFC-029 D3's fence-parity boolean toggle permanently silences all content after one stray marker (up to 100% loss); RFC-029 D4's keep-best density comparison makes OCR retry arithmetically impossible to win for no-text-layer PDFs; RFC-034 D11's ToC-stripping has no depth/node-count guard and collapses deep legal hierarchies to near-flat.
- New validation reasons or recovery paths are repeatedly added to one side of the gate (validate_tree) without updating the matching side (client.py recovery routing / OCR escalation trigger string matching), so new failure classifications (low_content_density, suspect_density, arabic_low_content_ratio, node_garbling) fall through to a terminal LowQualityTreeError instead of triggering the recovery they were meant to enable.
- Threshold and hysteresis calibration oscillates rather than converges: PASS_MAX_LEAF_RATIO and density-floor thresholds are repeatedly widened to kill false positives (masking real garbling/content loss as PASS) then tightened to catch it again (rejecting valid dense legal trees), with prior-verdict hysteresis banding compounding the softening on re-ingestion.
- Detection improving in one dimension paradoxically defeats a downstream gate: better Arabic OCR language detection (RFC-028 D5) produces higher-volume junk output that dilutes garble-ratio below detection thresholds; garble-gate finally firing on known-junk docs (وارد 597) still has no OCR-recovery hook wired to actually fix the content, so detection improves while remediation stays absent for multiple runs.
- Promotion/auto-pass shortcuts (image_enrichment_promoted, cat_b_promoted) repeatedly bypass content-quality gates in violation of Hard Rule 5, persisting near-empty or junk-content documents as PASS/MARGINAL until a subsequent RFC adds a char-floor or zero-content check — which itself only checks volume, not validity, leaving a residual bypass for high-volume junk (barcode/watermark OCR noise).
- Read-after-write persistence races between the worker (MinIO writes) and the scorer/audit harness recur across many runs (transient NoSuchKey → later found live), and the eventual fix (RFC-034 D18 write-visibility barrier) over-corrects with heavy fixed delays that push job completion past the scorer's polling window, converting genuine successes into false ERROR verdicts — later reduced by RFC-036 D1.
- Orientation- or feature-specific fixes leak into unrelated code paths via shared logic: RFC-035's landscape table/chart segmentation change regressed both landscape AND portrait chart documents simultaneously; RFC-034 D3's markdown-level re-normalization safety net risks double-application with the existing node-level bidi repair loop and is suspected (jointly with D17's table-repair guard) of causing MOU block-merging collapse.
- Single flags/parameters conflate independent concerns, preventing precise control: `_OCR_ESCALATION` gates both page-level and per-picture OCR together; `expected_script` is inferred from (possibly already-corrupted) text rather than threaded from filename/caller context in multiple call sites, silently disabling script-aware checks for German and already-garbled Latin text alike.
- Enrichment and OCR-recovery paths sometimes actively destroy real content in the name of fixing extraction: `image_enrichment_promoted` and per-picture enrichment replace real OCR'd chart digits with boilerplate placeholder descriptions, and standalone image ingestion bypasses per-picture enrichment entirely, producing literal `<!-- image -->` markers with zero recovered text.
- Large/complex documents (world-stats-pocketbook, 292 pages) experience a persistent multi-run saga of timeout and persistence failures that migrates root cause over time — dead timeout-wiring code, then serial landscape-reextract runaway, then write-barrier/job-completion regressions — without ever reaching a stable fix.
- Investigation reports (OCR/image-block conflation, cross-cutting exploration, BiDi root cause, ISSUES/FIXES backlog) repeatedly document defects as 'not yet fixed' with concrete recommended approaches, indicating a persistent backlog of known, unaddressed structural gaps (registry dual-write silent failure, erasure cascade missing the preloaded/ bucket, tessdata silent script fallback, table row_records invisible to content scoring, pdf-inspector classification computed but never consumed) that recur across audit cycles.
</content>
